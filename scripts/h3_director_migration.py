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
    attached: int
    unattached: int


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


def _manifest_for(
    candidate: LegacyH3Artifact,
    *,
    duration_seconds: float,
    ambience_stem_path: Path | None = None,
    dialogue_stem_path: Path | None = None,
) -> H3DirectorOutputManifest:
    external_tts = ambience_stem_path is not None and dialogue_stem_path is not None
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
            # Old movies retain their source audio by default.  External TTS
            # is only safe when an operator supplied both verified stems.
            dialogue_source=(
                DialogueSource.EXTERNAL_TTS if external_tts else DialogueSource.H3_NATIVE
            ),
        )
        for beat in candidate.beat_numbers
    )
    timeline = compile_h3_timeline(segments)
    return H3DirectorOutputManifest(
        physical_video=candidate.video_path.as_posix(),
        entries=timeline.entries,
        workflow_id=None,
        provider_task_id=None,
        dialogue_stem_path=dialogue_stem_path.as_posix() if dialogue_stem_path else None,
        dialogue_stem_status="succeeded" if dialogue_stem_path else "not_requested",
        ambience_stem_path=ambience_stem_path.as_posix() if ambience_stem_path else None,
        ambience_stem_status="succeeded" if ambience_stem_path else "not_requested",
    )


def _attach_group_manifest(candidate: LegacyH3Artifact, manifest_path: Path) -> bool:
    """CAS-attach only the exact stale group revision while its sidecar is locked."""
    if candidate.kind != "group":
        return False
    from novelvideo.narrative_groups.models import GroupStageState
    from novelvideo.narrative_groups.service import _sidecar_guard, load_groups, record_stage_result
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest

    root = candidate.video_path.parents[3]
    with _sidecar_guard(root, candidate.episode):
        group = next(
            (item for item in load_groups(root, candidate.episode) if item.id == candidate.group_id),
            None,
        )
        if group is None:
            return False
        current = group.stages.get("video", GroupStageState())
        # A higher revision, or an already materialized same-revision result,
        # always wins over a discovered legacy movie.
        if current.revision != candidate.revision or (
            current.status == "completed"
            and (current.video_asset or current.manifest_asset)
        ):
            return False
        try:
            manifest = load_h3_director_manifest(manifest_path)
        except (OSError, ValueError):
            return False
        if not _manifest_has_verified_external_stems(manifest):
            return False
        updated = record_stage_result(
            root,
            candidate.episode,
            candidate.group_id,
            "video",
            expected_revision=candidate.revision,
            status="completed",
            video_asset=candidate.video_path.as_posix(),
            manifest_asset=manifest_path.as_posix(),
            dialogue_stem_path=str(manifest.dialogue_stem_path or ""),
            ambience_stem_path=str(manifest.ambience_stem_path or ""),
            dialogue_stem_status=manifest.dialogue_stem_status,
            ambience_stem_status=manifest.ambience_stem_status,
        )
        stage = updated.stages.get("video", GroupStageState())
        return (
            stage.revision == candidate.revision
            and stage.status == "completed"
            and stage.video_asset == candidate.video_path.as_posix()
            and stage.manifest_asset == manifest_path.as_posix()
        )


def _manifest_has_verified_external_stems(manifest: H3DirectorOutputManifest) -> bool:
    """Reject unsafe legacy manifests before they reach composition.

    ``external_tts`` must never be attached without the two successful,
    materialized stems.  Returning false keeps the sidecar unchanged, which
    makes the migration additive and prevents a resolver from mixing stale
    H3 speech with a missing external dialogue track.
    """
    if not any(
        entry.segment.dialogue_source is DialogueSource.EXTERNAL_TTS
        for entry in manifest.entries
    ):
        return True
    if (
        manifest.ambience_stem_status != "succeeded"
        or manifest.dialogue_stem_status != "succeeded"
        or not manifest.ambience_stem_path
        or not manifest.dialogue_stem_path
    ):
        return False
    return (
        Path(manifest.ambience_stem_path).is_file()
        and Path(manifest.dialogue_stem_path).is_file()
    )


def backfill_legacy_h3_manifests(
    project_dir: Path | str,
    *,
    write: bool = False,
    duration_seconds: float = 5.0,
    ambience_stem_path: Path | str | None = None,
    dialogue_stem_path: Path | str | None = None,
) -> BackfillReport:
    """Plan or add manifests, without altering original movies or group sidecars."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if bool(ambience_stem_path) != bool(dialogue_stem_path):
        raise ValueError("external_tts migration requires both ambience and dialogue stems")
    ambience_stem = Path(ambience_stem_path).resolve() if ambience_stem_path else None
    dialogue_stem = Path(dialogue_stem_path).resolve() if dialogue_stem_path else None
    for label, stem in (("ambience", ambience_stem), ("dialogue", dialogue_stem)):
        if stem is not None and not stem.is_file():
            raise FileNotFoundError(f"{label} stem is unavailable: {stem}")
    items = detect_legacy_h3_artifacts(project_dir)
    # A pair of stems belongs to one physical H3 result, never to an episode
    # batch.  Do this before writing any manifest so an ambiguous invocation
    # has no partial side effects.  Dry-runs stay useful as candidate reports.
    if write and ambience_stem is not None:
        writable = tuple(item for item in items if not item.manifest_path.exists())
        if len(writable) != 1:
            raise ValueError(
                "external_tts stem binding requires exactly one writable legacy artifact; "
                f"found {len(writable)}"
            )
    written = 0
    skipped_existing = 0
    attached = 0
    unattached = 0
    for item in items:
        exists = item.manifest_path.exists()
        if exists:
            skipped_existing += 1
        elif write:
            save_h3_director_manifest(
                item.manifest_path,
                _manifest_for(
                    item,
                    duration_seconds=duration_seconds,
                    ambience_stem_path=ambience_stem,
                    dialogue_stem_path=dialogue_stem,
                ),
            )
            written += 1
        if write:
            if _attach_group_manifest(item, item.manifest_path):
                attached += 1
            else:
                unattached += 1
    return BackfillReport(
        items=items,
        planned=len(items) - skipped_existing,
        written=written,
        skipped_existing=skipped_existing,
        attached=attached,
        unattached=unattached,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--write", action="store_true", help="write additive manifests; default is dry-run")
    parser.add_argument("--duration-seconds", type=float, default=5.0)
    parser.add_argument("--ambience-stem", type=Path)
    parser.add_argument("--dialogue-stem", type=Path)
    args = parser.parse_args()
    report = backfill_legacy_h3_manifests(
        args.project_dir, write=args.write, duration_seconds=args.duration_seconds,
        ambience_stem_path=args.ambience_stem, dialogue_stem_path=args.dialogue_stem,
    )
    mode = "written" if args.write else "dry-run"
    print(
        f"{mode}: planned={report.planned} written={report.written} "
        f"skipped_existing={report.skipped_existing} attached={report.attached} "
        f"unattached={report.unattached}"
    )
    for item in report.items:
        print(f"{item.kind} ep{item.episode:03d} beats={list(item.beat_numbers)} -> {item.manifest_path}")


if __name__ == "__main__":
    main()
