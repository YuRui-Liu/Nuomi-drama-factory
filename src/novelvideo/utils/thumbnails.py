"""Project-local, versioned thumbnail cache for generated images.

Thumbnail creation is intentionally separated from history reads: producers
may queue work after an image is written, while GET paths only read the URL
already persisted in the history record.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger("novelvideo.thumbnails")

DEFAULT_SIZE = (320, 320)
THUMBNAIL_ROOT = "_thumbnails"
_SUPPORTED_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
)
_QUEUE_SIZE = 256
_Renderer = Callable[[Path, Path, tuple[int, int], Path], Path | None]
_stripes = tuple(threading.Lock() for _ in range(32))


class _Job(NamedTuple):
    project_dir: Path
    source: Path
    size: tuple[int, int]
    fingerprint: str
    destination: Path
    key: str


def _normalized_size(size: tuple[int, int]) -> tuple[int, int] | None:
    try:
        width, height = int(size[0]), int(size[1])
    except (IndexError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _project_relative(project_dir: Path, source: Path) -> Path | None:
    """Resolve ``source`` and reject symlink/path traversal outside a project."""

    try:
        return source.resolve().relative_to(project_dir.resolve())
    except (OSError, RuntimeError, ValueError):
        return None


def _thumbnail_target(
    project_dir: Path,
    source: Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> tuple[str, Path] | None:
    dimensions = _normalized_size(size)
    relative = _project_relative(project_dir, source)
    if dimensions is None or relative is None:
        return None
    if (
        relative.parts
        and relative.parts[0] == "freezone"
        and THUMBNAIL_ROOT in relative.parts
    ):
        return None
    try:
        stat = source.stat()
    except OSError:
        return None
    width, height = dimensions
    key = "\0".join(
        (
            relative.as_posix(),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            f"{width}x{height}",
        )
    )
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    destination = (
        project_dir
        / "freezone"
        / THUMBNAIL_ROOT
        / f"{width}x{height}"
        / f"{fingerprint[:32]}.webp"
    )
    return fingerprint, destination


def thumbnail_path(
    project_dir: Path,
    source: Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> Path | None:
    """Return the cache path for one exact source version and pixel budget."""

    target = _thumbnail_target(project_dir, source, size)
    return target[1] if target is not None else None


def _ensure_thumbnail_at(
    project_dir: Path,
    source: Path,
    size: tuple[int, int],
    destination: Path,
) -> Path | None:
    """Render to the destination fixed by the queued source fingerprint."""

    dimensions = _normalized_size(size)
    relative_destination = _project_relative(project_dir, destination)
    if (
        source.suffix.lower() not in _SUPPORTED_SUFFIXES
        or dimensions is None
        or relative_destination is None
        or relative_destination.parts[:2] != ("freezone", THUMBNAIL_ROOT)
    ):
        return None
    if destination.is_file():
        return destination

    lock = _stripes[hash(str(destination)) % len(_stripes)]
    with lock:
        if destination.is_file():
            return destination
        try:
            from PIL import Image, ImageOps

            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened)
                try:
                    image.thumbnail(dimensions, Image.Resampling.LANCZOS)
                    has_alpha = image.mode in {"RGBA", "LA"} or (
                        image.mode == "P" and "transparency" in image.info
                    )
                    output = image.convert("RGBA" if has_alpha else "RGB")
                finally:
                    if image is not opened:
                        image.close()

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f"{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                output.save(temporary, "WEBP", quality=82, method=4)
                os.replace(temporary, destination)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            finally:
                output.close()
            return destination
        except Exception:
            logger.debug("thumbnail generation skipped for %s", source, exc_info=True)
            return None


def ensure_thumbnail(
    project_dir: Path,
    source: Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> Path | None:
    """Build a Pillow thumbnail, publishing it with an atomic replace."""

    destination = thumbnail_path(project_dir, source, size)
    if destination is None:
        return None
    return _ensure_thumbnail_at(project_dir, source, size, destination)


class ThumbnailPrewarmer:
    """Bounded single-worker thumbnail queue with destination-key deduplication."""

    def __init__(
        self,
        *,
        queue_size: int = _QUEUE_SIZE,
        renderer: _Renderer | None = None,
    ) -> None:
        self._queue: queue.Queue[_Job | object] = queue.Queue(
            maxsize=max(1, queue_size)
        )
        self._renderer = renderer or _ensure_thumbnail_at
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._stop = object()

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def is_pending(self, destination: Path) -> bool:
        with self._pending_lock:
            return str(destination) in self._pending

    def _discard(self, key: str) -> None:
        with self._pending_lock:
            self._pending.discard(key)

    def _process_job(self, job: _Job) -> None:
        active_key = job.key
        try:
            latest = _thumbnail_target(job.project_dir, job.source, job.size)
            if latest is None:
                return
            fingerprint, destination = latest
            if fingerprint != job.fingerprint or destination != job.destination:
                latest_key = str(destination)
                with self._pending_lock:
                    self._pending.discard(job.key)
                    if latest_key in self._pending:
                        return
                    self._pending.add(latest_key)
                active_key = latest_key
                job = _Job(
                    job.project_dir,
                    job.source,
                    job.size,
                    fingerprint,
                    destination,
                    latest_key,
                )
            self._renderer(
                job.project_dir,
                job.source,
                job.size,
                job.destination,
            )
        except BaseException:
            logger.debug(
                "thumbnail prewarm failed for %s", job.source, exc_info=True
            )
        finally:
            self._discard(active_key)

    def _worker(self) -> None:
        while True:
            queued = self._queue.get()
            try:
                if queued is self._stop:
                    return
                self._process_job(queued)
            finally:
                self._queue.task_done()

    def _ensure_worker(self) -> None:
        if self._closed:
            return
        with self._worker_lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(
                target=self._worker,
                name="thumbnail-prewarm",
                daemon=True,
            )
            self._thread.start()

    @property
    def worker_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop this prewarmer explicitly; queued work is completed first."""

        with self._worker_lock:
            if self._closed:
                thread = self._thread
            else:
                self._closed = True
                thread = self._thread
                if thread is not None and thread.is_alive():
                    self._queue.put(self._stop)
        if wait and thread is not None:
            thread.join()

    def prewarm(
        self,
        project_dir: Path,
        source: Path,
        size: tuple[int, int] = DEFAULT_SIZE,
    ) -> Path | None:
        if self._closed:
            return None
        target = _thumbnail_target(project_dir, source, size)
        if target is None or source.suffix.lower() not in _SUPPORTED_SUFFIXES:
            return None
        fingerprint, destination = target
        if destination.is_file():
            return destination
        try:
            self._ensure_worker()
        except Exception:
            logger.debug("thumbnail worker unavailable", exc_info=True)
            return None
        key = str(destination)
        with self._pending_lock:
            if self._closed:
                return None
            if key in self._pending:
                return destination
            self._pending.add(key)
            try:
                self._queue.put_nowait(
                    _Job(project_dir, source, size, fingerprint, destination, key)
                )
            except queue.Full:
                self._pending.discard(key)
                logger.debug("thumbnail prewarm queue full; skipped %s", source)
                return None
        return destination


_prewarmer = ThumbnailPrewarmer()


def prewarm(
    project_dir: Path,
    source: Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> Path | None:
    return _prewarmer.prewarm(project_dir, source, size)


def is_prewarm_pending(destination: Path) -> bool:
    return _prewarmer.is_pending(destination)
