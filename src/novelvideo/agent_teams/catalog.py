"""Existing pipeline entry points, not claims of Agent Team adapter support."""
from .models import RoleDefinition

ROLE_CATALOG = (
    # Writer methods bind to document kinds within the generation runner.
    RoleDefinition(id="writer", name="编剧", subtasks=("brief", "outline", "people", "scenes", "props", "episode_synopsis", "episode_script")),
    RoleDefinition(id="script_review", name="剧本审查", subtasks=("script_creation_consistency",)),
    RoleDefinition(id="script_parser", name="剧本解析", subtasks=("screenplay_semantics", "script_creation_asset_extraction", "script_creation_prop_extraction")),
    RoleDefinition(id="director", name="分镜导演", subtasks=("director_plan",)),
    RoleDefinition(id="shot_review", name="分镜审查", subtasks=("novelvideo.shot_continuity.production_review", "novelvideo.shot_continuity.visual_review")),
    RoleDefinition(id="asset_design", name="资产设计", subtasks=("character_portrait", "identity_image", "scene_reference_asset", "prop_reference_asset")),
    RoleDefinition(id="asset_review", name="资产审查", subtasks=("character_casting_review",)),
    RoleDefinition(id="video_director", name="视频导演", subtasks=("h3_episode_pack", "h3_segment_repair", "freezone_video_director")),
)


def get_role_catalog() -> tuple[RoleDefinition, ...]:
    return ROLE_CATALOG
