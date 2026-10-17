"""Durable narrative-group dubbing adapter for Freezone audio nodes."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re

from novelvideo.freezone.orchestration.models import OrchestrationError, fingerprint


OPERATIONS = {
    ("audioNode", "prepare_dubbing"),
    ("audioNode", "generate_dubbing"),
    ("audioNode", "group_dubbing"),
    ("audioNode", "audition"),
}
# Preparation persists an immutable domain receipt, so it remains a budgeted
# node submission even though it does not enqueue provider work.
LOCAL_OPERATIONS: set[tuple[str, str]] = set()
_AUDITION_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def _data_value(data: Mapping, camel: str, snake: str, default=None):
    return data.get(camel, data.get(snake, default))


def _scope(run, node):
    data = node.get("data") or {}
    episode = _data_value(data, "episodeNumber", "episode_number", data.get("episode"))
    if type(episode) is not int or episode < 1:
        raise OrchestrationError("invalid_input", "A positive episodeNumber is required for group dubbing")
    group_id = str(_data_value(data, "groupId", "group_id", "") or "").strip()
    if not group_id:
        raise OrchestrationError("invalid_input", "Select a narrative group before generating dubbing")
    return episode, group_id


def _performance_for_dialogues(
    data: Mapping, episode: int, group_id: str, dialogues: list[dict],
) -> dict[str, dict[str, str]]:
    """Resolve scoped authoring drafts, validating them with the live compiler."""
    drafts = data.get("dubbingPerformanceDrafts")
    if drafts is not None and not isinstance(drafts, Mapping):
        raise OrchestrationError("invalid_input", "dubbingPerformanceDrafts must be an object keyed by scoped dialogue")
    legacy = _data_value(data, "performance", "performance", {}) or {}
    if not isinstance(legacy, Mapping):
        raise OrchestrationError("invalid_input", "performance must be an object keyed by dialogue id")

    from novelvideo.audio.dubbing_service import compile_dialogue_performance

    prefix = f"{episode}:{group_id}:"
    normalized: dict[str, dict[str, str]] = {}
    for line in dialogues:
        dialogue_id = str(line.get("dialogue_id") or "")
        if not dialogue_id:
            raise OrchestrationError("invalid_input", "Narrative group dialogue is missing its stable ID")
        request = (drafts or {}).get(prefix + dialogue_id)
        if request is None:
            request = legacy.get(dialogue_id)
        if request is None:
            continue
        if not isinstance(request, Mapping) or set(request) - {"mode", "direction", "payload"}:
            raise OrchestrationError("invalid_input", "Each performance draft may contain only mode, direction, and payload")
        mode = request.get("mode", "neutral")
        direction = request.get("direction", "")
        payload = request.get("payload", "")
        if not isinstance(mode, str) or mode not in {"neutral", "emotion_text"}:
            raise OrchestrationError("invalid_input", "Only IndexTTS2 neutral and text emotion modes are supported")
        if (not isinstance(direction, str) or len(direction) > 2000 or _AUDITION_URL.search(direction)
                or not isinstance(payload, str) or len(payload) > 1000 or _AUDITION_URL.search(payload)):
            raise OrchestrationError("invalid_input", "Performance direction and payload must be bounded text without URLs")
        effective_payload = payload if mode == "emotion_text" else None
        try:
            compile_dialogue_performance(str(line.get("text") or ""), {
                "mode": mode, "direction": direction, "payload": effective_payload,
            })
        except (TypeError, ValueError) as exc:
            raise OrchestrationError("invalid_input", f"Dialogue performance failed IndexTTS2 validation: {dialogue_id}") from exc
        value = {"mode": mode, "direction": direction}
        if mode == "emotion_text" and payload.strip():
            value["payload"] = payload
        normalized[dialogue_id] = value
    return normalized


def _voice_bindings(run, node) -> list[dict[str, str]]:
    """Freeze the selected upstream character voice version references."""
    snapshot = run.get("snapshot") or {}
    nodes = {item.get("id"): item for item in snapshot.get("nodes", [])}
    bindings: dict[tuple[str, str], dict[str, str]] = {}
    for edge in snapshot.get("edges", []):
        if edge.get("target") != node.get("id") or (edge.get("data") or {}).get("contextLink"):
            continue
        source = nodes.get(edge.get("source")) or {}
        if source.get("type") != "characterVoiceNode":
            continue
        data = source.get("data") or {}
        reference = data.get("voiceReference") or {}
        if not isinstance(reference, Mapping):
            reference = {}
        character_id = str(_data_value(data, "characterId", "character_id", reference.get("character_id")) or "").strip()
        identity_id = str(_data_value(data, "identityId", "identity_id", reference.get("identity_id")) or "")
        version_id = str(_data_value(data, "voiceVersionId", "voice_version_id", reference.get("version_id")) or "").strip()
        if not character_id or not version_id:
            raise OrchestrationError(
                "input_required", "Connect character voice nodes with a pinned adopted voice version",
            )
        key = (character_id, identity_id)
        previous = bindings.get(key)
        value = {"character_id": character_id, "identity_id": identity_id, "version_id": version_id}
        if previous and previous["version_id"] != version_id:
            raise OrchestrationError(
                "voice_binding_conflict", "Multiple connected voice nodes select different versions for one identity",
            )
        bindings[key] = value
    return [bindings[key] for key in sorted(bindings)]


def _selected_dialogues(data: Mapping, state: Mapping) -> tuple[list[str] | None, list[dict]]:
    all_dialogues = state.get("dialogues") or []
    by_id = {str(item.get("dialogue_id") or ""): item for item in all_dialogues}
    requested = _data_value(data, "dialogueIds", "dialogue_ids")
    if requested is None:
        selected_ids = [item for item in by_id if item]
        request_value = None
    else:
        if not isinstance(requested, list) or any(not isinstance(item, str) or not item.strip() for item in requested):
            raise OrchestrationError("invalid_input", "dialogueIds must be a list of group dialogue IDs")
        selected_ids = [item.strip() for item in requested]
        if not selected_ids or len(set(selected_ids)) != len(selected_ids):
            raise OrchestrationError("invalid_input", "dialogueIds must be unique and non-empty")
        request_value = selected_ids
    if not selected_ids or any(item not in by_id for item in selected_ids):
        raise OrchestrationError("invalid_input", "dialogueIds must select existing group dialogue lines")
    return request_value, [by_id[item] for item in selected_ids]


def _check_bindings(dialogues: list[dict], bindings: list[dict[str, str]]) -> None:
    by_identity = {(item["character_id"], item["identity_id"]): item["version_id"] for item in bindings}
    missing = []
    for line in dialogues:
        key = (str(line.get("character_id") or ""), str(line.get("identity_id") or ""))
        if not by_identity.get(key):
            missing.append(f"{line.get('speaker') or key[0]}:{key[1] or 'default'}")
    if missing:
        raise OrchestrationError(
            "input_required", "Connect a fixed voice version for each selected dialogue identity: " + ", ".join(missing),
        )


def _preparation_id(run, episode: int, group_id: str, attempt_id: str) -> str:
    key = f"{run['project_id']}\0{episode}\0{group_id}\0{attempt_id}"
    return "agent-prep-" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def _preparation_input_fingerprint(
    *, episode: int, group_id: str, script_revision: str, source_revision: str,
    group_revision: int, dialogue_ids: list[str], bindings: list[dict[str, str]],
    performance: Mapping,
) -> str:
    return fingerprint({
        "episode": episode,
        "group_id": group_id,
        "script_revision": script_revision,
        "source_revision": source_revision,
        "group_revision": group_revision,
        "dialogue_ids": dialogue_ids,
        "voice_versions": bindings,
        "performance": dict(performance),
    })


def _preparation_request_fingerprint(
    *, episode: int, group_id: str, script_revision: str,
    requested_dialogue_ids: list[str] | None,
    bindings: list[dict[str, str]], performance: Mapping,
) -> str:
    return fingerprint({
        "episode": episode,
        "group_id": group_id,
        "script_revision": script_revision,
        "requested_dialogue_ids": requested_dialogue_ids,
        "voice_versions": bindings,
        "performance": dict(performance),
    })


def _task_for_request(state: Mapping, request_id: str) -> dict | None:
    for task in state.get("tasks") or []:
        if str(task.get("request_id") or "") == request_id:
            return task
    return None


def _submission(
    task: Mapping, *, episode: int, group_id: str, request_id: str, node_id: str,
) -> dict:
    if str(task.get("request_id") or "") != request_id:
        raise OrchestrationError("invalid_submission", "Dubbing task receipt does not match this attempt")
    task_id = str(task.get("task_id") or task.get("dubbing_task_id") or "")
    if not task_id:
        raise OrchestrationError("invalid_submission", "Dubbing receipt is missing its durable task id")
    return {
        "dubbing": True,
        "request_id": request_id,
        "dubbing_task_id": task_id,
        "task_id": str(task.get("backend_task_id") or task.get("task_id_backend") or ""),
        "preparation_id": str(task.get("preparation_id") or ""),
        "group_id": group_id,
        "episode": episode,
        "node_id": node_id,
        "status": str(task.get("status") or "submission_unknown"),
    }


async def _get_state(run, episode: int, group_id: str) -> dict:
    from novelvideo.api.routes import dubbing as api

    response = await api.get_dubbing(run["project_id"], episode, group_id, run["actor"])
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("data"), dict):
        raise OrchestrationError("dubbing_status_unavailable", "Unable to read narrative-group dubbing status")
    return response["data"]


async def _prepare_snapshot(
    run, attempt, node, *, state=None, create_if_missing=True, require_fingerprint=False,
):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.audio.dubbing_contracts import DubbingPreparation, DubbingPrepareRequest
    from pydantic import ValidationError

    episode, group_id = _scope(run, node)
    request_id = str(attempt.get("id") or "").strip()
    if not request_id:
        raise OrchestrationError("invalid_input", "A stable attempt id is required for dubbing preparation")
    preparation_id = _preparation_id(run, episode, group_id, request_id)
    if not create_if_missing:
        preparation_data = await _historical_preparation(run, episode, group_id, preparation_id)
        if preparation_data is None:
            return None
        try:
            preparation = DubbingPreparation.model_validate(preparation_data)
        except ValidationError as exc:
            raise OrchestrationError(
                "invalid_submission", "Dubbing preparation receipt failed validation",
                errors=exc.errors(include_url=False, include_input=False),
            ) from exc
        if (preparation.preparation_id != preparation_id
                or preparation.episode != episode or preparation.group_id != group_id):
            raise OrchestrationError("idempotency_conflict", "Dubbing preparation receipt does not match this attempt")
        if (require_fingerprint
                and (not preparation.request_fingerprint or not preparation.input_fingerprint)):
            raise OrchestrationError("invalid_submission", "Dubbing preparation receipt is missing fingerprints")
        snapshot = preparation.model_dump(mode="json")
        snapshot.update({
            "schema_version": 1,
            "input_fingerprint": preparation.input_fingerprint,
            "voice_versions": [item.model_dump(mode="json") for item in preparation.voice_versions],
        })
        return episode, group_id, state or {}, preparation, snapshot

    state = state if state is not None else await _get_state(run, episode, group_id)
    bindings = _voice_bindings(run, node)
    if not bindings:
        raise OrchestrationError("input_required", "Connect at least one pinned character voice version")
    data = node.get("data") or {}
    dialogue_ids, selected_dialogues = _selected_dialogues(data, state)
    performance = _performance_for_dialogues(data, episode, group_id, selected_dialogues)
    _check_bindings(selected_dialogues, bindings)
    script_revision = str(_data_value(
        data, "scriptRevision", "script_revision", state.get("script_revision") or "",
    ))
    if not script_revision:
        raise OrchestrationError("input_required", "The narrative group has no current script revision")
    try:
        body = DubbingPrepareRequest.model_validate({
            "preparation_id": preparation_id,
            "script_revision": script_revision,
            "dialogue_ids": dialogue_ids,
            "voice_versions": bindings,
            "performance": performance,
        })
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_input", "Dubbing preparation input failed validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc

    preparation_data = await _historical_preparation(run, episode, group_id, preparation_id)
    if preparation_data is None:
        if not create_if_missing:
            return None
        response = await api.prepare_dubbing(
            run["project_id"], episode, group_id, body, run["actor"],
        )
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise OrchestrationError("dubbing_prepare_failed", "Dubbing preparation did not succeed")
        preparation_data = response.get("data") or {}
    try:
        preparation = DubbingPreparation.model_validate(preparation_data)
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_submission", "Dubbing preparation receipt failed validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc
    if (preparation.preparation_id != preparation_id
            or preparation.episode != episode or preparation.group_id != group_id):
        raise OrchestrationError("idempotency_conflict", "Dubbing preparation receipt does not match this attempt")
    if preparation.script_revision != script_revision:
        raise OrchestrationError("input_conflict", "The prepared script revision changed; prepare dubbing again")
    if dialogue_ids is not None and list(preparation.dialogue_ids) != dialogue_ids:
        raise OrchestrationError("idempotency_conflict", "Dubbing preparation was reused for a different dialogue selection")
    if preparation.missing_voices:
        raise OrchestrationError(
            "input_required", "Dubbing preparation is missing fixed voice versions",
            missing_voices=list(preparation.missing_voices),
        )
    if preparation.issues:
        raise OrchestrationError(
            "dubbing_prepare_blocked", "Dubbing preparation reported blocking issues",
            issues=list(preparation.issues),
        )
    current_revision = state.get("revision")
    if type(current_revision) is not int:
        current_revision = preparation.expected_revision
    current_source_revision = str(state.get("source_revision") or preparation.source_revision)
    input_digest = _preparation_input_fingerprint(
        episode=episode, group_id=group_id, script_revision=script_revision,
        source_revision=current_source_revision, group_revision=current_revision,
        dialogue_ids=[str(item.get("dialogue_id") or "") for item in selected_dialogues],
        bindings=bindings,
        performance=performance,
    )
    request_digest = _preparation_request_fingerprint(
        episode=episode, group_id=group_id, script_revision=script_revision,
        requested_dialogue_ids=dialogue_ids,
        bindings=bindings,
        performance=performance,
    )
    if preparation.request_fingerprint and preparation.request_fingerprint != request_digest:
        raise OrchestrationError(
            "idempotency_conflict", "Dubbing preparation id was reused with different inputs",
        )
    if require_fingerprint and (not preparation.request_fingerprint or not preparation.input_fingerprint):
        raise OrchestrationError(
            "invalid_submission", "Dubbing preparation does not contain input fingerprints",
        )
    snapshot = preparation.model_dump(mode="json")
    snapshot.update({
        "schema_version": 1,
        "input_fingerprint": preparation.input_fingerprint or input_digest,
        "voice_versions": [item.model_dump(mode="json") for item in preparation.voice_versions] or bindings,
    })
    return episode, group_id, state, preparation, snapshot


async def _submit_prepare(run, attempt, node):
    episode, group_id, _state, preparation, snapshot = await _prepare_snapshot(
        run, attempt, node, require_fingerprint=True,
    )
    return {
        "local": True,
        "preparation_id": preparation.preparation_id,
        "result": {"dubbingPreparation": snapshot},
    }


async def _submit_generate(run, attempt, node):
    from novelvideo.api.routes import dubbing as api
    from novelvideo.audio.dubbing_contracts import DubbingGenerateRequest, DubbingPreparation
    from pydantic import ValidationError

    episode, group_id = _scope(run, node)
    request_id = str(attempt.get("id") or "").strip()
    if not request_id:
        raise OrchestrationError("invalid_input", "A stable attempt id is required for group dubbing")
    state = await _get_state(run, episode, group_id)
    previous = _task_for_request(state, request_id)
    if previous is not None:
        return _submission(
            previous, episode=episode, group_id=group_id, request_id=request_id,
            node_id=str(node.get("id") or ""),
        )

    recorded = (node.get("data") or {}).get("dubbingPreparation")
    if not isinstance(recorded, dict) or recorded.get("schema_version") != 1:
        raise OrchestrationError("input_required", "Prepare dubbing before generating audio")
    try:
        preparation = DubbingPreparation.model_validate({
            key: value for key, value in recorded.items()
            if key != "schema_version"
        })
    except ValidationError as exc:
        raise OrchestrationError("invalid_input", "The saved dubbing preparation is invalid") from exc
    if not preparation.request_fingerprint or not preparation.input_fingerprint:
        raise OrchestrationError("invalid_input", "The saved dubbing preparation has no input fingerprints")

    stored = await _historical_preparation(run, episode, group_id, preparation.preparation_id)
    try:
        stored_preparation = DubbingPreparation.model_validate(stored) if stored is not None else None
    except ValidationError as exc:
        raise OrchestrationError("invalid_input", "The durable dubbing preparation receipt is invalid") from exc
    if stored_preparation is None or stored_preparation != preparation:
        raise OrchestrationError("invalid_input", "The selected dubbing preparation receipt is unavailable or changed")

    bindings = _voice_bindings(run, node)
    if not bindings:
        raise OrchestrationError("input_required", "Connect at least one pinned character voice version")
    data = node.get("data") or {}
    dialogue_ids, selected_dialogues = _selected_dialogues(data, state)
    performance = _performance_for_dialogues(data, episode, group_id, selected_dialogues)
    _check_bindings(selected_dialogues, bindings)
    script_revision = str(_data_value(
        data, "scriptRevision", "script_revision", state.get("script_revision") or "",
    ))
    if not script_revision:
        raise OrchestrationError("input_required", "The narrative group has no current script revision")
    current_revision = state.get("revision")
    if type(current_revision) is not int:
        current_revision = preparation.expected_revision
    current_source_revision = str(state.get("source_revision") or "")
    input_digest = _preparation_input_fingerprint(
        episode=episode, group_id=group_id, script_revision=script_revision,
        source_revision=current_source_revision or preparation.source_revision,
        group_revision=current_revision,
        dialogue_ids=[str(item.get("dialogue_id") or "") for item in selected_dialogues],
        bindings=bindings,
        performance=performance,
    )
    request_digest = _preparation_request_fingerprint(
        episode=episode, group_id=group_id, script_revision=script_revision,
        requested_dialogue_ids=dialogue_ids,
        bindings=bindings,
        performance=performance,
    )
    if (recorded.get("input_fingerprint") != input_digest
            or preparation.input_fingerprint != input_digest
            or preparation.request_fingerprint != request_digest
            or list(recorded.get("voice_versions") or []) != bindings
            or preparation.dialogue_ids != tuple(
                str(item.get("dialogue_id") or "") for item in selected_dialogues
            )
            or preparation.script_revision != script_revision
            or preparation.source_revision != current_source_revision
            or preparation.expected_revision != current_revision):
        raise OrchestrationError("input_conflict", "Dubbing inputs changed after preparation; prepare a new snapshot")
    if preparation.missing_voices or preparation.issues:
        raise OrchestrationError("dubbing_prepare_blocked", "The saved preparation contains blocking issues")

    try:
        body = DubbingGenerateRequest.model_validate({
            "preparation_id": preparation.preparation_id,
            "expected_revision": preparation.expected_revision,
            "request_id": request_id,
        })
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_input", "Dubbing generation input failed validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc
    response = await api.generate_dubbing(run["project_id"], episode, group_id, body, run["actor"])
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise OrchestrationError("dubbing_submit_failed", "Dubbing task submission did not return a receipt")
    receipt = response.get("data") or {}
    if str(receipt.get("request_id") or "") != request_id:
        raise OrchestrationError("invalid_submission", "Dubbing service returned a different request id")
    return _submission({
        "task_id": receipt.get("dubbing_task_id"),
        "backend_task_id": receipt.get("task_id"),
        "request_id": receipt.get("request_id"),
        "preparation_id": preparation.preparation_id,
        "status": receipt.get("status"),
    }, episode=episode, group_id=group_id, request_id=request_id,
        node_id=str(node.get("id") or ""))


def _audition_input(data: Mapping) -> tuple[str, str, str]:
    draft = data.get("auditionDraft")
    if not isinstance(draft, Mapping) or set(draft) - {"text", "emotionPrompt", "voiceVersionId"}:
        raise OrchestrationError("invalid_input", "auditionDraft must contain only text, emotionPrompt, and voiceVersionId")
    text = draft.get("text")
    emotion = draft.get("emotionPrompt", "")
    version_id = draft.get("voiceVersionId")
    if not isinstance(text, str) or not text.strip() or len(text) > 10_000 or _AUDITION_URL.search(text):
        raise OrchestrationError("invalid_input", "Free audition text must be non-empty, bounded text without URLs")
    if not isinstance(emotion, str) or len(emotion) > 2_000 or _AUDITION_URL.search(emotion):
        raise OrchestrationError("invalid_input", "Free audition emotion prompt must be bounded text without URLs")
    if (not isinstance(version_id, str) or not version_id or version_id != version_id.strip()
            or len(version_id) > 128 or any(char in version_id for char in ("/", "\\", "%", "?", "#"))
            or any(ord(char) < 32 for char in version_id)):
        raise OrchestrationError("invalid_input", "Free audition requires a safe fixed voice version ID")
    return text, emotion, version_id


def _audition_voice_binding(run, node, version_id: str) -> dict[str, str]:
    snapshot = run.get("snapshot") or {}
    nodes = {item.get("id"): item for item in snapshot.get("nodes", [])}
    bindings: dict[tuple[str, str, str], dict[str, str]] = {}
    for edge in snapshot.get("edges", []):
        if edge.get("target") != node.get("id") or (edge.get("data") or {}).get("contextLink"):
            continue
        source = nodes.get(edge.get("source")) or {}
        if source.get("type") != "characterVoiceNode":
            continue
        data = source.get("data") or {}
        reference = data.get("voiceReference") or {}
        pinned_version = str(_data_value(data, "voiceVersionId", "voice_version_id", reference.get("version_id")) or "")
        if pinned_version != version_id:
            continue
        character_id = str(_data_value(data, "characterId", "character_id", reference.get("character_id")) or "").strip()
        identity_id = str(_data_value(data, "identityId", "identity_id", reference.get("identity_id")) or "")
        slot = str(_data_value(data, "slot", "slot", reference.get("slot")) or "default").strip()
        character_name = str(_data_value(data, "characterName", "character_name", reference.get("character_name")) or "")
        if not character_id or not slot or len(slot) > 128 or any(char in slot for char in ("/", "\\", "%", "?", "#")):
            raise OrchestrationError("invalid_input", "Connected fixed voice node has an invalid character identity or slot")
        key = (character_id, identity_id, slot)
        bindings[key] = {
            "character_id": character_id,
            "identity_id": identity_id,
            "slot": slot,
            "character_name": character_name,
            "version_id": version_id,
        }
    if not bindings:
        raise OrchestrationError(
            "input_required", "Free audition must select an adopted voice version from a connected character voice node",
        )
    if len(bindings) != 1:
        raise OrchestrationError("voice_binding_conflict", "Selected voice version is ambiguous across connected character identities")
    return next(iter(bindings.values()))


def _audition_job_id(project_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(f"{project_id}\0{attempt_id}".encode("utf-8")).hexdigest()
    return "agent-audition-" + digest[:48]


def _audition_fingerprint(text: str, emotion: str, binding: Mapping[str, str]) -> str:
    return fingerprint({
        "text": text,
        "emotion_prompt": emotion,
        "voice": {key: binding[key] for key in ("character_id", "identity_id", "slot", "version_id")},
    })


def _audition_submission(*, task, run, attempt, node, job_id, input_digest, version_id) -> dict:
    return {
        "audio_audition": True,
        "task_type": "freezone_audio_speech",
        "task_id": str(getattr(task, "task_id", "") or ""),
        "job_id": job_id,
        "request_id": str(attempt.get("id") or ""),
        "node_id": str(node.get("id") or ""),
        "input_fingerprint": input_digest,
        "voice_version_id": version_id,
    }


async def _audition_context(run, *, role: str):
    from novelvideo.api.routes.freezone import _resolve_freezone_project

    ctx, _username, _project, project_dir, _output_dir = await _resolve_freezone_project(
        run["project_id"], run["actor"], required_role=role,
    )
    if ctx is None:
        raise OrchestrationError("project_not_found", "Project does not exist")
    return ctx, project_dir


def _audition_task(ctx, job_id: str):
    from novelvideo.task_state import get_task_manager

    return get_task_manager().get_task_for_project(
        ctx, "freezone_audio_speech", 0, scope=job_id,
    )


async def _submit_audition(run, attempt, node):
    from novelvideo.api.routes import freezone
    from novelvideo.api.schemas import FreezoneAudioSpeechRequest
    from novelvideo.task_backend.limits import (
        GlobalLaneQueueLimitExceeded, ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded,
    )
    from pydantic import ValidationError

    request_id = str(attempt.get("id") or "").strip()
    if not request_id:
        raise OrchestrationError("invalid_input", "A stable attempt id is required for free audition")
    data = node.get("data") or {}
    text, emotion, version_id = _audition_input(data)
    binding = _audition_voice_binding(run, node, version_id)
    input_digest = _audition_fingerprint(text, emotion, binding)
    job_id = _audition_job_id(run["project_id"], request_id)
    ctx, project_dir = await _audition_context(run, role="editor")
    previous = _audition_task(ctx, job_id)
    if previous is not None:
        return _audition_submission(
            task=previous, run=run, attempt=attempt, node=node, job_id=job_id,
            input_digest=input_digest, version_id=version_id,
        )

    try:
        body = FreezoneAudioSpeechRequest.model_validate({
            "text": text,
            "emotion_prompt": emotion,
            "voice_ref": {
                "scope": "character_voice_version",
                "character_name": binding["character_name"],
                "character_id": binding["character_id"],
                "identity_id": binding["identity_id"],
                "slot": binding["slot"],
                "version_id": version_id,
            },
            # Deliberately omit target_episode / target_beat: audition output is isolated.
        })
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_input", "Free audition input failed speech request validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc

    try:
        await freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_audio_speech",
            job_id=job_id,
            payload={
                "text": body.text,
                "emotion_prompt": body.emotion_prompt,
                "voice_ref": body.voice_ref.model_dump(mode="json") if body.voice_ref else None,
                "account_voice_username": ctx.requester_username or ctx.owner_username,
                "target_episode": None,
                "target_beat": None,
            },
        )
    except (ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded, GlobalLaneQueueLimitExceeded) as exc:
        raise OrchestrationError("capacity_wait", str(exc)) from exc
    task = _audition_task(ctx, job_id)
    if task is None:
        raise OrchestrationError("submission_unknown", "Free audition queue did not return a durable task receipt")
    return _audition_submission(
        task=task, run=run, attempt=attempt, node=node, job_id=job_id,
        input_digest=input_digest, version_id=version_id,
    )


async def submit(run, attempt, node, operation):
    if (node.get("type"), operation) not in OPERATIONS:
        raise OrchestrationError("unsupported_operation", "Unsupported audio node dubbing operation")
    if operation == "audition":
        return await _submit_audition(run, attempt, node)
    if operation == "prepare_dubbing":
        return await _submit_prepare(run, attempt, node)
    if operation == "generate_dubbing":
        return await _submit_generate(run, attempt, node)

    from novelvideo.api.routes import dubbing as api
    from novelvideo.audio.dubbing_contracts import DubbingGenerateRequest
    from pydantic import ValidationError

    episode, group_id = _scope(run, node)
    request_id = str(attempt.get("id") or "").strip()
    if not request_id:
        raise OrchestrationError("invalid_input", "A stable attempt id is required for group dubbing")
    state = await _get_state(run, episode, group_id)
    previous = _task_for_request(state, request_id)
    if previous is not None:
        return _submission(
            previous, episode=episode, group_id=group_id, request_id=request_id,
            node_id=str(node.get("id") or ""),
        )

    _episode, _group_id, _state, preparation, _snapshot = await _prepare_snapshot(
        run, attempt, node, state=state,
    )
    try:
        generate_body = DubbingGenerateRequest.model_validate({
            "preparation_id": preparation.preparation_id,
            "expected_revision": preparation.expected_revision,
            "request_id": request_id,
        })
    except ValidationError as exc:
        raise OrchestrationError(
            "invalid_input", "Dubbing generation input failed validation",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc
    generated_response = await api.generate_dubbing(
        run["project_id"], episode, group_id, generate_body, run["actor"],
    )
    if not isinstance(generated_response, dict) or generated_response.get("ok") is not True:
        raise OrchestrationError("dubbing_submit_failed", "Dubbing task submission did not return a receipt")
    receipt = generated_response.get("data") or {}
    if str(receipt.get("request_id") or "") != request_id:
        raise OrchestrationError("invalid_submission", "Dubbing service returned a different request id")
    return _submission({
        "task_id": receipt.get("dubbing_task_id"),
        "backend_task_id": receipt.get("task_id"),
        "request_id": receipt.get("request_id"),
        "preparation_id": preparation.preparation_id,
        "status": receipt.get("status"),
    }, episode=episode, group_id=group_id, request_id=request_id,
        node_id=str(node.get("id") or ""))


async def reconcile_submission(run, attempt):
    node = next((item for item in run.get("snapshot", {}).get("nodes", [])
                 if item.get("id") == attempt.get("node_id")), None)
    operation = (node or {}).get("data", {}).get("operation", "group_dubbing")
    if node is None or (node.get("type"), operation) not in OPERATIONS:
        return {"status": "submission_unknown"}
    if operation == "audition":
        try:
            text, emotion, version_id = _audition_input(node.get("data") or {})
            binding = _audition_voice_binding(run, node, version_id)
            input_digest = _audition_fingerprint(text, emotion, binding)
            job_id = _audition_job_id(run["project_id"], str(attempt.get("id") or ""))
            ctx, _project_dir = await _audition_context(run, role="viewer")
        except OrchestrationError:
            return {"status": "submission_unknown"}
        task = _audition_task(ctx, job_id)
        if task is None:
            return {"status": "not_submitted", "evidence": "no_free_audition_task_receipt"}
        return {"status": "submitted", "submission": _audition_submission(
            task=task, run=run, attempt=attempt, node=node, job_id=job_id,
            input_digest=input_digest, version_id=version_id,
        )}
    try:
        episode, group_id = _scope(run, node)
        request_id = str(attempt.get("id") or "")
        if operation == "prepare_dubbing":
            prepared = await _prepare_snapshot(
                run, attempt, node, create_if_missing=False, require_fingerprint=True,
            )
            if prepared is None:
                return {"status": "not_submitted", "evidence": "no_dubbing_preparation_receipt"}
            _episode, _group, _state, preparation, snapshot = prepared
            return {"status": "submitted", "submission": {
                "local": True,
                "preparation_id": preparation.preparation_id,
                "result": {"dubbingPreparation": snapshot},
            }}
        state = await _get_state(run, episode, group_id)
    except OrchestrationError:
        return {"status": "submission_unknown"}
    task = _task_for_request(state, request_id)
    if task is None:
        # The service writes its durable task row before queue submission. A
        # successful status read without this attempt receipt proves no task
        # was accepted and permits a stable-id retry.
        return {"status": "not_submitted", "evidence": "no_dubbing_task_receipt"}
    return {"status": "submitted", "submission": _submission(
        task, episode=episode, group_id=group_id, request_id=request_id,
        node_id=str(node.get("id") or ""),
    )}


async def _historical_preparation(run, episode: int, group_id: str, preparation_id: str):
    """Load the task's immutable input snapshot when a newer prep is current."""
    from novelvideo.api.routes import dubbing as api
    from novelvideo.audio.dubbing_store import DubbingStore

    resolved, sqlite_store, _group = await api._group_scope(
        run["project_id"], episode, group_id, run["actor"], role="viewer",
    )
    try:
        preparation = DubbingStore(sqlite_store.db_path, resolved.project_dir).get_preparation(preparation_id)
        return preparation.model_dump(mode="json") if preparation is not None else None
    finally:
        await sqlite_store.close()


