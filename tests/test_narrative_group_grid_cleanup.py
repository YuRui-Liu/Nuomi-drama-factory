from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from novelvideo.narrative_groups.grid_cleanup import cleanup_grid_cells


def _save(path: Path, pixels: np.ndarray) -> Path:
    Image.fromarray(pixels.astype(np.uint8)).save(path)
    return path


def test_removes_plain_white_border_and_reports_the_cleanup(tmp_path: Path) -> None:
    pixels = np.full((100, 100, 3), 255, dtype=np.uint8)
    pixels[3:97, 3:97] = [40, 120, 200]
    path = _save(tmp_path / "bordered.png", pixels)

    reports, output_size = cleanup_grid_cells([path], "1:1")

    report = reports[0]
    assert output_size == "94x94"
    assert report["trim"] == {"left": 3, "top": 3, "right": 3, "bottom": 3}
    assert report["adaptive"] is True
    assert isinstance(report["enhanced"], bool)
    assert report["source_size"] == [100, 100]
    assert report["output_size"] == [94, 94]
    assert report["warnings"] == []
    with Image.open(path) as cleaned:
        assert cleaned.size == (94, 94)
        assert np.asarray(cleaned)[0, 0].tolist() == [40, 120, 200]


def test_does_not_adaptively_trim_a_textured_edge(tmp_path: Path) -> None:
    y, x = np.indices((100, 100))
    checker = ((x + y) % 2 * 255).astype(np.uint8)
    pixels = np.stack((checker, 255 - checker, checker), axis=-1)
    path = _save(tmp_path / "textured.png", pixels)

    reports, _ = cleanup_grid_cells([path], "1:1")

    assert reports[0]["trim"] == {
        "left": 2,
        "top": 2,
        "right": 2,
        "bottom": 2,
    }
    assert reports[0]["adaptive"] is False


def test_adaptive_trim_is_capped_at_three_percent_per_edge(tmp_path: Path) -> None:
    pixels = np.full((100, 100, 3), 128, dtype=np.uint8)
    pixels[10:90, 10:90] = [220, 40, 80]
    path = _save(tmp_path / "wide-border.png", pixels)

    reports, _ = cleanup_grid_cells([path], "1:1", fixed_inset_px=1)

    trim = reports[0]["trim"]
    assert trim == {"left": 4, "top": 4, "right": 4, "bottom": 4}
    assert all(value - 1 <= 3 for value in trim.values())


def test_center_cover_crop_keeps_geometry_and_unifies_9_16_outputs(
    tmp_path: Path,
) -> None:
    paths: list[Path] = []
    for index, size in enumerate((100, 160)):
        pixels = np.empty((size, size, 3), dtype=np.uint8)
        pixels[:, : size // 3] = [230, 20, 20]
        pixels[:, size // 3 : 2 * size // 3] = [20, 230, 20]
        pixels[:, 2 * size // 3 :] = [20, 20, 230]
        paths.append(_save(tmp_path / f"cell-{index}.png", pixels))

    reports, output_size = cleanup_grid_cells(paths, "9:16")

    assert output_size == "54x96"
    assert [report["output_size"] for report in reports] == [[54, 96], [54, 96]]
    for path in paths:
        with Image.open(path) as cleaned:
            assert cleaned.size == (54, 96)
            assert cleaned.width * 16 == cleaned.height * 9
            center = np.asarray(cleaned)[cleaned.height // 2, cleaned.width // 2]
            assert center[1] > 200
            assert center[0] < 40
            assert center[2] < 40
