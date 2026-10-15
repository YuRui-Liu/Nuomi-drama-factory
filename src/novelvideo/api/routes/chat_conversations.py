"""List/create independent assistant conversations inside an authorized scope."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.chat.conversations import ConversationStore
from novelvideo.chat.session_store import conversation_scope
from novelvideo.chat.store import ChatScope

router = APIRouter()


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


async def authorized_store(user, project, canvas_id=None, *, write=False):
    from novelvideo.api.routes.chat import _authorize_chat_scope
    from novelvideo.chat.conversation_binding import validate_canvas
    from novelvideo.creation.project_mode import is_canvas_project
    from novelvideo.project_context import require_project_home_node
    scope = ChatScope.from_payload({'kind': 'project' if project else 'home', 'id': project})
    with conversation_scope('default'):
        ctx = await _authorize_chat_scope(user, scope, write=write)
    if ctx is not None:
        require_project_home_node(ctx, operation='conversation catalog')
    managed = bool(ctx and is_canvas_project(ctx.state_dir))
    if canvas_id:
        if scope.kind != 'project':
            raise HTTPException(409, '画布会话必须属于项目')
        validate_canvas(ctx, canvas_id)
    elif managed and write:
        raise HTTPException(409, '新创作会话必须明确选择画布')
    return ConversationStore(str(user['username']), scope.kind, scope.id), managed


@router.get('/chat/conversations')
async def list_conversations(project: str | None = None, canvas_id: str | None = None, user: dict = Depends(get_api_user)):
    store, managed = await authorized_store(user, project, canvas_id)
    return {'ok': True, 'data': store.list(canvas_id=canvas_id, include_default=not managed)}


@router.post('/chat/conversations', status_code=201)
async def create_conversation(body: CreateConversation, project: str | None = None, canvas_id: str | None = None, user: dict = Depends(get_api_user)):
    store, _ = await authorized_store(user, project, canvas_id, write=True)
    if canvas_id and not body.request_id:
        raise HTTPException(422, '画布会话创建必须提供 request_id')
    try:
        return {'ok': True, 'data': store.create(body.title, canvas_id=canvas_id, request_id=body.request_id)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
