"""Inspect API graphs without executing nodes or changing connections."""
from __future__ import annotations
import copy
import hashlib
import json
from .models import WorkflowError, WorkflowManifest


def validate_graph(graph: object) -> dict:
    if not isinstance(graph, dict) or not graph or "nodes" in graph:
        raise WorkflowError("invalid_api_graph")
    for node_id, node in graph.items():
        if not isinstance(node_id, str) or not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise WorkflowError("invalid_node", str(node_id))
        for name, value in node["inputs"].items():
            if is_link(value) and value[0] not in graph:
                raise WorkflowError("broken_link", f"{node_id}.{name}")
    return graph


def is_link(value: object) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int and value[1] >= 0


def normalized_graph(graph: dict) -> dict:
    validate_graph(graph)
    return {key: {k: v for k, v in node.items() if k != "_meta"} for key, node in graph.items()}


def graph_digest(graph: dict) -> str:
    return hashlib.sha256(json.dumps(normalized_graph(graph), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def validate_bindings(manifest: WorkflowManifest, graph: dict) -> None:
    validate_graph(graph)
    for binding in manifest.bindings:
        for target in binding.targets:
            node = graph.get(target.node_id)
            if node is None or target.field not in node["inputs"]:
                raise WorkflowError("missing_target", f"{target.node_id}.{target.field}")
            if is_link(node["inputs"][target.field]):
                raise WorkflowError("linked_field", f"{target.node_id}.{target.field}")
    if any(o.node_id not in graph for o in manifest.outputs.values()):
        raise WorkflowError("missing_output_node")


def validate_deployed_graph(manifest: WorkflowManifest, local: dict, deployed: dict) -> None:
    validate_bindings(manifest, local)
    validate_bindings(manifest, deployed)
    left, right = copy.deepcopy(normalized_graph(local)), copy.deepcopy(normalized_graph(deployed))
    for b in manifest.bindings:
        for t in b.targets:
            left[t.node_id]["inputs"][t.field] = None
            right[t.node_id]["inputs"][t.field] = None
    if left != right:
        raise WorkflowError("graph_drift")


def inspect_graph(graph: dict) -> dict:
    validate_graph(graph)
    nodes = []
    for key, node in graph.items():
        fields = []
        for name, value in node["inputs"].items():
            fields.append({"field": name, "linked": is_link(value), "type": type(value).__name__})
        nodes.append({"node_id": key, "class_type": node["class_type"], "fields": fields})
    return {"nodes": nodes, "graph_sha256": graph_digest(graph)}
