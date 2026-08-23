"""Pure grouping rules plus a versioned JSON sidecar store."""

from __future__ import annotations

import json
import inspect
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

import portalocker

from .nonvisual import is_nonvisual_production_note
from .models import (
    CellMapping,
    GridLayout,
    GroupStageState,
    NarrativeGroup,
    StageName,
    VideoPlan,
    VideoPlanUnit,
)

SIDECAR_VERSION = 1
VIDEO_PROMPT_MANIFEST_MAX_BYTES = 12 * 1024 * 1024
VIDEO_PROMPT_MANIFEST_MAX_ENTRIES = 512
VIDEO_PROMPT_MANIFEST_MAX_DEPTH = 32
VIDEO_PROMPT_MANIFEST_MAX_COLLECTION_ITEMS = 4096
VIDEO_PROMPT_MANIFEST_MAX_STRING_LENGTH = 512 * 1024
_SIDECAR_LOCKS: dict[str, threading.RLock] = {}
_SIDECAR_LOCKS_GUARD = threading.Lock()
_SIDECAR_LOCK_STATE = threading.local()


def _sidecar_lock(project_dir: str | Path, episode: int) -> threading.RLock:
    key = str(sidecar_path(project_dir, episode).resolve())
    with _SIDECAR_LOCKS_GUARD:
        return _SIDECAR_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _sidecar_guard(project_dir: str | Path, episode: int):
    """Re-entrant in-process lock plus a Windows-compatible process lock."""
    target = sidecar_path(project_dir, episode)
    key = str(target.resolve())
    held = getattr(_SIDECAR_LOCK_STATE, "held", set())
    with _sidecar_lock(project_dir, episode):
        if key in held:
            yield
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(f"{target.suffix}.lock")
        with portalocker.Lock(str(lock_path), mode="a+", timeout=60):
            previous = held
            _SIDECAR_LOCK_STATE.held = {*held, key}
            try:
                yield
            finally:
                _SIDECAR_LOCK_STATE.held = previous


def layout_for_group(count: int) -> GridLayout:
    if count < 1 or count > 9:
        raise ValueError("a narrative group must contain between 1 and 9 beats")
    if count == 1:
        return GridLayout(rows=1, columns=1, capacity=1)
    if count == 2:
        return GridLayout(rows=1, columns=2, capacity=2)
    if count <= 4:
        return GridLayout(rows=2, columns=2, capacity=4)
    if count <= 6:
        return GridLayout(rows=2, columns=3, capacity=6)
    return GridLayout(rows=3, columns=3, capacity=9)


def _beat_id(beat: Any) -> str:
    if isinstance(beat, Mapping):
        value = beat.get("id") or beat.get("beat_id") or beat.get("beat_number")
    else:
        value = getattr(beat, "id", None) or getattr(beat, "beat_id", None)
        if value is None:
            value = getattr(beat, "beat_number", None)
    if value is None or str(value).strip() == "":
        raise ValueError("every beat must have an id, beat_id, or beat_number")
    return str(value)


def _continuity_key(beat: Any) -> tuple[str, str]:
    def value(*names: str) -> str:
        for name in names:
            raw = beat.get(name) if isinstance(beat, Mapping) else getattr(beat, name, None)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
        return ""

    return (
        value("scene_id", "scene", "location", "scene_name"),
        value("time_of_day", "scene_time", "time", "time_label"),
    )


def _beat_duration(beat: Any) -> float:
    for name in ("duration_seconds", "video_duration", "duration"):
        raw = beat.get(name) if isinstance(beat, Mapping) else getattr(beat, name, None)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 5.0


