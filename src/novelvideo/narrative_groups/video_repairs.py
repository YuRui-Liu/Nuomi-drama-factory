"""Immutable reuse snapshots for narrowly scoped narrative-group video repairs."""

from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    source_shot_ids_for,
)
from novelvideo.narrative_groups.execution_contract import freeze_execution_contract


_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class VideoRepairPriorEntry(BaseModel):
    """A pinned completed source segment and whether the repair may reuse it."""

    model_config = _MODEL_CONFIG

    segment_id: str = Field(min_length=1)
    source_shot_ids: tuple[str, ...] = Field(min_length=1)
    physical_video: str = Field(min_length=1)
    physical_video_sha256: str = Field(min_length=64, max_length=64)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    # Canonical JSON preserves the rest of the verified H3 entry without placing
    # mutable dictionaries inside this frozen payload.
    entry_json: str = Field(min_length=2)
    reuse: bool

    @field_validator("physical_video_sha256")
    @classmethod
    def validate_clip_hash(cls, value: str) -> str:
        _require_sha256(value, "physical video")
        return value


class VideoRepairSnapshot(BaseModel):
    """JSON-serializable, immutable evidence for reusing prior group clips."""

    model_config = _MODEL_CONFIG

    schema_version: int = Field(default=1, ge=1, le=1)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(min_length=64, max_length=64)
    episode: int = Field(gt=0)
    group_id: str = Field(min_length=1)
    director_revision_id: str = Field(min_length=1)
    expected_source_shot_ids: tuple[str, ...] = Field(min_length=2)
    repair_shot_ids: tuple[str, ...] = Field(min_length=1)
    prior_entries: tuple[VideoRepairPriorEntry, ...] = Field(min_length=2)
    snapshot_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("source_manifest_sha256", "snapshot_sha256")
    @classmethod
    def validate_manifest_hashes(cls, value: str) -> str:
        _require_sha256(value, "snapshot")
        return value

    @model_validator(mode="after")
    def validate_snapshot_digest(self) -> "VideoRepairSnapshot":
        expected = _snapshot_digest(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("video repair snapshot digest does not match its payload")
        return self


def freeze_video_repair_snapshot(
    source_manifest_path: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    *,
    episode: int,
    group_id: str,
    director_revision_id: str,
    expected_source_shot_ids: Sequence[str],
    repair_shot_ids: Sequence[str],
) -> VideoRepairSnapshot:
    """Validate and hash a completed group manifest before any repair is queued.

    Every prior clip is hashed, including the clip(s) being replaced. That
    makes the snapshot a complete statement of which old group output was
    reviewed at enqueue time, while ``reuse`` identifies the immutable subset.
    Paths in the returned payload are project-relative and may not traverse a
    symlink.
    """

    root = _project_root(project_root)
    manifest_path, relative_manifest_path = _safe_project_file(
        source_manifest_path, root, label="source manifest"
    )
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = H3DirectorOutputManifest.model_validate_json(manifest_bytes)
    except Exception as exc:
        raise ValueError(f"source manifest is unreadable or invalid: {exc}") from exc

    expected_ids = _normalize_ids(expected_source_shot_ids, "expected source shot IDs")
    requested_repair_ids = _normalize_ids(repair_shot_ids, "repair shot IDs")
    _validate_repair_selection(expected_ids, requested_repair_ids)
    if manifest.status != "completed":
        raise ValueError("source manifest must be completed before a video repair")
    _validate_provenance(
        manifest.execution_contract,
        episode=episode,
        group_id=group_id,
        director_revision_id=director_revision_id,
        expected_source_shot_ids=expected_ids,
    )

    entries = tuple(manifest.entries)
    seen_segment_ids: set[str] = set()
    flattened_ids: list[str] = []
    entry_shot_ids: list[tuple[str, ...]] = []
    for entry in entries:
        if entry.status != "completed":
            raise ValueError("every source manifest entry must be completed")
        if entry.segment.segment_id in seen_segment_ids:
            raise ValueError("source manifest contains duplicate segment IDs")
        seen_segment_ids.add(entry.segment.segment_id)
        shot_ids = source_shot_ids_for(entry.segment)
        if not shot_ids or any(not shot_id.strip() for shot_id in shot_ids):
            raise ValueError("source manifest entry has invalid source shot coverage")
        duplicated = set(flattened_ids).intersection(shot_ids)
        duplicated.update(
            shot_id for shot_id in shot_ids if shot_ids.count(shot_id) > 1
        )
        if duplicated:
            raise ValueError("source manifest entries overlap in source shot coverage")
        flattened_ids.extend(shot_ids)
        entry_shot_ids.append(shot_ids)

    if tuple(flattened_ids) != expected_ids:
        raise ValueError(
            "source manifest shot coverage must exactly match expected source shot IDs"
        )

    repair_set = set(requested_repair_ids)
    prior_entries: list[VideoRepairPriorEntry] = []
    for entry, shot_ids in zip(entries, entry_shot_ids, strict=True):
        overlapping_repair_ids = repair_set.intersection(shot_ids)
        if overlapping_repair_ids and len(overlapping_repair_ids) != len(shot_ids):
            raise ValueError(
                "repair shot IDs must align to a complete prior entry boundary"
            )
        reuse = not overlapping_repair_ids
        raw_video_path = entry.physical_video
        if not raw_video_path:
            raise ValueError(
                f"completed source entry {entry.segment.segment_id} has no video"
            )
        clip_path, relative_clip_path = _safe_project_file(
            raw_video_path, root, label="source clip"
        )
        clip_sha256 = _hash_file(clip_path, label="source clip")
        duration_seconds = entry.actual_duration_seconds
        if (
            duration_seconds is None
            or not math.isfinite(duration_seconds)
            or duration_seconds <= 0
        ):
            raise ValueError("source entry duration must be positive and finite")

        normalized_entry = entry.model_copy(
            update={"physical_video": relative_clip_path}
        )
        entry_json = _canonical_json(normalized_entry.model_dump(mode="json"))
        prior_entries.append(
            VideoRepairPriorEntry(
                segment_id=entry.segment.segment_id,
                source_shot_ids=shot_ids,
                physical_video=relative_clip_path,
                physical_video_sha256=clip_sha256,
                duration_seconds=duration_seconds,
                entry_json=entry_json,
                reuse=reuse,
            )
        )

    snapshot_data: dict[str, Any] = {
        "schema_version": 1,
        "source_manifest_path": relative_manifest_path,
        "source_manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "episode": episode,
        "group_id": group_id,
        "director_revision_id": director_revision_id,
        "expected_source_shot_ids": expected_ids,
        "repair_shot_ids": requested_repair_ids,
        "prior_entries": tuple(prior_entries),
    }
    digest_body = {
        **snapshot_data,
        "prior_entries": [entry.model_dump(mode="json") for entry in prior_entries],
        "expected_source_shot_ids": list(expected_ids),
        "repair_shot_ids": list(requested_repair_ids),
    }
    return VideoRepairSnapshot(
        **snapshot_data,
        snapshot_sha256=_snapshot_digest(digest_body),
    )


def validate_video_repair_snapshot(
    snapshot_or_json_mapping: VideoRepairSnapshot | Mapping[str, Any],
    *,
    project_root: str | os.PathLike[str],
    episode: int,
    group_id: str,
    director_revision_id: str,
    expected_source_shot_ids: Sequence[str],
    repair_shot_ids: Sequence[str],
) -> VideoRepairSnapshot:
    """Re-hash the pinned manifest and all clips immediately before reuse.

    The returned model is freshly derived from disk. A changed clip, manifest,
    provenance value, requested repair set, or payload digest fails closed.
    """

    try:
        snapshot = (
            snapshot_or_json_mapping
            if isinstance(snapshot_or_json_mapping, VideoRepairSnapshot)
            else VideoRepairSnapshot.model_validate(snapshot_or_json_mapping)
        )
    except Exception as exc:
        raise ValueError(f"video repair snapshot is invalid: {exc}") from exc

    expected_ids = _normalize_ids(expected_source_shot_ids, "expected source shot IDs")
    requested_repair_ids = _normalize_ids(repair_shot_ids, "repair shot IDs")
    requested = {
        "episode": episode,
        "group_id": group_id,
        "director_revision_id": director_revision_id,
        "expected_source_shot_ids": expected_ids,
        "repair_shot_ids": requested_repair_ids,
    }
    for field, value in requested.items():
        if getattr(snapshot, field) != value:
            raise ValueError(
                f"video repair snapshot {field} differs from queued repair"
            )

    current = freeze_video_repair_snapshot(
        snapshot.source_manifest_path,
        project_root,
        episode=episode,
        group_id=group_id,
        director_revision_id=director_revision_id,
        expected_source_shot_ids=expected_ids,
        repair_shot_ids=requested_repair_ids,
    )
    if current != snapshot:
        raise ValueError(
            "video repair snapshot no longer matches source manifest or clips"
        )
    return current


def _project_root(project_root: str | os.PathLike[str]) -> Path:
    supplied = Path(project_root)
    if supplied.is_symlink():
        raise ValueError("project root may not be a symlink")
    try:
        root = supplied.resolve(strict=True)
    except OSError as exc:
        raise ValueError("project root does not exist") from exc
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    return root


def _safe_project_file(
    raw_path: str | os.PathLike[str], root: Path, *, label: str
) -> tuple[Path, str]:
    try:
        supplied = Path(raw_path)
    except TypeError as exc:
        raise ValueError(f"{label} path is invalid") from exc
    if not str(raw_path).strip() or ".." in supplied.parts:
        raise ValueError(f"{label} path must stay inside the project root")
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path is outside the project root") from exc
    if not relative.parts:
        raise ValueError(f"{label} path must name a file")

    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            is_symlink = cursor.is_symlink()
        except OSError as exc:
            raise ValueError(f"{label} path cannot be inspected") from exc
        if is_symlink:
            raise ValueError(f"{label} path may not traverse a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} file does not exist") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path resolves outside the project root") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} path must name a regular file")
    return resolved, relative.as_posix()


