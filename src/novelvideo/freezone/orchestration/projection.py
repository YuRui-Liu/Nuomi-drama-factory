"""Atomic graph changes and input-guarded execution result projection."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from novelvideo.freezone.canvas_store import default_canvas_payload, read_canvas, save_canvas, utc_now_iso
from .graph import validate_graph
from .models import (OrchestrationError, RESULT_FIELDS, dependency_edge_fingerprint,
                     fingerprint, frozen_node_result, input_fingerprint,
                     media_projection, result_projection)


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
            elif op == "remove_edge":
                edge = next((edge for edge in edges if edge["id"] == operation.get("edge_id")), None)
                if edge is None:
                    raise OrchestrationError("edge_not_found", "Edge does not exist")
                edges.remove(edge)
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
                   expected_input: str, attempt_id: str, result: dict,
                   history_entry: dict | None = None,
                   expected_result_fingerprint: str | None = None,
                   expected_media_fingerprint: str | None = None,
                   expected_source_output_fingerprints: dict | None = None,
                   expected_dependency_edge_fingerprint: str | None = None,
                   authorize_projection=None) -> dict:
    def build(existing):
        payload = _existing(existing)
        node = next((node for node in payload["nodes"] if node["id"] == node_id), None)
        if node is None:
            raise OrchestrationError("input_conflict", "Node was removed while its task was running", node_id=node_id)
        current_result_fingerprint = fingerprint(result_projection(node))
        current_media_fingerprint = fingerprint(media_projection(node))
        current_media = {key: value for key, value in media_projection(node).items()
                         if key in {"imageUrl", "previewImageUrl", "videoUrl", "resultVideoUrl",
                                    "audioUrl", "plyUrl"}
                         and isinstance(value, str) and len(value) <= 4096}
        if input_fingerprint(node) != expected_input:
            raise OrchestrationError("input_conflict", "Node input changed while its task was running",
                                     node_id=node_id, current_result_fingerprint=current_result_fingerprint,
                                     current_media_fingerprint=current_media_fingerprint,
                                     current_media=current_media)
        if (expected_result_fingerprint is not None
                and current_result_fingerprint != expected_result_fingerprint):
            raise OrchestrationError("input_conflict", "Node output changed while its task was running",
                                     node_id=node_id, current_result_fingerprint=current_result_fingerprint,
                                     current_media_fingerprint=current_media_fingerprint,
                                     current_media=current_media)
        if (expected_media_fingerprint is not None
                and current_media_fingerprint != expected_media_fingerprint):
            raise OrchestrationError("input_conflict", "Node media changed while its task was running",
                                     node_id=node_id, current_result_fingerprint=current_result_fingerprint,
                                     current_media_fingerprint=current_media_fingerprint,
                                     current_media=current_media)
        if expected_dependency_edge_fingerprint is not None:
            actual_edge_fingerprint = dependency_edge_fingerprint(payload, node_id)
            if actual_edge_fingerprint != expected_dependency_edge_fingerprint:
                raise OrchestrationError("input_conflict", "Node dependency edges changed while its task was running",
                                         node_id=node_id, current_result_fingerprint=current_result_fingerprint,
                                         current_media_fingerprint=current_media_fingerprint,
                                         current_media=current_media)
        for source_id, expected_source_fingerprint in (expected_source_output_fingerprints or {}).items():
            source = next((candidate for candidate in payload["nodes"] if candidate["id"] == source_id), None)
            if source is None or fingerprint(frozen_node_result(source)) != expected_source_fingerprint:
                raise OrchestrationError("input_conflict", "A frozen upstream input changed while the task was running",
                                         node_id=source_id, current_result_fingerprint=current_result_fingerprint,
                                         current_media_fingerprint=current_media_fingerprint,
                                         current_media=current_media)
        if authorize_projection is not None:
            try:
                authorized = authorize_projection()
                if hasattr(authorized, "__await__"):
                    raise OrchestrationError("authorization_unavailable",
                                             "Canvas projection authorization must be synchronous")
            except OrchestrationError:
                raise
            except Exception as exc:
                raise OrchestrationError("authorization_revoked",
                                         "Agent execution authorization changed before projection") from exc
        allowed = RESULT_FIELDS.get(node["type"], set())
        if set(result) - allowed:
            raise OrchestrationError("invalid_result_patch", "Result contains fields outside the operation output", fields=sorted(set(result) - allowed))
        node["data"].update(deepcopy(result))
        previous_orchestration = node["data"].get("orchestration")
        history = deepcopy(previous_orchestration.get("result_history", [])) \
            if isinstance(previous_orchestration, dict) else []
        node["data"]["orchestration"] = {"attempt_id": attempt_id, "status": "succeeded"}
        if history_entry is not None:
            history.append(deepcopy(history_entry))
            node["data"]["orchestration"]["result_history"] = history[-25:]
            node["data"]["orchestration"]["projected_media_fingerprint"] = fingerprint(media_projection(node))
            node["data"]["orchestration"]["projected_result_fingerprint"] = fingerprint(result_projection(node))
        origin = node['data'].get('chatOrigin')
        if isinstance(origin, dict):
            from novelvideo.chat.canvas_context import node_fingerprint
            source = next((item for item in payload['nodes'] if item['id'] == origin.get('node_id')), None)
            changed = source is None or node_fingerprint(source) != origin.get('fingerprint')
            if changed:
                node['data']['orchestration'].update(
                    status='source_changed', source_node_id=origin.get('node_id'),
                    reason='source_deleted' if source is None else 'source_modified',
                    message='来源已变化，候选成果已保留，待选择放置位置')
                node['data']['displayName'] = str(node['data'].get('displayName') or node_id) + ' · 来源已变化，待选择放置位置'
            # Provenance remains in chatOrigin even if the visible source was deleted.
            # Remove only dangling provenance edges, never executable dependencies.
            ids = {item['id'] for item in payload['nodes']}
            payload['edges'] = [edge for edge in payload['edges'] if not (
                (edge.get('data') or {}).get('contextLink') and
                (edge.get('source') not in ids or edge.get('target') not in ids))]
        # Agent target-scoped projection may write only its granted compose
        # node; companion creation/update would silently widen that write.
        if node["type"] == "videoComposeNode" and result.get("resultVideoUrl") and history_entry is None:
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
