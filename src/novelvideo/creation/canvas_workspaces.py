"""Registered project canvases with recoverable creation and read-only discovery."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import Field

from novelvideo.freezone.canvas_store import (
    CanvasRevisionConflict,
    default_canvas_payload,
    read_canvas,
    save_canvas,
    soft_delete_canvas,
    utc_now_iso,
)
from novelvideo.freezone.orchestration.models import fingerprint
from .models import OperationResult, StrictModel
from .operations import OperationError
from .store import ObjectStore
from .workspace import list_episodes


class CanvasCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal['concept', 'free']
    title: str = Field(min_length=1, max_length=200)


class CanvasRename(StrictModel):
    title: str = Field(min_length=1, max_length=200)


def list_canvases(state_dir):
    path = Path(state_dir) / 'creation' / 'objects.sqlite3'
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        if 'canvas_workspaces' in tables:
            for row in conn.execute('SELECT w.*, o.state FROM canvas_workspaces w JOIN operations o ON o.id=w.operation_id ORDER BY w.rowid'):
                result[row['canvas_id']] = {**{k: row[k] for k in ('canvas_id', 'kind', 'episode_id', 'title')},
                    'status': 'running' if row['state'] == 'pending_projection' else row['state']}
        if {'episode_canvases', 'objects', 'object_versions', 'operations'} <= tables:
            rows = conn.execute("""SELECT e.canvas_id, e.episode_id, v.payload, o.state
                FROM episode_canvases e JOIN objects obj ON obj.id=e.episode_id AND obj.kind='episode'
                JOIN object_versions v ON v.object_id=obj.id AND v.revision=obj.current_revision
                JOIN operations o ON o.id=json_extract(v.payload,'$.operation_id')
                WHERE json_extract(o.payload,'$.kind')='episode.create'
                  AND json_extract(o.payload,'$.scope.episode_id')=e.episode_id
                  AND json_extract(o.payload,'$.scope.canvas_id')=e.canvas_id""")
            for row in rows:
                result.setdefault(row['canvas_id'], {'canvas_id': row['canvas_id'], 'kind': 'episode',
                    'episode_id': row['episode_id'], 'title': json.loads(row['payload'])['title'],
                    'status': 'running' if row['state'] == 'pending_projection' else row['state']})
        episodes = list_episodes(state_dir)
        for item in result.values():
            item['episode_number'] = None
            if item['kind'] != 'episode':
                continue
            matches = [episode for episode in episodes
                       if episode.get('episode_id') == item.get('episode_id')
                       and episode.get('canvas_id') == item.get('canvas_id')]
            if len(matches) == 1 and type(matches[0].get('number')) is int and matches[0]['number'] > 0:
                item['episode_number'] = matches[0]['number']
        # Managed deletions keep their registry rows and snapshots for restore,
        # but tombstoned canvases stay out of the active creation workspace.
        active = Path(state_dir) / 'freezone' / 'canvases'
        return [item for item in result.values()
                if not (active / f"{item['canvas_id']}.deleted.json").is_file()]


class CanvasWorkspace:
    def __init__(self, objects, *, project_id):
        self.objects, self.project_id = objects, project_id

    def create(self, actor, request: CanvasCreate):
        if not request.title.strip():
            raise OperationError('invalid_contract', '画布标题不能为空')
        intent = {'kind': 'canvas.create', 'project_id': self.project_id, 'request': request.model_dump()}
        digest = fingerprint(intent)
        with self.objects.transaction() as conn:
            previous = conn.execute('SELECT * FROM operations WHERE request_id=?', (request.request_id,)).fetchone()
            if previous:
                if previous['request_hash'] != digest or previous['actor_id'] != actor:
                    raise OperationError('idempotency_conflict', '请求编号已用于不同操作', request_id=request.request_id)
                return OperationResult.model_validate_json(previous['result'])
            canonical = conn.execute("SELECT * FROM canvas_workspaces WHERE kind='concept'").fetchone() if request.kind == 'concept' else None
            if canonical:
                tombstone = (self.objects.db_path.parent.parent / 'freezone' / 'canvases'
                             / f"{canonical['canvas_id']}.deleted.json")
                if tombstone.is_file():
                    # Keep deleted snapshots on disk, but release the unique
                    # project concept slot so users can create its replacement.
                    conn.execute('DELETE FROM canvas_workspaces WHERE canvas_id=?',
                                 (canonical['canvas_id'],))
                    canonical = None
            if canonical:
                # A distinct ensure request is an explicit conflict, so no
                # unrecorded request ID can accidentally be reused elsewhere.
                raise OperationError('concept_exists', '项目概念画布已存在', canvas_id=canonical['canvas_id'])
            canvas_id, operation_id = uuid4().hex, uuid4().hex
            scope = {'project_id': self.project_id, 'canvas_id': canvas_id, 'episode_id': None, 'node_ids': []}
            initial = default_canvas_payload(project_id=self.project_id, actor_id=actor)
            initial.update(canvas_id=canvas_id, name=request.title, metadata={'kind': request.kind, 'title': request.title})
            ref = {'canvas_id': canvas_id, 'kind': request.kind, 'episode_id': None,
                   'episode_number': None, 'title': request.title}
            result = OperationResult(operation_id=operation_id, status='running', object_refs=[ref])
            conn.execute('INSERT INTO canvas_workspaces VALUES (?, ?, NULL, ?, ?, ?)',
                (canvas_id, request.kind, request.title, operation_id, datetime.now(timezone.utc).isoformat()))
            conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (operation_id, request.request_id, digest, 'pending_projection', json.dumps({**intent, 'scope': scope}), result.model_dump_json(), actor))
            patch = {'schema_version': 1, 'operation_id': operation_id, 'scope': scope, 'canvas_create': initial, 'relation_changes': []}
            conn.execute('INSERT INTO projection_outbox (operation_id, canvas_id, patch) VALUES (?, ?, ?)',
                (operation_id, canvas_id, json.dumps(patch)))
        return result

    def rename(self, actor: str, canvas_id: str, request: CanvasRename) -> dict:
        """Rename a registered canvas and keep its projection and registry aligned."""
        title = request.title.strip()
        if not title:
            raise OperationError('invalid_contract', '画布标题不能为空')

        with closing(self.objects._connect()) as conn:
            registered = conn.execute(
                'SELECT kind, episode_id, title FROM canvas_workspaces WHERE canvas_id=?',
                (canvas_id,),
            ).fetchone()
        if registered is None:
            raise OperationError('scope_violation', '画布不存在或未注册', canvas_id=canvas_id)

        current = read_canvas(self.objects.db_path.parent.parent, canvas_id)
        if not isinstance(current, dict) or current.get('project_id') != self.project_id:
            raise OperationError('scope_violation', '画布不属于当前项目', canvas_id=canvas_id)
        if current.get('name') == title and registered['title'] == title:
            return {'canvas_id': canvas_id, 'title': title,
                    'revision': current.get('revision'), 'kind': registered['kind']}

        state_dir = self.objects.db_path.parent.parent
        try:
            saved = save_canvas(
                state_dir,
                canvas_id,
                base_revision=current.get('revision'),
                client_save_id='creation-rename-' + uuid4().hex,
                save_source='creation_rename',
                allow_empty_overwrite=True,
                build_payload=lambda existing: self._renamed_payload(existing, title, actor),
            )
        except CanvasRevisionConflict as exc:
            raise OperationError('revision_conflict', '画布版本已变化，请刷新后重试',
                                 current_revision=exc.current_revision) from exc

        # Updating the SQL registry after the projection avoids holding a DB
        # transaction while acquiring the cross-process canvas write lock.
        with self.objects.transaction() as conn:
            row = conn.execute(
                'SELECT kind, episode_id FROM canvas_workspaces WHERE canvas_id=?', (canvas_id,)
            ).fetchone()
            if row is None or row['kind'] != registered['kind'] or row['episode_id'] != registered['episode_id']:
                raise OperationError('scope_violation', '画布注册信息已变化', canvas_id=canvas_id)
            conn.execute('UPDATE canvas_workspaces SET title=? WHERE canvas_id=?', (title, canvas_id))
            if row['kind'] == 'episode' and row['episode_id']:
                episode = conn.execute(
                    'SELECT current_revision FROM objects WHERE id=? AND kind=\'episode\'',
                    (row['episode_id'],),
                ).fetchone()
                if episode is not None:
                    old = ObjectStore._read(conn, row['episode_id'], episode['current_revision'])
                    payload = dict(old['payload'])
                    payload['title'] = title
                    revision = episode['current_revision'] + 1
                    conn.execute('INSERT INTO object_versions VALUES (?, ?, ?, ?, ?)',
                                 (row['episode_id'], revision, json.dumps(payload, ensure_ascii=False),
                                  actor, datetime.now(timezone.utc).isoformat()))
                    conn.execute('UPDATE objects SET current_revision=? WHERE id=?',
                                 (revision, row['episode_id']))
        return {'canvas_id': canvas_id, 'title': title,
                'revision': saved.payload.get('revision'), 'kind': registered['kind']}

    def delete(self, actor: str, canvas_id: str) -> dict:
        """Soft-delete a registered user canvas while keeping its registry for restore."""
        if canvas_id == 'default':
            raise OperationError('scope_violation', '系统默认画布不可删除', canvas_id=canvas_id)
        with closing(self.objects._connect()) as conn:
            registered = conn.execute(
                'SELECT kind, episode_id, title FROM canvas_workspaces WHERE canvas_id=?',
                (canvas_id,),
            ).fetchone()
        state_dir = self.objects.db_path.parent.parent
        tombstone = state_dir / 'freezone' / 'canvases' / f'{canvas_id}.deleted.json'
        if registered is None:
            if tombstone.is_file():
                return {'canvas_id': canvas_id, 'deleted': True, 'recoverable': True}
            raise OperationError('scope_violation', '仅支持删除已注册的创作画布', canvas_id=canvas_id)
        if registered['kind'] not in {'concept', 'free', 'episode'}:
            raise OperationError('scope_violation', '仅支持删除已注册的创作画布', canvas_id=canvas_id)
        current = read_canvas(state_dir, canvas_id)
        if current is None:
            if tombstone.is_file():
                return {'canvas_id': canvas_id, 'deleted': True, 'recoverable': True}
            raise OperationError('scope_violation', '画布不存在或未注册', canvas_id=canvas_id)
        if not isinstance(current, dict) or current.get('project_id') != self.project_id:
            raise OperationError('scope_violation', '画布不属于当前项目', canvas_id=canvas_id)
        metadata = current.get('metadata') if isinstance(current.get('metadata'), dict) else {}
        if metadata.get('system') or current.get('canvas_scope') == 'default':
            raise OperationError('scope_violation', '系统画布不可删除', canvas_id=canvas_id)
        deleted = soft_delete_canvas(state_dir, canvas_id, deleted_by=actor)
        if deleted.existing is None:
            raise OperationError('scope_violation', '画布不存在或未注册', canvas_id=canvas_id)
        return {'canvas_id': canvas_id, 'deleted': True, 'recoverable': True,
                'revision': deleted.existing.get('revision')}

    @staticmethod
    def _renamed_payload(existing, title: str, actor: str):
        if not isinstance(existing, dict):
            raise OperationError('scope_violation', '画布不存在')
        payload = dict(existing)
        payload['name'] = title
        payload['revision'] = existing['revision'] + 1
        metadata = dict(payload.get('metadata') or {})
        metadata['title'] = title
        payload['metadata'] = metadata
        payload['updated_by'] = actor
        payload['updated_at'] = utc_now_iso()
        return payload


def canvas_response(result):
    return {**result.object_refs[0], 'episode_number': None,
            'status': result.status, 'operation_id': result.operation_id}
