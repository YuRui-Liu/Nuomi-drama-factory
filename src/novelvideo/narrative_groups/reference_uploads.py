"""Validated, project-scoped uploads used by narrative reference decisions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol, Sequence

from PIL import Image, UnidentifiedImageError

MAX_REFERENCE_UPLOAD_BYTES = 10 * 1024 * 1024
PERSISTENCE_WARNING = "Reference image could not be persisted; using the temporary upload."
_MIME_FORMATS = {
    "image/png": (".png", "PNG"),
    "image/jpeg": (".jpg", "JPEG"),
    "image/webp": (".webp", "WEBP"),
}
_EXTENSION_ALIASES = {".jpeg": ".jpg"}


class InvalidReferenceUpload(ValueError):
    """Raised when bytes or persistence targets violate the upload contract."""


@dataclass(frozen=True, slots=True)
class PersistenceRequest:
    requirement_id: str
    asset_kind: Literal["character_identity", "scene_base", "scene_variant", "prop"]
    target_entity_id: str
    base_entity_id: str
    variant_id: str
    upload_id: str
    extension: str


class PersistResolver(Protocol):
    """Resolve a target only; implementations must not mutate the filesystem."""

    def __call__(self, project_dir: Path, request: PersistenceRequest) -> Path | None: ...


@dataclass(frozen=True, slots=True)
class ReferenceUpload:
    upload_id: str
    image_path: str
    mime_type: str
    size_bytes: int
    temporary: bool = True
    persisted: bool = False
    persistence_warning: str = ""


@dataclass(frozen=True, slots=True)
class ValidatedReferenceImage:
    image_path: str
    mime_type: str
    size_bytes: int


def _scope(project_dir: Path) -> str:
    return hashlib.sha256(str(project_dir.resolve(strict=False)).encode()).hexdigest()


def _upload_root(project_dir: Path) -> Path:
    return project_dir.resolve(strict=False) / ".runtime" / "reference_uploads"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _reject_symlinks(root: Path, path: Path) -> None:
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise InvalidReferenceUpload("reference image is outside its controlled root") from None
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise InvalidReferenceUpload("reference image path contains a symlink")
        except FileNotFoundError:
            continue


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


def _open_dir_chain(anchor: Path, parts: Sequence[str], *, create: bool) -> int:
    """Open every directory relative to the previous fd without following symlinks."""
    try:
        current_fd = os.open(anchor, _DIRECTORY_FLAGS)
    except OSError:
        raise InvalidReferenceUpload("controlled root is unavailable") from None
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part:
                raise InvalidReferenceUpload("controlled directory component is invalid")
            try:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise InvalidReferenceUpload("controlled directory is unavailable") from None
                try:
                    os.mkdir(part, 0o755, dir_fd=current_fd)
                    next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                except OSError:
                    raise InvalidReferenceUpload("controlled directory could not be created") from None
            except OSError:
                raise InvalidReferenceUpload("controlled directory contains a symlink") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _same_directory_identity(anchor: Path, parts: Sequence[str], directory_fd: int) -> bool:
    try:
        check_fd = _open_dir_chain(anchor, parts, create=False)
    except InvalidReferenceUpload:
        return False
    try:
        first = os.fstat(directory_fd)
        second = os.fstat(check_fd)
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
    finally:
        os.close(check_fd)


def _anchor_for_root(root: Path) -> Path:
    if root.name == "assets":
        return root.parent
    if root.name == "reference_uploads" and root.parent.name == ".runtime":
        return root.parent.parent
    return root


def _relative_parent(anchor: Path, path: Path) -> tuple[tuple[str, ...], str]:
    try:
        relative = path.absolute().relative_to(anchor.absolute())
    except ValueError:
        raise InvalidReferenceUpload("reference image is outside its controlled root") from None
    if not relative.parts:
        raise InvalidReferenceUpload("reference image path is invalid")
    return tuple(relative.parts[:-1]), relative.parts[-1]


def _create_parent_via_dirfd(root: Path, target: Path) -> None:
    anchor = _anchor_for_root(root)
    parent_parts, _ = _relative_parent(anchor, target)
    parent_fd = _open_dir_chain(anchor, parent_parts, create=True)
    os.close(parent_fd)


def _decode_image(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            detected_format = str(image.format or "").upper()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise InvalidReferenceUpload("reference is not a valid image") from None
    return detected_format


def _validate_bytes(data: bytes, content_type: str, filename: str, max_bytes: int) -> tuple[str, str]:
    if not isinstance(data, bytes) or not data:
        raise InvalidReferenceUpload("upload must contain image bytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise InvalidReferenceUpload("max upload size must be a positive integer")
    if len(data) > max_bytes:
        raise InvalidReferenceUpload(f"upload size exceeds {max_bytes} bytes")
    mime_type = str(content_type or "").split(";", 1)[0].strip().lower()
    expected = _MIME_FORMATS.get(mime_type)
    if expected is None:
        raise InvalidReferenceUpload("unsupported image MIME type")
    supplied = Path(filename).suffix.lower()
    extension = _EXTENSION_ALIASES.get(supplied, supplied) if filename else expected[0]
    if extension != expected[0]:
        raise InvalidReferenceUpload("filename extension does not match MIME type")
    if _decode_image(data) != expected[1]:
        raise InvalidReferenceUpload("actual image format does not match MIME type")
    return mime_type, extension


def validate_reference_image(
    image_path: str | Path,
    *,
    allowed_roots: Sequence[Path],
    expected_mime: str | None = None,
    max_bytes: int = MAX_REFERENCE_UPLOAD_BYTES,
) -> ValidatedReferenceImage:
    """Re-read and decode an existing image without following a final symlink."""
    lexical_path = Path(image_path).absolute()
    matching_roots = [root.absolute() for root in allowed_roots if _inside(lexical_path, root)]
    if not matching_roots:
        raise InvalidReferenceUpload("reference image is outside its controlled root")
    root = matching_roots[0]
    _reject_symlinks(root, lexical_path)
    anchor = _anchor_for_root(root)
    parent_parts, filename = _relative_parent(anchor, lexical_path)
    parent_fd = _open_dir_chain(anchor, parent_parts, create=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if not _same_directory_identity(anchor, parent_parts, parent_fd):
            raise InvalidReferenceUpload("reference image directory changed during validation")
        descriptor = os.open(filename, flags, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size < 1 or info.st_size > max_bytes:
                raise InvalidReferenceUpload("reference image size is invalid")
            data = os.read(descriptor, max_bytes + 1)
        finally:
            os.close(descriptor)
    except OSError:
        raise InvalidReferenceUpload("reference image is unavailable") from None
    finally:
        os.close(parent_fd)
    detected = _decode_image(data)
    by_format = {value[1]: mime for mime, value in _MIME_FORMATS.items()}
    mime_type = by_format.get(detected)
    if mime_type is None:
        raise InvalidReferenceUpload("unsupported actual image format")
    extension = _EXTENSION_ALIASES.get(lexical_path.suffix.lower(), lexical_path.suffix.lower())
    if extension != _MIME_FORMATS[mime_type][0]:
        raise InvalidReferenceUpload("image extension does not match actual image format")
    if expected_mime and mime_type != expected_mime:
        raise InvalidReferenceUpload("actual image format does not match recorded MIME type")
    return ValidatedReferenceImage(str(lexical_path.resolve()), mime_type, len(data))


def _atomic_write(root: Path, target: Path, data: bytes) -> None:
    _reject_symlinks(root, target.parent)
    anchor = _anchor_for_root(root)
    parent_parts, filename = _relative_parent(anchor, target)
    parent_fd = _open_dir_chain(anchor, parent_parts, create=True)
    staging_name = f".reference-{uuid.uuid4().hex}"
    try:
        try:
            current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise InvalidReferenceUpload("persistence target is not a regular file")
        except FileNotFoundError:
            pass
        descriptor = os.open(
            staging_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not _same_directory_identity(anchor, parent_parts, parent_fd):
            raise InvalidReferenceUpload("persistence directory changed during write")
        os.replace(
            staging_name, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
        )
    finally:
        try:
            os.unlink(staging_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _read_existing_bytes(root: Path, target: Path) -> bytes | None:
    anchor = _anchor_for_root(root)
    parent_parts, filename = _relative_parent(anchor, target)
    parent_fd = _open_dir_chain(anchor, parent_parts, create=False)
    try:
        if not _same_directory_identity(anchor, parent_parts, parent_fd):
            raise InvalidReferenceUpload("persistence directory changed during read")
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise InvalidReferenceUpload("persistence target is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _remove_file(root: Path, target: Path) -> None:
    anchor = _anchor_for_root(root)
    parent_parts, filename = _relative_parent(anchor, target)
    parent_fd = _open_dir_chain(anchor, parent_parts, create=False)
    try:
        if not _same_directory_identity(anchor, parent_parts, parent_fd):
            raise InvalidReferenceUpload("persistence directory changed during cleanup")
        try:
            os.unlink(filename, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(parent_fd)


def _persistence_request(
    *, requirement_id: str, asset_kind: str, target_entity_id: str,
    base_entity_id: str, variant_id: str, upload_id: str, extension: str,
) -> PersistenceRequest:
    if asset_kind not in {"character_identity", "scene_base", "scene_variant", "prop"}:
        raise InvalidReferenceUpload("valid asset_kind is required for persistence")
    if not target_entity_id.strip():
        raise InvalidReferenceUpload("target_entity_id is required for persistence")
    if asset_kind == "scene_variant" and (not base_entity_id.strip() or not variant_id.strip()):
        raise InvalidReferenceUpload(
            "base_entity_id and variant_id are required for scene variant persistence"
        )
    return PersistenceRequest(
        requirement_id, asset_kind, target_entity_id, base_entity_id, variant_id,
        upload_id, extension,  # type: ignore[arg-type]
    )


def _write_metadata(root: Path, project_dir: Path, upload: ReferenceUpload) -> None:
    path = Path(upload.image_path)
    storage_root = project_dir / "assets" if upload.persisted else root
    payload = {
        "upload_id": upload.upload_id,
        "project_scope": _scope(project_dir),
        "storage_kind": "persisted" if upload.persisted else "temporary",
        "relative_path": str(path.resolve().relative_to(storage_root.resolve())),
        "mime_type": upload.mime_type,
        "size_bytes": upload.size_bytes,
        "persistence_warning": upload.persistence_warning,
    }
    _atomic_write(root, root / f"{upload.upload_id}.json", json.dumps(payload).encode())


def save_reference_upload(
    project_dir: Path, data: bytes, content_type: str, filename: str = "", *,
    persist: bool = False, requirement_id: str = "", asset_kind: str = "",
    target_entity_id: str = "", base_entity_id: str = "", variant_id: str = "",
    persist_resolver: PersistResolver | None = None,
    max_bytes: int = MAX_REFERENCE_UPLOAD_BYTES,
) -> ReferenceUpload:
    """Save validated bytes; persistence adapters only select a controlled target."""
    project_dir = Path(project_dir).resolve(strict=False)
    mime_type, extension = _validate_bytes(data, content_type, filename, max_bytes)
    upload_id = f"upl_{uuid.uuid4().hex}"
    request = None
    if persist:
        request = _persistence_request(
            requirement_id=requirement_id, asset_kind=asset_kind,
            target_entity_id=target_entity_id, base_entity_id=base_entity_id,
            variant_id=variant_id, upload_id=upload_id, extension=extension,
        )
    root = _upload_root(project_dir)
    temporary_path = root / f"blob_{uuid.uuid4().hex}{extension}"
    _atomic_write(root, temporary_path, data)
    result = ReferenceUpload(upload_id, str(temporary_path), mime_type, len(data))
    if persist:
        target: Path | None = None
        previous_data: bytes | None = None
        try:
            target_value = persist_resolver(project_dir, request) if persist_resolver else None
            if target_value is None:
                raise InvalidReferenceUpload("persistence resolver returned no target")
            target = Path(target_value).absolute()
            assets_root = project_dir / "assets"
            allowed_suffixes = {extension, ".jpeg" if extension == ".jpg" else extension}
            if not _inside(target, assets_root) or target.suffix.lower() not in allowed_suffixes:
                raise InvalidReferenceUpload("persistence target is invalid")
            _create_parent_via_dirfd(assets_root, target)
            _reject_symlinks(assets_root, target)
            previous_data = _read_existing_bytes(assets_root, target)
            _atomic_write(assets_root, target, data)
            validate_reference_image(target, allowed_roots=(assets_root,), expected_mime=mime_type)
            result = ReferenceUpload(upload_id, str(target.resolve()), mime_type, len(data), False, True)
        except Exception:
            if target is not None:
                try:
                    if previous_data is None:
                        _remove_file(project_dir / "assets", target)
                    else:
                        _atomic_write(project_dir / "assets", target, previous_data)
                except (OSError, InvalidReferenceUpload):
                    pass
            result = ReferenceUpload(
                upload_id, str(temporary_path), mime_type, len(data),
                persistence_warning=PERSISTENCE_WARNING,
            )
    _write_metadata(root, project_dir, result)
    return result


def load_reference_upload(project_dir: Path, upload_id: str) -> ReferenceUpload | None:
    """Resolve an opaque upload ID without trusting paths stored in metadata."""
    project_dir = Path(project_dir).resolve(strict=False)
    if not str(upload_id).startswith("upl_") or len(str(upload_id)) != 36:
        return None
    root = _upload_root(project_dir)
    metadata = root / f"{upload_id}.json"
    try:
        _reject_symlinks(project_dir, metadata)
        metadata_bytes = _read_existing_bytes(root, metadata)
        if metadata_bytes is None:
            return None
        raw = json.loads(metadata_bytes.decode("utf-8"))
        if raw.get("upload_id") != upload_id or raw.get("project_scope") != _scope(project_dir):
            return None
        persisted = raw.get("storage_kind") == "persisted"
        storage_root = project_dir / "assets" if persisted else root
        relative = Path(str(raw["relative_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        validated = validate_reference_image(
            storage_root / relative, allowed_roots=(storage_root,),
            expected_mime=str(raw["mime_type"]),
        )
        if validated.size_bytes != int(raw["size_bytes"]):
            return None
        return ReferenceUpload(
            upload_id, validated.image_path, validated.mime_type, validated.size_bytes,
            not persisted, persisted, str(raw.get("persistence_warning") or ""),
        )
    except (InvalidReferenceUpload, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


__all__ = [
    "InvalidReferenceUpload", "MAX_REFERENCE_UPLOAD_BYTES", "PERSISTENCE_WARNING",
    "PersistResolver", "PersistenceRequest", "ReferenceUpload",
    "ValidatedReferenceImage", "load_reference_upload", "save_reference_upload",
    "validate_reference_image",
]
