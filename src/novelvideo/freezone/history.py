"""Per-node Freezone generation history storage.

The canvas graph is still owned by the frontend.  This module keeps a small
backend-side append-only history keyed by (canvas_id, node_id) so a frontend can
later recover completed/failed generation attempts without bloating canvas JSON.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from novelvideo.freezone.paths import (
    CANVAS_ID_RE,
    freezone_root,
    resolve_static_url_to_path,
)
from novelvideo.utils import thumbnails

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_DEFAULT_LIMIT = 100
_IMAGE_OUTPUT_KEYS = ("output_url", "image_url", "master_url", "url")

# Cap the prompt stored per history attempt. History is append-only JSONL read
# whole into memory, so an uncapped prompt would bloat disk + the read response
# on heavily-regenerated nodes. The frontend only needs enough to identify the
# version, not the full multi-KB prompt.
MAX_HISTORY_PROMPT_CHARS = 4000
THUMBNAIL_PENDING_TTL_SECONDS = 300


def build_node_history_record(
    *,
    task_type: str,
    job_id: str,
    task_key: str,
    status: str,
    media_type: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    prompt: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical per-node generation-history record.

    Single owner of the record schema so every producer (image/text/video
    runners, the API-route helper, 3GS) stays consistent — a new field is added
    here once instead of in each call site. ``prompt`` is the text that produced
    this version; it is capped (see ``MAX_HISTORY_PROMPT_CHARS``) and is the
    field the frontend reads to show each version's own prompt.
    """
    record: dict[str, Any] = {
        "id": f"{task_type}:{job_id}",
        "task_type": task_type,
        "task_key": task_key,
        "job_id": job_id,
        "status": status,
        "media_type": media_type,
        **(extra or {}),
        "result": copy.deepcopy(result) if result else None,
        "error": error,
    }
    prompt = str(prompt or "").strip()
    if prompt:
        record["prompt"] = prompt[:MAX_HISTORY_PROMPT_CHARS]
    return record


def _safe_part(value: str) -> str:
    text = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return text[:128] or "unknown"


def generation_history_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "_generation_history"


def generation_history_path(project_dir: Path, canvas_id: str, node_id: str) -> Path:
    canvas = canvas_id.strip() or "default"
    if not CANVAS_ID_RE.match(canvas):
        raise ValueError(f"invalid canvas_id: {canvas_id!r}")
    node = _safe_part(node_id)
    return generation_history_dir(project_dir) / canvas / f"{node}.jsonl"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _attach_history_thumbnail(project_dir: Path, record: dict[str, Any]) -> None:
    """Queue one image thumbnail without exposing a path before it exists.

    Generation happens on the background worker. The persisted pending marker
    contains no local path and is safe to return before that worker finishes.
    """

    result = record.get("result")
    if not isinstance(result, dict):
        return
    result.pop("thumbnail_url", None)
    result.pop("thumbnail_status", None)
    result.pop("thumbnail_requested_at", None)
    if (
        record.get("status") not in {"completed", "succeeded"}
        or record.get("media_type") != "image"
    ):
        return
    for key in _IMAGE_OUTPUT_KEYS:
        raw_url = result.get(key)
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            source = resolve_static_url_to_path(urlsplit(raw_url).path, project_dir)
            expected = thumbnails.thumbnail_path(
                project_dir, source, thumbnails.DEFAULT_SIZE
            )
        except (OSError, RuntimeError, ValueError):
            continue
        if expected is None:
            continue
        try:
            destination = thumbnails.prewarm(
                project_dir, source, thumbnails.DEFAULT_SIZE
            )
        except (OSError, RuntimeError, ValueError):
            destination = None
        if destination is None:
            result["thumbnail_status"] = "deferred"
        elif destination.is_file():
            result["thumbnail_status"] = "ready"
        else:
            result["thumbnail_status"] = "pending"
            result["thumbnail_requested_at"] = _utc_timestamp(_utc_now())
        return
    result["thumbnail_status"] = "missing"


