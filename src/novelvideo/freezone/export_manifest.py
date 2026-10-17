"""Immutable input contracts for Freezone video timeline exports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class ExportManifestError(ValueError):
    """An export snapshot references invalid, unadopted, or changed media."""


VIDEO_TIMELINE_TOLERANCE_SECONDS = 1e-6


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_file(project_dir: Path, raw_path: str, *, label: str) -> Path:
    root = project_dir.resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ExportManifestError(f"{label} resolves outside the project") from exc
    if not resolved.is_file():
        raise ExportManifestError(f"{label} is missing")
    return resolved


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _video_output_end_seconds(tracks: list[dict[str, Any]]) -> float:
    """Return the longest video-track end using the renderer's playback-rate math."""
    end = 0.0
    for track in tracks:
        if str(track.get("kind") or "") != "video":
            continue
        raw_items = track.get("items") or []
        if not isinstance(raw_items, list):
            raise ExportManifestError("video track contains invalid items")
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ExportManifestError("video track contains an invalid item")
            try:
                timeline_start = float(item.get("timeline_start", 0.0) or 0.0)
                source_start = float(item.get("source_start", 0.0) or 0.0)
                source_end = float(item.get("source_end", 0.0) or 0.0)
                speed = float(item.get("speed", 1.0) if item.get("speed") is not None else 1.0)
            except (TypeError, ValueError) as exc:
                raise ExportManifestError("video clip has invalid timing") from exc
            if (
                not all(math.isfinite(value) for value in (timeline_start, source_start, source_end, speed))
                or timeline_start < 0
                or source_start < 0
                or source_end <= source_start
                or not 0.25 <= speed <= 4.0
            ):
                raise ExportManifestError("video clip has invalid timing or playback speed")
            end = max(end, timeline_start + (source_end - source_start) / speed)
    return end


