"""Freeze assistant routes per turn while preserving the legacy settings API."""
from contextvars import ContextVar
from .runtime_settings import ChatRuntimeSettings
from . import runtime_settings

frozen_route = ContextVar('assistant_route', default=None)


def resolve_chat_route(project_ctx=None):
    settings = runtime_settings.load_chat_runtime_settings()
    source = 'global'
    if project_ctx is not None:
        from novelvideo.project_config import load_project_config_file_from_state_dir
        config = load_project_config_file_from_state_dir(project_ctx.state_dir)
        override = config.get('chat_agent_route_v1')
        if override:
            settings = ChatRuntimeSettings.model_validate({**settings.model_dump(), **override})
            source = 'project'
    return settings, source


def current_route():
    return frozen_route.get() or runtime_settings.load_chat_runtime_settings()
