"""Read-only provenance contracts for versioned storyboard images.

Paths are relative to the project's media root, never shared frame aliases.
Validation does not repair, re-split, or regenerate any asset.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novelvideo.utils.safe_paths import resolve_under_root

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


def source_identity(*, grid_sha256: str, generation_id: str) -> str:
    """Identify a grid generation independently of its display revision."""
    value = json.dumps([grid_sha256, generation_id], separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value or path.is_absolute() or ".." in path.parts
        or "\\" in value or "\0" in value or ":" in value or str(path) == "."
    ):
        raise ValueError("storyboard path must be project relative")
    return value


class StoryboardCellSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    shot_id: Identifier
    cell_index: Annotated[int, Field(strict=True, ge=0)]
    path: str
    sha256: Digest
    width: PositiveInt
    height: PositiveInt
    crop_box: tuple[int, int, int, int]
    scale_size: tuple[PositiveInt, PositiveInt]

    _validate_path = field_validator("path")(_relative_path)

    @model_validator(mode="after")
    def _valid_crop(self) -> Self:
        left, top, right, bottom = self.crop_box
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ValueError("invalid storyboard crop box")
        return self


class StoryboardSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    episode: PositiveInt
    group_id: Identifier
    batch_id: Identifier
    asset_id: Identifier
    generation_id: Identifier
    grid_path: str
    grid_sha256: Digest
    rows: PositiveInt
    columns: PositiveInt
    splitter_version: Identifier
    cells: tuple[StoryboardCellSource, ...] = Field(min_length=1)

    _validate_path = field_validator("grid_path")(_relative_path)

    @property
    def source_id(self) -> str:
        return source_identity(grid_sha256=self.grid_sha256, generation_id=self.generation_id)

    @model_validator(mode="after")
    def _valid_mapping(self) -> Self:
        if len({cell.shot_id for cell in self.cells}) != len(self.cells):
            raise ValueError("duplicate storyboard shot")
        if len({cell.cell_index for cell in self.cells}) != len(self.cells):
            raise ValueError("duplicate storyboard cell")
        if any(cell.cell_index >= self.rows * self.columns for cell in self.cells):
            raise ValueError("storyboard cell outside layout")
        return self

    def validate_files(self, media_root: Path, *, project_id: str) -> None:
        """Check scope, bytes and geometry before a separate atomic freeze.

        This is not a replacement for enqueue-time freezing: consumers must
        hash the bytes they freeze as well, since files can change after return.
        """
        if self.project_id != project_id:
            raise ValueError("storyboard project mismatch")
        grid_size = _verify_image(media_root, self.grid_path, self.grid_sha256)
        for cell in self.cells:
            size = _verify_image(media_root, cell.path, cell.sha256)
            if size != (cell.width, cell.height) or size != cell.scale_size:
                raise ValueError("storyboard cell dimensions mismatch")
            if cell.crop_box[2] > grid_size[0] or cell.crop_box[3] > grid_size[1]:
                raise ValueError("storyboard crop outside grid dimensions")


class StoryboardSplitInput(BaseModel):
    """Paid grid identity and exact splitting inputs, retained even on split failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: Identifier
    episode: PositiveInt
    group_id: Identifier
    batch_id: Identifier
    generation_id: Identifier
    grid_path: str
    grid_sha256: Digest
    rows: PositiveInt
    columns: PositiveInt
    shot_ids: tuple[Identifier, ...] = Field(min_length=1)
    aspect_ratio: Literal["9:16", "16:9"]
    model: str = ""
    image_size: Literal["1K", "2K", "4K"] = "1K"
    _validate_path = field_validator("grid_path")(_relative_path)

    def restore(self, media_root: Path, *, project_id: str, episode: int, group_id: str) -> dict:
        if (self.project_id, self.episode, self.group_id) != (project_id, episode, group_id):
            raise ValueError("storyboard split scope mismatch")
        _verify_image(media_root, self.grid_path, self.grid_sha256)
        return {
            "grid_asset": str(resolve_under_root(media_root, self.grid_path)),
            "generation_id": self.generation_id, "batch_id": self.batch_id,
            "expected_grid_sha256": self.grid_sha256,
            "layout": {"rows": self.rows, "columns": self.columns, "capacity": self.rows * self.columns},
            "beat_ids": list(self.shot_ids),
            "cell_to_beat": [{"cell": index, "beat_id": shot} for index, shot in enumerate(self.shot_ids)],
            "aspect_ratio": self.aspect_ratio, "model": self.model, "image_size": self.image_size,
        }


def capture_storyboard_split_input(grid_asset: str, payload: dict, media_root: Path) -> StoryboardSplitInput:
    root = Path(media_root).resolve()
    path = resolve_under_root(root, grid_asset)
    mapping = payload["cell_to_beat"]
    if any(item["cell"] != index for index, item in enumerate(mapping)):
        raise ValueError("versioned storyboard requires ordered cell mapping")
    return StoryboardSplitInput(
        project_id=payload["project_id"], episode=payload["episode"], group_id=payload["group_id"],
        batch_id=payload.get("batch_id") or payload["group_id"], generation_id=payload["generation_id"],
        grid_path=path.relative_to(root).as_posix(), grid_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rows=payload["layout"]["rows"], columns=payload["layout"]["columns"],
        shot_ids=tuple(item["beat_id"] for item in mapping), aspect_ratio=payload.get("aspect_ratio", "9:16"),
        model=payload.get("model", ""), image_size=payload.get("image_size", "1K"),
    )


