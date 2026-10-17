"""Scoped, receipt-backed director content edits for professional agents.

These operations call the existing director workspace and director plan API
services. Their results are reviewable content revisions; none adopts a plan
or starts media production.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import portalocker
from pydantic import TypeAdapter

from novelvideo.director_plan.models import UpdateShot
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.director_plan.workspace import SceneBlocking
from novelvideo.freezone.orchestration.models import OrchestrationError, fingerprint
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore


OPERATIONS = {
    ("directorWorkspaceNode", "save_blocking"),
    ("directorWorkspaceNode", "compile_blocking"),
    ("directorWorkspaceNode", "edit_shot"),
    ("directorWorkspaceNode", "edit_structure"),
}

_SHOT_FIELDS = frozenset(UpdateShot.model_fields) - {"kind", "shot_id"}
_SAFE_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_RECEIPT_KEY = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_LOCKS_GUARD = threading.Lock()
_RECEIPT_LOCKS: dict[str, threading.RLock] = {}


def _required_text(value: object, name: str, *, maximum: int = 128) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not result or len(result) > maximum:
        raise OrchestrationError("invalid_input", f"A valid {name} is required")
    return result


def _operation_context(run: Mapping[str, Any], attempt: Mapping[str, Any], operation: str) -> dict[str, Any]:
    project_id = _required_text(run.get("project_id"), "project id")
    run_id = _required_text(run.get("id"), "run id")
    node_id = _required_text(attempt.get("node_id"), "director node id")
    attempt_id = _required_text(attempt.get("id"), "attempt id")
    if _SAFE_ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise OrchestrationError("invalid_input", "Director attempt id is not safe")

    node = next(
        (item for item in (run.get("snapshot") or {}).get("nodes", ())
         if isinstance(item, Mapping) and item.get("id") == node_id),
        None,
    )
    if not isinstance(node, Mapping) or node.get("type") != "directorWorkspaceNode":
        raise OrchestrationError("scope_violation", "Professional director operation requires a director workspace node")
    data = node.get("data") or {}
    if not isinstance(data, Mapping):
        raise OrchestrationError("invalid_input", "Director node data is invalid")
    if data.get("operation") != operation:
        raise OrchestrationError("scope_violation", "Selected director operation does not match the node")

    episode = data.get("episodeNumber")
    if type(episode) is not int or episode <= 0:
        raise OrchestrationError("invalid_input", "A positive bound episodeNumber is required")
    revision_id = _required_text(data.get("directorRevisionId"), "bound directorRevisionId")
    inputs = data.get("directorOperationInputs")
    if not isinstance(inputs, Mapping) or inputs.get("operation") != operation:
        raise OrchestrationError("invalid_input", "directorOperationInputs.operation must match the selected operation")

    normalized, scope = _normalize_inputs(operation, revision_id, inputs)
    request_hash = fingerprint({
        "project_id": project_id,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "episode": episode,
        "director_revision_id": revision_id,
        "operation": operation,
        "inputs": normalized,
    })
    receipt_key = fingerprint({
        "project_id": project_id,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
    })
    return {
        "project_id": project_id,
        "run_id": run_id,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "episode": episode,
        "revision_id": revision_id,
        "operation": operation,
        "inputs": normalized,
        "scope": scope,
        "request_hash": request_hash,
        "receipt_key": receipt_key,
        "node": node,
    }


def _normalize_inputs(operation: str, revision_id: str, inputs: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    common = {"operation"}
    if operation == "save_blocking":
        allowed = common | {"sceneId", "groupIds", "baseVersion", "blocking"}
        _reject_unknown(inputs, allowed)
        scene_id = _required_text(inputs.get("sceneId"), "sceneId", maximum=200)
        raw_group_ids = inputs.get("groupIds")
        if not isinstance(raw_group_ids, list) or not raw_group_ids or len(raw_group_ids) > 500:
            raise OrchestrationError("invalid_input", "save_blocking requires one or more groupIds")
        group_ids = [_required_text(item, "groupId") for item in raw_group_ids]
        if len(group_ids) != len(set(group_ids)):
            raise OrchestrationError("invalid_input", "groupIds must be unique")
        if "baseVersion" not in inputs:
            raise OrchestrationError("invalid_input", "save_blocking requires baseVersion for compare-and-swap")
        base_version = inputs["baseVersion"]
        if (base_version is not None
                and not (isinstance(base_version, str) and base_version.strip())
                and not (type(base_version) is int and base_version >= 0)):
            raise OrchestrationError("invalid_input", "baseVersion must be a revision id, non-negative version, or null")
        try:
            blocking = SceneBlocking.model_validate(inputs.get("blocking")).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise OrchestrationError("invalid_input", "Scene blocking failed validation") from exc
        normalized = {
            "sceneId": scene_id,
            "groupIds": sorted(group_ids),
            "baseVersion": base_version.strip() if isinstance(base_version, str) else base_version,
            "blocking": blocking,
        }
        return normalized, {
            "director_revision_id": revision_id,
            "scene_id": scene_id,
            "group_ids": sorted(group_ids),
            "base_version": normalized["baseVersion"],
        }

    if operation == "compile_blocking":
        allowed = common | {"baseRevisionId"}
        _reject_unknown(inputs, allowed)
        base_revision_id = _required_text(inputs.get("baseRevisionId"), "baseRevisionId")
        if base_revision_id != revision_id:
            raise OrchestrationError("scope_violation", "compile_blocking baseRevisionId must match the node-bound director revision")
        return {"baseRevisionId": base_revision_id}, {
            "director_revision_id": revision_id,
            "base_revision_id": base_revision_id,
        }

    if operation == "edit_shot":
        allowed = common | {"revisionId", "groupId", "shotId"} | _SHOT_FIELDS
        _reject_unknown(inputs, allowed)
        selected_revision_id = _required_text(inputs.get("revisionId"), "revisionId")
        if selected_revision_id != revision_id:
            raise OrchestrationError("scope_violation", "edit_shot revisionId must match the node-bound director revision")
        group_id = _required_text(inputs.get("groupId"), "groupId")
        shot_id = _required_text(inputs.get("shotId"), "shotId")
        updates = {field: inputs[field] for field in _SHOT_FIELDS if field in inputs}
        if not updates:
            raise OrchestrationError("invalid_input", "edit_shot requires at least one UpdateShot field")
        try:
            command = UpdateShot.model_validate({"kind": "update_shot", "shot_id": shot_id, **updates})
        except (TypeError, ValueError) as exc:
            raise OrchestrationError("invalid_input", "UpdateShot fields failed validation") from exc
        normalized_updates = command.model_dump(mode="json", exclude={"kind", "shot_id"}, exclude_none=True)
        normalized = {
            "revisionId": selected_revision_id,
            "groupId": group_id,
            "shotId": shot_id,
            **normalized_updates,
        }
        return normalized, {
            "director_revision_id": revision_id,
            "group_id": group_id,
            "shot_id": shot_id,
        }

    if operation == "edit_structure":
        from novelvideo.director_plan.models import DirectorEdit
        _reject_unknown(inputs, {"operation", "revisionId", "groupIds", "command"})
        if inputs.get("revisionId") != revision_id:
            raise OrchestrationError("scope_violation", "Structural edits require the bound director revision")
        groups = inputs.get("groupIds")
        if not isinstance(groups, list) or not 1 <= len(groups) <= 50:
            raise OrchestrationError("invalid_input", "Structural edits require explicit groupIds")
        groups = [_required_text(value, "groupId") for value in groups]
        if len(groups) != len(set(groups)):
            raise OrchestrationError("invalid_input", "groupIds must be unique")
        raw = inputs.get("command")
        if not isinstance(raw, dict) or raw.get("kind") not in {
            "add_shot_from_template", "delete_shot", "reorder_shots", "split_group", "merge_adjacent_groups",
        }:
            raise OrchestrationError("invalid_input", "Unsupported structural edit command")
        try:
            command = TypeAdapter(DirectorEdit).validate_python(raw)
        except (TypeError, ValueError) as exc:
            raise OrchestrationError("invalid_input", "Invalid structural edit command") from exc
        return {"revisionId": revision_id, "groupIds": sorted(groups),
                "command": command.model_dump(mode="json")}, {
            "director_revision_id": revision_id, "group_ids": sorted(groups)}

    raise OrchestrationError("unsupported_operation", "Unsupported professional director operation")


def _reject_unknown(inputs: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(inputs) - allowed)
    if unknown:
        raise OrchestrationError("invalid_input", "directorOperationInputs contains unsupported fields", fields=unknown)


async def _resolve_context(run: Mapping[str, Any], *, role: str):
    from novelvideo.api.routes import director_workspace

    ctx = await director_workspace._resolve(run["project_id"], run["actor"], role=role)
    if str(getattr(ctx, "project_id", "") or "") != str(run["project_id"]):
        raise OrchestrationError("scope_violation", "Resolved project context does not match this director operation")
    return ctx


def _load_revision(plans: DirectorPlanStore, episode: int, revision_id: str):
    try:
        revision = plans.load(episode, revision_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise OrchestrationError("scope_violation", "Bound director revision is unavailable for this episode") from exc
    if revision.episode != episode:
        raise OrchestrationError("scope_violation", "Bound director revision belongs to another episode")
    return revision


def _validate_scope(ctx: Any, context: Mapping[str, Any]):
    episode = context["episode"]
    operation = context["operation"]
    bound_revision_id = context["revision_id"]
    plans = DirectorPlanStore(ctx.output_dir)

    if operation in {"save_blocking", "compile_blocking"}:
        try:
            revision = plans.load_active(episode)
        except Exception as exc:
            from novelvideo.episode_source_versions import SourceVersionConflict

            if isinstance(exc, SourceVersionConflict):
                raise OrchestrationError("input_conflict", "Active director revision is stale") from exc
            raise
        if revision is None or revision.status != "active":
            raise OrchestrationError("scope_violation", "An active director revision is required")
        if revision.revision_id != bound_revision_id:
            raise OrchestrationError("input_conflict", "Node-bound director revision is no longer active")
    else:
        revision = _load_revision(plans, episode, bound_revision_id)

    if operation == "edit_structure":
        allowed = set(context["scope"]["group_ids"])
        groups = {group.id: group for group in revision.groups}
        if allowed - groups.keys():
            raise OrchestrationError("scope_violation", "Selected group is outside the bound revision")
        command = context["inputs"]["command"]
        affected = {command[key] for key in ("group_id", "left_group_id", "right_group_id") if key in command}
        shots = {shot.id: group.id for group in revision.groups for shot in group.shots}
        requested_shots = [command[key] for key in ("shot_id", "template_shot_id", "before_shot_id") if key in command]
        requested_shots.extend(command.get("shot_ids", []))
        for shot_id in requested_shots:
            if shot_id not in shots:
                raise OrchestrationError("scope_violation", "Structural edit shot is outside the bound revision")
            affected.add(shots[shot_id])
        if not affected or affected != allowed:
            raise OrchestrationError("scope_violation", "Structural command must match the exact selected group scope")
        return revision

    if operation == "edit_shot":
        group_id = context["scope"]["group_id"]
        shot_id = context["scope"]["shot_id"]
        group = next((item for item in revision.groups if item.id == group_id), None)
        if group is None:
            raise OrchestrationError("scope_violation", "groupId is not in the bound director revision")
        if not any(shot.id == shot_id for shot in group.shots):
            raise OrchestrationError("scope_violation", "shotId is not in the selected director group")
        return revision

    if operation == "save_blocking":
        semantics = ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
        if semantics is None or semantics.revision_id != revision.semantic_revision_id:
            raise OrchestrationError("input_conflict", "The active semantic revision no longer matches the director plan")
        scene_id = context["scope"]["scene_id"]
        if scene_id not in {scene.id for scene in semantics.scenes}:
            raise OrchestrationError("scope_violation", "sceneId is not in the active screenplay semantics")
        from novelvideo.api.routes import director_workspace

        groups = {group.id: group for group in revision.groups}
        group_ids = context["scope"]["group_ids"]
        for group_id in group_ids:
            group = groups.get(group_id)
            if group is None or director_workspace._group_scene_id(group.model_dump(mode="json"), semantics) != scene_id:
                raise OrchestrationError("scope_violation", "Every groupId must belong to the selected scene and bound revision")
        blocking = SceneBlocking.model_validate(context["inputs"]["blocking"])
        selected_groups = [groups[group_id] for group_id in group_ids]
        subject_ids = {
            subject.subject_id
            for group in selected_groups
            for shot in group.shots
            if shot.cinematography is not None
            for subject in shot.cinematography.subjects
        }
        light_ids = {
            light.light_id
            for group in selected_groups
            for shot in group.shots
            if shot.cinematography is not None
            for light in shot.cinematography.lights
        }
        prop_ids = {
            requirement.entity_key
            for group in selected_groups for shot in group.shots
            for requirement in shot.asset_requirements if requirement.kind == 'prop'
        }
        if {actor.id for actor in blocking.actors} - subject_ids:
            raise OrchestrationError("scope_violation", "Scene blocking actor ids must exist in the selected director groups")
        if {light.id for light in blocking.lights} - light_ids:
            raise OrchestrationError("scope_violation", "Scene blocking light ids must exist in the selected director groups")
        if {prop.id for prop in blocking.props} - prop_ids:
            raise OrchestrationError("scope_violation", "Scene blocking prop ids must reference selected group asset requirements")
    return revision


def _receipt_paths(ctx: Any, episode: int, receipt_key: str) -> tuple[Path, Path]:
    if _SAFE_RECEIPT_KEY.fullmatch(receipt_key) is None:
        raise OrchestrationError("invalid_submission", "Professional edit receipt key is invalid")
    root = Path(ctx.output_dir).resolve()
    directory = (root / "director_workspace" / "agent_operation_receipts" / f"episode_{episode:03d}").resolve()
    if not directory.is_relative_to(root):
        raise OrchestrationError("scope_violation", "Professional edit receipt path escaped the project")
    path = directory / f"{receipt_key}.json"
    lock_path = directory / f"{receipt_key}.lock"
    if not path.resolve().is_relative_to(root) or not lock_path.resolve().is_relative_to(root):
        raise OrchestrationError("scope_violation", "Professional edit receipt path escaped the project")
    return path, lock_path


def _read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise OrchestrationError("submission_unknown", "Professional edit receipt is unreadable; it will not be replayed") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise OrchestrationError("submission_unknown", "Professional edit receipt is invalid; it will not be replayed")
    return value


def _receipt_lock(lock_path: Path):
    key = str(lock_path.resolve())
    with _RECEIPT_LOCKS_GUARD:
        lock = _RECEIPT_LOCKS.setdefault(key, threading.RLock())
    return lock


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _begin_receipt(ctx: Any, context: Mapping[str, Any]) -> dict[str, Any] | None:
    path, lock_path = _receipt_paths(ctx, context["episode"], context["receipt_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with _receipt_lock(lock_path), portalocker.Lock(str(lock_path), mode="a+", timeout=10):
        existing = _read_receipt(path)
        if existing is not None:
            if existing.get("request_hash") != context["request_hash"]:
                raise OrchestrationError("idempotency_conflict", "idempotency conflict: attempt ID was reused for a different director operation")
            if existing.get("status") == "complete" and isinstance(existing.get("submission"), dict):
                return existing["submission"]
            raise OrchestrationError("submission_unknown", "Submission outcome is unknown for this unfinished director write; reconcile before any retry")
        pending = {
            "schema_version": 1,
            "status": "pending",
            "receipt_key": context["receipt_key"],
            "project_id": context["project_id"],
            "run_id": context["run_id"],
            "node_id": context["node_id"],
            "attempt_id": context["attempt_id"],
            "operation": context["operation"],
            "episode": context["episode"],
            "request_hash": context["request_hash"],
            "scope": dict(context["scope"]),
        }
        _atomic_write(path, pending)
    return None


def _complete_receipt(ctx: Any, context: Mapping[str, Any], submission: dict[str, Any]) -> dict[str, Any]:
    path, lock_path = _receipt_paths(ctx, context["episode"], context["receipt_key"])
    with _receipt_lock(lock_path), portalocker.Lock(str(lock_path), mode="a+", timeout=10):
        existing = _read_receipt(path)
        if existing is None or existing.get("request_hash") != context["request_hash"]:
            raise OrchestrationError("submission_unknown", "Professional edit receipt changed before completion")
        if existing.get("status") == "complete" and isinstance(existing.get("submission"), dict):
            return existing["submission"]
        if existing.get("status") != "pending":
            raise OrchestrationError("submission_unknown", "Professional edit receipt is not in a completable state")
        completed = {**existing, "status": "complete", "submission": submission}
        _atomic_write(path, completed)
    return submission


def _submission_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    submission = receipt.get("submission")
    if receipt.get("status") != "complete" or not isinstance(submission, dict):
        return None
    return submission


def _existing_receipt(ctx: Any, context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a previously completed result before checking mutable domain scope.

    A completed write is authoritative for its stable attempt even if a later
    revision activation makes the original bound revision stale. Reads are safe
    without the receipt lock because writers publish the JSON with os.replace.
    """
    path, _lock_path = _receipt_paths(ctx, context["episode"], context["receipt_key"])
    receipt = _read_receipt(path)
    if receipt is None:
        return None
    if receipt.get("request_hash") != context["request_hash"]:
        raise OrchestrationError(
            "idempotency_conflict",
            "idempotency conflict: attempt ID was reused for a different director operation",
        )
    submission = _submission_from_receipt(receipt)
    if submission is not None:
        return submission
    raise OrchestrationError(
        "submission_unknown",
        "Submission outcome is unknown for this unfinished director write; reconcile before any retry",
    )


