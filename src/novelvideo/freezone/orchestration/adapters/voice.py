"""Durable character-voice design adapter for Freezone canvas runs.

Designing a voice creates a reviewable candidate. Adoption remains an explicit
human action through the voice-candidate API.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from novelvideo.freezone.orchestration.models import OrchestrationError, fingerprint


OPERATIONS = {("characterVoiceNode", "prepare_design"), ("characterVoiceNode", "design")}
LOCAL_OPERATIONS = {("characterVoiceNode", "prepare_design")}
TASK_TYPE = "character_voice_design"


def _value(data: dict, camel: str, snake: str, default=None):
    return data.get(camel, data.get(snake, default))


def _design_input(data: dict) -> dict:
    """Only authoring fields that affect the compiled request enter its identity."""
    design = data.get("voiceDesign") if "voiceDesign" in data else data.get("design")
    nested_audition = design.get("audition_text", "") if isinstance(design, dict) else ""
    return {
        "character_id": _value(data, "characterId", "character_id", ""),
        "character_name": _value(data, "characterName", "character_name", ""),
        "identity_id": _value(data, "identityId", "identity_id", ""),
        "slot": _value(data, "slot", "slot", "default"),
        "voice_description": _value(data, "voiceDescription", "voice_description", ""),
        "voice_description_draft": data.get("voiceDescriptionDraft", ""),
        "audition_text": _value(data, "auditionText", "audition_text", nested_audition),
        "language": _value(data, "language", "language", "Auto"),
        "voice_spec": _value(data, "voiceSpec", "voice_spec"),
        "design": design,
        "perceived_age": _value(data, "perceivedAge", "perceived_age", ""),
    }


def _design_input_fingerprint(data: dict) -> str:
    return fingerprint(_design_input(data))


async def _resolve_scope(run, *, read_only: bool):
    from novelvideo.api.routes.characters import _resolve_character_project

    result = await _resolve_character_project(
        run["project_id"], run["actor"],
        required_role="viewer" if read_only else "editor",
    )
    ctx, _username, _project_name, project_dir, _output_dir, store = result
    if ctx is None:
        raise OrchestrationError("project_not_found", "Project does not exist")
    return ctx, Path(project_dir), store


def _candidate_store(store, project_dir):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore

    return VoiceCandidateStore(store.db_path, project_dir)


def _candidate_for_request(candidates, request_id: str):
    """Look up the persistent idempotency receipt left by the task runner."""
    with candidates.connection() as db:
        row = db.execute(
            "SELECT * FROM character_voice_candidates WHERE request_id=?",
            (request_id,),
        ).fetchone()
    return candidates.decode(row) if row else None


def _task_receipt(ctx, scope: str):
    from novelvideo.task_state import get_task_manager

    return get_task_manager().get_task_for_project(ctx, TASK_TYPE, 0, scope=scope)


def _submission(scope: str, task) -> dict:
    return {
        "voice_design": True,
        "task_type": TASK_TYPE,
        "task_id": str(getattr(task, "task_id", "") or ""),
        "candidate_id": str((getattr(task, "result", None) or {}).get("candidate_id") or ""),
        "scope": scope,
        "request_id": scope,
    }


async def submit(run, attempt, node, operation):
    if (node.get("type"), operation) not in OPERATIONS:
        raise OrchestrationError("unsupported_operation", "Unsupported character voice operation")
    data = node.get("data") or {}
    scope = str(attempt.get("id") or "").strip()
    if not scope:
        raise OrchestrationError("invalid_input", "Voice design attempt id is required")

    draft = data.get("voiceDescriptionDraft")
    if isinstance(draft, str) and draft.strip():
        raise OrchestrationError(
            "voice_description_unaccepted",
            "Accept the structured voice-field changes and compile the design before preparing or generating it",
        )

    preview = data.get("voiceDesignPreview")
    if operation == "design" and preview is not None:
        if (not isinstance(preview, dict) or preview.get("schema_version") != 1
                or preview.get("input_fingerprint") != _design_input_fingerprint(data)
                or not isinstance(preview.get("request"), dict)):
            raise OrchestrationError("input_conflict", "Prepared voice design is stale; prepare it again")

    name = str(_value(data, "characterName", "character_name", "") or "").strip()
    slot = str(_value(data, "slot", "slot", "default") or "default").strip()

    from novelvideo.api.routes import characters
    from novelvideo.api.schemas import CharacterVoiceDesignRequest
    from novelvideo.media_capabilities.tts.character_voice import (
        prepare_character_voice_request,
        voice_snapshot_digest,
    )
    from novelvideo.media_capabilities.tts.voice_recommendation import voice_project_context
    from novelvideo.ports import get_task_backend
    from novelvideo.task_backend.limits import (
        GlobalLaneQueueLimitExceeded,
        ProjectTaskLimitExceeded,
        ProjectUserTaskLimitExceeded,
    )
    from pydantic import ValidationError

    try:
        design = data.get("voiceDesign") if "voiceDesign" in data else data.get("design")
        canonical_design = "voiceDesign" in data and design is not None
        audition_text = _design_input(data)["audition_text"]
        body = CharacterVoiceDesignRequest.model_validate({
            # In the canvas, voiceDescription is a compiler receipt, never an
            # alternate user-authored prompt that can bypass voiceDesign.
            "voice_description": "" if canonical_design else _value(data, "voiceDescription", "voice_description", ""),
            "audition_text": audition_text,
            "language": _value(data, "language", "language", "Auto"),
            "voice_spec": None if canonical_design else _value(data, "voiceSpec", "voice_spec"),
            "design": design,
            "perceived_age": _value(data, "perceivedAge", "perceived_age", ""),
        })
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_input", "Voice design input failed validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc

    ctx, _username, _project_name, _project_dir, _output_dir, store = (
        await characters._resolve_character_project(run["project_id"], run["actor"])
    )
    if ctx is None:
        raise OrchestrationError("project_not_found", "Project does not exist")
    selected_id = str(_value(data, "characterId", "character_id", "") or "")
    if selected_id:
        character = next((item for item in store.get_all_characters()
                          if str(getattr(item, "character_id", "") or "") == selected_id), None)
        if character is None:
            raise OrchestrationError("character_changed", "The selected character changed; refresh the canvas")
        # Stable UUID resolution lets an in-flight canvas reference survive a
        # display-name change without ever targeting a replacement character.
        name = character.name
    else:
        if not name:
            raise OrchestrationError("invalid_input", "Select a character before designing a voice")
        character = store.get_character(name)
        if character is None:
            raise OrchestrationError("character_not_found", f"Character does not exist: {name}")
    if slot not in characters.VOICE_SAMPLE_SLOTS:
        raise OrchestrationError("invalid_input", f"Unsupported voice slot: {slot}")

    identity_id = str(_value(data, "identityId", "identity_id", "") or "")
    if identity_id and not any(
        str(getattr(identity, "identity_id", "") or "") == identity_id
        for identity in (getattr(character, "identities", []) or [])
    ):
        raise OrchestrationError("invalid_input", "Selected character identity no longer exists")

    try:
        payload = prepare_character_voice_request(
            character, slot=slot,
            voice_description=body.voice_description,
            audition_text=body.audition_text,
            language=body.language,
            voice_spec=body.voice_spec,
            db_path=getattr(store, "db_path", None),
            design=body.design,
            perceived_age=body.perceived_age,
        )
        context = voice_project_context(store.db_path)
        payload["design_context"] = context
        payload["design_context_digest"] = voice_snapshot_digest(context)
    except ValueError as exc:
        raise OrchestrationError("invalid_input", str(exc)) from exc

    supplied_compilation = data.get("voiceDescription")
    if canonical_design and supplied_compilation is not None and supplied_compilation != payload["voice_description"]:
        raise OrchestrationError(
            "input_conflict",
            "Saved voiceDescription does not match the current structured design; compile the accepted design again",
        )

    if identity_id:
        payload["identity_id"] = identity_id

    if operation == "prepare_design":
        frozen_payload = deepcopy(payload)
        return {"local": True, "result": {"voiceDesignPreview": {
            "schema_version": 1,
            "input_fingerprint": _design_input_fingerprint(data),
            "request_fingerprint": fingerprint(frozen_payload),
            "character_id": str(getattr(character, "character_id", "") or ""),
            "identity_id": identity_id,
            "request": frozen_payload,
        }}}

    if preview is not None:
        frozen_payload = preview["request"]
        if (fingerprint(frozen_payload) != preview.get("request_fingerprint")
                or payload != frozen_payload):
            raise OrchestrationError(
                "input_conflict", "Character facts or voice compilation changed; prepare the voice design again",
            )

    # The same attempt identifier fences both task enqueue and candidate
    # creation, so a lost adapter response can only recover this design.
    payload["request_id"] = scope
    try:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=TASK_TYPE,
            queue_kind="default",
            episode=0,
            scope=scope,
            payload=payload,
        )
    except (ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded, GlobalLaneQueueLimitExceeded) as exc:
        raise OrchestrationError("capacity_wait", str(exc)) from exc

    return {
        **_submission(scope, queued.task_state),
        "backend": getattr(queued, "backend", ""),
        "queue": getattr(queued, "queue", "default"),
    }


async def reconcile_submission(run, attempt):
    """Recover enqueue outcome from task or candidate receipts after a lost reply."""
    scope = str(attempt.get("id") or "").strip()
    if not scope:
        return {"status": "submission_unknown"}
    node = next((item for item in (run.get("snapshot") or {}).get("nodes", [])
                 if item.get("id") == attempt.get("node_id")), None)
    operation = (node or {}).get("data", {}).get("operation", "design")
    if ((node or {}).get("type"), operation) in LOCAL_OPERATIONS:
        # This operation only validates and compiles local character facts.
        # It can be safely retried when the process died before journaling its
        # local result; no task or provider request was created.
        return {"status": "not_submitted", "evidence": "voice_design_preparation_has_no_external_side_effect"}
    ctx, project_dir, store = await _resolve_scope(run, read_only=True)
    task = _task_receipt(ctx, scope)
    if task is not None:
        return {"status": "submitted", "submission": _submission(scope, task)}

    candidate = _candidate_for_request(_candidate_store(store, project_dir), scope)
    if candidate is None:
        # Missing local receipts cannot prove that enqueue did not reach the
        # backend; never permit a blind retry that could bill twice.
        return {"status": "submission_unknown"}
    if candidate.get("candidate_id"):
        return {
            "status": "submitted",
            "submission": {"voice_design": True, "task_type": TASK_TYPE,
                           "task_id": "", "candidate_id": candidate["candidate_id"],
                           "scope": scope, "request_id": scope},
        }
    return {"status": "submission_unknown"}


def _candidate_result(ctx, candidates, candidate):
    if not candidate or not candidate.get("path"):
        raise OrchestrationError("invalid_artifact", "Voice design completed without an audio candidate")
    if candidate.get("status") == "deleted":
        raise OrchestrationError("invalid_artifact", "Voice candidate was deleted before it could be consumed")
    from novelvideo.api.routes.voice_candidates import candidate_view

    return {"voiceCandidates": [candidate_view(ctx, candidates, candidate)]}


async def reconcile(run, submission):
    scope = str(submission.get("scope") or submission.get("request_id") or "").strip()
    if not scope:
        raise OrchestrationError("invalid_submission", "Voice task receipt has no attempt scope")
    ctx, project_dir, store = await _resolve_scope(run, read_only=True)
    candidates = _candidate_store(store, project_dir)
    task = _task_receipt(ctx, scope)
    candidate = _candidate_for_request(candidates, scope)

    if task is None:
        if candidate and candidate.get("path") and candidate.get("report"):
            return {"status": "succeeded", "result": _candidate_result(ctx, candidates, candidate)}
        return {"status": "submission_unknown"}

    if task.status == "completed":
        candidate_id = str((task.result or {}).get("candidate_id") or "")
        if candidate_id:
            candidate = candidates.get(candidate_id)
        if not candidate or candidate.get("request_id") != scope:
            raise OrchestrationError("invalid_artifact", "Voice task receipt references an unknown candidate")
        return {"status": "succeeded", "result": _candidate_result(ctx, candidates, candidate)}
    if task.status in {"failed", "cancelled"}:
        return {"status": task.status, "error": task.error or f"Voice design task {task.status}"}
    return {"status": "running"}


async def verify(run, attempt):
    """Verify candidate bytes and bind evidence to this attempt's candidate."""
    result = attempt.get("result") or {}
    records = result.get("voiceCandidates") or []
    if not isinstance(records, list) or not records:
        raise OrchestrationError("invalid_artifact", "Voice design produced no candidate list")
    ctx, project_dir, store = await _resolve_scope(run, read_only=True)
    candidates = _candidate_store(store, project_dir)
    expected_request_id = str(attempt.get("id") or "")
    artifacts = []
    for item in records:
        candidate_id = str(item.get("candidate_id") or "") if isinstance(item, dict) else ""
        if not candidate_id:
            raise OrchestrationError("invalid_artifact", "Voice candidate is missing its id")
        candidate = candidates.get(candidate_id)
        if candidate.get("request_id") != expected_request_id:
            raise OrchestrationError("invalid_artifact", "Voice candidate belongs to another attempt")
        try:
            candidates.read_audio(candidate_id)
        except (OSError, ValueError) as exc:
            raise OrchestrationError("invalid_artifact", f"Voice candidate audio failed integrity verification: {exc}") from exc
        artifacts.append({
            "kind": "audio",
            "sha256": candidate["sha256"],
            "path": candidate["path"],
            "candidate_id": candidate_id,
        })
    return artifacts

