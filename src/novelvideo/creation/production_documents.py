"""Project-local text documents shared by the canvas and local production tools."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import uuid

import portalocker


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})
_REPARSE_POINT = 0x400


class DocumentError(RuntimeError):
    """Base error for project document operations."""


class DocumentConflict(DocumentError):
    """The caller's expected document hash is no longer current."""


class DocumentNotFound(DocumentError, FileNotFoundError):
    """A requested project document does not exist."""


class DocumentValidation(DocumentError, ValueError):
    """A document path or body is not safe to read or write."""


def _relative_parts(relative_path: str | Path, *, text_document: bool = True) -> tuple[str, ...]:
    raw = str(relative_path)
    if not raw or "\x00" in raw or "\\" in raw:
        raise DocumentValidation("document path must be a project-relative POSIX path")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise DocumentValidation("absolute document paths are not allowed")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise DocumentValidation("document path must not contain empty, '.' or '..' segments")
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or not parsed.parts:
        raise DocumentValidation("document path must be project-relative")
    if text_document and parsed.suffix.casefold() not in _TEXT_SUFFIXES:
        raise DocumentValidation("only text document paths are allowed")
    return tuple(parsed.parts)


def _is_handoff_receipt_path(relative_path: str | Path) -> bool:
    try:
        parts = _relative_parts(relative_path)
    except DocumentValidation:
        return False
    return (
        len(parts) == 4
        and parts[0] == "episodes"
        and parts[1].isdigit()
        and parts[2] == "handoff-receipts"
        and re.fullmatch(r"[0-9a-f]{32}\.md", parts[3]) is not None
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _contained_path(
    root: Path,
    relative_path: str | Path,
    *,
    text_document: bool = True,
) -> Path:
    """Resolve a relative path while rejecting every existing symlink component."""
    parts = _relative_parts(relative_path, text_document=text_document)
    candidate = root.joinpath(*parts)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        if os.path.lexists(current):
            if _is_reparse_point(current):
                raise DocumentValidation("symlink document paths are not allowed")
            if index < len(parts) - 1 and not current.is_dir():
                raise DocumentValidation("a document parent path is not a directory")
            if index == len(parts) - 1 and not current.is_file():
                raise DocumentValidation("document path must refer to a regular file")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DocumentValidation("document path escapes the project") from exc
    return candidate


def _ensure_parent(
    root: Path,
    relative_path: str | Path,
    *,
    text_document: bool = True,
) -> Path:
    parts = _relative_parts(relative_path, text_document=text_document)
    current = root
    for part in parts[:-1]:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        if _is_reparse_point(current):
            raise DocumentValidation("symlink document paths are not allowed")
        if not current.is_dir():
            raise DocumentValidation("a document parent path is not a directory")
    return _contained_path(root, relative_path, text_document=text_document)


@contextmanager
def _project_file_lock(root: Path):
    """Serialize compare-and-write operations across threads and processes."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".production-documents.lock"
    if _is_reparse_point(lock_path):
        raise DocumentValidation("symlink project lock paths are not allowed")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise DocumentValidation("cannot safely open the project document lock") from exc
    with os.fdopen(descriptor, "a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(handle)


def _atomic_write_bytes(
    root: Path,
    relative_path: str | Path,
    data: bytes,
    *,
    text_document: bool = True,
) -> None:
    target = _ensure_parent(root, relative_path, text_document=text_document)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck parents immediately before publication. os.replace replaces a
        # final symlink itself, and never follows it, but parent symlinks are unsafe.
        target = _contained_path(root, relative_path, text_document=text_document)
        os.replace(temporary, target)
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class ProductionDocuments:
    """Read and write project text through one hash-checked atomic boundary."""

    def __init__(self, root: str | Path) -> None:
        # The caller supplies a trusted project directory. Resolve that boundary
        # once, then constrain every caller-provided path beneath it.
        self.root = Path(root).expanduser().resolve(strict=False)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()

    def read(self, relative_path: str | Path) -> dict[str, str]:
        with _project_file_lock(self.root):
            path = _contained_path(self.root, relative_path)
            if not path.exists():
                raise DocumentNotFound(f"document does not exist: {relative_path}")
            try:
                data = path.read_bytes()
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise DocumentValidation("document is not valid UTF-8 text") from exc
            except OSError as exc:
                raise DocumentNotFound(f"document does not exist: {relative_path}") from exc
            return {
                "path": PurePosixPath(*_relative_parts(relative_path)).as_posix(),
                "text": text,
                "hash": self._hash(text),
            }

    def write(
        self,
        relative_path: str | Path,
        text: str,
        expected_hash: str | None,
    ) -> dict[str, str]:
        if not isinstance(text, str):
            raise DocumentValidation("document text must be a string")
        if _is_handoff_receipt_path(relative_path):
            raise DocumentValidation("handoff receipt documents are immutable")
        if expected_hash is not None and (
            not isinstance(expected_hash, str) or _HASH_RE.fullmatch(expected_hash) is None
        ):
            raise DocumentValidation("expected_hash must be null or a SHA-256 hash")
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise DocumentValidation("document text must be valid UTF-8") from exc

        relative = PurePosixPath(*_relative_parts(relative_path)).as_posix()
        with _project_file_lock(self.root):
            path = _ensure_parent(self.root, relative)
            exists = os.path.lexists(path)
            if exists and _is_reparse_point(path):
                raise DocumentValidation("symlink document paths are not allowed")
            if expected_hash is None:
                if exists:
                    raise DocumentConflict("document already exists; expected_hash is required to replace it")
            else:
                if not exists or not path.is_file():
                    raise DocumentConflict("document changed or no longer exists")
                try:
                    current_text = path.read_bytes().decode("utf-8", errors="strict")
                except (OSError, UnicodeDecodeError) as exc:
                    raise DocumentConflict("document changed or is no longer valid UTF-8 text") from exc
                if self._hash(current_text) != expected_hash:
                    raise DocumentConflict("document hash changed; reload before saving")

            _atomic_write_bytes(self.root, relative, encoded)
            return {"path": relative, "text": text, "hash": self._hash(text)}

    def write_immutable(self, relative_path: str | Path, text: str) -> dict[str, str]:
        """Create an internal handoff receipt once; retries must match its bytes."""
        if not _is_handoff_receipt_path(relative_path):
            raise DocumentValidation("immutable writes are reserved for handoff receipts")
        if not isinstance(text, str):
            raise DocumentValidation("document text must be a string")
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise DocumentValidation("document text must be valid UTF-8") from exc

        relative = PurePosixPath(*_relative_parts(relative_path)).as_posix()
        with _project_file_lock(self.root):
            path = _contained_path(self.root, relative)
            if path.exists():
                try:
                    current = path.read_bytes()
                except OSError as exc:
                    raise DocumentConflict("immutable handoff receipt cannot be read") from exc
                if current != encoded:
                    raise DocumentConflict("immutable handoff receipt already exists with different content")
            else:
                _atomic_write_bytes(self.root, relative, encoded)
            return {"path": relative, "text": text, "hash": self._hash(text)}


__all__ = [
    "DocumentConflict",
    "DocumentError",
    "DocumentNotFound",
    "DocumentValidation",
    "ProductionDocuments",
]
