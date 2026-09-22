"""Recompose an episode after changing a logical H3 dialogue-source choice."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novelvideo.media_capabilities.video.h3_timeline import (
    H3TransitionRule,
    transition_for,
)
from novelvideo.narrative_groups.service import stage_payload
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner


@dataclass(frozen=True)
class SegmentCompositionItem:
    group_ordinal: int
    segment_ordinal: int
    path: str
    relation_to_previous: str
    has_leading_dialogue: bool = False
    has_trailing_ambience: bool = False


@dataclass(frozen=True)
class LocalCompositionPlan:
    paths: tuple[str, ...]
    transitions: tuple[H3TransitionRule, ...]


def _probe_duration(path: str) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"failed to probe segment duration: {completed.stderr[-500:]}")
    return float(completed.stdout.strip())


def build_ffmpeg_filter_complex(
    plan: LocalCompositionPlan, *, durations: tuple[float, ...],
    output_size: tuple[int, int] | None = None,
) -> str:
    """Compile reviewed transition rules into executable FFmpeg filters."""
    if len(durations) != len(plan.paths):
        raise ValueError("one duration is required for each composition path")
    if len(plan.transitions) != max(0, len(plan.paths) - 1):
        raise ValueError("one transition is required between adjacent paths")
    filters: list[str] = []
    video_sources = [f"{index}:v" for index in range(len(plan.paths))]
    audio_sources = [f"{index}:a" for index in range(len(plan.paths))]
    if output_size is not None:
        width, height = output_size
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError("composition output size must contain positive even dimensions")
        for index in range(len(plan.paths)):
            video_sources[index] = f"normalized_v{index}"
            audio_sources[index] = f"normalized_a{index}"
            filters.append(
                f"[{index}:v]scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps=24,settb=AVTB,setpts=PTS-STARTPTS,"
                f"format=yuv420p[{video_sources[index]}]"
            )
            filters.append(f"[{index}:a]asetpts=PTS-STARTPTS[{audio_sources[index]}]")
    if len(plan.paths) == 1:
        filters.extend((f"[{video_sources[0]}]null[outv]", f"[{audio_sources[0]}]anull[outa]"))
        return ";".join(filters)

    video_label = video_sources[0]
    visual_overlap = 0.0
    visual_starts = [0.0]
    for index, rule in enumerate(plan.transitions, start=1):
        next_video = f"v{index}"
        if rule.kind == "dissolve":
            duration = rule.frames / 24
            offset = sum(durations[:index]) - visual_overlap - duration
            filters.append(
                f"[{video_label}][{video_sources[index]}]xfade=transition=fade:"
                f"duration={duration:.6f}:offset={offset:.6f}[{next_video}]"
            )
            visual_overlap += duration
            visual_starts.append(offset)
        else:
            filters.append(
                f"[{video_label}][{video_sources[index]}]concat=n=2:v=1:a=0[{next_video}]"
            )
            visual_starts.append(sum(durations[:index]) - visual_overlap)
        video_label = next_video

    audio_labels: list[str] = []
    for index, visual_start in enumerate(visual_starts):
        incoming = plan.transitions[index - 1] if index else None
        outgoing = plan.transitions[index] if index < len(plan.transitions) else None
        main_source = audio_sources[index]
        tail_source = main_source
        if outgoing is not None and outgoing.audio == "l_cut":
            main_source = f"a{index}-main"
            tail_source = f"a{index}-tail"
            filters.append(
                f"[{audio_sources[index]}]asplit=2[{main_source}][{tail_source}]"
            )
        lead = (
            incoming.audio_ms / 1000
            if incoming is not None and incoming.audio == "j_cut"
            else 0.0
        )
        label = f"j{index}" if lead else f"a{index}"
        delay_ms = max(0, round((visual_start - lead) * 1000))
        filters.append(f"[{main_source}]adelay={delay_ms}|{delay_ms}[{label}]")
        audio_labels.append(label)

        if outgoing is not None and outgoing.audio == "l_cut":
            tail = outgoing.audio_ms / 1000
            tail_start = max(0.0, durations[index] - tail)
            boundary_ms = round(visual_starts[index + 1] * 1000)
            tail_label = f"l{index + 1}"
            filters.append(
                f"[{tail_source}]atrim=start={tail_start:.6f},asetpts=PTS-STARTPTS,"
                f"adelay={boundary_ms}|{boundary_ms}[{tail_label}]"
            )
            audio_labels.append(tail_label)

    mixed = "".join(f"[{label}]" for label in audio_labels)
    total_duration = sum(durations) - visual_overlap
    filters.extend((
        f"[{video_label}]null[outv]",
        # Unbounded apad can exhaust the filter consumer queue before the
        # muxer's -shortest stops it. End the audio stream at the video timeline.
        f"{mixed}amix=inputs={len(audio_labels)}:normalize=0,"
        f"apad=whole_dur={total_duration:.6f},atrim=duration={total_duration:.6f}[outa]",
    ))
    return ";".join(filters)


def compose_local_segments(
    plan: LocalCompositionPlan, output_path: Path, *,
    output_size: tuple[int, int] | None = None,
) -> Path:
    """Compose provider segment files in the exact reviewed order."""
    if not plan.paths:
        raise ValueError("at least one video segment is required for composition")
    if output_size is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", plan.paths[0]],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode:
            raise RuntimeError(f"failed to probe segment canvas: {probe.stderr[-500:]}")
        stream = json.loads(probe.stdout)["streams"][0]
        output_size = (int(stream["width"]), int(stream["height"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = output_path.with_suffix(".composition.json")
    manifest.write_text(
        json.dumps(
            {
                "paths": list(plan.paths),
                "output_size": list(output_size),
                "transitions": [item.model_dump(mode="json") for item in plan.transitions],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in plan.paths:
        command.extend(["-i", path])
    durations = tuple(_probe_duration(path) for path in plan.paths)
    command.extend(
        [
            "-filter_complex",
            build_ffmpeg_filter_complex(plan, durations=durations, output_size=output_size),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(output_path),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"local segment composition failed: {completed.stderr[-500:]}")
    return output_path


def build_local_composition_plan(
    items: tuple[SegmentCompositionItem, ...],
) -> LocalCompositionPlan:
    ordered = tuple(
        sorted(items, key=lambda item: (item.group_ordinal, item.segment_ordinal))
    )
    if not ordered:
        raise ValueError("at least one video segment is required for composition")
    transitions = tuple(
        transition_for(
            item.relation_to_previous,
            has_leading_dialogue=item.has_leading_dialogue,
            has_trailing_ambience=item.has_trailing_ambience,
        )
        for item in ordered[1:]
    )
    return LocalCompositionPlan(
        paths=tuple(item.path for item in ordered), transitions=transitions
    )


async def _load_canonical_beats(ctx: ProjectContext, episode: int) -> list[dict[str, Any]]:
    from novelvideo.api.deps import make_sqlite_store_for_context

    store = await make_sqlite_store_for_context(ctx)
    return list(await store.get_beats_as_dicts(episode))


async def _execute(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    """Run only final composition; director generation is intentionally absent."""
    payload = dict(envelope.get("payload") or {})
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    group_id = str(payload["group_id"])
    revision = int(payload["revision"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    state = stage_payload(project_dir, episode, group_id, "video")
    if int(state["revision"]) != revision:
        return {"status": "stale", "group_id": group_id, "revision": revision}

    from novelvideo.task_backend.runners.video import run_compose_episode

    beats = await _load_canonical_beats(ctx, episode)
    compose_envelope = {
        **envelope,
        "task_type": "compose_episode",
        "episode": episode,
        "payload": {
            "output_dir": str(project_dir),
            "beats": beats,
            "resolution": str(payload.get("resolution") or "720x1280"),
            "add_subtitles": bool(payload.get("add_subtitles")),
        },
    }
    result = await asyncio.to_thread(run_compose_episode, compose_envelope, ctx)
    return {**result, "group_id": group_id, "revision": revision, "recomposition_only": True}


def run_narrative_group_video_compose(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return asyncio.run(_execute(envelope, ctx))


register_project_task_runner("narrative_group_video_compose", run_narrative_group_video_compose)


__all__ = [
    "LocalCompositionPlan",
    "SegmentCompositionItem",
    "build_local_composition_plan",
    "build_ffmpeg_filter_complex",
    "compose_local_segments",
    "run_narrative_group_video_compose",
]