def _derive_video_plan(
    group: NarrativeGroup,
    beat_by_id: Mapping[str, Any],
    partitions: Iterable[Iterable[str]],
    *,
    revision: int,
    source: str,
) -> VideoPlan:
    units = []
    for index, raw_ids in enumerate(partitions, start=1):
        beat_ids = tuple(str(value) for value in raw_ids)
        duration = sum(_beat_duration(beat_by_id.get(beat_id)) for beat_id in beat_ids)
        pair = len(beat_ids) == 2
        units.append(
            VideoPlanUnit(
                id=f"unit-{index:02d}",
                beat_ids=beat_ids,
                mode="fl2va" if pair else "i2va",
                duration_seconds=duration,
                reason=(
                    f"{source}_adjacent_pair" if pair else f"{source}_singleton"
                ),
            )
        )
    return VideoPlan(
        revision=revision,
        source=source,
        units=tuple(units),
        total_duration_seconds=sum(unit.duration_seconds for unit in units),
    )


def _recommended_video_plan(
    group: NarrativeGroup, beat_by_id: Mapping[str, Any]
) -> VideoPlan:
    partitions: list[tuple[str, ...]] = []
    index = 0
    pair_turn = True
    while index < len(group.beat_ids):
        current = group.beat_ids[index]
        if pair_turn and index + 1 < len(group.beat_ids):
            following = group.beat_ids[index + 1]
            if (
                _beat_duration(beat_by_id.get(current))
                + _beat_duration(beat_by_id.get(following))
                <= 10
            ):
                partitions.append((current, following))
                index += 2
                pair_turn = False
                continue
        partitions.append((current,))
        index += 1
        pair_turn = True
    return _derive_video_plan(
        group,
        beat_by_id,
        partitions,
        revision=1,
        source="recommended",
    )


def _has_valid_video_plan(group: NarrativeGroup) -> bool:
    plan = group.video_plan
    return bool(
        plan.revision > 0
        and plan.units
        and all(len(unit.beat_ids) in {1, 2} for unit in plan.units)
        and tuple(
            beat_id for unit in plan.units for beat_id in unit.beat_ids
        )
        == group.beat_ids
    )


def group_beats(beats: Iterable[Any]) -> list[NarrativeGroup]:
    source = [beat for beat in beats if not is_nonvisual_production_note(beat)]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_key: tuple[str, str] | None = None
    for beat in source:
        key = _continuity_key(beat)
        # Empty legacy continuity fields do not create artificial boundaries.
        boundary = bool(current and current_key and key != current_key and any(current_key + key))
        if len(current) >= 9 or boundary:
            chunks.append(current)
            current = []
        current.append(_beat_id(beat))
        current_key = key
    if current:
        chunks.append(current)
    groups: list[NarrativeGroup] = []
    for chunk in chunks:
        group_ids = tuple(chunk)
        ordinal = len(groups) + 1
        groups.append(
            NarrativeGroup(
                id=f"ng-{ordinal:02d}",
                ordinal=ordinal,
                beat_ids=group_ids,
                layout=layout_for_group(len(group_ids)),
                cell_to_beat=tuple(
                    CellMapping(cell=cell, beat_id=beat_id)
                    for cell, beat_id in enumerate(group_ids)
                ),
            )
        )
    beat_by_id = {_beat_id(beat): beat for beat in source}
    return [
        replace(group, video_plan=_recommended_video_plan(group, beat_by_id))
        for group in groups
    ]


def sidecar_path(project_dir: str | Path, episode: int) -> Path:
    return Path(project_dir) / ".narrative_groups" / f"ep{episode:03d}.json"


def save_groups(project_dir: str | Path, episode: int, groups: list[NarrativeGroup]) -> None:
    with _sidecar_guard(project_dir, episode):
        target = sidecar_path(project_dir, episode)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SIDECAR_VERSION, "episode": episode, "groups": [g.to_dict() for g in groups]}
        temporary = target.with_suffix(
            f"{target.suffix}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
        )
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)


