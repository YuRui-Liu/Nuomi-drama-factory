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
    from novelvideo.seedance2_i2v.character_voice_storage import persist_character_voice_file
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
        progress(0.15, "提交 RunningHub Qwen3 音色设计...")
        runtime = load_runninghub_runtime_configuration(
            get_media_capability_store(), get_media_credential_resolver()
        )
        content, filename = await generate_qwen3_voice_sample(
            runtime,
            audition_text=str(payload["audition_text"]),
            voice_description=str(payload["voice_description"]),
            language=str(payload.get("language") or "Chinese"),
        )
        progress(0.85, "保存角色试听音频...")
        rel_path, sha256, updated_at = persist_character_voice_file(
            project_dir=ctx.output_dir, character_name=name, slot=slot,
            filename=filename, content=content,
        )
        if slot == "default":
            fields = {
                "reference_audio_path": rel_path,
                "reference_audio_sha256": sha256,
                "reference_audio_updated_at": updated_at,
            }
        else:
            samples = dict(character.voice_samples_by_age_group or {})
            samples[slot] = {"path": rel_path, "sha256": sha256, "updated_at": updated_at}
            fields = {"voice_samples_by_age_group": samples}
        await store.update_character(name, **fields)
        progress(1.0, "Qwen3 音色设计完成")
        return {"character_name": name, "slot": slot, "path": rel_path, "sha256": sha256}
    finally:
        await store.close()


register_project_task_runner("character_voice_design", run_voice_design)
