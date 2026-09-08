"""Celery runners for single-episode scene and prop planning."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.ports import get_usage_meter
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

_TASK_ASSET_KIND = {
    "episode_scene_planner": "scene",
    "episode_prop_planner": "prop",
}

logger = logging.getLogger(__name__)


async def _refresh_asset_caches(sqlite_store: Any, cognee_store: Any) -> bool:
    """Refresh post-commit caches while honoring Cognee's explicit status."""
    try:
        return await cognee_store.load_graph_state() is not False
    except Exception:
        logger.warning(
            "asset plan committed but cache refresh raised an exception",
            exc_info=True,
        )
        return False


def _dump_items(items: list[Any]) -> list[dict]:
    data: list[dict] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            data.append(item.model_dump())
        elif isinstance(item, dict):
            data.append(dict(item))
    return data


def _episode_asset_bindings(
    *,
    asset_kind: str,
    project_id: str,
    episode_number: int,
    director_plan: Any,
    changed_entities: tuple[Any, ...] | list[Any],
    characters: tuple[Any, ...] | list[Any],
    scenes: tuple[Any, ...] | list[Any],
    props: tuple[Any, ...] | list[Any],
):
    """Project one asset kind after overlaying the zero-write draft by name."""
    from novelvideo.narrative_groups.planned_binding_service import (
        bindings_by_kind,
        bindings_for_director_plan,
    )

    groups = tuple(director_plan.groups)
    shots = tuple(shot for group in groups for shot in group.shots)
    if not groups or not shots:
        raise ValueError("ACTIVE_DIRECTOR_PLAN_HAS_NO_SCOPE")
    scene_map = {item.name: item for item in scenes}
    prop_map = {item.name: item for item in props}
    if asset_kind == "scene":
        scene_map.update({item.name: item for item in changed_entities})
        selected_kinds = ("scene_base", "scene_variant")
    elif asset_kind == "prop":
        prop_map.update({item.name: item for item in changed_entities})
        selected_kinds = ("prop",)
    else:
        raise ValueError(f"Unsupported asset kind: {asset_kind}")
    projected = bindings_for_director_plan(
        project_id=project_id,
        episode_number=episode_number,
        source_plan_revision_id=director_plan.revision_id,
        groups=groups,
        shots=shots,
        characters=characters,
        scenes=tuple(scene_map.values()),
        props=tuple(prop_map.values()),
    )
    grouped = bindings_by_kind(projected)
    bindings = tuple(
        binding
        for kind in selected_kinds
        for binding in grouped.get(kind, ())
    )
    if asset_kind == "prop":
        selected_prop_ids = {
            str(getattr(item, "name", "") or "").strip()
            for item in changed_entities
            if str(getattr(item, "name", "") or "").strip()
        }
        bindings = tuple(
            binding for binding in bindings if binding.entity_id in selected_prop_ids
        )
    return bindings


def run_episode_asset_planner(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_episode_asset_planner(envelope, ctx),
            envelope,
            task_type=str(envelope.get("task_type") or ""),
        )
    )