def _group_from_dict(data: Mapping[str, Any]) -> NarrativeGroup:
    layout = data["layout"]
    stages = {}
    for name, raw_state in (data.get("stages") or {}).items():
        state = dict(raw_state)
        state["cell_assets"] = tuple(state.get("cell_assets") or ())
        state["revision_history"] = tuple(state.get("revision_history") or ())
        stages[name] = GroupStageState(**state)
    default_stages = {
        "sketch": GroupStageState(),
        "render": GroupStageState(),
        "video": GroupStageState(),
    }
    raw_plan = dict(data.get("video_plan") or {})
    plan_units = tuple(
        VideoPlanUnit(
            id=str(item.get("id") or f"unit-{index:02d}"),
            beat_ids=tuple(str(value) for value in item.get("beat_ids") or ()),
            mode=str(item.get("mode") or "i2va"),
            duration_seconds=float(item.get("duration_seconds") or 0),
            reason=str(item.get("reason") or ""),
        )
        for index, item in enumerate(raw_plan.get("units") or (), start=1)
    )
    video_plan = VideoPlan(
        revision=int(raw_plan.get("revision") or 0),
        source=str(raw_plan.get("source") or "recommended"),
        units=plan_units,
        total_duration_seconds=float(
            raw_plan.get("total_duration_seconds")
            or sum(unit.duration_seconds for unit in plan_units)
        ),
    )
    return NarrativeGroup(
        id=str(data["id"]),
        ordinal=int(data["ordinal"]),
        beat_ids=tuple(str(value) for value in data["beat_ids"]),
        layout=GridLayout(**layout),
        cell_to_beat=tuple(CellMapping(**item) for item in data["cell_to_beat"]),
        video_plan=video_plan,
        stages=stages or default_stages,
        errors=tuple(data.get("errors") or ()),
    )


def load_groups(project_dir: str | Path, episode: int) -> list[NarrativeGroup]:
    with _sidecar_guard(project_dir, episode):
        path = sidecar_path(project_dir, episode)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version") or 0) != SIDECAR_VERSION:
        raise ValueError("unsupported narrative-group sidecar version")
    return [_group_from_dict(item) for item in payload.get("groups") or []]


def ensure_groups(project_dir: str | Path, episode: int, beats: Iterable[Any]) -> list[NarrativeGroup]:
    source = list(beats)
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        if groups:
            beat_by_id = {_beat_id(beat): beat for beat in source}
            migrated = [
                (
                    group
                    if _has_valid_video_plan(group)
                    else replace(
                        group,
                        video_plan=_recommended_video_plan(group, beat_by_id),
                    )
                )
                for group in groups
            ]
            if migrated != groups:
                save_groups(project_dir, episode, migrated)
            return migrated
        groups = group_beats(source)
        save_groups(project_dir, episode, groups)
        return groups


def rebuild_groups(project_dir: str | Path, episode: int, beats: Iterable[Any]) -> list[NarrativeGroup]:
    with _sidecar_guard(project_dir, episode):
        previous = {group.id: group for group in load_groups(project_dir, episode)}
        rebuilt = []
        for group in group_beats(beats):
            old = previous.get(group.id)
            unchanged = bool(
                old
                and old.beat_ids == group.beat_ids
                and old.layout == group.layout
                and old.cell_to_beat == group.cell_to_beat
            )
            rebuilt.append(
                (
                    replace(
                        group,
                        video_plan=(
                            old.video_plan
                            if old.video_plan.revision > 0 and old.video_plan.units
                            else group.video_plan
                        ),
                        stages=old.stages,
                        errors=old.errors,
                    )
                    if unchanged
                    else group
                )
            )
        save_groups(project_dir, episode, rebuilt)
        return rebuilt