def _verify_image(root: Path, relative: str, digest: str) -> tuple[int, int]:
    path = resolve_under_root(root, relative)
    try:
        content = path.read_bytes()
    except OSError:
        raise ValueError("storyboard image missing or unreadable") from None
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("storyboard image digest mismatch")
    try:
        with Image.open(io.BytesIO(content)) as image:
            size = image.size
            image.verify()
    except (OSError, ValueError):
        raise ValueError("invalid storyboard image") from None
    return size


def split_storyboard_source(
    *, media_root: Path, grid_path: str, project_id: str, episode: int,
    group_id: str, batch_id: str, asset_id: str, generation_id: str,
    rows: int, columns: int, shot_ids: tuple[str, ...], target_aspect: str,
    target_cell_size: tuple[int, int] | None = None,
    expected_grid_sha256: str | None = None,
) -> StoryboardSource:
    """Publish an independent split atomically; never overwrite a prior split.

    The original grid is frozen alongside the cells. Repeating a generation
    with different splitting parameters is an error, not a new interpretation
    of an existing source ID. No shared beat projection is written here.
    """
    from novelvideo.narrative_groups.grid_cleanup import split_and_cleanup

    layout = {(1, 1): "single", (1, 2): "diptych", (1, 3): "triptych",
              (2, 2): "grid_2x2"}.get((rows, columns))
    if layout is None or not shot_ids or len(shot_ids) > rows * columns:
        raise ValueError("unsupported storyboard layout or shot count")
    if len(set(shot_ids)) != len(shot_ids):
        raise ValueError("duplicate storyboard shot")
    root = Path(media_root).resolve()
    content = resolve_under_root(root, _relative_path(grid_path)).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected_grid_sha256 is not None and digest != expected_grid_sha256:
        raise ValueError("storyboard image digest mismatch")
    source_id = source_identity(grid_sha256=digest, generation_id=generation_id)
    relative_dir = f"frames/ep{episode:03d}/storyboard_versions/{source_id}"
    destination = resolve_under_root(root, relative_dir)
    parameters = json.dumps({
        "project_id": project_id, "episode": episode, "group_id": group_id,
        "batch_id": batch_id, "asset_id": asset_id, "generation_id": generation_id,
        "rows": rows, "columns": columns, "shot_ids": shot_ids,
        "target_aspect": target_aspect, "target_cell_size": target_cell_size,
        "splitter_version": "grid-cleanup/v1",
    }, sort_keys=True, separators=(",", ":"))

    def reuse() -> StoryboardSource:
        params_path = resolve_under_root(root, destination / "parameters.json")
        manifest_path = resolve_under_root(root, destination / "source.json")
        if params_path.read_text() != parameters:
            raise ValueError("storyboard split parameters changed for existing source")
        source = StoryboardSource.model_validate_json(manifest_path.read_text())
        if source.source_id != source_id:
            raise ValueError("storyboard source identity mismatch")
        source.validate_files(root, project_id=project_id)
        return source

    if destination.exists():
        return reuse()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".split-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "assets"
        staging.mkdir()
        frozen_grid = staging / "grid.png"
        frozen_grid.write_bytes(content)
        raw_paths, reports = split_and_cleanup(
            frozen_grid, expected_layout=layout, target_aspect=target_aspect,
            output_dir=staging, target_cell_size=target_cell_size,
        )
        cells = []
        for index, shot_id in enumerate(shot_ids):
            path = raw_paths[index]
            with Image.open(path) as image:
                cell_width, cell_height = image.size
            cells.append(StoryboardCellSource(
                shot_id=shot_id, cell_index=index, path=f"{relative_dir}/{path.name}",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                width=cell_width, height=cell_height,
                crop_box=tuple(reports[index]["source_crop_box"]),
                scale_size=(cell_width, cell_height),
            ))
        source = StoryboardSource(
            project_id=project_id, episode=episode, group_id=group_id, batch_id=batch_id,
            asset_id=asset_id, generation_id=generation_id,
            grid_path=f"{relative_dir}/grid.png", grid_sha256=digest,
            rows=rows, columns=columns, splitter_version="grid-cleanup/v1", cells=tuple(cells),
        )
        for report in reports:
            report["path"] = f"{relative_dir}/{Path(str(report['path'])).name}"
        (staging / "cleanup.json").write_text(json.dumps(reports, sort_keys=True))
        (staging / "parameters.json").write_text(parameters)
        (staging / "source.json").write_text(source.model_dump_json())
        try:
            staging.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            # Another identical split may have won the publish race. Never
            # replace that directory, and validate its content before reuse.
        return reuse()
