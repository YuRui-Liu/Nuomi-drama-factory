"""Offline import and integrity-checked package loading."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .graph import graph_digest, inspect_graph, validate_bindings
from .models import WorkflowError, WorkflowManifest


def import_workflow(source_path: str | Path, *, identifier: str, workflow_id: str, out_dir: str | Path) -> dict:
    source, out = Path(source_path), Path(out_dir)
    raw = source.read_bytes()
    graph = json.loads(raw.decode("utf-8-sig"))
    report = inspect_graph(graph)
    draft = {"schema_version": 1, "id": identifier, "version": 1, "label": identifier,
             "provider": "runninghub", "workflow_id": workflow_id, "status": "draft",
             "source": {"path": "workflow_api.json", "sha256": hashlib.sha256(raw).hexdigest(), "graph_sha256": graph_digest(graph)},
             "inputs": {}, "bindings": [], "outputs": {}, "integrations": []}
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise WorkflowError("package_exists") from exc
    (out / "workflow_api.json").write_bytes(raw)
    (out / "manifest.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "draft", "path": str(out), **report}


def load_package(directory: str | Path) -> tuple[WorkflowManifest, dict]:
    root = Path(directory).resolve()
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if data.get("status") == "draft":
        raise WorkflowError("draft_package")
    manifest = WorkflowManifest.model_validate(data)
    source = (root / manifest.source.path).resolve()
    if not source.is_relative_to(root) or source.is_symlink() or not source.is_file():
        raise WorkflowError("source_path")
    raw = source.read_bytes()
    graph = json.loads(raw.decode("utf-8-sig"))
    if hashlib.sha256(raw).hexdigest() != manifest.source.sha256 or graph_digest(graph) != manifest.source.graph_sha256:
        raise WorkflowError("source_digest")
    validate_bindings(manifest, graph)
    return manifest, graph
