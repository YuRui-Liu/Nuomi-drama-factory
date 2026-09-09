"""Celery runners for single-episode scene and prop planning."""

from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    validate_reference_image,
)
from novelvideo.project_context import ProjectContext
from novelvideo.ports import get_usage_meter
from novelvideo.production_workflow import (
    AdoptionStatus,
    ProductionWorkflowStore,
)
from novelvideo.production_workflow.slot_ids import (
    scene_base_slot_id,
    scene_state_slot_id,
)
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.utils.path_resolver import canonical_scene_master_path

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


async def _shield_scene_publication(operation: Any) -> Any:
    """Wait for scene publication to settle even if its caller is cancelled."""
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            continue
        if cancelled:
            raise asyncio.CancelledError
        return result
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def _dump_items(items: list[Any]) -> list[dict]:
    data: list[dict] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            data.append(item.model_dump())
        elif isinstance(item, dict):
            data.append(dict(item))
    return data


def _safe_scene_name(value: Any) -> str:
    scene_name = str(value or "").strip()
    if (
        not scene_name
        or scene_name in {".", ".."}
        or Path(scene_name).is_absolute()
        or "/" in scene_name
        or "\\" in scene_name
        or ":" in scene_name
        or any(
        ord(character) < 32 or ord(character) == 127 for character in scene_name
        )
    ):
        raise ValueError("invalid scene name")
    return scene_name


def _available_scene_reference_slots(
    *,
    ctx: ProjectContext,
    scenes: tuple[Any, ...] | list[Any],
    workflow: ProductionWorkflowStore | None = None,
) -> frozenset[str]:
    """Return usable workflow or legacy scene master slots without mutation."""
    root = Path(ctx.output_dir).resolve()
    state_dir = Path(ctx.state_dir)
    if workflow is None:
        workflow = ProductionWorkflowStore(
            state_dir / "production_workflow.json"
        )
    if workflow.read_only_reason:
        return frozenset()

    assets_root = root / "assets"
    available: set[str] = set()
    for scene in scenes:
        try:
            scene_name = _safe_scene_name(getattr(scene, "name", ""))
            raw_base_scene_id = str(
                getattr(scene, "base_scene_id", "") or ""
            ).strip()
            base_scene_id = (
                _safe_scene_name(raw_base_scene_id) if raw_base_scene_id else ""
            )
            if base_scene_id:
                slot_id = scene_state_slot_id(base_scene_id, scene_name, "master")
                expected_kind = "scene_state"
            else:
                slot_id = scene_base_slot_id(scene_name, "master")
                expected_kind = "scene_base"
            canonical_path = canonical_scene_master_path(root, scene_name)
            expected_canonical_path = (
                root / "assets" / "scenes" / scene_name / "master.png"
            )
            if canonical_path != expected_canonical_path:
                continue
        except ValueError:
            continue

        try:
            slot, versions = workflow.get_slot(slot_id)
        except KeyError:
            try:
                validate_reference_image(
                    canonical_path,
                    allowed_roots=(assets_root,),
                    expected_mime="image/png",
                )
            except (InvalidReferenceUpload, OSError, ValueError):
                continue
            available.add(slot_id)
            continue

        if (
            slot.slot_id != slot_id
            or slot.asset_kind != expected_kind
            or not slot.current_version_id
        ):
            continue
        current = versions.get(slot.current_version_id)
        if current is None or current.slot_id != slot_id:
            continue
        if current.adoption_status not in {
            AdoptionStatus.PROVISIONAL,
            AdoptionStatus.ADOPTED,
        }:
            continue
        current_path = Path(current.asset_path)
        if not current_path.is_absolute():
            current_path = root / current_path
        try:
            validate_reference_image(
                current_path,
                allowed_roots=(assets_root,),
            )
        except (InvalidReferenceUpload, OSError, ValueError):
            continue
        available.add(slot_id)
    return frozenset(available)


def _legacy_scene_reference_fingerprints(
    *, ctx: ProjectContext, scenes: tuple[Any, ...]
) -> dict[str, str]:
    root = Path(ctx.output_dir).resolve()
    legacy: dict[str, str] = {}
    for scene in scenes:
        try:
            scene_name = _safe_scene_name(getattr(scene, "name", ""))
            canonical = canonical_scene_master_path(root, scene_name)
            expected = root / "assets" / "scenes" / scene_name / "master.png"
            if canonical != expected:
                continue
            validated = validate_reference_image(
                canonical,
                allowed_roots=(root / "assets",),
                expected_mime="image/png",
            )
        except (InvalidReferenceUpload, OSError, ValueError):
            continue
        legacy[scene_name] = validated.sha256
    return legacy


