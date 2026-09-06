"""Resolve safe, ordered image references for narrative-group video generation."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re
import stat
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from novelvideo.models import beat_scene_id, real_detected_identities, real_detected_props
from novelvideo.utils.path_resolver import (
    canonical_identity_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
)

from .models import (
    NarrativeGroup,
    VideoReferenceItem,
    VideoReferenceSourceKind,
)

_SOURCE_RANK = {
    "character_identity": 0,
    "scene_master": 1,
    "prop_reference": 2,
    "temporary_upload": 3,
}
_SAFE_UPLOAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_REFERENCE_NUMBERING = re.compile(
    r"<?\s*(?:subject|picture)\s+\d+\s*>?", re.IGNORECASE
)
MAX_VIDEO_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_REFERENCE_PIXELS = 40_000_000
MAX_VIDEO_REFERENCE_DESCRIPTION_LENGTH = 500
_ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


@dataclass(frozen=True)
class VideoReferenceCandidate:
    reference_id: str
    source_kind: VideoReferenceSourceKind
    label: str
    subject_description: str
    asset_id: str = ""
    temporary_upload_id: str = ""


@dataclass(frozen=True)
class VideoReferenceSelection:
    reference_id: str
    subject_description: str


@dataclass(frozen=True)
class ResolvedVideoReference:
    reference_id: str
    source_kind: VideoReferenceSourceKind
    label: str
    subject_description: str
    path: Path
    content: bytes
    sha256: str
    asset_id: str = ""
    temporary_upload_id: str = ""


@dataclass(frozen=True)
class VideoReferencePreview:
    revision: int
    candidates: tuple[VideoReferenceCandidate, ...]
    references: tuple[VideoReferenceItem, ...]
    warnings: tuple[str, ...]
    max_images: int


def opaque_video_reference_id(source_kind: str, stable_asset_id: str) -> str:
    """Return a stable ID that never contains a local filesystem path."""
    canonical = f"{source_kind}\0{stable_asset_id.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_max_images(max_images: int) -> int:
    if isinstance(max_images, bool) or not isinstance(max_images, int):
        raise TypeError("max_images must be an integer")
    if not 1 <= max_images <= 10:
        raise ValueError("max_images must be between 1 and 10")
    return max_images


def _normalize_subject_description(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("video reference subject description must be a string")
    description = value.strip()
    if not description:
        raise ValueError("video reference subject description cannot be blank")
    if "\n" in description or "\r" in description:
        raise ValueError("video reference subject description must be a single line")
    if len(description) > MAX_VIDEO_REFERENCE_DESCRIPTION_LENGTH:
        raise ValueError(
            "video reference subject description exceeds the 500 character limit"
        )
    return description


def _value(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


async def _call_store(store: object, name: str, *args: object) -> object:
    method = getattr(store, name, None)
    if method is None:
        return None
    result = method(*args)
    return await result if inspect.isawaitable(result) else result


def _identifiers_for_beat(beat: object) -> set[str]:
    identifiers: set[str] = set()
    for name in ("id", "beat_id", "source_span_id"):
        raw = str(_value(beat, name, "") or "").strip()
        if raw:
            identifiers.add(raw)
    for raw in _value(beat, "source_span_ids", ()) or ():
        item = str(raw or "").strip()
        if item:
            identifiers.add(item)
    number = str(_value(beat, "beat_number", "") or "").strip()
    if number:
        identifiers.update({number, f"beat-{number}", f"beat_{number}"})
        try:
            normalized = str(int(number))
        except ValueError:
            pass
        else:
            identifiers.update(
                {normalized, f"beat-{normalized}", f"beat_{normalized}"}
            )
    return identifiers


async def _group_beats(
    store: object, episode_number: int, group: NarrativeGroup
) -> tuple[object, ...]:
    raw = await _call_store(store, "get_beats_as_dicts", episode_number)
    beats = tuple(raw or ())
    wanted = set(group.source_span_ids or group.beat_ids)
    if not wanted:
        return ()
    return tuple(beat for beat in beats if _identifiers_for_beat(beat) & wanted)


def _join_description(label: str, *descriptions: object) -> str:
    def normalize(value: object) -> str:
        text = _REFERENCE_NUMBERING.sub("", str(value or ""))
        return " ".join(text.split())

    clean_label = normalize(label)
    details = " ".join(
        dict.fromkeys(
            text
            for value in descriptions
            if (text := normalize(value))
        )
    )
    return f"{clean_label}: {details}" if details else clean_label


async def _asset_descriptions(
    store: object, episode_number: int
) -> tuple[dict[str, tuple[str, str]], dict[str, str], dict[str, str]]:
    identities: dict[str, tuple[str, str]] = {}
    characters = await _call_store(store, "get_all_characters") or ()
    for character in characters:
        character_name = str(_value(character, "name", "") or "").strip()
        for identity in _value(character, "identities", ()) or ():
            identity_id = str(_value(identity, "identity_id", "") or "").strip()
            if not identity_id:
                continue
            identity_name = str(_value(identity, "identity_name", "") or "").strip()
            label = (
                f"{character_name} · {identity_name}"
                if identity_name
                else identity_id
            )
            description = _join_description(
                label,
                _value(character, "face_prompt", ""),
                _value(identity, "face_prompt", ""),
                _value(identity, "appearance_details", ""),
                _value(identity, "body_type", ""),
                _value(character, "description", ""),
            )
            identities[identity_id] = (character_name, description)

    scenes: dict[str, str] = {}
    for scene in (await _call_store(store, "list_scenes") or ()):
        name = str(_value(scene, "name", "") or "").strip()
        if name:
            scenes[name] = _join_description(
                name,
                _value(scene, "environment_prompt", ""),
                _value(scene, "variant_prompt", ""),
                _value(scene, "description", ""),
            )

    props: dict[str, str] = {}
    for prop in (await _call_store(store, "list_props") or ()):
        name = str(_value(prop, "name", "") or "").strip()
        if name:
            props[name] = _join_description(
                name,
                _value(prop, "visual_prompt", ""),
                _value(prop, "description", ""),
            )
    episode = await _call_store(store, "get_episode", episode_number)
    for prop in _value(episode, "prop_menu", ()) or ():
        prop_id = str(_value(prop, "prop_id", "") or "").strip()
        if prop_id:
            props[prop_id] = _join_description(
                prop_id,
                _value(prop, "visual_prompt", ""),
                _value(prop, "description", ""),
            )
    return identities, scenes, props


def _path_within_project(project_dir: Path, path: Path) -> Path:
    project_root = project_dir.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("video reference path must remain inside the project") from exc
    return resolved


def _safe_path_segment(value: str, label: str) -> str:
    raw = str(value or "").strip()
    try:
        path = Path(raw)
        is_absolute = path.is_absolute()
    except (OSError, ValueError):
        is_absolute = True
        path = Path(".")
    if (
        not raw
        or raw in {".", ".."}
        or "\0" in raw
        or is_absolute
        or "/" in raw
        or "\\" in raw
        or path.name != raw
    ):
        raise ValueError(f"{label} asset ID must be a safe path segment")
    return raw


def _path_within_asset_root(
    project_dir: Path, asset_kind: str, candidate: Path
) -> Path:
    project_root = project_dir.resolve(strict=False)
    asset_root = (project_root / "assets" / asset_kind).resolve(strict=False)
    lexical = Path(os.path.abspath(candidate))
    try:
        asset_root.relative_to(project_root)
        lexical.relative_to(asset_root)
        lexical.resolve(strict=False).relative_to(asset_root)
    except ValueError as exc:
        raise ValueError(
            f"video reference path must remain inside the canonical {asset_kind} root"
        ) from exc
    return lexical


def _lexical_asset_root(project_dir: Path, asset_kind: str) -> Path:
    return Path(os.path.abspath(project_dir / "assets" / asset_kind))


def _path_contains_symlink(allowed_root: Path, path: Path) -> bool:
    root = Path(os.path.abspath(allowed_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def _identity_path(
    project_dir: Path, identity_id: str, character_name: str
) -> tuple[Path, bool]:
    identity_id = _safe_path_segment(identity_id, "character identity")
    name = _safe_path_segment(
        character_name or identity_id.split("_", 1)[0].strip(),
        "character",
    )
    identity = _path_within_asset_root(
        project_dir,
        "characters",
        canonical_identity_path(project_dir, name, identity_id),
    )
    if identity.is_file():
        return identity, False
    portrait = _path_within_asset_root(
        project_dir,
        "characters",
        canonical_portrait_path(project_dir, name),
    )
    return portrait, True


def _temporary_group_root(
    project_dir: Path, episode_number: int, group_id: str
) -> Path:
    raw_group_id = str(group_id or "").strip()
    try:
        is_absolute = Path(raw_group_id).is_absolute()
    except (OSError, ValueError):
        is_absolute = True
    if (
        not raw_group_id
        or raw_group_id in {".", ".."}
        or is_absolute
        or "/" in raw_group_id
        or "\\" in raw_group_id
        or Path(raw_group_id).name != raw_group_id
    ):
        raise ValueError("temporary reference group ID is invalid")

    references_root = _path_within_project(
        project_dir,
        project_dir
        / "videos"
        / f"ep{int(episode_number):03d}"
        / "narrative_groups"
        / "references",
    )
    lexical_group_root = references_root / raw_group_id
    if lexical_group_root.is_symlink():
        raise ValueError("temporary reference group directory cannot be a symlink")
    group_root = lexical_group_root.resolve(strict=False)
    if group_root.parent != references_root:
        raise ValueError("temporary reference group directory is invalid")
    return group_root


def temporary_upload_path(
    project_dir: str | Path,
    episode_number: int,
    group_id: str,
    upload_id: str,
) -> Path:
    upload_id = str(upload_id or "").strip()
    if (
        not _SAFE_UPLOAD_ID.fullmatch(upload_id)
        or upload_id in {".", ".."}
        or Path(upload_id).name != upload_id
    ):
        raise ValueError("temporary upload ID is invalid")
    project = Path(project_dir)
    group_root = _temporary_group_root(project, episode_number, group_id)
    path = group_root / f"{upload_id}.png"
    if path.parent != group_root:
        raise ValueError(
            "temporary upload path must remain inside the current group directory"
        )
    if path.is_symlink():
        raise ValueError("temporary upload symlink is forbidden by no-follow policy")
    return path


def _candidate(
    source_kind: VideoReferenceSourceKind,
    stable_id: str,
    label: str,
    description: str,
) -> VideoReferenceCandidate:
    return VideoReferenceCandidate(
        reference_id=opaque_video_reference_id(source_kind, stable_id),
        source_kind=source_kind,
        label=label,
        subject_description=description,
        asset_id="" if source_kind == "temporary_upload" else stable_id,
        temporary_upload_id=stable_id if source_kind == "temporary_upload" else "",
    )


def _candidate_sort_key(candidate: VideoReferenceCandidate) -> tuple[int, str]:
    stable_id = candidate.asset_id or candidate.temporary_upload_id
    return (_SOURCE_RANK[candidate.source_kind], stable_id)


async def resolve_group_video_reference_preview(
    *,
    store: object,
    project_dir: str | Path,
    episode_number: int,
    group: NarrativeGroup,
    max_images: int,
) -> VideoReferencePreview:
    """Discover current-group assets without exposing their filesystem paths."""
    limit = validate_max_images(max_images)
    project = Path(project_dir)
    safe_upload_root = _temporary_group_root(
        project, episode_number, group.id
    )
    beats = await _group_beats(store, episode_number, group)
    identity_metadata, scene_descriptions, prop_descriptions = (
        await _asset_descriptions(store, episode_number)
    )

    identity_ids: set[str] = set()
    scene_ids: set[str] = set()
    prop_ids: set[str] = set()
    for beat in beats:
        identity_ids.update(
            real_detected_identities(_value(beat, "detected_identities", ()) or ())
        )
        scene_id = beat_scene_id(beat)
        if scene_id:
            scene_ids.add(scene_id)
        prop_ids.update(real_detected_props(_value(beat, "detected_props", ()) or ()))

    candidates: list[VideoReferenceCandidate] = []
    warnings: list[str] = []
    for identity_id in sorted(identity_ids):
        character_name, description = identity_metadata.get(
            identity_id,
            (
                identity_id.split("_", 1)[0].strip(),
                _join_description(identity_id),
            ),
        )
        try:
            path, used_portrait = _identity_path(
                project, identity_id, character_name
            )
        except ValueError:
            warnings.append(
                f"Character identity {identity_id} has an unsafe asset ID."
            )
            continue
        if not path.is_file():
            warnings.append(
                f"Character identity {identity_id} is missing its identity image and portrait."
            )
            continue
        if _path_contains_symlink(
            _lexical_asset_root(project, "characters"), path
        ):
            warnings.append(
                f"Character identity {identity_id} uses a symlink and cannot be selected."
            )
            continue
        if used_portrait:
            warnings.append(
                f"Character identity {identity_id} is using its portrait fallback."
            )
        candidates.append(
            _candidate(
                "character_identity", identity_id, identity_id, description
            )
        )

    for scene_id in sorted(scene_ids):
        try:
            scene_id = _safe_path_segment(scene_id, "scene")
            path = _path_within_asset_root(
                project,
                "scenes",
                canonical_scene_master_path(project, scene_id),
            )
        except ValueError:
            warnings.append(f"Scene {scene_id} has an unsafe asset ID.")
            continue
        if not path.is_file():
            warnings.append(f"Scene {scene_id} is missing its master image.")
            continue
        if _path_contains_symlink(_lexical_asset_root(project, "scenes"), path):
            warnings.append(
                f"Scene {scene_id} uses a symlink and cannot be selected."
            )
            continue
        candidates.append(
            _candidate(
                "scene_master",
                scene_id,
                scene_id,
                scene_descriptions.get(scene_id, _join_description(scene_id)),
            )
        )

    for prop_id in sorted(prop_ids):
        try:
            prop_id = _safe_path_segment(prop_id, "prop")
            path = _path_within_asset_root(
                project,
                "props",
                canonical_prop_reference_path(project, prop_id),
            )
        except ValueError:
            warnings.append(f"Prop {prop_id} has an unsafe asset ID.")
            continue
        if not path.is_file():
            warnings.append(f"Prop {prop_id} is missing its reference image.")
            continue
        if _path_contains_symlink(_lexical_asset_root(project, "props"), path):
            warnings.append(
                f"Prop {prop_id} uses a symlink and cannot be selected."
            )
            continue
        candidates.append(
            _candidate(
                "prop_reference",
                prop_id,
                prop_id,
                prop_descriptions.get(prop_id, _join_description(prop_id)),
            )
        )

    if safe_upload_root.is_dir():
        for upload in sorted(safe_upload_root.glob("*.png"), key=lambda item: item.name):
            upload_id = upload.stem
            try:
                safe_path = temporary_upload_path(
                    project, episode_number, group.id, upload_id
                )
            except ValueError:
                warnings.append(f"Temporary upload {upload.name} has an invalid ID.")
                continue
            if not safe_path.is_file():
                warnings.append(f"Temporary upload {upload.name} is missing or unsafe.")
                continue
            candidates.append(
                _candidate(
                    "temporary_upload",
                    upload_id,
                    upload_id,
                    _join_description(upload_id, "temporary uploaded reference image"),
                )
            )

    deduplicated = {candidate.reference_id: candidate for candidate in candidates}
    ordered = tuple(sorted(deduplicated.values(), key=_candidate_sort_key))
    return VideoReferencePreview(
        revision=group.video_reference_settings.revision,
        candidates=ordered,
        references=group.video_reference_settings.references,
        warnings=tuple(warnings),
        max_images=limit,
    )


def _resolve_item_path(
    project: Path,
    episode_number: int,
    group: NarrativeGroup,
    item: VideoReferenceItem,
    identities: Mapping[str, tuple[str, str]],
) -> tuple[Path, Path]:
    if item.source_kind == "temporary_upload":
        if item.asset_id:
            raise ValueError("temporary upload reference cannot contain an asset ID")
        root = _temporary_group_root(project, episode_number, group.id)
        return (
            temporary_upload_path(
                project, episode_number, group.id, item.temporary_upload_id
            ),
            root,
        )
    if item.temporary_upload_id or not item.asset_id:
        raise ValueError("asset reference has invalid stable asset metadata")
    if item.source_kind == "character_identity":
        character_name = identities.get(item.asset_id, ("", ""))[0]
        path, _ = _identity_path(project, item.asset_id, character_name)
        return path, _lexical_asset_root(project, "characters")
    if item.source_kind == "scene_master":
        scene_id = _safe_path_segment(item.asset_id, "scene")
        return (
            _path_within_asset_root(
                project,
                "scenes",
                canonical_scene_master_path(project, scene_id),
            ),
            _lexical_asset_root(project, "scenes"),
        )
    if item.source_kind == "prop_reference":
        prop_id = _safe_path_segment(item.asset_id, "prop")
        return (
            _path_within_asset_root(
                project,
                "props",
                canonical_prop_reference_path(project, prop_id),
            ),
            _lexical_asset_root(project, "props"),
        )
    raise ValueError(f"unsupported video reference source kind: {item.source_kind}")


def _read_posix_file_snapshot(
    allowed_root: Path, path: Path, label: str
) -> bytes:
    """Read one regular file through directory FDs without following symlinks."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
    ):
        raise ValueError("no-follow reference reads are unsupported on this platform")

    project_root = Path(os.path.abspath(allowed_root))
    lexical_path = Path(os.path.abspath(path))
    try:
        relative = lexical_path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("video reference path must remain inside the project") from exc
    if not relative.parts:
        raise ValueError("video reference image path is invalid")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(project_root, directory_flags))
        for component in relative.parts[:-1]:
            descriptors.append(
                os.open(component, directory_flags, dir_fd=descriptors[-1])
            )
        file_descriptor = os.open(
            relative.parts[-1], file_flags, dir_fd=descriptors[-1]
        )
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("video reference must be a regular file")
        if before.st_size <= 0:
            raise ValueError(f"video reference image is empty: {label}")
        if before.st_size > MAX_VIDEO_REFERENCE_BYTES:
            raise ValueError("video reference image exceeds the byte limit")

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_VIDEO_REFERENCE_BYTES:
            chunk = os.read(
                file_descriptor,
                min(1024 * 1024, MAX_VIDEO_REFERENCE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_VIDEO_REFERENCE_BYTES:
            raise ValueError("video reference image exceeds the byte limit")
        content = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(content) != after.st_size
        ):
            raise ValueError("video reference image changed while being read")
        return content
    except FileNotFoundError as exc:
        raise ValueError(f"video reference image is missing: {label}") from exc
    except OSError as exc:
        raise ValueError(
            f"video reference image violates the no-follow policy: {label}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class _CtypesWin32SnapshotAdapter:
    """Small ctypes boundary for same-handle, no-follow Windows reads."""

    DIRECTORY = 0x10
    REPARSE_POINT = 0x400
    DISK_FILE_TYPE = 1

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._file_information_type = FileInformation
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.GetFileInformationByHandle.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(FileInformation),
        )
        self._kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        self._kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
        self._kernel32.GetFileType.restype = wintypes.DWORD
        self._kernel32.GetFinalPathNameByHandleW.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self._kernel32.ReadFile.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        self._kernel32.ReadFile.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._invalid_handle = ctypes.c_void_p(-1).value

    def _raise_last_error(self) -> None:
        raise self._ctypes.WinError(self._ctypes.get_last_error())

    def _information(self, handle: object):
        information = self._file_information_type()
        if not self._kernel32.GetFileInformationByHandle(
            handle, self._ctypes.byref(information)
        ):
            self._raise_last_error()
        return information

    def open_path(self, path: Path, *, directory: bool) -> object:
        generic_read = 0x80000000
        share_all = 0x1 | 0x2 | 0x4
        open_existing = 3
        open_reparse_point = 0x00200000
        backup_semantics = 0x02000000
        flags = open_reparse_point | (backup_semantics if directory else 0)
        handle = self._kernel32.CreateFileW(
            str(path),
            0 if directory else generic_read,
            share_all,
            None,
            open_existing,
            flags,
            None,
        )
        handle_value = getattr(handle, "value", handle)
        if handle_value == self._invalid_handle:
            self._raise_last_error()
        return handle

    def attributes(self, handle: object) -> int:
        return int(self._information(handle).dwFileAttributes)

    def file_type(self, handle: object) -> int:
        return int(self._kernel32.GetFileType(handle))

    def file_size(self, handle: object) -> int:
        information = self._information(handle)
        return (int(information.nFileSizeHigh) << 32) | int(
            information.nFileSizeLow
        )

    def final_path_for_handle(self, handle: object) -> Path:
        capacity = 512
        while True:
            buffer = self._ctypes.create_unicode_buffer(capacity)
            length = self._kernel32.GetFinalPathNameByHandleW(
                handle, buffer, capacity, 0
            )
            if length == 0:
                self._raise_last_error()
            if length < capacity:
                raw_path = buffer.value
                if raw_path.startswith("\\\\?\\UNC\\"):
                    raw_path = "\\\\" + raw_path[8:]
                elif raw_path.startswith("\\\\?\\"):
                    raw_path = raw_path[4:]
                return Path(raw_path)
            capacity = int(length) + 1

    def read_file(self, handle: object, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total < max_bytes:
            chunk_size = min(1024 * 1024, max_bytes - total)
            buffer = self._ctypes.create_string_buffer(chunk_size)
            bytes_read = self._wintypes.DWORD()
            if not self._kernel32.ReadFile(
                handle,
                buffer,
                chunk_size,
                self._ctypes.byref(bytes_read),
                None,
            ):
                self._raise_last_error()
            if bytes_read.value == 0:
                break
            chunks.append(buffer.raw[: bytes_read.value])
            total += int(bytes_read.value)
        return b"".join(chunks)

    def close(self, handle: object) -> None:
        if not self._kernel32.CloseHandle(handle):
            self._raise_last_error()


def _path_is_within_root(allowed_root: Path, candidate: Path) -> bool:
    root = os.path.normcase(os.path.abspath(allowed_root))
    path = os.path.normcase(os.path.abspath(candidate))
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def _read_windows_file_snapshot(
    allowed_root: Path,
    path: Path,
    label: str,
    *,
    adapter: object | None = None,
) -> bytes:
    """Read through Win32 handles while rejecting reparse-point traversal."""
    try:
        win32 = adapter or _CtypesWin32SnapshotAdapter()
    except (AttributeError, OSError) as exc:
        raise ValueError("secure Windows reference reads are unavailable") from exc
    root = Path(os.path.abspath(allowed_root))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "video reference path must remain inside its canonical root"
        ) from exc
    if not relative.parts:
        raise ValueError("video reference image path is invalid")

    handles: list[object] = []
    try:
        current = root
        directory_paths = [root]
        for component in relative.parts[:-1]:
            current /= component
            directory_paths.append(current)
        for directory in directory_paths:
            handle = win32.open_path(directory, directory=True)
            handles.append(handle)
            attributes = int(win32.attributes(handle))
            if attributes & int(win32.REPARSE_POINT):
                raise ValueError(
                    f"video reference directory is a reparse point: {label}"
                )
            if not attributes & int(win32.DIRECTORY):
                raise ValueError("video reference parent must be a directory")

        file_handle = win32.open_path(candidate, directory=False)
        handles.append(file_handle)
        attributes = int(win32.attributes(file_handle))
        if attributes & int(win32.REPARSE_POINT):
            raise ValueError(f"video reference file is a reparse point: {label}")
        if attributes & int(win32.DIRECTORY):
            raise ValueError("video reference must be a regular file")
        if int(win32.file_type(file_handle)) != int(win32.DISK_FILE_TYPE):
            raise ValueError("video reference must be a regular disk file")

        final_path = Path(win32.final_path_for_handle(file_handle))
        if not _path_is_within_root(root, final_path):
            raise ValueError(
                "video reference final handle path escaped its canonical root"
            )
        before_size = int(win32.file_size(file_handle))
        if before_size <= 0:
            raise ValueError(f"video reference image is empty: {label}")
        if before_size > MAX_VIDEO_REFERENCE_BYTES:
            raise ValueError("video reference image exceeds the byte limit")
        content = win32.read_file(file_handle, MAX_VIDEO_REFERENCE_BYTES + 1)
        after_size = int(win32.file_size(file_handle))
        if len(content) > MAX_VIDEO_REFERENCE_BYTES:
            raise ValueError("video reference image exceeds the byte limit")
        if before_size != after_size or len(content) != after_size:
            raise ValueError("video reference image changed while being read")
        return bytes(content)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(
            f"video reference image violates the Windows no-follow policy: {label}"
        ) from exc
    finally:
        for handle in reversed(handles):
            try:
                win32.close(handle)
            except OSError:
                pass


def _read_file_snapshot(
    allowed_root: Path,
    path: Path,
    label: str,
    *,
    win32_adapter: object | None = None,
    platform_name: str | None = None,
) -> bytes:
    platform = (
        "nt"
        if platform_name is None and (os.name == "nt" or sys.platform == "win32")
        else (platform_name or os.name)
    )
    if platform == "nt":
        return _read_windows_file_snapshot(
            allowed_root, path, label, adapter=win32_adapter
        )
    return _read_posix_file_snapshot(allowed_root, path, label)


def _validate_decoded_image(content: bytes, label: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(content))
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise ValueError(f"video reference image cannot be decoded: {label}") from exc

    with image:
        image_format = str(image.format or "").upper()
        if image_format not in _ALLOWED_IMAGE_FORMATS:
            raise ValueError(
                f"video reference image format is unsupported: {image_format or 'unknown'}"
            )
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_VIDEO_REFERENCE_PIXELS:
            raise ValueError("video reference image exceeds the pixel limit")
        try:
            image.load()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError(
                f"video reference image cannot be decoded: {label}"
            ) from exc


def _snapshot_reference_image(
    allowed_root: Path,
    path: Path,
    label: str,
    *,
    win32_adapter: object | None = None,
    platform_name: str | None = None,
) -> tuple[bytes, str]:
    content = _read_file_snapshot(
        allowed_root,
        path,
        label,
        win32_adapter=win32_adapter,
        platform_name=platform_name,
    )
    _validate_decoded_image(content, label)
    return content, hashlib.sha256(content).hexdigest()


async def resolve_saved_video_references(
    *,
    store: object,
    project_dir: str | Path,
    episode_number: int,
    group: NarrativeGroup,
    max_images: int,
) -> tuple[ResolvedVideoReference, ...]:
    """Resolve persisted logical references to verified server-side image paths."""
    limit = validate_max_images(max_images)
    references = group.video_reference_settings.references
    if not 1 <= len(references) <= limit:
        raise ValueError(f"video references must contain between 1 and {limit} items")
    reference_ids = [reference.reference_id for reference in references]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("video references contain a duplicate reference ID")

    identities, _, _ = await _asset_descriptions(store, episode_number)
    project = Path(project_dir)
    resolved: list[ResolvedVideoReference] = []
    for reference in references:
        description = _normalize_subject_description(
            reference.subject_description
        )
        stable_id = (
            reference.temporary_upload_id
            if reference.source_kind == "temporary_upload"
            else reference.asset_id
        )
        expected_id = opaque_video_reference_id(reference.source_kind, stable_id)
        if reference.reference_id != expected_id:
            raise ValueError("video reference ID does not match its stable asset ID")
        path, allowed_root = _resolve_item_path(
            project, episode_number, group, reference, identities
        )
        content, content_sha256 = await asyncio.to_thread(
            _snapshot_reference_image, allowed_root, path, reference.label
        )
        resolved.append(
            ResolvedVideoReference(
                reference_id=reference.reference_id,
                source_kind=reference.source_kind,
                label=reference.label,
                subject_description=description,
                path=path,
                content=content,
                sha256=content_sha256,
                asset_id=reference.asset_id,
                temporary_upload_id=reference.temporary_upload_id,
            )
        )
    return tuple(resolved)


def validate_video_reference_selections(
    selections: Sequence[VideoReferenceSelection], max_images: int
) -> tuple[VideoReferenceSelection, ...]:
    limit = validate_max_images(max_images)
    if isinstance(selections, (str, bytes, set, frozenset)) or not isinstance(
        selections, Sequence
    ):
        raise TypeError("video reference selections must be an ordered sequence")
    copied = tuple(selections)
    if not 1 <= len(copied) <= limit:
        raise ValueError(f"video references must contain between 1 and {limit} items")
    ids: list[str] = []
    normalized: list[VideoReferenceSelection] = []
    for selection in copied:
        if not isinstance(selection, VideoReferenceSelection):
            raise TypeError("video reference selection is invalid")
        reference_id = str(selection.reference_id or "").strip()
        description = _normalize_subject_description(
            selection.subject_description
        )
        ids.append(reference_id)
        normalized.append(VideoReferenceSelection(reference_id, description))
    if len(ids) != len(set(ids)):
        raise ValueError("video references contain a duplicate reference ID")
    return tuple(normalized)