async def _preparation_for_task(state: Mapping, task: Mapping, run, episode: int, group_id: str):
    current = state.get("preparation") or {}
    preparation_id = str(task.get("preparation_id") or "")
    if isinstance(current, Mapping) and current.get("preparation_id") == preparation_id:
        return current
    # The latest-status contract exposes only the most recent preparation; a
    # task may still refer to an older immutable one after later editing.
    return await _historical_preparation(run, episode, group_id, preparation_id)


def _voice_binding_map(run, node):
    return {(item["character_id"], item["identity_id"]): item["version_id"]
            for item in _voice_bindings(run, node)}


def _candidate_takes(state: Mapping, task: Mapping, preparation: Mapping, bindings: Mapping) -> list[dict]:
    from novelvideo.audio.dubbing_store import line_input_hash

    internal_id = str(task.get("task_id") or "")
    selected = set(task.get("dialogue_ids") or [])
    dialogue_map = {str(item.get("dialogue_id") or ""): item
                    for item in preparation.get("dialogues", [])}
    expected: dict[str, tuple[str, str]] = {}
    for dialogue_id in selected:
        line = dialogue_map.get(dialogue_id)
        if not line:
            continue
        identity = (str(line.get("character_id") or ""), str(line.get("identity_id") or ""))
        version_id = bindings.get(identity)
        if not version_id or str(line.get("voice_version_id") or "") != version_id:
            raise OrchestrationError("invalid_artifact", "Dubbing preparation does not preserve the pinned voice version")
        expected[dialogue_id] = (line_input_hash(line), version_id)

    matches: dict[str, dict] = {}
    for take in state.get("takes") or []:
        dialogue_id = str(take.get("dialogue_id") or "")
        target = expected.get(dialogue_id)
        if not target or take.get("status") != "succeeded" or take.get("technical_status") != "passed":
            continue
        if (str(take.get("input_hash") or "") != target[0]
                or str(take.get("voice_version_id") or "") != target[1]):
            continue
        # DubbingStore deliberately reuses a take from an older preparation
        # when its audio-affecting input hash is identical. The pinned version
        # and hash above are the proof; requiring the same task/preparation
        # would hide valid reusable outputs from this completed task.
        old = matches.get(dialogue_id)
        if old is None or str(take.get("task_id") or "") == internal_id:
            matches[dialogue_id] = dict(take)
    return [matches[key] for key in sorted(matches)]


