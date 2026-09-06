"""Celery runner for character portrait and identity image assets."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.character_visual.identity_sheet import (
    IDENTITY_SHEET_LAYOUT_VERSION,
    IDENTITY_SHEET_PANEL_LAYOUT,
    IdentitySheetQualityReport,
    build_identity_sheet_v2_prompt,
    compose_identity_sheet_v2,
)
from novelvideo.character_visual.identity_sheet_qc import assess_identity_sheet_quality
from novelvideo.project_context import ProjectContext
from novelvideo.production_workflow import production_workflow_project_lock
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


@dataclass(frozen=True, slots=True)
class CharacterStateGeneration:
    output_path: Path
    canonical_path: Path
    reference_paths: tuple[Path, ...]
    prompt: str
    state_id: str
    raw_candidate_path: Path
    face_source: Path
    quality_report: IdentitySheetQualityReport


def _safe_asset_name(name: str) -> str:
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", str(name or "").strip())
    return "untitled" if safe_name in {"", ".", ".."} else safe_name


def _strip_known_style_prefix(prompt: str) -> str:
    text = str(prompt or "").strip()
    prefixes = [
        "写实古装剧风格，",
        "写实古装剧风格,",
        "anime style,",
        "anime风格，",
        "动漫风格，",
        "蜘蛛宇宙风格，",
        "蜘蛛宇宙风格,",
        "realistic style,",
        "chinese period drama style,",
    ]
    for prefix in prefixes:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix) :].strip()
    return text


def _asset_suffix() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def _archive_existing_asset(path: Path) -> Path | None:
    if not path.exists():
        return None
    archived = path.with_name(f"{path.stem}_{_asset_suffix()}{path.suffix}")
    path.replace(archived)
    return archived


def _replace_canonical_asset(source_path: Path, target_path: Path) -> Path:
    if not source_path.exists() or source_path.stat().st_size <= 0:
        raise RuntimeError("图像模型未返回有效文件")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    archived = _archive_existing_asset(target_path)
    try:
        shutil.move(str(source_path), str(target_path))
    except Exception:
        if archived is not None and archived.exists() and not target_path.exists():
            archived.replace(target_path)
        raise
    return target_path


def _find_identity(character, identity_id: str, identity_name: str):
    for identity in character.identities or []:
        if identity_id and identity.identity_id == identity_id:
            return identity
        if identity_name and identity.identity_name == identity_name:
            return identity
    return None


def _character_portrait_face_prompt(character: Any) -> str:
    # Legacy face_prompt has no provenance and must not silently become a
    # generation constraint. A confirmed VisualBible is handled separately.
    context_parts = [
        f"name: {str(getattr(character, 'name', '') or '').strip()}",
        f"gender: {str(getattr(character, 'gender', '') or '').strip()}",
        f"age group: {str(getattr(character, 'age_group', '') or '').strip()}",
        f"body type: {str(getattr(character, 'body_type', '') or '').strip()}",
        f"role: {str(getattr(character, 'role', '') or '').strip()}",
        f"source description: {str(getattr(character, 'description', '') or '').strip()}",
    ]
    context = "; ".join(part for part in context_parts if not part.endswith(": "))
    return (
        "Face details were not specified in the imported source. Design one stable "
        f"reusable facial identity consistent with this character context: {context}. "
        "Use natural facial proportions and distinctive, repeatable features; do not "
        "add clothing, props, action, or an exaggerated expression."
    )


def _character_portrait_prompt(character: Any, *, style: str, visual_bible=None) -> str:
    if visual_bible is not None:
        from novelvideo.character_visual import compile_visual_prompt_snapshot

        return compile_visual_prompt_snapshot(
            bible=visual_bible,
            project_style=style,
            reference_paths=[],
        ).prompt
    return _character_portrait_face_prompt(character)


def _visual_bible_required_error(character_name: str) -> RuntimeError:
    return RuntimeError(
        json.dumps(
            {
                "error_code": "CHARACTER_VISUAL_BIBLE_REQUIRED",
                "message": (
                    f"角色“{character_name}”尚未确认视觉身份设定。"
                    "请先在资产中心选择并确认一套视觉提案，再生成角色头像。"
                ),
                "transport_called": False,
            },
            ensure_ascii=False,
        )
    )


async def _generate_grsai_image(
    *, model: str, prompt: str, output_path: str | Path,
    reference_paths: list[str] | None = None,
    aspect_ratio: str = "2:3",
    image_size: str = "1K",
) -> Path:
    from novelvideo.generators.scene_reference_images import _call_grsai_image_api

    references = []
    for raw_path in reference_paths or []:
        path = Path(raw_path)
        if path.exists():
            references.append((path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png"))
    image_bytes, _text, error = await _call_grsai_image_api(
        model=model,
        prompt=prompt,
        reference_images=references or None,
        image_config={"aspect_ratio": aspect_ratio, "image_size": image_size},
    )
    if error or not image_bytes:
        raise RuntimeError(error or "GRSAI image response contained no image")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image_bytes)
    return destination


def run_character_image(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_character_image(envelope, ctx),
            envelope,
            task_type=str(envelope.get("task_type") or "character_portrait"),
        )
    )


async def _run_character_image(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    from novelvideo.project_config import load_project_config_file
    from novelvideo.sqlite_store import SQLiteStore

    payload = envelope.get("payload") or {}
    mode = str(payload["mode"])
    character_name = str(payload["character_name"])
    identity_id = str(payload.get("identity_id") or "")
    identity_name = str(payload.get("identity_name") or "")
    style = str(payload.get("style") or "")
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    task_type = str(envelope.get("task_type") or payload.get("task_type") or "character_portrait")
    scope = envelope.get("scope") or payload.get("scope")
    manager = get_task_manager()

    def update(progress: float, current_task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            task_type,
            0,
            scope=scope,
            progress=progress,
            current_task=current_task,
            logs=[current_task],
        )

    update(0.10, "加载角色数据...")
    store = SQLiteStore(ctx.owner_project_label, output_dir=str(output_dir), state_dir=str(ctx.state_dir))
    await store.initialize()
    await store.load_graph_state()
    try:
        character = store.get_character(character_name)
        if character is None:
            raise RuntimeError(f"找不到角色: {character_name}")
        project_config = load_project_config_file(ctx.owner_username, ctx.project_name)
        ethnicity = project_config.get("ethnicity", "Chinese")
        from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
        from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration
        model = load_grsai_runtime_configuration(
            get_media_capability_store(), get_media_credential_resolver()
        ).model
        from novelvideo.character_visual import CharacterVisualWorkspaceStore

        visual_bible = CharacterVisualWorkspaceStore(output_dir).get_confirmed_bible(
            character.name
        )

        update(0.25, "准备生成参数...")
        if mode == "portrait":
            if visual_bible is None:
                raise _visual_bible_required_error(character.name)
            portrait_prompt = _character_portrait_prompt(
                character,
                style=style,
                visual_bible=visual_bible,
            )
            output_path = await _generate_character_portrait(
                character=character,
                ethnicity=ethnicity,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
                compiled_prompt=portrait_prompt,
            )
        elif mode == "identity_portrait":
            output_path = await _generate_identity_portrait(
                store=store,
                character=character,
                ethnicity=ethnicity,
                identity_id=identity_id,
                identity_name=identity_name,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
            )
        elif mode == "identity_image":
            state_generation = await _generate_identity_image(
                character=character,
                ethnicity=ethnicity,
                identity_id=identity_id,
                identity_name=identity_name,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
            )
            output_path = state_generation.output_path
            workflow_result = _register_character_state_candidate(
                ctx=ctx,
                output_dir=output_dir,
                character_name=character.name,
                identity_id=identity_id,
                generation=state_generation,
                source_attempt_id=str(
                    envelope.get("task_id") or envelope.get("job_id") or ""
                )
                or None,
                recipe_revision=str(
                    project_config.get("production_recipe_version") or "1"
                ),
            )
        else:
            raise RuntimeError(f"未知角色图像生成模式: {mode}")
        result = {
            "mode": mode,
            "character_name": character.name,
            "identity_id": identity_id,
            "identity_name": identity_name,
            "path": str(output_path),
        }
        if mode == "identity_image":
            result.update(workflow_result)
            result["prompt_snapshot"] = state_generation.prompt
        if mode == "portrait":
            result["prompt_snapshot"] = portrait_prompt
            result["visual_bible_revision"] = (
                visual_bible.revision_id if visual_bible is not None else None
            )
        return result
    finally:
        await store.close()


async def _generate_character_portrait(
    *,
    character,
    ethnicity: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
    compiled_prompt: str | None = None,
) -> Path:
    if not str(compiled_prompt or "").strip():
        raise _visual_bible_required_error(character.name)
    face_prompt = str(compiled_prompt).strip()
    char_assets_dir = output_dir / "assets" / "characters" / character.name
    portrait_path = char_assets_dir / "portrait.png"
    temp_dir = char_assets_dir / f".tmp_portrait_{_asset_suffix()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        update(0.45, "调用图像模型生成角色 Portrait...")
        generated = await _generate_grsai_image(
            model=model,
            prompt=("Single close-up character portrait, centered head and shoulders, neutral clean background, "
                    f"no text, no watermark. Ethnicity: {ethnicity}. Visual style: {style}. "
                    f"Face: {_strip_known_style_prefix(face_prompt)}"),
            output_path=temp_dir / "reference_01.png",
        )
        return _replace_canonical_asset(generated, portrait_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _generate_identity_portrait(
    *,
    store,
    character,
    ethnicity: str,
    identity_id: str,
    identity_name: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
) -> Path:
    identity = _find_identity(character, identity_id, identity_name)
    if identity is None:
        raise RuntimeError(f"找不到身份: {identity_id or identity_name}")
    from novelvideo.character_visual import (
        CharacterVisualWorkspaceStore,
        compile_visual_prompt_snapshot,
    )

    visual_bible = CharacterVisualWorkspaceStore(output_dir).get_confirmed_bible(
        character.name
    )
    if visual_bible is None:
        raise RuntimeError("请先在角色视觉身份设定中确认 VisualBible")
    base_prompt = compile_visual_prompt_snapshot(
        bible=visual_bible,
        project_style=style,
        reference_paths=[],
    ).prompt
    variant_parts = [
        base_prompt,
        f"Identity age group: {identity.age_group}" if identity.age_group else "",
        f"Identity body type: {identity.body_type}" if identity.body_type else "",
    ]
    face_prompt = ". ".join(part for part in variant_parts if part)
    safe_name = _safe_asset_name(identity.identity_name)
    id_dir = output_dir / "assets" / "characters" / character.name / "identities"
    portrait_path = id_dir / f"{character.name}_{safe_name}_portrait.png"
    temp_dir = id_dir / f".tmp_identity_portrait_{safe_name}_{_asset_suffix()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        update(0.45, "调用图像模型生成身份 Portrait...")
        generated = await _generate_grsai_image(
            model=model,
            prompt=("Single close-up character portrait, centered head and shoulders, neutral clean background, "
                    f"no text, no watermark. Identity: {identity.identity_name}. Ethnicity: {ethnicity}. "
                    f"Visual style: {style}. Face: {_strip_known_style_prefix(face_prompt)}"),
            output_path=temp_dir / "reference_01.png",
        )
        _replace_canonical_asset(generated, portrait_path)
        await store.update_character_identity(
            character.name,
            identity.identity_id,
            portrait_image=str(portrait_path),
        )
        return portrait_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _register_character_state_candidate(
    *,
    ctx: ProjectContext,
    output_dir: Path,
    character_name: str,
    identity_id: str,
    generation: CharacterStateGeneration,
    source_attempt_id: str | None,
    recipe_revision: str,
) -> dict[str, Any]:
    from novelvideo.production_workflow import ProductionWorkflowStore

    root = output_dir.resolve()
    candidate_path = generation.output_path.resolve().relative_to(root).as_posix()
    canonical_path = generation.canonical_path.resolve().relative_to(root).as_posix()
    reference_sources = [
        path.resolve().relative_to(root).as_posix()
        for path in generation.reference_paths
        if path.resolve().is_relative_to(root)
    ]
    face_source = generation.face_source.resolve().relative_to(root).as_posix()
    raw_candidate_path = (
        generation.raw_candidate_path.resolve().relative_to(root).as_posix()
    )
    quality_report = generation.quality_report.model_dump(mode="json")
    slot_id = f"character:{character_name}:state:{generation.state_id}"
    version_id = generation.output_path.stem
    staged_canonical: Path | None = None
    state_dir = Path(ctx.state_dir)
    with production_workflow_project_lock(state_dir):
        if generation.quality_report.passed:
            generation.canonical_path.parent.mkdir(parents=True, exist_ok=True)
            staged_canonical = generation.canonical_path.with_name(
                f".{generation.canonical_path.name}.{version_id}.stage"
            )
            try:
                shutil.copy2(generation.output_path, staged_canonical)
                with staged_canonical.open("rb") as staged_file:
                    os.fsync(staged_file.fileno())
            except Exception:
                staged_canonical.unlink(missing_ok=True)
                raise

        workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
        workflow_snapshot = workflow.capture_file_snapshot()
        canonical_snapshot = (
            generation.canonical_path.read_bytes()
            if generation.canonical_path.exists()
            else None
        )
        try:
            slot, version, _event = workflow.register_candidate_version(
                slot_id=slot_id,
                asset_kind="character_state",
                version_id=version_id,
                asset_path=candidate_path,
                source_attempt_id=source_attempt_id,
                qc_passed=generation.quality_report.passed,
                soft_issues=(
                    []
                    if generation.quality_report.passed
                    else generation.quality_report.issues
                ),
                generation_metadata={
                    "character_name": character_name,
                    "identity_id": identity_id,
                    "state_id": generation.state_id,
                    "layout_version": IDENTITY_SHEET_LAYOUT_VERSION,
                    "panel_layout": list(IDENTITY_SHEET_PANEL_LAYOUT),
                    "face_source": face_source,
                    "face_source_panel": "portrait_3q",
                    "quality_report": quality_report,
                    "raw_candidate_path": raw_candidate_path,
                    "recipe_revision": recipe_revision,
                    "reference_sources": reference_sources,
                    "canonical_path": canonical_path,
                },
                actor=str(getattr(ctx, "requester_username", "") or "system"),
                at=datetime.now(timezone.utc),
            )
            if staged_canonical is not None and slot.current_version_id == version.version_id:
                os.replace(staged_canonical, generation.canonical_path)
                staged_canonical = None
        except Exception:
            workflow.restore_file_snapshot(workflow_snapshot)
            if canonical_snapshot is None:
                generation.canonical_path.unlink(missing_ok=True)
            else:
                generation.canonical_path.write_bytes(canonical_snapshot)
            raise
        finally:
            if staged_canonical is not None:
                staged_canonical.unlink(missing_ok=True)
    return {
        "slot_id": slot_id,
        "version_id": version.version_id,
        "adoption_status": version.adoption_status.value,
        "layout_version": IDENTITY_SHEET_LAYOUT_VERSION,
        "qc_passed": generation.quality_report.passed,
    }


async def _generate_identity_image(
    *,
    character,
    ethnicity: str,
    identity_id: str,
    identity_name: str,
    output_dir: Path,
    style: str,
    model: str,
    task_type: str,
    scope: str,
    update,
) -> CharacterStateGeneration:
    from novelvideo.utils.path_resolver import (
        compute_identity_costume_path,
        compute_identity_portrait_path,
    )

    identity = _find_identity(character, identity_id, identity_name)
    if identity is None:
        raise RuntimeError(f"找不到身份: {identity_id or identity_name}")

    appearance_details = str(identity.appearance_details or "").strip()
    costume_image = compute_identity_costume_path(
        output_dir, character.name, identity.identity_name
    ) or str(identity.costume_image or "")
    identity_portrait = compute_identity_portrait_path(
        output_dir, character.name, identity.identity_name
    ) or str(identity.portrait_image or "")
    has_costume_image = bool(costume_image and Path(costume_image).exists())
    has_identity_portrait = bool(identity_portrait and Path(identity_portrait).exists())
    if not appearance_details and not has_costume_image:
        raise RuntimeError("请先设置身份服装描述或上传服装参考图")

    safe_name = _safe_asset_name(identity.identity_name)
    char_assets_dir = output_dir / "assets" / "characters" / character.name
    identity_dir = char_assets_dir / "identities"
    identity_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = identity_dir / f"{safe_name}.png"
    state_id = identity.identity_id
    version_id = f"character-state-{_asset_suffix()}"
    versions_dir = identity_dir / safe_name / "versions"
    output_path = versions_dir / f"{version_id}.png"
    raw_candidate_path = versions_dir / f"{version_id}.raw.png"

    identity_age = str(identity.age_group or "").strip()
    char_age = str(character.age_group or "youth").strip() or "youth"
    if identity_age and identity_age != char_age:
        if not has_identity_portrait:
            raise RuntimeError("年龄变体必须先生成或上传 Identity Portrait")
        identity_prompt = "" if has_costume_image else appearance_details
        reference_image_path = identity_portrait
    else:
        portrait_path = char_assets_dir / "portrait.png"
        if not portrait_path.exists():
            raise RuntimeError(f"请先为角色「{character.name}」生成 Portrait（面部特写）")
        identity_prompt = "" if has_costume_image else appearance_details
        reference_image_path = str(portrait_path)

    update(0.45, "调用图像模型生成身份图...")
    references = [reference_image_path] + ([costume_image] if has_costume_image else [])
    prompt = build_identity_sheet_v2_prompt(
            character_name=character.name,
            character_tag=str(identity.character_tag or character.name),
            appearance=_strip_known_style_prefix(
                identity_prompt or appearance_details
            ),
            project_style=style,
            style_instructions=style,
            avoid_instructions="no identity drift, no inconsistent clothing",
            ethnicity=ethnicity,
            has_costume_reference=has_costume_image,
            project_dir=output_dir,
        )
    await _generate_grsai_image(
            model=model,
            prompt=prompt,
            output_path=raw_candidate_path,
            reference_paths=[path for path in references if path],
            aspect_ratio="3:2",
        )
    compose_identity_sheet_v2(raw_candidate_path, reference_image_path, output_path)
    quality_report = await assess_identity_sheet_quality(
        image_data=output_path.read_bytes(),
        style=style,
        project_dir=output_dir,
    )
    return CharacterStateGeneration(
        output_path=output_path,
        canonical_path=canonical_path,
        reference_paths=tuple(Path(path) for path in references if path),
        prompt=prompt,
        state_id=state_id,
        raw_candidate_path=raw_candidate_path,
        face_source=Path(reference_image_path),
        quality_report=quality_report,
    )


register_project_task_runner("character_portrait", run_character_image)
register_project_task_runner("identity_image", run_character_image)
