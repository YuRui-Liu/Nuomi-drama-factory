"""Episode subtitle and archive export helpers shared by API and legacy UI."""

from __future__ import annotations

from pathlib import Path

from novelvideo.utils.async_ops import call_blocking
from novelvideo.utils.media_io import get_audio_duration_async


async def _probe_caption_media(path: str) -> dict:
    import asyncio
    import json
    process = await asyncio.create_subprocess_exec(
        'ffprobe', '-v', 'error', '-show_entries',
        'stream=codec_type,nb_frames,r_frame_rate,duration', '-of', 'json', path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise ValueError('Cannot measure caption video timeline')
    return json.loads(stdout)


async def caption_video_duration(path: str) -> float:
    """Measure the visible frame timeline without an encoded audio padding tail."""
    from fractions import Fraction
    import math
    data = await _probe_caption_media(path)
    stream = next(item for item in data['streams'] if item.get('codec_type') == 'video')
    try:
        duration = int(stream['nb_frames']) / float(Fraction(stream['r_frame_rate']))
    except (KeyError, ValueError, ZeroDivisionError):
        duration = float(stream.get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Cannot measure caption video timeline')
    return duration


def format_srt_time(seconds: float) -> str:
    """Format seconds as SRT time: HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


async def build_caption_track(project_dir: Path, episode: int, beats: list[dict], *,
                              sources=None, actual_durations: list[float] | None = None,
                              ignore_saved: bool = False, allow_estimated_remap: bool = False):
    """Resolve one caption track; native speech remains unaligned until reviewed.

    A versioned epNNN.caption-events.json can be supplied by a manual editor or
    aligner. Its content fingerprint invalidates stale timing after media/script
    changes. Composition supplies measured durations of its normalized clips.
    """
    import hashlib
    import json
    from novelvideo.export.captions import CaptionTrack, estimate_events
    from novelvideo.task_backend.runners.video import resolve_episode_composition_sources

    sources = sources if sources is not None else resolve_episode_composition_sources(project_dir, episode, beats)
    def fingerprint():
        digest = hashlib.sha256(json.dumps(beats, sort_keys=True, ensure_ascii=False).encode())
        for source in sources:
            media = [source.video_path, source.manifest_path, source.original_audio_path, source.ambience_stem_path]
            media.extend(project_dir / "audio" / f"ep{episode:03d}" / f"beat_{number:02d}.mp3" for number in source.beat_numbers)
            media.extend(Path(entry.external_audio_path) for entry in source.entries if entry.external_audio_path)
            for path in media:
                if path and path.is_file():
                    digest.update(str(path).encode())
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    audio_fingerprint = await call_blocking(fingerprint)
    saved = project_dir / "videos" / "episodes" / f"ep{episode:03d}.caption-events.json"
    if saved.is_file() and not ignore_saved:
        track = CaptionTrack.model_validate_json(saved.read_text(encoding="utf-8"))
        if track.audio_fingerprint != audio_fingerprint:
            raise ValueError("CAPTION_SOURCE_CHANGED: 字幕对应的音频或脚本已改变，请重新校时")
        if actual_durations is None or (
            len(track.source_durations) == len(actual_durations)
            and all(abs(old - new) < .001 for old, new in zip(track.source_durations, actual_durations))
        ):
            return track
        raise ValueError("CAPTION_TIMELINE_CHANGED: 字幕时间轴与实际合成时长不一致")
    if actual_durations is not None and len(actual_durations) != len(sources):
        raise ValueError("caption durations must match composition sources")
    by_number = {int(beat.get("beat_number", i)): beat for i, beat in enumerate(beats, 1)}
    events = []
    offset = 0.0
    timing_basis = "actual"
    source_durations = []
    timing_notes = []
    for index, source in enumerate(sources):
        duration = actual_durations[index] if actual_durations is not None else None
        if duration is None:
            try:
                duration = await caption_video_duration(str(source.video_path))
            except Exception:
                timing_basis = "estimated"
                timing_notes.append(f'第 {index + 1} 组无法测量视频帧时长；回退时间仅可用于草稿。')
                try:
                    duration = await get_audio_duration_async(str(source.video_path))
                except Exception:
                    duration = None
        if not duration or duration <= 0:
            timing_basis = "estimated"
            duration = float(source.entries[-1].end_seconds) if source.entries else 5.0
        source_durations.append(duration)
        if source.is_director:
            manifest_end = float(source.entries[-1].end_seconds) if source.entries else duration
            remap = allow_estimated_remap and manifest_end > duration + 1e-6
            ratio = duration / manifest_end if remap else 1.0
            if remap:
                timing_notes.append(f'第 {index + 1} 组清单与实际帧时长不同；草稿已按比例重排，须按原声校时，不能作为准确字幕。')
            for entry in source.entries:
                segment = entry.segment
                text = segment.dialogue.strip() or str(by_number.get(segment.beat_number, {}).get("narration_segment", "")).strip()
                # Manifest shot boundaries are not word timestamps. Never label
                # this allocation aligned, even when the frame range is known.
                start = float(entry.start_seconds or 0) * ratio
                end = float(entry.end_seconds if entry.end_seconds is not None else duration) * ratio
                if text and (start < 0 or end <= start or start >= duration or end > duration + 1e-6):
                    raise ValueError(
                        f"CAPTION_TIMELINE_CHANGED: 对白 {segment.segment_id} 的时间范围 "
                        f"{start:g}-{end:g}s 超出实际片段 {duration:g}s，请重新校时"
                    )
                events.extend(estimate_events(f"source{index}:{segment.segment_id}", text, offset + start,
                                              offset + end, audio_fingerprint, segment.speaker))
        else:
            number = source.beat_numbers[0]
            events.extend(estimate_events(f"beat{number}", str(by_number.get(number, {}).get("narration_segment", "")),
                                          offset, offset + duration, audio_fingerprint))
        offset += duration
    if not sources:
        # Legacy text-only export remains a draft with explicit estimated basis.
        timing_basis = "estimated"
        for number, beat in by_number.items():
            duration = 5.0
            audio = project_dir / "audio" / f"ep{episode:03d}" / f"beat_{number:02d}.mp3"
            if audio.is_file():
                duration = await get_audio_duration_async(str(audio))
            events.extend(estimate_events(f"beat{number}", str(beat.get("narration_segment", "")), offset,
                                          offset + duration, audio_fingerprint))
            offset += duration
    return CaptionTrack(duration=offset, audio_fingerprint=audio_fingerprint, timing_basis=timing_basis,
                        source_durations=source_durations, events=events, timing_notes=timing_notes)


async def build_srt_content(project_dir: Path, episode: int, beats: list[dict]) -> str:
    from novelvideo.export.captions import render_srt
    return render_srt(await build_caption_track(project_dir, episode, beats))


async def build_episode_srt_file(project_dir: Path, episode: int, beats: list[dict]) -> Path | None:
    """Generate and return the episode SRT file path."""
    srt_content = await build_srt_content(project_dir, episode, beats)
    if not srt_content:
        return None

    srt_dir = project_dir / "videos" / "episodes"
    srt_dir.mkdir(parents=True, exist_ok=True)
    srt_path = srt_dir / f"ep{episode:03d}.srt"
    srt_path.write_text(srt_content, encoding="utf-8")
    return srt_path


async def build_episode_zip_file(
    project_dir: Path,
    project_name: str,
    episode: int,
    beats: list[dict],
) -> Path | None:
    """Generate and return the episode ZIP file path."""
    import zipfile

    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(project_dir), episode)
    files_to_pack: list[tuple[Path, str]] = []

    from novelvideo.task_backend.runners.video import resolve_episode_composition_sources

    for beat in beats:
        beat_num = beat.get("beat_number", 0)
        if beat_num <= 0:
            continue
        audio_path = paths.audio(beat_num)
        if audio_path.exists():
            files_to_pack.append((audio_path, f"audio/{audio_path.name}"))
    for source_index, span in enumerate(
        resolve_episode_composition_sources(project_dir, episode, beats), start=1
    ):
        # Keep the archive reversible when Director groups use the conventional
        # manifest/stem names (for example ``manifest.json`` and
        # ``original.wav``).  The stable composition order provides a compact,
        # deterministic per-group namespace.
        group_prefix = f"group_{source_index:03d}"
        video_name = (
            f"{group_prefix}_{span.video_path.name}" if span.is_director
            else span.video_path.name
        )
        files_to_pack.append((span.video_path, f"video/{video_name}"))
        if span.manifest_path is not None and span.manifest_path.exists():
            files_to_pack.append(
                (span.manifest_path, f"manifests/{group_prefix}_{span.manifest_path.name}")
            )
        for stem in (span.ambience_stem_path, span.original_audio_path):
            if stem is not None and stem.exists():
                files_to_pack.append((stem, f"stems/{group_prefix}_{stem.name}"))

    final_path = paths.final_video()
    if final_path.exists():
        files_to_pack.append((final_path, final_path.name))

    srt_path = await build_episode_srt_file(project_dir, episode, beats)
    if srt_path and srt_path.exists():
        files_to_pack.append((srt_path, srt_path.name))

    if not files_to_pack:
        return None

    zip_dir = project_dir / "videos" / "episodes"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"{project_name}_第{episode}集.zip"

    def _write_zip_file() -> None:
        import os
        import tempfile
        fd, temporary = tempfile.mkstemp(dir=zip_dir, prefix='.episode-export-', suffix='.zip')
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as zip_file:
                for local_path, arc_name in files_to_pack:
                    zip_file.write(local_path, arc_name)
            os.replace(temporary, zip_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    await call_blocking(_write_zip_file)
    return zip_path


__all__ = [
    "build_episode_srt_file",
    "build_episode_zip_file",
    "build_srt_content",
    "format_srt_time",
]