async def reconcile(run, submission):
    if submission.get("audio_audition"):
        return await _reconcile_audition(run, submission)
    node = next((item for item in run.get("snapshot", {}).get("nodes", [])
                 if item.get("id") == submission.get("node_id")), None)
    if node is None:
        # Node id is normally held by the attempt, and is copied into receipts
        # by submit below. Fall back to finding the unique AudioNode group.
        node = next((item for item in run.get("snapshot", {}).get("nodes", [])
                     if item.get("type") == "audioNode"
                     and str(_data_value(item.get("data") or {}, "groupId", "group_id", "")) == str(submission.get("group_id") or "")), None)
    if node is None:
        raise OrchestrationError("invalid_submission", "Dubbing node is missing from the run snapshot")
    episode, group_id = _scope(run, node)
    request_id = str(submission.get("request_id") or "")
    if (not submission.get("dubbing") or not request_id
            or group_id != str(submission.get("group_id") or "")
            or episode != submission.get("episode")):
        raise OrchestrationError("invalid_submission", "Dubbing receipt does not match the frozen node inputs")
    state = await _get_state(run, episode, group_id)
    task = _task_for_request(state, request_id)
    if task is None or str(task.get("task_id") or "") != str(submission.get("dubbing_task_id") or ""):
        return {"status": "submission_unknown"}
    status = str(task.get("status") or "submission_unknown")
    if status == "submission_unknown":
        return {"status": "submission_unknown", "error": task.get("error") or "Dubbing submission is still unknown"}
    if status in {"queued", "running"}:
        return {"status": "running"}
    if status == "failed":
        return {"status": "failed", "error": task.get("error") or "Group dubbing failed"}
    if status not in {"completed", "partial_failure"}:
        return {"status": "running"}

    preparation = await _preparation_for_task(state, task, run, episode, group_id)
    if preparation is None:
        raise OrchestrationError("invalid_artifact", "Dubbing preparation snapshot is unavailable")
    node_bindings = _voice_binding_map(run, node)
    candidates = _candidate_takes(state, task, preparation, node_bindings)
    if not candidates:
        return {"status": "failed", "error": "Group dubbing completed without verified audio takes"}
    result = {
        "dubbingTaskId": task["task_id"],
        "dubbingGroupId": group_id,
        "episodeNumber": episode,
        "dubbingStatus": status,
        "dubbingCandidates": candidates,
        "failedDialogueIds": list(task.get("failed_dialogue_ids") or []),
    }
    return {"status": "succeeded", "result": result}


