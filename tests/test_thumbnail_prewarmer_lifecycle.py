from __future__ import annotations

import os
import threading
from datetime import timedelta
from pathlib import Path

from PIL import Image

from novelvideo.freezone import history
from novelvideo.utils import thumbnails


def _image(path: Path, color: str = "#5b8def") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 800), color).save(path)


def test_pending_status_survives_other_process_until_ttl_then_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "result.png"
    _image(source)
    destination = thumbnails.thumbnail_path(tmp_path, source, (320, 320))
    assert destination is not None
    monkeypatch.setattr(thumbnails, "prewarm", lambda *_args: destination)
    requested_at = history._utc_now()
    other_prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=1)
    assert other_prewarmer.is_pending(destination) is False
    other_prewarmer.shutdown()
    monkeypatch.setattr(history, "_utc_now", lambda: requested_at)

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
    assert written["result"]["thumbnail_status"] == "pending"
    assert written["result"]["thumbnail_requested_at"].endswith("Z")

    records = history.read_generation_history(
        project_dir=tmp_path, canvas_id="default", node_id="node-1"
    )
    assert records[0]["result"]["thumbnail_status"] == "pending"

    monkeypatch.setattr(
        history,
        "_utc_now",
        lambda: requested_at
        + timedelta(seconds=history.THUMBNAIL_PENDING_TTL_SECONDS + 1),
    )
    expired = history.read_generation_history(
        project_dir=tmp_path, canvas_id="default", node_id="node-1"
    )
    assert expired[0]["result"]["thumbnail_status"] == "failed"


def test_worker_migrates_changed_source_to_latest_fixed_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    source = project / "source.png"
    _image(source, "red")
    rendered: list[Path] = []

    def renderer(
        _project: Path,
        _source: Path,
        _size: tuple[int, int],
        destination: Path,
    ) -> Path:
        rendered.append(destination)
        return destination

    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=2, renderer=renderer)
    monkeypatch.setattr(prewarmer, "_ensure_worker", lambda: None)
    original_destination = prewarmer.prewarm(project, source, (320, 320))
    assert original_destination is not None

    before = source.stat().st_mtime_ns
    _image(source, "blue")
    os.utime(source, ns=(before + 1_000_000, before + 1_000_000))
    latest_destination = thumbnails.thumbnail_path(project, source, (320, 320))
    assert latest_destination is not None
    assert latest_destination != original_destination

    job = prewarmer._queue.get_nowait()
    prewarmer._process_job(job)
    prewarmer._queue.task_done()

    assert rendered == [latest_destination]
    assert prewarmer.pending_count == 0


def test_inflight_duplicate_is_not_enqueued_twice(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = project / "source.png"
    _image(source)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def renderer(
        _project: Path,
        _source: Path,
        _size: tuple[int, int],
        destination: Path,
    ) -> Path:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=5)
        return destination

    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=2, renderer=renderer)
    first = prewarmer.prewarm(project, source, (320, 320))
    assert first is not None
    assert started.wait(timeout=5)
    assert prewarmer.prewarm(project, source, (320, 320)) == first
    release.set()
    prewarmer._queue.join()
    prewarmer.shutdown()

    assert calls == 1


def test_worker_survives_base_exception_and_shutdown_is_explicit(tmp_path: Path) -> None:
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
    ) -> Path:
        calls.append(source)
        if source == source_a:
            raise KeyboardInterrupt("unexpected worker exit")
        return destination

    prewarmer = thumbnails.ThumbnailPrewarmer(queue_size=4, renderer=renderer)
    assert prewarmer.prewarm(project, source_a, (320, 320)) is not None
    assert prewarmer.prewarm(project, source_b, (320, 320)) is not None
    prewarmer._queue.join()
    prewarmer.shutdown()

    assert calls == [source_a, source_b]
    assert prewarmer.pending_count == 0
    assert prewarmer.worker_alive is False
    assert prewarmer.prewarm(project, source_a, (320, 320)) is None
