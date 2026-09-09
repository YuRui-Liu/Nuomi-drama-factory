"""Celery runner for episode identity planning."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
from pathlib import Path
from typing import Any

from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    validate_reference_image,
)
from novelvideo.project_context import ProjectContext
from novelvideo.production_workflow import (
    ProductionWorkflowStore,
    production_workflow_project_lock,
)
from novelvideo.production_workflow.slot_ids import character_portrait_slot_id
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.utils.path_resolver import canonical_portrait_path
from novelvideo.utils.safe_paths import resolve_under_root, validate_path_segment

logger = logging.getLogger(__name__)


def _available_character_portraits(
    *,
    ctx: ProjectContext,
    characters,
) -> frozenset[str]:
    root = Path(ctx.output_dir).resolve()
    characters_root = root / "assets" / "characters"
    state_dir = Path(ctx.state_dir)
    available: set[str] = set()
    with production_workflow_project_lock(state_dir):
        workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
        for character in characters:
            try:
                character_name = validate_path_segment(
                    str(getattr(character, "name", "") or ""),
                    label="character name",
                )
                character_root = resolve_under_root(characters_root, character_name)
            except ValueError:
                continue
            try:
                slot_id = character_portrait_slot_id(character_name)
            except ValueError:
                continue
            try:
                slot, versions = workflow.get_slot(slot_id)
            except KeyError:
                portrait_path = canonical_portrait_path(root, character_name)
                try:
                    validate_reference_image(
                        portrait_path,
                        allowed_roots=(character_root,),
                    )
                except (InvalidReferenceUpload, OSError, ValueError):
                    continue
                workflow.materialize_legacy_current(
                    slot_id=slot_id,
                    asset_kind="character_portrait",
                    asset_path=portrait_path.relative_to(root).as_posix(),
                )
                slot, versions = workflow.get_slot(slot_id)
            if slot.asset_kind != "character_portrait":
                logger.warning(
                    "ignoring incompatible character portrait slot kind",
                    extra={"slot_id": slot_id, "asset_kind": slot.asset_kind},
                )
                continue
            version = versions.get(str(slot.current_version_id or ""))
            if version is None or version.slot_id != slot_id:
                continue
            if version.adoption_status.value not in {"provisional", "adopted"}:
                continue
            image_path = Path(version.asset_path)
            if not image_path.is_absolute():
                image_path = root / image_path
            try:
                validate_reference_image(
                    image_path,
                    allowed_roots=(character_root,),
                )
            except (InvalidReferenceUpload, OSError, ValueError):
                continue
            available.add(character_name)
    return frozenset(available)


async def _refresh_identity_caches(cognee_store: Any) -> bool:
    """Refresh shared post-commit caches without invalidating a durable publish."""
    try:
        return await cognee_store.load_graph_state() is not False
    except Exception:
        logger.warning(
            "identity plan committed but cache refresh raised an exception",
            exc_info=True,
        )
        return False


def _build_identity_planner_result(
    *,
    episode: int,
    new_count: int,
    resolved_count: int,
    identities: list[dict[str, str]],
    auto_promoted_characters: list[str],
    binding_count: int | None = None,
    binding_statuses: dict[str, int] | None = None,
    cache_refresh_pending: bool | None = None,
) -> dict[str, Any]:
    result = {
        "episode": episode,
        "new_count": new_count,
        "resolved_count": resolved_count,
        "identities": identities,
        "auto_promoted_characters": auto_promoted_characters,
    }
    if binding_count is not None:
        result["binding_count"] = binding_count
        result["binding_statuses"] = dict(binding_statuses or {})
    if cache_refresh_pending is not None:
        result["cache_refresh_pending"] = cache_refresh_pending
    return result


def _character_identity_bindings(
    *,
    project_id: str,
    episode_number: int,
    director_plan,
    draft,
    characters,
    scenes,
    props,
    available_character_portraits=(),
):
    """Project only identity bindings, overlaying the zero-write draft snapshot."""
    from novelvideo.narrative_groups.planned_binding_service import (
        bindings_by_kind,
        bindings_for_director_plan,
    )

    groups = tuple(director_plan.groups)
    shots = tuple(shot for group in groups for shot in group.shots)
    if not groups or not shots:
        raise ValueError("ACTIVE_DIRECTOR_PLAN_HAS_NO_SCOPE")
    characters_by_name = {character.name: character for character in characters}
    characters_by_name.update(
        {character.name: character for character in draft.characters}
    )
    bindings = bindings_for_director_plan(
        project_id=project_id,
        episode_number=episode_number,
        source_plan_revision_id=director_plan.revision_id,
        groups=groups,
        shots=shots,
        characters=tuple(characters_by_name.values()),
        scenes=scenes,
        props=props,
        episode_identity_ids=draft.episode_identity_ids,
        identity_default_map=draft.identity_default_map,
        available_character_portraits=available_character_portraits,
    )
    return bindings_by_kind(bindings).get("character_identity", ())


def run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_identity_planner(envelope, ctx),
            envelope,
            task_type="identity_planner",
        )
    )


async def _run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.agents.identity_planner import IdentityPlanner
    from novelvideo.cognee import CogneeStore
    from novelvideo.sqlite_store import SQLiteStore

    episode = int(envelope.get("episode") or (envelope.get("payload") or {}).get("episode") or 0)
    manager = get_task_manager()

    def update(
        progress: float | None = None, task: str | None = None, log: str | None = None
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            "identity_planner",
            episode,
            progress=progress,
            current_task=task,
            logs=[log] if log else None,
        )

    update(0.05, "加载项目数据...")
    sqlite_store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await sqlite_store.initialize()
    await sqlite_store.load_graph_state()

    cognee_store = CogneeStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
        sqlite_store=sqlite_store,
    )
    await cognee_store.initialize()
    await cognee_store.load_graph_state()

    episode_obj = cognee_store.get_episode(episode)
    if episode_obj is None:
        raise ValueError(f"Episode {episode} not found")

    update(0.10, "分析身份需求...")
    planner = IdentityPlanner(cognee_store)

    def on_log(message: str) -> None:
        update(log=message)

    draft = await planner.build_identity_plan_draft(episode_obj, on_log=on_log)
    persisted_characters = tuple(cognee_store.get_all_characters())
    portrait_characters = {character.name: character for character in persisted_characters}
    portrait_characters.update(
        {character.name: character for character in draft.characters}
    )
    available_character_portraits = _available_character_portraits(
        ctx=ctx,
        characters=tuple(portrait_characters.values()),
    )

    from novelvideo.director_plan.store import DirectorPlanStore

    director_plan_store = DirectorPlanStore(ctx.output_dir)
    with director_plan_store.lock_active_revision(episode) as director_plan:
        if director_plan is None:
            raise ValueError("ACTIVE_DIRECTOR_PLAN_REQUIRED")
        bindings = _character_identity_bindings(
            project_id=ctx.project_id,
            episode_number=episode,
            director_plan=director_plan,
            draft=draft,
            characters=persisted_characters,
            scenes=tuple(await sqlite_store.list_scenes()),
            props=tuple(await sqlite_store.list_props()),
            available_character_portraits=available_character_portraits,
        )
        publication = await sqlite_store.publish_identity_plan_atomic(
            episode_number=episode,
            characters=draft.characters,
            episode_identity_ids=draft.episode_identity_ids,
            identity_default_map=draft.identity_default_map,
            identity_baseline_digests=draft.identity_baseline_digests,
            episode_identity_baseline_digest=(
                draft.episode_identity_baseline_digest
            ),
            bindings=bindings,
        )
    cache_refresh_pending = bool(
        isinstance(publication, dict)
        and publication.get("cache_refresh_pending", False)
    )
    if await _refresh_identity_caches(cognee_store):
        cache_refresh_pending = False
    else:
        cache_refresh_pending = True
        logger.warning("identity plan committed but runner cache refresh is pending")
        update(log="身份规划已提交，缓存刷新待重试")
    refreshed = cognee_store.get_episode(episode) or episode_obj

    identities: list[dict[str, str]] = []
    episode_identity_ids = set(getattr(refreshed, "identity_ids", []) or [])
    for character in cognee_store.get_all_characters():
        for identity in getattr(character, "identities", []) or []:
            identity_id = getattr(identity, "identity_id", "") or ""
            if not identity_id or identity_id not in episode_identity_ids:
                continue
            identities.append(
                {
                    "character_name": character.name,
                    "identity_id": identity_id,
                    "identity_name": getattr(identity, "identity_name", "") or identity_id,
                    "appearance_details": getattr(identity, "appearance_details", "") or "",
                }
            )

    update(
        0.95,
        "身份规划完成",
        f"新增 {draft.new_count} 个身份，复用 {draft.resolved_count} 个身份",
    )
    return _build_identity_planner_result(
        episode=episode,
        new_count=draft.new_count,
        resolved_count=draft.resolved_count,
        identities=identities,
        auto_promoted_characters=list(getattr(planner, "auto_promoted_characters", []) or []),
        binding_count=len(bindings),
        binding_statuses=dict(Counter(binding.status for binding in bindings)),
        cache_refresh_pending=cache_refresh_pending,
    )


register_project_task_runner(
    "identity_planner",
    run_identity_planner,
    text_task_role="knowledge_extraction",
)
