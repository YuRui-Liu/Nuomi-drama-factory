"""Production runner for one immutable narrative-group dubbing request."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.media_capabilities.concurrency import (
    DEFAULT_QUEUE_LIMIT,
    ProviderConcurrencyCoordinator,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner

_INDEX_TTS2_COORDINATORS: dict[str, ProviderConcurrencyCoordinator] = {}


def _configured_generator(profile: dict[str, str]):
    from novelvideo.generators.indextts2_fal import IndexTTS2FalClient

    return IndexTTS2FalClient(
        provider=profile["provider"],
        model=profile["model"],
    )


def _configured_coordinator(provider_id: str) -> ProviderConcurrencyCoordinator:
    from novelvideo.task_concurrency_settings import load_task_concurrency_settings

    concurrency = load_task_concurrency_settings().lanes.get("default")
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("saved default task concurrency is unavailable")
    coordinator = _INDEX_TTS2_COORDINATORS.setdefault(
        provider_id, ProviderConcurrencyCoordinator()
    )
    coordinator.configure(
        provider_id,
        max_concurrency=concurrency,
        queue_limit=DEFAULT_QUEUE_LIMIT,
    )
    return coordinator


def run_narrative_group_dubbing(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_narrative_group_dubbing(envelope, ctx),
            envelope,
            task_type="narrative_group_dubbing",
        )
    )


async def _run_narrative_group_dubbing(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.audio.dubbing_service import (
        configured_index_tts2_profile,
        run_dubbing_task,
    )
    from novelvideo.audio.dubbing_store import DubbingStore
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore
    from novelvideo.sqlite_store import SQLiteStore

    payload = envelope.get("payload") or {}
    internal_task_id = str(payload.get("internal_task_id") or "")
    episode = int(payload.get("episode") or 0)
    group_id = str(payload.get("group_id") or "")
    if not internal_task_id or episode < 1 or not group_id:
        raise ValueError("narrative group dubbing task payload is incomplete")

    sqlite_store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await sqlite_store.initialize()
    await sqlite_store.load_graph_state()
    dubbing_store = DubbingStore(sqlite_store.db_path, ctx.output_dir)
    try:
        profile = configured_index_tts2_profile()
        expected_profile = {
            "provider": str(payload.get("provider") or ""),
            "provider_id": str(payload.get("provider_id") or ""),
            "model": str(payload.get("model") or ""),
            "workflow_version": str(payload.get("workflow_version") or ""),
        }
        if expected_profile != profile:
            raise ValueError("IndexTTS2 provider configuration changed; prepare a new dubbing task")
        coordinator = _configured_coordinator(profile["provider_id"])
        generator = _configured_generator(profile)
        return await run_dubbing_task(
            store=dubbing_store,
            task_id=internal_task_id,
            project_dir=ctx.output_dir,
            sqlite_store=sqlite_store,
            voice_versions=VoiceVersionStore(sqlite_store.db_path),
            generator=generator,
            coordinator=coordinator,
            provider_id=profile["provider_id"],
            provider=profile["provider"],
            model=profile["model"],
            workflow_version=profile["workflow_version"],
        )
    except BaseException as exc:
        current = dubbing_store.get_task(internal_task_id)
        if current is not None and current.status in {"queued", "running", "submission_unknown"}:
            try:
                dubbing_store.update_task(
                    internal_task_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}"[:1000],
                )
            except Exception:
                pass
        raise
    finally:
        await sqlite_store.close()


register_project_task_runner("narrative_group_dubbing", run_narrative_group_dubbing)


__all__ = ["run_narrative_group_dubbing"]