def update_video_plan(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    beats: Iterable[Any],
    *,
    expected_revision: int,
    units: Iterable[Mapping[str, Any]],
) -> NarrativeGroup:
    source = list(beats)
    beat_by_id = {_beat_id(beat): beat for beat in source}
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        group = next((item for item in groups if item.id == group_id), None)
        if group is None:
            raise KeyError(group_id)
        if group.video_plan.revision != int(expected_revision):
            raise RuntimeError("narrative group video plan revision is stale")
        video_stage = group.stages.get("video", GroupStageState())
        if video_stage.status in {"queued", "running"}:
            raise RuntimeError(
                f"cannot update video plan while video stage is {video_stage.status}"
            )

        partitions = []
        for raw_unit in units:
            beat_ids = tuple(str(value) for value in raw_unit.get("beat_ids") or ())
            if len(beat_ids) not in {1, 2}:
                raise ValueError("each video plan unit must contain one or two beats")
            partitions.append(beat_ids)
        flattened = tuple(beat_id for unit in partitions for beat_id in unit)
        if flattened != group.beat_ids:
            raise ValueError(
                "video plan units must form a complete ordered partition"
            )
        positions = {beat_id: index for index, beat_id in enumerate(group.beat_ids)}
        if any(
            len(unit) == 2 and positions[unit[1]] != positions[unit[0]] + 1
            for unit in partitions
        ):
            raise ValueError("paired video plan beats must be adjacent")

        plan = _derive_video_plan(
            group,
            beat_by_id,
            partitions,
            revision=group.video_plan.revision + 1,
            source="manual",
        )
        current = group.stages.get("video", GroupStageState())
        invalidated = replace(
            current,
            status="pending",
            grid_asset="",
            cell_assets=(),
            video_asset="",
            manifest_asset="",
            original_audio_path="",
            dialogue_stem_path="",
            ambience_stem_path="",
            dialogue_stem_status="not_requested",
            ambience_stem_status="not_requested",
            error="",
            actual_provider="",
            actual_model="",
            actual_mode="",
            created_at="",
        )
        stages = dict(group.stages)
        stages["video"] = invalidated
        updated_group = replace(group, video_plan=plan, stages=stages)
        updated = [
            updated_group if item.id == group_id else item
            for item in groups
        ]
        save_groups(project_dir, episode, updated)
        return updated_group


def advance_revision(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    stage: StageName,
    *,
    regenerate: bool = False,
    expected_revision: int | None = None,
) -> tuple[NarrativeGroup, int]:
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        original = next((group for group in groups if group.id == group_id), None)
        if original is None:
            raise KeyError(group_id)
        current = original.stages.get(stage, GroupStageState())
        if expected_revision is not None and current.revision != int(expected_revision):
            raise RuntimeError(f"narrative group {stage} revision is stale")
        found: NarrativeGroup | None = None
        updated: list[NarrativeGroup] = []
        for group in groups:
            if group.id != group_id:
                updated.append(group)
                continue
            current = group.stages.get(stage, GroupStageState())
            revision = current.revision + 1 if regenerate or current.revision == 0 else current.revision
            stages = dict(group.stages)
            history = current.revision_history
            if regenerate and current.revision:
                history = (*history, _stage_snapshot(current))
            stages[stage] = replace(
                current,
                status="queued",
                revision=revision,
                grid_asset="" if regenerate else current.grid_asset,
                cell_assets=() if regenerate else current.cell_assets,
                error="",
                actual_provider="" if regenerate else current.actual_provider,
                actual_model="" if regenerate else current.actual_model,
                actual_mode="" if regenerate else current.actual_mode,
                created_at="" if regenerate else current.created_at,
                revision_history=history,
                video_asset="" if regenerate and stage == "video" else current.video_asset,
                manifest_asset="" if regenerate and stage == "video" else current.manifest_asset,
                original_audio_path="" if regenerate and stage == "video" else current.original_audio_path,
                dialogue_stem_path="" if regenerate and stage == "video" else current.dialogue_stem_path,
                ambience_stem_path="" if regenerate and stage == "video" else current.ambience_stem_path,
                dialogue_stem_status="not_requested" if regenerate and stage == "video" else current.dialogue_stem_status,
                ambience_stem_status="not_requested" if regenerate and stage == "video" else current.ambience_stem_status,
            )
            found = replace(group, stages=stages)
            updated.append(found)
        if found is None:
            raise KeyError(group_id)
        save_groups(project_dir, episode, updated)
        return found, found.stages[stage].revision


