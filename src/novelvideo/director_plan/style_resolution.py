"""Resolve the project-effective visual style for director planning."""

from __future__ import annotations

from typing import Any


def resolve_project_style_snapshot(
    ctx: Any,
    *,
    style_override: str | None = None,
):
    """Use the frozen project direction style when one is explicitly bound.

    Direction bindings already carry an immutable StyleSnapshot. Reading that
    snapshot keeps director planning aligned with the style the user fixed in
    the creation workspace, even when legacy ``visual_style`` is stale.
    """
    from novelvideo.director_plan.models import StyleSnapshot
    from novelvideo.project_config import load_project_config_file_from_state_dir
    from novelvideo.services.style_service import StyleService

    frozen = None
    if (str(getattr(ctx, "project_id", "") or "").strip()
            and str(getattr(ctx, "requester_user_id", "") or "").strip()):
        from novelvideo.creation.library_references import DirectionReferences

        effective = DirectionReferences(ctx).inspect({"level": "project"})[
            "effective"
        ]["style"]
        if effective.get("state") == "enabled":
            configuration = effective.get("configuration")
            if not isinstance(configuration, dict):
                raise ValueError("effective_project_style_snapshot_invalid")
            frozen = StyleSnapshot.model_validate(configuration)

    if frozen is not None and (
        not style_override or style_override == frozen.style_id
    ):
        return frozen

    config = load_project_config_file_from_state_dir(ctx.state_dir)
    project_style = str(config.get("visual_style") or "chinese_period_drama")
    override = style_override
    if frozen is not None and style_override:
        project_style = frozen.style_id
    return StyleService.resolve_style_snapshot(
        project_style,
        override,
        username=str(getattr(ctx, "owner_username", "") or ""),
        project=str(getattr(ctx, "project_name", "") or ""),
        project_dir=getattr(ctx, "output_dir", None),
    )
