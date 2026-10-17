"""Bounded, grant-checked execution and field edits for existing canvas nodes.

This service is an orchestration adapter: it journals requests and delegates
real work to the existing RunStore, Scheduler and NodeAdapter. It does not
create provider clients or execute upstream/downstream nodes implicitly.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any

from novelvideo.freezone.canvas_store import read_canvas, save_canvas
from novelvideo.freezone.orchestration.adapters import NodeAdapter, available_operations
from novelvideo.freezone.orchestration.capabilities import catalog_by_type
from novelvideo.freezone.orchestration.graph import execution_order
from novelvideo.freezone.orchestration.models import (
    OrchestrationError,
    RESULT_FIELDS,
    dependency_edge_fingerprint,
    fingerprint,
    frozen_node_result,
    input_fingerprint,
    media_projection,
    result_projection,
)
from novelvideo.freezone.orchestration.projection import _existing, _finish
from novelvideo.freezone.orchestration.scheduler import Scheduler
from novelvideo.freezone.orchestration.store import RunStore

from .operations import OperationError


_MAX_REQUEST_ID = 128
_MAX_PATCHES = 100
_MAX_PATCH_BYTES = 128_000
_MAX_READ_NODES = 1000
_CAMERA_FIELDS = {"cameraBodyId", "lensId", "focalLengthMm", "aperture"}
_PANEL_FIELDS = {"state", "framing", "setting", "lighting", "visible_text"}
_SUBJECT_FIELDS = {"screen_position", "pose", "gaze"}
_FRAME_TEXT_FIELDS = {"description", "static_source_description"}
_VOICE_SPEC_FIELDS = {
    "age_impression", "pitch", "texture", "resonance", "articulation",
    "pace", "accent", "emotional_baseline", "negative_constraints",
}
_VOICE_PERFORMANCE_FIELDS = {"intention", "emotion", "emphasis", "pauses", "delivery_scale"}
_DUBBING_PERFORMANCE_FIELDS = {"mode", "direction", "payload"}
_VOICE_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_MEDIA_FIELDS = {
    "imageUrl", "previewImageUrl", "videoUrl", "resultVideoUrl", "audioUrl", "plyUrl",
}


def _invalid(code: str, message: str, **details: Any):
    raise OperationError(code, message, **details)


def _transition(value: dict) -> dict:
    required = {"agent_run_id", "step_id", "action_id", "action_index"}
    if not isinstance(value, dict) or set(value) != required:
        _invalid("invalid_transition", "Accepted Agent action identity is required")
    result = deepcopy(value)
    for field in ("agent_run_id", "step_id", "action_id"):
        if not isinstance(result[field], str) or not result[field] or len(result[field]) > _MAX_REQUEST_ID:
            _invalid("invalid_transition", "Accepted Agent action identity is invalid", field=field)
    if type(result["action_index"]) is not int or result["action_index"] < 0:
        _invalid("invalid_transition", "Accepted Agent action index is invalid")
    return result


def _contract(contract: dict, *, project_id: str, canvas_id: str, actor: dict) -> dict:
    if not isinstance(contract, dict):
        _invalid("authorization_required", "A trusted frozen node authorization is required")
    if contract.get("project_id", project_id) != project_id:
        _invalid("scope_violation", "Node authorization belongs to another project")
    scope = contract.get("scope")
    scoped_canvas = contract.get("canvas_id") or (scope.get("canvas_id") if isinstance(scope, dict) else None)
    if scoped_canvas is not None and scoped_canvas != canvas_id:
        _invalid("scope_violation", "Node authorization belongs to another canvas")
    if contract.get("owner_id") is not None and str(contract["owner_id"]) != str(actor.get("id")):
        _invalid("scope_violation", "Node authorization belongs to another actor")
    delegation_id = contract.get("delegation_id")
    root_delegation_id = contract.get("root_delegation_id") or delegation_id
    authorization_id = contract.get("authorization_id")
    for name, value in (("delegation_id", delegation_id), ("root_delegation_id", root_delegation_id),
                        ("authorization_id", authorization_id)):
        if not isinstance(value, str) or not value or len(value) > 128:
            _invalid("authorization_required", "Node authorization identity is incomplete", field=name)

    grant = contract.get("execution_authorization")
    if grant is None:
        grant = contract
    if not isinstance(grant, dict):
        _invalid("authorization_required", "Node execution grant is missing")
    allowed_node_ids = grant.get("allowed_node_ids")
    operations = grant.get("allowed_node_operations")
    read_node_ids = contract.get("read_node_ids", [])
    raw_budget_chain = contract.get("node_run_budget_chain", grant.get("node_run_budget_chain"))
    max_node_runs = grant.get("max_node_runs")
    root_max_node_runs = grant.get("root_max_node_runs", contract.get("root_max_node_runs"))
    if (not isinstance(allowed_node_ids, list) or not allowed_node_ids or len(allowed_node_ids) > _MAX_READ_NODES
            or any(not isinstance(value, str) or not value for value in allowed_node_ids)
            or len(allowed_node_ids) != len(set(allowed_node_ids))):
        _invalid("authorization_required", "Writable node scope is invalid")
    if (not isinstance(read_node_ids, list) or len(read_node_ids) > _MAX_READ_NODES
            or any(not isinstance(value, str) or not value for value in read_node_ids)
            or len(read_node_ids) != len(set(read_node_ids))):
        _invalid("authorization_required", "Readable dependency scope is invalid")
    if not isinstance(operations, list) or not operations or len(operations) > _MAX_READ_NODES:
        _invalid("authorization_required", "Node operation grant is missing")
    pairs = []
    for entry in operations:
        if not isinstance(entry, dict) or set(entry) != {"node_id", "operation"}:
            _invalid("authorization_required", "Node operation grant is malformed")
        node_id, operation = entry["node_id"], entry["operation"]
        if (not isinstance(node_id, str) or not node_id or not isinstance(operation, str)
                or not operation or node_id not in allowed_node_ids):
            _invalid("authorization_required", "Node operation grant is outside writable scope")
        pairs.append((node_id, operation))
    if len(pairs) != len(set(pairs)):
        _invalid("authorization_required", "Node operation grant contains duplicates")
    if type(max_node_runs) is not int or max_node_runs < 0:
        _invalid("authorization_required", "Node run count grant is invalid")
    from .delegations import MAX_NODE_RUNS
    if max_node_runs > MAX_NODE_RUNS:
        _invalid("authorization_required", "Node run count grant exceeds the server limit")
    if raw_budget_chain is None:
        if root_delegation_id != delegation_id:
            _invalid("authorization_required", "Nested node run budget chain is required")
        raw_budget_chain = [{"delegation_id": root_delegation_id, "max_node_runs": max_node_runs}]
    if not isinstance(raw_budget_chain, list) or not raw_budget_chain or len(raw_budget_chain) > 32:
        _invalid("authorization_required", "Node run budget chain is invalid")
    budget_chain = []
    for item in raw_budget_chain:
        if not isinstance(item, dict) or set(item) != {"delegation_id", "max_node_runs"}:
            _invalid("authorization_required", "Node run budget chain entry is malformed")
        item_id, item_limit = item["delegation_id"], item["max_node_runs"]
        if (not isinstance(item_id, str) or not item_id or len(item_id) > 128
                or type(item_limit) is not int or not 0 <= item_limit <= MAX_NODE_RUNS):
            _invalid("authorization_required", "Node run budget chain entry is invalid")
        budget_chain.append({"delegation_id": item_id, "max_node_runs": item_limit})
    budget_ids = [item["delegation_id"] for item in budget_chain]
    if (len(budget_ids) != len(set(budget_ids)) or budget_ids[0] != root_delegation_id
            or budget_ids[-1] != delegation_id
            or budget_chain[-1]["max_node_runs"] != max_node_runs):
        _invalid("authorization_required", "Node run budget chain does not match delegation scope")
    root_max_node_runs = budget_chain[0]["max_node_runs"]
    if grant.get("root_max_node_runs", contract.get("root_max_node_runs")) is not None:
        stated_root_limit = grant.get("root_max_node_runs", contract.get("root_max_node_runs"))
        if type(stated_root_limit) is not int or stated_root_limit != root_max_node_runs:
            _invalid("authorization_required", "Root run limit does not match budget chain")
    if any(operation != "patch" for _, operation in pairs) and max_node_runs < 1:
        _invalid("authorization_required", "Node generation requires a positive run count grant")
    return {
        "delegation_id": delegation_id,
        "root_delegation_id": root_delegation_id,
        "authorization_id": authorization_id,
        "allowed_node_ids": list(allowed_node_ids),
        "read_node_ids": list(read_node_ids),
        "allowed_node_operations": [{"node_id": node_id, "operation": operation}
                                    for node_id, operation in pairs],
        "max_node_runs": max_node_runs,
        "root_max_node_runs": root_max_node_runs,
        "node_run_budget_chain": budget_chain,
        "fingerprint": fingerprint({
            "project_id": project_id, "canvas_id": canvas_id, "actor_id": str(actor.get("id")),
            "delegation_id": delegation_id, "root_delegation_id": root_delegation_id,
            "authorization_id": authorization_id,
            "allowed_node_ids": sorted(allowed_node_ids),
            "read_node_ids": sorted(read_node_ids),
            "allowed_node_operations": sorted(pairs), "max_node_runs": max_node_runs,
            "root_max_node_runs": root_max_node_runs, "node_run_budget_chain": budget_chain,
        }),
    }


def _require_pair(grant: dict, node_id: str, operation: str) -> None:
    if node_id not in grant["allowed_node_ids"]:
        _invalid("scope_violation", "Node target is outside writable scope", node_id=node_id)
    if not any(item == {"node_id": node_id, "operation": operation}
               for item in grant["allowed_node_operations"]):
        _invalid("capability_denied", "This exact node operation is not authorized",
                 node_id=node_id, operation=operation)


def _node(canvas: dict, node_id: str) -> dict:
    value = next((item for item in canvas.get("nodes", []) if item.get("id") == node_id), None)
    if value is None:
        _invalid("scope_violation", "Authorized node no longer exists", node_id=node_id)
    return value


def _check_revision(expected_revision: int, canvas: dict) -> None:
    if type(expected_revision) is not int or expected_revision < 1:
        _invalid("invalid_contract", "A positive expected canvas revision is required")
    if expected_revision > canvas.get("revision", 0):
        _invalid("stale_revision", "Expected canvas revision is newer than the saved canvas",
                 expected_revision=expected_revision, current_revision=canvas.get("revision"))


def _validate_change(node: dict, path: list, value: Any) -> tuple:
    if not isinstance(path, list) or not path:
        _invalid("invalid_patch", "A field path is required")
    if any(type(item) not in {str, int} for item in path):
        _invalid("invalid_patch", "Field paths may contain only field names and array indexes")
    kind = node.get("type")
    if kind == "videoComposeNode":
        from .timeline_inputs import validate_timeline_patch

        validated = validate_timeline_patch(node, path, value)
        if validated is not None:
            return validated
    if kind == "directorWorkspaceNode" and path == ["directorOperationInputs"]:
        _validate_director_operation_inputs(node, value)
        return tuple(path)
    if kind == "directorWorkspaceNode" and path == ["productionGroupIds"]:
        _validate_production_group_ids(value)
        return tuple(path)
    if kind == "directorWorkspaceNode" and path == ["handoffSelection"]:
        _validate_director_handoff_selection(value)
        return tuple(path)
    if kind == "screenplayNode" and path == ["screenplayOperationInputs"]:
        _validate_screenplay_operation_inputs(value)
        return tuple(path)
    if kind == "characterVoiceNode":
        _validate_character_voice_patch(node, path, value)
        return tuple(path)
    if kind == "audioNode" and len(path) == 3 and path[0] == "dubbingPerformanceDrafts":
        _validate_dubbing_performance_patch(node, path, value)
        return tuple(path)
    if kind == "audioNode" and len(path) == 2 and path[0] == "auditionDraft":
        _validate_audio_audition_patch(path[1], value)
        return tuple(path)
    if len(path) == 1 and path[0] in {"prompt", "negativePrompt"}:
        if not isinstance(value, str) or len(value) > 100_000:
            _invalid("invalid_patch", "Prompt fields must be bounded text")
        return tuple(path)
    if (kind in {"imageNode", "imageGenNode", "storyboardGenNode"}
            and len(path) == 2 and path[0] == "cameraSelection" and path[1] in _CAMERA_FIELDS):
        field = path[1]
        if field == "focalLengthMm":
            if type(value) not in {int, float} or not math.isfinite(value) or not 1 <= value <= 1200:
                _invalid("invalid_patch", "Camera focal length is invalid")
        elif not isinstance(value, str) or len(value) > 200:
            _invalid("invalid_patch", "Camera selection fields must be bounded text")
        return tuple(path)
    if kind == "storyboardGenNode" and len(path) == 3 and path[0] == "frames" and type(path[1]) is int:
        if path[1] < 0 or path[1] >= 1000 or path[2] not in _FRAME_TEXT_FIELDS:
            _invalid("invalid_patch", "Storyboard frame field is outside the editable field set")
        if not isinstance(value, str) or len(value) > 12_000:
            _invalid("invalid_patch", "Storyboard frame text is invalid")
        return tuple(path)
    if (kind == "storyboardGenNode" and len(path) == 4 and path[0] == "frames"
            and type(path[1]) is int and path[2] == "static_panel" and path[3] in _PANEL_FIELDS):
        if path[1] < 0 or path[1] >= 1000:
            _invalid("invalid_patch", "Storyboard frame index is invalid")
        if path[3] == "visible_text":
            if (not isinstance(value, list) or len(value) > 100
                    or any(not isinstance(item, str) or len(item) > 1000 for item in value)):
                _invalid("invalid_patch", "Panel visible text is invalid")
        elif not isinstance(value, str) or len(value) > 4000:
            _invalid("invalid_patch", "Static panel text is invalid")
        return tuple(path)
    if (kind == "storyboardGenNode" and len(path) == 6 and path[0] == "frames"
            and type(path[1]) is int and path[2] == "static_panel" and path[3] == "subjects"
            and type(path[4]) is int and path[5] in _SUBJECT_FIELDS):
        if path[1] < 0 or path[1] >= 1000 or path[4] < 0 or path[4] >= 100:
            _invalid("invalid_patch", "Storyboard subject index is invalid")
        if not isinstance(value, str) or len(value) > 2000:
            _invalid("invalid_patch", "Storyboard subject text is invalid")
        return tuple(path)
    _invalid("invalid_patch", "Field is not in the node editor allowlist", path=path)


def _validate_director_operation_inputs(node: dict, value: Any) -> None:
    if not isinstance(value, dict):
        _invalid("invalid_patch", "directorOperationInputs must be an operation-specific object")
    operation = value.get("operation")
    if operation not in {"save_blocking", "compile_blocking", "edit_shot", "edit_structure"}:
        _invalid("invalid_patch", "directorOperationInputs has an unsupported operation")
    data = node.get("data") or {}
    bound_revision = data.get("directorRevisionId")
    if not isinstance(bound_revision, str) or not bound_revision.strip():
        _invalid("invalid_patch", "Director node must be bound to directorRevisionId before configuring an operation")

    if operation == "save_blocking":
        allowed = {"operation", "sceneId", "groupIds", "baseVersion", "blocking"}
        if set(value) != allowed:
            _invalid("invalid_patch", "save_blocking inputs must contain only sceneId, groupIds, baseVersion, and blocking")
        scene_id = value.get("sceneId")
        group_ids = value.get("groupIds")
        base_version = value.get("baseVersion")
        if not isinstance(scene_id, str) or not scene_id.strip() or len(scene_id) > 200:
            _invalid("invalid_patch", "save_blocking sceneId is invalid")
        if (not isinstance(group_ids, list) or not group_ids or len(group_ids) > 500
                or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in group_ids)
                or len(group_ids) != len(set(group_ids))):
            _invalid("invalid_patch", "save_blocking groupIds must be unique bounded group IDs")
        if (base_version is not None
                and not (isinstance(base_version, str) and base_version.strip() and len(base_version) <= 128)
                and not (type(base_version) is int and base_version >= 0)):
            _invalid("invalid_patch", "save_blocking baseVersion must be a revision ID, non-negative version, or null")
        try:
            from novelvideo.director_plan.workspace import SceneBlocking

            SceneBlocking.model_validate(value.get("blocking"))
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_patch", "save_blocking blocking failed validation") from exc
        return

    if operation == "compile_blocking":
        if set(value) != {"operation", "baseRevisionId"}:
            _invalid("invalid_patch", "compile_blocking inputs must contain only baseRevisionId")
        if value.get("baseRevisionId") != bound_revision:
            _invalid("invalid_patch", "compile_blocking baseRevisionId must match the node-bound directorRevisionId")
        return

    if operation == "edit_structure":
        _validate_director_structure_inputs(value, bound_revision)
        return

    from novelvideo.director_plan.models import UpdateShot

    update_fields = set(UpdateShot.model_fields) - {"kind", "shot_id"}
    allowed = {"operation", "revisionId", "groupId", "shotId"} | update_fields
    if set(value) - allowed:
        _invalid("invalid_patch", "edit_shot inputs contain fields outside the UpdateShot schema")
    if value.get("revisionId") != bound_revision:
        _invalid("invalid_patch", "edit_shot revisionId must match the node-bound directorRevisionId")
    if any(not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > 128
           for field in ("groupId", "shotId")):
        _invalid("invalid_patch", "edit_shot requires bounded groupId and shotId")
    changes = {field: value[field] for field in update_fields if field in value}
    if not changes:
        _invalid("invalid_patch", "edit_shot requires at least one UpdateShot field")
    try:
        UpdateShot.model_validate({"kind": "update_shot", "shot_id": value["shotId"], **changes})
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_patch", "edit_shot fields failed UpdateShot validation") from exc


def _safe_path_segment(value: Any, *, label: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > 128
            or value in {".", ".."} or any(char in value for char in ("/", "\\", "%", "?", "#"))
            or any(ord(char) < 32 for char in value)):
        _invalid("invalid_patch", f"{label} is not a safe ID")
    return value


def _validate_production_group_ids(value: Any) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        _invalid("invalid_patch", "productionGroupIds must contain between 1 and 50 group IDs")
    safe = [_safe_path_segment(item, label="production group ID") for item in value]
    if len(safe) != len(set(safe)):
        _invalid("invalid_patch", "productionGroupIds must be unique")


def _validate_director_handoff_selection(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"expected_source_project_revision", "update_scope"}:
        _invalid("invalid_patch", "handoffSelection must contain only the source revision and typed update scope")
    source_revision = value.get("expected_source_project_revision")
    if type(source_revision) is not int or source_revision < 0:
        _invalid("invalid_patch", "handoffSelection source project revision must be a non-negative integer")
    scope = value.get("update_scope")
    if not isinstance(scope, dict) or set(scope) != {"mode", "scene_ids"}:
        _invalid("invalid_patch", "handoffSelection update_scope must contain only mode and scene_ids")
    mode = scope.get("mode")
    scene_ids = scope.get("scene_ids")
    if mode not in {"none", "all", "selected"}:
        _invalid("invalid_patch", "handoffSelection mode is unsupported")
    if not isinstance(scene_ids, list) or len(scene_ids) > 1000:
        _invalid("invalid_patch", "handoffSelection scene_ids must be a bounded list")
    safe_scene_ids = [_safe_path_segment(item, label="handoff scene ID") for item in scene_ids]
    if len(safe_scene_ids) != len(set(safe_scene_ids)):
        _invalid("invalid_patch", "handoffSelection scene_ids must be unique")
    if (mode == "selected" and not safe_scene_ids) or (mode != "selected" and safe_scene_ids):
        _invalid("invalid_patch", "handoffSelection scene_ids must be non-empty only for selected scope")


def _validate_director_structure_inputs(value: dict, bound_revision: str) -> None:
    if set(value) != {"operation", "revisionId", "groupIds", "command"}:
        _invalid("invalid_patch", "edit_structure inputs must contain only revisionId, groupIds, and command")
    if value.get("revisionId") != bound_revision:
        _invalid("invalid_patch", "edit_structure revisionId must match the node-bound directorRevisionId")
    group_ids = value.get("groupIds")
    if not isinstance(group_ids, list) or not 1 <= len(group_ids) <= 50:
        _invalid("invalid_patch", "edit_structure groupIds must contain between 1 and 50 group IDs")
    safe_groups = [_safe_path_segment(item, label="edit_structure group ID") for item in group_ids]
    if len(safe_groups) != len(set(safe_groups)):
        _invalid("invalid_patch", "edit_structure groupIds must be unique")

    command = value.get("command")
    if not isinstance(command, dict):
        _invalid("invalid_patch", "edit_structure command must be a typed command object")
    kind = command.get("kind")
    shapes = {
        "add_shot_from_template": {"kind", "group_id", "template_shot_id"},
        "delete_shot": {"kind", "shot_id"},
        "reorder_shots": {"kind", "group_id", "shot_ids"},
        "split_group": {"kind", "group_id", "before_shot_id"},
        "merge_adjacent_groups": {"kind", "left_group_id", "right_group_id"},
    }
    required = shapes.get(kind)
    if required is None or set(command) != required:
        _invalid("invalid_patch", "edit_structure command has an unsupported kind or fields")

    referenced_groups: list[str] = []
    for field in ("group_id", "left_group_id", "right_group_id"):
        if field in command:
            referenced_groups.append(_safe_path_segment(command[field], label=f"command {field}"))
    if any(item not in safe_groups for item in referenced_groups):
        _invalid("invalid_patch", "edit_structure command is outside the declared groupIds scope")
    for field in ("template_shot_id", "shot_id", "before_shot_id"):
        if field in command:
            _safe_path_segment(command[field], label=f"command {field}")
    if kind == "merge_adjacent_groups" and command["left_group_id"] == command["right_group_id"]:
        _invalid("invalid_patch", "merge_adjacent_groups requires two different groups")
    if kind == "reorder_shots":
        shot_ids = command.get("shot_ids")
        if (not isinstance(shot_ids, list) or not shot_ids or len(shot_ids) > 1000
                or any(not isinstance(item, str) for item in shot_ids)):
            _invalid("invalid_patch", "reorder_shots shot_ids must be a non-empty unique list")
        safe_shots = [_safe_path_segment(item, label="reorder_shots shot ID") for item in shot_ids]
        if len(safe_shots) != len(set(safe_shots)):
            _invalid("invalid_patch", "reorder_shots shot_ids must be unique")


def _validate_screenplay_operation_inputs(value: Any) -> None:
    if not isinstance(value, dict):
        _invalid("invalid_patch", "screenplayOperationInputs must be an operation-specific object")
    operation = value.get("operation")
    if operation == "review":
        if set(value) != {"operation"}:
            _invalid("invalid_patch", "review inputs may contain only operation")
        return
    if operation != "rewrite":
        _invalid("invalid_patch", "screenplayOperationInputs has an unsupported operation")
    if set(value) != {"operation", "start", "end", "scope", "mode", "instruction", "preserve"}:
        _invalid("invalid_patch", "rewrite inputs contain unsupported or missing fields")
    start, end = value.get("start"), value.get("end")
    if type(start) is not int or start < 0 or type(end) is not int or end <= start:
        _invalid("invalid_patch", "rewrite start/end must be non-negative integer offsets with end greater than start")
    if value.get("scope") not in {"selection", "scene", "episode"}:
        _invalid("invalid_patch", "rewrite scope is unsupported")
    if value.get("mode") not in {"dialogue", "subtext", "conflict", "compress", "custom"}:
        _invalid("invalid_patch", "rewrite mode is unsupported")
    instruction, preserve = value.get("instruction"), value.get("preserve")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 8000:
        _invalid("invalid_patch", "rewrite instruction must be non-empty text up to 8000 characters")
    if not isinstance(preserve, str) or len(preserve) > 4000:
        _invalid("invalid_patch", "rewrite preserve must be text up to 4000 characters")


def _validate_character_voice_patch(node: dict, path: list, value: Any) -> None:
    if path == ["voiceDescriptionDraft"]:
        if not isinstance(value, str) or len(value) > 8000 or _VOICE_URL.search(value):
            _invalid("invalid_patch", "voiceDescriptionDraft must be bounded descriptive text without URLs")
        return
    if (len(path) == 3 and path[0] == "voiceDesign" and path[1] == "voice_spec"
            and path[2] in _VOICE_SPEC_FIELDS):
        if path[2] == "negative_constraints":
            if (not isinstance(value, list) or len(value) > 32
                    or any(not isinstance(item, str) or len(item) > 500 or _VOICE_URL.search(item) for item in value)):
                _invalid("invalid_patch", "voiceDesign negative_constraints must be a bounded list of text")
        elif not isinstance(value, str) or len(value) > 1200 or _VOICE_URL.search(value):
            _invalid("invalid_patch", "voiceDesign stable voice fields must be bounded text without URLs")
    elif (len(path) == 3 and path[0] == "voiceDesign" and path[1] == "performance"
          and path[2] in _VOICE_PERFORMANCE_FIELDS):
        if not isinstance(value, str) or len(value) > 1200 or _VOICE_URL.search(value):
            _invalid("invalid_patch", "voiceDesign performance fields must be bounded text without URLs")
    elif path == ["voiceDesign", "audition_text"]:
        if not isinstance(value, str) or len(value) > 200 or _VOICE_URL.search(value):
            _invalid("invalid_patch", "voiceDesign audition_text must be bounded text without URLs")
    elif path == ["voiceDesign", "rationale"]:
        if not isinstance(value, str) or len(value) > 1200 or _VOICE_URL.search(value):
            _invalid("invalid_patch", "voiceDesign rationale must be bounded text without URLs")
    else:
        _invalid("invalid_patch", "Field is not in the character voice editor allowlist", path=path)

    data = node.get("data") or {}
    base = data.get("voiceDesign")
    design = deepcopy(base) if isinstance(base, dict) else {"voice_spec": {}, "performance": {}}
    if path[0] == "voiceDesign":
        cursor = design
        for item in path[1:-1]:
            nested = cursor.get(item)
            if not isinstance(nested, dict):
                nested = {}
                cursor[item] = nested
            cursor = nested
        cursor[path[-1]] = deepcopy(value)
        from novelvideo.media_capabilities.tts.models import CharacterVoiceDesign
        from novelvideo.media_capabilities.tts.voice_prompt import compile_character_voice_design

        try:
            validated = CharacterVoiceDesign.model_validate({
                "voice_spec": design.get("voice_spec") or {},
                "performance": design.get("performance") or {},
                "audition_text": design.get("audition_text", ""),
                "audition_source": design.get("audition_source", "example"),
                "rationale": design.get("rationale", ""),
            })
            compile_character_voice_design(validated)
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_patch", "voiceDesign failed compiler validation") from exc


def _validate_dubbing_performance_patch(node: dict, path: list, value: Any) -> None:
    field = path[2]
    if field not in _DUBBING_PERFORMANCE_FIELDS:
        _invalid("invalid_patch", "Dubbing performance field is outside the editor allowlist", path=path)
    data = node.get("data") or {}
    episode = data.get("episodeNumber")
    group_id = data.get("groupId")
    if type(episode) is not int or episode < 1 or not isinstance(group_id, str) or not group_id.strip():
        _invalid("invalid_patch", "Dubbing node must be bound to the current episode and group before editing performance")
    group_id = _safe_path_segment(group_id, label="dubbing group ID")
    draft_key = path[1]
    dialogue_ids = data.get("dialogueIds")
    if (not isinstance(draft_key, str) or not isinstance(dialogue_ids, list)
            or any(not isinstance(item, str) for item in dialogue_ids)):
        _invalid("invalid_patch", "Dubbing performance edits require the current dialogue ID list")
    prefix = f"{episode}:{group_id}:"
    dialogue_id = draft_key[len(prefix):] if draft_key.startswith(prefix) else ""
    if not dialogue_id or dialogue_id not in dialogue_ids:
        _invalid("invalid_patch", "Dubbing performance key must identify an existing dialogue in this episode and group")

    if field == "mode":
        if not isinstance(value, str) or value not in {"neutral", "emotion_text"}:
            _invalid("invalid_patch", "Only IndexTTS2 neutral and text emotion modes are supported")
    else:
        limit = 2000 if field == "direction" else 1000
        if not isinstance(value, str) or len(value) > limit or _VOICE_URL.search(value):
            _invalid("invalid_patch", "Dubbing performance text must be bounded text without URLs")
        if field == "payload" and value.strip():
            try:
                from novelvideo.media_capabilities.tts.performance import compile_performance

                compile_performance(
                    text="临时试听文本", direction={"director_note": ""}, mode="emotion_text",
                    payload=value, supported_modes=("neutral", "emotion_text"), provider="indextts2",
                )
            except (TypeError, ValueError) as exc:
                raise OperationError("invalid_patch", "Dubbing emotion payload failed IndexTTS2 validation") from exc


def _validate_audio_audition_patch(field: Any, value: Any) -> None:
    if field == "voiceVersionId":
        _safe_path_segment(value, label="auditionDraft voiceVersionId")
        return
    limits = {"text": 10_000, "emotionPrompt": 2_000}
    if field not in limits or not isinstance(value, str) or len(value) > limits[field] or _VOICE_URL.search(value):
        _invalid("invalid_patch", "auditionDraft accepts only bounded text, emotionPrompt, and voiceVersionId")


def _get_path(node: dict, path: tuple) -> Any:
    current: Any = node.get("data", {})
    for item in path:
        if isinstance(current, list) and type(item) is int and 0 <= item < len(current):
            current = current[item]
        elif isinstance(current, dict) and isinstance(item, str) and item in current:
            current = current[item]
        else:
            return None
    return deepcopy(current)


def _set_path(node: dict, path: tuple, value: Any) -> None:
    current: Any = node.setdefault("data", {})
    for item in path[:-1]:
        if path[0] == "cameraSelection" and item == "cameraSelection" and item not in current:
            current[item] = {}
        if (path[0] in {"voiceDesign", "dubbingPerformanceDrafts", "auditionDraft"}
                and isinstance(current, dict) and isinstance(item, str) and item not in current):
            current[item] = {}
        if isinstance(current, list) and type(item) is int and 0 <= item < len(current):
            current = current[item]
        elif isinstance(current, dict) and isinstance(item, str) and item in current:
            current = current[item]
        else:
            _invalid("invalid_patch", "Field path does not resolve to an existing structured field", path=list(path))
    leaf = path[-1]
    if not isinstance(current, dict):
        _invalid("invalid_patch", "Field path does not resolve to an editable object", path=list(path))
    current[leaf] = deepcopy(value)


def _path_key(path: tuple) -> str:
    return fingerprint(list(path))


def _media_evidence(node: dict) -> list[dict]:
    data = node.get("data", {})
    fields = _MEDIA_FIELDS
    return [{"field": key, "url": data[key]} for key in sorted(fields)
            if key in data and isinstance(data[key], str) and 0 < len(data[key]) <= 4096]


def _result_fields(node: dict) -> dict:
    values = result_projection(node)
    return {key: deepcopy(value) for key, value in values.items()
            if key in _MEDIA_FIELDS and isinstance(value, str) and len(value) <= 4096}


class NodeExecutionService:
    """Scoped facade over the existing canvas orchestration journal."""

    def __init__(self, project_dir: Path, *, project_id: str, canvas_id: str,
                 actor: dict, adapter=None, authorize_submission=None,
                 authorize_projection=None):
        self.project_dir = Path(project_dir)
        self.project_id = str(project_id)
        self.canvas_id = str(canvas_id)
        self.actor = {key: actor[key] for key in ("id", "username") if key in actor}
        if "id" not in self.actor:
            _invalid("authorization_required", "A scoped actor identity is required")
        self.store = RunStore(self.project_dir)
        self.adapter = adapter or NodeAdapter(self.project_dir)
        self.scheduler = Scheduler(self.store, self.adapter,
                                   authorize_submission=authorize_submission,
                                   authorize_projection=authorize_projection)

    def _canvas(self) -> dict:
        try:
            canvas = read_canvas(self.project_dir, self.canvas_id)
        except ValueError as exc:
            raise OperationError("scope_violation", "Canvas is unavailable", canvas_id=self.canvas_id) from exc
        if canvas is None or canvas.get("project_id") != self.project_id:
            _invalid("scope_violation", "Canvas does not belong to the authorized project",
                     canvas_id=self.canvas_id)
        return canvas

    def _grant(self, contract: dict, authorization_id: str) -> dict:
        grant = _contract(contract, project_id=self.project_id, canvas_id=self.canvas_id, actor=self.actor)
        if not isinstance(authorization_id, str) or authorization_id != grant["authorization_id"]:
            _invalid("authorization_required", "Authorization identity does not match the frozen contract")
        return grant

    def patch_node(self, *, request_id: str, node_id: str, expected_revision: int,
                   expected_input_fingerprint: str, changes: list[dict], contract: dict,
                   authorization_id: str, accepted_transition: dict) -> dict:
        grant = self._grant(contract, authorization_id)
        _require_pair(grant, node_id, "patch")
        transition = _transition(accepted_transition)
        if not isinstance(request_id, str) or not request_id or len(request_id) > _MAX_REQUEST_ID:
            _invalid("invalid_contract", "A stable node patch request ID is required")
        if (not isinstance(changes, list) or not changes or len(changes) > _MAX_PATCHES
                or any(not isinstance(item, dict) or set(item) != {"path", "value"} for item in changes)):
            _invalid("invalid_patch", "Patch changes must be bounded field-path updates")
        try:
            body_size = len(json.dumps(changes, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_patch", "Patch values must be finite JSON values") from exc
        if body_size > _MAX_PATCH_BYTES:
            _invalid("invalid_patch", "Patch payload is too large")
        try:
            request_hash = fingerprint({"kind": "node.patch", "project_id": self.project_id,
                "canvas_id": self.canvas_id, "actor_id": str(self.actor["id"]), "request_id": request_id,
                "node_id": node_id, "expected_revision": expected_revision,
                "expected_input_fingerprint": expected_input_fingerprint,
                "changes": [{"path": item["path"], "value": item["value"]} for item in changes],
                "grant": grant["fingerprint"], "transition": transition})
        except (TypeError, ValueError) as exc:
            raise OperationError("invalid_patch", "Patch contains unsupported JSON values") from exc
        request_key = "agent-" + fingerprint([grant["root_delegation_id"], request_id])[:64]
        saved_action = self.store.get_node_action(self.canvas_id, grant["root_delegation_id"], request_key)
        if saved_action and saved_action["request_hash"] != request_hash:
            _invalid("idempotency_conflict", "Patch request ID was reused for different fields")
        if saved_action and saved_action["status"] == "complete":
            return saved_action["payload"]["receipt"]
        canvas = self._canvas()
        _check_revision(expected_revision, canvas)
        target = _node(canvas, node_id)
        if saved_action:
            journal = saved_action["payload"]
        else:
            if input_fingerprint(target) != expected_input_fingerprint:
                _invalid("stale_input", "Node inputs changed before this patch", node_id=node_id)
            normalized = []
            seen_paths = set()
            for item in changes:
                path = _validate_change(target, item["path"], item["value"])
                key = _path_key(path)
                if key in seen_paths:
                    _invalid("invalid_patch", "A field may be patched only once per request")
                seen_paths.add(key)
                before = _get_path(target, path)
                normalized.append({"path": path, "before": before, "after": deepcopy(item["value"])})
            after_node = deepcopy(target)
            for change in normalized:
                _set_path(after_node, change["path"], change["after"])
            updates_timeline = any(change["path"][0] == "draftTimeline" for change in normalized)
            draft_revision = None
            if updates_timeline:
                current_revision = (target.get("data") or {}).get("draftRevision", 0)
                if type(current_revision) is not int or current_revision < 0:
                    _invalid("invalid_patch", "draftRevision must be a non-negative integer before timeline edits")
                draft_revision = current_revision + 1
                after_node.setdefault("data", {})["draftRevision"] = draft_revision
            journal = {
                "node_id": node_id,
                "expected_revision": expected_revision,
                "before_input_fingerprint": expected_input_fingerprint,
                "after_input_fingerprint": input_fingerprint(after_node),
                "changes": normalized,
                "draft_timeline_revision": draft_revision,
                "transition": transition,
            }
            self.store.prepare_node_action(self.canvas_id, grant["root_delegation_id"], request_key,
                                           request_hash, journal)

        def skip_if(existing):
            if not isinstance(existing, dict):
                return None
            current = next((item for item in existing.get("nodes", []) if item.get("id") == node_id), None)
            if (current is not None
                    and input_fingerprint(current) == journal["after_input_fingerprint"]
                    and all(_get_path(current, change["path"]) == change["after"]
                            for change in journal["changes"])):
                return {"saved": True, "revision": existing.get("revision"), "client_save_id": request_key}
            return None

        def build(existing):
            payload = _existing(existing)
            current = _node(payload, node_id)
            if input_fingerprint(current) != journal["before_input_fingerprint"]:
                raise OrchestrationError("input_conflict", "Node changed while patch was being applied", node_id=node_id)
            if any(_get_path(current, change["path"]) != change["before"] for change in journal["changes"]):
                raise OrchestrationError("input_conflict", "A patched field changed before application", node_id=node_id)
            for change in journal["changes"]:
                _set_path(current, change["path"], change["after"])
            if journal.get("draft_timeline_revision") is not None:
                current.setdefault("data", {})["draftRevision"] = journal["draft_timeline_revision"]
            return _finish(payload)

        try:
            result = save_canvas(self.project_dir, self.canvas_id, base_revision=None,
                build_payload=build, skip_if=skip_if, enforce_revision=False,
                client_save_id=request_key, request_hash=request_hash, save_source="orchestration")
        except OrchestrationError as exc:
            if exc.code == "input_conflict":
                raise OperationError("stale_input", str(exc), **exc.details) from exc
            raise
        revision = result.payload.get("revision") if isinstance(result.payload, dict) else None
        if revision is None and result.response_cache:
            revision = result.response_cache.get("revision")
        receipt = {
            "operation": "node.patch", "request_id": request_id, "node_id": node_id,
            "canvas_id": self.canvas_id, "expected_revision": expected_revision,
            "revision": revision, "after_revision": revision,
            "changes": [{"path": list(change["path"]), "before": deepcopy(change["before"]),
                         "after": deepcopy(change["after"])} for change in journal["changes"]],
            "before": {"input_fingerprint": journal["before_input_fingerprint"]},
            "after": {"input_fingerprint": journal["after_input_fingerprint"]},
            "accepted_transition": transition,
        }
        return self.store.complete_node_action(self.canvas_id, grant["root_delegation_id"], request_key,
                                               request_hash, receipt)

    async def execute_node(self, *, request_id: str, node_id: str, operation: str,
                           expected_revision: int, expected_input_fingerprint: str,
                           contract: dict, authorization_id: str,
                           accepted_transition: dict) -> dict:
        grant = self._grant(contract, authorization_id)
        _require_pair(grant, node_id, operation)
        transition = _transition(accepted_transition)
        if not isinstance(request_id, str) or not request_id or len(request_id) > _MAX_REQUEST_ID:
            _invalid("invalid_contract", "A stable node execution request ID is required")
        run_request_id = "agent-" + fingerprint([grant["root_delegation_id"], request_id])[:64]
        request_hash = fingerprint({"kind": "node.run", "project_id": self.project_id,
            "canvas_id": self.canvas_id, "actor_id": str(self.actor["id"]), "request_id": request_id,
            "node_id": node_id, "operation": operation, "expected_revision": expected_revision,
            "expected_input_fingerprint": expected_input_fingerprint,
            "grant": grant["fingerprint"], "transition": transition})
        # A response may be lost after the durable run was accepted. Resolve
        # the stable request identity before consulting mutable canvas inputs;
        # changed dependencies must never turn a replay into a second run.
        existing = self.store.find_request(self.canvas_id, run_request_id)
        if existing is not None:
            marker = existing.get("agent_execution", {})
            if (marker.get("request_hash") != request_hash
                    or marker.get("target_node_id") != node_id
                    or marker.get("operation") != operation
                    or marker.get("accepted_transition") != transition):
                _invalid("idempotency_conflict", "Run request ID was reused for a different node operation")
            self._check_run_scope(existing, grant)
            recovered = await self.scheduler.tick(existing["id"])
            return self._run_receipt(recovered, grant)

        canvas = self._canvas()
        _check_revision(expected_revision, canvas)
        target = _node(canvas, node_id)
        if input_fingerprint(target) != expected_input_fingerprint:
            _invalid("stale_input", "Node inputs changed before execution", node_id=node_id)
        spec = catalog_by_type().get(target.get("type"))
        if spec is None or operation not in {value["name"] for value in spec["operations"]}:
            _invalid("operation_unavailable", "The operation is not registered for this node type",
                     node_id=node_id, operation=operation)
        if (target["type"], operation) not in available_operations():
            _invalid("operation_unavailable", "No installed adapter supports this node operation",
                     node_id=node_id, operation=operation)
        try:
            order = execution_order(canvas["nodes"], canvas["edges"], node_id)
        except OrchestrationError as exc:
            raise OperationError(exc.code, str(exc), **exc.details) from exc
        ancestors = set(order) - {node_id}
        readable = set(grant["read_node_ids"])
        if not ancestors <= readable:
            _invalid("dependency_scope_denied", "A required graph ancestor is outside the frozen read scope",
                     node_ids=sorted(ancestors - readable))
        nodes = {node["id"]: node for node in canvas["nodes"]}
        incoming = [edge for edge in canvas["edges"] if edge.get("target") == node_id
                    and not (edge.get("data") or {}).get("contextLink")]
        frozen_inputs = []
        source_output_fingerprints = {}
        for edge in incoming:
            source_id = edge["source"]
            if source_id not in readable:
                _invalid("dependency_scope_denied", "A direct graph input is outside the frozen read scope",
                         node_id=source_id)
            source = nodes.get(source_id)
            if source is None:
                _invalid("dependency_input_unavailable", "A graph input no longer exists", node_id=source_id)
            result = frozen_node_result(source)
            if not result:
                _invalid("dependency_input_unavailable", "A required graph input has no existing output",
                         node_id=source_id)
            frozen_inputs.append({"node_id": source_id, "result": result, "artifacts": []})
            source_output_fingerprints[source_id] = fingerprint(result)

        previous_media = _media_evidence(target)
        marker = {
                "schema_version": 1, "target_node_id": node_id, "operation": operation,
                "request_id": request_id, "request_hash": request_hash,
                "project_id": self.project_id, "canvas_id": self.canvas_id,
                "actor_id": str(self.actor["id"]),
                "delegation_id": grant["delegation_id"],
                "root_delegation_id": grant["root_delegation_id"],
                "authorization_id": grant["authorization_id"],
                "contract_fingerprint": grant["fingerprint"],
                "allowed_node_ids": grant["allowed_node_ids"],
                "allowed_node_operations": grant["allowed_node_operations"],
                "read_node_ids": grant["read_node_ids"],
                "max_node_runs": grant["max_node_runs"],
                "root_max_node_runs": grant["root_max_node_runs"],
                "node_run_budget_chain": grant["node_run_budget_chain"],
                "expected_revision": expected_revision,
                "target_input_fingerprint": expected_input_fingerprint,
                "target_result_fingerprint": fingerprint(result_projection(target)),
                "target_media_fingerprint": fingerprint(media_projection(target)),
                "dependency_edge_fingerprint": dependency_edge_fingerprint(canvas, node_id),
                "accepted_transition": transition,
                "frozen_inputs": frozen_inputs,
                "source_output_fingerprints": source_output_fingerprints,
                "previous_media": previous_media,
        }
        from novelvideo.task_concurrency_settings import load_task_concurrency_settings
        lanes = load_task_concurrency_settings().lanes
        run = self.store.create(canvas, target=node_id, request_id=run_request_id,
            project_id=self.project_id,
            settings={"lanes": lanes, "concurrency": max(1, sum(lanes.values())),
                      "source": "task-runtime/concurrency"},
            actor=self.actor, operation=operation, target_only=True, agent_execution=marker)
        run = await self.scheduler.tick(run["id"])
        return self._run_receipt(run, grant)

    async def read_run(self, run_id: str, *, contract: dict, authorization_id: str) -> dict:
        grant = self._grant(contract, authorization_id)
        run = self.store.get(run_id)
        self._check_run_scope(run, grant)
        run = await self.scheduler.tick(run_id)
        return self._run_receipt(run, grant)

    def find_request_run(self, request_id: str, *, contract: dict,
                         authorization_id: str) -> dict | None:
        """Resolve a stable accepted request ID for stop/recovery controls."""
        grant = self._grant(contract, authorization_id)
        if not isinstance(request_id, str) or not request_id or len(request_id) > _MAX_REQUEST_ID:
            _invalid("invalid_contract", "A stable node execution request ID is required")
        request_key = "agent-" + fingerprint([grant["root_delegation_id"], request_id])[:64]
        run = self.store.find_request(self.canvas_id, request_key)
        if run is None:
            return None
        self._check_run_scope(run, grant)
        return self._run_receipt(run, grant)

    async def stop_run(self, run_id: str, *, contract: dict, authorization_id: str) -> dict:
        grant = self._grant(contract, authorization_id)
        run = self.store.get(run_id)
        self._check_run_scope(run, grant)
        run = self.store.cancel(run_id)
        return self._run_receipt(run, grant)

    async def resume_run(self, run_id: str, *, contract: dict, authorization_id: str) -> dict:
        grant = self._grant(contract, authorization_id)
        run = self.store.get(run_id)
        self._check_run_scope(run, grant)
        run = self.store.resume(run_id)
        run = await self.scheduler.tick(run_id)
        return self._run_receipt(run, grant)

    async def tick(self, run_id: str, *, contract: dict, authorization_id: str) -> dict:
        """Advance one scoped run tick; any new submission is reauthorized by Scheduler."""
        grant = self._grant(contract, authorization_id)
        run = self.store.get(run_id)
        self._check_run_scope(run, grant)
        run = await self.scheduler.tick(run_id)
        return self._run_receipt(run, grant)

    def _check_run_scope(self, run: dict, grant: dict) -> None:
        marker = run.get("agent_execution")
        if (not marker or run.get("project_id") != self.project_id
                or run.get("canvas_id") != self.canvas_id
                or run.get("actor", {}).get("id") != self.actor.get("id")
                or marker.get("project_id") != self.project_id
                or marker.get("canvas_id") != self.canvas_id
                or marker.get("actor_id") != str(self.actor.get("id"))
                or marker.get("delegation_id") != grant["delegation_id"]
                or marker.get("root_delegation_id") != grant["root_delegation_id"]
                or marker.get("authorization_id") != grant["authorization_id"]
                or marker.get("contract_fingerprint") != grant["fingerprint"]):
            _invalid("scope_violation", "Node run is outside the authorized actor and delegation scope")

    def _run_receipt(self, run: dict, grant: dict) -> dict:
        marker = run.get("agent_execution", {})
        target = marker.get("target_node_id")
        attempts = run.get("attempts", {}).get(target, [])
        attempt = attempts[-1] if attempts else {}
        allowed_results = RESULT_FIELDS.get(next((n["type"] for n in run["snapshot"]["nodes"]
                                                   if n["id"] == target), ""), set())
        result = {key: deepcopy(value) for key, value in (attempt.get("result") or {}).items()
                  if key in allowed_results and type(value) in {str, int, float, bool}
                  and (not isinstance(value, str) or len(value) <= 4096)}
        receipt = {
            "node_id": target,
            "node_run_id": run["id"],
            "run_id": run["id"],
            "status": run["status"],
            "attempt_status": attempt.get("status"),
            "provider_status": attempt.get("provider_status"),
            "cancel_reason": (attempt.get("cancel_reason")
                              or ("adoption_stopped" if attempt.get("status") == "cancelled"
                                  and attempt.get("result") is not None else
                                  "cancelled_before_submit" if attempt.get("status") == "cancelled"
                                  and attempt.get("submission") is None else
                                  "stop_requested_pending_resolution" if run.get("status") == "cancel_requested"
                                  else None)),
            "artifacts": deepcopy(attempt.get("artifacts", [])),
            "result": result,
            "error": deepcopy(attempt.get("error")),
            "previous_media": deepcopy(marker.get("previous_media", [])),
            "usage": {"kind": "node_run_count",
                      "delegation_used": self.store.node_run_count(grant["root_delegation_id"], grant["delegation_id"]),
                      "delegation_limit": grant["max_node_runs"],
                      "root_used": self.store.node_run_count(grant["root_delegation_id"]),
                      "root_limit": grant["root_max_node_runs"]},
        }
        if attempt.get("status") == "succeeded":
            try:
                canvas = self._canvas()
                node = _node(canvas, target)
                result_fp = fingerprint(result_projection(node))
                media_fp = fingerprint(media_projection(node))
                if self.scheduler.agent_projection_matches(run, attempt, canvas, node):
                    fields = _result_fields(node)
                    receipt["projection"] = {
                        "status": "verified_projected", "node_id": target,
                        "input_fingerprint": run["input_fingerprints"][target],
                        "result_fingerprint": result_fp,
                        "projected_media_fingerprint": media_fp,
                        "fields": fields,
                    }
                else:
                    receipt["projection"] = {"status": "stale", "node_id": target,
                                              "projected_media_fingerprint": media_fp}
            except OperationError:
                receipt["projection"] = {"status": "stale", "node_id": target}
        elif (attempt.get("error") or {}).get("code") == "input_conflict":
            error = attempt["error"]
            receipt["projection"] = {
                "status": "stale", "node_id": target,
                "current_result_fingerprint": error.get("current_result_fingerprint"),
                "projected_media_fingerprint": error.get("current_media_fingerprint"),
                "current_media": deepcopy(error.get("current_media", [])),
                "artifacts_preserved_on_run": True,
            }
        return receipt
