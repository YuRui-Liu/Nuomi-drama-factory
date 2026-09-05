"""Celery runner for canonical scene reference images."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


_SCENE_REFERENCE_FILENAMES = {
    "master": "master.png",
    "spatial_layout": "spatial_layout.png",
    "reverse_master": "reverse_master.png",
}


def _scene_reference_version_path(
    output_dir: Path,
    *,
    scene_name: str,
    kind: str,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return (
        output_dir
        / "assets"
        / "scenes"
        / scene_name
        / "versions"
        / f"{kind}-{stamp}.png"
    )


def _register_scene_reference_candidate(
    *,
    ctx: ProjectContext,
    output_dir: Path,
    scene,
    kind: str,
    output_path: Path,
    canonical_path: Path,
    source_attempt_id: str | None,
    recipe_revision: str,
) -> tuple[dict[str, str], bool]:
    from novelvideo.production_workflow import ProductionWorkflowStore

    root = output_dir.resolve()
    base_scene_id = str(getattr(scene, "base_scene_id", "") or "").strip()
    state_id = str(scene.name) if base_scene_id else ""
    if state_id:
        slot_id = f"scene:{base_scene_id}:state:{state_id}:{kind}"
        asset_kind = "scene_state"
    else:
        slot_id = f"scene:{scene.name}:base:{kind}"
        asset_kind = "scene_base"
    version_id = output_path.stem
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / "production_workflow.json")
    slot, version, _event = workflow.register_candidate_version(
        slot_id=slot_id,
        asset_kind=asset_kind,
        version_id=version_id,
        asset_path=output_path.resolve().relative_to(root).as_posix(),
        source_attempt_id=source_attempt_id,
        qc_passed=output_path.is_file() and output_path.stat().st_size > 0,
        generation_metadata={
            "scene_id": str(scene.name),
            "base_scene_id": base_scene_id or str(scene.name),
            "state_id": state_id,
            "variant_id": str(getattr(scene, "variant_id", "") or ""),
            "time_of_day": str(getattr(scene, "time_of_day", "") or ""),
            "anchor_kind": kind,
            "recipe_revision": recipe_revision,
            "canonical_path": canonical_path.resolve().relative_to(root).as_posix(),
        },
        actor=str(getattr(ctx, "requester_username", "") or "system"),
        at=datetime.now(timezone.utc),
    )
    canonical_updated = slot.current_version_id == version.version_id
    if canonical_updated:
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, canonical_path)
    return (
        {
            "slot_id": slot_id,
            "version_id": version.version_id,
            "adoption_status": version.adoption_status.value,
        },
        canonical_updated,
    )


def run_scene_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_scene_reference_asset(envelope, ctx),
            envelope,
            task_type="scene_reference_asset",
        )
    )


async def _run_scene_reference_asset(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.config import (
        IMAGE_DEFAULT_STYLE,
        get_style_preset,
    )
    from novelvideo.generators.scene_reference_images import generate_scene_reference_image
    from novelvideo.sqlite_store import SQLiteStore

    payload = envelope.get("payload") or {}
    scene_name = str(payload["scene_name"])
    kind = str(payload["kind"])
    style = str(payload.get("style") or "")
    scope = envelope.get("scope")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    manager = get_task_manager()

    if kind not in {"master", "spatial_layout", "reverse_master"}:
        raise ValueError(f"Unsupported scene reference kind: {kind}")

    def update(progress: float, current_task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            "scene_reference_asset",
            0,
            scope=scope,
            progress=progress,
            current_task=current_task,
            logs=[current_task],
        )

    update(0.10, "加载场景数据...")
    store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(output_dir),
        state_dir=str(ctx.state_dir),
    )
    await store.initialize()
    try:
        scene = await store.get_scene(scene_name)
        if scene is None:
            raise RuntimeError(f"找不到场景: {scene_name}")
        base_scene = None
        base_scene_id = str(getattr(scene, "base_scene_id", "") or "").strip()
        if base_scene_id and base_scene_id != scene.name:
            base_scene = await store.get_scene(base_scene_id)

        style_id = (style or IMAGE_DEFAULT_STYLE).strip() or IMAGE_DEFAULT_STYLE
        preset = get_style_preset(
            style_id,
            username=ctx.owner_username,
            project=ctx.project_name,
            project_dir=str(output_dir),
        )
        style_prompt = str(preset.get("style_instructions", "") or "").strip()
        avoid_instructions = str(preset.get("avoid_instructions", "") or "").strip()
        style_label = preset.get("label") or style_id
        style_name = f"{style_label} ({style_id})"

        update(0.40, f"调用图像模型生成 {kind}...")
        # Scene reference images use the persisted media runtime.  The UI's
        # legacy image-source selection must not route this task back through
        # DramaClawAPI/NewAPI.
        provider = "grsai"
        from novelvideo.api.deps import (
            get_media_capability_store,
            get_media_credential_resolver,
        )
        from novelvideo.media_capabilities.runtime.configuration import (
            load_grsai_runtime_configuration,
        )

        grsai_runtime = load_grsai_runtime_configuration(
            get_media_capability_store(),
            get_media_credential_resolver(),
        )
        model = grsai_runtime.model
        candidate_path = _scene_reference_version_path(
            output_dir,
            scene_name=scene.name,
            kind=kind,
        )
        canonical_path = (
            output_dir
            / "assets"
            / "scenes"
            / scene.name
            / _SCENE_REFERENCE_FILENAMES[kind]
        )
        output_path = await generate_scene_reference_image(
            project_dir=output_dir,
            scene=scene,
            kind=kind,  # type: ignore[arg-type]
            provider=provider,
            model=model,
            style_name=style_name,
            style_prompt=style_prompt,
            avoid_instructions=avoid_instructions,
            base_scene=base_scene,
            output_path_override=candidate_path,
        )
        workflow_result, canonical_updated = _register_scene_reference_candidate(
            ctx=ctx,
            output_dir=output_dir,
            scene=scene,
            kind=kind,
            output_path=Path(output_path),
            canonical_path=canonical_path,
            source_attempt_id=str(
                envelope.get("task_id") or envelope.get("job_id") or ""
            )
            or None,
            recipe_revision=str(payload.get("recipe_revision") or "1"),
        )
        if (
            canonical_updated
            and canonical_path.is_file()
            and kind in {"master", "reverse_master"}
        ):
            await store.clear_scene_stale_reference_kind(scene_name, kind)
        if kind == "spatial_layout":
            rel_path = str(canonical_path.relative_to(output_dir))
            await store.update_scene(scene_name, spatial_layout_image=rel_path)
        result = {
            "scene_name": scene_name,
            "kind": kind,
            "path": str(output_path),
            "style": style_name,
        }
        result.update(workflow_result)
        return result
    finally:
        await store.close()


register_project_task_runner("scene_reference_asset", run_scene_reference_asset)
