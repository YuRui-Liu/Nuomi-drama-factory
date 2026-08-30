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
    plan: LocalCompositionPlan, *, durations: tuple[float, ...]
) -> str:
    """Compile reviewed transition rules into executable FFmpeg filters."""
    if len(durations) != len(plan.paths):
        raise ValueError("one duration is required for each composition path")
    if len(plan.transitions) != max(0, len(plan.paths) - 1):
        raise ValueError("one transition is required between adjacent paths")
    if len(plan.paths) == 1:
        return "[0:v]null[outv];[0:a]anull[outa]"

    filters: list[str] = []
    video_label = "0:v"
    audio_label = "0:a"
    visual_overlap = 0.0
    for index, rule in enumerate(plan.transitions, start=1):
        next_video = f"v{index}"
        next_audio = f"a{index}"
        if rule.kind == "dissolve":
            duration = rule.frames / 24
            offset = sum(durations[:index]) - visual_overlap - duration
            filters.append(
                f"[{video_label}][{index}:v]xfade=transition=fade:"
                f"duration={duration:.6f}:offset={offset:.6f}[{next_video}]"
            )
            visual_overlap += duration
        else:
            filters.append(
                f"[{video_label}][{index}:v]concat=n=2:v=1:a=0[{next_video}]"
            )

        audio_overlap = (
            rule.audio_ms / 1000
            if rule.audio != "none"
            else (rule.frames / 24 if rule.kind == "dissolve" else 0.0)
        )
        if audio_overlap:
            filters.append(
                f"[{audio_label}][{index}:a]acrossfade=d={audio_overlap:.6f}:"
                f"c1=tri:c2=tri[{next_audio}]"
            )
        else:
            filters.append(
                f"[{audio_label}][{index}:a]concat=n=2:v=0:a=1[{next_audio}]"
            )
        video_label, audio_label = next_video, next_audio
    filters.extend((f"[{video_label}]null[outv]", f"[{audio_label}]apad[outa]"))
    return ";".join(filters)


def compose_local_segments(plan: LocalCompositionPlan, output_path: Path) -> Path:
    """Compose provider segment files in the exact reviewed order."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = output_path.with_suffix(".composition.json")
    manifest.write_text(
        json.dumps(
            {
                "paths": list(plan.paths),
                "transitions": [item.model_dump(mode="json") for item in plan.transitions],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    command = ["ffmpeg", "-y"]
    for path in plan.paths:
        command.extend(["-i", path])
    durations = tuple(_probe_duration(path) for path in plan.paths)
    command.extend(
        [
            "-filter_complex",
            build_ffmpeg_filter_complex(plan, durations=durations),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
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
    result = run_compose_episode(compose_envelope, ctx)
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
