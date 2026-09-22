"""A complete selected storyboard and its frozen enqueue-time cell bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from .storyboard_sources import Digest, Identifier, PositiveInt, StoryboardSource
from novelvideo.utils.safe_paths import resolve_under_root


def selection_identity(selection: dict[str, str]) -> str:
    if len(selection) == 1:
        return next(iter(selection.values()))
    return hashlib.sha256(
        json.dumps(sorted(selection.items()), separators=(",", ":")).encode()
    ).hexdigest()


class StoryboardBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    episode: PositiveInt
    group_id: Identifier
    selection_id: Digest
    shot_ids: tuple[str, ...]
    sources: tuple[StoryboardSource, ...]

    @model_validator(mode="after")
    def _validate_selection(self) -> Self:
        selection = {source.batch_id: source.source_id for source in self.sources}
        shots = [cell.shot_id for source in self.sources for cell in source.cells]
        if not self.sources or len(selection) != len(self.sources):
            raise ValueError("invalid storyboard batch selection")
        if len(set(shots)) != len(shots) or len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError("duplicate storyboard shots")
        if set(shots) != set(self.shot_ids):
            raise ValueError("incomplete storyboard selection")
        if selection_identity(selection) != self.selection_id:
            raise ValueError("storyboard selection identity mismatch")
        for source in self.sources:
            if (source.project_id, source.episode, source.group_id) != (
                self.project_id, self.episode, self.group_id
            ):
                raise ValueError("storyboard binding scope mismatch")
        return self

    def cell_assets(self, media_root: Path) -> tuple[dict, ...]:
        by_shot = {
            cell.shot_id: {
                "cell": cell.cell_index, "beat_id": cell.shot_id, "shot_id": cell.shot_id,
                "path": str(resolve_under_root(media_root, cell.path)), "sha256": cell.sha256,
                "storyboard_source_id": source.source_id,
            }
            for source in self.sources for cell in source.cells
        }
        return tuple(by_shot[shot] for shot in self.shot_ids)

    def validate_frames(self, frames) -> None:
        """Validate the frozen byte identities without consulting mutable files."""
        import hashlib

        expected = {(cell.sha256, cell.width, cell.height)
                    for source in self.sources for cell in source.cells}
        actual = {(frame.sha256, frame.width, frame.height) for frame in frames.values()}
        if expected != actual or any(hashlib.sha256(frame.content).hexdigest() != frame.sha256
                                     for frame in frames.values()):
            raise ValueError("storyboard frozen frame digest or dimensions mismatch")


def freeze_selected_storyboard(group, *, media_root: Path, project_id: str, episode: int):
    """Freeze all selected cells, including I2VA context (not just endpoints)."""
    from .service import _storyboard_selection, _validate_storyboard_batch
    from novelvideo.media_capabilities.video.h3_reference_runtime import freeze_h3_reference_frames

    state = group.stages["render"]
    sources = [StoryboardSource.model_validate(raw) for raw in state.storyboard_sources]
    selection = _storyboard_selection(state, sources)
    by_id = {source.source_id: source for source in sources}
    try:
        chosen = tuple(by_id[source_id] for _, source_id in sorted(selection.items()))
    except KeyError:
        raise ValueError("selected storyboard source is unavailable") from None
    binding = StoryboardBinding(project_id=project_id, episode=episode, group_id=group.id,
        selection_id=state.selected_storyboard_id, shot_ids=group.production_beat_ids, sources=chosen)
    for source in chosen:
        _validate_storyboard_batch(group, source)
        source.validate_files(media_root, project_id=project_id)
    assets = binding.cell_assets(media_root)
    frames = freeze_h3_reference_frames(
        tuple(SimpleNamespace(first_frame=asset["path"], last_frame=None) for asset in assets),
        project_root=media_root,
    )
    # Recheck each path after the safe read; equal image sets are insufficient
    # to catch a concurrent swap between different shot paths.
    for source in chosen:
        for cell in source.cells:
            frame = frames[str(resolve_under_root(media_root, cell.path))]
            if (frame.sha256, frame.width, frame.height) != (cell.sha256, cell.width, cell.height):
                raise ValueError("storyboard frozen cell digest mismatch")
    binding.validate_frames(frames)
    return binding, frames


def load_queued_storyboard(payload, *, media_root: Path, state_root: Path,
                          project_id: str, episode: int, group_id: str):
    """Recover only enqueue-time inputs, never re-read current cell images."""
    from novelvideo.media_capabilities.video.h3_reference_runtime import load_h3_reference_input_snapshot

    if type(payload.get("storyboard_contract_version")) is not int or payload["storyboard_contract_version"] != 1:
        raise ValueError("unsupported queued storyboard contract")
    binding = StoryboardBinding.model_validate(payload.get("storyboard_binding"))
    if (binding.project_id, binding.episode, binding.group_id, binding.selection_id) != (
        project_id, episode, group_id, payload.get("storyboard_source_id")
    ):
        raise ValueError("queued storyboard binding scope mismatch")
    cells = binding.cell_assets(media_root)
    snapshot = load_h3_reference_input_snapshot(
        state_root=state_root, snapshot_id=payload["reference_snapshot_id"],
        expected_digest=payload["reference_snapshot_digest"],
        frame_sources=tuple(cell["path"] for cell in cells),
    )
    if snapshot.storyboard_binding != binding:
        raise ValueError("queued storyboard does not match frozen snapshot")
    for cell in cells:
        if snapshot.frames[cell["path"]].sha256 != cell["sha256"]:
            raise ValueError("queued storyboard frame mapping mismatch")
    return binding, snapshot
