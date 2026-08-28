from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from novelvideo.freezone import history
from novelvideo.utils import thumbnails


def _image(path: Path, color: str = "#5b8def") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 800), color).save(path)


def test_queue_full_append_and_read_report_deferred_without_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    queued_source = tmp_path / "freezone" / "_outputs" / "image" / "queued.png"
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(queued_source, "red")
    _image(source)
    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=1)
    monkeypatch.setattr(prewarmer, "_ensure_worker", lambda: None)
    assert prewarmer.prewarm(tmp_path, queued_source, (320, 320)) is not None
    monkeypatch.setattr(thumbnails, "prewarm", prewarmer.prewarm)

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

    assert written is not None
    assert written["result"]["thumbnail_status"] == "deferred"
    assert "thumbnail_url" not in written["result"]
    records = history.read_generation_history(
        project_dir=tmp_path, canvas_id="default", node_id="node-1"
    )
    assert records[0]["result"]["thumbnail_status"] == "deferred"
    assert "thumbnail_url" not in records[0]["result"]


def test_prewarmer_deduplicates_concurrent_destination_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "project" / "source.png"
    _image(source)
    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=8)
    monkeypatch.setattr(prewarmer, "_ensure_worker", lambda: None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _index: prewarmer.prewarm(
                    tmp_path / "project", source, (320, 320)
                ),
                range(16),
            )
        )

    destination = thumbnails.thumbnail_path(tmp_path / "project", source, (320, 320))
    assert destination is not None
    assert results == [destination] * 16
    assert prewarmer._queue.qsize() == 1
    assert prewarmer.pending_count == 1


def test_prewarmer_rolls_back_dedup_key_when_queue_is_full(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source_a = project / "a.png"
    source_b = project / "b.png"
    _image(source_a, "red")
    _image(source_b, "blue")
    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=1)
    monkeypatch.setattr(prewarmer, "_ensure_worker", lambda: None)

    assert prewarmer.prewarm(project, source_a, (320, 320)) is not None
    assert prewarmer.prewarm(project, source_b, (320, 320)) is None
    first_job = prewarmer._queue.get_nowait()
    prewarmer._queue.task_done()

    assert first_job[1] == source_a
    assert prewarmer.prewarm(project, source_b, (320, 320)) is not None


def test_prewarmer_worker_continues_after_renderer_exception(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_a = project / "a.png"
    source_b = project / "b.png"
    _image(source_a, "red")
    _image(source_b, "blue")
    calls: list[Path] = []

    def renderer(
        _project: Path,
        source: Path,
        _size: tuple[int, int],
        destination: Path,
    ):
        calls.append(source)
        if source == source_a:
            raise RuntimeError("renderer failed")
        return destination

    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=4, renderer=renderer)
    assert prewarmer.prewarm(project, source_a, (320, 320)) is not None
    assert prewarmer.prewarm(project, source_b, (320, 320)) is not None
    prewarmer._queue.join()
    prewarmer.shutdown()

    assert calls == [source_a, source_b]
    assert prewarmer.pending_count == 0


def test_ensure_thumbnail_closes_exif_transpose_intermediate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source = project / "source.png"
    _image(source)
    with Image.open(source) as opened:
        intermediate = opened.copy()
    closed = False
    real_close = intermediate.close

    def close() -> None:
        nonlocal closed
        closed = True
        real_close()

    monkeypatch.setattr(intermediate, "close", close)
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda _opened: intermediate)

    assert thumbnails.ensure_thumbnail(project, source, (320, 320)) is not None
    assert closed is True


def test_ensure_thumbnail_removes_partial_temp_after_save_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source = project / "source.png"
    _image(source)
    destination = thumbnails.thumbnail_path(project, source, (320, 320))
    assert destination is not None

    def failing_save(_image, path, *_args, **_kwargs):
        Path(path).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", failing_save)

    assert thumbnails.ensure_thumbnail(project, source, (320, 320)) is None
    assert not destination.exists()
    assert list(destination.parent.glob(f"{destination.name}.*.tmp")) == []
