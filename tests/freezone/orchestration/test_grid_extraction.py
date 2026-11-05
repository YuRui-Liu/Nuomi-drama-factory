from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


PREFIX = "/api/v1/projects/p/freezone/orchestration"
CELL_COLORS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone_orchestration as routes

    async def resolve(project, user, required_role="editor"):
        if project != "p":
            raise HTTPException(403, "project denied")
        return (
            SimpleNamespace(project_id="p", state_dir=tmp_path),
            "u",
            "test",
            tmp_path,
            str(tmp_path),
        )

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u", "username": "u"}
    test_client = TestClient(app)
    created = test_client.post(
        PREFIX + "/canvases", json={"canvas_id": "grid", "name": "grid"}
    )
    assert created.status_code == 200, created.text
    return test_client


def _write_six_cell_vertical_grid(path):
    cell_width, cell_height, separator = 7, 5, 2
    image = Image.new(
        "RGB", (cell_width, cell_height * 6 + separator * 5), (11, 11, 11)
    )
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(CELL_COLORS):
        top = index * (cell_height + separator)
        draw.rectangle((0, top, cell_width - 1, top + cell_height - 1), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _payload(source, *, separator_px=2):
    return {
        "source_url": "/static/projects/p/freezone/_outputs/storyboard/grid.png",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "rows": 6,
        "columns": 1,
        "cell_ids": [
            f"S{number:02d}-{edge}" for number in (7, 8, 9) for edge in ("start", "end")
        ],
        "separator_px": separator_px,
    }


def test_public_split_grid_crops_exact_geometry_and_reuses_outputs(client, tmp_path):
    source = tmp_path / "freezone/_outputs/storyboard/grid.png"
    _write_six_cell_vertical_grid(source)
    payload = _payload(source)

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source_sha256"] == payload["source_sha256"]
    assert data["rows"] == 6
    assert data["columns"] == 1
    assert [cell["cell_id"] for cell in data["cells"]] == payload["cell_ids"]
    assert [cell["crop_box"] for cell in data["cells"]] == [
        {"x": 0, "y": index * 7, "width": 7, "height": 5} for index in range(6)
    ]
    assert all((tmp_path / cell["path"]).is_file() for cell in data["cells"])
    for cell, color in zip(data["cells"], CELL_COLORS, strict=True):
        output = tmp_path / cell["path"]
        assert cell["width"] == 7
        assert cell["height"] == 5
        assert cell["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        with Image.open(output) as cropped:
            assert cropped.size == (7, 5)
            assert set(cropped.getdata()) == {color}

    retry = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"] == data


def test_public_split_grid_accepts_explicit_mixed_cell_regions(client, tmp_path):
    source = tmp_path / "freezone/_outputs/storyboard/mixed.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (12, 32), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    regions = [
        {"x": 0, "y": 0, "width": 12, "height": 8},
        {"x": 0, "y": 8, "width": 12, "height": 8},
        {"x": 0, "y": 16, "width": 6, "height": 8},
        {"x": 6, "y": 16, "width": 6, "height": 8},
        {"x": 0, "y": 24, "width": 6, "height": 8},
        {"x": 6, "y": 24, "width": 6, "height": 8},
    ]
    for index, region in enumerate(regions):
        x, y = region["x"], region["y"]
        draw.rectangle(
            (x, y, x + region["width"] - 1, y + region["height"] - 1),
            fill=CELL_COLORS[index],
        )
    image.save(source, format="PNG")
    payload = {
        "source_url": "/static/projects/p/freezone/_outputs/storyboard/mixed.png",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "cell_ids": [
            "s01-start",
            "s01-end",
            "s02-start",
            "s03-start",
            "s02-end",
            "s03-end",
        ],
        "regions": regions,
    }

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["layout_kind"] == "explicit_regions"
    assert data["rows"] is None
    assert data["columns"] is None
    assert [cell["crop_box"] for cell in data["cells"]] == regions
    for cell, color, region in zip(data["cells"], CELL_COLORS, regions, strict=True):
        with Image.open(tmp_path / cell["path"]) as cropped:
            assert cropped.size == (region["width"], region["height"])
            assert set(cropped.getdata()) == {color}


@pytest.mark.parametrize(
    "regions",
    [
        [
            {"x": 0, "y": 0, "width": 4, "height": 8},
            {"x": 3, "y": 0, "width": 4, "height": 8},
        ],
        [{"x": 0, "y": 0, "width": 8, "height": 40}],
    ],
)
def test_public_split_grid_rejects_invalid_explicit_regions(client, tmp_path, regions):
    source = tmp_path / "freezone/_outputs/storyboard/grid.png"
    _write_six_cell_vertical_grid(source)
    payload = {
        "source_url": "/static/projects/p/freezone/_outputs/storyboard/grid.png",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "cell_ids": [f"cell-{index}" for index in range(len(regions))],
        "regions": regions,
    }

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 422, response.text
    assert not (tmp_path / "freezone/_outputs/grid_cells").exists()


def test_public_split_grid_rejects_source_hash_mismatch(client, tmp_path):
    source = tmp_path / "freezone/_outputs/storyboard/grid.png"
    _write_six_cell_vertical_grid(source)
    payload = _payload(source)
    payload["source_sha256"] = "0" * 64

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 409, response.text
    assert "source" in response.text.lower()
    assert not (tmp_path / "freezone/_outputs/grid_cells").exists()


def test_public_split_grid_decodes_the_same_bytes_it_hashes(
    client, monkeypatch, tmp_path
):
    from novelvideo.freezone.orchestration import grid_extraction

    source = tmp_path / "freezone/_outputs/storyboard/grid.png"
    _write_six_cell_vertical_grid(source)
    payload = _payload(source)
    alternate = Image.new("RGB", (7, 40), (12, 34, 56))
    from io import BytesIO

    alternate_buffer = BytesIO()
    alternate.save(alternate_buffer, format="PNG")
    original_open = Image.open
    changed = False

    def replace_source_after_read(source_handle, *args, **kwargs):
        nonlocal changed
        if not changed:
            source.write_bytes(alternate_buffer.getvalue())
            changed = True
        return original_open(source_handle, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(grid_extraction.Image, "open", replace_source_after_read)
        response = client.post(
            PREFIX + "/canvases/grid/images/split-grid", json=payload
        )

    assert response.status_code == 200, response.text
    first_cell = response.json()["data"]["cells"][0]
    with Image.open(tmp_path / first_cell["path"]) as cropped:
        assert set(cropped.getdata()) == {CELL_COLORS[0]}


@pytest.mark.parametrize(
    "changes",
    [
        {"cell_ids": ["duplicate"] * 6},
        {"cell_ids": ["../escape"] * 6},
        {"columns": 2},
        {"separator_px": -1},
    ],
)
def test_public_split_grid_rejects_invalid_layout_before_writing(
    client, tmp_path, changes
):
    source = tmp_path / "freezone/_outputs/storyboard/grid.png"
    _write_six_cell_vertical_grid(source)
    payload = _payload(source)
    payload.update(changes)

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 422, response.text
    assert not (tmp_path / "freezone/_outputs/grid_cells").exists()


def test_public_split_grid_rejects_symlinked_source(client, tmp_path):
    outside = tmp_path.parent / "outside.png"
    _write_six_cell_vertical_grid(outside)
    source = tmp_path / "freezone/_outputs/storyboard/linked.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(outside)
    payload = _payload(outside)
    payload["source_url"] = "/static/projects/p/freezone/_outputs/storyboard/linked.png"

    response = client.post(PREFIX + "/canvases/grid/images/split-grid", json=payload)

    assert response.status_code == 422, response.text
    assert "symlink" in response.text.lower()
