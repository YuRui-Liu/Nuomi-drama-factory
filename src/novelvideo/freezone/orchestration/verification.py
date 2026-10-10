"""Validate project-owned artifacts before marking a node successful."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

from PIL import Image

from .models import OrchestrationError


def local_media_path(project_dir: Path, project_id: str, url: str) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        raise OrchestrationError("foreign_media", "Only local project media is accepted")
    path = unquote(parsed.path)
    prefixes = (f"/static/projects/{project_id}/", f"/api/v1/projects/{project_id}/media/")
    if path.startswith(("/static/", "/api/")):
        prefix = next((p for p in prefixes if path.startswith(p)), None)
        if prefix is None:
            raise OrchestrationError("foreign_media", "Media belongs to another project")
        path = path[len(prefix):]
    else:
        path = path.lstrip("/")
    root = project_dir.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or ".." in Path(path).parts:
        raise OrchestrationError("foreign_media", "Media path escapes project")
    return candidate


def verify_media(project_dir: Path, project_id: str, url: str, kind: str) -> dict:
    path = local_media_path(project_dir, project_id, url)
    if not path.is_file() or path.stat().st_size == 0:
        raise OrchestrationError("invalid_artifact", "Output file is missing or empty")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    result = {"kind": kind, "url": url.split("?")[0], "sha256": digest.hexdigest(), "bytes": path.stat().st_size}
    try:
        if kind == "image":
            with Image.open(path) as img:
                result.update(width=img.width, height=img.height)
                img.verify()
        elif kind in {"video", "audio"}:
            proc = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                  capture_output=True, text=True, timeout=30, check=True)
            metadata = json.loads(proc.stdout)
            streams = [s for s in metadata["streams"] if s.get("codec_type") == kind]
            duration = float(metadata["format"].get("duration", 0))
            if not streams or duration <= 0:
                raise ValueError("missing media stream or duration")
            result.update(duration=duration, streams=[{key: stream[key] for key in
                          ("codec_type", "codec_name", "width", "height", "sample_rate", "channels") if key in stream}
                          for stream in metadata["streams"]])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise OrchestrationError("invalid_artifact", "Cannot decode output media", kind=kind) from exc
    return result