@dataclass(frozen=True)
class VideoRevisionReservation:
    """A queued video revision paired with the exact stage it replaced."""

    group_id: str
    revision: int
    previous_stage: GroupStageState


def reserve_video_revision(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    *,
    expected_revision: int,
    expected_plan_revision: int | None = None,
) -> tuple[NarrativeGroup, VideoRevisionReservation]:
    """Atomically reserve the next video revision while retaining rollback data.

    The caller must invoke :func:`restore_video_reservation` if task enqueue
    fails.  The compare-and-swap restore prevents an older failed request from
    overwriting a later successful reservation.
    """
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        found: NarrativeGroup | None = None
        updated: list[NarrativeGroup] = []
        reservation: VideoRevisionReservation | None = None
        for group in groups:
            if group.id != group_id:
                updated.append(group)
                continue
            if (
                expected_plan_revision is not None
                and group.video_plan.revision != int(expected_plan_revision)
            ):
                raise RuntimeError("narrative group video plan revision is stale")
            current = group.stages.get("video", GroupStageState())
            if current.revision != int(expected_revision):
                raise RuntimeError("narrative group video revision is stale")
            revision = current.revision + 1
            history = (*current.revision_history, _stage_snapshot(current)) if current.revision else current.revision_history
            stages = dict(group.stages)
            stages["video"] = replace(
                current,
                status="queued",
                revision=revision,
                grid_asset="",
                cell_assets=(),
                error="",
                actual_provider="",
                actual_model="",
                actual_mode="",
                created_at="",
                revision_history=history,
                video_asset="",
                manifest_asset="",
                original_audio_path="",
                dialogue_stem_path="",
                ambience_stem_path="",
                dialogue_stem_status="not_requested",
                ambience_stem_status="not_requested",
            )
            found = replace(group, stages=stages)
            reservation = VideoRevisionReservation(group_id=group_id, revision=revision, previous_stage=current)
            updated.append(found)
        if found is None or reservation is None:
            raise KeyError(group_id)
        save_groups(project_dir, episode, updated)
        return found, reservation


def restore_video_reservation(
    project_dir: str | Path,
    episode: int,
    reservation: VideoRevisionReservation,
) -> bool:
    """Restore a failed enqueue only if its reservation is still current."""
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        updated: list[NarrativeGroup] = []
        restored = False
        for group in groups:
            if group.id != reservation.group_id:
                updated.append(group)
                continue
            current = group.stages.get("video", GroupStageState())
            if current.revision == reservation.revision and current.status == "queued":
                stages = dict(group.stages)
                stages["video"] = reservation.previous_stage
                updated.append(replace(group, stages=stages))
                restored = True
            else:
                updated.append(group)
        if restored:
            save_groups(project_dir, episode, updated)
        return restored


