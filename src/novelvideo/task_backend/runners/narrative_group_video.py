"""One-task MiniMax H3 director generation for a narrative group."""

from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Mapping

from novelvideo.media_capabilities.audio.stem_separator import (
    DemucsStemSeparator,
    StemSeparationError,
)
from novelvideo.media_capabilities.video.h3_prompt import render_h3_optimized_prompt
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3DirectorSegment,
    build_h3_timeline_data,
    save_h3_director_manifest,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.runtime import generate_h3_director_video
from novelvideo.narrative_groups.service import record_stage_result, stage_payload
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner


def _project_dir(payload: Mapping[str, Any], ctx: ProjectContext) -> Path:
    return Path(str(payload.get("project_dir") or ctx.output_dir))


async def _load_canonical_beats(ctx: ProjectContext, episode: int) -> list[dict[str, Any]]:
    """Load durable beat records at execution time; never trust queued beat content."""
    from novelvideo.api.deps import make_sqlite_store_for_context

    store = await make_sqlite_store_for_context(ctx)
    return list(await store.get_beats_as_dicts(episode))


def _first_frame(cell: Mapping[str, Any]) -> str | None:
    return str(cell.get("first_frame") or cell.get("path") or "").strip() or None


def _last_frame(cell: Mapping[str, Any]) -> str | None:
    return str(cell.get("last_frame") or "").strip() or None


def _duration(beat: Mapping[str, Any]) -> float:
    for name in ("duration_seconds", "video_duration", "duration"):
        try:
            value = float(beat.get(name))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 5.0


def _raw_prompt(beat: Mapping[str, Any]) -> str:
    for name in ("h3_prompt", "video_prompt", "motion_prompt", "visual_description", "shot_description", "description", "content"):
        value = str(beat.get(name) or "").strip()
        if value:
            return value
    return "镜头保持人物、场景和空间关系连续，完成当前叙事动作。"


def _beat_number(beat: Mapping[str, Any], fallback: int) -> int:
    for name in ("beat_number", "panel_index", "id", "beat_id"):
        try:
            return int(beat.get(name))
        except (TypeError, ValueError):
            continue
    return fallback


def _mode_for(segment: H3DirectorSegment) -> H3Mode:
    return H3Mode.FL2VA if segment.last_frame else H3Mode.I2VA


async def _optimize_one(segment: H3DirectorSegment) -> H3DirectorSegment:
    """Deterministic safe fallback that still emits H3's typed wire format.

    A service deployment can replace this seam with an LLM-backed optimizer;
    workers never submit a raw drama prompt to H3.
    """
    prompt = segment.prompt
    if "integrated_multimodal_description:" not in prompt:
        prompt = render_h3_optimized_prompt(
            mode=_mode_for(segment),
            integrated_multimodal_description=prompt,
            overall_soundscape="保留现场环境声与动作音效，避免压过人物对白。",
            non_diegetic_music="无额外配乐，或使用低存在感的叙事配乐。",
            duration_seconds=segment.duration_seconds,
            dialogue=segment.dialogue,
            speaker=segment.speaker,
            tone=segment.tone,
        )
    return segment.model_copy(update={"prompt": prompt})


async def _optimize_missing_prompts(
    segments: list[H3DirectorSegment], *, max_parallel: int | None = None
) -> list[H3DirectorSegment]:
    limit = max_parallel or max(1, int(os.getenv("DRAMACLAW_H3_PROMPT_CONCURRENCY", "3")))
    semaphore = asyncio.Semaphore(limit)

    async def optimize(segment: H3DirectorSegment) -> H3DirectorSegment:
        async with semaphore:
            return await _optimize_one(segment)

    # gather preserves source order even though the work is concurrent.
    return list(await asyncio.gather(*(optimize(segment) for segment in segments)))


