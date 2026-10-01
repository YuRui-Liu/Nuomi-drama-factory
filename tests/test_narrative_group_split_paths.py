"""Split-cell paths must be group-scoped, and their media URLs versioned.

Regression: the legacy split promoted every group's cells to the episode-level
``frames/epNNN/beat_NN.png`` names, so group-01 and group-02 overwrote each
other and ``cell_assets`` referenced another group's frames. Those stable paths
also carried no cache-busting parameter, so the browser kept rendering the
previous bytes while the (revision-unique) grid image stayed fresh.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from novelvideo.api.routes import narrative_groups
from novelvideo.task_backend.runners import narrative_group


def _grid(path: Path, left: tuple[int, int, int], right: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1280, 360), left)
    image.paste(Image.new("RGB", (640, 360), right), (640, 0))
    image.save(path)
    return path


def _payload(
    tmp_path: Path, group_id: str, revision: int, *, beat_numbers: tuple[int, ...] = ()
) -> dict:
    """Narrative-group render payload.

    Real group payloads carry no episode beat numbers (their ``beats`` list is
    keyed by source lines while the cells map to shots), which is exactly why
    the legacy promote used to fall back to the group-local index.
    """
    return {
        "episode": 1,
        "stage": "render",
        "revision": revision,
        "group_id": group_id,
        "project_id": "p1",
        "output_dir": str(tmp_path),
        "layout": {"rows": 1, "columns": 2},
        "aspect_ratio": "16:9",
        "model": "",
        "image_size": "",
        "beats": [{"beat_number": number} for number in beat_numbers],
        "cell_to_beat": [
            {"cell": 0, "beat_id": "shot-01-01"},
            {"cell": 1, "beat_id": "shot-01-02"},
        ],
    }


def _split(
    tmp_path: Path,
    group_id: str,
    revision: int,
    colour,
    *,
    beat_numbers: tuple[int, ...] = (),
):
    grid = _grid(tmp_path / "grids" / "ep001" / f"{group_id}.png", *colour)
    ctx = SimpleNamespace(output_dir=str(tmp_path), project_id="p1")
    return narrative_group._split_existing_grid(
        str(grid), _payload(tmp_path, group_id, revision, beat_numbers=beat_numbers), ctx
    )


def test_narrative_group_split_never_writes_shared_beat_frames(tmp_path: Path) -> None:
    """Groups reference their own cells; the legacy beat slot stays untouched."""
    first = _split(tmp_path, "group-01", 2, ((10, 10, 10), (200, 30, 30)))
    second = _split(tmp_path, "group-02", 3, ((10, 10, 10), (30, 30, 200)))

    paths = [Path(cell["path"]) for cell in first["cell_assets"]]
    other = [Path(cell["path"]) for cell in second["cell_assets"]]
    first_slug = narrative_group._safe_path_slug("group-01", prefix="group")
    second_slug = narrative_group._safe_path_slug("group-02", prefix="group")

    assert len(paths) == 2
    for index, path in enumerate(paths):
        assert path.is_file()
        assert path.name == f"{first_slug}_r2_cell_{index:02d}.png"
    assert {path.name for path in paths}.isdisjoint({path.name for path in other})
    for index, path in enumerate(other):
        assert path.name == f"{second_slug}_r3_cell_{index:02d}.png"

    # No group may squat on frames/epNNN/beat_NN.png: that slot means "the frame
    # of episode beat NN" and is read by the manual-shot flow, so writing the
    # group-local index there handed those flows another group's image.
    frames = tmp_path / "frames" / "ep001"
    assert not (frames / "beat_01.png").exists()
    assert not (frames / "beat_02.png").exists()


def test_split_promotes_legacy_beat_frames_only_with_episode_numbers(
    tmp_path: Path,
) -> None:
    result = _split(
        tmp_path,
        "group-01",
        2,
        ((10, 10, 10), (200, 30, 30)),
        beat_numbers=(3, 4),
    )

    frames = tmp_path / "frames" / "ep001"
    assert (frames / "beat_03.png").is_file()
    assert (frames / "beat_04.png").is_file()
    # Cells stay group- and revision-scoped even when the legacy copy is written.
    slug = narrative_group._safe_path_slug("group-01", prefix="group")
    assert [Path(cell["path"]).name for cell in result["cell_assets"]] == [
        f"{slug}_r2_cell_00.png",
        f"{slug}_r2_cell_01.png",
    ]


def test_cell_asset_url_is_versioned_by_mtime(tmp_path: Path) -> None:
    cell = tmp_path / "frames" / "ep001" / "group-01_r2_cell_00.png"
    cell.parent.mkdir(parents=True, exist_ok=True)
    cell.write_bytes(b"cell")

    url = narrative_groups._asset_url("p1", tmp_path, str(cell))
    assert url.endswith(f"?v={cell.stat().st_mtime_ns}")

    # Unversioned when the file is gone, empty when it escapes the project root.
    missing = narrative_groups._asset_url("p1", tmp_path, str(tmp_path / "nope.png"))
    assert missing and "?v=" not in missing
    outside = narrative_groups._asset_url("p1", tmp_path, "/etc/hosts")
    assert outside == ""