async def submit(run, attempt, node, operation):
    context = _operation_context(run, attempt, operation)
    ctx = await _resolve_context(run, role="editor")
    existing = _existing_receipt(ctx, context)
    if existing is not None:
        return existing
    _validate_scope(ctx, context)
    existing = _begin_receipt(ctx, context)
    if existing is not None:
        return existing

    if operation == "save_blocking":
        from novelvideo.api.routes import director_workspace

        body = director_workspace.SceneBlockingRequest(
            base_version=context["inputs"]["baseVersion"],
            blocking=context["inputs"]["blocking"],
            group_ids=context["inputs"]["groupIds"],
            director_revision_id=context["revision_id"],
        )
        response = await director_workspace.save_scene_blocking(
            context["project_id"],
            context["episode"],
            context["scope"]["scene_id"],
            body,
            run["actor"],
        )
        blocking_revision = response["data"]
        operation_receipt = {
            "operation": operation,
            "revision_id": context["revision_id"],
            "scene_id": context["scope"]["scene_id"],
            "scene_blocking_revision_id": blocking_revision["revision_id"],
            "version": blocking_revision["version"],
            "group_ids": context["scope"]["group_ids"],
        }
        result_revision_id = context["revision_id"]
        submission_extra = {"scene_id": context["scope"]["scene_id"],
                            "scene_blocking_revision_id": blocking_revision["revision_id"]}
    elif operation == "compile_blocking":
        from novelvideo.api.routes import director_workspace

        response = await director_workspace.compile_director_scene_blocking(
            context["project_id"],
            context["episode"],
            director_workspace.DirectorWorkspaceCompileRequest(
                base_revision_id=context["inputs"]["baseRevisionId"]
            ),
            run["actor"],
        )
        candidate = response["data"]
        operation_receipt = {
            "operation": operation,
            "revision_id": str(candidate["revision_id"]),
            "parent_revision_id": context["revision_id"],
            "status": str(candidate["status"]),
        }
        result_revision_id = str(candidate["revision_id"])
        submission_extra = {"base_revision_id": context["revision_id"]}
    elif operation == "edit_structure":
        from novelvideo.api.routes import director_plans
        from novelvideo.director_plan.models import DirectorEdit
        command = TypeAdapter(DirectorEdit).validate_python(context["inputs"]["command"])
        response = await director_plans.edit_director_plan(context["project_id"], context["episode"],
            context["revision_id"], command, run["actor"])
        candidate = response["data"]
        result_revision_id = str(candidate["revision_id"])
        operation_receipt = {
            "operation": operation, "revision_id": result_revision_id,
            "parent_revision_id": context["revision_id"], "status": str(candidate["status"]),
            "group_ids": context["scope"]["group_ids"], "command": context["inputs"]["command"],
        }
        submission_extra = {"base_revision_id": context["revision_id"],
                            "group_ids": context["scope"]["group_ids"]}
    else:
        from novelvideo.api.routes import director_plans

        command = UpdateShot.model_validate({
            "kind": "update_shot",
            "shot_id": context["scope"]["shot_id"],
            **{key: value for key, value in context["inputs"].items()
               if key not in {"revisionId", "groupId", "shotId"}},
        })
        response = await director_plans.edit_director_plan(
            context["project_id"],
            context["episode"],
            context["revision_id"],
            command,
            run["actor"],
        )
        candidate = response["data"]
        operation_receipt = {
            "operation": operation,
            "revision_id": str(candidate["revision_id"]),
            "parent_revision_id": context["revision_id"],
            "group_id": context["scope"]["group_id"],
            "shot_id": context["scope"]["shot_id"],
            "status": str(candidate["status"]),
        }
        result_revision_id = str(candidate["revision_id"])
        submission_extra = {
            "base_revision_id": context["revision_id"],
            "group_id": context["scope"]["group_id"],
            "shot_id": context["scope"]["shot_id"],
        }

    operation_receipt["receipt_key"] = context["receipt_key"]
    submission = {
        "professional_edit": True,
        "operation": operation,
        "project_id": context["project_id"],
        "episode": context["episode"],
        "attempt_id": context["attempt_id"],
        "receipt_key": context["receipt_key"],
        "revision_id": result_revision_id,
        **submission_extra,
        "operation_receipt": operation_receipt,
    }
    return _complete_receipt(ctx, context, submission)


