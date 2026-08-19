"""Episode subtitle and archive export helpers shared by API and legacy UI."""

from __future__ import annotations

from pathlib import Path

from novelvideo.utils.async_ops import call_blocking
from novelvideo.utils.media_io import get_audio_duration_async


def format_srt_time(seconds: float) -> str:
    """Format seconds as SRT time: HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


async def build_srt_content(project_dir: Path, episode: int, beats: list[dict]) -> str:
    # Composition and subtitle timing must read the same H3 director manifests.
    # Import lazily to avoid loading task runners for callers that only use the
    # legacy export path.
    from novelvideo.task_backend.runners.video import resolve_episode_composition_sources

    audio_dir = project_dir / "audio" / f"ep{episode:03d}"
    spans = resolve_episode_composition_sources(project_dir, episode, beats)
    if any(span.is_director for span in spans):
        # A Director manifest is the shared source of truth for composition,
        # archive export, and subtitles.  Its entries retain real frame-based
        # boundaries, including silent shots that must still advance the clock.
        beat_by_number = {
            int(beat.get("beat_number", index)): beat
            for index, beat in enumerate(beats, 1)
        }
        srt_lines: list[str] = []
        sequence = 0
        span_offset = 0.0
        for span in spans:
            if span.is_director:
                for entry in span.entries:
                    start = span_offset + float(entry.start_seconds or 0.0)
                    end = span_offset + float(entry.end_seconds or 0.0)
                    beat = beat_by_number.get(entry.segment.beat_number, {})
                    text = entry.segment.dialogue.strip() or str(
                        beat.get("narration_segment", "")
                    ).strip()
                    if text:
                        sequence += 1
                        srt_lines.extend(
                            (
                                str(sequence),
                                f"{format_srt_time(start)} --> {format_srt_time(end)}",
                                text,
                                "",
                            )
                        )
                span_offset += float(
                    span.entries[-1].end_seconds or 0.0
                )
                continue

            for beat_number in span.beat_numbers:
                beat = beat_by_number.get(beat_number, {})
                audio_path = audio_dir / f"beat_{beat_number:02d}.mp3"
                duration = 5.0
                if audio_path.exists():
                    try:
                        duration = await get_audio_duration_async(str(audio_path))
                    except Exception:
                        pass
                start, end = span_offset, span_offset + duration
                span_offset = end
                text = str(beat.get("narration_segment", "")).strip()
                if text:
                    sequence += 1
                    srt_lines.extend(
                        (
                            str(sequence),
                            f"{format_srt_time(start)} --> {format_srt_time(end)}",
                            text,
                            "",
                        )
                    )
        return "\n".join(srt_lines)

    director_durations = {
        entry.segment.beat_number: entry.actual_duration_seconds
        for span in spans
        for entry in span.entries
    }
    srt_lines: list[str] = []
    current_time = 0.0
    seq = 0

    for index, beat in enumerate(beats, 1):
        beat_num = beat.get("beat_number", index)
        audio_path = audio_dir / f"beat_{beat_num:02d}.mp3"
        duration = director_durations.get(int(beat_num), 5.0)
        if int(beat_num) not in director_durations and audio_path.exists():
            try:
                duration = await get_audio_duration_async(str(audio_path))
            except Exception:
                duration = 5.0

        start = current_time
        end = current_time + duration
        current_time = end
        narration = beat.get("narration_segment", "")
        if not narration:
            continue
        seq += 1

        srt_lines.append(f"{seq}")
        srt_lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        srt_lines.append(narration)
        srt_lines.append("")

    return "\n".join(srt_lines)


async def build_episode_srt_file(project_dir: Path, episode: int, beats: list[dict]) -> Path | None:
    """Generate and return the episode SRT file path."""
    if not beats:
        return None

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
        files_to_pack.append((span.video_path, f"video/{group_prefix}_{span.video_path.name}"))
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
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zip_file:
            for local_path, arc_name in files_to_pack:
                zip_file.write(local_path, arc_name)

    await call_blocking(_write_zip_file)
    return zip_path


__all__ = [
    "build_episode_srt_file",
    "build_episode_zip_file",
    "build_srt_content",
    "format_srt_time",
]
