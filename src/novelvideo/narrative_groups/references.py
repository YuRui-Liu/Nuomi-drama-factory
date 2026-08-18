"""Resolve the bounded reference set used to generate one narrative group."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Literal, Sequence

from novelvideo.config import IMAGE_DEFAULT_STYLE, get_style_preset
from novelvideo.models import (
    beat_scene_id,
    extract_char_identities_from_markers,
    real_detected_identities,
)
from novelvideo.project_config import load_project_config_file
from novelvideo.utils.path_resolver import (
    compute_identity_path,
    compute_portrait_path,
    compute_scene_master_path,
)

MAX_GROUP_IMAGE_REFERENCES = 9

ReferenceKind = Literal["character", "scene"]
ReferenceSourceKind = Literal["identity", "portrait_fallback", "scene_master"]


@dataclass(frozen=True)
class GroupStyleReference:
    id: str
    name: str
    prompt: str
    warning: str = ""


@dataclass(frozen=True)
class GroupImageReference:
    id: str
    kind: ReferenceKind
    source_kind: ReferenceSourceKind
    label: str
    path: str
    beat_numbers: tuple[int, ...]
    first_appearance: int
    character_name: str = ""
    identity_id: str = ""
    scene_id: str = ""
    warning: str = ""


@dataclass(frozen=True)
class GroupReferencePreview:
    style: GroupStyleReference
    image_references: tuple[GroupImageReference, ...]
    warnings: tuple[str, ...] = ()
    asset_root: str = ""


@dataclass(frozen=True)
class GroupReferenceSelection:
    selected: tuple[GroupImageReference, ...]
    omitted: tuple[GroupImageReference, ...]
    image_paths: tuple[str, ...]
    style_prompt: str
    warnings: tuple[str, ...] = ()


class UnknownGroupReferenceIds(ValueError):
    def __init__(self, unknown_ids: Iterable[str]):
        self.unknown_ids = tuple(sorted(set(unknown_ids)))
        super().__init__(f"unknown narrative-group reference ids: {', '.join(self.unknown_ids)}")


def _opaque_id(kind: str, logical_identity: str) -> str:
    canonical = f"{kind}\0{logical_identity.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_existing_asset_path(asset_root: Path, candidate: str) -> str:
    """Return a resolved existing file only when it stays below ``asset_root``."""
    if not candidate:
        return ""
    root = asset_root.resolve(strict=False)
    path = Path(candidate).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        return ""
    return str(path) if path.is_file() else ""


def _style_reference(project_dir: Path) -> GroupStyleReference:
    config = load_project_config_file(project_dir.parent.name, project_dir.name)
    configured = config.get("visual_style")
    requested = str(configured if configured is not None else IMAGE_DEFAULT_STYLE).strip()
    warning = ""
    if not requested:
        requested = IMAGE_DEFAULT_STYLE
        warning = f"项目视觉风格为空，已回退到项目默认风格 {requested!r}。"
    try:
        preset = get_style_preset(requested, project_dir=str(project_dir))
        resolved_name = requested
    except (KeyError, ValueError):
        resolved_name = IMAGE_DEFAULT_STYLE
        preset = get_style_preset(resolved_name, project_dir=str(project_dir))
        warning = f"视觉风格 {requested!r} 无效，已回退到项目默认风格 {resolved_name!r}。"
    positive = str(preset.get("style_instructions") or "").strip()
    negative = str(preset.get("avoid_instructions") or "").strip()
    prompt = "\n".join(part for part in (positive, negative) if part)
    return GroupStyleReference(
        id=_opaque_id("style", resolved_name),
        name=resolved_name,
        prompt=prompt,
        warning=warning,
    )


def _beat_number(beat: object, fallback: int) -> int:
    raw = beat.get("beat_number", fallback) if isinstance(beat, dict) else getattr(beat, "beat_number", fallback)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _beat_value(beat: object, key: str, default: object = None) -> object:
    return beat.get(key, default) if isinstance(beat, dict) else getattr(beat, key, default)


def _identity_ids(beat: object) -> list[str]:
    result = real_detected_identities(_beat_value(beat, "detected_identities", []) or [])
    visual = str(_beat_value(beat, "visual_description", "") or "")
    for identity_id in extract_char_identities_from_markers(visual, strict=False).values():
        if identity_id not in result:
            result.append(identity_id)
    return result


def _reference_sort_key(ref: GroupImageReference) -> tuple[object, ...]:
    source_rank = {"identity": 0, "portrait_fallback": 1, "scene_master": 2}
    logical = ref.identity_id or ref.scene_id
    return (
        source_rank[ref.source_kind],
        -len(ref.beat_numbers),
        ref.first_appearance,
        logical,
    )


def resolve_group_reference_preview(
    project_dir: str | Path,
    beats: Sequence[object],
    stage: str = "render",
) -> GroupReferencePreview:
    """Resolve references mentioned by the supplied group beats only."""
    del stage  # Reserved for stage-specific reference policy.
    project_dir = Path(project_dir)
    asset_root = project_dir / "assets"
    coverage: dict[tuple[str, str], set[int]] = {}
    character_names: dict[str, str] = {}

    for index, beat in enumerate(beats, start=1):
        number = _beat_number(beat, index)
        for identity_id in _identity_ids(beat):
            character_name = identity_id.split("_", 1)[0].strip()
            if not character_name:
                continue
            coverage.setdefault(("character", identity_id), set()).add(number)
            character_names[identity_id] = character_name
        scene_id = beat_scene_id(beat)
        if scene_id:
            coverage.setdefault(("scene", scene_id), set()).add(number)

    references: list[GroupImageReference] = []
    for (kind, logical_id), numbers_set in coverage.items():
        numbers = tuple(sorted(numbers_set))
        if kind == "character":
            character_name = character_names[logical_id]
            identity_path = _safe_existing_asset_path(
                asset_root,
                compute_identity_path(project_dir, character_name, logical_id),
            )
            if identity_path:
                source_kind: ReferenceSourceKind = "identity"
                path = identity_path
                warning = ""
            else:
                source_kind = "portrait_fallback"
                path = _safe_existing_asset_path(
                    asset_root,
                    compute_portrait_path(project_dir, character_name),
                )
                warning = (
                    f"角色 {character_name} 的身份图 {logical_id} 缺失，已回退默认肖像。"
                    if path
                    else f"角色 {character_name} 的身份图 {logical_id} 与默认肖像均缺失。"
                )
            references.append(
                GroupImageReference(
                    id=_opaque_id("character", f"{character_name}\0{logical_id}"),
                    kind="character",
                    source_kind=source_kind,
                    label=f"{character_name} · {logical_id}",
                    path=path,
                    beat_numbers=numbers,
                    first_appearance=numbers[0],
                    character_name=character_name,
                    identity_id=logical_id,
                    warning=warning,
                )
            )
        else:
            path = _safe_existing_asset_path(
                asset_root,
                compute_scene_master_path(project_dir, logical_id),
            )
            warning = "" if path else f"场景 {logical_id} 的主图缺失。"
            references.append(
                GroupImageReference(
                    id=_opaque_id("scene", logical_id),
                    kind="scene",
                    source_kind="scene_master",
                    label=logical_id,
                    path=path,
                    beat_numbers=numbers,
                    first_appearance=numbers[0],
                    scene_id=logical_id,
                    warning=warning,
                )
            )

    style = _style_reference(project_dir)
    references.sort(key=_reference_sort_key)
    warnings = tuple(
        warning
        for warning in (style.warning, *(reference.warning for reference in references))
        if warning
    )
    return GroupReferencePreview(
        style=style,
        image_references=tuple(references),
        warnings=warnings,
        asset_root=str(asset_root.resolve(strict=False)),
    )


def apply_group_reference_selection(
    preview: GroupReferencePreview,
    use_style: bool = True,
    selected_character_reference_ids: Sequence[str] | None = None,
    selected_scene_reference_ids: Sequence[str] | None = None,
) -> GroupReferenceSelection:
    """Validate, sort and cap a user's reference selection."""
    by_kind = {
        "character": {ref.id: ref for ref in preview.image_references if ref.kind == "character"},
        "scene": {ref.id: ref for ref in preview.image_references if ref.kind == "scene"},
    }
    character_ids = (
        set(by_kind["character"])
        if selected_character_reference_ids is None
        else set(selected_character_reference_ids)
    )
    scene_ids = (
        set(by_kind["scene"])
        if selected_scene_reference_ids is None
        else set(selected_scene_reference_ids)
    )
    unknown = (character_ids - set(by_kind["character"])) | (scene_ids - set(by_kind["scene"]))
    if unknown:
        raise UnknownGroupReferenceIds(unknown)

    requested = [
        ref
        for ref in preview.image_references
        if ref.id in (character_ids if ref.kind == "character" else scene_ids)
    ]
    warnings = list(preview.warnings)
    eligible: list[GroupImageReference] = []
    asset_root = Path(preview.asset_root) if preview.asset_root else None
    for ref in requested:
        safe_path = (
            _safe_existing_asset_path(asset_root, ref.path)
            if asset_root is not None
            else (ref.path if ref.path and Path(ref.path).is_file() else "")
        )
        if safe_path:
            eligible.append(replace(ref, path=safe_path))
        else:
            warning = f"参考图 {ref.label} 不存在，已从生成输入中移除。"
            if warning not in warnings:
                warnings.append(warning)
    eligible.sort(key=_reference_sort_key)
    selected = tuple(eligible[:MAX_GROUP_IMAGE_REFERENCES])
    omitted = tuple(eligible[MAX_GROUP_IMAGE_REFERENCES:])
    valid_paths = [ref.path for ref in selected]
    if omitted:
        warnings.append(
            f"参考图最多 {MAX_GROUP_IMAGE_REFERENCES} 张，已省略 {len(omitted)} 张。"
        )

    return GroupReferenceSelection(
        selected=selected,
        omitted=omitted,
        image_paths=tuple(valid_paths),
        style_prompt=preview.style.prompt if use_style else "",
        warnings=tuple(warnings),
    )
