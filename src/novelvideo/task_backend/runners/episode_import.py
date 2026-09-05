"""Runner for revision-safe episode imports."""
from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.episode_source_store import (
    EpisodeImportPreviewNotFound,
    EpisodeSourceRevisionConflict,
    EpisodeSourceStore,
)
from novelvideo.knowledge_runtime.context import (
    build_project_knowledge_runtime,
    knowledge_runtime_scope,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


async def _build_repository(ctx: ProjectContext) -> EpisodeSourceStore:
    from novelvideo.api.deps import make_sqlite_store_for_context

    return EpisodeSourceStore(await make_sqlite_store_for_context(ctx))


async def _run_episode_import_with_runtime(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    runtime = build_project_knowledge_runtime(ctx)
    with knowledge_runtime_scope(runtime):
        return await _run_episode_import(envelope, ctx)


async def _run_episode_import(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    repository = await _build_repository(ctx)
    snapshot = dict(payload["snapshot"])
    resolutions = {int(number): decision for number, decision in dict(snapshot.get("resolutions") or {}).items()}
    episodes = []
    for item in payload.get("items") or snapshot.get("items") or []:
        number = int(item["episode_number"])
        decision = resolutions.get(number)
        status = "skipped" if decision == "skip" else "overwritten" if decision == "overwrite" else "added"
        episodes.append({"episode_number": number, "status": status})
    try:
        prepared = await repository.prepare_import({
            "snapshot_items": snapshot.get("items") or [],
            "preview_id": snapshot["preview_id"],
            "expected_revision": int(snapshot["expected_revision"]),
            "resolutions": resolutions,
            "audit_id": str(envelope.get("task_id") or envelope.get("id") or snapshot["preview_id"]),
            "audit_episodes": episodes,
        })
        await repository.commit_prepared(prepared)
    except EpisodeImportPreviewNotFound as exc:
        raise RuntimeError("EPISODE_IMPORT_PREVIEW_STALE") from exc
    except EpisodeSourceRevisionConflict as exc:
        raise RuntimeError("EPISODE_IMPORT_REVISION_CONFLICT") from exc
    except BaseException:
        if "prepared" in locals():
            await repository.discard_prepared(prepared)
        raise

    result = {"target_revision": prepared.target_revision, "episodes": episodes}
    if prepared.has_changes:
        result["graph_index"] = "pending"
        result["source_committed"] = True
    return result


def run_episode_import(envelope: dict[str, Any], ctx: ProjectContext):
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_episode_import_with_runtime(envelope, ctx),
            envelope,
            task_type="episode_import",
        )
    )


register_project_task_runner(
    "episode_import", run_episode_import, text_task_role="episode_normalization"
)
