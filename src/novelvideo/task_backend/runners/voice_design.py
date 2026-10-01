"""Durable project task for RunningHub Qwen3 voice design."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def run_voice_design(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_voice_design(envelope, ctx),
            envelope,
            task_type="character_voice_design",
        )
    )


async def _run_voice_design(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_runninghub_runtime_configuration
    from novelvideo.media_capabilities.tts.runninghub_voice_design import generate_qwen3_voice_sample
    from novelvideo.media_capabilities.models import MediaCapability
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.character_voice import character_voice_snapshot, voice_snapshot_digest
    from novelvideo.media_capabilities.tts.quality import check_voice_audio
    from novelvideo.task_backend.cancel import raise_if_envelope_cancel_requested
    from novelvideo.sqlite_store import SQLiteStore

    payload = envelope.get("payload") or {}
    name = str(payload["character_name"])
    slot = str(payload["slot"])
    manager = get_task_manager()

    def progress(value: float, message: str) -> None:
        manager.update_progress_for_project(
            ctx, "character_voice_design", 0,
            scope=str(envelope.get("scope") or ""), progress=value,
            current_task=message, logs=[message],
        )

    store = SQLiteStore(ctx.owner_project_label, output_dir=str(ctx.output_dir), state_dir=str(ctx.state_dir))
    await store.initialize()
    try:
        await store.load_graph_state()
        character = store.get_character(name)
        if character is None:
            raise RuntimeError(f"角色不存在: {name}")
        if not payload.get("profile_digest") or not payload.get("request_id"):
            raise ValueError("voice request snapshot is missing; prepare a new request")
        if voice_snapshot_digest(character_voice_snapshot(character, db_path=store.db_path)) != payload["profile_digest"]:
            raise ValueError("voice request snapshot is stale; character facts changed")
        if character.voice_facts.vocalization_mode not in {"dialogue", "both"} or character.voice_facts.conflicts:
            raise ValueError("vocalization_not_dialogue")
        progress(0.15, "提交 RunningHub Qwen3 音色设计...")
        runtime = load_runninghub_runtime_configuration(
            get_media_capability_store(), get_media_credential_resolver()
        )
        candidates = VoiceCandidateStore(store.db_path, ctx.output_dir)
        saved_payload = {**payload, "workflow_id": runtime.workflow_id(MediaCapability.TTS_VOICE_DESIGN),
                         "effective_language": "Auto", "model_verified": False}
        candidate = candidates.create(str(payload["request_id"]), saved_payload)
        candidate_id = candidate["candidate_id"]
        if not candidate["path"]:
            raise_if_envelope_cancel_requested(envelope, task_type="character_voice_design")
            if not candidate["provider_task_id"] and not candidates.claim_submission(candidate_id):
                raise ValueError("submission_unknown: reconcile supplier task before retrying")

            async def on_submitted(task_id: str) -> None:
                candidates.set_provider_task(candidate_id, task_id)

            content, filename = await generate_qwen3_voice_sample(
                runtime, audition_text=str(payload["audition_text"]),
                voice_description=str(payload["voice_description"]),
                language=str(payload.get("language") or "Chinese"),
                provider_task_id=candidate["provider_task_id"], on_submitted=on_submitted,
            )
            candidate = candidates.save_audio(candidate_id, content, filename)
        if not candidate["report"]:
            progress(0.85, "检查试听候选；不会覆盖已选声线...")
            content = candidates.read_audio(candidate_id)
            report = await asyncio.to_thread(check_voice_audio, content, kind="dialogue")
            candidate = candidates.save_report(candidate_id, report)
        raise_if_envelope_cancel_requested(envelope, task_type="character_voice_design")
        progress(1.0, f"候选已保存，质检状态：{candidate['report'].get('status', 'qc_unavailable')}")
        return {"character_name": name, "slot": slot, "candidate_id": candidate_id,
                "path": candidate["path"], "sha256": candidate["sha256"],
                "quality_status": candidate["report"].get("status", "qc_unavailable"), "published": False}
    finally:
        await store.close()


register_project_task_runner("character_voice_design", run_voice_design)
