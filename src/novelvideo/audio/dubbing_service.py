"""Business operations for narrative-group dialogue dubbing."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from novelvideo.audio.dubbing_contracts import (
    DialogueSnapshot,
    DubbingPreparation,
    DubbingTake,
    VoiceVersionBinding,
)
from novelvideo.audio.dubbing_store import (
    DubbingRequestConflict,
    DubbingRevisionConflict,
    DubbingStore,
    line_input_hash,
)
from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
from novelvideo.media_capabilities.models import MediaCapability


def _canonical(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _preparation_request_fingerprint(
    *, episode: int, group_id: str, script_revision: str,
    requested_dialogue_ids: Sequence[str] | None,
    voice_bindings: Sequence[VoiceVersionBinding], performance: Mapping[str, Mapping[str, Any]],
) -> str:
    voice_versions = sorted(
        (item.model_dump(mode="json") for item in voice_bindings),
        key=lambda item: (item["character_id"], item["identity_id"], item["version_id"]),
    )
    value = {
        "episode": episode,
        "group_id": group_id,
        "script_revision": script_revision,
        "requested_dialogue_ids": list(requested_dialogue_ids) if requested_dialogue_ids is not None else None,
        "voice_versions": voice_versions,
        "performance": dict(performance),
    }
    return hashlib.sha256(_canonical(value)).hexdigest()


def configured_index_tts2_profile() -> dict[str, str]:
    """Return the configured v2 Index adapter identity without exposing secrets."""
    from novelvideo import config

    provider = str(config.INDEXTTS2_PROVIDER or "").strip().lower()
    if provider not in {"newapi", "fal"}:
        raise ValueError(f"IndexTTS2 provider is unsupported: {provider or 'unset'}")
    model = (
        str(config.INDEXTTS2_NEWAPI_MODEL or "").strip()
        if provider == "newapi"
        else "IndexTTS2"
    )
    if not model:
        raise ValueError("IndexTTS2 v2 model is not configured")
    normalized_model = model.lower().replace("_", "-")
    if "2.5" in normalized_model or "turbo" in normalized_model or "base" in normalized_model:
        raise ValueError("only the configured IndexTTS2 v2 model is supported")
    endpoint = (
        str(config.INDEXTTS2_FAL_ENDPOINT or "").strip()
        if provider == "fal"
        else str(config.get_effective_newapi_gateway_config().base_url or "").strip()
    )
    workflow_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]
    return {
        "provider": provider,
        "provider_id": f"indextts2-{provider}",
        "model": model,
        "workflow_version": f"IndexTTS2-v2:{provider}:{workflow_digest}",
    }


def calculate_dubbing_impact(
    *,
    previous: Sequence[DialogueSnapshot],
    current: Sequence[DialogueSnapshot],
    take_dialogue_ids: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Compare audio-affecting line inputs while preserving historical takes."""
    old = {item.dialogue_id: item for item in previous}
    new = {item.dialogue_id: item for item in current}
    take_ids = set(take_dialogue_ids)
    changed = [
        dialogue_id
        for dialogue_id, line in new.items()
        if dialogue_id in old and line_input_hash(line) != line_input_hash(old[dialogue_id])
    ]
    added = [dialogue_id for dialogue_id in new if dialogue_id not in old]
    deleted = [dialogue_id for dialogue_id in old if dialogue_id not in new]
    return {
        "stale_dialogue_ids": changed,
        "added_dialogue_ids": added,
        "deleted_dialogue_ids": deleted,
        "preserved_take_dialogue_ids": [item for item in deleted if item in take_ids],
    }


def voice_version_impact(
    dialogues: Sequence[DialogueSnapshot],
    *,
    character_id: str,
    identity_id: str,
    old_version_id: str,
    new_version_id: str,
) -> list[str]:
    """List only lines bound to the exact replaced character identity version."""
    if old_version_id == new_version_id:
        return []
    return [
        line.dialogue_id
        for line in dialogues
        if line.character_id == character_id
        and line.identity_id == identity_id
        and line.voice_version_id == old_version_id
    ]


