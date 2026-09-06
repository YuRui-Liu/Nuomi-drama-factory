"""Match narrative reference requirements to project-owned assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from novelvideo.models import NovelScene
from novelvideo.narrative_groups.reference_requirements import (
    ReferenceRequirement,
    ReferenceStatus,
)
from novelvideo.utils.path_resolver import (
    compute_identity_path,
    compute_portrait_path,
    compute_prop_reference_path,
    compute_scene_master_path,
)

BindingDecision = Literal["project_asset", "fallback"]
DRAFT_VARIANT_NOTE = "由叙事组资产需求自动创建，尚未确认"


@dataclass(frozen=True)
class ReferenceBinding:
    requirement_id: str
    decision: BindingDecision
    asset_id: str
    asset_kind: str
    image_path: str


@dataclass(frozen=True)
class MatchedReferenceRequirement:
    id: str
    kind: str
    entity_id: str
    base_entity_id: str
    variant_id: str
    shot_ids: tuple[str, ...]
    required: bool
    label: str
    status: ReferenceStatus
    candidate_asset_ids: tuple[str, ...]
    available_actions: tuple[str, ...]
    bindings: tuple[ReferenceBinding, ...]
    warning: str = ""


@dataclass(frozen=True)
class ReferenceMatchPreview:
    requirements: tuple[MatchedReferenceRequirement, ...]
    bindings: tuple[ReferenceBinding, ...]
    warnings: tuple[str, ...] = ()


def _safe_asset(project_dir: Path, candidate: str) -> str:
    if not candidate:
        return ""
    root = (project_dir / "assets").resolve(strict=False)
    path = Path(candidate).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        return ""
    return str(path) if path.is_file() else ""


def _binding(
    requirement: ReferenceRequirement,
    *,
    decision: BindingDecision,
    asset_id: str,
    asset_kind: str,
    image_path: str,
) -> ReferenceBinding:
    return ReferenceBinding(
        requirement_id=requirement.id,
        decision=decision,
        asset_id=asset_id,
        asset_kind=asset_kind,
        image_path=image_path,
    )


def _matched(
    requirement: ReferenceRequirement,
    *,
    status: ReferenceStatus,
    actions: tuple[str, ...],
    bindings: tuple[ReferenceBinding, ...] = (),
    candidates: tuple[str, ...] = (),
    warning: str = "",
) -> MatchedReferenceRequirement:
    label = (
        f"{requirement.base_entity_id} / {requirement.variant_id}"
        if requirement.kind == "scene_variant"
        else requirement.entity_id
    )
    return MatchedReferenceRequirement(
        id=requirement.id,
        kind=requirement.kind,
        entity_id=requirement.entity_id,
        base_entity_id=requirement.base_entity_id,
        variant_id=requirement.variant_id,
        shot_ids=requirement.shot_ids,
        required=requirement.required,
        label=label,
        status=status,
        candidate_asset_ids=candidates,
        available_actions=actions,
        bindings=bindings,
        warning=warning,
    )


async def ensure_draft_scene_variant(
    store: object, base_scene_id: str, variant_id: str
) -> NovelScene:
    """Insert a draft once, returning a concurrent winner without overwriting it."""
    name = f"{base_scene_id}_{variant_id}"
    existing = await store.get_scene_exact(name)
    if existing is not None:
        return existing
    draft = NovelScene(
        name=name,
        base_scene_id=base_scene_id,
        variant_id=variant_id,
        notes=DRAFT_VARIANT_NOTE,
    )
    inserted = await store.add_scene_if_absent(draft)
    if inserted:
        return draft
    winner = await store.get_scene_exact(name)
    if winner is None:
        raise RuntimeError(f"scene variant insert conflicted but no record exists: {name}")
    return winner


async def _match_identity(
    store: object, project_dir: Path, requirement: ReferenceRequirement
) -> MatchedReferenceRequirement:
    character_name = requirement.entity_id.split("_", 1)[0].strip()
    character = store.get_character(character_name)
    if character is None:
        characters = await store.list_characters()
        character = next(
            (
                candidate
                for candidate in characters
                if candidate.name == character_name
                or requirement.entity_id
                in {
                    str(identity.identity_id or "").strip()
                    for identity in (candidate.identities or [])
                }
                or character_name in (candidate.aliases or [])
            ),
            None,
        )
    if character is None:
        return _matched(
            requirement,
            status="missing_asset",
            actions=("choose_identity", "upload", "ignore"),
            warning=f"角色身份 {requirement.entity_id} 不存在。",
        )
    identity_path = _safe_asset(
        project_dir,
        compute_identity_path(project_dir, character.name, requirement.entity_id),
    )
    if identity_path:
        binding = _binding(
            requirement,
            decision="project_asset",
            asset_id=requirement.entity_id,
            asset_kind="character_identity",
            image_path=identity_path,
        )
        return _matched(
            requirement,
            status="matched",
            actions=("keep", "choose_identity", "upload"),
            bindings=(binding,),
        )
    portrait_path = _safe_asset(
        project_dir, compute_portrait_path(project_dir, character.name)
    )
    if portrait_path:
        binding = _binding(
            requirement,
            decision="fallback",
            asset_id=character.name,
            asset_kind="character",
            image_path=portrait_path,
        )
        return _matched(
            requirement,
            status="fallback",
            actions=("accept_fallback", "choose_identity", "upload", "ignore"),
            bindings=(binding,),
            warning=f"身份图 {requirement.entity_id} 缺失，已提供默认肖像。",
        )
    return _matched(
        requirement,
        status="missing_image",
        actions=("choose_identity", "upload", "ignore"),
        warning=f"角色身份 {requirement.entity_id} 没有有效图片。",
    )


async def _match_scene(
    store: object, project_dir: Path, requirement: ReferenceRequirement
) -> MatchedReferenceRequirement:
    scene_id = requirement.entity_id
    scene = await store.get_scene_exact(scene_id)
    if requirement.kind == "scene_base":
        if scene is None:
            return _matched(
                requirement,
                status="missing_asset",
                actions=("choose_scene", "upload", "ignore"),
                warning=f"场景 {scene_id} 不存在。",
            )
        path = _safe_asset(project_dir, compute_scene_master_path(project_dir, scene.name))
        if not path:
            return _matched(
                requirement,
                status="missing_image",
                actions=("choose_scene", "upload", "ignore"),
                warning=f"场景 {scene.name} 没有有效主图。",
            )
        binding = _binding(
            requirement,
            decision="project_asset",
            asset_id=scene.name,
            asset_kind="scene_base",
            image_path=path,
        )
        return _matched(
            requirement,
            status="matched",
            actions=("keep", "choose_scene", "upload"),
            bindings=(binding,),
        )

    base_id = requirement.base_entity_id
    base = await store.get_scene_exact(base_id)
    if base is None:
        return _matched(
            requirement,
            status="missing_asset",
            actions=("choose_variant", "upload", "ignore"),
            warning=f"基础场景 {base_id} 不存在。",
        )
    all_scenes = await store.list_scenes()
    candidates = tuple(
        item.name
        for item in all_scenes
        if str(getattr(item, "base_scene_id", "") or "").strip() == base_id
    )
    base_path = _safe_asset(
        project_dir, compute_scene_master_path(project_dir, base.name)
    )
    fallback = (
        _binding(
            requirement,
            decision="fallback",
            asset_id=base.name,
            asset_kind="scene_base",
            image_path=base_path,
        ),
    ) if base_path else ()
    if scene is None:
        winner = await ensure_draft_scene_variant(store, base_id, requirement.variant_id)
        candidates = tuple(dict.fromkeys((*candidates, winner.name)))
        return _matched(
            requirement,
            status="draft_variant",
            actions=("confirm_draft", "choose_variant", "use_base", "upload"),
            bindings=fallback,
            candidates=candidates,
            warning=f"场景变体 {scene_id} 已建立为待确认草稿。",
        )
    if str(getattr(scene, "notes", "") or "").strip() == DRAFT_VARIANT_NOTE:
        return _matched(
            requirement,
            status="draft_variant",
            actions=("confirm_draft", "choose_variant", "use_base", "upload"),
            bindings=fallback,
            candidates=candidates,
            warning=f"场景变体 {scene_id} 是待确认草稿。",
        )
    path = _safe_asset(project_dir, compute_scene_master_path(project_dir, scene.name))
    if not path:
        return _matched(
            requirement,
            status="missing_image",
            actions=("choose_variant", "use_base", "upload", "ignore"),
            bindings=fallback,
            candidates=candidates,
            warning=f"场景变体 {scene.name} 没有有效主图。",
        )
    binding = _binding(
        requirement,
        decision="project_asset",
        asset_id=scene.name,
        asset_kind="scene_variant",
        image_path=path,
    )
    return _matched(
        requirement,
        status="matched",
        actions=("keep", "choose_variant", "use_base", "upload"),
        bindings=(binding,),
        candidates=candidates,
    )


async def _match_prop(
    store: object, project_dir: Path, requirement: ReferenceRequirement
) -> MatchedReferenceRequirement:
    prop = await store.get_prop(requirement.entity_id)
    if prop is None:
        return _matched(
            requirement,
            status="missing_asset",
            actions=("choose_prop", "create_prop", "upload", "ignore"),
            warning=f"道具 {requirement.entity_id} 不存在。",
        )
    path = _safe_asset(
        project_dir, compute_prop_reference_path(project_dir, prop.name)
    )
    if not path:
        return _matched(
            requirement,
            status="missing_image",
            actions=("choose_prop", "upload", "ignore"),
            warning=f"道具 {prop.name} 没有有效参考图。",
        )
    binding = _binding(
        requirement,
        decision="project_asset",
        asset_id=prop.name,
        asset_kind="prop",
        image_path=path,
    )
    return _matched(
        requirement,
        status="matched",
        actions=("keep", "choose_prop", "upload"),
        bindings=(binding,),
    )


async def match_reference_requirements(
    store: object, requirements: Sequence[ReferenceRequirement]
) -> ReferenceMatchPreview:
    """Match every requirement without dropping unresolved entries."""
    project_dir = Path(store.project_dir)
    matched: list[MatchedReferenceRequirement] = []
    for requirement in requirements:
        if requirement.kind == "character_identity":
            item = await _match_identity(store, project_dir, requirement)
        elif requirement.kind in {"scene_base", "scene_variant"}:
            item = await _match_scene(store, project_dir, requirement)
        else:
            item = await _match_prop(store, project_dir, requirement)
        matched.append(item)
    bindings = tuple(binding for item in matched for binding in item.bindings)
    warnings = tuple(item.warning for item in matched if item.warning)
    return ReferenceMatchPreview(tuple(matched), bindings, warnings)


__all__ = [
    "MatchedReferenceRequirement",
    "ReferenceBinding",
    "ReferenceMatchPreview",
    "ensure_draft_scene_variant",
    "match_reference_requirements",
]
