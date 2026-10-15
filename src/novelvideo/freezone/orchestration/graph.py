"""Validate persisted canvas graphs without executing any paid operations."""

from __future__ import annotations

from collections import Counter
from graphlib import CycleError, TopologicalSorter

from .capabilities import catalog_by_type
from .models import OrchestrationError


def validate_graph(nodes: list[dict], edges: list[dict]) -> dict:
    errors: list[dict] = []

    def error(code: str, **details) -> None:
        errors.append({"code": code, **details})

    catalog = catalog_by_type()
    by_id: dict[str, dict] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"].strip():
            error("invalid_node")
            continue
        key = node["id"]
        if key in by_id:
            error("duplicate_node", node_id=key)
        by_id[key] = node
        spec = catalog.get(node.get("type"))
        if spec is None:
            error("unknown_node_type", node_id=key)
            continue
        data = node.get("data", {})
        if not isinstance(data, dict):
            error("invalid_data", node_id=key)
            continue
        op = data.get("operation")
        if op and op not in {item["name"] for item in spec["operations"]}:
            error("unknown_operation", node_id=key, operation=op)
        if node["type"] == "skillNode" and data.get("skill_id") not in {s["id"] for s in spec["skills"]}:
            error("unknown_skill", node_id=key)

    parents = {}
    for key, node in by_id.items():
        parent = node.get("parentId")
        parents[key] = {parent} if parent in by_id else set()
        if parent and (parent not in by_id or by_id[parent].get("type") != "groupNode"):
            error("invalid_parent", node_id=key)
    try:
        tuple(TopologicalSorter(parents).static_order())
    except CycleError:
        error("parent_cycle")

    dependencies: dict[str, set[str]] = {key: set() for key in by_id}
    edge_ids: set[str] = set()
    skill_roles: Counter = Counter()
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
            error("invalid_edge")
            continue
        edge_id = edge["id"]
        if edge_id in edge_ids:
            error("duplicate_edge", edge_id=edge_id)
        edge_ids.add(edge_id)
        source, target = edge.get("source"), edge.get("target")
        if source not in by_id or target not in by_id:
            error("missing_endpoint", edge_id=edge_id)
            continue
        if (edge.get("data") or {}).get("contextLink"):
            continue
        dependencies[target].add(source)
        src = catalog.get(by_id[source].get("type"))
        dst = catalog.get(by_id[target].get("type"))
        if not src or not dst:
            continue
        output_types = {port["name"] for port in src["outputs"]}
        input_types = {port["name"] for port in dst["inputs"]}
        ports = edge.get("data") or {}
        source_port = ports.get("sourcePort", edge.get("sourceHandle"))
        target_port = ports.get("targetPort", edge.get("targetHandle"))
        # Existing ReactFlow default handles carry no semantic type.
        if source_port not in (None, "", "source"):
            if source_port not in output_types:
                error("unknown_port", edge_id=edge_id, port=source_port)
            output_types &= {source_port}
        if dst["node_type"] == "skillNode":
            data = by_id[target].get("data") or {}
            skill = next((s for s in dst["skills"] if s["id"] == data.get("skill_id")), None)
            roles = {s["role"]: s for s in skill["inputs"]} if skill else {}
            if target_port in roles:
                role = roles[target_port]
                skill_roles[(target, target_port)] += 1
                accepted = role["accepts"].get("node_types", [])
                if accepted and by_id[source].get("type") not in accepted:
                    error("incompatible_port", edge_id=edge_id)
                if role["cardinality"] == "single" and skill_roles[(target, target_port)] > 1:
                    error("port_cardinality", edge_id=edge_id)
                target_port = None
        if target_port not in (None, "", "target"):
            if target_port not in input_types:
                error("unknown_port", edge_id=edge_id, port=target_port)
            input_types &= {target_port}
        if not output_types.intersection(input_types):
            error("incompatible_port", edge_id=edge_id)
    try:
        order = list(TopologicalSorter(dependencies).static_order())
    except CycleError:
        error("cycle")
        order = []
    return {"valid": not errors, "errors": errors, "order": order}


def execution_order(nodes: list[dict], edges: list[dict], target: str) -> list[str]:
    report = validate_graph(nodes, edges)
    if not report["valid"]:
        raise OrchestrationError("invalid_graph", "Canvas graph is invalid", errors=report["errors"])
    if target not in report["order"]:
        raise OrchestrationError("unknown_target", "Target node does not exist", node_id=target)
    dependencies = {n["id"]: set() for n in nodes}
    for edge in edges:
        if not (edge.get("data") or {}).get("contextLink"):
            dependencies[edge["target"]].add(edge["source"])
    selected: set[str] = set()
    pending = [target]
    while pending:
        node_id = pending.pop()
        if node_id not in selected:
            selected.add(node_id)
            pending.extend(dependencies[node_id])
    return [node_id for node_id in report["order"] if node_id in selected]