def record_stage_result(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    stage: StageName,
    *,
    expected_revision: int,
    status: str,
    grid_asset: str | None = None,
    cell_assets: Iterable[Mapping[str, Any]] | None = None,
    error: str | None = None,
    actual_provider: str | None = None,
    actual_model: str | None = None,
    actual_mode: str | None = None,
    source_sketch_revision: int | None = None,
    constraint_mode: str | None = None,
    video_asset: str | None = None,
    manifest_asset: str | None = None,
    original_audio_path: str | None = None,
    dialogue_stem_path: str | None = None,
    ambience_stem_path: str | None = None,
    dialogue_stem_status: str | None = None,
    ambience_stem_status: str | None = None,
) -> NarrativeGroup:
    """Atomically merge a runner outcome into the durable group sidecar."""
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        found: NarrativeGroup | None = None
        updated: list[NarrativeGroup] = []
        for group in groups:
            if group.id != group_id:
                updated.append(group)
                continue
            current = group.stages.get(stage, GroupStageState())
            if current.revision != int(expected_revision):
                found = group
                updated.append(group)
                continue
            state = replace(
                current,
                status=status,
                grid_asset=current.grid_asset if grid_asset is None else str(grid_asset),
                cell_assets=(
                    current.cell_assets
                    if cell_assets is None
                    else tuple(dict(item) for item in cell_assets)
                ),
                video_asset=current.video_asset if video_asset is None else str(video_asset),
                manifest_asset=current.manifest_asset if manifest_asset is None else str(manifest_asset),
                original_audio_path=(
                    current.original_audio_path if original_audio_path is None else str(original_audio_path)
                ),
                dialogue_stem_path=(
                    current.dialogue_stem_path if dialogue_stem_path is None else str(dialogue_stem_path)
                ),
                ambience_stem_path=(
                    current.ambience_stem_path if ambience_stem_path is None else str(ambience_stem_path)
                ),
                dialogue_stem_status=(
                    current.dialogue_stem_status if dialogue_stem_status is None else str(dialogue_stem_status)
                ),
                ambience_stem_status=(
                    current.ambience_stem_status if ambience_stem_status is None else str(ambience_stem_status)
                ),
                error=current.error if error is None else str(error),
                actual_provider=(
                    current.actual_provider
                    if actual_provider is None
                    else str(actual_provider)
                ),
                actual_model=(
                    current.actual_model if actual_model is None else str(actual_model)
                ),
                actual_mode=(
                    current.actual_mode if actual_mode is None else str(actual_mode)
                ),
                source_sketch_revision=(
                    current.source_sketch_revision
                    if source_sketch_revision is None
                    else int(source_sketch_revision)
                ),
                constraint_mode=(
                    current.constraint_mode if constraint_mode is None else str(constraint_mode)
                ),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            stages = dict(group.stages)
            stages[stage] = state
            found = replace(group, stages=stages)
            updated.append(found)
        if found is None:
            raise KeyError(group_id)
        save_groups(project_dir, episode, updated)
        return found


def _stage_snapshot(state: GroupStageState) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "status": state.status,
        "grid_asset": state.grid_asset,
        "cell_assets": list(state.cell_assets),
        "video_asset": state.video_asset,
        "manifest_asset": state.manifest_asset,
        "original_audio_path": state.original_audio_path,
        "dialogue_stem_path": state.dialogue_stem_path,
        "ambience_stem_path": state.ambience_stem_path,
        "dialogue_stem_status": state.dialogue_stem_status,
        "ambience_stem_status": state.ambience_stem_status,
        "error": state.error,
        "actual_provider": state.actual_provider,
        "actual_model": state.actual_model,
        "actual_mode": state.actual_mode,
        "source_sketch_revision": state.source_sketch_revision,
        "constraint_mode": state.constraint_mode,
        "created_at": state.created_at,
    }


def stage_history(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    stage: StageName,
) -> list[dict[str, Any]]:
    for group in load_groups(project_dir, episode):
        if group.id == group_id:
            return list(group.stages.get(stage, GroupStageState()).revision_history)
    raise KeyError(group_id)


def rollback_stage_revision(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    stage: StageName,
    *,
    revision: int,
) -> NarrativeGroup:
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        updated: list[NarrativeGroup] = []
        found = None
        for group in groups:
            if group.id != group_id:
                updated.append(group)
                continue
            current = group.stages.get(stage, GroupStageState())
            candidates = [*current.revision_history, _stage_snapshot(current)]
            source = next(
                (
                    item
                    for item in candidates
                    if int(item["revision"]) == int(revision)
                ),
                None,
            )
            if source is None:
                raise KeyError(f"revision {revision}")
            history = (*current.revision_history, _stage_snapshot(current))
            restored = GroupStageState(
                status=source["status"],
                revision=current.revision + 1,
                grid_asset=source.get("grid_asset", ""),
                cell_assets=tuple(source.get("cell_assets") or ()),
                video_asset=source.get("video_asset", ""),
                manifest_asset=source.get("manifest_asset", ""),
                original_audio_path=source.get("original_audio_path", ""),
                dialogue_stem_path=source.get("dialogue_stem_path", ""),
                ambience_stem_path=source.get("ambience_stem_path", ""),
                dialogue_stem_status=source.get("dialogue_stem_status", "not_requested"),
                ambience_stem_status=source.get("ambience_stem_status", "not_requested"),
                error=source.get("error", ""),
                actual_provider=source.get("actual_provider", ""),
                actual_model=source.get("actual_model", ""),
                actual_mode=source.get("actual_mode", ""),
                source_sketch_revision=int(source.get("source_sketch_revision") or 0),
                constraint_mode=source.get("constraint_mode", ""),
                created_at=datetime.now(timezone.utc).isoformat(),
                revision_history=history,
            )
            stages = dict(group.stages)
            stages[stage] = restored
            found = replace(group, stages=stages)
            updated.append(found)
        if found is None:
            raise KeyError(group_id)
        save_groups(project_dir, episode, updated)
        return found


