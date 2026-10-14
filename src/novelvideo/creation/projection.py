"""Recoverable semantic projection. No providers or paid execution run here."""

from copy import deepcopy
from contextlib import closing
import json
from typing import Any

from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso
from novelvideo.freezone.orchestration.graph import validate_graph
from novelvideo.freezone.orchestration.models import fingerprint, input_fingerprint
from .models import OperationResult
from .operations import LAYOUT_FIELDS
from .store import ObjectStore

MARKER_KEY = 'creation_projection_receipts'


class ProjectionConflict(ValueError):
    """The persisted patch no longer applies to the current inputs."""


class ProjectionService:
    def __init__(self, objects: ObjectStore):
        self.objects = objects
        self.state_dir = objects.db_path.parent.parent

    def recover_pending(self) -> list[OperationResult]:
        """Recover a previously authorized local store, in commit order."""
        with closing(self.objects._connect()) as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM operations WHERE state='pending_projection' ORDER BY rowid")]
        return [self.apply(operation_id) for operation_id in ids]

    def apply(self, operation_id: str) -> OperationResult:
        with closing(self.objects._connect()) as conn:
            row = conn.execute('SELECT o.*, p.patch, p.canvas_id FROM operations o '
                'LEFT JOIN projection_outbox p ON p.operation_id=o.id WHERE o.id=?', (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        if row['state'] != 'pending_projection':
            return OperationResult.model_validate_json(row['result'])
        patch = json.loads(row['patch'])
        patch_hash = fingerprint(patch)
        terminal_result = None

        def receipt(existing):
            nonlocal terminal_result
            # The initial read can predate another worker's final decision.
            # This callback runs under the cross-process canvas lock, before
            # any write. Conflict finalization uses the same lock below.
            with closing(self.objects._connect()) as conn:
                current = conn.execute('SELECT state, result FROM operations WHERE id=?', (operation_id,)).fetchone()
            if current['state'] != 'pending_projection':
                terminal_result = OperationResult.model_validate_json(current['result'])
                return {}
            marker = ((existing or {}).get('metadata') or {}).get(MARKER_KEY, {}).get(operation_id)
            if marker is not None:
                if marker['hash'] != patch_hash:
                    raise RuntimeError('Projection receipt hash mismatch')
                return {'revision': marker['revision']}
            return None

        def build(existing):
            try:
                if patch.get('asset_archives'):
                    from .asset_reference import verify_blob, AssetReferenceError
                    evidence = patch['asset_archives']
                    try:
                        for blob in evidence['blobs']:
                            verify_blob(evidence['root'], blob)
                    except AssetReferenceError as exc:
                        raise ProjectionConflict(str(exc)) from exc
                if 'canvas_create' in patch:
                    if existing is not None:
                        raise ProjectionConflict('Canvas identity already exists')
                    payload = deepcopy(patch['canvas_create'])
                else:
                    if existing is None or existing.get('project_id') != patch['scope']['project_id']:
                        raise ProjectionConflict('Canvas is missing or changed project')
                    payload = self._merge(existing, patch)
            except ProjectionConflict as exc:
                # Finalize a rejected attempt while its observed canvas state
                # is still locked. A concurrent retry cannot apply between the
                # failed comparison and durable conflict acknowledgement.
                self._conflict(operation_id, patch, str(exc))
                raise
            revision = payload['revision'] = existing['revision'] + 1 if existing is not None else 1
            payload.update(updated_at=utc_now_iso(), save_source='creation_projection')
            if not isinstance(payload.get('metadata'), dict):
                payload['metadata'] = {}
            markers = payload['metadata'].setdefault(MARKER_KEY, {})
            # Pending operations retain their atomic evidence even if log writing
            # or the semantic acknowledgement failed. Only acknowledged receipts
            # may be pruned; the database remains the durable completed ledger.
            with closing(self.objects._connect()) as conn:
                pending = {r[0] for r in conn.execute("SELECT id FROM operations WHERE state='pending_projection'")}
            completed = [key for key in markers if key not in pending]
            for key in completed[:-128]:
                del markers[key]
            markers[operation_id] = {'hash': patch_hash, 'revision': revision}
            return payload

        try:
            saved = save_canvas(self.state_dir, row['canvas_id'], base_revision=None,
                enforce_revision=False, build_payload=build, skip_if=receipt,
                client_save_id='creation-' + operation_id, request_hash=patch_hash,
                save_source='creation_projection', allow_empty_overwrite=True,
                trusted_creation_projection=True)
        except ProjectionConflict as exc:
            return self._conflict(operation_id, patch, str(exc))
        if terminal_result is not None:
            return terminal_result
        revision = (saved.response_cache or saved.payload)['revision']
        result = OperationResult.model_validate_json(row['result']).model_copy(
            update={'status': 'applied', 'canvas_revision': revision})
        # Never hold the semantic write transaction while acquiring a canvas lock.
        # Until acknowledgement, the atomic receipt prevents every other worker
        # from entering build/conflict, even after unrelated canvas edits.
        with self.objects.transaction() as conn:
            current = conn.execute('SELECT state, result FROM operations WHERE id=?', (operation_id,)).fetchone()
            if current['state'] != 'pending_projection':
                return OperationResult.model_validate_json(current['result'])
            conn.execute('UPDATE projection_outbox SET applied_revision=? WHERE operation_id=?', (revision, operation_id))
            conn.execute("UPDATE operations SET state='applied', result=? WHERE id=?", (result.model_dump_json(), operation_id))
        return result

    @staticmethod
    def _merge(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(existing)
        nodes = {node['id']: node for node in payload['nodes']}
        original = {node['id']: node for node in existing['nodes']}
        before = patch['before']['nodes']
        # Guard only actual operation inputs, not every node in the authorized
        # scope. Display/runtime changes and independent layout remain intact.
        guarded = set()
        for op in patch['operations']:
            if op['op'] in {'update_node', 'remove_node'}:
                guarded.add(op['node_id'])
            elif op['op'] == 'add_edge':
                guarded.update((op['edge']['source'], op['edge']['target']))
        for node_id in guarded & before.keys():
            if node_id not in nodes or input_fingerprint(nodes[node_id]) != patch['input_guards'][node_id]:
                raise ProjectionConflict('Node input changed: ' + node_id)
        for op in patch['operations']:
            kind = op['op']
            if kind == 'add_node':
                if op['node']['id'] in nodes:
                    raise ProjectionConflict('Node identity already exists')
                node = deepcopy(op['node'])
                payload['nodes'].append(node)
                nodes[node['id']] = node
            elif kind == 'update_node':
                node_id = op['node_id']
                node = nodes.get(node_id)
                if node is None:
                    raise ProjectionConflict('Node was removed')
                old = before.get(node_id, {})
                for field in LAYOUT_FIELDS & op.keys():
                    if field == 'style':
                        current_style = original.get(node_id, {}).get('style')
                        previous_style = old.get('style')
                        if any(style is not None and not isinstance(style, dict)
                               for style in (node.get('style'), current_style, previous_style)):
                            raise ProjectionConflict('Invalid existing node style')
                        if node.get('style') is None:
                            node['style'] = {}
                        for key, value in op[field].items():
                            if (node_id in before and (current_style or {}).get(key)
                                    != (previous_style or {}).get(key)):
                                raise ProjectionConflict('Owned style field changed: ' + key)
                            node.setdefault('style', {})[key] = deepcopy(value)
                        continue
                    if node_id in before and original[node_id].get(field) != old.get(field):
                        raise ProjectionConflict('Owned layout field changed: ' + field)
                    node[field] = deepcopy(op[field])
                for field, value in op.get('data', {}).items():
                    # displayName is outside input identity but is still owned
                    # when explicitly edited by this operation.
                    if node_id in before and original[node_id].get('data', {}).get(field) != old.get('data', {}).get(field):
                        raise ProjectionConflict('Owned node field changed: ' + field)
                    node.setdefault('data', {})[field] = deepcopy(value)
            elif kind == 'remove_node':
                node = nodes.pop(op['node_id'], None)
                if node is None:
                    raise ProjectionConflict('Node was removed')
                current_edges = [e for e in existing['edges'] if node['id'] in (e['source'], e['target'])]
                old_edges = [e for e in patch['before']['edges'] if node['id'] in (e['source'], e['target'])]
                if current_edges != old_edges:
                    raise ProjectionConflict('Node connections changed')
                payload['nodes'].remove(node)
                payload['edges'] = [e for e in payload['edges'] if node['id'] not in (e['source'], e['target'])]
            elif kind == 'add_edge':
                if any(e['id'] == op['edge']['id'] for e in payload['edges']):
                    raise ProjectionConflict('Edge identity already exists')
                payload['edges'].append(deepcopy(op['edge']))
            elif kind == 'remove_edge':
                edge = next((e for e in payload['edges'] if e['id'] == op['edge_id']), None)
                old = next((e for e in patch['before']['edges'] if e['id'] == op['edge_id']), None)
                current = next((e for e in existing['edges'] if e['id'] == op['edge_id']), None)
                if edge is None or current != old:
                    raise ProjectionConflict('Edge changed')
                payload['edges'].remove(edge)
        if not validate_graph(payload['nodes'], payload['edges'])['valid']:
            raise ProjectionConflict('Graph changed incompatibly')
        return payload

    def _conflict(self, operation_id: str, patch: dict, reason: str) -> OperationResult:
        result = OperationResult(operation_id=operation_id, status='conflict',
                                 candidate_id='candidate-' + operation_id)
        with self.objects.transaction() as conn:
            state = conn.execute('SELECT state, result FROM operations WHERE id=?', (operation_id,)).fetchone()
            if state['state'] != 'pending_projection':
                return OperationResult.model_validate_json(state['result'])
            result.object_refs = OperationResult.model_validate_json(state['result']).object_refs
            conn.execute('CREATE TABLE IF NOT EXISTS projection_candidates ('
                         'id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, patch TEXT NOT NULL, reason TEXT NOT NULL)')
            conn.execute('INSERT INTO projection_candidates VALUES (?, ?, ?, ?)',
                         (result.candidate_id, operation_id, json.dumps(patch, ensure_ascii=False), reason))
            conn.execute("UPDATE operations SET state='conflict', result=? WHERE id=?", (result.model_dump_json(), operation_id))
            for change in patch['relation_changes']:
                before, after = change['before'], change['after']
                # A failed predecessor can be hidden behind several pending
                # bindings. Follow its exact saved chain, never resurrect it.
                seen = set()
                while before:
                    owner = json.loads(before['payload']).get('operation_id')
                    if not owner or owner in seen:
                        break
                    seen.add(owner)
                    prior = conn.execute('SELECT o.state, p.patch FROM operations o JOIN projection_outbox p '
                                         'ON p.operation_id=o.id WHERE o.id=?', (owner,)).fetchone()
                    if prior is None or prior['state'] != 'conflict':
                        break
                    previous_change = next((c for c in json.loads(prior['patch'])['relation_changes']
                                            if c['after'] == before), None)
                    if previous_change is None:
                        raise RuntimeError('Invalid relation compensation chain')
                    before = previous_change['before']
                if before is None:
                    conn.execute('DELETE FROM relations WHERE id=? AND source_id=? AND target_id=? AND kind=? AND payload=?',
                                 tuple(after[k] for k in ('id', 'source_id', 'target_id', 'kind', 'payload')))
                else:
                    conn.execute('UPDATE relations SET source_id=?, target_id=?, kind=?, payload=? '
                                 'WHERE id=? AND source_id=? AND target_id=? AND kind=? AND payload=?',
                                 tuple(before[k] for k in ('source_id', 'target_id', 'kind', 'payload')) +
                                 tuple(after[k] for k in ('id', 'source_id', 'target_id', 'kind', 'payload')))
        return result
