"""Celery runners for prop reference image generation."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _prop_version_path(output_dir: Path, prop_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return (
        output_dir
        / "assets"
        / "props"
        / prop_name
        / "versions"
        / f"prop-reference-{stamp}.png"
    )


def _prop_reference_prompt(*, style: str, visual_prompt: str) -> str:
    return (
        "Production prop turnaround reference sheet with exactly three panels: "
        "front, strict side, and back views. Keep the same object geometry, material, "
        "damage state, color, scale, and design in every panel. Clean neutral background, "
        "complete object visible, no scene composition. Do not generate authoritative or "
        "readable text, letters, numbers, UI, document fields, or screen content; reserve "
        "blank content regions for deterministic post-compositing. "
        f"Style: {style}. Prop: {visual_prompt}"
    )


def _register_prop_candidate(
    *,
    ctx: ProjectContext,
    output_dir: Path,
    prop,
    output_path: Path,
    canonical_path: Path,
    prompt: str,
    model: str,
    source_attempt_id: str | None,
) -> dict[str, str]:
    from novelvideo.production_workflow import (
        ProductionWorkflowStore,
        production_workflow_project_lock,
    )

    root = output_dir.resolve()
    slot_id = f"prop:{prop.name}:reference"
    with production_workflow_project_lock(ctx.state_dir):
        qc_passed = output_path.is_file() and output_path.stat().st_size > 0
        staged_canonical: Path | None = None
        if qc_passed:
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            staged_canonical = canonical_path.with_name(
                f".{canonical_path.name}.{output_path.stem}.stage"
            )
            try:
                shutil.copy2(output_path, staged_canonical)
                with staged_canonical.open("rb") as staged_file:
                    os.fsync(staged_file.fileno())
            except Exception:
                staged_canonical.unlink(missing_ok=True)
                raise
        workflow = ProductionWorkflowStore(Path(ctx.state_dir) / "production_workflow.json")
        workflow_snapshot = workflow.capture_file_snapshot()
        canonical_snapshot = canonical_path.read_bytes() if canonical_path.exists() else None
        try:
            slot, version, _event = workflow.register_candidate_version(
                slot_id=slot_id,
                asset_kind="prop_reference",
                version_id=output_path.stem,
                asset_path=output_path.resolve().relative_to(root).as_posix(),
                source_attempt_id=source_attempt_id,
                qc_passed=qc_passed,
                generation_metadata={
                    "prop_name": str(prop.name),
                    "prop_type": str(getattr(prop, "prop_type", "") or "object"),
                    "provider": "grsai",
                    "model": model,
                    "aspect_ratio": "16:9",
                    "panel_layout": ["front", "side", "back"],
                    "prompt_snapshot": prompt,
                    "content_policy": "deterministic_post_composite",
                    "canonical_path": canonical_path.resolve().relative_to(root).as_posix(),
                },
                actor=str(getattr(ctx, "requester_username", "") or "system"),
                at=datetime.now(timezone.utc),
            )
            if staged_canonical is not None and slot.current_version_id == version.version_id:
                os.replace(staged_canonical, canonical_path)
                staged_canonical = None
        except Exception:
            workflow.restore_file_snapshot(workflow_snapshot)
            if canonical_snapshot is None:
                canonical_path.unlink(missing_ok=True)
            else:
                canonical_path.write_bytes(canonical_snapshot)
            raise
        finally:
            if staged_canonical is not None:
                staged_canonical.unlink(missing_ok=True)
    return {
        "slot_id": slot_id,
        "version_id": version.version_id,
        "adoption_status": version.adoption_status.value,
    }


def run_prop_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_prop_reference_asset(envelope, ctx),
            envelope,
            task_type="prop_reference_asset",
        )
    )


async def _run_prop_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.task_backend.runners.character_image import _generate_grsai_image

    payload = envelope.get("payload") or {}
    prop_name = str(payload["prop_name"])
    style = str(payload.get("style") or "")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    scope = envelope.get("scope")
    manager = get_task_manager()

    store = SQLiteStore(ctx.owner_project_label, output_dir=str(output_dir), state_dir=str(ctx.state_dir))
    await store.initialize()
    try:
        prop = await store.get_prop(prop_name)
        if prop is None:
            raise RuntimeError(f"找不到道具: {prop_name}")
        visual_prompt = prop.visual_prompt or prop.description or prop.name
        prop_dir = output_dir / "assets" / "props" / prop.name
        prop_dir.mkdir(parents=True, exist_ok=True)
        canonical_path = prop_dir / "reference_3view.png"
        output_path = _prop_version_path(output_dir, prop.name)
        manager.update_progress_for_project(
            ctx,
            "prop_reference_asset",
            0,
            scope=scope,
            progress=0.50,
            current_task="调用图像模型生成三视图...",
        )
        runtime = load_grsai_runtime_configuration(
            get_media_capability_store(), get_media_credential_resolver()
        )
        model = str(payload.get("model") or runtime.model).strip() or runtime.model
        prompt = _prop_reference_prompt(style=style, visual_prompt=visual_prompt)
        result_path = await _generate_grsai_image(
            model=model,
            prompt=prompt,
            output_path=output_path,
            aspect_ratio="16:9",
        )
        if not result_path:
            raise RuntimeError("图像 API 未返回有效图像")
        workflow_result = _register_prop_candidate(
            ctx=ctx,
            output_dir=output_dir,
            prop=prop,
            output_path=Path(result_path),
            canonical_path=canonical_path,
            prompt=prompt,
            model=model,
            source_attempt_id=str(
                envelope.get("task_id") or envelope.get("job_id") or ""
            )
            or None,
        )
        return {
            "prop_name": prop.name,
            "path": str(result_path),
            "style": style,
            "prompt_snapshot": prompt,
            **workflow_result,
        }
    finally:
        await store.close()


def run_batch_prop_ref(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_batch_prop_ref(envelope, ctx),
            envelope,
            task_type="batch_prop_ref",
        )
    )


async def _run_batch_prop_ref(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.task_backend.runners.character_image import _generate_grsai_image

    payload = envelope.get("payload") or {}
    style = str(payload.get("style") or "")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    manager = get_task_manager()

    store = SQLiteStore(ctx.owner_project_label, output_dir=str(output_dir), state_dir=str(ctx.state_dir))
    await store.initialize()
    await store.load_graph_state()
    try:
        props = await store.list_props()
        props_to_gen = [
            prop
            for prop in props
            if not (output_dir / "assets" / "props" / prop.name / "reference_3view.png").exists()
        ]
        if not props_to_gen:
            return {"generated": 0}

        generated = 0
        for index, prop in enumerate(props_to_gen, start=1):
            current = f"生成三视图: {prop.name}..."
            manager.update_progress_for_project(
                ctx,
                "batch_prop_ref",
                0,
                progress=index / len(props_to_gen),
                current_task=current,
                logs=[current],
            )
            prop_dir = output_dir / "assets" / "props" / prop.name
            prop_dir.mkdir(parents=True, exist_ok=True)
            runtime = load_grsai_runtime_configuration(
                get_media_capability_store(), get_media_credential_resolver()
            )
            model = str(payload.get("model") or runtime.model).strip() or runtime.model
            visual_prompt = prop.visual_prompt or prop.description or prop.name
            prompt = _prop_reference_prompt(
                style=style,
                visual_prompt=visual_prompt,
            )
            output_path = _prop_version_path(output_dir, prop.name)
            result = await _generate_grsai_image(
                model=model,
                prompt=prompt,
                output_path=output_path,
                aspect_ratio="16:9",
            )
            if result:
                _register_prop_candidate(
                    ctx=ctx,
                    output_dir=output_dir,
                    prop=prop,
                    output_path=Path(result),
                    canonical_path=prop_dir / "reference_3view.png",
                    prompt=prompt,
                    model=model,
                    source_attempt_id=str(
                        envelope.get("task_id") or envelope.get("job_id") or ""
                    )
                    or None,
                )
                generated += 1
        return {"generated": generated}
    finally:
        await store.close()


register_project_task_runner("prop_reference_asset", run_prop_reference_asset)
register_project_task_runner("batch_prop_ref", run_batch_prop_ref)
