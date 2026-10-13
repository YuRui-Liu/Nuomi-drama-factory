"""Episode identity and its single primary canvas are committed together."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import Field

from novelvideo.freezone.canvas_store import default_canvas_payload
from novelvideo.freezone.orchestration.models import fingerprint
from .models import OperationResult, StrictModel
from .operations import OperationError
from .store import ObjectStore


class EpisodeCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


def list_episodes(state_dir) -> list[dict]:
    """Read only: neither a missing database nor schema is initialized here."""
    db_path = Path(state_dir) / 'creation' / 'objects.sqlite3'
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT o.id, o.current_revision, v.payload, e.canvas_id "
            "FROM objects o JOIN object_versions v ON v.object_id=o.id AND v.revision=o.current_revision "
            "JOIN episode_canvases e ON e.episode_id=o.id WHERE o.kind='episode'").fetchall()
        episodes = []
        for row in rows:
            payload = json.loads(row['payload'])
            operation = conn.execute('SELECT state FROM operations WHERE id=?', (payload['operation_id'],)).fetchone()
            episodes.append({'episode_id': row['id'], 'canvas_id': row['canvas_id'],
                'title': payload['title'], 'number': payload['number'], 'revision': row['current_revision'],
                'status': 'running' if operation['state'] == 'pending_projection' else operation['state']})
    return sorted(episodes, key=lambda episode: (episode['number'], episode['episode_id']))


class EpisodeWorkspace:
    def __init__(self, objects: ObjectStore, *, project_id: str):
        self.objects, self.project_id = objects, project_id

    def create(self, actor: str, request: EpisodeCreate) -> OperationResult:
        if not request.title.strip():
            raise OperationError('invalid_contract', '分集标题不能为空')
        intent = {'kind': 'episode.create', 'project_id': self.project_id, **request.model_dump()}
        request_hash = fingerprint(intent)
        with self.objects.transaction() as conn:
            previous = conn.execute('SELECT * FROM operations WHERE request_id=?', (request.request_id,)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash or previous['actor_id'] != actor:
                    raise OperationError('idempotency_conflict', '请求编号已用于不同操作', request_id=request.request_id)
                return OperationResult.model_validate_json(previous['result'])
            # Allocate ordinal under the same serialized transaction as identity.
            rows = conn.execute("SELECT v.payload FROM objects o JOIN object_versions v "
                "ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.kind='episode'").fetchall()
            number = max((json.loads(row['payload']).get('number', 0) for row in rows), default=0) + 1
            episode_id, canvas_id, operation_id = uuid4().hex, uuid4().hex, uuid4().hex
            payload = {'title': request.title, 'number': number, 'operation_id': operation_id}
            scope = {'project_id': self.project_id, 'episode_id': episode_id, 'canvas_id': canvas_id, 'node_ids': []}
            initial = default_canvas_payload(project_id=self.project_id, actor_id=actor)
            initial.update(canvas_id=canvas_id, name=request.title,
                           metadata={'episode_id': episode_id, 'title': request.title})
            patch = {'schema_version': 1, 'operation_id': operation_id, 'scope': scope,
                     'canvas_create': initial, 'relation_changes': []}
            ref = {'object_id': episode_id, 'kind': 'episode', 'revision': 1,
                   'canvas_id': canvas_id, 'title': request.title, 'number': number}
            result = OperationResult(operation_id=operation_id, status='running', object_refs=[ref])
            conn.execute("INSERT INTO objects (id, kind, current_revision) VALUES (?, 'episode', 1)", (episode_id,))
            conn.execute('INSERT INTO object_versions VALUES (?, 1, ?, ?, ?)',
                         (episode_id, json.dumps(payload, ensure_ascii=False), actor, datetime.now(timezone.utc).isoformat()))
            conn.execute('INSERT INTO episode_canvases VALUES (?, ?)', (episode_id, canvas_id))
            conn.execute('INSERT INTO canvas_workspaces VALUES (?, ?, ?, ?, ?, ?)',
                         (canvas_id, 'episode', episode_id, request.title, operation_id, datetime.now(timezone.utc).isoformat()))
            conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (operation_id, request.request_id, request_hash, 'pending_projection',
                 json.dumps({**intent, 'scope': scope}, ensure_ascii=False), result.model_dump_json(), actor))
            conn.execute('INSERT INTO projection_outbox (operation_id, canvas_id, patch) VALUES (?, ?, ?)',
                         (operation_id, canvas_id, json.dumps(patch, ensure_ascii=False)))
        return result


def episode_response(result: OperationResult) -> dict:
    ref = result.object_refs[0]
    return {'episode_id': ref['object_id'], 'canvas_id': ref['canvas_id'], 'title': ref['title'],
            'number': ref['number'], 'revision': ref['revision'], 'operation_id': result.operation_id,
            'status': result.status}
