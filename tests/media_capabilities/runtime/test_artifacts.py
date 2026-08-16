from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

import pytest

from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore


def test_put_bytes_writes_content_addressed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"artifact payload"
    digest = hashlib.sha256(data).hexdigest()
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", tracking_fsync)

    artifact = ArtifactStore(tmp_path).put_bytes(
        data,
        media_type="image/png",
        extension=".PNG",
        metadata={"width": 1024},
    )

    assert artifact.id == digest
    assert artifact.content_sha256 == digest
    assert artifact.media_type == "image/png"
    assert artifact.metadata == {"width": 1024}
    assert artifact.local_path == f"{digest[:2]}/{digest}.png"
    assert PurePosixPath(artifact.local_path).as_posix() == artifact.local_path
    assert (tmp_path / Path(artifact.local_path)).read_bytes() == data
    assert len(fsync_calls) == 1


def test_put_bytes_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    first = store.put_bytes(b"same", media_type="audio/mpeg", extension="mp3")
    first_path = tmp_path / Path(first.local_path)
    first_stat = first_path.stat()
    second = store.put_bytes(b"same", media_type="audio/mpeg", extension=".mp3")

    assert second == first
    assert first_path.stat().st_mtime_ns == first_stat.st_mtime_ns
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [first_path]


@pytest.mark.parametrize(
    "extension",
    ["", ".", "../png", "png/jpg", r"png\jpg", "p ng", "png.exe"],
)
def test_put_bytes_rejects_unsafe_extension(
    tmp_path: Path,
    extension: str,
) -> None:
    with pytest.raises(ValueError, match="extension"):
        ArtifactStore(tmp_path).put_bytes(
            b"unsafe",
            media_type="application/octet-stream",
            extension=extension,
        )

    assert list(tmp_path.iterdir()) == []


def test_replace_failure_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        ArtifactStore(tmp_path).put_bytes(
            b"cannot publish",
            media_type="video/mp4",
            extension="mp4",
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
