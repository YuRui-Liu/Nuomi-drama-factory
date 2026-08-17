"""Runner for revision-safe episode imports."""
from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.episode_import_service import (
    CogneeShadowGraph,
    EpisodeImportGraphError,
    EpisodeImportService,
)
from novelvideo.episode_source_store import (
    EpisodeImportPreviewNotFound,
    EpisodeSourceRevisionConflict,
    EpisodeSourceStore,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


async def _build_service(ctx: ProjectContext) -> EpisodeImportService:
    from novelvideo.api.deps import make_sqlite_store_for_context

    repository = EpisodeSourceStore(await make_sqlite_store_for_context(ctx))
    graph = CogneeShadowGraph(project_name=ctx.owner_project_label, project_dir=ctx.output_dir, state_dir=ctx.state_dir)
    return EpisodeImportService(repository=repository, graph=graph)


async def _run_episode_import(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    service = await _build_service(ctx)
    snapshot = dict(payload["snapshot"])
    resolutions = {int(number): decision for number, decision in dict(snapshot.get("resolutions") or {}).items()}
    episodes = []
    for item in payload.get("items") or snapshot.get("items") or []:
        number = int(item["episode_number"])
        decision = resolutions.get(number)
        status = "skipped" if decision == "skip" else "overwritten" if decision == "overwrite" else "added"
        episodes.append({"episode_number": number, "status": status})
    try:
        prepared = await service.commit({
            "snapshot_items": snapshot.get("items") or [],
            "preview_id": snapshot["preview_id"],
            "expected_revision": int(snapshot["expected_revision"]),
            "resolutions": resolutions,
            "audit_id": str(envelope.get("task_id") or envelope.get("id") or snapshot["preview_id"]),
            "audit_episodes": episodes,
        })
    except EpisodeImportPreviewNotFound as exc:
        raise RuntimeError("EPISODE_IMPORT_PREVIEW_STALE") from exc
    except EpisodeSourceRevisionConflict as exc:
        raise RuntimeError("EPISODE_IMPORT_REVISION_CONFLICT") from exc
    except EpisodeImportGraphError as exc:
        overwrite = any(value == "overwrite" for value in resolutions.values())
        code = "EPISODE_IMPORT_GRAPH_REBUILD_FAILED" if overwrite else "EPISODE_IMPORT_GRAPH_INCREMENT_FAILED"
        raise RuntimeError(code) from exc
    return {"target_revision": prepared.target_revision, "episodes": episodes}


def run_episode_import(envelope: dict[str, Any], ctx: ProjectContext):
    return asyncio.run(await_envelope_with_cancel_watch(_run_episode_import(envelope, ctx), envelope, task_type="episode_import"))


register_project_task_runner("episode_import", run_episode_import)
