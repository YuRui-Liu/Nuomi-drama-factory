"""Safely backfill Director manifests for pre-Director H3 video artifacts.

The command never rewrites existing MP4 files.  It first reports candidates;
only ``--write`` creates additive manifest sidecars.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    compile_h3_timeline,
    save_h3_director_manifest,
)


_BEAT_VIDEO = re.compile(r"videos/beats/ep(?P<episode>\d{3})/beat_(?P<beat>\d+)\.mp4$")
_GROUP_VIDEO = re.compile(
    r"videos/ep(?P<episode>\d{3})/narrative_groups/"
    r"(?P<group>[A-Za-z0-9][A-Za-z0-9_-]*)_r(?P<revision>\d+)\.mp4$"
)


@dataclass(frozen=True)
class LegacyH3Artifact:
    kind: str
    video_path: Path
    episode: int
    group_id: str
    revision: int
    beat_numbers: tuple[int, ...]
    manifest_path: Path


@dataclass(frozen=True)
class BackfillReport:
    items: tuple[LegacyH3Artifact, ...]
    planned: int
    written: int
    skipped_existing: int


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _legacy_manifest_path(root: Path, episode: int, group_id: str, revision: int) -> Path:
    return root / "videos" / "director" / f"ep{episode:03d}" / group_id / f"r{revision:03d}" / "manifest.json"


def _sidecar_group_beats(root: Path, episode: int) -> dict[str, tuple[int, ...]]:
    path = root / ".narrative_groups" / f"ep{episode:03d}.json"
    try:
        groups = json.loads(path.read_text(encoding="utf-8")).get("groups") or []
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    resolved: dict[str, tuple[int, ...]] = {}
    for group in groups:
        try:
            beats = tuple(int(value) for value in group.get("beat_ids") or ())
        except (TypeError, ValueError):
            continue
        if beats and all(value > 0 for value in beats):
            resolved[str(group.get("id") or "")] = beats
    return resolved


def detect_legacy_h3_artifacts(project_dir: Path | str) -> tuple[LegacyH3Artifact, ...]:
    """Return legacy beat/group movies that lack additive Director manifests."""
    root = Path(project_dir).resolve()
    candidates: list[LegacyH3Artifact] = []
    group_beats_by_episode: dict[int, dict[str, tuple[int, ...]]] = {}
    for video in sorted((root / "videos").glob("**/*.mp4")) if (root / "videos").exists() else ():
        relative = _relative_posix(root, video)
        beat_match = _BEAT_VIDEO.fullmatch(relative)
        if beat_match:
            episode = int(beat_match["episode"])
            beat = int(beat_match["beat"])
            group_id = f"legacy-beat-{beat:02d}"
            candidates.append(LegacyH3Artifact(
                kind="beat", video_path=video, episode=episode, group_id=group_id,
                revision=1, beat_numbers=(beat,),
                manifest_path=_legacy_manifest_path(root, episode, group_id, 1),
            ))
            continue
        group_match = _GROUP_VIDEO.fullmatch(relative)
        if not group_match:
            continue
        episode = int(group_match["episode"])
        group_id = group_match["group"]
        group_beats = group_beats_by_episode.setdefault(episode, _sidecar_group_beats(root, episode))
        beats = group_beats.get(group_id)
        if not beats:
            continue
        revision = int(group_match["revision"])
        candidates.append(LegacyH3Artifact(
            kind="group", video_path=video, episode=episode, group_id=group_id,
            revision=revision, beat_numbers=beats,
            manifest_path=_legacy_manifest_path(root, episode, group_id, revision),
        ))
    return tuple(candidates)


def _manifest_for(candidate: LegacyH3Artifact, *, duration_seconds: float) -> H3DirectorOutputManifest:
    segments = tuple(
        H3DirectorSegment(
            segment_id=f"legacy-ep{candidate.episode:03d}-{candidate.group_id}-{beat:02d}",
            beat_number=beat,
            prompt="遗留 H3 视频兼容条目；仅用于时间线与合成，不重新提交生成任务。",
            duration_seconds=duration_seconds,
            # The Director manifest contract needs a frame reference.  Legacy
            # MP4s did not persist one, so retain a non-resolvable marker that
            # can never be accidentally uploaded as a new generation input.
            first_frame=f"legacy://{candidate.video_path.name}",
            dialogue_source=DialogueSource.EXTERNAL_TTS,
        )
        for beat in candidate.beat_numbers
    )
    timeline = compile_h3_timeline(segments)
    return H3DirectorOutputManifest(
        physical_video=candidate.video_path.as_posix(),
        entries=timeline.entries,
        workflow_id=None,
        provider_task_id=None,
    )


def backfill_legacy_h3_manifests(
    project_dir: Path | str,
    *,
    write: bool = False,
    duration_seconds: float = 5.0,
) -> BackfillReport:
    """Plan or add manifests, without altering original movies or group sidecars."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    items = detect_legacy_h3_artifacts(project_dir)
    written = 0
    skipped_existing = 0
    for item in items:
        if item.manifest_path.exists():
            skipped_existing += 1
            continue
        if write:
            save_h3_director_manifest(
                item.manifest_path,
                _manifest_for(item, duration_seconds=duration_seconds),
            )
            written += 1
    return BackfillReport(
        items=items,
        planned=len(items) - skipped_existing,
        written=written,
        skipped_existing=skipped_existing,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--write", action="store_true", help="write additive manifests; default is dry-run")
    parser.add_argument("--duration-seconds", type=float, default=5.0)
    args = parser.parse_args()
    report = backfill_legacy_h3_manifests(
        args.project_dir, write=args.write, duration_seconds=args.duration_seconds
    )
    mode = "written" if args.write else "dry-run"
    print(
        f"{mode}: planned={report.planned} written={report.written} "
        f"skipped_existing={report.skipped_existing}"
    )
    for item in report.items:
        print(f"{item.kind} ep{item.episode:03d} beats={list(item.beat_numbers)} -> {item.manifest_path}")


if __name__ == "__main__":
    main()
