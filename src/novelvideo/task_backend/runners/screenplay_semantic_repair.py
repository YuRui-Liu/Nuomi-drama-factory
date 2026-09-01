"""Background runner for bounded Runtime screenplay semantic repair."""
from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.project_context import ProjectContext
from novelvideo.screenplay_semantics import ScreenplaySemanticStore
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


class ScreenplaySemanticRepairTaskError(RuntimeError):
    pass


async def _build_episode_source_store(ctx: ProjectContext) -> EpisodeSourceStore:
    from novelvideo.api.deps import make_sqlite_store_for_context

    return EpisodeSourceStore(await make_sqlite_store_for_context(ctx))


def _build_store(ctx: ProjectContext) -> ScreenplaySemanticStore:
    return ScreenplaySemanticStore(ctx.output_dir)


def _build_service(store: ScreenplaySemanticStore):
    # Delayed so task registration remains usable while the domain module is loaded.
    from novelvideo.screenplay_semantics.repair import ScreenplaySemanticRepairService

    return ScreenplaySemanticRepairService(store)


async def _load_source(repository: EpisodeSourceStore, episode: int):
    return next(
        (item for item in await repository.list_sources() if item.episode_number == episode),
        None,
    )


def _value(progress: object, name: str, default: int = 0) -> int:
    if isinstance(progress, dict):
        value = progress.get(name, default)
    else:
        value = getattr(progress, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _run_screenplay_semantic_repair(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    if str(payload.get("project_id")) != str(ctx.project_id):
        raise ScreenplaySemanticRepairTaskError("PROJECT_SCOPE_MISMATCH")

    episode = int(payload["episode"])
    expected_source_revision = int(payload["source_revision"])
    semantic_revision_id = str(payload["semantic_revision_id"])
    max_rounds = min(max(int(payload.get("max_rounds", 2)), 1), 2)
    concurrency = max(1, min(int(payload.get("concurrency", 3)), 20))
    repository = await _build_episode_source_store(ctx)
    source = await _load_source(repository, episode)
    if source is None:
        raise ScreenplaySemanticRepairTaskError("EPISODE_SOURCE_NOT_FOUND")
    if int(source.source_revision) != expected_source_revision:
        raise ScreenplaySemanticRepairTaskError("SOURCE_REVISION_CONFLICT")

    store = _build_store(ctx)
    base = store.load(episode, semantic_revision_id)
    if base is None:
        raise ScreenplaySemanticRepairTaskError("SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND")
    if int(base.source_revision) != expected_source_revision:
        raise ScreenplaySemanticRepairTaskError("SOURCE_REVISION_CONFLICT")

    initial_errors = tuple(
        issue
        for issue in base.validation_report.issues
        if getattr(issue, "severity", "error") == "error"
    )
    targeted_scene_ids = {
        str(issue.scene_id) for issue in initial_errors if getattr(issue, "scene_id", None)
    }
    manager = get_task_manager()
    expected_task_id = str(envelope.get("__run_task_id") or "") or None
    latest_round = 0

    async def before_commit() -> None:
        current_source = await _load_source(repository, episode)
        if (
            current_source is None
            or int(current_source.source_revision) != expected_source_revision
        ):
            raise ScreenplaySemanticRepairTaskError("SOURCE_REVISION_CONFLICT")
        current = store.load(episode, semantic_revision_id)
        if current is None or current != base:
            raise ScreenplaySemanticRepairTaskError("SEMANTIC_REVISION_CONFLICT")

    def on_progress(progress: object) -> None:
        nonlocal latest_round
        latest_round = max(latest_round, _value(progress, "repair_round", 1))
        if isinstance(progress, dict):
            pending = tuple(progress.get("pending_scene_ids") or ())
            completed = tuple(progress.get("completed_scene_ids") or ())
        else:
            pending = tuple(getattr(progress, "pending_scene_ids", ()) or ())
            completed = tuple(getattr(progress, "completed_scene_ids", ()) or ())
        current_round = max(1, latest_round)
        manager.update_progress_for_project(
            ctx,
            "screenplay_semantic_repair",
            episode,
            progress=min(0.95, 0.1 + 0.4 * current_round),
            current_task=f"Runtime 修复第 {current_round}/{max_rounds} 轮",
            logs=[
                f"completed_scenes={len(completed)} pending_scenes={len(pending)}"
            ],
            expected_task_id=expected_task_id,
        )

    manager.update_progress_for_project(
        ctx,
        "screenplay_semantic_repair",
        episode,
        progress=0.05,
        current_task="锁定剧本与语义基础版本",
        logs=[
            f"source_revision={expected_source_revision}",
            f"semantic_revision_id={semantic_revision_id}",
        ],
        expected_task_id=expected_task_id,
    )
    child = await _build_service(store).repair(
        base,
        max_rounds=max_rounds,
        concurrency=concurrency,
        before_commit=before_commit,
        on_progress=on_progress,
    )

    remaining_errors = tuple(
        issue
        for issue in child.validation_report.issues
        if getattr(issue, "severity", "error") == "error"
    )
    remaining_scene_ids = {
        str(issue.scene_id)
        for issue in remaining_errors
        if getattr(issue, "scene_id", None)
    }
    contract_violations = sum(
        getattr(issue, "code", "") == "repair_contract_violation"
        for issue in child.validation_report.issues
    )
    repaired_scene_ids = targeted_scene_ids - remaining_scene_ids
    rounds = latest_round or (1 if targeted_scene_ids else 0)
    manager.update_progress_for_project(
        ctx,
        "screenplay_semantic_repair",
        episode,
        progress=1.0,
        current_task="导演拆解 Runtime 修复已生成待审版本",
        logs=[
            f"repaired_scenes={len(repaired_scene_ids)}",
            f"remaining_issues={len(remaining_errors)}",
        ],
        expected_task_id=expected_task_id,
    )
    return {
        "base_semantic_revision_id": semantic_revision_id,
        "semantic_revision_id": child.revision_id,
        "source_revision": expected_source_revision,
        "status": child.status,
        "rounds": rounds,
        "targeted_scenes": len(targeted_scene_ids),
        "repaired_scenes": len(repaired_scene_ids),
        "failed_scenes": len(remaining_scene_ids),
        "remaining_issues": len(remaining_errors),
        "contract_violations": contract_violations,
        "validation_report": child.validation_report.model_dump(mode="json"),
    }


def run_screenplay_semantic_repair(envelope: dict[str, Any], ctx: ProjectContext):
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_screenplay_semantic_repair(envelope, ctx),
            envelope,
            task_type="screenplay_semantic_repair",
        )
    )


register_project_task_runner(
    "screenplay_semantic_repair",
    run_screenplay_semantic_repair,
    text_task_role="screenplay_semantic_repair",
)

__all__ = [
    "ScreenplaySemanticRepairTaskError",
    "_run_screenplay_semantic_repair",
    "run_screenplay_semantic_repair",
]
