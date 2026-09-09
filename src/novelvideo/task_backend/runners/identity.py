"""Celery runner for episode identity planning."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from novelvideo.narrative_groups.reference_uploads import validate_reference_image
from novelvideo.project_context import ProjectContext
from novelvideo.production_workflow import (
    ProductionWorkflowStore,
    production_workflow_project_lock,
)
from novelvideo.production_workflow.character_portraits import (
    materialize_legacy_character_portrait,
    reconcile_character_portrait_canonical,
    validate_character_name,
)
from novelvideo.production_workflow.slot_ids import (
    character_portrait_slot_id,
    character_state_slot_id,
)
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

logger = logging.getLogger(__name__)


def _immutable_legacy_identity_reference(
    *,
    root: Path,
    character_root: Path,
    image_path: Path,
) -> tuple[Path, bool]:
    validated = validate_reference_image(image_path, allowed_roots=(character_root,))
    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[validated.mime_type]
    versions_root = (
        character_root / "identities" / "_workflow_versions"
    ).resolve(strict=False)
    versions_root.relative_to(character_root)
    immutable_path = (
        versions_root / f"legacy-{validated.sha256[:20]}{extension}"
    ).resolve(strict=False)
    immutable_path.relative_to(character_root)
    stage = immutable_path.with_name(
        f".{immutable_path.stem}.{uuid.uuid4().hex}.stage{immutable_path.suffix}"
    )
    created = False
    try:
        versions_root.mkdir(parents=True, exist_ok=True)
        if immutable_path.is_file():
            existing = validate_reference_image(
                immutable_path,
                allowed_roots=(character_root,),
            )
            if existing.sha256 != validated.sha256:
                raise RuntimeError("legacy identity history content mismatch")
        else:
            shutil.copy2(image_path, stage)
            staged = validate_reference_image(stage, allowed_roots=(character_root,))
            if staged.sha256 != validated.sha256:
                raise RuntimeError("legacy identity changed while being copied")
            with stage.open("rb") as staged_file:
                os.fsync(staged_file.fileno())
            os.replace(stage, immutable_path)
            created = True
    finally:
        stage.unlink(missing_ok=True)
    immutable_path.relative_to(root)
    return immutable_path, created


def _available_character_identity_ids(
    *,
    ctx: ProjectContext,
    characters,
) -> frozenset[str]:
    """Return identities backed by a valid workflow current and real image."""

    root = Path(ctx.output_dir).resolve()
    state_dir = Path(ctx.state_dir)
    available: set[str] = set()
    with production_workflow_project_lock(state_dir):
        workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
        for character in characters:
            try:
                character_name = validate_character_name(
                    str(getattr(character, "name", "") or "")
                )
                canonical_characters_root = root / "assets" / "characters"
                characters_root = canonical_characters_root.resolve(strict=False)
                if characters_root != canonical_characters_root:
                    raise ValueError("characters root must not be redirected")
                characters_root.relative_to(root)
                character_root = (characters_root / character_name).resolve(
                    strict=False
                )
                character_root.relative_to(characters_root)
            except ValueError:
                continue
            for identity in getattr(character, "identities", ()) or ():
                identity_id = str(getattr(identity, "identity_id", "") or "").strip()
                try:
                    slot_id = character_state_slot_id(character_name, identity_id)
                except ValueError:
                    continue
                try:
                    slot, versions = workflow.get_slot(slot_id)
                except KeyError:
                    legacy_path = None
                    for raw_path in getattr(identity, "reference_images", ()) or ():
                        candidate = Path(str(raw_path or ""))
                        if not candidate.is_absolute():
                            candidate = root / candidate
                        try:
                            validated = validate_reference_image(
                                candidate,
                                allowed_roots=(character_root,),
                            )
                            legacy_path = Path(validated.image_path)
                            break
                        except (OSError, ValueError):
                            continue
                    if legacy_path is None:
                        continue
                    try:
                        immutable_path, created = _immutable_legacy_identity_reference(
                            root=root,
                            character_root=character_root,
                            image_path=legacy_path,
                        )
                    except (OSError, ValueError, RuntimeError):
                        continue
                    workflow_snapshot = workflow.capture_file_snapshot()
                    try:
                        workflow.materialize_legacy_current(
                            slot_id=slot_id,
                            asset_kind="character_state",
                            asset_path=immutable_path.relative_to(root).as_posix(),
                        )
                    except (OSError, ValueError, RuntimeError):
                        workflow.restore_file_snapshot(workflow_snapshot)
                        if created:
                            immutable_path.unlink(missing_ok=True)
                        continue
                    slot, versions = workflow.get_slot(slot_id)
                if (
                    slot.slot_id != slot_id
                    or slot.asset_kind != "character_state"
                    or not slot.current_version_id
                ):
                    continue
                current = versions.get(slot.current_version_id)
                if (
                    current is None
                    or current.slot_id != slot_id
                    or current.adoption_status.value not in {"provisional", "adopted"}
                ):
                    continue
                metadata_identity_id = str(
                    (current.generation_metadata or {}).get("identity_id") or ""
                ).strip()
                if metadata_identity_id and metadata_identity_id != identity_id:
                    continue
                image_path = Path(current.asset_path)
                if not image_path.is_absolute():
                    image_path = root / image_path
                try:
                    validate_reference_image(
                        image_path,
                        allowed_roots=(character_root,),
                    )
                except (OSError, ValueError):
                    continue
                available.add(identity_id)
    return frozenset(available)


def _available_character_portraits(
    *,
    ctx: ProjectContext,
    characters,
) -> frozenset[str]:
    root = Path(ctx.output_dir).resolve()
    state_dir = Path(ctx.state_dir)
    available: set[str] = set()
    with production_workflow_project_lock(state_dir):
        workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
        for character in characters:
            try:
                character_name = validate_character_name(
                    str(getattr(character, "name", "") or "")
                )
            except ValueError:
                continue
            try:
                slot_id = character_portrait_slot_id(character_name)
            except ValueError:
                continue
            try:
                slot, _versions = workflow.get_slot(slot_id)
            except KeyError:
                slot = None
            if slot is not None and slot.asset_kind != "character_portrait":
                logger.warning(
                    "ignoring incompatible character portrait slot kind",
                    extra={"slot_id": slot_id, "asset_kind": slot.asset_kind},
                )
                continue
            try:
                if not materialize_legacy_character_portrait(
                    workflow=workflow,
                    project_dir=root,
                    character_name=character_name,
                ):
                    continue
                slot, _versions = workflow.get_slot(slot_id)
            except (OSError, ValueError, RuntimeError):
                continue
            if slot.asset_kind != "character_portrait":
                logger.warning(
                    "ignoring incompatible character portrait slot kind",
                    extra={"slot_id": slot_id, "asset_kind": slot.asset_kind},
                )
                continue
            try:
                usable = reconcile_character_portrait_canonical(
                    workflow=workflow,
                    project_dir=root,
                    character_name=character_name,
                )
            except (OSError, ValueError):
                continue
            if usable:
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
    available_character_identity_ids=None,
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
        available_character_identity_ids=available_character_identity_ids,
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
    available_character_identity_ids = _available_character_identity_ids(
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
            available_character_identity_ids=available_character_identity_ids,
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