def _freeze_subtitle_tracks(
    subtitle_tracks: list[dict[str, Any]],
    tracks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate editable captions and retain the audio-source/timing boundary."""
    video_end = _video_output_end_seconds(tracks)
    audio_items = {
        str(item.get("item_id") or ""): item
        for track in tracks
        if str(track.get("kind") or "") == "audio"
        for item in (track.get("items") or [])
        if str(item.get("item_id") or "")
    }
    frozen_tracks: list[dict[str, Any]] = []
    seen_track_ids: set[str] = set()
    seen_cue_ids: set[str] = set()
    for track in subtitle_tracks:
        track_id = str(track.get("track_id") or "")
        if not track_id or track_id in seen_track_ids:
            raise ExportManifestError("subtitle tracks require unique, non-empty track ids")
        seen_track_ids.add(track_id)
        raw_cues = track.get("cues") or []
        if not isinstance(raw_cues, list):
            raise ExportManifestError("subtitle track contains invalid cues")
        frozen_cues: list[dict[str, Any]] = []
        for cue in raw_cues:
            if not isinstance(cue, Mapping):
                raise ExportManifestError("subtitle track contains an invalid cue")
            cue_id = str(cue.get("cue_id") or "")
            text = str(cue.get("text") or "").strip()
            start = float(cue.get("timeline_start", 0.0) or 0.0)
            end = float(cue.get("timeline_end", 0.0) or 0.0)
            if not cue_id or cue_id in seen_cue_ids:
                raise ExportManifestError("subtitle cues require unique, non-empty ids")
            seen_cue_ids.add(cue_id)
            if not text:
                raise ExportManifestError(f"subtitle cue {cue_id} has no text")
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ExportManifestError(f"subtitle cue {cue_id} has an invalid time range")
            if end > video_end + VIDEO_TIMELINE_TOLERANCE_SECONDS:
                raise ExportManifestError(
                    f"subtitle cue {cue_id} extends beyond video output ({video_end:.6f}s)"
                )

            source_clip_id = str(cue.get("source_clip_id") or "") or None
            source_clip_url = str(cue.get("source_clip_url") or "") or None
            source_take_id = str(cue.get("source_take_id") or "") or None
            source_dialogue_id = str(cue.get("source_dialogue_id") or "") or None
            source_group_id = str(cue.get("source_group_id") or "") or None
            source_timing = _mapping(cue.get("source_timing"))
            timing = str(cue.get("timing") or "manual")
            timing_confirmed = bool(cue.get("timing_confirmed", False))
            if timing not in {"manual", "estimated_audio_clip"}:
                raise ExportManifestError(f"subtitle cue {cue_id} has an invalid timing mode")
            if not timing_confirmed:
                raise ExportManifestError(f"subtitle cue {cue_id} needs timing review before export")
            source_status = "manual"
            if source_clip_id is not None:
                source_item = audio_items.get(source_clip_id)
                if source_item is None:
                    raise ExportManifestError(
                        f"subtitle cue {cue_id} refers to missing audio clip {source_clip_id}"
                    )
                if source_timing is None:
                    raise ExportManifestError(
                        f"subtitle cue {cue_id} needs a saved audio timing snapshot"
                    )
                expected_start = float(source_item.get("timeline_start", 0.0) or 0.0)
                source_start = float(source_item.get("source_start", 0.0) or 0.0)
                source_end = float(source_item.get("source_end", 0.0) or 0.0)
                speed = float(source_item.get("speed", 1.0) or 1.0)
                expected_end = expected_start + (source_end - source_start) / speed
                saved_start = float(source_timing.get("timeline_start", 0.0) or 0.0)
                saved_end = float(source_timing.get("timeline_end", 0.0) or 0.0)
                saved_source_start = float(source_timing.get("source_start", 0.0) or 0.0)
                saved_source_end = float(source_timing.get("source_end", 0.0) or 0.0)
                saved_speed = float(source_timing.get("speed", 1.0) or 1.0)
                if (
                    not math.isfinite(saved_start)
                    or not math.isfinite(saved_end)
                    or saved_end <= saved_start
                    or abs(saved_start - expected_start) > 0.001
                    or abs(saved_end - expected_end) > 0.001
                    or abs(saved_source_start - source_start) > 0.001
                    or abs(saved_source_end - source_end) > 0.001
                    or abs(saved_speed - speed) > 0.000001
                    or source_clip_url != str(source_item.get("source_url") or "")
                ):
                    raise ExportManifestError(
                        f"subtitle cue {cue_id} needs timing review after its audio source changed"
                    )
                source_status = "linked_audio"
                audio_dubbing = _mapping(source_item.get("dubbing_source"))
                has_dubbing_source = any(
                    value is not None
                    for value in (source_take_id, source_dialogue_id, source_group_id)
                )
                if has_dubbing_source:
                    if (
                        source_take_id is None
                        or source_dialogue_id is None
                        or source_group_id is None
                        or audio_dubbing is None
                        or str(audio_dubbing.get("take_id") or audio_dubbing.get("takeId") or "")
                        != source_take_id
                        or str(audio_dubbing.get("dialogue_id") or audio_dubbing.get("dialogueId") or "")
                        != source_dialogue_id
                        or str(audio_dubbing.get("group_id") or audio_dubbing.get("groupId") or "")
                        != source_group_id
                    ):
                        raise ExportManifestError(
                            f"subtitle cue {cue_id} does not match its adopted dialogue source"
                        )
                    source_status = "linked_adopted_dubbing"
            elif any(
                value is not None
                for value in (source_clip_url, source_take_id, source_dialogue_id, source_group_id)
            ):
                raise ExportManifestError(f"subtitle cue {cue_id} has audio source metadata without an audio clip")

            frozen_cue: dict[str, Any] = {
                "cue_id": cue_id,
                "text": text,
                "timeline_start": start,
                "timeline_end": end,
                "source_clip_id": source_clip_id,
                "source_clip_url": source_clip_url,
                "source_take_id": source_take_id,
                "source_dialogue_id": source_dialogue_id,
                "source_group_id": source_group_id,
                "source_status": source_status,
                "timing": timing,
                "timing_confirmed": timing_confirmed,
            }
            if source_timing is not None:
                frozen_cue["source_timing"] = {
                    "timeline_start": float(source_timing.get("timeline_start", 0.0) or 0.0),
                    "timeline_end": float(source_timing.get("timeline_end", 0.0) or 0.0),
                    "source_start": float(source_timing.get("source_start", 0.0) or 0.0),
                    "source_end": float(source_timing.get("source_end", 0.0) or 0.0),
                    "speed": float(source_timing.get("speed", 1.0) or 1.0),
                }
            frozen_cues.append(frozen_cue)
        frozen_tracks.append({"track_id": track_id, "cues": frozen_cues})
    return {
        "status": "ready" if any(track["cues"] for track in frozen_tracks) else "none",
        "tracks": frozen_tracks,
    }


def _validate_dubbing_source(
    *,
    project_dir: Path,
    source_path: Path,
    source_sha256: str,
    source: Mapping[str, Any],
    dubbing_store: Any,
) -> dict[str, Any]:
    take_id = str(source.get("take_id") or source.get("takeId") or "").strip()
    dialogue_id = str(source.get("dialogue_id") or source.get("dialogueId") or "").strip()
    group_id = str(source.get("group_id") or source.get("groupId") or "").strip()
    if not take_id or not dialogue_id or not group_id:
        raise ExportManifestError("dubbing source requires take, dialogue, and group identity")
    if dubbing_store is None:
        raise ExportManifestError("cannot verify adopted dubbing source without DubbingStore")

    take = dubbing_store.get_take(take_id)
    if take is None:
        raise ExportManifestError(f"dubbing take is unknown: {take_id}")
    preparation = dubbing_store.get_preparation(take.preparation_id)
    if preparation is None:
        raise ExportManifestError(f"dubbing take preparation is unknown: {take_id}")
    if take.dialogue_id != dialogue_id or preparation.group_id != group_id:
        raise ExportManifestError("dubbing source does not match the requested group/dialogue/take")
    if preparation.episode <= 0:
        raise ExportManifestError("dubbing source has no authoritative episode")
    supplied_episode = source.get("episode")
    if supplied_episode is not None and int(supplied_episode) != preparation.episode:
        raise ExportManifestError("dubbing source episode does not match its authoritative take")
    if take.status != "succeeded" or take.technical_status != "passed":
        raise ExportManifestError("dubbing source take is not a technically passed successful take")

    state = dubbing_store.get_group_state(preparation.episode, preparation.group_id)
    adoption = (state.get("adoptions") or {}).get(dialogue_id)
    if not isinstance(adoption, Mapping) or str(adoption.get("take_id") or "") != take_id:
        raise ExportManifestError(f"dubbing take has not been adopted: {take_id}")

    take_path = _project_file(project_dir, take.audio_path, label="adopted dubbing audio")
    if take_path != source_path:
        raise ExportManifestError("dubbing source media path does not match the adopted take")
    digest = source_sha256
    if digest != str(take.audio_sha256 or "").lower():
        raise ExportManifestError("adopted dubbing source hash does not match its recorded take")
    return {
        "take_id": take_id,
        "episode": int(preparation.episode),
        "group_id": group_id,
        "dialogue_id": dialogue_id,
        "audio_sha256": digest,
        "adoption_revision": int(adoption.get("adopted_revision") or 0),
    }


def build_export_manifest(
    *,
    project_id: str,
    project_dir: Path,
    resolution: str,
    resolution_origin: str = "default",
    fps: int,
    fps_origin: str,
    background_color: str = "#000000",
    keep_original_audio: bool = True,
    tracks: list[dict[str, Any]],
    subtitle_tracks: list[dict[str, Any]] | None = None,
    draft_revision: int = 0,
    dubbing_store: Any,
) -> dict[str, Any]:
    """Build the server-authoritative, content-addressed export snapshot.

    Call from a worker thread: source media hashing is deliberately synchronous
    so async API and task handlers can keep it off their event loops.
    """
    root = project_dir.resolve()
    frozen_tracks: list[dict[str, Any]] = []
    has_legacy_sources = False
    seen_tracks: set[str] = set()
    seen_items: set[str] = set()
    for track in tracks:
        track_id = str(track.get("track_id") or "")
        if not track_id or track_id in seen_tracks:
            raise ExportManifestError("compose tracks require unique, non-empty track ids")
        seen_tracks.add(track_id)
        track_data = {
            "track_id": track_id,
            "kind": str(track.get("kind") or ""),
            "items": [],
        }
        for item in track.get("items") or []:
            speed = float(item.get("speed", 1.0) if item.get("speed") is not None else 1.0)
            if not math.isfinite(speed) or not 0.25 <= speed <= 4.0:
                raise ExportManifestError("compose item speed must be between 0.25x and 4x")
            item_id = str(item.get("item_id") or "")
            if not item_id or item_id in seen_items:
                raise ExportManifestError("compose items require unique, non-empty item ids")
            seen_items.add(item_id)
            source_path = _project_file(
                root,
                str(item.get("source_path") or ""),
                label=f"compose source {item.get('item_id') or ''}",
            )
            digest = sha256_file(source_path)
            source_identity = _mapping(item.get("dubbing_source"))
            source_node_id = str(item.get("source_node_id") or "") or None
            if source_identity is not None:
                frozen_dubbing_source = _validate_dubbing_source(
                    project_dir=root,
                    source_path=source_path,
                    source_sha256=digest,
                    source=source_identity,
                    dubbing_store=dubbing_store,
                )
                source_status = "adopted"
            else:
                frozen_dubbing_source = None
                # A client-supplied node ID is a reference hint, not authority.
                source_status = "legacy_unverified"
                has_legacy_sources = True

            frozen_item = {
                "item_id": item_id,
                "source_url": str(item.get("source_url") or ""),
                "source_node_id": source_node_id,
                "source_path": source_path.relative_to(root).as_posix(),
                "timeline_start": float(item.get("timeline_start", 0.0) or 0.0),
                "source_start": float(item.get("source_start", 0.0) or 0.0),
                "source_end": float(item.get("source_end", 0.0) or 0.0),
                "volume": float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0),
                "muted": bool(item.get("muted", False)),
                "speed": float(item.get("speed", 1.0) if item.get("speed") is not None else 1.0),
                "source_media_sha256": digest,
                "source_status": source_status,
            }
            if frozen_dubbing_source is not None:
                frozen_item["dubbing_source"] = frozen_dubbing_source
            track_data["items"].append(frozen_item)
        frozen_tracks.append(track_data)

    frozen_subtitles = _freeze_subtitle_tracks(subtitle_tracks or [], tracks)
    core = {
        "schema_version": 2,
        "project_id": str(project_id),
        "draft_revision": max(0, int(draft_revision)),
        "settings": {
            "resolution": str(resolution),
            "resolution_origin": str(resolution_origin),
            "fps": int(fps),
            "fps_origin": str(fps_origin),
            "background_color": str(background_color),
            "keep_original_audio": bool(keep_original_audio),
        },
        "verification": "legacy_unverified" if has_legacy_sources else "verified",
        "subtitles": frozen_subtitles,
        "tracks": frozen_tracks,
    }
    snapshot_hash = hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()
    return {
        **core,
        "snapshot_hash": snapshot_hash,
        "timeline_revision": snapshot_hash,
    }


def verify_export_source_integrity(
    project_dir: Path,
    tracks: list[dict[str, Any]],
    manifest: Mapping[str, Any],
    *,
    resolution: str | None = None,
    fps: int | None = None,
    background_color: str | None = None,
    keep_original_audio: bool | None = None,
    project_id: str | None = None,
    subtitle_tracks: list[dict[str, Any]] | None = None,
    draft_revision: int | None = None,
) -> None:
    """Reject queued exports whose media or execution settings changed after freeze."""
    expected_tracks = manifest.get("tracks")
    if not isinstance(expected_tracks, list):
        raise ExportManifestError("export manifest has no frozen tracks")
    core = {
        key: manifest.get(key)
        for key in (
            "schema_version",
            "project_id",
            "draft_revision",
            "settings",
            "verification",
            "subtitles",
            "tracks",
        )
    }
    expected_hash = hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()
    if (
        str(manifest.get("snapshot_hash") or "") != expected_hash
        or str(manifest.get("timeline_revision") or "") != expected_hash
    ):
        raise ExportManifestError("export manifest snapshot hash is invalid")
    if str(manifest.get("project_id") or "") == "":
        raise ExportManifestError("export manifest has no project identity")
    if project_id is not None and str(manifest.get("project_id") or "") != str(project_id):
        raise ExportManifestError("execution project does not match the frozen export manifest")
    if draft_revision is not None and int(manifest.get("draft_revision") or 0) != int(draft_revision):
        raise ExportManifestError("execution timeline revision does not match the frozen export manifest")
    settings = manifest.get("settings")
    if not isinstance(settings, Mapping):
        raise ExportManifestError("export manifest has no frozen settings")
    if resolution is not None and str(settings.get("resolution") or "") != str(resolution):
        raise ExportManifestError("execution resolution does not match the frozen export manifest")
    if fps is not None and int(settings.get("fps") or 0) != int(fps):
        raise ExportManifestError("execution FPS does not match the frozen export manifest")
    if background_color is not None and str(settings.get("background_color") or "") != str(background_color):
        raise ExportManifestError("execution background color does not match the frozen export manifest")
    if keep_original_audio is not None and bool(settings.get("keep_original_audio")) != bool(keep_original_audio):
        raise ExportManifestError("execution original-audio setting does not match the frozen export manifest")

    frozen_track_ids: set[str] = set()
    frozen_item_ids: list[list[str]] = []
    for track in expected_tracks:
        if not isinstance(track, Mapping):
            raise ExportManifestError("export manifest contains an invalid track")
        track_id = str(track.get("track_id") or "")
        if not track_id or track_id in frozen_track_ids:
            raise ExportManifestError("export manifest has duplicate or missing track ids")
        frozen_track_ids.add(track_id)
        raw_items = track.get("items") or []
        if not isinstance(raw_items, list):
            raise ExportManifestError("export manifest contains invalid items")
        item_ids: list[str] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ExportManifestError("export manifest contains an invalid item")
            speed = float(item.get("speed", 1.0))
            if not math.isfinite(speed) or not 0.25 <= speed <= 4.0:
                raise ExportManifestError("frozen compose item speed is outside 0.25x to 4x")
            item_id = str(item.get("item_id") or "")
            if not item_id:
                raise ExportManifestError("export manifest has an item without identity")
            item_ids.append(item_id)
        frozen_item_ids.append(item_ids)
    flat_item_ids = [item_id for item_ids in frozen_item_ids for item_id in item_ids]
    if len(flat_item_ids) != len(set(flat_item_ids)):
        raise ExportManifestError("export manifest has duplicate item ids")

    seen_items: set[str] = set()
    seen_tracks: set[str] = set()
    for track_index, track in enumerate(tracks):
        if track_index >= len(expected_tracks):
            raise ExportManifestError("execution tracks do not match the frozen export manifest")
        frozen_track = expected_tracks[track_index]
        track_id = str(track.get("track_id") or "")
        if not track_id or track_id in seen_tracks:
            raise ExportManifestError("execution tracks have duplicate or missing identities")
        seen_tracks.add(track_id)
        if track_id != str(frozen_track.get("track_id") or "") or str(track.get("kind") or "") != str(frozen_track.get("kind") or ""):
            raise ExportManifestError("execution tracks do not match the frozen export manifest")
        frozen_items = frozen_track.get("items") or []
        if not isinstance(frozen_items, list):
            raise ExportManifestError("export manifest contains invalid items")
        raw_items = track.get("items") or []
        if len(raw_items) != len(frozen_items):
            raise ExportManifestError("execution tracks do not match the frozen export manifest")
        for item_index, item in enumerate(raw_items):
            frozen = frozen_items[item_index]
            key_id = str(item.get("item_id") or "")
            if not key_id or key_id in seen_items:
                raise ExportManifestError("execution items have duplicate or missing identities")
            seen_items.add(key_id)
            if key_id != frozen_item_ids[track_index][item_index]:
                raise ExportManifestError("execution items do not match the frozen export manifest")
            source_path = _project_file(
                project_dir,
                str(item.get("source_path") or ""),
                label=f"compose source {key_id}",
            )
            source_relative = source_path.relative_to(project_dir.resolve()).as_posix()
            actual_dubbing = _mapping(item.get("dubbing_source"))
            frozen_dubbing = frozen.get("dubbing_source") if isinstance(frozen, Mapping) else None
            if isinstance(frozen_dubbing, Mapping):
                if actual_dubbing is None or any(
                    str(actual_dubbing.get(key) or "") != str(frozen_dubbing.get(key) or "")
                    for key in ("take_id", "dialogue_id", "group_id")
                ):
                    raise ExportManifestError("execution dubbing source does not match the frozen manifest")
                supplied_episode = actual_dubbing.get("episode")
                if supplied_episode is not None and int(supplied_episode) != int(frozen_dubbing.get("episode") or 0):
                    raise ExportManifestError("execution dubbing episode does not match the frozen manifest")
            elif actual_dubbing is not None:
                raise ExportManifestError("execution added dubbing provenance after the export was frozen")
            current = {
                "item_id": key_id,
                "source_url": str(item.get("source_url") or ""),
                "source_node_id": str(item.get("source_node_id") or "") or None,
                "source_path": source_relative,
                "timeline_start": float(item.get("timeline_start", 0.0) or 0.0),
                "source_start": float(item.get("source_start", 0.0) or 0.0),
                "source_end": float(item.get("source_end", 0.0) or 0.0),
                "volume": float(item.get("volume", 1.0) if item.get("volume") is not None else 1.0),
                "muted": bool(item.get("muted", False)),
                "speed": float(item.get("speed", 1.0) if item.get("speed") is not None else 1.0),
            }
            if not isinstance(frozen, Mapping) or not set(current).issubset(frozen):
                raise ExportManifestError("frozen export item is missing required settings")
            frozen_edit = {key: frozen[key] for key in current}
            if current != frozen_edit:
                raise ExportManifestError("execution clip settings do not match the frozen export manifest")
            if sha256_file(source_path) != str(frozen.get("source_media_sha256") or ""):
                raise ExportManifestError(f"compose source changed after freeze: {key_id}")
    if len(tracks) != len(expected_tracks) or seen_items != set(flat_item_ids):
        raise ExportManifestError("execution tracks do not match the frozen export manifest")
    expected_subtitles = manifest.get("subtitles")
    if not isinstance(expected_subtitles, Mapping):
        raise ExportManifestError("export manifest has no frozen subtitle tracks")
    actual_subtitles = _freeze_subtitle_tracks(subtitle_tracks or [], tracks)
    if actual_subtitles != expected_subtitles:
        raise ExportManifestError("execution subtitle tracks do not match the frozen export manifest")


def write_export_sidecar(
    sidecar_path: Path,
    manifest: Mapping[str, Any],
    output_path: Path,
    *,
    project_dir: Path,
    subtitle_output_path: Path | None = None,
    render_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the frozen contract next to the finished MP4 and its file hash."""
    root = project_dir.resolve()
    try:
        relative_output = output_path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ExportManifestError("compose output resolves outside the project") from exc
    artifact = {
        "path": relative_output,
        "sha256": sha256_file(output_path),
        "size": output_path.stat().st_size,
    }
    subtitles = manifest.get("subtitles")
    has_subtitles = isinstance(subtitles, Mapping) and subtitles.get("status") == "ready"
    if has_subtitles and subtitle_output_path is None:
        raise ExportManifestError("subtitle export is missing its frozen SRT artifact")
    if subtitle_output_path is not None:
        try:
            relative_subtitle = subtitle_output_path.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise ExportManifestError("subtitle artifact resolves outside the project") from exc
        if not subtitle_output_path.is_file():
            raise ExportManifestError("subtitle artifact is missing")
        artifact["subtitle_file"] = {
            "path": relative_subtitle,
            "sha256": sha256_file(subtitle_output_path),
            "size": subtitle_output_path.stat().st_size,
            "rendered_in_video": True,
        }
    if render_validation is not None:
        artifact["ffprobe"] = dict(render_validation)
    result = {
        "manifest": dict(manifest),
        "artifact": artifact,
    }
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{sidecar_path.name}.", dir=sidecar_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temp_name).replace(sidecar_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return result


__all__ = [
    "ExportManifestError",
    "build_export_manifest",
    "canonical_json",
    "sha256_file",
    "verify_export_source_integrity",
    "write_export_sidecar",
]