def _hash_file(path: Path, *, label: str) -> str:
    try:
        if path.stat().st_size <= 0:
            raise ValueError(f"{label} file is empty")
        digest = sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ValueError(f"{label} file cannot be read") from exc


def _validate_provenance(
    execution_contract: Any,
    *,
    episode: int,
    group_id: str,
    director_revision_id: str,
    expected_source_shot_ids: tuple[str, ...],
) -> None:
    if not isinstance(execution_contract, Mapping):
        raise ValueError("source manifest execution provenance is missing")
    effective = execution_contract.get("effective")
    if not isinstance(effective, Mapping):
        raise ValueError("source manifest execution provenance is malformed")
    try:
        if dict(execution_contract) != freeze_execution_contract(effective):
            raise ValueError("source manifest execution provenance digest is invalid")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "source manifest execution provenance digest is invalid"
        ) from exc

    if effective.get("episode") != episode or effective.get("group_id") != group_id:
        raise ValueError("source manifest episode or group provenance does not match")
    storyboard = effective.get("storyboard_binding")
    keyframe = effective.get("video_keyframe_binding")
    if not isinstance(storyboard, Mapping) or not isinstance(keyframe, Mapping):
        raise ValueError("source manifest storyboard or director provenance is missing")
    for binding_name, binding in (("storyboard", storyboard), ("director", keyframe)):
        if (
            binding.get("episode") != episode
            or binding.get("group_id") != group_id
            or tuple(binding.get("shot_ids", ())) != expected_source_shot_ids
        ):
            raise ValueError(
                f"source manifest {binding_name} provenance does not match"
            )
    if keyframe.get("director_revision_id") != director_revision_id:
        raise ValueError("source manifest director revision provenance does not match")
    direct_revision_id = effective.get("director_revision_id")
    if direct_revision_id is not None and direct_revision_id != director_revision_id:
        raise ValueError("source manifest director revision provenance does not match")
    storyboard_project = storyboard.get("project_id")
    keyframe_project = keyframe.get("project_id")
    if (
        storyboard_project
        and keyframe_project
        and storyboard_project != keyframe_project
    ):
        raise ValueError("source manifest project provenance does not match")


