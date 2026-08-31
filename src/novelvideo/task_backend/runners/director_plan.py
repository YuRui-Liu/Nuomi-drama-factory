"""Background task runner for revisioned whole-episode director plans."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from novelvideo.director_plan.models import SourceSpan
from novelvideo.director_plan.migration import LegacyShotAsset
from novelvideo.director_plan.planner import DirectorPlanInput, DirectorPlanner
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.screenplay_semantics import ScreenplaySemanticStore
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
    semantic = _load_active_semantic_revision(ctx, episode)
    if semantic is None:
        raise DirectorPlanTaskError("SCREENPLAY_SEMANTICS_REQUIRED")
    if semantic.source_revision != source_revision:
        raise DirectorPlanTaskError("SCREENPLAY_SEMANTICS_SOURCE_CONFLICT")
    from novelvideo.project_config import load_project_config_file_from_state_dir
    from novelvideo.services.style_service import StyleService

    config = load_project_config_file_from_state_dir(ctx.state_dir)
    project_style = str(config.get("visual_style") or "chinese_period_drama")
    style_override = str(payload.get("style_id") or "").strip() or None
    snapshot = StyleService.resolve_style_snapshot(
        project_style,
        style_override,
        username=ctx.owner_username,
        project=ctx.project_name,
        project_dir=ctx.output_dir,
    )
    expected_snapshot_id = str(payload.get("style_snapshot_id") or "").strip()
    if expected_snapshot_id and snapshot.snapshot_id != expected_snapshot_id:
        raise DirectorPlanTaskError("STYLE_SNAPSHOT_CONFLICT")
    return DirectorPlanInput(
        episode=episode,
        source_script_hash=str(source.content_hash),
        source_spans=_semantic_source_spans(semantic),
        semantic_revision_id=semantic.revision_id,
        scenes=semantic.scenes,
        dramatic_beats=semantic.beats,
        relevant_bible={},
        aspect_ratio="9:16",
        style_director={
            "snapshot_id": snapshot.snapshot_id,
            "style_hash": snapshot.style_hash,
            "projection": snapshot.projections.director,
        },
        project_style_snapshot_id=snapshot.snapshot_id,
        project_style_snapshot=snapshot,
    )


def _load_active_semantic_revision(ctx: ProjectContext, episode: int):
    return ScreenplaySemanticStore(ctx.output_dir).load_active(episode)


def _semantic_source_spans(semantic) -> tuple[SourceSpan, ...]:
    spans: list[SourceSpan] = []
    for scene in semantic.scenes:
        for block in scene.blocks:
            spans.append(SourceSpan(
                id=block.id, ordinal=len(spans) + 1,
                scene=scene.location or scene.heading,
                time=scene.time_of_day or "unspecified", text=block.text,
                dialogue_text=block.text if block.kind == "dialogue" else "",
            ))
    if not spans:
        raise DirectorPlanTaskError("SCREENPLAY_SEMANTICS_EMPTY")
    return tuple(spans)


def _build_director_plan_service(ctx: ProjectContext) -> DirectorPlanService:
    return DirectorPlanService(DirectorPlanStore(ctx.output_dir), DirectorPlanner())


def _load_asset_migration_context(
    ctx: ProjectContext, episode: int
) -> tuple[Any | None, tuple[LegacyShotAsset, ...]]:
    output_dir = getattr(ctx, "output_dir", None)
    if output_dir is None:
        return None, ()
    root = Path(output_dir).resolve()
    active = DirectorPlanStore(root).load_active(episode)
    if active is None:
        return None, ()
    from novelvideo.narrative_groups.service import load_groups

    sidecars = {group.id: group for group in load_groups(root, episode)}
    assets: list[LegacyShotAsset] = []
    seen: set[tuple[str, str]] = set()
    for group in active.groups:
        sidecar = sidecars.get(group.id)
        if sidecar is None or (
            sidecar.director_revision_id
            and sidecar.director_revision_id != active.revision_id
        ):
            continue
        shots = {shot.id: shot for shot in group.shots}
        for stage_name in ("render", "sketch"):
            stage = sidecar.stages.get(stage_name)
            if stage is None:
                continue
            stage_style = str(stage.provider_parameters.get("style_hash") or "")
            for cell in stage.cell_assets:
                shot_id = str(cell.get("shot_id") or cell.get("beat_id") or "")
                shot = shots.get(shot_id)
                path = _safe_project_asset(root, cell.get("path"))
                if shot is None or path is None:
                    continue
                asset_id = str(cell.get("asset_id") or cell.get("id") or path)
                key = (asset_id, shot_id)
                if key in seen:
                    continue
                seen.add(key)
                assets.append(
                    _legacy_asset(
                        asset_id=asset_id,
                        asset_path=path,
                        asset_kind="image",
                        shot=shot,
                        scene=group.scene_anchor,
                        style_hash=str(cell.get("style_hash") or stage_style),
                    )
                )
        video = sidecar.stages.get("video")
        if video is not None:
            path = _safe_project_asset(root, video.video_asset)
            if path is not None:
                style_hash = str(
                    video.provider_parameters.get("style_hash")
                    or _manifest_style_hash(root, video.manifest_asset)
                )
                for shot in group.shots:
                    asset_id = f"video:{sidecar.id}:{video.revision}"
                    key = (asset_id, shot.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    assets.append(
                        _legacy_asset(
                            asset_id=asset_id,
                            asset_path=path,
                            asset_kind="video",
                            shot=shot,
                            scene=group.scene_anchor,
                            style_hash=style_hash,
                        )
                    )
    return active, tuple(assets)


def _safe_project_asset(root: Path, stored: Any) -> str | None:
    value = str(stored or "").strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return str(resolved)


def _manifest_style_hash(root: Path, stored: Any) -> str:
    path = _safe_project_asset(root, stored)
    if path is None:
        return ""
    manifest = Path(path)
    if manifest.stat().st_size > 2_000_000:
        return ""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata")
    return str(
        payload.get("style_hash")
        or (metadata.get("style_hash") if isinstance(metadata, dict) else "")
        or ""
    )


def _legacy_asset(
    *,
    asset_id: str,
    asset_path: str,
    asset_kind: str,
    shot: Any,
    scene: str,
    style_hash: str,
) -> LegacyShotAsset:
    return LegacyShotAsset(
        asset_id=asset_id,
        asset_path=asset_path,
        asset_kind=asset_kind,
        old_shot_id=shot.id,
        source_span_ids=shot.source_span_ids,
        subject=shot.subject,
        scene=scene,
        action=shot.action,
        shot_size=shot.shot_size,
        camera_angle=shot.camera_angle,
        style_hash=style_hash,
    )


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
    old_plan, assets = _load_asset_migration_context(ctx, episode)
    try:
        revision = await asyncio.wait_for(
            service.create_draft(
                input_value,
                on_stage=lambda stage: progress(
                    0.55 if stage == "episode_planned" else 0.8,
                    f"M1 {stage}",
                ),
                old_plan=old_plan,
                assets=assets,
            ),
            timeout=DIRECTOR_PLAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise DirectorPlanTaskError("DIRECTOR_PLAN_TIMEOUT") from exc
    except DirectorPlanTaskError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "director_plan_failed"))
        raise DirectorPlanTaskError(code) from exc
    report = _validation_report(revision)
    if not bool(report.get("passed")) or str(revision.status) != "review_required":
        raise DirectorPlanTaskError(
            "DIRECTOR_PLAN_VALIDATION_FAILED", validation_report=report
        )
    progress(0.85, "M2 assets_matched")
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


register_project_task_runner(
    "director_plan", run_director_plan, text_task_role="director_plan"
)