def _refresh_history_thumbnail(project_dir: Path, record: dict[str, Any]) -> None:
    """Expose only an already-generated thumbnail; never build or queue one."""

    result = record.get("result")
    if not isinstance(result, dict):
        return
    persisted_status = str(result.get("thumbnail_status") or "")
    persisted_requested_at = result.get("thumbnail_requested_at")
    result.pop("thumbnail_url", None)
    result.pop("thumbnail_status", None)
    result.pop("thumbnail_requested_at", None)
    if (
        record.get("status") not in {"completed", "succeeded"}
        or record.get("media_type") != "image"
    ):
        return
    for key in _IMAGE_OUTPUT_KEYS:
        raw_url = result.get(key)
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            source = resolve_static_url_to_path(urlsplit(raw_url).path, project_dir)
            destination = thumbnails.thumbnail_path(
                project_dir, source, thumbnails.DEFAULT_SIZE
            )
        except (OSError, RuntimeError, ValueError):
            continue
        if destination is None:
            continue
        if destination.is_file():
            # Internal local path only: history API endpoints already pass URL
            # fields through migrate_canvas_static_urls_in_memory, yielding the
            # canonical /static/projects/<project_id>/... browser URL.
            result["thumbnail_url"] = str(destination)
            result["thumbnail_status"] = "ready"
            return
        if persisted_status == "pending":
            requested_at = _parse_utc_timestamp(persisted_requested_at)
            if (
                requested_at is not None
                and (_utc_now() - requested_at).total_seconds()
                <= THUMBNAIL_PENDING_TTL_SECONDS
            ):
                result["thumbnail_status"] = "pending"
                result["thumbnail_requested_at"] = _utc_timestamp(requested_at)
            else:
                result["thumbnail_status"] = "failed"
        elif persisted_status in {"deferred", "failed"}:
            result["thumbnail_status"] = persisted_status
        else:
            result["thumbnail_status"] = "missing"
        return
    result["thumbnail_status"] = (
        "failed" if persisted_status == "pending" else "missing"
    )


def append_generation_history(
    *,
    project_dir: Path,
    canvas_id: str | None,
    node_id: str | None,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """Append one generation attempt for a canvas node.

    Returns the normalized record, or None when no node_id is supplied.  Missing
    node_id means the caller is an older frontend or a non-node job.
    """

    if not node_id:
        return None
    normalized = {
        "schema_version": 1,
        "canvas_id": canvas_id or "default",
        "node_id": node_id,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **record,
    }
    path = generation_history_path(project_dir, normalized["canvas_id"], node_id)
    _attach_history_thumbnail(project_dir, normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")
    return normalized


def _read_history_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def read_generation_history(
    *,
    project_dir: Path,
    canvas_id: str,
    node_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    records = _read_history_file(generation_history_path(project_dir, canvas_id, node_id))
    for record in records:
        _refresh_history_thumbnail(project_dir, record)
    if limit <= 0:
        return records
    return records[-limit:]


def read_canvas_generation_history(
    *,
    project_dir: Path,
    canvas_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Aggregate every node's generation history for a whole canvas.

    Reads all per-node JSONL files under the canvas history dir and merges them,
    newest first. Unlike the per-node read, this is *not* scoped to nodes still
    present on the canvas — a node deleted from the canvas keeps its history file,
    so its past attempts stay recoverable here. Malformed lines/files are skipped.
    """
    canvas = (canvas_id or "").strip() or "default"
    if not CANVAS_ID_RE.match(canvas):
        raise ValueError(f"invalid canvas_id: {canvas_id!r}")
    canvas_dir = generation_history_dir(project_dir) / canvas
    if not canvas_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in canvas_dir.glob("*.jsonl"):
        records.extend(_read_history_file(path))
    # Newest first; records without a usable timestamp sort last (empty string).
    for record in records:
        _refresh_history_thumbnail(project_dir, record)
    records.sort(key=lambda record: str(record.get("recorded_at") or ""), reverse=True)
    if limit <= 0:
        return records
    return records[:limit]
