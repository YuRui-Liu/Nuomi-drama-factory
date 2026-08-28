from __future__ import annotations

import json
import os
import unittest.mock
from pathlib import Path
import pytest

from PIL import Image

from novelvideo.freezone import history
from novelvideo.freezone.canvas_static_urls import migrate_canvas_static_urls_in_memory
from novelvideo.utils import thumbnails


def _image(path: Path, size: tuple[int, int] = (1200, 800)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "#5b8def").save(path)


def test_thumbnail_cache_is_project_scoped_and_keyed_by_source_version_and_size(
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    source_a = project_a / "freezone" / "_outputs" / "image" / "same.png"
    source_b = project_b / "freezone" / "_outputs" / "image" / "same.png"
    _image(source_a)
    _image(source_b)

    first = thumbnails.thumbnail_path(project_a, source_a, (320, 240))
    other_project = thumbnails.thumbnail_path(project_b, source_b, (320, 240))
    other_size = thumbnails.thumbnail_path(project_a, source_a, (640, 480))

    assert first != other_project
    assert first != other_size
    before = source_a.stat().st_mtime_ns
    os.utime(source_a, ns=(before + 1_000_000, before + 1_000_000))
    assert thumbnails.thumbnail_path(project_a, source_a, (320, 240)) != first


def test_thumbnail_path_rejects_sources_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside.png"
    _image(outside)

    assert thumbnails.thumbnail_path(project, outside, (320, 240)) is None


def test_ensure_thumbnail_downscales_with_pillow_and_replaces_atomically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source = project / "freezone" / "_outputs" / "image" / "large.png"
    _image(source, (1200, 800))
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        replace_calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(thumbnails.os, "replace", recording_replace)
    result = thumbnails.ensure_thumbnail(project, source, (320, 240))

    assert result is not None and result.exists()
    with Image.open(result) as image:
        assert image.size == (320, 213)
    assert replace_calls == [(replace_calls[0][0], result)]
    assert replace_calls[0][0].parent == result.parent
    assert replace_calls[0][0] != result
    assert not replace_calls[0][0].exists()


def test_history_append_queues_thumbnail_but_reads_never_decode_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(source)
    queued: list[tuple[Path, Path, tuple[int, int]]] = []

    def fake_prewarm(project_dir: Path, source_path: Path, size: tuple[int, int]):
        queued.append((project_dir, source_path, size))
        return thumbnails.thumbnail_path(project_dir, source_path, size)

    monkeypatch.setattr(thumbnails, "prewarm", fake_prewarm)
    monkeypatch.setattr(thumbnails, "is_prewarm_pending", lambda _path: True)
    written = history.append_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
        record={
            "id": "freezone_image:job-1",
            "status": "completed",
            "media_type": "image",
            "result": {"output_url": str(source)},
        },
    )

    assert queued == [(tmp_path, source, (320, 320))]
    assert written is not None
    assert "thumbnail_url" not in written["result"]
    assert written["result"]["thumbnail_status"] == "pending"

    monkeypatch.setattr(
        thumbnails,
        "ensure_thumbnail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decoded on GET")),
    )
    records = history.read_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
    )
    assert "thumbnail_url" not in records[0]["result"]
    assert records[0]["result"]["thumbnail_status"] == "pending"


def test_history_read_exposes_existing_thumbnail_as_browser_static_url_without_decode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(source)
    monkeypatch.setattr(
        thumbnails,
        "prewarm",
        lambda project_dir, source_path, size: thumbnails.thumbnail_path(
            project_dir, source_path, size
        ),
    )
    history.append_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
        record={
            "id": "freezone_image:job-1",
            "status": "completed",
            "media_type": "image",
            "result": {"output_url": str(source)},
        },
    )
    destination = thumbnails.ensure_thumbnail(tmp_path, source, (320, 320))
    assert destination is not None and destination.exists()

    monkeypatch.setattr(
        thumbnails,
        "ensure_thumbnail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decoded on GET")),
    )
    records = history.read_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
    )
    migrated = migrate_canvas_static_urls_in_memory(
        records[0],
        project_id="project-123",
        owner_username="owner",
        project_name="demo",
        project_dir=tmp_path,
    )
    thumbnail_url = migrated["result"]["thumbnail_url"]
    assert thumbnail_url.startswith(
        "/static/projects/project-123/freezone/_thumbnails/320x320/"
    )
    assert ":" not in thumbnail_url.split("?", 1)[0]
    assert "\\" not in thumbnail_url


def test_history_append_discards_untrusted_preexisting_thumbnail_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(source)
    monkeypatch.setattr(
        thumbnails,
        "prewarm",
        lambda project_dir, source_path, size: thumbnails.thumbnail_path(
            project_dir, source_path, size
        ),
    )

    written = history.append_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
        record={
            "id": "freezone_image:job-1",
            "status": "completed",
            "media_type": "image",
            "result": {
                "output_url": str(source),
                "thumbnail_url": r"C:\private\missing.webp",
            },
        },
    )

    assert written is not None
    assert "thumbnail_url" not in written["result"]
    assert written["result"]["thumbnail_status"] == "pending"
    records = history.read_generation_history(
        project_dir=tmp_path, canvas_id="default", node_id="node-1"
    )
    assert "thumbnail_url" not in records[0]["result"]


@pytest.mark.parametrize(
    ("status", "media_type"),
    [
        ("failed", "image"),
        ("running", "image"),
        ("completed", "video"),
    ],
)
def test_history_discards_derived_thumbnail_fields_for_ineligible_records(
    tmp_path: Path,
    monkeypatch,
    status: str,
    media_type: str,
) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(source)
    prewarm = unittest.mock.Mock()
    monkeypatch.setattr(thumbnails, "prewarm", prewarm)
    written = history.append_generation_history(
        project_dir=tmp_path,
        canvas_id="default",
        node_id="node-1",
        record={
            "id": "freezone_image:job-1",
            "status": status,
            "media_type": media_type,
            "result": {
                "output_url": str(source),
                "thumbnail_url": r"C:\private\missing.webp",
                "thumbnail_status": "ready",
            },
        },
    )

    assert written is not None
    result = written["result"]
    assert "thumbnail_url" not in result
    assert "thumbnail_status" not in result
    prewarm.assert_not_called()
    path = history.generation_history_path(tmp_path, "default", "node-1")
    path.write_text(
        json.dumps(
            {
                "id": "freezone_image:legacy-job",
                "status": status,
                "media_type": media_type,
                "result": {
                    "output_url": str(source),
                    "thumbnail_url": r"C:\private\missing.webp",
                    "thumbnail_status": "ready",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records = history.read_generation_history(
        project_dir=tmp_path, canvas_id="default", node_id="node-1"
    )
    assert "thumbnail_url" not in records[0]["result"]
    assert "thumbnail_status" not in records[0]["result"]
    prewarm.assert_not_called()