async def _audition_node_and_inputs(run, submission):
    node = next((item for item in (run.get("snapshot") or {}).get("nodes", [])
                 if item.get("id") == submission.get("node_id")), None)
    if node is None or node.get("type") != "audioNode":
        raise OrchestrationError("invalid_submission", "Free audition node is missing from the run snapshot")
    text, emotion, version_id = _audition_input(node.get("data") or {})
    binding = _audition_voice_binding(run, node, version_id)
    expected_job_id = _audition_job_id(run["project_id"], str(submission.get("request_id") or ""))
    expected_fingerprint = _audition_fingerprint(text, emotion, binding)
    if (submission.get("task_type") != "freezone_audio_speech"
            or not submission.get("request_id")
            or submission.get("job_id") != expected_job_id
            or submission.get("voice_version_id") != version_id
            or submission.get("input_fingerprint") != expected_fingerprint):
        raise OrchestrationError("invalid_submission", "Free audition receipt does not match its fixed voice and text inputs")
    return node, text, emotion, version_id, expected_job_id


async def _reconcile_audition(run, submission):
    from novelvideo.api.routes import freezone

    _node, text, _emotion, version_id, job_id = await _audition_node_and_inputs(run, submission)
    ctx, _project_dir = await _audition_context(run, role="viewer")
    task = _audition_task(ctx, job_id)
    if task is None or str(task.task_id) != str(submission.get("task_id") or ""):
        return {"status": "submission_unknown"}
    if task.status in {"failed", "cancelled"}:
        return {"status": task.status, "error": task.error or "Free audition task did not complete"}
    if task.status != "completed":
        return {"status": "running"}
    response = await freezone.freezone_job_result(
        run["project_id"], "freezone_audio_speech", job_id, run["actor"],
    )
    result_data = response.get("data") if isinstance(response, dict) else None
    url = (result_data.get("url") or result_data.get("audio_url")) if isinstance(result_data, Mapping) else None
    if not isinstance(url, str) or not url.strip():
        return {"status": "failed", "error": "Free audition completed without an audio artifact"}
    return {"status": "succeeded", "result": {"auditionResult": {
        "url": url,
        "taskId": str(task.task_id),
        "jobId": job_id,
        "voiceVersionId": version_id,
        "text": text,
    }}}


