"""Pure grouping rules plus a versioned JSON sidecar store."""

from __future__ import annotations

import json
import inspect
import os
import threading
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

import portalocker

from .models import CellMapping, GridLayout, GroupStageState, NarrativeGroup, StageName

SIDECAR_VERSION = 1
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


def group_beats(beats: Iterable[Any]) -> list[NarrativeGroup]:
    source = list(beats)
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
    return groups


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
    return NarrativeGroup(
        id=str(data["id"]),
        ordinal=int(data["ordinal"]),
        beat_ids=tuple(str(value) for value in data["beat_ids"]),
        layout=GridLayout(**layout),
        cell_to_beat=tuple(CellMapping(**item) for item in data["cell_to_beat"]),
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
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        if groups:
            return groups
        groups = group_beats(beats)
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
                replace(group, stages=old.stages, errors=old.errors) if unchanged else group
            )
        save_groups(project_dir, episode, rebuilt)
        return rebuilt


def advance_revision(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    stage: StageName,
    *,
    regenerate: bool = False,
) -> tuple[NarrativeGroup, int]:
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
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
            )
            found = replace(group, stages=stages)
            updated.append(found)
        if found is None:
            raise KeyError(group_id)
        save_groups(project_dir, episode, updated)
        return found, found.stages[stage].revision


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
        "error": state.error,
        "actual_provider": state.actual_provider,
        "actual_model": state.actual_model,
        "actual_mode": state.actual_mode,
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
                error=source.get("error", ""),
                actual_provider=source.get("actual_provider", ""),
                actual_model=source.get("actual_model", ""),
                actual_mode=source.get("actual_mode", ""),
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
                "error": state.error,
                "revision": state.revision,
                "cell_to_beat": [item.__dict__ for item in group.cell_to_beat],
                "beat_ids": list(group.beat_ids),
                "layout": group.layout.__dict__,
            }
    raise KeyError(group_id)


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
