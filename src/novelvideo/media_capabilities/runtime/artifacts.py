"""Atomic, content-addressed storage for media artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from novelvideo.media_capabilities.models import MediaArtifact


_SAFE_EXTENSION = re.compile(r"\.?([A-Za-z0-9]+)\Z")


class ArtifactStore:
    """Store media bytes under deterministic SHA256-based paths."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def put_bytes(
        self,
        data: bytes,
        *,
        media_type: str,
        extension: str,
        metadata: dict[str, Any] | None = None,
    ) -> MediaArtifact:
        match = _SAFE_EXTENSION.fullmatch(extension)
        if match is None:
            raise ValueError("extension must contain only ASCII letters and digits")

        normalized_extension = match.group(1).lower()
        digest = hashlib.sha256(data).hexdigest()
        relative_path = PurePosixPath(
            digest[:2],
            f"{digest}.{normalized_extension}",
        )
        target = self._root.joinpath(*relative_path.parts)

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f"{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as temporary_file:
                    temporary_file.write(data)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                os.replace(temporary_path, target)
            finally:
                temporary_path.unlink(missing_ok=True)

        return MediaArtifact(
            id=digest,
            media_type=media_type,
            local_path=relative_path.as_posix(),
            content_sha256=digest,
            metadata=metadata or {},
        )