async def verify(run, attempt):
    result = attempt.get("result") or {}
    submission = attempt.get("submission") or {}
    if submission.get("audio_audition"):
        return await _verify_audition(run, attempt)
    node = next((item for item in run.get("snapshot", {}).get("nodes", [])
                 if item.get("id") == attempt.get("node_id")), None)
    if node is None:
        raise OrchestrationError("invalid_artifact", "Dubbing audio node is missing from the run snapshot")
    if not submission.get("dubbing"):
        raise OrchestrationError("invalid_artifact", "Dubbing receipt is missing")
    episode, group_id = _scope(run, node)
    state = await _get_state(run, episode, group_id)
    task = _task_for_request(state, str(submission.get("request_id") or ""))
    if task is None or task.get("task_id") != submission.get("dubbing_task_id"):
        raise OrchestrationError("invalid_artifact", "Dubbing task receipt could not be verified")
    preparation = await _preparation_for_task(state, task, run, episode, group_id)
    if preparation is None:
        raise OrchestrationError("invalid_artifact", "Dubbing input snapshot could not be verified")
    bindings = _voice_binding_map(run, node)
    candidates = _candidate_takes(state, task, preparation, bindings)
    returned = result.get("dubbingCandidates") or []
    candidates_by_id = {str(item.get("take_id") or ""): item for item in candidates}
    if not isinstance(returned, list) or not returned:
        raise OrchestrationError("invalid_artifact", "Dubbing result contains no candidate takes")
    if any(str(item.get("take_id") or "") not in candidates_by_id for item in returned):
        raise OrchestrationError("invalid_artifact", "Dubbing result contains a take outside this immutable task")

    from novelvideo.api.deps import resolve_project_scope
    from novelvideo.freezone.orchestration.verification import verify_media
    import asyncio

    resolved = await resolve_project_scope(run["project_id"], run["actor"], required_role="viewer")
    artifacts = []
    for item in returned:
        verified = await asyncio.to_thread(
            verify_media, resolved.project_dir, run["project_id"], item.get("url", ""), "audio",
        )
        if verified["sha256"] != item.get("audio_sha256"):
            raise OrchestrationError("invalid_artifact", "Dubbing take hash does not match the persisted artifact")
        artifacts.append({
            "kind": "audio", "sha256": verified["sha256"], "url": item["url"],
            "take_id": item["take_id"], "dialogue_id": item["dialogue_id"],
            "voice_version_id": item["voice_version_id"],
        })
    return artifacts


