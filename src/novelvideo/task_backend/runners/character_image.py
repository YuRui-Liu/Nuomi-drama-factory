"""Celery runner for character portrait and identity image assets."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _safe_asset_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", str(name or "").strip()) or "untitled"


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
    explicit = str(getattr(character, "face_prompt", "") or "").strip()
    if explicit:
        return explicit

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

        update(0.25, "准备生成参数...")
        if mode == "portrait":
            output_path = await _generate_character_portrait(
                character=character,
                ethnicity=ethnicity,
                output_dir=output_dir,
                style=style,
                model=model,
                task_type=task_type,
                scope=str(scope or ""),
                update=update,
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
            output_path = await _generate_identity_image(
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
        else:
            raise RuntimeError(f"未知角色图像生成模式: {mode}")
        return {
            "mode": mode,
            "character_name": character.name,
            "identity_id": identity_id,
            "identity_name": identity_name,
            "path": str(output_path),
        }
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
) -> Path:
    face_prompt = _character_portrait_face_prompt(character)
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
    face_prompt = str(identity.face_prompt or "").strip()
    if not face_prompt:
        raise RuntimeError("该身份无 face_prompt，无需独立 Portrait")
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
) -> Path:
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
    output_path = identity_dir / f"{safe_name}.png"
    temp_output_path = identity_dir / f".tmp_{safe_name}_{_asset_suffix()}.png"

    identity_age = str(identity.age_group or "").strip()
    char_age = str(character.age_group or "youth").strip() or "youth"
    if identity_age and identity_age != char_age:
        if has_identity_portrait:
            identity_prompt = "" if has_costume_image else appearance_details
            reference_image_path = identity_portrait
        else:
            face_override = str(identity.face_prompt or "").strip()
            identity_prompt = (
                face_override
                if has_costume_image
                else "\n".join(part for part in [face_override, appearance_details] if part)
            )
            reference_image_path = ""
    else:
        portrait_path = char_assets_dir / "portrait.png"
        if not portrait_path.exists():
            raise RuntimeError(f"请先为角色「{character.name}」生成 Portrait（面部特写）")
        identity_prompt = "" if has_costume_image else appearance_details
        reference_image_path = str(portrait_path)

    update(0.45, "调用图像模型生成身份图...")
    try:
        references = [reference_image_path] + ([costume_image] if has_costume_image else [])
        await _generate_grsai_image(
            model=model,
            prompt=("Full-body single character reference image, preserve the same face and identity from references, "
                    f"coherent anatomy, no text, no watermark. Identity: {identity.identity_name}. "
                    f"Ethnicity: {ethnicity}. Visual style: {style}. Clothing and appearance: "
                    f"{_strip_known_style_prefix(identity_prompt or appearance_details)}"),
            output_path=temp_output_path,
            reference_paths=[path for path in references if path],
        )
        return _replace_canonical_asset(temp_output_path, output_path)
    finally:
        temp_output_path.unlink(missing_ok=True)
        temp_body_path = temp_output_path.with_name(f"{temp_output_path.stem}_body_temp.png")
        temp_body_path.unlink(missing_ok=True)


register_project_task_runner("character_portrait", run_character_image)
register_project_task_runner("identity_image", run_character_image)
