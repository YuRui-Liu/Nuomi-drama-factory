"""Atomic graph changes and input-guarded execution result projection."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from novelvideo.freezone.canvas_store import default_canvas_payload, read_canvas, save_canvas, utc_now_iso
from .graph import validate_graph
from .models import OrchestrationError, RESULT_FIELDS, fingerprint, input_fingerprint


def create_canvas(project_dir: Path, canvas_id: str, *, project_id: str, name: str, actor_id: str) -> dict:
    def build(existing):
        if existing is not None:
            raise OrchestrationError("canvas_exists", "Canvas already exists")
        payload = default_canvas_payload(project_id=project_id, actor_id=actor_id)
        payload.update(canvas_id=canvas_id, canvas_scope="default", name=name,
                       metadata={"name": name, "display_name": name})
        return payload
    save_canvas(project_dir, canvas_id, base_revision=None, build_payload=build,
                client_save_id="create", request_hash=fingerprint([project_id, name, actor_id]),
                save_source="orchestration")
    return read_canvas(project_dir, canvas_id)


def _existing(existing: dict | None) -> dict:
    if existing is None:
        raise OrchestrationError("canvas_not_found", "Canvas does not exist")
    return deepcopy(existing)


def _finish(payload: dict) -> dict:
    report = validate_graph(payload["nodes"], payload["edges"])
    if not report["valid"]:
        raise OrchestrationError("invalid_graph", "Graph patch is invalid", errors=report["errors"])
    payload["revision"] += 1
    payload["updated_at"] = utc_now_iso()
    payload["save_source"] = "orchestration"
    return payload


def patch_graph(project_dir: Path, canvas_id: str, *, base_revision: int, request_id: str,
                operations: list[dict]) -> dict:
    if not request_id.strip():
        raise OrchestrationError("request_id_required", "A stable request ID is required")

    def build(existing):
        payload = _existing(existing)
        nodes, edges = payload["nodes"], payload["edges"]
        for operation in operations:
            op = operation.get("op")
            if op == "add_node":
                nodes.append(deepcopy(operation["node"]))
            elif op == "add_edge":
                edges.append(deepcopy(operation["edge"]))
            elif op == "update_node":
                node = next((node for node in nodes if node["id"] == operation.get("node_id")), None)
                if node is None:
                    raise OrchestrationError("node_not_found", "Node does not exist")
                node.setdefault("data", {}).update(deepcopy(operation.get("data", {})))
                for field in ("position", "parentId", "style", "width", "height"):
                    if field in operation:
                        node[field] = deepcopy(operation[field])
            else:
                raise OrchestrationError("unknown_patch_operation", "Unsupported graph patch operation", operation=op)
        return _finish(payload)

    save_canvas(project_dir, canvas_id, base_revision=base_revision, build_payload=build,
                client_save_id=request_id, request_hash=fingerprint(operations), save_source="orchestration")
    return read_canvas(project_dir, canvas_id)


def project_result(project_dir: Path, canvas_id: str, *, node_id: str,
                   expected_input: str, attempt_id: str, result: dict) -> dict:
    def build(existing):
        payload = _existing(existing)
        node = next((node for node in payload["nodes"] if node["id"] == node_id), None)
        if node is None or input_fingerprint(node) != expected_input:
            raise OrchestrationError("input_conflict", "Node input changed while its task was running", node_id=node_id)
        allowed = RESULT_FIELDS.get(node["type"], set())
        if set(result) - allowed:
            raise OrchestrationError("invalid_result_patch", "Result contains fields outside the operation output", fields=sorted(set(result) - allowed))
        node["data"].update(deepcopy(result))
        node["data"]["orchestration"] = {"attempt_id": attempt_id, "status": "succeeded"}
        if node["type"] == "videoComposeNode" and result.get("resultVideoUrl"):
            # Logical output identity survives retry/resume and successive renders.
            output_id = "output-" + fingerprint([canvas_id, node_id, "video"])[:24]
            output = next((n for n in payload["nodes"] if n["id"] == output_id), None)
            if output is None:
                output = {"id": output_id, "type": "videoNode", "data": {},
                          "position": {"x": node.get("position", {}).get("x", 0) + 520,
                                       "y": node.get("position", {}).get("y", 0)}}
                payload["nodes"].append(output)
            output["data"].update(videoUrl=result["resultVideoUrl"], referenceOnly=True,
                                  displayName=output["data"].get("displayName", "成片"),
                                  orchestration={"source_node_id": node_id, "attempt_id": attempt_id})
            edge_id = "edge-" + output_id
            if not any(edge["id"] == edge_id for edge in payload["edges"]):
                payload["edges"].append({"id": edge_id, "source": node_id, "target": output_id,
                                         "sourceHandle": "source", "targetHandle": "target",
                                         "data": {"sourcePort": "video", "targetPort": "video"}})
        return _finish(payload)

    # The build callback runs under the existing canvas lock. Compare semantic
    # inputs there, instead of discarding unrelated edits via a stale full save.
    save_canvas(project_dir, canvas_id, base_revision=None, enforce_revision=False,
                build_payload=build, client_save_id="result-" + attempt_id,
                request_hash=fingerprint([node_id, expected_input, result]), save_source="orchestration")
    return read_canvas(project_dir, canvas_id)
