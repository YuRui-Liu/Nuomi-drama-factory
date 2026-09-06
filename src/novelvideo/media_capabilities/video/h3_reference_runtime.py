"""Frozen-input runtime for the RunningHub MiniMax H3 reference workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from PIL import Image

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    VideoGenerationRequest,
)
from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
from novelvideo.media_capabilities.task_store import TaskStore
from novelvideo.media_capabilities.video.h3_reference_payload import (
    H3GlobalReference,
    build_h3_reference_timeline_payload,
)
from novelvideo.media_capabilities.video.pipeline import H3VideoPipeline
from novelvideo.media_capabilities.video.runtime import (
    H3GenerationResult,
    _director_output_settings,
    _probe_video,
    get_h3_concurrency_coordinator,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    load_h3_reference_workflow_profile,
)
from novelvideo.narrative_groups.video_references import ResolvedVideoReference
from novelvideo.narrative_groups.video_references import (
    MAX_VIDEO_REFERENCE_BYTES,
    MAX_VIDEO_REFERENCE_PIXELS,
    _CtypesWin32SnapshotAdapter,
    _path_is_within_root,
    _read_file_snapshot,
)


_COMPILER_VERSION = 5
H3_FRAME_MAX_BYTES = MAX_VIDEO_REFERENCE_BYTES
H3_FRAME_MAX_PIXELS = MAX_VIDEO_REFERENCE_PIXELS
H3_FRAME_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
# A narrative group normally has at most five physical units with two frames each.
H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES = 5 * H3_FRAME_MAX_BYTES
H3_REFERENCE_INPUT_SNAPSHOT_VERSION = 1
H3_REFERENCE_INPUT_SNAPSHOT_TTL_SECONDS = 7 * 24 * 60 * 60
_SNAPSHOT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class H3FrozenFrame:
    source: str
    content: bytes
    sha256: str
    width: int
    height: int
    suffix: str


@dataclass(frozen=True, slots=True)
class H3ReferenceInputSnapshot:
    digest: str
    reference_revision: int
    reference_limit: int
    provider_workflow_id: str
    references: tuple[ResolvedVideoReference, ...]
    frames: MappingProxyType


@dataclass(frozen=True, slots=True)
class H3PersistedReferenceInputSnapshot:
    snapshot_id: str
    digest: str


def _write_private_file_at(directory_fd: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("failed to write H3 input snapshot")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _delete_posix_snapshot_at(storage_fd: int, snapshot_id: str) -> bool:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        snapshot_fd = os.open(snapshot_id, flags, dir_fd=storage_fd)
    except FileNotFoundError:
        return False
    try:
        for name in os.listdir(snapshot_fd):
            metadata = os.stat(name, dir_fd=snapshot_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                if name not in {"references", "frames"}:
                    raise ValueError("unexpected H3 snapshot directory")
                child_fd = os.open(name, flags, dir_fd=snapshot_fd)
                try:
                    for child in os.listdir(child_fd):
                        child_metadata = os.stat(
                            child, dir_fd=child_fd, follow_symlinks=False
                        )
                        if not stat.S_ISREG(child_metadata.st_mode):
                            raise ValueError("unexpected H3 snapshot entry")
                        os.unlink(child, dir_fd=child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=snapshot_fd)
            elif stat.S_ISREG(metadata.st_mode) and name in {
                "snapshot.json", ".snapshot.json.tmp", "lease.json", ".lease.json.tmp"
            }:
                os.unlink(name, dir_fd=snapshot_fd)
            else:
                raise ValueError("unexpected H3 snapshot entry")
        os.rmdir(snapshot_id, dir_fd=storage_fd)
        return True
    finally:
        os.close(snapshot_fd)


def _persist_posix_snapshot(
    state: Path,
    snapshot_id: str,
    blobs: list[tuple[str, bytes]],
    descriptor: bytes,
    lease: bytes,
) -> None:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors: list[int] = []
    created = False
    try:
        descriptors.append(os.open(state, flags))
        try:
            os.mkdir("h3_reference_input_snapshots", 0o700, dir_fd=descriptors[-1])
        except FileExistsError:
            pass
        descriptors.append(os.open(
            "h3_reference_input_snapshots", flags, dir_fd=descriptors[-1]
        ))
        os.mkdir(snapshot_id, 0o700, dir_fd=descriptors[-1])
        created = True
        descriptors.append(os.open(snapshot_id, flags, dir_fd=descriptors[-1]))
        snapshot_fd = descriptors[-1]
        os.mkdir("references", 0o700, dir_fd=snapshot_fd)
        os.mkdir("frames", 0o700, dir_fd=snapshot_fd)
        child_fds = {}
        try:
            for name in ("references", "frames"):
                child_fds[name] = os.open(name, flags, dir_fd=snapshot_fd)
            for blob, content in blobs:
                directory, name = blob.split("/", 1)
                _write_private_file_at(child_fds[directory], name, content)
            _write_private_file_at(snapshot_fd, ".snapshot.json.tmp", descriptor)
            _write_private_file_at(snapshot_fd, "lease.json", lease)
            os.rename(
                ".snapshot.json.tmp", "snapshot.json",
                src_dir_fd=snapshot_fd, dst_dir_fd=snapshot_fd,
            )
            os.fsync(snapshot_fd)
            os.fsync(descriptors[1])
        finally:
            for descriptor_fd in child_fds.values():
                os.close(descriptor_fd)
    except BaseException:
        if created and len(descriptors) >= 2:
            try:
                _delete_posix_snapshot_at(descriptors[1], snapshot_id)
            except (OSError, ValueError):
                pass
        raise
    finally:
        for descriptor_fd in reversed(descriptors):
            os.close(descriptor_fd)


def _open_validated_windows_directory(adapter, path: Path, root: Path):
    handle = adapter.open_write_directory(path)
    attributes = int(adapter.attributes(handle))
    if attributes & int(adapter.REPARSE_POINT):
        adapter.close(handle)
        raise ValueError("H3 snapshot directory is a reparse point")
    if not attributes & int(adapter.DIRECTORY):
        adapter.close(handle)
        raise ValueError("H3 snapshot parent is not a directory")
    final_path = Path(adapter.final_path_for_handle(handle))
    if not _path_is_within_root(root, final_path) or os.path.normcase(
        os.path.abspath(final_path)
    ) != os.path.normcase(os.path.abspath(path)):
        adapter.close(handle)
        raise ValueError("H3 snapshot directory handle path changed")
    return handle


def _persist_windows_snapshot(
    state: Path,
    snapshot_id: str,
    blobs: list[tuple[str, bytes]],
    descriptor: bytes,
    lease: bytes,
    *,
    adapter=None,
) -> None:
    win32 = adapter or _CtypesWin32SnapshotAdapter()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage = state / "h3_reference_input_snapshots"
    snapshot_dir = storage / snapshot_id
    directories = [state, storage, snapshot_dir, snapshot_dir / "references", snapshot_dir / "frames"]
    handles = []
    published_files: list[Path] = []
    try:
        handles.append(_open_validated_windows_directory(win32, state, state))
        for directory in directories[1:]:
            win32.create_directory(directory)
            handles.append(_open_validated_windows_directory(win32, directory, state))
        for blob, content in blobs:
            path = snapshot_dir / blob
            handle = win32.create_new_file(path)
            try:
                if int(win32.attributes(handle)) & int(win32.REPARSE_POINT):
                    raise ValueError("H3 snapshot blob is a reparse point")
                final_path = Path(win32.final_path_for_handle(handle))
                if os.path.normcase(os.path.abspath(final_path)) != os.path.normcase(
                    os.path.abspath(path)
                ):
                    raise ValueError("H3 snapshot blob handle path changed")
                win32.write_file(handle, content)
                win32.flush_file(handle)
            finally:
                win32.close(handle)
            published_files.append(path)
        temporary = snapshot_dir / ".snapshot.json.tmp"
        handle = win32.create_new_file(temporary)
        try:
            win32.write_file(handle, descriptor)
            win32.flush_file(handle)
        finally:
            win32.close(handle)
        published_files.append(temporary)
        target = snapshot_dir / "snapshot.json"
        win32.move_file(temporary, target)
        published_files[-1] = target
        lease_path = snapshot_dir / "lease.json"
        handle = win32.create_new_file(lease_path)
        try:
            win32.write_file(handle, lease)
            win32.flush_file(handle)
        finally:
            win32.close(handle)
        published_files.append(lease_path)
    except BaseException:
        for path in reversed(published_files):
            try:
                win32.delete_file(path)
            except OSError:
                pass
        raise
    finally:
        for handle in reversed(handles):
            win32.close(handle)


def _delete_windows_snapshot(state: Path, snapshot_id: str, *, adapter=None) -> bool:
    win32 = adapter or _CtypesWin32SnapshotAdapter()
    storage = state / "h3_reference_input_snapshots"
    snapshot_dir = storage / snapshot_id
    handles = []
    try:
        try:
            handles.append(_open_validated_windows_directory(win32, state, state))
            handles.append(_open_validated_windows_directory(win32, storage, state))
            snapshot_handle = _open_validated_windows_directory(
                win32, snapshot_dir, state
            )
            handles.append(snapshot_handle)
        except FileNotFoundError:
            return False
        for directory_name in ("references", "frames"):
            directory = snapshot_dir / directory_name
            child_handle = _open_validated_windows_directory(win32, directory, state)
            handles.append(child_handle)
            for child in directory.iterdir():
                file_handle = win32.open_path(child, directory=False)
                try:
                    attributes = int(win32.attributes(file_handle))
                    if attributes & (int(win32.REPARSE_POINT) | int(win32.DIRECTORY)):
                        raise ValueError("unexpected H3 snapshot entry")
                    final_path = Path(win32.final_path_for_handle(file_handle))
                    if os.path.normcase(os.path.abspath(final_path)) != os.path.normcase(
                        os.path.abspath(child)
                    ):
                        raise ValueError("H3 snapshot cleanup handle path changed")
                finally:
                    win32.close(file_handle)
                win32.delete_file(child)
            win32.close(handles.pop())
            os.rmdir(directory)
        for child in snapshot_dir.iterdir():
            if child.name not in {
                "snapshot.json", ".snapshot.json.tmp", "lease.json", ".lease.json.tmp"
            }:
                raise ValueError("unexpected H3 snapshot entry")
            file_handle = win32.open_path(child, directory=False)
            try:
                if int(win32.attributes(file_handle)) & int(win32.REPARSE_POINT):
                    raise ValueError("unexpected H3 snapshot reparse point")
            finally:
                win32.close(file_handle)
            win32.delete_file(child)
        win32.close(handles.pop())
        os.rmdir(snapshot_dir)
        return True
    finally:
        for handle in reversed(handles):
            win32.close(handle)


def persist_h3_reference_input_snapshot(
    *,
    state_root: str | Path,
    references,
    frames,
    reference_revision: int,
    reference_limit: int,
    provider_workflow_id: str,
    win32_adapter: object | None = None,
) -> H3PersistedReferenceInputSnapshot:
    """Persist immutable enqueue inputs; only the opaque returned ID enters payloads."""
    if isinstance(reference_revision, bool) or not isinstance(reference_revision, int):
        raise ValueError("reference_revision must be a non-negative integer")
    if reference_revision < 0:
        raise ValueError("reference_revision must be a non-negative integer")
    if isinstance(reference_limit, bool) or not isinstance(reference_limit, int):
        raise ValueError("reference_limit must be an integer from 1 to 10")
    if not 1 <= reference_limit <= 10:
        raise ValueError("reference_limit must be from 1 to 10")
    workflow_id = str(provider_workflow_id).strip()
    if not workflow_id:
        raise ValueError("provider_workflow_id is required")
    frozen_references = tuple(references)
    if not 1 <= len(frozen_references) <= reference_limit:
        raise ValueError("global references do not satisfy reference_limit")

    reference_records = []
    reference_contents = []
    for index, reference in enumerate(frozen_references, start=1):
        if not isinstance(reference, ResolvedVideoReference):
            raise TypeError("references must contain ResolvedVideoReference values")
        if len(reference.content) > MAX_VIDEO_REFERENCE_BYTES:
            raise ValueError("reference snapshot exceeds the byte limit")
        digest = hashlib.sha256(reference.content).hexdigest()
        if digest != reference.sha256:
            raise ValueError("reference snapshot sha256 does not match content")
        _image_metadata(reference.content, label=f"reference {reference.reference_id}")
        blob = f"references/{index:03d}.blob"
        reference_contents.append((blob, reference.content))
        reference_records.append({
            "reference_id": reference.reference_id,
            "source_kind": str(reference.source_kind),
            "label": reference.label,
            "subject_description": reference.subject_description,
            "sha256": digest,
            "asset_id": reference.asset_id,
            "temporary_upload_id": reference.temporary_upload_id,
            "blob": blob,
        })

    frame_records = []
    frame_contents = []
    cumulative = 0
    for index, (source, frame) in enumerate(dict(frames).items(), start=1):
        if not isinstance(frame, H3FrozenFrame):
            raise TypeError("frames must contain H3FrozenFrame values")
        digest = hashlib.sha256(frame.content).hexdigest()
        if digest != frame.sha256:
            raise ValueError("frame snapshot sha256 does not match content")
        width, height, suffix = _image_metadata(frame.content, label="frame snapshot")
        if (width, height, suffix) != (frame.width, frame.height, frame.suffix):
            raise ValueError("frame snapshot metadata does not match content")
        cumulative += len(frame.content)
        if cumulative > H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES:
            raise ValueError("H3 frame snapshot exceeds the group byte limit")
        blob = f"frames/{index:03d}.blob"
        frame_contents.append((blob, frame.content))
        frame_records.append({
            "source_key": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
            "sha256": digest,
            "width": width,
            "height": height,
            "suffix": suffix,
            "blob": blob,
        })

    snapshot_id = uuid4().hex
    descriptor = json.dumps({
        "version": H3_REFERENCE_INPUT_SNAPSHOT_VERSION,
        "reference_revision": reference_revision,
        "reference_limit": reference_limit,
        "provider_workflow_id": workflow_id,
        "references": reference_records,
        "frames": frame_records,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(descriptor).hexdigest()
    lease = json.dumps(
        {"expires_at": time.time() + H3_REFERENCE_INPUT_SNAPSHOT_TTL_SECONDS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    blobs = reference_contents + frame_contents
    if os.name != "nt":
        _persist_posix_snapshot(Path(state_root), snapshot_id, blobs, descriptor, lease)
    else:
        _persist_windows_snapshot(
            Path(state_root), snapshot_id, blobs, descriptor, lease,
            adapter=win32_adapter,
        )
    return H3PersistedReferenceInputSnapshot(snapshot_id=snapshot_id, digest=digest)


def load_h3_reference_input_snapshot(
    *,
    state_root: str | Path,
    snapshot_id: str,
    expected_digest: str,
    frame_sources,
) -> H3ReferenceInputSnapshot:
    """Load and hash-check immutable enqueue inputs through no-follow handles."""
    if _SNAPSHOT_ID_PATTERN.fullmatch(str(snapshot_id)) is None:
        raise ValueError("invalid H3 reference snapshot ID")
    state = Path(state_root)
    snapshot_dir = state / "h3_reference_input_snapshots" / str(snapshot_id)
    raw_descriptor = _read_file_snapshot(
        state,
        snapshot_dir / "snapshot.json",
        "H3 reference snapshot descriptor",
        expected_root=snapshot_dir,
    )
    actual_digest = hashlib.sha256(raw_descriptor).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_digest)):
        raise ValueError("invalid H3 reference snapshot digest")
    if actual_digest != expected_digest:
        raise ValueError("H3 reference snapshot digest does not match payload")
    try:
        descriptor = json.loads(raw_descriptor)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid H3 reference snapshot descriptor") from exc
    if descriptor.get("version") != H3_REFERENCE_INPUT_SNAPSHOT_VERSION:
        raise ValueError("unsupported H3 reference snapshot version")
    reference_limit = int(descriptor["reference_limit"])
    reference_records = descriptor.get("references", ())
    if not isinstance(reference_records, list) or not 1 <= len(reference_records) <= reference_limit:
        raise ValueError("invalid H3 reference snapshot count")

    references = []
    for record in reference_records:
        blob_path = snapshot_dir / str(record["blob"])
        content = _read_file_snapshot(
            state, blob_path, "H3 reference snapshot", expected_root=snapshot_dir
        )
        digest = hashlib.sha256(content).hexdigest()
        if digest != record.get("sha256"):
            raise ValueError("reference snapshot sha256 does not match content")
        _image_metadata(content, label="reference snapshot")
        references.append(ResolvedVideoReference(
            reference_id=str(record["reference_id"]),
            source_kind=str(record["source_kind"]),
            label=str(record["label"]),
            subject_description=str(record["subject_description"]),
            path=blob_path,
            content=content,
            sha256=digest,
            asset_id=str(record.get("asset_id") or ""),
            temporary_upload_id=str(record.get("temporary_upload_id") or ""),
        ))

    records_by_key = {
        str(record.get("source_key")): record
        for record in descriptor.get("frames", ())
    }
    sources = tuple(dict.fromkeys(str(source) for source in frame_sources if source))
    if {hashlib.sha256(source.encode("utf-8")).hexdigest() for source in sources} != set(records_by_key):
        raise ValueError("frame sources do not match H3 reference snapshot")
    frames = {}
    cumulative = 0
    for source in sources:
        key = hashlib.sha256(source.encode("utf-8")).hexdigest()
        record = records_by_key[key]
        blob_path = snapshot_dir / str(record["blob"])
        content = _read_file_snapshot(
            state, blob_path, "H3 frame snapshot", expected_root=snapshot_dir
        )
        digest = hashlib.sha256(content).hexdigest()
        if digest != record.get("sha256"):
            raise ValueError("frame snapshot sha256 does not match content")
        cumulative += len(content)
        if cumulative > H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES:
            raise ValueError("H3 frame snapshot exceeds the group byte limit")
        width, height, suffix = _image_metadata(content, label="frame snapshot")
        if [width, height, suffix] != [
            record.get("width"), record.get("height"), record.get("suffix")
        ]:
            raise ValueError("frame snapshot metadata does not match content")
        frames[source] = H3FrozenFrame(
            source=source, content=content, sha256=digest,
            width=width, height=height, suffix=suffix,
        )
    return H3ReferenceInputSnapshot(
        digest=actual_digest,
        reference_revision=int(descriptor["reference_revision"]),
        reference_limit=reference_limit,
        provider_workflow_id=str(descriptor["provider_workflow_id"]),
        references=tuple(references),
        frames=MappingProxyType(frames),
    )


def delete_h3_reference_input_snapshot(
    *,
    state_root: str | Path,
    snapshot_id: str,
    win32_adapter: object | None = None,
) -> bool:
    """Remove an unqueued snapshot without following caller-controlled links."""
    if _SNAPSHOT_ID_PATTERN.fullmatch(str(snapshot_id)) is None:
        raise ValueError("invalid H3 reference snapshot ID")
    state = Path(state_root)
    storage = state / "h3_reference_input_snapshots"
    if os.name != "nt" and all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")
    ):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors: list[int] = []
        try:
            descriptors.append(os.open(state, flags))
            descriptors.append(os.open(storage.name, flags, dir_fd=descriptors[-1]))
            return _delete_posix_snapshot_at(descriptors[-1], str(snapshot_id))
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ValueError("H3 snapshot cleanup violates no-follow policy") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    return _delete_windows_snapshot(
        state, str(snapshot_id), adapter=win32_adapter
    )


def renew_h3_reference_input_snapshot_lease(
    *, state_root: str | Path, snapshot_id: str,
    ttl_seconds: int = H3_REFERENCE_INPUT_SNAPSHOT_TTL_SECONDS,
) -> None:
    """Extend a queued/running snapshot lease through a no-follow directory chain."""
    if _SNAPSHOT_ID_PATTERN.fullmatch(str(snapshot_id)) is None:
        raise ValueError("invalid H3 reference snapshot ID")
    if isinstance(ttl_seconds, bool) or int(ttl_seconds) < 0:
        raise ValueError("snapshot TTL must be a non-negative integer")
    if os.name == "nt":
        state = Path(state_root)
        storage = state / "h3_reference_input_snapshots"
        snapshot = storage / snapshot_id
        temporary = snapshot / ".lease.json.tmp"
        target = snapshot / "lease.json"
        win32 = _CtypesWin32SnapshotAdapter()
        handles = []
        published = False
        try:
            handles.append(_open_validated_windows_directory(win32, state, state))
            handles.append(_open_validated_windows_directory(win32, storage, state))
            handles.append(_open_validated_windows_directory(win32, snapshot, state))
            lease = json.dumps(
                {"expires_at": time.time() + int(ttl_seconds)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            handle = win32.create_new_file(temporary)
            try:
                win32.write_file(handle, lease)
                win32.flush_file(handle)
            finally:
                win32.close(handle)
            published = True
            os.replace(temporary, target)
            published = False
        finally:
            if published:
                try:
                    win32.delete_file(temporary)
                except OSError:
                    pass
            for handle in reversed(handles):
                win32.close(handle)
        return
    import fcntl

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        descriptors.append(os.open(Path(state_root), flags))
        descriptors.append(os.open(
            "h3_reference_input_snapshots", flags, dir_fd=descriptors[-1]
        ))
        descriptors.append(os.open(snapshot_id, flags, dir_fd=descriptors[-1]))
        fcntl.flock(descriptors[-1], fcntl.LOCK_EX)
        try:
            temporary_metadata = os.stat(
                ".lease.json.tmp",
                dir_fd=descriptors[-1],
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(temporary_metadata.st_mode):
                raise ValueError("unexpected H3 snapshot lease entry")
            os.unlink(".lease.json.tmp", dir_fd=descriptors[-1])
        lease = json.dumps(
            {"expires_at": time.time() + int(ttl_seconds)},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        _write_private_file_at(descriptors[-1], ".lease.json.tmp", lease)
        os.rename(
            ".lease.json.tmp", "lease.json",
            src_dir_fd=descriptors[-1], dst_dir_fd=descriptors[-1],
        )
        os.fsync(descriptors[-1])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def garbage_collect_h3_reference_input_snapshots(
    *,
    state_root: str | Path,
    protected_ids=(),
    ttl_seconds: int = H3_REFERENCE_INPUT_SNAPSHOT_TTL_SECONDS,
    now: float | None = None,
) -> int:
    """Delete expired orphan snapshots while retaining active task inputs."""
    if isinstance(ttl_seconds, bool) or int(ttl_seconds) < 0:
        raise ValueError("snapshot TTL must be a non-negative integer")
    protected = {str(item) for item in protected_ids}
    state = Path(state_root)
    if os.name == "nt":
        storage = state / "h3_reference_input_snapshots"
        win32 = _CtypesWin32SnapshotAdapter()
        handles = []
        try:
            try:
                handles.append(_open_validated_windows_directory(win32, state, state))
                handles.append(
                    _open_validated_windows_directory(win32, storage, state)
                )
            except FileNotFoundError:
                return 0
            expired = []
            for item in storage.iterdir():
                if (
                    _SNAPSHOT_ID_PATTERN.fullmatch(item.name) is None
                    or item.name in protected
                ):
                    continue
                handle = _open_validated_windows_directory(win32, item, state)
                try:
                    lease_path = item / "lease.json"
                    lease_handle = win32.open_path(lease_path, directory=False)
                    try:
                        if int(win32.attributes(lease_handle)) & int(
                            win32.REPARSE_POINT
                        ):
                            continue
                        final_path = Path(win32.final_path_for_handle(lease_handle))
                        if os.path.normcase(os.path.abspath(final_path)) != os.path.normcase(
                            os.path.abspath(lease_path)
                        ):
                            continue
                        lease = json.loads(win32.read_file(lease_handle, 4096))
                    finally:
                        win32.close(lease_handle)
                finally:
                    win32.close(handle)
                if float(lease.get("expires_at") or 0) <= float(
                    now or time.time()
                ):
                    expired.append(item.name)
        finally:
            for handle in reversed(handles):
                win32.close(handle)
    else:
        import fcntl

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors: list[int] = []
        try:
            descriptors.append(os.open(state, flags))
            try:
                descriptors.append(os.open(
                    "h3_reference_input_snapshots", flags, dir_fd=descriptors[-1]
                ))
            except FileNotFoundError:
                return 0
            deleted = 0
            for name in os.listdir(descriptors[-1]):
                if _SNAPSHOT_ID_PATTERN.fullmatch(name) is None or name in protected:
                    continue
                snapshot_fd = os.open(name, flags, dir_fd=descriptors[-1])
                try:
                    try:
                        fcntl.flock(snapshot_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    lease_fd = os.open(
                        "lease.json", os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=snapshot_fd,
                    )
                    try:
                        lease = json.loads(os.read(lease_fd, 4096))
                    finally:
                        os.close(lease_fd)
                    if float(lease.get("expires_at") or 0) <= float(now or time.time()):
                        if _delete_posix_snapshot_at(descriptors[-1], name):
                            deleted += 1
                except (FileNotFoundError, OSError, TypeError, ValueError):
                    continue
                finally:
                    os.close(snapshot_fd)
            return deleted
        except FileNotFoundError:
            return 0
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    deleted = 0
    for snapshot_id in expired:
        if delete_h3_reference_input_snapshot(
            state_root=state, snapshot_id=snapshot_id
        ):
            deleted += 1
    return deleted


def _image_metadata(content: bytes, *, label: str) -> tuple[int, int, str]:
    try:
        image = Image.open(BytesIO(content))
    except OSError as exc:
        raise ValueError(f"{label} is not a valid image") from exc
    with image:
        image_format = str(image.format or "").upper()
        if image_format not in H3_FRAME_ALLOWED_FORMATS:
            raise ValueError(f"{label} must use PNG, JPEG, or WEBP format")
        if image.width * image.height > H3_FRAME_MAX_PIXELS:
            raise ValueError(f"{label} exceeds the pixel limit")
        try:
            image.load()
            suffix = {
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
            }[image_format]
            return image.width, image.height, suffix
        except OSError as exc:
            raise ValueError(f"{label} is not a valid image") from exc


def freeze_h3_reference_frames(
    segments,
    *,
    project_root: str | Path,
    win32_adapter: object | None = None,
    platform_name: str | None = None,
) -> MappingProxyType:
    """Read and validate every frame in a physical group before transport."""
    root = Path(project_root)
    frozen_frames: dict[str, H3FrozenFrame] = {}
    cumulative_bytes = 0
    for segment in tuple(segments):
        for raw_source in (segment.first_frame, segment.last_frame):
            if not raw_source or raw_source in frozen_frames:
                continue
            source = str(raw_source)
            path = Path(source)
            if not path.is_absolute():
                path = root / path
            content = _read_file_snapshot(
                root,
                path,
                f"H3 frame {source}",
                win32_adapter=win32_adapter,
                platform_name=platform_name,
            )
            if len(content) > H3_FRAME_MAX_BYTES:
                raise ValueError(f"H3 frame exceeds the byte limit: {path}")
            cumulative_bytes += len(content)
            if cumulative_bytes > H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES:
                raise ValueError("H3 frame snapshot exceeds the group byte limit")
            width, height, suffix = _image_metadata(content, label=f"frame {path}")
            frozen_frames[source] = H3FrozenFrame(
                source=source,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                width=width,
                height=height,
                suffix=suffix,
            )
    return MappingProxyType(frozen_frames)


def _selected_frozen_frames(segments, frozen_frames) -> dict[str, H3FrozenFrame]:
    selected: dict[str, H3FrozenFrame] = {}
    for segment in segments:
        for raw_source in (segment.first_frame, segment.last_frame):
            if not raw_source or raw_source in selected:
                continue
            source = str(raw_source)
            try:
                frame = frozen_frames[source]
            except KeyError as exc:
                raise ValueError(f"frame snapshot is missing: {source}") from exc
            if not isinstance(frame, H3FrozenFrame):
                raise TypeError("frozen_frames must contain H3FrozenFrame values")
            if hashlib.sha256(frame.content).hexdigest() != frame.sha256:
                raise ValueError(f"frame snapshot sha256 does not match content: {source}")
            width, height, suffix = _image_metadata(
                frame.content, label=f"frame snapshot {source}"
            )
            if (width, height, suffix) != (frame.width, frame.height, frame.suffix):
                raise ValueError(f"frame snapshot metadata does not match content: {source}")
            selected[source] = frame
    return selected


def _freeze_inputs(
    segments,
    references,
    *,
    reference_limit: int,
    frozen_frames=None,
    project_root: str | Path | None = None,
):
    if isinstance(reference_limit, bool) or not isinstance(reference_limit, int):
        raise ValueError("reference_limit must be an integer from 1 to 10")
    if not 1 <= reference_limit <= 10:
        raise ValueError("reference_limit must be from 1 to 10")
    frozen_references = tuple(references)
    if not 1 <= len(frozen_references) <= reference_limit:
        raise ValueError(
            f"global references must contain between 1 and {reference_limit} items"
        )
    reference_models = []
    reference_suffixes = []
    for index, reference in enumerate(frozen_references, start=1):
        if not isinstance(reference, ResolvedVideoReference):
            raise TypeError("global_references must contain ResolvedVideoReference values")
        if not isinstance(reference.content, bytes):
            raise TypeError("resolved reference content must be immutable bytes")
        digest = hashlib.sha256(reference.content).hexdigest()
        if digest != reference.sha256:
            raise ValueError(f"reference {reference.reference_id} sha256 does not match content")
        _, _, suffix = _image_metadata(
            reference.content, label=f"reference {reference.reference_id}"
        )
        reference_suffixes.append(suffix)
        reference_models.append(H3GlobalReference(
            reference_id=reference.reference_id,
            source_kind=reference.source_kind,
            label=reference.label,
            subject_description=reference.subject_description,
            uploaded_url=f"frozen://reference/{index}",
            sha256=digest,
        ))

    if frozen_frames is None:
        if project_root is None:
            raise ValueError("project_root is required to freeze H3 frames")
        snapshot = freeze_h3_reference_frames(
            segments, project_root=project_root
        )
    else:
        snapshot = frozen_frames
    selected_frames = _selected_frozen_frames(segments, snapshot)
    return (
        frozen_references,
        tuple(reference_models),
        tuple(reference_suffixes),
        selected_frames,
    )


def _load_runtime():
    from novelvideo.api.deps import (
        get_media_capability_store,
        get_media_credential_resolver,
    )
    from novelvideo.media_capabilities.runtime.configuration import (
        load_runninghub_runtime_configuration,
    )

    return load_runninghub_runtime_configuration(
        get_media_capability_store(), get_media_credential_resolver()
    )


def _idempotency_input(
    *,
    timeline,
    references: tuple[ResolvedVideoReference, ...],
    frames: dict[str, H3FrozenFrame],
    workflow_id: str,
    reference_limit: int,
    mode: str,
    aspect_ratio: str,
    resolution: str,
    output_path: str,
) -> dict[str, object]:
    return {
        "compiler_version": _COMPILER_VERSION,
        "workflow_id": workflow_id,
        "reference_limit": reference_limit,
        "references": [
            {
                "picture_index": index,
                "reference_id": reference.reference_id,
                "source_kind": str(reference.source_kind),
                "label": reference.label,
                "subject_description": reference.subject_description,
                "sha256": reference.sha256,
            }
            for index, reference in enumerate(references, start=1)
        ],
        "frames": [
            {
                "segment_id": entry.segment.segment_id,
                "first_frame_sha256": frames[entry.segment.first_frame].sha256,
                "last_frame_sha256": (
                    frames[entry.segment.last_frame].sha256
                    if entry.segment.last_frame else None
                ),
                "prompt": entry.segment.prompt,
                "start": entry.start_frame,
                "frame_count": entry.frame_count,
            }
            for entry in timeline.entries
        ],
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "output_target": Path(output_path).as_posix(),
    }


async def generate_h3_reference_director_video(
    ctx,
    *,
    segments,
    output_path: str,
    aspect_ratio: str = "9:16",
    resolution: str | None = None,
    mode: str = "auto",
    global_references,
    reference_limit: int,
    workflow_id: str,
    frozen_frames=None,
    on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
) -> H3GenerationResult:
    """Submit H3 Ref using immutable bytes captured before remote I/O."""
    from novelvideo.media_capabilities.video.h3_timeline import build_h3_timeline_data
    normalized_segments = tuple(segments)
    timeline = build_h3_timeline_data(normalized_segments, strict_first_frame=True)
    references, preflight_references, reference_suffixes, frames = _freeze_inputs(
        normalized_segments,
        global_references,
        reference_limit=reference_limit,
        frozen_frames=frozen_frames,
        project_root=getattr(ctx, "output_dir", Path(output_path).parent),
    )
    output_settings = _director_output_settings(aspect_ratio, resolution)
    preflight_frames = {
        source: {
            "imageFile": f"frozen://frame/{index}",
            "width": frame.width,
            "height": frame.height,
            "sha256": frame.sha256,
        }
        for index, (source, frame) in enumerate(frames.items(), start=1)
    }
    build_h3_reference_timeline_payload(
        timeline,
        preflight_references,
        max_references=reference_limit,
        mode=mode,
        uploaded_frames=preflight_frames,
        aspect_ratio=aspect_ratio,
        resolution=resolution or "720p",
        output_settings=output_settings,
    )
    profile = load_h3_reference_workflow_profile(workflow_id=workflow_id)
    runtime = _load_runtime()
    runtime_root = Path(ctx.runtime_dir) / "media_h3_ref"
    artifact_root = runtime_root / "artifacts"
    staging_parent = runtime_root / "staging"
    staging = staging_parent / uuid4().hex
    client = None
    try:
        staging.mkdir(parents=True, mode=0o700)
        staging.chmod(0o700)
        client = runtime.create_client()
        uploaded_references = []
        for index, (reference, validated, suffix) in enumerate(
            zip(references, preflight_references, reference_suffixes, strict=True),
            start=1,
        ):
            staged = staging / f"reference-{index}{suffix}"
            staged.write_bytes(reference.content)
            staged.chmod(0o600)
            uploaded_references.append(validated.model_copy(update={
                "uploaded_url": await client.upload(staged),
            }))
        uploaded_frames = {}
        for index, (source, frame) in enumerate(frames.items(), start=1):
            staged = staging / f"frame-{index}{frame.suffix}"
            staged.write_bytes(frame.content)
            staged.chmod(0o600)
            uploaded_frames[source] = {
                "imageFile": await client.upload(staged),
                "width": frame.width,
                "height": frame.height,
                "sha256": frame.sha256,
            }

        timeline_data = build_h3_reference_timeline_payload(
            timeline,
            uploaded_references,
            max_references=reference_limit,
            mode=mode,
            uploaded_frames=uploaded_frames,
            aspect_ratio=aspect_ratio,
            resolution=resolution or "720p",
            output_settings=output_settings,
        )
        actual_mode = "fl2va" if any(
            entry.segment.last_frame for entry in timeline.entries
        ) else "i2va"
        first_frame = frames[timeline.entries[0].segment.first_frame]
        last_source = next((
            entry.segment.last_frame
            for entry in reversed(timeline.entries)
            if entry.segment.last_frame
        ), None)
        request = VideoGenerationRequest(
            capability=(
                MediaCapability.VIDEO_FL2VA
                if actual_mode == "fl2va"
                else MediaCapability.VIDEO_I2VA
            ),
            prompt="\n".join(entry.segment.prompt for entry in timeline.entries),
            duration=timeline.duration_seconds,
            first_frame=f"sha256:{first_frame.sha256}",
            last_frame=(f"sha256:{frames[last_source].sha256}" if last_source else None),
            aspect_ratio=aspect_ratio,
            resolution=f"{output_settings['width']}x{output_settings['height']}",
        )
        task_store = TaskStore(runtime_root / "tasks.db")
        artifacts = ArtifactStore(artifact_root)
        concurrency = get_h3_concurrency_coordinator(runtime.account.id)
        concurrency.configure(
            runtime.account.id,
            runtime.account.max_concurrency,
            runtime.account.capability_limits,
            runtime.account.queue_limit,
        )

        async def probe(artifact: MediaArtifact):
            return await _probe_video(artifact_root / artifact.local_path)

        async def unexpected_upload(_source: str):
            raise AssertionError("timeline generation must use frozen uploaded assets")

        pipeline = H3VideoPipeline(
            store=task_store,
            executor=RunningHubExecutor(task_store, client, artifacts, concurrency),
            workflow_profile=profile,
            provider_account_id=runtime.account.id,
            upload_reference=unexpected_upload,
            probe_video=probe,
            register_candidate=lambda _candidate: None,
            prompt_profile={"id": "minimax-h3-ref", "version": 1},
        )
        candidate = await pipeline.generate_timeline(
            request,
            timeline_data=timeline_data,
            input_asset_hashes=tuple(
                [reference.sha256 for reference in references]
                + [frame.sha256 for frame in frames.values()]
            ),
            idempotency_input=_idempotency_input(
                timeline=timeline,
                references=references,
                frames=frames,
                workflow_id=workflow_id,
                reference_limit=reference_limit,
                mode=mode,
                aspect_ratio=aspect_ratio,
                resolution=resolution or "720p",
                output_path=output_path,
            ),
            on_provider_submitted=on_provider_submitted,
        )
        source = artifact_root / candidate.artifact.local_path
        actual_probe = getattr(candidate, "probe", None)
        if candidate.status is not MediaTaskStatus.SUCCEEDED:
            issue_codes = {issue.code for issue in candidate.quality_issues}
            if issue_codes != {"video.resolution_mismatch"}:
                codes = ", ".join(sorted(issue_codes))
                raise RuntimeError(f"MiniMax H3 Ref output failed quality checks: {codes}")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        return H3GenerationResult(
            output_path=target.as_posix(),
            provider_task_id=candidate.provider_task_id,
            actual_mode=actual_mode,
            actual_output=(
                {"width": actual_probe.width, "height": actual_probe.height}
                if actual_probe is not None
                else {
                    "width": int(output_settings["width"]),
                    "height": int(output_settings["height"]),
                }
            ),
        )
    finally:
        primary_error_active = sys.exc_info()[0] is not None
        cleanup_errors: list[BaseException] = []
        if client is not None:
            try:
                await client.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            shutil.rmtree(staging, ignore_errors=False)
        except FileNotFoundError:
            pass
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            staging_parent.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            if staging_parent.exists() and not any(staging_parent.iterdir()):
                cleanup_errors.append(OSError("H3 staging directory cleanup failed"))
        if cleanup_errors and not primary_error_active:
            raise cleanup_errors[0]


__all__ = [
    "H3FrozenFrame",
    "H3PersistedReferenceInputSnapshot",
    "H3ReferenceInputSnapshot",
    "delete_h3_reference_input_snapshot",
    "freeze_h3_reference_frames",
    "garbage_collect_h3_reference_input_snapshots",
    "generate_h3_reference_director_video",
    "load_h3_reference_input_snapshot",
    "persist_h3_reference_input_snapshot",
    "renew_h3_reference_input_snapshot_lease",
]