async def _run_episode_asset_planner(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.agents.asset_compiler import AssetCompiler
    from novelvideo.cognee import CogneeStore
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.sqlite_store import (
        SQLiteStore,
        prop_catalog_baseline_digest,
        scene_catalog_baseline_digest,
    )

    task_type = str(envelope.get("task_type") or "")
    scope = envelope.get("scope")
    payload = envelope.get("payload") or {}
    billing_metadata = envelope.get("billing_metadata") or {}
    asset_kind = str(payload.get("asset_kind") or _TASK_ASSET_KIND.get(task_type, ""))
    expected_kind = _TASK_ASSET_KIND.get(task_type)
    if asset_kind not in {"scene", "prop"} or (expected_kind and asset_kind != expected_kind):
        raise ValueError(f"Unsupported episode asset planner kind: {asset_kind}")

    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    if episode <= 0:
        raise ValueError("episode must be greater than 0")

    manager = get_task_manager()
    await get_usage_meter().set_project_llm_usage_context(
        username=ctx.owner_username,
        project_name=ctx.project_name,
        resource_kind="script",
        billing_metadata=billing_metadata if isinstance(billing_metadata, dict) else None,
    )

    label = "场景" if asset_kind == "scene" else "道具"

    def update(
        progress: float | None = None,
        task: str | None = None,
        log: str | None = None,
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            scope=scope,
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
    update(0.15, f"规划{label}资产...")

    def on_log(message: str) -> None:
        update(log=message)

    director_plan_store = DirectorPlanStore(ctx.output_dir)
    with director_plan_store.lock_active_revision(episode) as active_snapshot:
        if active_snapshot is None:
            raise ValueError("DIRECTOR_PLAN_REQUIRED: 请先完成并激活导演镜头方案")
    compiler = AssetCompiler(cognee_store, director_plan=active_snapshot)
    if asset_kind == "scene":
        draft = await compiler.build_scene_plan_draft(
            episode_obj,
            on_log=on_log,
            on_progress=lambda progress, task: update(0.15 + progress * 0.75, task),
        )
    else:
        draft = await compiler.build_prop_plan_draft(
            episode_obj,
            on_log=on_log,
            on_progress=lambda progress, task: update(0.15 + progress * 0.75, task),
        )
    characters = tuple(cognee_store.get_all_characters())
    scenes = tuple(await sqlite_store.list_scenes())
    props = tuple(await sqlite_store.list_props())
    scene_catalog_digest = scene_catalog_baseline_digest(scenes)
    prop_catalog_digest = prop_catalog_baseline_digest(props)
    selected_prop_ids = {
        str(getattr(item, "prop_id", "") or "").strip()
        for item in getattr(draft, "prop_menu", ())
        if str(getattr(item, "prop_id", "") or "").strip()
    }
    selected_props = tuple(
        prop
        for prop in props
        if str(getattr(prop, "name", "") or "").strip() in selected_prop_ids
    )
    bindings = _episode_asset_bindings(
        asset_kind=asset_kind,
        project_id=ctx.project_id,
        episode_number=episode,
        director_plan=active_snapshot,
        changed_entities=draft.scenes if asset_kind == "scene" else selected_props,
        characters=characters,
        scenes=scenes,
        props=props,
    )
    with director_plan_store.lock_active_revision(episode) as final_active:
        if (
            final_active is None
            or final_active.revision_id != active_snapshot.revision_id
        ):
            raise ValueError("ACTIVE_DIRECTOR_PLAN_STALE")
        if asset_kind == "scene":
            publication = await sqlite_store.publish_scene_plan_atomic(
                episode_number=episode,
                scenes=draft.scenes,
                scene_menu=draft.scene_menu,
                scene_baseline_digests=draft.scene_baseline_digests,
                episode_scene_menu_baseline_digest=(
                    draft.episode_scene_menu_baseline_digest
                ),
                scene_catalog_baseline_digest=scene_catalog_digest,
                prop_catalog_baseline_digest=prop_catalog_digest,
                bindings=bindings,
                refresh_cache=False,
            )
        else:
            publication = await sqlite_store.publish_prop_plan_atomic(
                episode_number=episode,
                props=draft.props,
                prop_menu=draft.prop_menu,
                prop_baseline_digests=draft.prop_baseline_digests,
                episode_prop_menu_baseline_digest=(
                    draft.episode_prop_menu_baseline_digest
                ),
                scene_catalog_baseline_digest=scene_catalog_digest,
                prop_catalog_baseline_digest=prop_catalog_digest,
                bindings=bindings,
                refresh_cache=False,
            )

    cache_refresh_pending = bool(
        isinstance(publication, dict)
        and publication.get("cache_refresh_pending", False)
    )
    if await _refresh_asset_caches(sqlite_store, cognee_store):
        cache_refresh_pending = False
    else:
        cache_refresh_pending = True
        logger.warning(
            "%s plan committed but runner cache refresh is pending",
            asset_kind,
        )
        update(log=f"{label}规划已提交，缓存刷新待重试")
    binding_statuses = dict(Counter(binding.status for binding in bindings))
    if asset_kind == "scene":
        scene_menu_data = _dump_items(list(draft.scene_menu))
        if not scene_menu_data:
            raise ValueError("未识别到任何场景，请先生成逐行解说工作稿或补充场次地点")
        update(0.95, "场景规划完成", f"场景 {draft.new_count} 新建/{len(scene_menu_data)} 总计")
        return {
            "episode": episode,
            "kind": "scene",
            "new_count": draft.new_count,
            "total_count": len(scene_menu_data),
            "scene_menu": scene_menu_data,
            "binding_count": len(bindings),
            "binding_statuses": binding_statuses,
            "cache_refresh_pending": cache_refresh_pending,
        }

    prop_menu_data = _dump_items(list(draft.prop_menu))
    update(0.95, "道具规划完成", f"道具 {len(prop_menu_data)} 总计")
    return {
        "episode": episode,
        "kind": "prop",
        "total_count": len(prop_menu_data),
        "auto_promoted_props": [],
        "prop_menu": prop_menu_data,
        "binding_count": len(bindings),
        "binding_statuses": binding_statuses,
        "cache_refresh_pending": cache_refresh_pending,
    }


register_project_task_runner(
    "episode_scene_planner",
    run_episode_asset_planner,
    text_task_role="episode_asset_planning",
)
register_project_task_runner(
    "episode_prop_planner",
    run_episode_asset_planner,
)