def _normalize_ids(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of IDs")
    try:
        result = tuple(
            value.strip() if isinstance(value, str) else "" for value in values
        )
    except TypeError as exc:
        raise ValueError(f"{label} must be a sequence of IDs") from exc
    if not result:
        raise ValueError(f"{label} must contain at least one ID")
    if any(not value for value in result):
        raise ValueError(f"{label} must contain nonblank IDs")
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contain duplicate IDs")
    return result


def _validate_repair_selection(
    expected_source_shot_ids: tuple[str, ...], repair_shot_ids: tuple[str, ...]
) -> None:
    if len(expected_source_shot_ids) < 2:
        raise ValueError("video repair requires at least two source shots")
    unknown = set(repair_shot_ids).difference(expected_source_shot_ids)
    if unknown:
        raise ValueError(f"repair shot IDs contain unknown IDs: {sorted(unknown)}")
    if len(repair_shot_ids) >= len(expected_source_shot_ids):
        raise ValueError("repair shot IDs must be a proper subset of source shots")


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value.lower()
    ):
        raise ValueError(f"{label} SHA-256 must be 64 hexadecimal characters")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _snapshot_digest(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "VideoRepairPriorEntry",
    "VideoRepairSnapshot",
    "freeze_video_repair_snapshot",
    "validate_video_repair_snapshot",
]
