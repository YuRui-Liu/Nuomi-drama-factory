"""Frozen project methods propagated through a task's context, never a library."""
from contextlib import contextmanager, closing
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sqlite3

from .models import ExecutionSnapshot

WRITER_KINDS = ('brief', 'outline', 'people', 'scenes', 'props', 'episode_synopsis', 'episode_script')
_CURRENT = ContextVar('agent_team_methods', default={})


def load_generation_methods(ctx, run_id):
    """Read durable run routing from the document DB, independent of task TTL."""
    path = Path(ctx.state_dir) / 'data.db'
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path, timeout=30)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_team_generation_bindings'").fetchone()
        if not exists:
            return None
        row = db.execute('SELECT data FROM agent_team_generation_bindings WHERE project_id=? AND run_id=?', (ctx.project_id, run_id)).fetchone()
        return json.loads(row[0]) if row else None


def bind_generation_methods(ctx, run_id, metadata):
    """First submission wins atomically, including the explicit no-team [] state."""
    path = Path(ctx.state_dir) / 'data.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS agent_team_generation_bindings (project_id TEXT NOT NULL, run_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(project_id, run_id))')
        db.execute('INSERT OR IGNORE INTO agent_team_generation_bindings VALUES (?, ?, ?)',
                   (ctx.project_id, run_id, json.dumps(metadata, ensure_ascii=False)))
        row = db.execute('SELECT data FROM agent_team_generation_bindings WHERE project_id=? AND run_id=?', (ctx.project_id, run_id)).fetchone()
        return json.loads(row[0])


def connected_subtasks():
    return {*(('writer', k) for k in WRITER_KINDS),
            ('script_parser', 'screenplay_semantics'), ('director', 'director_plan'),
            ('video_director', 'h3_episode_pack')}


def task_methods(task_type):
    if task_type == 'script_creation_generation':
        return [('writer', k) for k in WRITER_KINDS]
    if task_type == 'screenplay_semantics':
        return [('script_parser', 'screenplay_semantics')]
    if task_type == 'director_plan':
        return [('director', 'director_plan')]
    if task_type in ('narrative_group_video', 'narrative_group_video_segment'):
        return [('video_director', 'h3_episode_pack')]
    return []


@contextmanager
def method_scope(snapshots=(), *, project_id=None, task_type=None):
    methods = {}
    for raw in snapshots:
        snapshot = ExecutionSnapshot.model_validate(raw)
        if project_id is not None and snapshot.project_id != project_id:
            raise ValueError('agent team snapshot project mismatch')
        key = (snapshot.role_id, snapshot.subtask_id)
        if key not in connected_subtasks() or (task_type and key not in task_methods(task_type)):
            raise ValueError('agent team snapshot role/subtask mismatch')
        if key in methods:
            raise ValueError('duplicate agent team snapshot')
        methods[key] = snapshot.model_copy(deep=True)
    token = _CURRENT.set(methods)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_method(role, subtask):
    value = _CURRENT.get().get((role, subtask))
    return value.model_copy(deep=True) if value else None


def method_hash(role, subtask):
    snapshot = current_method(role, subtask)
    if snapshot is None:
        return None
    # Identity, input and activation counters do not describe creative behavior.
    content = {'method': snapshot.resolved_method.model_dump(mode='json'),
               'model': snapshot.resolved_model.model_dump(mode='json'),
               'resources': [r.model_dump(mode='json') for r in snapshot.resource_snapshots]}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def method_cache_dir(path, role, subtask):
    digest = method_hash(role, subtask)
    return Path(path) / ('method-' + digest) if digest else path


def freeze_task_methods(ctx, task_type, payload, route):
    from .service import AgentTeamService
    from .store import AgentTeamStore
    from novelvideo.text_task_runtime.models import AgentTaskRoute
    path = Path(ctx.state_dir) / 'agent-team.db'
    needed = task_methods(task_type)
    if not needed or not path.is_file():
        return []
    service = AgentTeamService(AgentTeamStore(path), None, '', connected_subtasks)
    active = service.store.get_binding(ctx.project_id)
    if active is None:
        return []
    # Payload fingerprints identify submitted references, not the mutable content
    # behind an ID. Individual runners retain their existing revision guards.
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    revision = str(payload.get('input_revision') or payload.get('revision_id') or payload.get('run_id') or digest)
    clean_route = AgentTaskRoute.model_validate({k: v for k, v in route.model_dump().items() if k in AgentTaskRoute.model_fields})
    result = []
    for role, subtask in needed:
        frozen = service.freeze_binding(active, ctx.project_id, role, subtask, revision, digest, clean_route)
        if frozen:
            result.append(frozen.model_dump(mode='json'))
    return result