async def _verify_audition(run, attempt):
    from novelvideo.api.routes import freezone
    from novelvideo.api.deps import resolve_project_scope
    from novelvideo.freezone.orchestration.verification import verify_media
    import asyncio

    submission = attempt.get("submission") or {}
    node_id = str(attempt.get("node_id") or "")
    if submission.get("node_id") != node_id:
        raise OrchestrationError("invalid_artifact", "Free audition receipt is bound to another node")
    node, _text, _emotion, version_id, job_id = await _audition_node_and_inputs(run, submission)
    ctx, _project_dir = await _audition_context(run, role="viewer")
    task = _audition_task(ctx, job_id)
    if task is None or task.status != "completed" or str(task.task_id) != str(submission.get("task_id") or ""):
        raise OrchestrationError("invalid_artifact", "Free audition task receipt could not be verified")
    response = await freezone.freezone_job_result(
        run["project_id"], "freezone_audio_speech", job_id, run["actor"],
    )
    result_data = response.get("data") if isinstance(response, dict) else None
    expected_url = (result_data.get("url") or result_data.get("audio_url")) if isinstance(result_data, Mapping) else None
    projected = (attempt.get("result") or {}).get("auditionResult")
    if (not isinstance(projected, Mapping) or projected.get("url") != expected_url
            or projected.get("taskId") != str(task.task_id)
            or projected.get("jobId") != job_id
            or projected.get("voiceVersionId") != version_id):
        raise OrchestrationError("invalid_artifact", "Projected audition result does not match the completed fixed-version task")
    resolved = await resolve_project_scope(run["project_id"], run["actor"], required_role="viewer")
    verified = await asyncio.to_thread(
        verify_media, resolved.project_dir, run["project_id"], projected["url"], "audio",
    )
    return [{
        "kind": "audio", "sha256": verified["sha256"], "url": projected["url"],
        "task_id": str(task.task_id), "voice_version_id": version_id,
    }]