async def reconcile_submission(run, attempt):
    try:
        node = next(item for item in (run.get("snapshot") or {}).get("nodes", ())
                    if item.get("id") == attempt.get("node_id"))
        operation = str(node.get("data", {}).get("operation") or "")
        context = _operation_context(run, attempt, operation)
        ctx = await _resolve_context(run, role="viewer")
        path, _lock_path = _receipt_paths(ctx, context["episode"], context["receipt_key"])
        receipt = _read_receipt(path)
        if receipt is None:
            return {"status": "submission_unknown"}
        if receipt.get("request_hash") != context["request_hash"]:
            raise OrchestrationError("idempotency_conflict", "idempotency conflict: attempt ID was reused for a different director operation")
        submission = _submission_from_receipt(receipt)
        if submission is None:
            return {"status": "submission_unknown"}
        return {"status": "submitted", "submission": submission}
    except OrchestrationError:
        raise
    except (KeyError, StopIteration, TypeError, ValueError):
        return {"status": "submission_unknown"}


async def _read_submission_receipt(run, submission):
    if submission.get("project_id") != run.get("project_id"):
        raise OrchestrationError("invalid_submission", "Professional edit receipt project does not match this run")
    episode = submission.get("episode")
    if type(episode) is not int or episode <= 0:
        raise OrchestrationError("invalid_submission", "Professional edit receipt episode is invalid")
    ctx = await _resolve_context(run, role="viewer")
    path, _lock_path = _receipt_paths(ctx, episode, str(submission.get("receipt_key") or ""))
    receipt = _read_receipt(path)
    if receipt is None or receipt.get("status") != "complete":
        raise OrchestrationError("submission_unknown", "Professional edit has no completed durable receipt")
    if receipt.get("submission") != dict(submission):
        raise OrchestrationError("invalid_submission", "Professional edit submission does not match its durable receipt")
    return receipt


async def reconcile(run, submission):
    receipt = await _read_submission_receipt(run, submission)
    operation_receipt = receipt["submission"]["operation_receipt"]
    return {
        "status": "succeeded",
        "result": {
            "directorRevisionId": receipt["submission"]["revision_id"],
            "directorOperationReceipt": operation_receipt,
        },
    }


async def verify(run, attempt):
    submission = attempt.get("submission") or {}
    await _read_submission_receipt(run, submission)
    expected = {
        "directorRevisionId": submission["revision_id"],
        "directorOperationReceipt": submission["operation_receipt"],
    }
    if (attempt.get("result") or {}) != expected:
        raise OrchestrationError("invalid_artifact", "Professional director result does not match its durable receipt")
    return [{"kind": "json", "sha256": fingerprint(expected)}]