def _build_segments(
    payload: Mapping[str, Any], beat_records: list[dict[str, Any]], saved: Mapping[str, Any]
) -> list[H3DirectorSegment]:
    by_id = {str(item.get("id") or item.get("beat_id") or item.get("beat_number")): item for item in beat_records}
    cells = {str(item.get("beat_id")): item for item in saved.get("cell_assets") or []}
    segments: list[H3DirectorSegment] = []
    for index, beat_id in enumerate(saved.get("beat_ids") or [], start=1):
        beat = by_id.get(str(beat_id))
        if beat is None:
            raise ValueError(f"canonical beat is unavailable: {beat_id}")
        cell = cells.get(str(beat_id), {})
        first = _first_frame(cell)
        if not first:
            raise ValueError(f"rendered first frame is unavailable: {beat_id}")
        segments.append(H3DirectorSegment(
            segment_id=str(beat_id), beat_number=_beat_number(beat, index),
            prompt=_raw_prompt(beat), duration_seconds=_duration(beat),
            first_frame=first, last_frame=_last_frame(cell),
            dialogue=str(beat.get("dialogue") or beat.get("line") or "").strip(),
            speaker=str(beat.get("speaker") or beat.get("character") or "").strip(),
            tone=str(beat.get("tone") or beat.get("emotion") or "").strip(),
        ))
    return segments


async def _separate_stems(video_path: Path, directory: Path) -> dict[str, str]:
    try:
        result = await DemucsStemSeparator().separate(video_path, directory)
    except StemSeparationError:
        return {"dialogue_stem_status": "unavailable", "ambience_stem_status": "unavailable"}
    return {
        "original_audio_path": str(result.source),
        "dialogue_stem_path": str(result.vocals),
        "ambience_stem_path": str(result.no_vocals),
        "dialogue_stem_status": "succeeded",
        "ambience_stem_status": "succeeded",
    }


async def _execute(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    project_dir = _project_dir(payload, ctx)
    group_id = str(payload["group_id"])
    revision = int(payload["revision"])
    saved = stage_payload(project_dir, episode, group_id, "video")
    if saved["revision"] != revision:
        return {"status": "stale", "group_id": group_id, "revision": revision}
    record_stage_result(project_dir, episode, group_id, "video", expected_revision=revision, status="running", error="")
    try:
        beats = await _load_canonical_beats(ctx, episode)
        # The video stage owns revision/status; frame assets are canonical render outputs.
        render_state = stage_payload(project_dir, episode, group_id, "render")
        raw_segments = _build_segments(payload, beats, render_state)
        segments = await _optimize_missing_prompts(raw_segments)
        timeline = build_h3_timeline_data(segments, strict_first_frame=True)
        video_dir = project_dir / "videos" / f"ep{episode:03d}" / "narrative_groups"
        output = video_dir / f"{group_id}_r{revision}.mp4"
        generated = await generate_h3_director_video(
            ctx, segments=tuple(segments), output_path=str(output),
            aspect_ratio=str(payload.get("aspect_ratio") or "9:16"), resolution=payload.get("resolution"),
        )
        separated = _separate_stems(Path(generated.output_path), video_dir / "stems")
        stems = await separated if inspect.isawaitable(separated) else (separated or {})
        manifest = H3DirectorOutputManifest(
            physical_video=str(generated.output_path), entries=timeline.entries,
            workflow_id=str(payload.get("workflow_id") or "" ) or None,
            provider_task_id=generated.provider_task_id,
            original_audio_path=stems.get("original_audio_path"),
            original_audio_status="succeeded" if stems.get("original_audio_path") else "unavailable",
            dialogue_stem_path=stems.get("dialogue_stem_path"),
            dialogue_stem_status=stems.get("dialogue_stem_status", "not_requested"),
            ambience_stem_path=stems.get("ambience_stem_path"),
            ambience_stem_status=stems.get("ambience_stem_status", "not_requested"),
        )
        manifest_path = output.with_suffix(".manifest.json")
        save_h3_director_manifest(manifest_path, manifest)
        record_stage_result(
            project_dir, episode, group_id, "video", expected_revision=revision, status="completed",
            video_asset=str(generated.output_path), manifest_asset=str(manifest_path),
            actual_provider="runninghub", actual_model=str(payload.get("model") or "minimax-h3"),
            actual_mode=generated.actual_mode, **stems,
        )
        return {"status": "completed", "group_id": group_id, "revision": revision,
                "video_asset": str(generated.output_path), "manifest_asset": str(manifest_path),
                "provider_task_id": generated.provider_task_id, "logical_shots": len(segments)}
    except Exception as exc:
        record_stage_result(project_dir, episode, group_id, "video", expected_revision=revision, status="failed", error=str(exc))
        raise


def run_narrative_group_video(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(_execute(envelope, ctx))


register_project_task_runner("narrative_group_video", run_narrative_group_video)

__all__ = ["run_narrative_group_video"]