def _scene_reference_catalog_snapshot(
    *, ctx: ProjectContext, scenes: tuple[Any, ...]
) -> tuple[frozenset[str], str]:
    """Read a stable workflow/canonical snapshot for optimistic binding CAS."""
    workflow_path = Path(ctx.state_dir) / "production_workflow.json"
    for _attempt in range(3):
        before = workflow_path.read_bytes() if workflow_path.is_file() else b""
        legacy_before = _legacy_scene_reference_fingerprints(
            ctx=ctx, scenes=scenes
        )
        workflow = ProductionWorkflowStore(workflow_path)
        available = _available_scene_reference_slots(
            ctx=ctx, scenes=scenes, workflow=workflow
        )
        legacy_after = _legacy_scene_reference_fingerprints(ctx=ctx, scenes=scenes)
        after = workflow_path.read_bytes() if workflow_path.is_file() else b""
        if before == after and legacy_before == legacy_after:
            canonical = json.dumps(
                {
                    "available_slots": sorted(available),
                    "legacy": legacy_after,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            revision = hashlib.sha256(after + canonical.encode()).hexdigest()
            return available, revision
    raise ValueError("SCENE_REFERENCE_CATALOG_BUSY")


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
    available_scene_reference_slots: frozenset[str] | None = None,
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
        available_scene_reference_slots=available_scene_reference_slots,
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
    scene_projection_items: tuple[Any, ...] = ()
    available_scene_reference_slots = None
    scene_reference_revision = ""
    if asset_kind == "scene":
        scene_map = {scene.name: scene for scene in scenes}
        scene_map.update({scene.name: scene for scene in draft.scenes})
        scene_projection_items = tuple(scene_map.values())
        (
            available_scene_reference_slots,
            scene_reference_revision,
        ) = _scene_reference_catalog_snapshot(
            ctx=ctx,
            scenes=scene_projection_items,
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
        available_scene_reference_slots=available_scene_reference_slots,
    )
    with director_plan_store.lock_active_revision(episode) as final_active:
        if (
            final_active is None
            or final_active.revision_id != active_snapshot.revision_id
        ):
            raise ValueError("ACTIVE_DIRECTOR_PLAN_STALE")
        if asset_kind == "scene":
            async def publish_and_reconcile() -> tuple[Any, tuple[Any, ...]]:
                current_bindings = bindings
                current_revision = scene_reference_revision
                scene_publication = await sqlite_store.publish_scene_plan_atomic(
                    episode_number=episode,
                    scenes=draft.scenes,
                    scene_menu=draft.scene_menu,
                    scene_baseline_digests=draft.scene_baseline_digests,
                    episode_scene_menu_baseline_digest=(
                        draft.episode_scene_menu_baseline_digest
                    ),
                    scene_catalog_baseline_digest=scene_catalog_digest,
                    prop_catalog_baseline_digest=prop_catalog_digest,
                    bindings=current_bindings,
                    refresh_cache=False,
                )
                for _attempt in range(3):
                    (
                        current_scene_slots,
                        observed_revision,
                    ) = _scene_reference_catalog_snapshot(
                        ctx=ctx,
                        scenes=scene_projection_items,
                    )
                    if observed_revision == current_revision:
                        return scene_publication, current_bindings
                    current_revision = observed_revision
                    current_bindings = _episode_asset_bindings(
                        asset_kind=asset_kind,
                        project_id=ctx.project_id,
                        episode_number=episode,
                        director_plan=final_active,
                        changed_entities=draft.scenes,
                        characters=characters,
                        scenes=scenes,
                        props=props,
                        available_scene_reference_slots=current_scene_slots,
                    )
                    await sqlite_store.replace_planned_reference_bindings_atomic(
                        episode,
                        ("scene_base", "scene_variant"),
                        current_bindings,
                    )
                await sqlite_store.replace_planned_reference_bindings_atomic(
                    episode,
                    ("scene_base", "scene_variant"),
                    (),
                )
                raise ValueError("SCENE_REFERENCE_CATALOG_BUSY")

            publication, bindings = await _shield_scene_publication(
                publish_and_reconcile()
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
