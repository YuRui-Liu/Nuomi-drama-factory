"""Background task runner for revisioned whole-episode director plans."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from novelvideo.director_plan.models import SourceSpan
from novelvideo.director_plan.planner import DirectorPlanInput, DirectorPlanner
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

DIRECTOR_PLAN_TIMEOUT_SECONDS = 180
_DIALOGUE_LINE = re.compile(r"^([^\s:：]{1,24})\s*[:：]\s*(.+)$")
_SCENE_PREFIX = re.compile(r"^场景\s*[:：]\s*(.+)$")
_SCENE_KIND = re.compile(r"^(内景|外景|内|外)\s+(.+)$")
_SCENE_TIMES = {"日", "夜", "晨", "暮", "白天", "晚上", "黄昏", "清晨", "深夜"}
_SCENE_KINDS = {"内景", "外景", "内", "外"}


class DirectorPlanTaskError(RuntimeError):
    """A structured, caller-safe director-plan task failure."""

    def __init__(
        self,
        error_code: str,
        *,
        validation_report: dict[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.validation_report = validation_report or {"passed": False, "issues": []}
        super().__init__(error_code)


async def _build_episode_source_store(ctx: ProjectContext) -> EpisodeSourceStore:
    from novelvideo.api.deps import make_sqlite_store_for_context

    return EpisodeSourceStore(await make_sqlite_store_for_context(ctx))


def _scene_heading(line: str) -> tuple[str, str] | None:
    match = _SCENE_PREFIX.match(line.strip())
    value = match.group(1).strip() if match else line.strip()
    tokens = value.split()
    if not match and not (
        any(token in _SCENE_KINDS for token in tokens)
        and any(token in _SCENE_TIMES for token in tokens)
    ):
        return None
    time = "unspecified"
    if tokens and tokens[-1] in _SCENE_TIMES:
        time = tokens.pop()
    tokens = [token for token in tokens if token not in _SCENE_KINDS]
    scene = " ".join(tokens).strip() or "unspecified"
    return scene, time


def _source_spans(episode: int, content: str) -> tuple[SourceSpan, ...]:
    scene = "unspecified"
    time = "unspecified"
    spans: list[SourceSpan] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ordinal = len(spans) + 1
        heading = _scene_heading(line)
        dialogue_text = ""
        if heading is not None:
            heading_scene, heading_time = heading
            scene = heading_scene
            if heading_time != "unspecified":
                time = heading_time
        else:
            dialogue = _DIALOGUE_LINE.match(line)
            if dialogue is not None:
                dialogue_text = dialogue.group(2).strip()
        spans.append(
            SourceSpan(
                id=f"ep{episode:03d}-line{ordinal:04d}",
                ordinal=ordinal,
                scene=scene,
                time=time,
                text=line,
                dialogue_text=dialogue_text,
            )
        )
    if not spans:
        raise DirectorPlanTaskError("EPISODE_SOURCE_EMPTY")
    return tuple(spans)


async def _build_director_plan_input(
    payload: dict[str, Any], ctx: ProjectContext
) -> DirectorPlanInput:
    episode = int(payload["episode"])
    source_revision = int(payload["source_revision"])
    payload_project_id = str(payload["project_id"])
    if payload_project_id != str(ctx.project_id):
        raise DirectorPlanTaskError("PROJECT_SCOPE_MISMATCH")
    repository = await _build_episode_source_store(ctx)
    source = next(
        (
            item
            for item in await repository.list_sources()
            if int(item.episode_number) == episode
        ),
        None,
    )
    if source is None:
        raise DirectorPlanTaskError("EPISODE_SOURCE_NOT_FOUND")
    if int(source.source_revision) != source_revision:
        raise DirectorPlanTaskError("SOURCE_REVISION_CONFLICT")
    return DirectorPlanInput(
        episode=episode,
        source_script_hash=str(source.content_hash),
        source_spans=_source_spans(episode, str(source.content)),
        relevant_bible={},
        aspect_ratio="9:16",
        style_director={},
        project_style_snapshot_id=f"source-revision:{source_revision}",
    )


def _build_director_plan_service(ctx: ProjectContext) -> DirectorPlanService:
    return DirectorPlanService(DirectorPlanStore(ctx.output_dir), DirectorPlanner())


def _validation_report(revision: Any) -> dict[str, Any]:
    report = revision.validation_report
    if hasattr(report, "model_dump"):
        return dict(report.model_dump(mode="json"))
    return dict(report)


async def _run_director_plan(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    episode = int(payload["episode"])
    scope = str(envelope.get("scope") or f"revision:{payload['source_revision']}")
    manager = get_task_manager()

    def progress(value: float, stage: str) -> None:
        manager.update_progress_for_project(
            ctx,
            "director_plan",
            episode,
            scope=scope,
            progress=value,
            current_task=stage,
            logs=[stage],
            expected_task_id=str(envelope.get("__run_task_id") or "") or None,
        )

    input_value = await _build_director_plan_input(payload, ctx)
    progress(0.05, "M1 source_locked")
    service = _build_director_plan_service(ctx)
    try:
        revision = await asyncio.wait_for(
            service.create_draft(input_value),
            timeout=DIRECTOR_PLAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise DirectorPlanTaskError("DIRECTOR_PLAN_TIMEOUT") from exc
    except DirectorPlanTaskError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "director_plan_failed"))
        raise DirectorPlanTaskError(code) from exc
    progress(0.55, "M1 episode_planned")
    report = _validation_report(revision)
    progress(0.8, "M1 validated")
    if not bool(report.get("passed")) or str(revision.status) != "review_required":
        raise DirectorPlanTaskError(
            "DIRECTOR_PLAN_VALIDATION_FAILED", validation_report=report
        )
    progress(1.0, "M1 review_ready")
    return {
        "revision_id": str(revision.revision_id),
        "status": str(revision.status),
        "validation_report": report,
    }


def run_director_plan(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_director_plan(envelope, ctx),
            envelope,
            task_type="director_plan",
        )
    )


register_project_task_runner("director_plan", run_director_plan)
