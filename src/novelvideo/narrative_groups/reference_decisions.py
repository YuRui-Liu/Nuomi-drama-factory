"""Validate client choices and freeze safe narrative reference images."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from novelvideo.narrative_groups.reference_matching import (
    MatchedReferenceRequirement,
    ReferenceBinding,
    ReferenceMatchPreview,
)
from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    ReferenceUpload,
    validate_reference_image,
)

DecisionAction = Literal[
    "keep", "accept_fallback", "use_base", "confirm_draft",
    "choose_identity", "choose_scene", "choose_variant", "choose_prop",
    "upload", "ignore",
]
ReferenceResolution = Literal["matched", "fallback", "temporary", "project_asset"]


class InvalidReferenceDecisions(ValueError):
    """Raised when decisions contain unknown IDs or unsafe server data."""


class UnresolvedReferenceRequirements(InvalidReferenceDecisions):
    """Raised when a draft or missing requirement has no explicit resolution."""


class TooManyReferenceImages(InvalidReferenceDecisions):
    """Raised when a snapshot exceeds the provider image limit."""


@dataclass(frozen=True, slots=True)
class ReferenceDecision:
    requirement_id: str
    action: DecisionAction
    asset_id: str = ""
    upload_id: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedProjectAsset:
    asset_id: str
    image_path: str
    asset_kind: str
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotReferenceImage:
    requirement_id: str
    source: Literal["matched", "project_asset", "upload"]
    source_id: str
    asset_kind: str
    image_path: str
    resolution: ReferenceResolution


@dataclass(frozen=True, slots=True)
class ReferenceDecisionSnapshot:
    id: str
    schema_version: str
    images: tuple[SnapshotReferenceImage, ...]
    ignored_requirement_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    style_reference: str = ""

    @property
    def schema(self) -> str:
        return self.schema_version

    @property
    def ignored(self) -> tuple[str, ...]:
        return self.ignored_requirement_ids


def _validated_path(
    path: str, roots: Sequence[Path], *, expected_mime: str | None = None
) -> str:
    try:
        return validate_reference_image(
            path, allowed_roots=roots, expected_mime=expected_mime
        ).image_path
    except InvalidReferenceUpload as exc:
        message = "reference is not a valid image"
        if "outside" in str(exc) or "symlink" in str(exc):
            message = "reference is outside project assets"
        raise InvalidReferenceDecisions(message) from None


def _parse_decision(value: ReferenceDecision | Mapping[str, Any]) -> ReferenceDecision:
    if isinstance(value, ReferenceDecision):
        return value
    if not isinstance(value, Mapping):
        raise InvalidReferenceDecisions("each decision must be an object")
    if any("path" in str(key).lower() for key in value):
        raise InvalidReferenceDecisions("client decisions must not contain a path")
    unknown = set(value) - {"requirement_id", "action", "asset_id", "upload_id"}
    if unknown:
        raise InvalidReferenceDecisions("decision contains unknown fields")
    try:
        return ReferenceDecision(
            str(value["requirement_id"]), str(value["action"]),  # type: ignore[arg-type]
            str(value.get("asset_id", "")), str(value.get("upload_id", "")),
        )
    except KeyError:
        raise InvalidReferenceDecisions("decision is missing required fields") from None


def _binding_matches(requirement: MatchedReferenceRequirement, binding: ReferenceBinding) -> bool:
    if requirement.kind == "character_identity":
        return binding.asset_kind in {"character_identity", "character"}
    if requirement.kind == "scene_base":
        return binding.asset_kind == "scene_base"
    if requirement.kind == "scene_variant":
        return binding.asset_kind in {"scene_variant", "scene_base"}
    return binding.asset_kind == "prop"


def _binding_image(
    requirement: MatchedReferenceRequirement,
    binding: ReferenceBinding,
    assets_root: Path,
) -> SnapshotReferenceImage:
    if not _binding_matches(requirement, binding):
        raise InvalidReferenceDecisions("matched binding does not match requirement")
    return SnapshotReferenceImage(
        requirement_id=requirement.id,
        source="matched",
        source_id=binding.asset_id,
        asset_kind=binding.asset_kind,
        image_path=_validated_path(binding.image_path, (assets_root,)),
        resolution="fallback" if binding.decision == "fallback" else "matched",
    )


def _asset_matches(requirement: MatchedReferenceRequirement, asset: ResolvedProjectAsset) -> bool:
    if requirement.kind == "scene_variant":
        return (
            asset.asset_kind == "scene_variant"
            and asset.entity_id == requirement.entity_id
            and asset.base_entity_id == requirement.base_entity_id
            and asset.variant_id == requirement.variant_id
        )
    return asset.asset_kind == requirement.kind and asset.entity_id == requirement.entity_id


def _asset_image(
    requirement: MatchedReferenceRequirement,
    asset_id: str,
    assets: Mapping[str, ResolvedProjectAsset],
    assets_root: Path,
) -> SnapshotReferenceImage:
    asset = assets.get(asset_id)
    if asset is None or asset.asset_id != asset_id:
        raise InvalidReferenceDecisions("unknown project asset")
    if not _asset_matches(requirement, asset):
        raise InvalidReferenceDecisions("project asset does not match requirement")
    return SnapshotReferenceImage(
        requirement_id=requirement.id,
        source="project_asset",
        source_id=asset.asset_id,
        asset_kind=asset.asset_kind,
        image_path=_validated_path(asset.image_path, (assets_root,)),
        resolution="project_asset",
    )


def build_reference_snapshot(
    preview: ReferenceMatchPreview,
    decisions: Sequence[ReferenceDecision | Mapping[str, Any]],
    *,
    project_dir: Path,
    project_assets: Mapping[str, ResolvedProjectAsset] | None = None,
    uploads: Mapping[str, ReferenceUpload] | None = None,
    style_reference: str | None = None,
    max_images: int = 9,
) -> ReferenceDecisionSnapshot:
    """Resolve advertised choices into a fully validated, project-scoped snapshot."""
    project_dir = Path(project_dir).resolve(strict=False)
    assets_root = project_dir / "assets"
    upload_root = project_dir / ".runtime" / "reference_uploads"
    requirements = {item.id: item for item in preview.requirements}
    parsed: dict[str, ReferenceDecision] = {}
    for raw in decisions:
        decision = _parse_decision(raw)
        requirement = requirements.get(decision.requirement_id)
        if requirement is None:
            raise InvalidReferenceDecisions("unknown requirement")
        if decision.requirement_id in parsed:
            raise InvalidReferenceDecisions("duplicate decision for requirement")
        if requirement.status == "draft_variant" and decision.action == "ignore":
            raise InvalidReferenceDecisions("draft variants cannot be ignored")
        if decision.action not in requirement.available_actions:
            raise InvalidReferenceDecisions("decision action is not available for requirement")
        if decision.asset_id and decision.upload_id:
            raise InvalidReferenceDecisions("decision cannot select both asset and upload")
        parsed[decision.requirement_id] = decision

    images: list[SnapshotReferenceImage] = []
    ignored: list[str] = []
    warnings = list(preview.warnings)
    asset_lookup = project_assets or {}
    upload_lookup = uploads or {}
    unresolved: list[str] = []
    choose_actions = {"choose_identity", "choose_scene", "choose_variant", "choose_prop"}
    for requirement in preview.requirements:
        decision = parsed.get(requirement.id)
        if decision is None:
            if requirement.status in {"matched", "fallback", "temporary"} and requirement.bindings:
                images.extend(_binding_image(requirement, item, assets_root) for item in requirement.bindings)
            elif requirement.status == "ignored":
                ignored.append(requirement.id)
            else:
                unresolved.append(requirement.id)
            continue
        if decision.action == "ignore":
            if decision.asset_id or decision.upload_id:
                raise InvalidReferenceDecisions("ignore cannot include an asset or upload ID")
            ignored.append(requirement.id)
        elif decision.action in {"keep", "accept_fallback"}:
            expected_status = "matched" if decision.action == "keep" else "fallback"
            if requirement.status != expected_status or decision.asset_id or decision.upload_id:
                raise InvalidReferenceDecisions("decision does not match requirement state")
            images.extend(_binding_image(requirement, item, assets_root) for item in requirement.bindings)
        elif decision.action == "use_base":
            fallback = [item for item in requirement.bindings if item.asset_kind == "scene_base"]
            if requirement.kind != "scene_variant" or decision.asset_id or decision.upload_id or not fallback:
                raise UnresolvedReferenceRequirements(f"unresolved reference requirement: {requirement.id}")
            images.extend(_binding_image(requirement, item, assets_root) for item in fallback)
        elif decision.action == "confirm_draft":
            if requirement.status != "draft_variant" or not decision.asset_id or decision.upload_id:
                unresolved.append(requirement.id)
            else:
                images.append(_asset_image(requirement, decision.asset_id, asset_lookup, assets_root))
        elif decision.action in choose_actions:
            if not decision.asset_id or decision.upload_id:
                raise InvalidReferenceDecisions("asset choice requires only asset_id")
            images.append(_asset_image(requirement, decision.asset_id, asset_lookup, assets_root))
        elif decision.action == "upload":
            if not decision.upload_id or decision.asset_id:
                raise InvalidReferenceDecisions("upload requires only upload_id")
            upload = upload_lookup.get(decision.upload_id)
            if upload is None or upload.upload_id != decision.upload_id:
                raise InvalidReferenceDecisions("unknown upload")
            path = _validated_path(
                upload.image_path, (assets_root, upload_root), expected_mime=upload.mime_type
            )
            images.append(SnapshotReferenceImage(
                requirement_id=requirement.id,
                source="upload",
                source_id=upload.upload_id,
                asset_kind=requirement.kind,
                image_path=path,
                resolution="temporary" if upload.temporary else "project_asset",
            ))
            if upload.persistence_warning:
                warnings.append(upload.persistence_warning)
        else:
            raise InvalidReferenceDecisions("unsupported decision action")
    if unresolved:
        raise UnresolvedReferenceRequirements(
            "unresolved reference requirements: " + ", ".join(unresolved)
        )

    safe_style = _validated_path(style_reference, (assets_root,)) if style_reference else ""
    count = len(images) + bool(safe_style)
    if isinstance(max_images, bool) or not isinstance(max_images, int) or max_images < 1:
        raise InvalidReferenceDecisions("max_images must be a positive integer")
    if count > max_images:
        raise TooManyReferenceImages(f"reference snapshot contains {count} images; limit is {max_images}")
    return ReferenceDecisionSnapshot(
        f"refsnap_{uuid.uuid4().hex}", "narrative-reference-decision/v1",
        tuple(images), tuple(ignored), tuple(dict.fromkeys(warnings)), safe_style,
    )


__all__ = [
    "InvalidReferenceDecisions", "ReferenceDecision", "ReferenceDecisionSnapshot",
    "ReferenceResolution", "ResolvedProjectAsset", "SnapshotReferenceImage", "TooManyReferenceImages",
    "UnresolvedReferenceRequirements", "build_reference_snapshot",
]
