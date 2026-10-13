"""Registered project canvases with recoverable creation and read-only discovery."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import Field

from novelvideo.freezone.canvas_store import default_canvas_payload
from novelvideo.freezone.orchestration.models import fingerprint
from .models import OperationResult, StrictModel
from .operations import OperationError


class CanvasCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal['concept', 'free']
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
        return list(result.values())


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
                # A distinct ensure request is an explicit conflict, so no
                # unrecorded request ID can accidentally be reused elsewhere.
                raise OperationError('concept_exists', '项目概念画布已存在', canvas_id=canonical['canvas_id'])
            canvas_id, operation_id = uuid4().hex, uuid4().hex
            scope = {'project_id': self.project_id, 'canvas_id': canvas_id, 'episode_id': None, 'node_ids': []}
            initial = default_canvas_payload(project_id=self.project_id, actor_id=actor)
            initial.update(canvas_id=canvas_id, name=request.title, metadata={'kind': request.kind, 'title': request.title})
            ref = {'canvas_id': canvas_id, 'kind': request.kind, 'episode_id': None, 'title': request.title}
            result = OperationResult(operation_id=operation_id, status='running', object_refs=[ref])
            conn.execute('INSERT INTO canvas_workspaces VALUES (?, ?, NULL, ?, ?, ?)',
                (canvas_id, request.kind, request.title, operation_id, datetime.now(timezone.utc).isoformat()))
            conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (operation_id, request.request_id, digest, 'pending_projection', json.dumps({**intent, 'scope': scope}), result.model_dump_json(), actor))
            patch = {'schema_version': 1, 'operation_id': operation_id, 'scope': scope, 'canvas_create': initial, 'relation_changes': []}
            conn.execute('INSERT INTO projection_outbox (operation_id, canvas_id, patch) VALUES (?, ?, ?)',
                (operation_id, canvas_id, json.dumps(patch)))
        return result


def canvas_response(result):
    return {**result.object_refs[0], 'status': result.status, 'operation_id': result.operation_id}