def compile_dialogue_performance(text: str, performance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compile one IndexTTS2 line using the existing capability compiler."""
    from novelvideo.media_capabilities.tts.performance import compile_performance
    from novelvideo.media_capabilities.tts.workflow_variants import EmotionMode

    requested = dict(performance or {})
    direction = requested.get("direction", {})
    if isinstance(direction, str):
        direction = {"director_note": direction} if direction.strip() else {}
    if not isinstance(direction, Mapping):
        raise ValueError("performance direction must be text or an object")
    mode = str(requested.get("mode") or "neutral")
    payload = requested.get("payload")
    if payload is None:
        payload_key = {
            "text": "emotion_text",
            "emotion_text": "emotion_text",
            "audio": "emotion_audio",
            "emotion_audio": "emotion_audio",
            "vector": "emotion_vector",
            "emotion_vector": "emotion_vector",
        }.get(mode.strip().lower())
        if payload_key:
            payload = requested.get(payload_key)
    # Existing Fal/NewAPI IndexTTS2 adapters expose text emotion only. Audio
    # and vector modes stay unavailable until an adapter explicitly supports
    # those inputs; do not silently drop them.
    supported_modes = (EmotionMode.NEUTRAL.value, EmotionMode.EMOTION_TEXT.value)
    compiled = compile_performance(
        text=text,
        direction=direction,
        mode=mode,
        payload=payload,
        supported_modes=supported_modes,
        provider="indextts2",
    )
    profile = configured_index_tts2_profile()
    return {
        **compiled,
        "index_provider": profile["provider"],
        "model": profile["model"],
        "workflow_version": profile["workflow_version"],
    }


def make_source_snapshot_hash(
    *,
    script_revision: str,
    source_revision: str,
    dialogues: Sequence[DialogueSnapshot],
) -> str:
    data = {
        "script_revision": script_revision,
        "source_revision": source_revision,
        "dialogues": [item.model_dump(mode="json") for item in dialogues],
    }
    return hashlib.sha256(_canonical(data)).hexdigest()


def _group_manifest(project_dir: str | Path, episode: int, group_id: str):
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.narrative_groups.service import stage_payload

    root = Path(project_dir).resolve()
    state = stage_payload(root, episode, group_id, "video")
    stored = str(state.get("manifest_asset") or "").strip()
    candidate = Path(stored)
    if not stored:
        raise FileNotFoundError("narrative group video manifest is unavailable")
    if not candidate.is_absolute():
        candidate = root / candidate
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError("narrative group video manifest is unavailable")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    source_revision = f"video-r{int(state['revision'])}:{digest}"
    manifest = load_h3_director_manifest(path)
    return state, path, raw, source_revision, manifest


def _source_line_rows(manifest) -> list[dict[str, Any]]:
    from novelvideo.media_capabilities.video.h3_timeline import H3SourceDialogueLine

    rows: list[dict[str, Any]] = []
    for span_index, entry in enumerate(manifest.entries):
        segment = entry.segment
        lines = list(segment.dialogue_lines)
        if not lines and segment.dialogue.strip():
            lines = [H3SourceDialogueLine(speaker=segment.speaker, text=segment.dialogue)]
        duration = float(entry.actual_duration_seconds or segment.duration_seconds)
        for line_index, line in enumerate(lines):
            rows.append({
                "source_key": f"span:{span_index}:{segment.segment_id}:line:{line_index}",
                "speaker": line.speaker,
                "text": line.text,
                "target_duration_seconds": duration if len(lines) == 1 else None,
                "performance": {},
            })
    return rows


def _speaker_identity(speaker: str, characters) -> tuple[str, str] | None:
    matches: list[tuple[str, str]] = []
    for character in characters:
        character_id = str(getattr(character, "character_id", "") or "").strip()
        if not character_id:
            continue
        if speaker in {str(getattr(character, "name", "")), character_id}:
            matches.append((character_id, ""))
        for identity in getattr(character, "identities", None) or ():
            identity_id = str(getattr(identity, "identity_id", "") or "").strip()
            if identity_id and speaker == identity_id:
                matches.append((character_id, identity_id))
    distinct = list(dict.fromkeys(matches))
    return distinct[0] if len(distinct) == 1 else None


def load_group_dialogue_sources(
    *,
    project_dir: str | Path,
    episode: int,
    group,
    sqlite_store,
    dubbing_store: DubbingStore,
) -> tuple[list[DialogueSnapshot], str, str]:
    """Load group lines and durable legacy IDs for read-only UI projection."""
    group_id = str(group.id)
    script_revision = str(getattr(group, "director_revision_id", "") or "legacy")
    _, _, _, source_revision, manifest = _group_manifest(project_dir, episode, group_id)
    raw_lines = _source_line_rows(manifest)
    characters = sqlite_store.get_all_characters()
    for raw in raw_lines:
        resolved = _speaker_identity(str(raw["speaker"] or "").strip(), characters)
        raw["character_id"], raw["identity_id"] = resolved or (
            f"unresolved:{raw['speaker']}", ""
        )
        raw["script_revision"] = script_revision
    imported = dubbing_store.import_legacy_dialogues(
        episode=episode,
        group_id=group_id,
        source_revision=source_revision,
        lines=raw_lines,
    )
    return imported, script_revision, source_revision


def prepare_group_dubbing(
    *,
    project_dir: str | Path,
    episode: int,
    group,
    sqlite_store,
    dubbing_store: DubbingStore,
    voice_versions,
    script_revision: str,
    dialogue_ids: Sequence[str] | None,
    voice_bindings: Sequence[VoiceVersionBinding],
    performance: Mapping[str, Mapping[str, Any]],
    preparation_id: str | None = None,
) -> DubbingPreparation:
    """Freeze group dialogue IDs, selected voice versions and performance inputs."""
    group_id = str(group.id)
    if preparation_id:
        existing = dubbing_store.get_preparation(preparation_id)
        if existing is not None:
            request_fingerprint = _preparation_request_fingerprint(
                episode=episode, group_id=group_id, script_revision=script_revision,
                requested_dialogue_ids=dialogue_ids,
                voice_bindings=voice_bindings, performance=performance,
            )
            if (existing.episode != episode or existing.group_id != group_id
                    or not existing.request_fingerprint
                    or existing.request_fingerprint != request_fingerprint):
                raise DubbingRequestConflict("preparation id reused with different inputs")
            return existing
    current_script_revision = str(getattr(group, "director_revision_id", "") or "legacy")
    if script_revision != current_script_revision:
        raise DubbingRevisionConflict("script revision changed; refresh the group before preparing")
    imported, _, source_revision = load_group_dialogue_sources(
        project_dir=project_dir,
        episode=episode,
        group=group,
        sqlite_store=sqlite_store,
        dubbing_store=dubbing_store,
    )
    if not imported:
        raise ValueError("narrative group has no dialogue lines")
    state, _, _, _, _ = _group_manifest(project_dir, episode, group_id)
    characters = sqlite_store.get_all_characters()
    bindings = {(item.character_id, item.identity_id): item for item in voice_bindings}
    characters_by_id = {
        str(getattr(item, "character_id", "") or ""): item
        for item in characters
        if str(getattr(item, "character_id", "") or "")
    }
    for binding in voice_bindings:
        version = voice_versions.get_version(binding.version_id)
        if not isinstance(version, Mapping):
            raise ValueError(f"voice version not found: {binding.version_id}")
        if (
            str(version.get("character_id") or "") != binding.character_id
            or str(version.get("identity_id") or "") != binding.identity_id
        ):
            raise ValueError("voice version does not match the selected character identity")
        if binding.character_id not in characters_by_id:
            raise ValueError("voice version character is no longer in the project")

    previous = dubbing_store.latest_preparation(episode, group_id)
    previous_by_id = {item.dialogue_id: item for item in previous.dialogues} if previous else {}
    prepared_all: list[DialogueSnapshot] = []
    missing_voices: list[str] = []
    for line in imported:
        binding = bindings.get((line.character_id, line.identity_id))
        previous_line = previous_by_id.get(line.dialogue_id)
        voice_version_id = binding.version_id if binding else (
            previous_line.voice_version_id if previous_line else ""
        )
        perf_request = performance.get(line.dialogue_id)
        if perf_request is None and previous_line is not None:
            compiled_performance = previous_line.performance
        else:
            compiled_performance = compile_dialogue_performance(line.text, perf_request)
        if not voice_version_id:
            display = line.speaker or line.character_id
            missing_voices.append(f"{display}:{line.identity_id or 'default'}")
        elif binding:
            try:
                _voice_reference(
                    project_dir=Path(project_dir).resolve(),
                    version_store=voice_versions,
                    line=line.model_copy(update={"voice_version_id": voice_version_id}),
                )
            except Exception:
                display = line.speaker or line.character_id
                missing_voices.append(f"{display}:{line.identity_id or 'default'}")
        prepared_all.append(line.model_copy(update={
            "script_revision": current_script_revision,
            "voice_version_id": voice_version_id,
            "performance": compiled_performance,
        }))

    available_ids = {item.dialogue_id for item in prepared_all}
    selected_ids = list(dialogue_ids) if dialogue_ids is not None else [item.dialogue_id for item in prepared_all]
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("dialogue_ids must contain unique selected group dialogue IDs")
    if any(item not in available_ids for item in selected_ids):
        raise ValueError("dialogue_ids contain a line outside the narrative group")
    selected = tuple(item for item in prepared_all if item.dialogue_id in set(selected_ids))
    source_hash = make_source_snapshot_hash(
        script_revision=current_script_revision,
        source_revision=source_revision,
        dialogues=prepared_all,
    )
    group_revision = dubbing_store.get_group_state(episode, group_id)["revision"]
    voice_version_values = sorted(
        (item.model_dump(mode="json") for item in voice_bindings),
        key=lambda item: (item["character_id"], item["identity_id"], item["version_id"]),
    )
    request_fingerprint = _preparation_request_fingerprint(
        episode=episode, group_id=group_id, script_revision=current_script_revision,
        requested_dialogue_ids=dialogue_ids,
        voice_bindings=voice_bindings, performance=performance,
    )
    input_fingerprint = hashlib.sha256(_canonical({
        "episode": episode,
        "group_id": group_id,
        "script_revision": current_script_revision,
        "source_revision": source_revision,
        "group_revision": group_revision,
        "dialogue_ids": selected_ids,
        "voice_versions": voice_version_values,
        "performance": dict(performance),
    })).hexdigest()
    preparation = DubbingPreparation(
        preparation_id=preparation_id or uuid4().hex,
        episode=episode,
        group_id=group_id,
        script_revision=current_script_revision,
        source_revision=source_revision,
        expected_revision=group_revision,
        source_snapshot_hash=source_hash,
        dialogue_ids=tuple(selected_ids),
        requested_dialogue_ids=tuple(dialogue_ids) if dialogue_ids is not None else None,
        voice_versions=tuple(voice_bindings),
        request_fingerprint=request_fingerprint,
        input_fingerprint=input_fingerprint,
        source_dialogue_ids=tuple(item.dialogue_id for item in prepared_all),
        dialogues=selected,
        source_dialogues=tuple(prepared_all),
        missing_voices=tuple(dict.fromkeys(missing_voices)),
    )
    dubbing_store.save_preparation(preparation)
    return preparation


def current_group_source_revision(project_dir: str | Path, episode: int, group_id: str) -> str:
    return _group_manifest(project_dir, episode, group_id)[3]


def validate_preparation_current(project_dir: str | Path, preparation: DubbingPreparation) -> None:
    from novelvideo.director_plan.store import DirectorPlanStore

    current_source = current_group_source_revision(
        project_dir, preparation.episode, preparation.group_id
    )
    if current_source != preparation.source_revision:
        raise DubbingRevisionConflict("group source changed; prepare a new dubbing snapshot")
    active_plan = DirectorPlanStore(project_dir).load_active(preparation.episode)
    current_script = str(active_plan.revision_id if active_plan is not None else "legacy")
    if current_script != preparation.script_revision:
        raise DubbingRevisionConflict("script revision changed; prepare a new dubbing snapshot")


def dubbing_task_scope(group_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{group_id}\0{request_id}".encode("utf-8")).hexdigest()[:32]
    return f"dubbing-{digest}"


def _project_task_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "succeeded", "success"}:
        return "completed"
    if normalized in {"partial_failure"}:
        return "partial_failure"
    if normalized in {"failed", "failure", "cancelled", "canceled"}:
        return "failed"
    if normalized in {"running", "processing", "started"}:
        return "running"
    return "queued"


def _find_project_task(task_manager, ctx, episode: int, scope: str):
    if task_manager is None:
        from novelvideo.task_state import get_task_manager

        task_manager = get_task_manager()
    try:
        return task_manager.get_task_for_project(
            ctx,
            "narrative_group_dubbing",
            episode,
            scope=scope,
        )
    except Exception:
        # Corrupt/unavailable task state is not proof that submission did not
        # happen. Callers keep submission_unknown and avoid another charge.
        return None


async def enqueue_group_dubbing(
    *,
    ctx,
    dubbing_store: DubbingStore,
    preparation: DubbingPreparation,
    expected_revision: int,
    request_id: str,
    dialogue_ids: Sequence[str] | None = None,
    task_backend=None,
    task_manager=None,
    media_store=None,
) -> dict[str, Any]:
    """Persist intent, reconcile unknown submissions, then queue one group task."""
    from novelvideo.ports import get_task_backend

    if not request_id.strip():
        raise ValueError("request_id is required")
    current_revision = dubbing_store.get_group_state(
        preparation.episode, preparation.group_id
    )["revision"]
    if current_revision != expected_revision or expected_revision != preparation.expected_revision:
        raise DubbingRevisionConflict("dubbing group revision changed; prepare again")
    validate_preparation_current(ctx.output_dir, preparation)
    selected = tuple(dialogue_ids) if dialogue_ids is not None else preparation.dialogue_ids
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("dialogue_ids must contain unique selected group dialogue IDs")
    if not set(selected).issubset(set(preparation.dialogue_ids)):
        raise ValueError("dialogue_ids are outside this preparation")
    lines = {item.dialogue_id: item for item in preparation.dialogues}
    missing = [item for item in selected if not lines[item].voice_version_id]
    if missing:
        raise ValueError("missing fixed voice versions for: " + ", ".join(missing))

    profile = configured_index_tts2_profile()
    prepared_profiles = {
        (
            str((lines[item].performance or {}).get("index_provider") or ""),
            str((lines[item].performance or {}).get("model") or ""),
            str((lines[item].performance or {}).get("workflow_version") or ""),
        )
        for item in selected
    }
    expected_profile = (profile["provider"], profile["model"], profile["workflow_version"])
    if prepared_profiles != {expected_profile}:
        raise DubbingRevisionConflict("IndexTTS2 provider configuration changed; prepare again")
    payload = {
        "preparation_id": preparation.preparation_id,
        "source_snapshot_hash": preparation.source_snapshot_hash,
        "dialogue_ids": list(selected),
        "provider_id": profile["provider_id"],
        "provider": profile["provider"],
        "model": profile["model"],
        "workflow_version": profile["workflow_version"],
        "compiler_version": str(
            (lines[selected[0]].performance or {}).get("compiler_version") or "1"
        ),
    }
    payload_hash = hashlib.sha256(_canonical(payload)).hexdigest()
    task, created = dubbing_store.create_task(
        episode=preparation.episode,
        group_id=preparation.group_id,
        preparation_id=preparation.preparation_id,
        request_id=request_id,
        payload_hash=payload_hash,
        dialogue_ids=tuple(selected),
    )
    scope = dubbing_task_scope(preparation.group_id, request_id)
    if not created:
        persisted = _find_project_task(
            task_manager, ctx, preparation.episode, scope
        )
        if persisted is None:
            # The request may have reached the queue while its response was
            # lost. A read-only miss is still treated as unknown; never double
            # submit the same request id from this recovery path.
            task = dubbing_store.update_task(
                task.task_id,
                status="submission_unknown",
                error="submission not found yet; reconcile task status before retrying",
            )
        else:
            task = dubbing_store.update_task(
                task.task_id,
                status=_project_task_status(persisted.status),
                backend_task_id=persisted.task_id,
            )
        return _task_response(task)

    backend = task_backend or get_task_backend()
    try:
        queued = await backend.enqueue_project_task(
            ctx,
            task_type="narrative_group_dubbing",
            queue_kind="default",
            episode=preparation.episode,
            scope=scope,
            payload={
                "internal_task_id": task.task_id,
                "preparation_id": preparation.preparation_id,
                "episode": preparation.episode,
                "group_id": preparation.group_id,
                "dialogue_ids": list(selected),
                "source_revision": preparation.source_revision,
                "script_revision": preparation.script_revision,
                "provider": profile["provider"],
                "provider_id": profile["provider_id"],
                "model": profile["model"],
                "workflow_version": profile["workflow_version"],
                "compiler_version": payload["compiler_version"],
            },
        )
    except Exception as exc:
        unknown = dubbing_store.update_task(
            task.task_id,
            status="submission_unknown",
            error=str(exc)[:1000],
        )
        return _task_response(unknown)
    state = getattr(queued, "task_state", None)
    task_id = str(getattr(state, "task_id", "") or "")
    if not task_id:
        unknown = dubbing_store.update_task(
            task.task_id,
            status="submission_unknown",
            error="task backend returned no task id",
        )
        return _task_response(unknown)
    submitted = dubbing_store.update_task(
        task.task_id,
        status="queued",
        backend_task_id=task_id,
        error="",
    )
    return _task_response(submitted)


def _task_response(task) -> dict[str, Any]:
    return {
        "dubbing_task_id": task.task_id,
        "task_id": task.backend_task_id or None,
        "request_id": task.request_id,
        "status": task.status,
        "failed_dialogue_ids": list(task.failed_dialogue_ids),
        "error": task.error,
    }


async def retry_group_dubbing(
    *,
    ctx,
    dubbing_store: DubbingStore,
    task_id: str,
    failed_dialogue_ids: Sequence[str],
    request_id: str,
    task_backend=None,
    task_manager=None,
    media_store=None,
) -> dict[str, Any]:
    previous = dubbing_store.get_task(task_id) or dubbing_store.get_task_by_backend_id(task_id)
    if previous is None:
        raise KeyError(task_id)
    if previous.status not in {"failed", "partial_failure"}:
        raise ValueError("only failed dubbing tasks can be retried")
    selected = tuple(failed_dialogue_ids)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("failed_dialogue_ids must be unique and non-empty")
    if not set(selected).issubset(set(previous.failed_dialogue_ids)):
        raise ValueError("retry may include only failed dialogue IDs")
    preparation = dubbing_store.get_preparation(previous.preparation_id)
    if preparation is None:
        raise ValueError("dubbing preparation is unavailable")
    return await enqueue_group_dubbing(
        ctx=ctx,
        dubbing_store=dubbing_store,
        preparation=preparation,
        expected_revision=preparation.expected_revision,
        request_id=request_id,
        dialogue_ids=selected,
        task_backend=task_backend,
        task_manager=task_manager,
        media_store=media_store,
    )


def _absolute_voice_path(project_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    resolved = path.resolve()
    if not resolved.is_relative_to(project_dir.resolve()):
        raise ValueError("voice version path is outside the project")
    if not resolved.is_file():
        raise FileNotFoundError("voice version audio is missing")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _voice_reference(
    *,
    project_dir: Path,
    version_store,
    line: DialogueSnapshot,
) -> tuple[Path, str]:
    version = version_store.get_version(line.voice_version_id)
    if not isinstance(version, Mapping):
        raise LookupError(f"missing voice version: {line.character_id}:{line.identity_id}")
    if (
        str(version.get("version_id") or "") != line.voice_version_id
        or str(version.get("character_id") or "") != line.character_id
        or str(version.get("identity_id") or "") != line.identity_id
    ):
        raise ValueError("voice version does not match the dialogue character identity")
    path = _absolute_voice_path(project_dir, str(version.get("path") or ""))
    expected_hash = str(version.get("audio_sha256") or "").lower()
    if len(expected_hash) != 64 or _sha256_file(path) != expected_hash:
        raise ValueError("voice version audio integrity mismatch")
    return path, expected_hash


async def _resolve_fixed_line(line: DialogueSnapshot, path: Path, digest: str, sqlite_store):
    from novelvideo.audio.narrative_group_dubbing import resolve_lines
    from novelvideo.media_capabilities.video.h3_timeline import H3SourceDialogueLine

    source_line = H3SourceDialogueLine(
        speaker=line.speaker or line.character_id,
        text=line.text,
    )
    segment = SimpleNamespace(dialogue_lines=(source_line,), dialogue="", speaker="")

    async def resolver(beat, _store):
        if beat.get("speaker") != source_line.speaker:
            return None
        return path, digest

    resolved = await resolve_lines(segment, sqlite_store, resolver=resolver)
    return resolved[0]


async def _synthesize(
    *,
    generator,
    text: str,
    reference_path: Path,
    output_path: Path,
    compiled: Mapping[str, Any],
):
    effective = compiled.get("effective") or {}
    params = inspect.signature(generator.generate).parameters
    kwargs: dict[str, Any] = {
        "prompt": str(effective.get("text") or text),
        "output_path": output_path,
    }
    if "reference_audio_path" in params:
        kwargs["reference_audio_path"] = reference_path
    else:
        # Preserve the existing adapter boundary for audio_url-based Index
        # wrappers. The selected production generator uses the reference path.
        from novelvideo.seedance2_i2v.voice_clone import build_reference_audio_url

        if effective.get("mode") not in {None, "neutral", "text"}:
            raise ValueError("selected Index adapter does not support this emotion mode")
        kwargs["audio_url"] = build_reference_audio_url(reference_path)
    if "emotion_prompt" in params:
        kwargs["emotion_prompt"] = str(effective.get("emotion_text") or "")
    if "emotion_audio_path" in params and effective.get("emotion_path"):
        kwargs["emotion_audio_path"] = Path(effective["emotion_path"])
    elif effective.get("mode") == "audio":
        raise ValueError("selected Index adapter does not support emotion audio")
    if "emotion_vector" in params and effective.get("emotion_vector") is not None:
        kwargs["emotion_vector"] = effective["emotion_vector"]
    elif effective.get("mode") == "vector":
        raise ValueError("selected Index adapter does not support emotion vector")
    if "emotion_alpha" in params:
        kwargs["emotion_alpha"] = 0.6 if effective.get("mode") == "text" else 1.0
    result = generator.generate(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def run_dubbing_task(
    *,
    store: DubbingStore,
    task_id: str,
    project_dir: str | Path,
    sqlite_store,
    voice_versions,
    generator=None,
    coordinator: ProviderConcurrencyCoordinator,
    provider_id: str,
    provider: str | None = None,
    model: str | None = None,
    workflow_version: str | None = None,
) -> dict[str, Any]:
    """Run a durable group task; only successful immutable takes are published."""
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    preparation = store.get_preparation(task.preparation_id)
    if preparation is None:
        raise ValueError("dubbing preparation is unavailable")
    lines_by_id = {line.dialogue_id: line for line in preparation.dialogues}
    if any(dialogue_id not in lines_by_id for dialogue_id in task.dialogue_ids):
        raise ValueError("dubbing task scope is outside its preparation")
    selected = [lines_by_id[item] for item in task.dialogue_ids]
    if generator is None:
        from novelvideo.audio.narrative_group_dubbing import _default_indextts2_generator

        generator = _default_indextts2_generator()

    store.update_task(task_id, status="running")
    reusable: dict[str, DubbingTake] = {}
    pending: list[DialogueSnapshot] = []
    for line in selected:
        take = store.find_reusable_take(
            preparation.episode, preparation.group_id, line.dialogue_id, line_input_hash(line)
        )
        if take:
            reusable[line.dialogue_id] = take
        else:
            pending.append(line)

    references: dict[str, tuple[Path, str]] = {}
    preflight_errors: dict[str, str] = {}
    for line in pending:
        try:
            references[line.dialogue_id] = _voice_reference(
                project_dir=Path(project_dir).resolve(), version_store=voice_versions, line=line
            )
        except Exception as exc:
            preflight_errors[line.dialogue_id] = str(exc)
    resolved_lines: dict[str, tuple[Any, Path, str]] = {}
    if not preflight_errors:
        for line in pending:
            try:
                resolved_lines[line.dialogue_id] = await _resolve_fixed_line(
                    line, *references[line.dialogue_id], sqlite_store
                )
            except Exception as exc:
                preflight_errors[line.dialogue_id] = str(exc)
    if preflight_errors:
        failed = tuple(line.dialogue_id for line in pending)
        status = "partial_failure" if reusable else "failed"
        message = "; ".join(f"{key}: {value}" for key, value in preflight_errors.items())
        store.update_task(
            task_id, status=status, failed_dialogue_ids=failed, error=message
        )
        return {
            "status": status,
            "take_ids": [item.take_id for item in reusable.values()],
            "reused_take_ids": [item.take_id for item in reusable.values()],
            "failed_dialogue_ids": list(failed),
            "errors": preflight_errors,
        }

    if generator is None:
        raise RuntimeError("IndexTTS2 generator is unavailable")
    if not pending:
        store.update_task(task_id, status="completed", failed_dialogue_ids=())
        return {
            "status": "completed",
            "take_ids": [item.take_id for item in reusable.values()],
            "reused_take_ids": [item.take_id for item in reusable.values()],
            "failed_dialogue_ids": [],
            "errors": {},
        }

    root = Path(project_dir).resolve()
    take_ids: list[str] = [item.take_id for item in reusable.values()]
    errors: dict[str, str] = {}

    async def synthesize(line: DialogueSnapshot) -> tuple[str, str | None]:
        input_hash = line_input_hash(line)
        take_id = uuid4().hex
        relative_path = Path("assets") / "dubbing" / f"ep{preparation.episode:03d}" / preparation.group_id / line.dialogue_id / f"{take_id}.wav"
        output = root / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        compiled = line.performance
        if "effective" not in compiled:
            compiled = compile_dialogue_performance(line.text, compiled)
        try:
            async with coordinator.lease(provider_id, MediaCapability.TTS_VOICE_CLONE):
                result = await _synthesize(
                    generator=generator,
                    text=line.text,
                    reference_path=references[line.dialogue_id][0],
                    output_path=output,
                    compiled=compiled,
                )
            if not getattr(result, "success", False):
                raise RuntimeError(str(getattr(result, "error", "IndexTTS2 generation failed")))
            if not output.is_file() or output.stat().st_size <= 0:
                raise ValueError("IndexTTS2 produced no audio file")
            from novelvideo.audio.narrative_group_dubbing import _probe_audio_duration

            duration = await _probe_audio_duration(output)
            digest = _sha256_file(output)
            target = line.target_duration_seconds
            take = DubbingTake(
                take_id=take_id,
                preparation_id=preparation.preparation_id,
                task_id=task_id,
                dialogue_id=line.dialogue_id,
                input_hash=input_hash,
                voice_version_id=line.voice_version_id,
                audio_path=relative_path.as_posix(),
                audio_sha256=digest,
                actual_duration_seconds=duration,
                target_duration_seconds=target,
                duration_delta_seconds=(duration - target) if target is not None else None,
                provider=provider or str(compiled.get("index_provider") or "indextts2"),
                model=model or str(compiled.get("model") or "IndexTTS2-v2"),
                workflow_version=workflow_version or str(compiled.get("workflow_version") or ""),
                compiler_version=str(compiled.get("compiler_version") or "1"),
                effective_parameters=dict(compiled.get("effective") or {}),
            )
            store.save_take(take)
            return take_id, None
        except Exception as exc:
            return line.dialogue_id, str(exc)

    outcomes = await asyncio.gather(*(synthesize(line) for line in pending))
    failed_ids: list[str] = []
    for item_id, error in outcomes:
        if error is None:
            take_ids.append(item_id)
        else:
            failed_ids.append(item_id)
            errors[item_id] = error
    state = "completed" if not failed_ids else "partial_failure" if take_ids else "failed"
    store.update_task(
        task_id, status=state, failed_dialogue_ids=tuple(failed_ids),
        error="; ".join(f"{key}: {value}" for key, value in errors.items()),
    )
    return {
        "status": state,
        "take_ids": take_ids,
        "reused_take_ids": [item.take_id for item in reusable.values()],
        "failed_dialogue_ids": failed_ids,
        "errors": errors,
    }


__all__ = [
    "calculate_dubbing_impact",
    "compile_dialogue_performance",
    "make_source_snapshot_hash",
    "load_group_dialogue_sources",
    "run_dubbing_task",
    "voice_version_impact",
]
