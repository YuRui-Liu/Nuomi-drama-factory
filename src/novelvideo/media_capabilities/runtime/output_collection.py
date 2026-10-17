"""Collect every declared output with durable per-file download checkpoints."""

from __future__ import annotations
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from novelvideo.media_capabilities.models import MediaTaskStatus
from novelvideo.media_capabilities.workflows.models import (
    WorkflowManifest,
    WorkflowError,
)


def result_kind(result) -> str:
    raw = (result.output_type or "").lower()
    if raw in {"image", "video", "audio", "document", "file"}:
        return raw
    for prefix in ("image", "video", "audio", "text"):
        if raw.startswith(prefix + "/"):
            return "document" if prefix == "text" else prefix
    mime = mimetypes.guess_type("file." + raw.lstrip("."))[0]
    if mime and mime.split("/")[0] in {"image", "video", "audio"}:
        return mime.split("/")[0]
    if raw in {"pdf", "json", "txt", "application/pdf", "application/json"}:
        return "document"
    return "unknown"


def select_outputs(manifest: WorkflowManifest, results) -> dict:
    selected = {}
    for name, rule in manifest.outputs.items():
        matches = [r for r in results if r.node_id == rule.node_id]
        if len(matches) < rule.min_count or (
            rule.max_count is not None and len(matches) > rule.max_count
        ):
            raise WorkflowError("output_count", name, phase="download")
        if rule.kind != "file" and any(result_kind(r) != rule.kind for r in matches):
            raise WorkflowError("output_type", name, phase="download")
        selected[name] = matches
    return selected


async def collect_outputs(store, attempt, client, artifacts, manifest, results):
    selected = select_outputs(manifest, results)
    if attempt.status == MediaTaskStatus.SUBMITTED:
        attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    if attempt.status == MediaTaskStatus.RUNNING:
        attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    checkpoint = store.get_checkpoint(attempt.id)
    completed = dict(checkpoint.get("downloaded", {}))
    named = {}
    for name, matches in selected.items():
        named[name] = []
        for index, result in enumerate(matches):
            key = f"{name}:{index}"
            if key not in completed:
                data = await client.download(result.url)
                extension = (
                    PurePosixPath(urlsplit(result.url).path).suffix.lstrip(".") or "bin"
                )
                artifact = artifacts.put_bytes(
                    data,
                    media_type=result.output_type or "application/octet-stream",
                    extension=extension,
                    metadata={
                        "output_name": name,
                        "node_id": result.node_id,
                        "index": index,
                    },
                )
                completed[key] = artifact.model_dump(mode="json")
                store.save_checkpoint(attempt.id, {"downloaded": dict(completed)})
            named[name].append(completed[key])
    if attempt.status != MediaTaskStatus.VALIDATING:
        store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)
    store.complete_success(
        attempt.id,
        {
            "named_artifacts": named,
            "artifacts": [a for values in named.values() for a in values],
        },
    )
