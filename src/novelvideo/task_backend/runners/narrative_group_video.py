"""One-task MiniMax H3 director generation for a narrative group."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping

from novelvideo.media_capabilities.audio.stem_separator import (
    DemucsStemSeparator,
    StemSeparationUnavailable,
)
from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptContext,
)
from novelvideo.media_capabilities.video.h3_episode_pack import (
    H3EpisodeInput,
    H3EpisodeVideoSegment,
    create_h3_episode_pack_optimizer,
)
from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_PROMPT_PROFILE_ID,
    H3_PROMPT_PROFILE_VERSION,
)
from novelvideo.media_capabilities.video.h3_prompt_compiler import (
    H3_PROMPT_COMPILER_VERSION,
)
from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityError
from novelvideo.media_capabilities.video.h3_beat_adapter import (
    h3_dialogue_required,
    h3_dialogue_text,
    h3_speaker_text,
    h3_tone_text,
)
from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    H3GenerationAttemptEvidence,
    H3TimelineEntry,
    build_h3_timeline_data,
    save_h3_director_manifest,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.quality import resolution_matches
from novelvideo.media_capabilities.video.runtime import generate_h3_director_video
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
    build_video_workflow_registry,
)
from novelvideo.narrative_groups.nonvisual import is_nonvisual_production_note
from novelvideo.narrative_groups.service import (
    generation_beats_for_group,
    load_materialized_groups,
    record_stage_result,
    record_video_segment_result,
    stage_payload,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner


def _project_dir(payload: Mapping[str, Any], ctx: ProjectContext) -> Path:
    return Path(str(payload.get("project_dir") or ctx.output_dir))


def _video_workflow_registry() -> VideoWorkflowRegistry:
    from novelvideo.api.deps import (
        get_media_capability_store,
        get_media_credential_resolver,
    )

    return build_video_workflow_registry(
        get_media_capability_store(), get_media_credential_resolver()
    )


def _workflow_definition_for_payload(
    payload: Mapping[str, Any],
) -> VideoWorkflowDefinition:
    registry = _video_workflow_registry()
    model = str(payload.get("model") or "").strip()
    if not model:
        model = registry.default(VideoWorkflowScene.NARRATIVE_GROUP).id
    return registry.resolve(model, VideoWorkflowScene.NARRATIVE_GROUP)


def _video_workflow_adapters():
    from novelvideo.media_capabilities.video.adapters import (
        H3WorkflowAdapter,
        VideoWorkflowAdapters,
    )

    # Inject through this module so existing tests and runtime instrumentation
    # can replace the H3 transport without changing adapter internals.
    return VideoWorkflowAdapters(
        (H3WorkflowAdapter(generator=generate_h3_director_video),)
    )


async def _load_canonical_beats(ctx: ProjectContext, episode: int) -> list[dict[str, Any]]:
    """Load durable beat records at execution time; never trust queued beat content."""
    from novelvideo.api.deps import make_sqlite_store_for_context

    store = await make_sqlite_store_for_context(ctx)
    return list(await store.get_beats_as_dicts(episode))


def _first_frame(cell: Mapping[str, Any]) -> str | None:
    for name in ("first_frame", "path"):
        value = cell.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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


def _planned_duration(unit: Mapping[str, Any], beat: Mapping[str, Any]) -> float:
    try:
        value = float(unit.get("duration_seconds"))
    except (TypeError, ValueError):
        return _duration(beat)
    return value if value > 0 else _duration(beat)


def _raw_prompt(beat: Mapping[str, Any]) -> str:
    for name in ("h3_prompt", "video_prompt", "motion_prompt", "visual_description", "shot_description", "description", "content"):
        value = str(beat.get(name) or "").strip()
        if value:
            return value
    return "镜头保持人物、场景和空间关系连续，完成当前叙事动作。"


def _join_beat_text(beats: list[Mapping[str, Any]], reader) -> str:
    values = [reader(beat).strip() for beat in beats]
    return "\n".join(value for value in values if value)


def _join_labels(beats: list[Mapping[str, Any]], reader) -> str:
    values = []
    for beat in beats:
        value = reader(beat).strip()
        if value and value not in values:
            values.append(value)
    return " / ".join(values)


def _pair_prompt(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    start = _raw_prompt(left).rstrip("。！？.!?")
    target = _raw_prompt(right).rstrip("。！？.!?")
    return (
        f"起始状态：{start}。目标状态：{target}。"
        "镜头保持人物、场景与空间关系连续，"
        "以连贯动作完成从起始状态到目标状态的自然过渡。"
    )


def _dialogue_source_for(beats: list[Mapping[str, Any]]) -> DialogueSource:
    sources = [
        DialogueSource(str(beat.get("dialogue_source") or DialogueSource.EXTERNAL_TTS))
        for beat in beats
    ]
    if any(source is DialogueSource.EXTERNAL_TTS for source in sources):
        return DialogueSource.EXTERNAL_TTS
    return sources[0]


def _synthetic_pair_beat(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    beats = [left, right]
    segment_id = "--".join(
        str(beat.get("id") or beat.get("beat_id") or beat.get("beat_number"))
        for beat in beats
    )
    return {
        "id": segment_id,
        "beat_id": segment_id,
        "beat_number": _beat_number(left, 1),
        "start_beat_number": _beat_number(left, 1),
        "target_beat_number": _beat_number(right, 2),
        "visual_description": _pair_prompt(left, right),
        "narration": _join_beat_text(beats, _narrative),
        "dialogue": _join_beat_text(beats, h3_dialogue_text),
        "speaker": _join_labels(beats, h3_speaker_text),
        "tone": _join_labels(beats, h3_tone_text),
        "dialogue_required": any(h3_dialogue_required(beat) for beat in beats),
        "dialogue_source": _dialogue_source_for(beats).value,
        "duration_seconds": sum(_duration(beat) for beat in beats),
    }


def _beat_number(beat: Mapping[str, Any], fallback: int) -> int:
    for name in ("beat_number", "panel_index", "id", "beat_id"):
        try:
            return int(beat.get(name))
        except (TypeError, ValueError):
            continue
    return fallback


def _mode_for(segment: H3DirectorSegment) -> H3Mode:
    return H3Mode.FL2VA if segment.last_frame else H3Mode.I2VA


def _frame_sha256(path: str) -> str:
    frame = Path(path)
    if not frame.is_file():
        raise FileNotFoundError(f"H3 frame is unavailable: {frame}")
    return hashlib.sha256(frame.read_bytes()).hexdigest()


def _dialogue_required(beat: Mapping[str, Any], segment: H3DirectorSegment) -> bool:
    return h3_dialogue_required(beat)


def _narrative(beat: Mapping[str, Any]) -> str:
    for name in ("narration", "content", "description", "visual_description", "shot_description"):
        value = str(beat.get(name) or "").strip()
        if value:
            return value
    return _raw_prompt(beat)


_is_nonvisual_production_note = is_nonvisual_production_note


def _director_context(project_dir: Path, episode: int, beat_number: int) -> str:
    from novelvideo.director_world.store import load_beat_blocking

    try:
        blocking = load_beat_blocking(project_dir, episode, beat_number) or {}
    except (OSError, ValueError):
        return ""
    snapshot = blocking.get("snapshot") or {}
    payload = {
        "scene_id": blocking.get("scene_id"),
        "frame_aspect": blocking.get("frame_aspect"),
        "camera": snapshot.get("camera") or (blocking.get("frame_meta") or {}).get("camera"),
        "actors": blocking.get("actors") or snapshot.get("actors") or [],
        "props": blocking.get("props") or snapshot.get("props") or [],
        "stagings": blocking.get("stagings") or snapshot.get("stagings") or [],
    }
    compact = {
        key: value for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    return json.dumps(
        compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _optimizer_director_context(
    project_dir: Path, episode: int, beat: Mapping[str, Any]
) -> str:
    start_number = beat.get("start_beat_number")
    target_number = beat.get("target_beat_number")
    if start_number is None or target_number is None:
        return _director_context(
            project_dir, episode, _beat_number(beat, 1)
        )

    def load(number: Any) -> dict[str, Any]:
        raw = _director_context(project_dir, episode, int(number))
        if not raw:
            return {}
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {"value": decoded}

    return json.dumps(
        {
            "start_context": load(start_number),
            "target_context": load(target_number),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prompt_context(
    segment: H3DirectorSegment,
    beat: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    following: Mapping[str, Any] | None,
    *,
    director_context: str = "",
) -> H3PromptContext:
    from novelvideo.text_runtime_settings import load_text_runtime_settings

    return H3PromptContext(
        visual_description=_raw_prompt(beat),
        narration=_narrative(beat),
        prev_summary=_narrative(previous) if previous else "",
        next_summary=_narrative(following) if following else "",
        first_frame_sha256=_frame_sha256(str(segment.first_frame)),
        last_frame_sha256=_frame_sha256(str(segment.last_frame)) if segment.last_frame else None,
        model_id=load_text_runtime_settings().model,
        dialogue_required=_dialogue_required(beat, segment),
        director_context=director_context,
    )


async def _optimize_missing_prompts(
    segments: list[H3DirectorSegment],
    beats: list[Mapping[str, Any]],
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode: int,
    max_parallel: int | None = None,
    evidence_by_segment: dict[str, dict[str, Any]] | None = None,
    episode_beats: list[Mapping[str, Any]] | None = None,
) -> list[H3DirectorSegment]:
    del max_parallel
    requested_ids = {segment.segment_id for segment in segments}
    episode_segments: list[H3DirectorSegment] = []
    episode_context_beats: list[Mapping[str, Any]] = []
    segment_group_ids: dict[str, str] = {}
    source_beats = list(episode_beats or beats)
    for group in load_materialized_groups(project_dir, episode):
        render = stage_payload(project_dir, episode, group.id, "render")
        # Episode-level prompt planning may use already-rendered neighbouring
        # groups for continuity, but an unfinished sibling must never expand
        # and then fail the scope of the group that the user actually queued.
        if str(render.get("status") or "").strip().lower() != "completed":
            continue
        group_beats = generation_beats_for_group(
            project_dir, episode, group.id, source_beats
        )
        try:
            candidate = _build_segments({"mode": "auto"}, group_beats, render)
        except ValueError:
            # A stale/incomplete sibling render is context that can be omitted;
            # the requested group's segments were validated before this call.
            continue
        candidate_beats = _canonical_beats_for_segments(candidate, group_beats)
        episode_segments.extend(candidate)
        episode_context_beats.extend(candidate_beats)
        segment_group_ids.update({item.segment_id: group.id for item in candidate})
    if episode_segments:
        segments = episode_segments
        beats = list(episode_context_beats)
    optimizer = create_h3_episode_pack_optimizer(
        cache_dir=ctx.state_dir / "h3_episode_prompt_cache"
    )
    contexts = [
        _prompt_context(
            segment, beat,
            beats[index - 1] if index else None,
            beats[index + 1] if index + 1 < len(beats) else None,
            director_context=_optimizer_director_context(
                project_dir, episode, beat
            ),
        )
        for index, (segment, beat) in enumerate(zip(segments, beats, strict=True))
    ]

    active = __import__(
        "novelvideo.director_plan.store", fromlist=["DirectorPlanStore"]
    ).DirectorPlanStore(project_dir).load_active(episode)
    revision_id = str(getattr(active, "revision_id", "") or f"episode:{episode}")
    snapshot = getattr(active, "project_style_snapshot", None)
    style_hash = str(getattr(snapshot, "style_hash", "") or "legacy-style")
    style_video = {
        "projection": str(
            getattr(getattr(snapshot, "projections", None), "video", "")
        )
    }
    entries = tuple(
        H3EpisodeVideoSegment(
            segment_id=segment.segment_id,
            group_id=segment_group_ids.get(segment.segment_id, "group"),
            shot_ids=tuple(segment.segment_id.split("--")),
            duration_seconds=segment.duration_seconds,
            style_snapshot_id=str(
                getattr(snapshot, "snapshot_id", "") or "legacy-style"
            ),
            source_segment=segment,
            context=context,
            mode=_mode_for(segment),
            summary=context.visual_description or segment.prompt,
            character_anchor=segment.speaker,
            scene_anchor=context.director_context,
        )
        for segment, context in zip(segments, contexts, strict=True)
    )
    result = await optimizer.optimize(
        H3EpisodeInput(
            episode=episode,
            director_revision_id=revision_id,
            style_hash=style_hash,
            style_video=style_video,
            segments=entries,
        )
    )
    by_id = {item.segment_id: item for item in result.segments}
    optimized = []
    for segment, context in zip(segments, contexts, strict=True):
        item = by_id[segment.segment_id]
        if evidence_by_segment is not None:
            evidence_by_segment[segment.segment_id] = {
                "director_plan": item.plan.model_dump(mode="json"),
                "prompt_profile": {
                    "id": H3_PROMPT_PROFILE_ID,
                    "version": H3_PROMPT_PROFILE_VERSION,
                    "compiler_version": item.compiler_version,
                },
                "quality_report": item.quality_report.model_dump(mode="json"),
                "input_summary": _input_summary(segment, context, _mode_for(segment)),
                "_final_prompt": item.prompt,
            }
        if segment.segment_id in requested_ids:
            optimized.append(segment.model_copy(update={"prompt": item.prompt}))
    return optimized


def _input_summary(
    segment: H3DirectorSegment,
    context: H3PromptContext,
    mode: H3Mode,
) -> dict[str, Any]:
    return {
        "beat_ids": segment.segment_id.split("--"),
        "mode": mode.value,
        "duration_seconds": segment.duration_seconds,
        "first_frame_sha256": context.first_frame_sha256,
        "last_frame_sha256": context.last_frame_sha256,
    }


def _entries_with_evidence(
    entries: tuple[H3TimelineEntry, ...],
    evidence_by_segment: Mapping[str, Mapping[str, Any]],
    *,
    default_status: str,
) -> tuple[H3TimelineEntry, ...]:
    evidenced = []
    for entry in entries:
        evidence = dict(evidence_by_segment.get(entry.segment.segment_id) or {})
        final_prompt = evidence.pop("_final_prompt", None)
        status = str(evidence.pop("_status", default_status))
        segment = (
            entry.segment.model_copy(update={"prompt": final_prompt})
            if final_prompt
            else entry.segment
        )
        evidenced.append(entry.model_copy(update={
            "segment": segment,
            "status": status,
            **evidence,
        }))
    return tuple(evidenced)


def _manifest_with_status(
    manifest: H3DirectorOutputManifest,
    status: str,
    *,
    physical_video: str | None = None,
    provider_task_id: str | None = None,
    **updates: Any,
) -> H3DirectorOutputManifest:
    payload = manifest.model_dump(mode="python")
    payload.update({
        "status": status,
        "physical_video": physical_video,
        "provider_task_id": provider_task_id,
        **updates,
    })
    payload["entries"] = [
        {
            **entry.model_dump(mode="python"),
            "status": status,
            "physical_video": physical_video,
            "provider_task_id": provider_task_id,
        }
        for entry in manifest.entries
    ]
    return H3DirectorOutputManifest.model_validate(payload)


def _append_attempt(
    manifest: H3DirectorOutputManifest,
    segment_id: str,
    evidence: H3GenerationAttemptEvidence,
) -> H3DirectorOutputManifest:
    if not any(
        entry.segment.segment_id == segment_id for entry in manifest.entries
    ):
        raise ValueError(f"unknown segment: {segment_id}")
    entries = []
    for entry in manifest.entries:
        if entry.segment.segment_id != segment_id:
            entries.append(entry)
            continue
        expected = len(entry.attempts) + 1
        if evidence.attempt != expected:
            raise ValueError(
                f"next attempt for segment {segment_id} must be {expected}"
            )
        entries.append(entry.model_copy(update={
            "attempts": (*entry.attempts, evidence),
        }))
    return H3DirectorOutputManifest.model_validate({
        **manifest.model_dump(mode="python"),
        "entries": tuple(entries),
    })


def _finalize_segment_manifest(
    manifest: H3DirectorOutputManifest,
    *,
    generated: Mapping[str, tuple[str | None, str]],
    failed_ids: set[str],
    physical_video: str,
    provider_parameters: Mapping[str, object],
    actual_output: Mapping[str, int],
    **updates: Any,
) -> H3DirectorOutputManifest:
    partial = bool(failed_ids)
    terminal = "transport_failed" if partial else "completed"
    finalized = _manifest_with_status(
        manifest,
        terminal,
        physical_video=physical_video,
        provider_task_id=None if partial else manifest.provider_task_id,
        provider_parameters=dict(provider_parameters),
        actual_output=dict(actual_output),
        **updates,
    )
    entries = []
    for entry in finalized.entries:
        segment_id = entry.segment.segment_id
        if segment_id in failed_ids:
            entries.append(entry.model_copy(update={
                "status": "transport_failed", "provider_task_id": None,
                "physical_video": None,
            }))
            continue
        provider_task_id, output_path = generated[segment_id]
        entries.append(entry.model_copy(update={
            "status": "completed", "provider_task_id": provider_task_id,
            "physical_video": output_path,
        }))
    return finalized.model_copy(update={"entries": tuple(entries)})


def _build_segments(
    payload: Mapping[str, Any], beat_records: list[dict[str, Any]], saved: Mapping[str, Any]
) -> list[H3DirectorSegment]:
    requested_mode = str(payload.get("mode") or "auto").strip().lower()
    if requested_mode not in {"auto", "i2va", "fl2va"}:
        raise ValueError("MiniMax H3 group mode must be auto, i2va, or fl2va")
    by_id = {str(item.get("id") or item.get("beat_id") or item.get("beat_number")): item for item in beat_records}
    cells = {str(item.get("beat_id")): item for item in saved.get("cell_assets") or []}
    plan_units = list((saved.get("video_plan") or {}).get("units") or [])
    if plan_units:
        return _build_planned_segments(requested_mode, plan_units, by_id, cells)

    segments: list[H3DirectorSegment] = []
    for index, beat_id in enumerate(saved.get("beat_ids") or [], start=1):
        beat = by_id.get(str(beat_id))
        if beat is None:
            raise ValueError(f"canonical beat is unavailable: {beat_id}")
        cell = cells.get(str(beat_id), {})
        first = _first_frame(cell)
        if not first:
            raise ValueError(f"rendered first frame is unavailable: {beat_id}")
        last = None if requested_mode == "i2va" else _last_frame(cell)
        if requested_mode == "fl2va" and not last:
            raise ValueError(
                f"MiniMax H3 fl2va group mode requires a last frame: {beat_id}"
            )
        dialogue_source = DialogueSource(str(beat.get("dialogue_source") or DialogueSource.EXTERNAL_TTS))
        segments.append(H3DirectorSegment(
            segment_id=str(beat_id), beat_number=_beat_number(beat, index),
            prompt=_raw_prompt(beat), duration_seconds=_duration(beat),
            first_frame=first, last_frame=last,
            dialogue=h3_dialogue_text(beat),
            speaker=h3_speaker_text(beat),
            tone=h3_tone_text(beat),
            dialogue_source=dialogue_source,
        ))
    return segments


def _build_planned_segments(
    requested_mode: str,
    plan_units: list[Mapping[str, Any]],
    by_id: Mapping[str, Mapping[str, Any]],
    cells: Mapping[str, Mapping[str, Any]],
) -> list[H3DirectorSegment]:
    if requested_mode == "i2va":
        units = [
            {"beat_ids": [beat_id]}
            for unit in plan_units
            for beat_id in unit.get("beat_ids") or []
        ]
    else:
        units = plan_units

    segments = []
    for index, unit in enumerate(units, start=1):
        beat_ids = [str(value) for value in unit.get("beat_ids") or []]
        if len(beat_ids) not in {1, 2}:
            raise ValueError("video plan units must contain one or two beats")
        beats = []
        for beat_id in beat_ids:
            beat = by_id.get(beat_id)
            if beat is None:
                raise ValueError(f"canonical beat is unavailable: {beat_id}")
            beats.append(beat)

        first = _first_frame(cells.get(beat_ids[0], {}))
        if not first:
            raise ValueError(f"rendered first frame is unavailable: {beat_ids[0]}")

        if len(beats) == 1:
            beat = beats[0]
            segments.append(H3DirectorSegment(
                segment_id=beat_ids[0],
                beat_number=_beat_number(beat, index),
                prompt=_raw_prompt(beat),
                duration_seconds=_planned_duration(unit, beat),
                first_frame=first,
                last_frame=None,
                dialogue=h3_dialogue_text(beat),
                speaker=h3_speaker_text(beat),
                tone=h3_tone_text(beat),
                dialogue_source=_dialogue_source_for(beats),
            ))
            continue

        last = _first_frame(cells.get(beat_ids[1], {}))
        if not last:
            raise ValueError(
                f"planned video pair requires a rendered right frame: {beat_ids[1]}"
            )
        synthetic = _synthetic_pair_beat(beats[0], beats[1])
        segments.append(H3DirectorSegment(
            segment_id=str(synthetic["id"]),
            beat_number=int(synthetic["beat_number"]),
            prompt=str(synthetic["visual_description"]),
            duration_seconds=sum(_duration(beat) for beat in beats),
            first_frame=first,
            last_frame=last,
            dialogue=str(synthetic["dialogue"]),
            speaker=str(synthetic["speaker"]),
            tone=str(synthetic["tone"]),
            dialogue_source=_dialogue_source_for(beats),
        ))
    return segments


def _canonical_beats_for_segments(
    segments: list[H3DirectorSegment], beats: list[dict[str, Any]]
) -> list[Mapping[str, Any]]:
    by_id = {
        str(beat.get("id") or beat.get("beat_id") or beat.get("beat_number")): beat
        for beat in beats
    }
    result = []
    for segment in segments:
        exact = by_id.get(segment.segment_id)
        if exact is not None:
            result.append(exact)
            continue
        left_id, separator, right_id = segment.segment_id.partition("--")
        left = by_id.get(left_id)
        right = by_id.get(right_id) if separator else None
        if left is None or right is None:
            raise ValueError(
                f"canonical beat mapping is unavailable: {segment.segment_id}"
            )
        result.append(_synthetic_pair_beat(left, right))
    return result


async def _separate_stems(video_path: Path, directory: Path) -> dict[str, str]:
    result = await DemucsStemSeparator().separate(video_path, directory)
    return {
        "original_audio_path": str(result.source),
        "dialogue_stem_path": str(result.vocals),
        "ambience_stem_path": str(result.no_vocals),
        "dialogue_stem_status": "succeeded",
        "ambience_stem_status": "succeeded",
    }


@dataclass(frozen=True)
class SegmentProviderRequest:
    segment_ids: tuple[str, ...]
    prompt: str
    duration_seconds: float
    dialogue_source: DialogueSource
    audio_override: str | None


@dataclass(frozen=True)
class SegmentRunResult:
    segment_id: str
    status: Literal["completed", "failed"]
    output_path: str | None = None
    provider_task_id: str | None = None
    error: str = ""


SegmentTTS = Callable[[H3DirectorSegment], Awaitable[Any]]
SegmentProvider = Callable[[SegmentProviderRequest], Awaitable[Any]]


def video_segment_task_key(project: str, episode: int, segment_id: str) -> str:
    return (
        "task:narrative_group_video_segment:"
        f"project:{project}:episode:{episode}:segment:{segment_id}"
    )


async def run_video_segment(
    segment: H3DirectorSegment,
    *,
    tts: SegmentTTS | None,
    provider: SegmentProvider,
    audio_mode: Literal["project_default", "external_tts", "h3_original"] = (
        "project_default"
    ),
) -> SegmentRunResult:
    has_dialogue = bool(segment.dialogue.strip())
    use_external_tts = audio_mode == "external_tts" or (
        audio_mode == "project_default" and has_dialogue
    )
    audio_override: str | None = None
    duration = segment.duration_seconds
    if use_external_tts:
        if tts is None:
            raise ValueError("external_tts requires a segment TTS renderer")
        rendered = await tts(segment)
        audio_override = str(getattr(rendered, "audio_path", "") or "").strip()
        duration = float(getattr(rendered, "duration_seconds", 0) or 0)
        if not audio_override or duration <= 0:
            raise ValueError("segment TTS must provide audio_path and actual duration")
        dialogue_source = DialogueSource.EXTERNAL_TTS
    else:
        dialogue_source = DialogueSource.H3_NATIVE
    prompt = segment.prompt.rstrip() + "\nnon_diegetic_music=N/A"
    generated = await provider(
        SegmentProviderRequest(
            segment_ids=(segment.segment_id,),
            prompt=prompt,
            duration_seconds=duration,
            dialogue_source=dialogue_source,
            audio_override=audio_override,
        )
    )
    return SegmentRunResult(
        segment_id=segment.segment_id,
        status="completed",
        output_path=str(getattr(generated, "output_path", "") or "") or None,
        provider_task_id=(
            str(getattr(generated, "provider_task_id", "") or "") or None
        ),
    )


async def run_video_segments(
    segments: tuple[H3DirectorSegment, ...],
    *,
    tts: SegmentTTS | None,
    provider: SegmentProvider,
) -> tuple[SegmentRunResult, ...]:
    async def isolated(segment: H3DirectorSegment) -> SegmentRunResult:
        try:
            return await run_video_segment(segment, tts=tts, provider=provider)
        except Exception as exc:
            return SegmentRunResult(
                segment_id=segment.segment_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    return tuple(await asyncio.gather(*(isolated(segment) for segment in segments)))


async def _execute(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    project_dir = _project_dir(payload, ctx)
    group_id = str(payload["group_id"])
    requested_segment_id = str(payload.get("segment_id") or "").strip()
    revision = int(payload["revision"])
    workflow_parameters = dict(payload.get("workflow_parameters") or {})
    if not workflow_parameters:
        workflow_parameters = {"resolution": str(payload.get("resolution") or "720p")}
    saved = stage_payload(project_dir, episode, group_id, "video")
    if saved["revision"] != revision:
        return {"status": "stale", "group_id": group_id, "revision": revision}
    plan_revision = payload.get("plan_revision")
    saved_plan_revision = (saved.get("video_plan") or {}).get("revision")
    if (
        plan_revision is not None
        and int(plan_revision) != int(saved_plan_revision or 0)
    ):
        return {"status": "stale", "group_id": group_id, "revision": revision}
    record_stage_result(
        project_dir, episode, group_id, "video", expected_revision=revision,
        status="running", error="", workflow_parameters=workflow_parameters,
    )
    manifest_path: Path | None = None
    try:
        workflow = _workflow_definition_for_payload(payload)
        adapter = _video_workflow_adapters().resolve(workflow.adapter_key)
        source_beats = await _load_canonical_beats(ctx, episode)
        beats = generation_beats_for_group(
            project_dir, episode, group_id, source_beats
        )
        # The video stage owns revision/status; frame assets are canonical render outputs.
        render_state = stage_payload(project_dir, episode, group_id, "render")
        if plan_revision is None:
            # Tasks queued before plan revisions were frozen retain their original
            # one-beat-per-segment interpretation.
            render_state = {**render_state, "video_plan": {}}
        raw_segments = _build_segments(payload, beats, render_state)
        materialized_group = next(
            group for group in load_materialized_groups(project_dir, episode)
            if group.id == group_id
        )
        durable_segment_ids = [
            str(item.get("id")) for item in materialized_group.video_segments
        ]
        if requested_segment_id:
            try:
                requested_index = durable_segment_ids.index(requested_segment_id)
                raw_segments = [raw_segments[requested_index]]
                durable_segment_ids = [requested_segment_id]
            except (ValueError, IndexError):
                raise ValueError(
                    f"video segment is unavailable: {requested_segment_id}"
                )
        if not raw_segments:
            record_stage_result(
                project_dir, episode, group_id, "video",
                expected_revision=revision, status="completed",
                actual_provider="none", actual_model="none",
                actual_mode="skipped_nonvisual",
            )
            return {
                "status": "skipped", "reason": "nonvisual_beats",
                "group_id": group_id, "revision": revision,
            }
        segment_beats = _canonical_beats_for_segments(raw_segments, beats)
        video_dir = project_dir / "videos" / f"ep{episode:03d}" / "narrative_groups"
        segment_suffix = (
            "_segment_"
            + hashlib.sha256(requested_segment_id.encode("utf-8")).hexdigest()[:12]
            if requested_segment_id
            else ""
        )
        output = video_dir / f"{group_id}_r{revision}{segment_suffix}.mp4"
        manifest_path = output.with_suffix(".manifest.json")
        evidence_by_segment: dict[str, dict[str, Any]] = {}
        try:
            segments = await _optimize_missing_prompts(
                raw_segments, segment_beats, ctx=ctx,
                project_dir=project_dir, episode=episode,
                evidence_by_segment=evidence_by_segment,
                episode_beats=source_beats,
            )
        except H3PromptQualityError as exc:
            report = exc.report.model_dump(mode="json")
            for segment in raw_segments:
                evidence_by_segment.setdefault(segment.segment_id, {
                    "prompt_profile": {
                        "id": H3_PROMPT_PROFILE_ID,
                        "version": H3_PROMPT_PROFILE_VERSION,
                        "compiler_version": H3_PROMPT_COMPILER_VERSION,
                    },
                    "quality_report": report,
                    "input_summary": {
                        "beat_ids": segment.segment_id.split("--"),
                        "mode": _mode_for(segment).value,
                        "duration_seconds": segment.duration_seconds,
                        "first_frame_sha256": _frame_sha256(str(segment.first_frame)),
                        "last_frame_sha256": (
                            _frame_sha256(str(segment.last_frame))
                            if segment.last_frame else None
                        ),
                    },
                    "_status": "quality_rejected",
                })
            rejected_timeline = build_h3_timeline_data(
                raw_segments, strict_first_frame=True
            )
            rejected_manifest = H3DirectorOutputManifest(
                physical_video=None,
                entries=_entries_with_evidence(
                    rejected_timeline.entries,
                    evidence_by_segment,
                    default_status="quality_rejected",
                ),
                workflow_id=workflow.id,
                workflow_parameters=workflow_parameters,
                status="quality_rejected",
            )
            save_h3_director_manifest(manifest_path, rejected_manifest)
            error_payload = {
                "error_code": "H3_PROMPT_QUALITY_REJECTED",
                "transport_called": False,
                "quality_report": report,
            }
            exc.args = (json.dumps(error_payload, ensure_ascii=False, separators=(",", ":")),)
            record_stage_result(
                project_dir, episode, group_id, "video",
                expected_revision=revision,
                status="failed",
                error=str(exc),
                manifest_asset=str(manifest_path),
            )
            raise
        timeline = build_h3_timeline_data(segments, strict_first_frame=True)
        evidenced_entries = _entries_with_evidence(
            timeline.entries, evidence_by_segment, default_status="submitted"
        )
        manifest = H3DirectorOutputManifest(
            physical_video=None,
            entries=evidenced_entries,
            workflow_id=workflow.id,
            workflow_parameters=workflow_parameters,
            status="submitted",
        )
        save_h3_director_manifest(manifest_path, manifest)
        record_stage_result(
            project_dir, episode, group_id, "video",
            expected_revision=revision,
            status="running",
            error="",
            manifest_asset=str(manifest_path),
        )
        from novelvideo.media_capabilities.video.adapters import (
            NarrativeGroupVideoRequest,
        )

        try:
            generated_segments = []
            segment_errors = []
            for segment_index, segment in enumerate(segments, start=1):
                durable_segment_id = durable_segment_ids[segment_index - 1]
                segment_output = output.with_name(
                    f"{output.stem}_segment_{segment_index:03d}{output.suffix}"
                )

                async def on_provider_submitted(provider_task_id: str) -> None:
                    nonlocal manifest
                    manifest = _manifest_with_status(
                        manifest,
                        "submitted",
                        provider_task_id=provider_task_id,
                    )
                    entry = next(
                        item for item in manifest.entries
                        if item.segment.segment_id == segment.segment_id
                    )
                    manifest = _append_attempt(
                        manifest,
                        segment.segment_id,
                        H3GenerationAttemptEvidence(
                            attempt=len(entry.attempts) + 1,
                            status="submitted",
                            provider_task_id=provider_task_id,
                        ),
                    )
                    save_h3_director_manifest(manifest_path, manifest)

                try:
                    item = await adapter.generate_narrative_group(
                        ctx,
                        NarrativeGroupVideoRequest(
                            segments=(segment,),
                            output_path=str(segment_output),
                            aspect_ratio=str(payload.get("aspect_ratio") or "9:16"),
                            workflow_parameters=workflow_parameters,
                            on_provider_submitted=on_provider_submitted,
                        ),
                    )
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    entry = next(
                        current for current in manifest.entries
                        if current.segment.segment_id == segment.segment_id
                    )
                    provider_task_id = (
                        entry.attempts[-1].provider_task_id
                        if entry.attempts else None
                    )
                    manifest = _append_attempt(
                        manifest,
                        segment.segment_id,
                        H3GenerationAttemptEvidence(
                            attempt=len(entry.attempts) + 1,
                            status="transport_failed",
                            provider_task_id=provider_task_id,
                            error_code=type(exc).__name__,
                        ),
                    )
                    save_h3_director_manifest(manifest_path, manifest)
                    segment_errors.append({"segment_id": segment.segment_id, "error": message})
                    record_video_segment_result(
                        project_dir, episode, group_id, durable_segment_id,
                        status="failed", error=message,
                    )
                else:
                    entry = next(
                        current for current in manifest.entries
                        if current.segment.segment_id == segment.segment_id
                    )
                    manifest = _append_attempt(
                        manifest,
                        segment.segment_id,
                        H3GenerationAttemptEvidence(
                            attempt=len(entry.attempts) + 1,
                            status="completed",
                            provider_task_id=item.provider_task_id,
                        ),
                    )
                    save_h3_director_manifest(manifest_path, manifest)
                    generated_segments.append((segment_index, segment, item))
                    record_video_segment_result(
                        project_dir, episode, group_id, durable_segment_id,
                        status="completed", provider_task_id=item.provider_task_id,
                        result={"output_path": str(item.output_path)},
                    )
            if not generated_segments:
                raise RuntimeError(f"all video segments failed: {segment_errors}")
            from novelvideo.task_backend.runners.narrative_group_video_compose import (
                SegmentCompositionItem,
                build_local_composition_plan,
                compose_local_segments,
            )

            composition = build_local_composition_plan(
                tuple(
                    SegmentCompositionItem(
                        group_ordinal=1,
                        segment_ordinal=index,
                        path=str(item.output_path),
                        relation_to_previous="single" if index == 1 else "causal",
                        has_leading_dialogue=bool(segment.dialogue.strip()),
                    )
                    for index, segment, item in generated_segments
                )
            )
            if len(composition.paths) > 1:
                compose_local_segments(composition, output)
                generated = replace(generated_segments[-1][2], output_path=str(output))
            else:
                generated = generated_segments[0][2]
        except Exception:
            manifest = _manifest_with_status(
                manifest,
                "transport_failed",
                provider_task_id=manifest.provider_task_id,
            )
            save_h3_director_manifest(manifest_path, manifest)
            raise

        manifest = _manifest_with_status(
            manifest,
            "partial_failure" if segment_errors else "generated",
            physical_video=str(generated.output_path),
            provider_task_id=generated.provider_task_id,
            provider_parameters=generated.provider_parameters,
            actual_output=generated.actual_output,
        )
        if segment_errors:
            failed_ids = {str(item["segment_id"]) for item in segment_errors}
            manifest = manifest.model_copy(update={
                "entries": tuple(
                    entry.model_copy(update={"status": "transport_failed"})
                    if entry.segment.segment_id in failed_ids else entry
                    for entry in manifest.entries
                )
            })
        save_h3_director_manifest(manifest_path, manifest)

        expected_output = (
            int(generated.provider_parameters.get("width") or 0),
            int(generated.provider_parameters.get("height") or 0),
        )
        actual_output = (
            int(generated.actual_output.get("width") or 0),
            int(generated.actual_output.get("height") or 0),
        )
        if not resolution_matches(expected_output, actual_output, tolerance_px=32):
            message = (
                f"video output resolution mismatch: expected "
                f"{expected_output[0]}x{expected_output[1]}, got "
                f"{actual_output[0]}x{actual_output[1]}"
            )
            manifest = _manifest_with_status(
                manifest,
                "quality_mismatch",
                physical_video=str(generated.output_path),
                provider_task_id=generated.provider_task_id,
                provider_parameters=generated.provider_parameters,
                actual_output=generated.actual_output,
            )
            save_h3_director_manifest(manifest_path, manifest)
            record_stage_result(
                project_dir, episode, group_id, "video",
                expected_revision=revision, status="partial_failure", error=message,
                video_asset=str(generated.output_path), manifest_asset=str(manifest_path),
                actual_provider=workflow.provider, actual_model=workflow.id,
                actual_mode=generated.actual_mode,
                workflow_parameters=workflow_parameters,
                provider_parameters=generated.provider_parameters,
                actual_output=generated.actual_output,
            )
            return {
                "status": "partial_failure", "group_id": group_id,
                "revision": revision, "error": message,
                "video_asset": str(generated.output_path),
                "manifest_asset": str(manifest_path),
                "provider_task_id": generated.provider_task_id,
            }

        try:
            if any(segment.dialogue_source is DialogueSource.EXTERNAL_TTS for segment in segments):
                try:
                    stems = await _separate_stems(
                        Path(generated.output_path), video_dir / "stems"
                    )
                except StemSeparationUnavailable:
                    # Demucs is optional. Keep generated evidence and expose
                    # availability for later external-TTS composition.
                    stems = {
                        "original_audio_path": str(generated.output_path),
                        "dialogue_stem_status": "unavailable",
                        "ambience_stem_status": "unavailable",
                    }
                if (
                    stems.get("ambience_stem_status") != "succeeded"
                    and stems.get("ambience_stem_status") != "unavailable"
                ):
                    raise RuntimeError(
                        "external_tts requires successful H3 ambience stem separation"
                    )
            else:
                stems = {}
        except Exception:
            manifest = _manifest_with_status(
                manifest,
                "postprocess_failed",
                physical_video=str(generated.output_path),
                provider_task_id=generated.provider_task_id,
            )
            save_h3_director_manifest(manifest_path, manifest)
            raise

        generated_by_segment = {
            segment.segment_id: (item.provider_task_id, str(item.output_path))
            for _, segment, item in generated_segments
        }
        manifest = _finalize_segment_manifest(
            manifest,
            generated=generated_by_segment,
            failed_ids={str(item["segment_id"]) for item in segment_errors},
            physical_video=str(generated.output_path),
            provider_parameters=generated.provider_parameters,
            actual_output=generated.actual_output,
            original_audio_path=stems.get("original_audio_path"),
            original_audio_status=(
                "succeeded" if stems.get("original_audio_path") else "unavailable"
            ),
            dialogue_stem_path=stems.get("dialogue_stem_path"),
            dialogue_stem_status=stems.get("dialogue_stem_status", "not_requested"),
            ambience_stem_path=stems.get("ambience_stem_path"),
            ambience_stem_status=stems.get("ambience_stem_status", "not_requested"),
        )
        save_h3_director_manifest(manifest_path, manifest)
        terminal_status = "partial_failure" if segment_errors else "completed"
        record_stage_result(
            project_dir, episode, group_id, "video", expected_revision=revision,
            status=terminal_status,
            error=(json.dumps(segment_errors, ensure_ascii=False) if segment_errors else ""),
            video_asset=str(generated.output_path), manifest_asset=str(manifest_path),
            actual_provider=workflow.provider, actual_model=workflow.id,
            actual_mode=generated.actual_mode,
            workflow_parameters=workflow_parameters,
            provider_parameters=generated.provider_parameters,
            actual_output=generated.actual_output,
            **stems,
        )
        return {"status": terminal_status, "group_id": group_id, "revision": revision,
                "video_asset": str(generated.output_path), "manifest_asset": str(manifest_path),
                "provider_task_id": generated.provider_task_id, "logical_shots": len(segments)}
    except Exception as exc:
        failure_assets = (
            {"manifest_asset": str(manifest_path)}
            if manifest_path is not None and manifest_path.is_file()
            else {}
        )
        record_stage_result(
            project_dir, episode, group_id, "video",
            expected_revision=revision, status="failed", error=str(exc),
            **failure_assets,
        )
        raise


def run_narrative_group_video(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(_execute(envelope, ctx))


register_project_task_runner(
    "narrative_group_video",
    run_narrative_group_video,
    text_task_role="h3_episode_pack",
)
register_project_task_runner(
    "narrative_group_video_segment",
    run_narrative_group_video,
    text_task_role="h3_segment_repair",
)

__all__ = [
    "SegmentProviderRequest",
    "SegmentRunResult",
    "run_narrative_group_video",
    "run_video_segment",
    "run_video_segments",
    "video_segment_task_key",
]