def stage_payload(project_dir: str | Path, episode: int, group_id: str, stage: StageName) -> dict[str, Any]:
    for group in load_groups(project_dir, episode):
        if group.id == group_id:
            state = group.stages.get(stage, GroupStageState())
            return {
                "grid_asset": state.grid_asset,
                "cell_assets": list(state.cell_assets),
                "video_asset": state.video_asset,
                "manifest_asset": state.manifest_asset,
                "original_audio_path": state.original_audio_path,
                "dialogue_stem_path": state.dialogue_stem_path,
                "ambience_stem_path": state.ambience_stem_path,
                "dialogue_stem_status": state.dialogue_stem_status,
                "ambience_stem_status": state.ambience_stem_status,
                "error": state.error,
                "revision": state.revision,
                "cell_to_beat": [item.__dict__ for item in group.cell_to_beat],
                "beat_ids": list(group.beat_ids),
                "layout": group.layout.__dict__,
                "video_plan": group.video_plan.to_dict(),
            }
    raise KeyError(group_id)


def load_group_video_prompt_manifest(
    project_dir: str | Path, episode: int, group_id: str
) -> tuple[dict[str, Any], GroupStageState]:
    """Load the current video manifest without trusting a client-supplied path."""
    root = Path(project_dir).resolve()
    group = next(
        (item for item in load_groups(root, episode) if item.id == group_id), None
    )
    if group is None:
        raise KeyError(group_id)
    stage = group.stages.get("video", GroupStageState())
    stored = str(stage.manifest_asset or "").strip()
    if not stored:
        raise FileNotFoundError("narrative group video manifest is unavailable")
    candidate = Path(stored)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise FileNotFoundError("narrative group video manifest is unavailable")
    if resolved.stat().st_size > VIDEO_PROMPT_MANIFEST_MAX_BYTES:
        raise ValueError("narrative group video manifest exceeds size limit")
    raw = resolved.read_bytes()
    if len(raw) > VIDEO_PROMPT_MANIFEST_MAX_BYTES:
        raise ValueError("narrative group video manifest exceeds size limit")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("narrative group video manifest is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError("narrative group video manifest is invalid")
    if len(payload["entries"]) > VIDEO_PROMPT_MANIFEST_MAX_ENTRIES:
        raise ValueError("narrative group video manifest has too many entries")
    stack: list[tuple[Any, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > VIDEO_PROMPT_MANIFEST_MAX_DEPTH:
            raise ValueError("narrative group video manifest is too deeply nested")
        if isinstance(value, dict):
            if len(value) > VIDEO_PROMPT_MANIFEST_MAX_COLLECTION_ITEMS:
                raise ValueError("narrative group video manifest collection is too large")
            for key, child in value.items():
                if len(key) > VIDEO_PROMPT_MANIFEST_MAX_STRING_LENGTH:
                    raise ValueError("narrative group video manifest string is too long")
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > VIDEO_PROMPT_MANIFEST_MAX_COLLECTION_ITEMS:
                raise ValueError("narrative group video manifest collection is too large")
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if len(value) > VIDEO_PROMPT_MANIFEST_MAX_STRING_LENGTH:
                raise ValueError("narrative group video manifest string is too long")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("narrative group video manifest number is invalid")
    return payload, stage


def update_video_manifest_dialogue_source(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    *,
    span_index: int,
    dialogue_source: str,
    expected_revision: int,
):
    """Atomically change one logical H3 span without regenerating video.

    The sidecar lock serializes revision checks with any generation task.  The
    manifest itself is replaced atomically, so a composer sees either the old
    complete mapping or the new complete mapping, never a partial JSON write.
    """
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        H3DirectorOutputManifest,
        H3TimelineEntry,
        load_h3_director_manifest,
        save_h3_director_manifest,
    )

    with _sidecar_guard(project_dir, episode):
        group = next((item for item in load_groups(project_dir, episode) if item.id == group_id), None)
        if group is None:
            raise KeyError(group_id)
        state = group.stages.get("video", GroupStageState())
        if state.revision != int(expected_revision):
            raise RuntimeError("narrative group video revision is stale")
        manifest_name = str(state.manifest_asset or "").strip()
        if not manifest_name:
            raise FileNotFoundError("narrative group video manifest is unavailable")
        manifest_path = Path(manifest_name)
        if not manifest_path.is_file():
            raise FileNotFoundError("narrative group video manifest is unavailable")
        manifest = load_h3_director_manifest(manifest_path)
        if span_index < 0 or span_index >= len(manifest.entries):
            raise IndexError(span_index)
        source = DialogueSource(dialogue_source)
        entries = []
        for index, entry in enumerate(manifest.entries):
            segment = entry.segment
            if index == span_index:
                segment = segment.model_copy(update={"dialogue_source": source})
            entries.append(H3TimelineEntry(
                segment=segment,
                start_frame=entry.start_frame,
                frame_count=entry.frame_count,
                physical_video=entry.physical_video,
                format_version=entry.format_version,
                workflow_id=entry.workflow_id,
                provider_task_id=entry.provider_task_id,
            ))
        updated = H3DirectorOutputManifest(
            **manifest.model_dump(exclude={"entries"}), entries=tuple(entries)
        )
        save_h3_director_manifest(manifest_path, updated)
        return updated


async def _await_result(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def retry_split(
    payload: Mapping[str, Any],
    *,
    splitter: Callable[[str, Mapping[str, Any]], Awaitable[dict] | dict],
) -> dict[str, Any]:
    """Retry only deterministic cutting; never invoke the image provider."""
    grid_asset = str(payload.get("grid_asset") or "").strip()
    if not grid_asset:
        raise ValueError("grid_asset is required for split retry")
    split_result = await _await_result(splitter(grid_asset, payload))
    errors = list(split_result.get("errors") or [])
    return {
        "group_id": str(payload["group_id"]),
        "stage": str(payload["stage"]),
        "revision": int(payload["revision"]),
        "grid_asset": grid_asset,
        "cell_to_beat": list(payload.get("cell_to_beat") or []),
        "cell_assets": list(split_result.get("cell_assets") or []),
        "errors": errors,
        "status": "partial_failure" if errors else "completed",
    }


async def run_group_grid(
    payload: Mapping[str, Any],
    *,
    generator: Callable[[Mapping[str, Any]], Awaitable[dict] | dict],
    splitter: Callable[[str, Mapping[str, Any]], Awaitable[dict] | dict],
) -> dict[str, Any]:
    """Generate once and split before reporting completion.

    Dependencies are explicit so the orchestration can be tested without any
    provider calls and reused by both sketch and render task runners.
    """
    generated = await _await_result(generator(payload))
    grid_asset = str(generated.get("grid_asset") or "").strip()
    if not grid_asset:
        raise RuntimeError("grid generation did not return grid_asset")
    result = await retry_split({**dict(payload), "grid_asset": grid_asset}, splitter=splitter)
    for field in ("actual_provider", "actual_model", "actual_mode"):
        if generated.get(field) is not None:
            result[field] = generated[field]
    return result
