"""Administrator-only TEAM chat Agent configuration."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from novelvideo.api.auth import get_api_user
from novelvideo.chat.runtime_settings import ChatBackend, ChatRuntimeSettings, load_chat_runtime_settings, save_chat_runtime_settings
from novelvideo.shared.runtime_env import edition
from novelvideo.text_task_runtime.models import TextTaskReasoningEffort


def require_chat_admin(user: dict = Depends(get_api_user)) -> dict:
    if edition() != "team":
        raise HTTPException(404, "Chat runtime configuration requires TEAM edition")
    if user.get("role") != "admin" or user.get("credential_kind") == "agent_session":
        raise HTTPException(403, "Instance administrator browser session required")
    return user


router = APIRouter(prefix="/chat-runtime", tags=["chat-runtime"], dependencies=[Depends(require_chat_admin)])


class SaveChatRuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: ChatBackend
    model: str
    reasoningEffort: TextTaskReasoningEffort | None = None


def _response(settings: ChatRuntimeSettings) -> dict:
    return {"ok": True, "data": {"backend": settings.backend, "model": settings.model, "reasoningEffort": settings.reasoning_effort}}


@router.get("/config")
def get_config():
    return _response(load_chat_runtime_settings())


@router.put("/config")
def put_config(body: SaveChatRuntimeInput):
    try:
        settings = save_chat_runtime_settings(backend=body.backend, model=body.model, reasoning_effort=body.reasoningEffort)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _response(settings)
