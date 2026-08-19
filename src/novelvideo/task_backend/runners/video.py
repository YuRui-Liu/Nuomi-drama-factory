"""Celery runners for video-generation tasks."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.narrative_groups.service import load_groups
from novelvideo.utils.path_resolver import PathResolver
from novelvideo.task_backend.cancel import (
    TaskTimedOut,
    await_envelope_with_cancel_watch,
    raise_if_envelope_cancel_requested,
    remaining_timeout_seconds,
)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.subprocesses import run_project_subprocess
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.media_capabilities.video.h3_prompt_optimizer import create_h3_prompt_optimizer
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode


@dataclass(frozen=True)
class VideoSpan:
    """One physical source to place on the episode timeline.

    Director manifests intentionally retain their logical H3 entries here, but
    the physical video appears exactly once in the composition source list.
    """

    video_path: Path
    beat_numbers: tuple[int, ...]
    entries: tuple[Any, ...] = ()
    manifest_path: Path | None = None
    ambience_stem_path: Path | None = None
    original_audio_path: Path | None = None

    @property
    def is_director(self) -> bool:
        return bool(self.entries)


def resolve_episode_composition_sources(
    project_dir: str | Path, episode: int, beats: list[dict[str, Any]]
) -> list[VideoSpan]:
    """Resolve Director outputs first, then legacy per-beat video fallbacks.

    A completed narrative group's manifest is the authoritative mapping from
    logical beats to a physical H3 movie.  Old beat MP4s fill only uncovered
    beats, so migration cannot duplicate a director group in the final cut.
    """
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        load_h3_director_manifest,
    )
    root = Path(project_dir)
    director_spans: list[tuple[int, int, VideoSpan]] = []
    covered: set[int] = set()
    try:
        groups = load_groups(root, episode)
    except (OSError, ValueError):
        groups = []
    for group in sorted(groups, key=lambda item: int(getattr(item, "ordinal", 0))):
        stage = getattr(group, "stages", {}).get("video")
        manifest_name = str(getattr(stage, "manifest_asset", "") or "").strip()
        if getattr(stage, "status", "") != "completed" or not manifest_name:
            continue
        manifest_path = Path(manifest_name)
        if not manifest_path.exists():
            continue
        manifest = load_h3_director_manifest(manifest_path)
        video_path = Path(manifest.physical_video)
        if not video_path.exists():
            continue
        entries = tuple(manifest.entries)
        if any(entry.dialogue_source is DialogueSource.EXTERNAL_TTS for entry in entries):
            ambience = Path(manifest.ambience_stem_path or "")
            if manifest.ambience_stem_status != "succeeded" or not ambience.exists():
                raise RuntimeError(
                    f"Director manifest {manifest_path} requires an ambience stem for external_tts"
                )
        else:
            ambience = None
        span = VideoSpan(
                video_path=video_path,
                beat_numbers=tuple(entry.segment.beat_number for entry in entries),
                entries=entries,
                manifest_path=manifest_path,
                ambience_stem_path=ambience,
                original_audio_path=Path(manifest.original_audio_path)
                if manifest.original_audio_path else None,
        )
        # A physical Director movie is placed once, at its earliest logical
        # beat.  ``ordinal`` only breaks ties between groups with equal starts.
        director_spans.append((min(span.beat_numbers), int(getattr(group, "ordinal", 0)), span))
        covered.update(entry.segment.beat_number for entry in entries)

    paths = PathResolver(str(root), episode)
    legacy_spans: list[tuple[int, int, VideoSpan]] = []
    for index, beat in enumerate(beats, start=1):
        beat_num = int(beat.get("beat_number") or index)
        if beat_num in covered:
            continue
        legacy = paths.video(beat_num)
        if legacy.exists():
            legacy_spans.append((beat_num, index, VideoSpan(video_path=legacy, beat_numbers=(beat_num,))))

    ordered = [
        (beat_number, 0, ordinal, span)
        for beat_number, ordinal, span in director_spans
    ]
    ordered.extend(
        (beat_number, 1, index, span)
        for beat_number, index, span in legacy_spans
    )
    return [span for _beat, _kind, _tie, span in sorted(ordered)]


def _log(manager, ctx: ProjectContext, envelope: dict[str, Any], message: str) -> None:
    manager.update_progress_for_project(
        ctx,
        str(envelope["task_type"]),
        int(envelope.get("episode") or 0),
        beat_num=envelope.get("beat_num"),
        scope=envelope.get("scope"),
        current_task=message,
        logs=[message],
    )


def _resolve_video_aspect_ratio(value: object, frame_path: object) -> str:
    """Use the provider's adaptive mode when I2V should follow its first frame.

    Legacy Seedance 1.x tasks historically omitted ``ratio`` and were forced to
    9:16.  Seedance does not expose a fixed 2:3 output preset, but its I2V API
    supports ``adaptive`` specifically for following the supplied first frame.
    """
    requested = str(value or "").strip()
    if requested and requested.lower() != "auto":
        return requested
    return "adaptive" if str(frame_path or "").strip() else "9:16"


def _frame_content_sha256(path: str, *, label: str) -> str:
    frame = Path(path)
    if not frame.is_file():
        raise FileNotFoundError(f"H3 {label} frame is unavailable: {frame}")
    return hashlib.sha256(frame.read_bytes()).hexdigest()


def _h3_dialogue_required(beat: dict[str, Any], config: dict[str, Any]) -> bool:
    """Derive lip-sync intent only from explicit beat/config dialogue signals."""
    from novelvideo.media_capabilities.video.h3_beat_adapter import h3_dialogue_required

    return h3_dialogue_required(beat, config)


def _h3_prompt_context(
    *, beat: dict[str, Any], config: dict[str, Any], first_frame: str, last_frame: str | None
):
    from novelvideo.config import get_newapi_text_model_name
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext
    from novelvideo.official_defaults import DEFAULT_H3_PROMPT_OPTIMIZER_MODEL

    next_beat = config.get("next_beat") or {}
    draft = str(config.get("prompt") or "").strip()
    return H3PromptContext(
        visual_description=str(beat.get("visual_description") or beat.get("shot_description") or draft),
        narration=str(beat.get("narration") or beat.get("content") or ""),
        prev_summary=str(config.get("prev_summary") or beat.get("prev_summary") or ""),
        next_summary=str(next_beat.get("narration") or next_beat.get("content") or ""),
        first_frame_sha256=_frame_content_sha256(first_frame, label="first"),
        last_frame_sha256=(
            _frame_content_sha256(last_frame, label="last") if last_frame else None
        ),
        model_id=get_newapi_text_model_name(
            "H3_PROMPT_OPTIMIZER_MODEL", DEFAULT_H3_PROMPT_OPTIMIZER_MODEL
        ),
        dialogue_required=_h3_dialogue_required(beat, config),
    )


async def _optimize_h3_single_prompt(
    *, ctx: ProjectContext, beat_num: int, beat: dict[str, Any], config: dict[str, Any],
    first_frame: str, last_frame: str | None, duration: float, draft: str,
) -> str:
    from novelvideo.media_capabilities.video.h3_beat_adapter import (
        h3_dialogue_text,
        h3_speaker_text,
        h3_tone_text,
    )
    from novelvideo.media_capabilities.video.h3_prompt import select_mode

    segment = H3DirectorSegment(
        segment_id=f"single-{beat_num}", beat_number=max(1, beat_num), prompt=draft or "当前镜头动作连续推进。",
        duration_seconds=duration, first_frame=first_frame, last_frame=last_frame,
        dialogue=h3_dialogue_text(beat, config),
        speaker=h3_speaker_text(beat, config),
        tone=h3_tone_text(beat, config),
    )
    mode = select_mode(first_frame, last_frame, None)
    if mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
        raise ValueError("single-video H3 requires a first frame")
    optimizer = create_h3_prompt_optimizer(cache_dir=ctx.state_dir / "h3_prompt_cache")
    result = await optimizer.optimize_segment(
        segment, _h3_prompt_context(
            beat=beat, config=config, first_frame=first_frame, last_frame=last_frame
        ), mode,
    )
    return result.prompt


def _append_freezone_video_node_history(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    payload: dict[str, Any],
    job_id: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    node_id = str(payload.get("node_id") or "").strip()
    if not node_id:
        return None

    from novelvideo.freezone.history import (
        append_generation_history,
        build_node_history_record,
    )

    extra: dict[str, Any] = {}
    if payload.get("model_id"):
        extra["model"] = str(payload["model_id"])
    if payload.get("gen_mode"):
        extra["gen_mode"] = str(payload["gen_mode"])

    record = build_node_history_record(
        task_type="freezone_video_gen",
        job_id=job_id,
        task_key=project_task_state_key(
            "freezone_video_gen", ctx.project_id, 0, scope=job_id
        ),
        status="failed" if error else "completed",
        media_type="video",
        result=result,
        error=error,
        prompt=payload.get("prompt"),
        extra=extra or None,
    )

    return append_generation_history(
        project_dir=project_dir,
        canvas_id=str(payload.get("canvas_id") or "default"),
        node_id=node_id,
        record=record,
    )


async def _run_single_video_async(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    task_type = "single_video"
    episode = int(envelope.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or 0)
    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    output_dir = str(payload.get("output_dir") or ctx.output_dir)

    manager = get_task_manager()
    _log(manager, ctx, envelope, f"开始生成 Beat {beat_num} 视频")

    from novelvideo.utils.path_resolver import PathResolver

    beat = config.get("beat", {})
    frame_path = config.get("frame_path")
    video_mode = config.get("video_mode", "first_frame")
    prompt = config.get("prompt", "")
    video_duration = config.get("video_duration", 5.0)
    backend_str = config.get("video_backend", "runninghub_minimax_h3")
    last_frame_path = config.get("last_frame_path")
    seedance2_config = config.get("seedance2_config") or beat.get("seedance2_config_json")
    is_h3_backend = str(backend_str).strip().lower() == "runninghub:minimax-h3"

    paths = PathResolver(output_dir, episode)
    videos_dir = paths.videos_dir()
    videos_dir.mkdir(parents=True, exist_ok=True)
    video_path = paths.video(beat_num)
    if is_h3_backend:
        from novelvideo.media_capabilities.video.runtime import generate_h3_video

        first_frame = str(frame_path or "").strip()
        if not first_frame:
            raise ValueError("H3 single-video generation requires a first frame")
        normalized_last = str(last_frame_path).strip() if last_frame_path else None
        optimized_prompt = await _optimize_h3_single_prompt(
            ctx=ctx, beat_num=beat_num, beat=beat, config=config, first_frame=first_frame,
            last_frame=normalized_last, duration=float(video_duration), draft=str(prompt),
        )

        generated = await generate_h3_video(
            ctx=ctx,
            first_frame=first_frame,
            last_frame=normalized_last,
            prompt=optimized_prompt,
            duration=float(video_duration),
            aspect_ratio=str(config.get("ratio") or "9:16"),
            resolution=str(config["resolution"]) if config.get("resolution") else None,
            output_path=video_path.as_posix(),
            mode=str(config.get("h3_mode") or "auto"),
        )
        video_pool_id = None
        try:
            from novelvideo.generators.video_pool_indexer import add_video_to_pool

            entry = add_video_to_pool(
                videos_ep_dir=videos_dir,
                episode=episode,
                beat_num=beat_num,
                source_video_path=Path(generated.output_path),
                duration=video_duration,
                video_mode=("keyframe" if generated.actual_mode == "fl2va" else "first_frame"),
                backend="runninghub:minimax-h3",
                prompt=optimized_prompt,
            )
            video_pool_id = entry.id
        except Exception as exc:  # noqa: BLE001
            _log(manager, ctx, envelope, f"添加到视频池失败 (非致命): {exc}")
        return {
            "video_path": generated.output_path,
            "beat_num": beat_num,
            "video_pool_id": video_pool_id,
            "provider_task_id": generated.provider_task_id,
            "actual_provider": "runninghub",
            "actual_model": "runninghub:minimax-h3",
            "actual_mode": generated.actual_mode,
        }
    from novelvideo.generators.video_generator import ShotReference, create_video_generator
    from novelvideo.seedance2_i2v.pipeline import is_huimeng_seedance2_backend

    is_seedance2_backend = is_huimeng_seedance2_backend(backend_str)
    gen_kwargs: dict[str, Any] = {}
    # 非 seedance2 后端（含 seedance-1.5-pro）的清晰度走构造参数透传；
    # seedance2 的清晰度在 prepare 阶段并入 seedance2_config，无需在此重复。
    single_resolution = config.get("resolution")
    if single_resolution and not is_seedance2_backend:
        gen_kwargs["resolution"] = str(single_resolution)
    video_gen = create_video_generator(backend=backend_str, **gen_kwargs)

    def on_log(msg: str) -> None:
        _log(manager, ctx, envelope, msg)

    def on_progress(value: float) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            progress=value,
            current_task=f"生成 Beat {beat_num} 视频",
        )

    if video_mode == "keyframe" and last_frame_path and not is_seedance2_backend:
        video_duration = 5.0

    seedance2_references = []
    if is_seedance2_backend:
        from novelvideo.seedance2_i2v.models import Seedance2I2VMode
        from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

        prepared = await prepare_seedance2_generation_inputs(
            project_output=output_dir,
            episode=episode,
            beat={**beat, "seedance2_config_json": seedance2_config or "{}"},
            next_beat=config.get("next_beat"),
            video_mode=video_mode,
            prompt=prompt,
            duration=video_duration,
            resolution=(
                str(config["resolution"]) if config.get("resolution") is not None else None
            ),
            ratio=str(config["ratio"]) if config.get("ratio") is not None else None,
            prop_menu=config.get("prop_menu"),
        )
        prompt = prepared.prompt
        video_duration = prepared.duration
        frame_path = prepared.image_path
        last_frame_path = prepared.last_frame_path
        seedance2_config = prepared.seedance2_config_json
        seedance2_references = prepared.references
        video_mode = (
            "keyframe" if prepared.mode == Seedance2I2VMode.FIRST_LAST_FRAME else "first_frame"
        )

    model_references = seedance2_references
    if not is_seedance2_backend:
        model_references = [
            ShotReference(
                str(item.get("type") or "image"),
                str(item.get("path") or ""),
                str(item.get("role") or ""),
            )
            for item in config.get("references") or []
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]

    generate_kwargs = {
        "image_path": frame_path,
        "prompt": prompt,
        "output_path": video_path.as_posix(),
        "aspect_ratio": _resolve_video_aspect_ratio(config.get("ratio"), frame_path),
        "duration": video_duration,
        "on_log": on_log,
        "on_progress": on_progress,
        "last_frame_path": last_frame_path,
        "project_output_dir": output_dir,
        "episode": episode,
        "beat_num": beat_num,
        "task_type": task_type,
    }
    if model_references:
        generate_kwargs["references"] = model_references
    if config.get("audio_setting"):
        generate_kwargs["audio_setting"] = str(config["audio_setting"])
    if is_seedance2_backend:
        generate_kwargs["seedance2_config"] = seedance2_config

    result = await video_gen.generate(**generate_kwargs)
    if result.status.value != "done":
        raise RuntimeError(result.error or "视频生成失败")

    video_pool_id = None
    try:
        from novelvideo.generators.video_pool_indexer import add_video_to_pool

        entry = add_video_to_pool(
            videos_ep_dir=videos_dir,
            episode=episode,
            beat_num=beat_num,
            source_video_path=Path(video_path),
            duration=video_duration,
            video_mode=video_mode,
            backend=backend_str,
            prompt=prompt,
        )
        video_pool_id = entry.id
    except Exception as exc:  # noqa: BLE001
        on_log(f"添加到视频池失败 (非致命): {exc}")

    task_result = {
        "video_path": video_path.as_posix(),
        "beat_num": beat_num,
        "video_pool_id": video_pool_id,
    }
    provider_task_id = getattr(result, "provider_task_id", None) or getattr(result, "task_id", None)
    if provider_task_id:
        task_result["provider_task_id"] = provider_task_id
    if result.last_frame_path:
        task_result["last_frame_path"] = result.last_frame_path
    if result.last_frame_url:
        task_result["last_frame_url"] = result.last_frame_url
    return task_result


def run_single_video(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_single_video_async(envelope, ctx),
            envelope,
            task_type="single_video",
        )
    )


register_project_task_runner("single_video", run_single_video)


def _audio_duration(audio_path: Path, *, timeout_seconds: int | None = 30) -> float | None:
    if not audio_path.exists():
        return None
    import subprocess

    try:
        result = run_project_subprocess(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TaskTimedOut(timeout_seconds=timeout_seconds) from exc
    try:
        return float(result.stdout.strip())
    except Exception:
        return None


def _video_has_audio_stream(video_path: Path, *, timeout_seconds: int | None = 30) -> bool:
    if not video_path.exists():
        return False
    import subprocess

    try:
        result = run_project_subprocess(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TaskTimedOut(timeout_seconds=timeout_seconds) from exc
    return result.returncode == 0 and bool(result.stdout.strip())


async def _run_video_generation_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.manual_shots import resolve_target_video_duration
    from novelvideo.utils.path_resolver import PathResolver

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    beats = list(payload.get("beats") or [])
    video_backend = str(payload.get("video_backend") or "mock")
    resolution = str(payload.get("resolution") or "720p")
    ratio = str(payload.get("ratio") or "9:16")
    prop_menu = payload.get("prop_menu")
    use_director_render = bool(payload.get("use_director_render"))
    manager = get_task_manager()
    paths = PathResolver(output_dir, episode)
    generated: list[dict[str, Any]] = []

    for index, beat in enumerate(beats):
        beat_num = int(beat.get("beat_number") or index + 1)
        manager.update_progress_for_project(
            ctx,
            "video_generation",
            episode,
            progress=index / max(1, len(beats)),
            current_task=f"生成 Beat {beat_num} 视频...",
        )
        frame_path = paths.first_frame_for_video(
            beat_num,
            use_director_render=use_director_render,
        )
        if not frame_path.exists():
            manager.update_progress_for_project(
                ctx,
                "video_generation",
                episode,
                logs=[f"Beat {beat_num} 缺少首帧，跳过: {frame_path}"],
            )
            continue

        video_mode = str(beat.get("video_mode") or "first_frame")
        prompt = str(
            beat.get("keyframe_prompt") if video_mode == "keyframe" else beat.get("video_prompt")
            or ""
        )
        audio_path = paths.audio(beat_num)
        duration = resolve_target_video_duration(
            beat,
            _audio_duration(
                audio_path,
                timeout_seconds=remaining_timeout_seconds(envelope, default_seconds=30),
            ),
        )
        last_frame_path = None
        if video_mode == "keyframe":
            next_frame = paths.first_frame_for_video(
                beat_num + 1,
                use_director_render=use_director_render,
            )
            if next_frame.exists():
                last_frame_path = str(next_frame)
            else:
                video_mode = "first_frame"

        single_envelope = {
            "task_type": "single_video",
            "episode": episode,
            "beat_num": beat_num,
            "payload": {
                "output_dir": output_dir,
                "config": {
                    "beat": beat,
                    "next_beat": beats[index + 1] if index + 1 < len(beats) else None,
                    "frame_path": str(frame_path),
                    "video_mode": video_mode,
                    "prompt": prompt,
                    "video_duration": duration,
                    "video_backend": video_backend,
                    "last_frame_path": last_frame_path,
                    "resolution": resolution,
                    "ratio": ratio,
                    "prop_menu": prop_menu,
                },
            },
        }
        generated.append(await _run_single_video_async(single_envelope, ctx))

    return {"generated": len(generated), "items": generated}


def run_video_generation(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_video_generation_async(envelope, ctx),
            envelope,
            task_type="video_generation",
        )
    )


def run_compose_episode(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    import subprocess
    import tempfile

    from novelvideo.utils.path_resolver import PathResolver

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    beats = list(payload.get("beats") or [])
    resolution = str(payload.get("resolution") or "720x1280")
    add_subtitles = bool(payload.get("add_subtitles"))
    manager = get_task_manager()
    paths = PathResolver(output_dir, episode)
    final_dir = Path(output_dir) / "videos" / "episodes"
    final_dir.mkdir(parents=True, exist_ok=True)
    output_path = final_dir / f"ep{episode:03d}_final.mp4"

    def check_cancel() -> None:
        raise_if_envelope_cancel_requested(
            envelope,
            task_type="compose_episode",
            episode=episode,
        )

    def subprocess_timeout(default_seconds: int) -> int | None:
        return remaining_timeout_seconds(envelope, default_seconds=default_seconds)

    def run_checked(cmd: list[str], *, default_timeout_seconds: int):
        try:
            return run_project_subprocess(
                cmd,
                envelope=envelope,
                capture_output=True,
                text=True,
                timeout=subprocess_timeout(default_timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskTimedOut(
                timeout_seconds=int(envelope.get("__timeout_seconds") or 30 * 60)
            ) from exc

    try:
        target_width, target_height = map(int, resolution.split("x"))
    except Exception:
        target_width, target_height = 720, 1280

    video_clips: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        sources = resolve_episode_composition_sources(output_dir, episode, beats)
        for index, source in enumerate(sources):
            check_cancel()
            beat_num = source.beat_numbers[0]
            video_path = source.video_path
            clip_path = tmp_dir / f"source_{index:04d}.mp4"
            manager.update_progress_for_project(
                ctx,
                "compose_episode",
                episode,
                progress=index / max(1, len(sources)),
                current_task=f"合成 {'导演组' if source.is_director else 'Beat'} {beat_num}...",
            )
            if source.is_director:
                from novelvideo.media_capabilities.video.h3_timeline import DialogueSource

                # H3-native lines retain the original video audio; external TTS
                # lines use the separated ambience stem, never the original mix.
                needs_ambience = any(
                    entry.dialogue_source is DialogueSource.EXTERNAL_TTS
                    for entry in source.entries
                )
                cmd = ["ffmpeg", "-y", "-i", str(video_path)]
                if needs_ambience:
                    # Resolver guarantees this exists for every external-TTS
                    # Director span; native-only spans must not receive a
                    # phantom ``None`` input.
                    cmd.extend(["-i", str(source.ambience_stem_path)])
                filter_parts: list[str] = []
                audio_labels: list[str] = []
                input_index = 2
                for entry_index, entry in enumerate(source.entries):
                    start, end = entry.start_seconds, entry.end_seconds
                    label = f"a{entry_index}"
                    if entry.dialogue_source is DialogueSource.H3_NATIVE:
                        filter_parts.append(
                            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{label}]"
                        )
                    else:
                        tts = paths.audio(entry.segment.beat_number)
                        if not tts.exists():
                            raise RuntimeError(
                                f"Beat {entry.segment.beat_number} requires TTS for external_tts"
                            )
                        cmd.extend(["-i", str(tts)])
                        ambience_label = f"amb{entry_index}"
                        filter_parts.append(
                            f"[1:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{ambience_label}]"
                        )
                        filter_parts.append(
                            f"[{input_index}:a]atrim=duration={entry.actual_duration_seconds},"
                            f"asetpts=PTS-STARTPTS[tts{entry_index}]"
                        )
                        filter_parts.append(
                            f"[{ambience_label}][tts{entry_index}]amix=inputs=2:duration=first[{label}]"
                        )
                        input_index += 1
                    audio_labels.append(f"[{label}]")
                filter_parts.append(
                    f"{''.join(audio_labels)}concat=n={len(audio_labels)}:v=0:a=1[director_a]"
                )
                cmd.extend([
                    "-filter_complex", ";".join(filter_parts), "-map", "0:v:0", "-map", "[director_a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", str(clip_path),
                ])
                result = run_checked(cmd, default_timeout_seconds=30 * 60)
                check_cancel()
                if result.returncode == 0:
                    video_clips.append(str(clip_path))
                else:
                    manager.update_progress_for_project(
                        ctx, "compose_episode", episode,
                        logs=[f"导演组 {beat_num} 合成失败: {result.stderr[:500]}"],
                    )
                continue

            audio_path = paths.audio(beat_num)
            cmd = ["ffmpeg", "-y", "-i", str(video_path)]
            has_embedded_audio = False
            if audio_path.exists():
                cmd.extend(["-i", str(audio_path)])
                cmd.extend(
                    [
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-pix_fmt",
                        "yuv420p",
                        "-shortest",
                    ]
                )
                manager.update_progress_for_project(
                    ctx,
                    "compose_episode",
                    episode,
                    logs=[f"Beat {beat_num} 使用独立音频: {audio_path.name}"],
                )
            else:
                has_embedded_audio = _video_has_audio_stream(
                    video_path,
                    timeout_seconds=subprocess_timeout(30),
                )
                check_cancel()
            if has_embedded_audio:
                cmd.extend(
                    [
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-pix_fmt",
                        "yuv420p",
                        "-shortest",
                    ]
                )
                manager.update_progress_for_project(
                    ctx,
                    "compose_episode",
                    episode,
                    logs=[f"Beat {beat_num} 使用视频内置音轨"],
                )
            elif not audio_path.exists():
                cmd.extend(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=44100:cl=stereo",
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-pix_fmt",
                        "yuv420p",
                        "-shortest",
                    ]
            )
            cmd.append(str(clip_path))
            result = run_checked(cmd, default_timeout_seconds=30 * 60)
            check_cancel()
            if result.returncode == 0:
                video_clips.append(str(clip_path))
            else:
                manager.update_progress_for_project(
                    ctx,
                    "compose_episode",
                    episode,
                    logs=[f"Beat {beat_num} 合成失败: {result.stderr[:500]}"],
                )

        if not video_clips:
            raise RuntimeError("没有可用的视频片段")

        check_cancel()
        cmd = ["ffmpeg", "-y"]
        for clip in video_clips:
            cmd.extend(["-i", clip])
        filter_parts = []
        for index in range(len(video_clips)):
            filter_parts.append(
                f"[{index}:v]scale={target_width}:{target_height}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,format=yuv420p[v{index}]"
            )
            filter_parts.append(f"[{index}:a]aresample=44100[a{index}]")
        concat_inputs = "".join(f"[v{index}][a{index}]" for index in range(len(video_clips)))
        filter_parts.append(f"{concat_inputs}concat=n={len(video_clips)}:v=1:a=1[outv][outa]")
        cmd.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(output_path),
            ]
        )
        result = run_checked(cmd, default_timeout_seconds=30 * 60)
        check_cancel()
        if result.returncode != 0:
            raise RuntimeError(f"拼接失败: {result.stderr[:500]}")

    return {
        "video_path": output_path.as_posix(),
        "add_subtitles_requested": add_subtitles,
    }


register_project_task_runner("compose_episode", run_compose_episode)


async def _run_global_optimize_video_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    import os

    from novelvideo.agents.global_video_optimizer import (
        get_global_video_optimizer,
        prepare_global_optimizer_input,
    )
    from novelvideo.cognee import CogneeStore
    from novelvideo.utils.path_resolver import PathResolver

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beats = list(payload.get("beats") or [])
    characters = list(payload.get("characters") or [])
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    language = str(payload.get("language") or "zh")
    manager = get_task_manager()

    def log(message: str, *, progress: float | None = None) -> None:
        manager.update_progress_for_project(
            ctx,
            "global_optimize_video",
            episode,
            progress=progress,
            current_task=message,
            logs=[message],
        )

    log("开始全局视频提示词优化（仅 first_frame）...", progress=0.02)
    store = CogneeStore(
        ctx.owner_project_label,
        output_dir=output_dir,
        state_dir=str(ctx.state_dir),
    )
    try:
        await store.initialize()
        await store.load_graph_state()

        sketch_paths, color_map, _total_beats = prepare_global_optimizer_input(
            beats=beats,
            characters=characters,
            output_dir=output_dir,
            episode=episode,
            project=ctx.project_name,
        )
        if not sketch_paths:
            raise RuntimeError("找不到草图网格，请先生成草图")

        resolver = PathResolver(output_dir, episode)
        sketches_dir = str(resolver.sketches_dir())
        optimizer = get_global_video_optimizer()
        sorted_beats = sorted(beats, key=lambda beat: beat.get("beat_number", 0))
        updated_count = 0
        failure_messages: list[str] = []
        prev_prompt = None

        for index, beat in enumerate(sorted_beats):
            beat_num = int(beat.get("beat_number") or 0)
            log(
                f"Beat {beat_num}/{len(sorted_beats)}: 生成视频提示词...",
                progress=0.2 + 0.7 * index / max(1, len(sorted_beats)),
            )
            sketch_path = None
            for ext in ("png", "jpg"):
                candidate = os.path.join(sketches_dir, f"beat_{beat_num:02d}.{ext}")
                if os.path.exists(candidate):
                    sketch_path = candidate
                    break
            if not sketch_path:
                log(f"Beat {beat_num}: 无草图帧，跳过")
                continue

            prev_beat = sorted_beats[index - 1] if index > 0 else None
            next_beat = sorted_beats[index + 1] if index < len(sorted_beats) - 1 else None
            try:
                result = await optimizer.optimize_single_beat(
                    beat=beat,
                    sketch_image_path=sketch_path,
                    character_color_map=color_map,
                    language=language,
                    prev_beat=prev_beat,
                    next_beat=next_beat,
                    prev_prompt=prev_prompt,
                    total_beats=len(sorted_beats),
                )
                prompt = result["prompt"]
                beat["video_mode"] = "first_frame"
                beat["video_prompt"] = prompt
                beat["keyframe_prompt"] = None
                await store.update_beat_asset(
                    episode_number=episode,
                    beat_number=beat_num,
                    video_mode="first_frame",
                    video_prompt=prompt,
                    keyframe_prompt=None,
                )
                updated_count += 1
                prev_prompt = prompt
            except Exception as exc:  # noqa: BLE001
                failure_messages.append(f"Beat {beat_num}: {exc}")
                log(f"Beat {beat_num}: 生成失败 ({exc})")

        if updated_count == 0:
            error = f"全局优化失败：0/{len(sorted_beats)} 个 Beat 生成成功"
            if failure_messages:
                error = f"{error}；最后错误：{failure_messages[-1]}"
            raise RuntimeError(error)

        log(f"全局优化完成：成功更新 {updated_count}/{len(sorted_beats)} 个 Beat", progress=1.0)
        return {"optimized": updated_count, "beats": beats}
    finally:
        await store.close()


def run_global_optimize_video(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_global_optimize_video_async(envelope, ctx),
            envelope,
            task_type="global_optimize_video",
        )
    )


register_project_task_runner("global_optimize_video", run_global_optimize_video)


async def _run_freezone_video_gen_async(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs, run_freezone_video_gen

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    manager = get_task_manager()
    manager.update_progress_for_project(
        ctx,
        "freezone_video_gen",
        0,
        scope=job_id,
        progress=0.1,
        current_task="调用视频生成器...",
        logs=["开始 freezone 视频生成"],
    )

    try:
        out_path = await run_freezone_video_gen(
            project_dir=project_dir,
            job_id=job_id,
            prompt=str(payload.get("prompt") or ""),
            reference_items=payload.get("reference_items") or None,
            aspect_ratio=str(payload.get("aspect_ratio") or "16:9"),
            resolution=str(payload.get("resolution") or "720p"),
            duration_seconds=int(payload.get("duration_seconds") or 5),
            generate_audio=bool(payload.get("generate_audio")),
            human_review=bool(payload.get("human_review")),
            scene_optimize=str(payload.get("scene_optimize") or ""),
            backend=str(payload.get("backend") or ""),
            last_frame_path=payload.get("last_frame_path"),
            audio_setting=payload.get("audio_setting") or None,
        )
    except Exception as exc:
        _append_freezone_video_node_history(
            ctx=ctx,
            project_dir=project_dir,
            payload=payload,
            job_id=job_id,
            error=str(exc),
        )
        raise

    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }
    history_record = _append_freezone_video_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        job_id=job_id,
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


def run_freezone_video_gen(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_freezone_video_gen_async(envelope, ctx),
            envelope,
            task_type="freezone_video_gen",
        )
    )


register_project_task_runner("freezone_video_gen", run_freezone_video_gen)
