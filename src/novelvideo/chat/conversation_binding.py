"""Transport evidence for immutable conversation ownership, never a rebind."""
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException

from novelvideo.creation.canvas_workspaces import list_canvases
from novelvideo.creation.project_mode import is_canvas_project
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.project_context import require_project_home_node
from .conversations import ConversationStore
from .session_store import current_conversation_id

_evidence = ContextVar('chat_canvas_evidence', default=(None, None))


@contextmanager
def canvas_evidence(project_id=None, canvas_id=None):
    token = _evidence.set((project_id, canvas_id))
    try:
        yield
    finally:
        _evidence.reset(token)


def requested_binding():
    return _evidence.get()


def validate_canvas(ctx, canvas_id):
    if not ctx:
        raise HTTPException(409, '此项目不支持画布会话')
    require_project_home_node(ctx, operation='canvas conversation')
    if not is_canvas_project(ctx.state_dir):
        raise HTTPException(409, '此项目不支持画布会话')
    entry = next((r for r in list_canvases(ctx.state_dir) if r['canvas_id'] == canvas_id and r['status'] == 'applied'), None)
    if not entry:
        raise HTTPException(409, '画布尚未登记或尚未完成创建')
    canvas = read_canvas(ctx.state_dir, canvas_id)
    if not canvas or canvas.get('project_id') != ctx.project_id:
        raise HTTPException(409, '画布不存在或不属于此项目')


def validate_conversation(user, scope, ctx):
    project_id, canvas_id = requested_binding()
    if project_id is not None and (scope.kind != 'project' or project_id != scope.id):
        raise HTTPException(409, '会话项目与固定连接范围不一致')
    identifier = current_conversation_id()
    index = ConversationStore(str(user['username']), scope.kind, scope.id)
    row = index.lookup(identifier) if identifier != 'default' else None
    if identifier != 'default' and row is None:
        raise HTTPException(404, '会话未登记')
    if row and row.get('state', 'ready') != 'ready':
        raise HTTPException(409, '会话创建尚未完成，请恢复原创建请求')
    if canvas_id:
        if scope.kind != 'project' or not row or row.get('canvas_id') != canvas_id:
            raise HTTPException(409, '会话不属于此画布')
        validate_canvas(ctx, canvas_id)
    elif row and row.get('canvas_id'):
        raise HTTPException(409, '画布会话必须提供固定画布编号')
    elif scope.kind == 'project' and ctx and is_canvas_project(ctx.state_dir):
        # Only already indexed historical project conversations remain usable.
        # A default history is not silently adopted by a new managed canvas.
        if not row:
            raise HTTPException(409, '创作项目请先明确创建画布会话')
