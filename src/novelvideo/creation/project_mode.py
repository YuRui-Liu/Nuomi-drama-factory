"""Explicit opt-in for new workspaces; inspecting a project never migrates it."""

from novelvideo.project_config import load_project_config_file_from_state_dir
from .operations import OperationError

CREATION_MODE = 'agent_canvas_v1'


def is_canvas_project(state_dir, *, config=None) -> bool:
    if config is None:
        config = load_project_config_file_from_state_dir(state_dir)
    return config.get('creation_mode') == CREATION_MODE


def require_canvas_project(state_dir, project_id, *, config=None):
    if not is_canvas_project(state_dir, config=config):
        raise OperationError('legacy_project_readonly', '旧项目暂不支持创作画布操作', project_id=project_id)
