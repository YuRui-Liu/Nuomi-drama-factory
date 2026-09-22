"""Compile continuity contracts into immutable H3 transport bundles."""

from __future__ import annotations

from typing import Literal

from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptOptimizationResult,
)
from novelvideo.media_capabilities.video.models import H3Mode

from .hashing import canonical_sha256
from .models import (
    CompiledShotBundle,
    ContractRef,
    FrameEvidence,
    H3ModeDecision,
    H3ReferenceBinding,
    ShotContinuityContract,
    ShotRiskReport,
)

H3_SHOT_COMPILER_VERSION = 1

_REF_SUFFIX = {
    "character_identity": "preserve identity, hair, and wardrobe",
    "scene_base": "preserve architecture and set dressing",
    "prop": "preserve shape, material, and visible state",
}


def _revalidate_reference_binding(
    binding: H3ReferenceBinding,
) -> H3ReferenceBinding:
    try:
        asset = binding.asset
        payload = {
            "reference_id": binding.reference_id,
            "source_kind": binding.source_kind,
            "subject_index": binding.subject_index,
            "picture_index": binding.picture_index,
            "label": binding.label,
            "asset": {
                "asset_id": asset.asset_id,
                "sha256": asset.sha256,
            },
        }
    except (AttributeError, TypeError) as exc:
        raise ValueError("invalid reference binding structure") from exc

    source_kind = payload["source_kind"]
    if not isinstance(source_kind, str) or source_kind not in _REF_SUFFIX:
        raise ValueError(
            "reference source kind must be character_identity, scene_base, or prop"
        )
    return H3ReferenceBinding.model_validate(payload)


def compile_reference_definitions(
    bindings: tuple[H3ReferenceBinding, ...],
) -> str:
    """Compile validated H3 reference bindings into ordered prompt lines."""
    if not bindings:
        raise ValueError("reference bindings must be non-empty")

    validated = tuple(_revalidate_reference_binding(binding) for binding in bindings)
    ordered = tuple(sorted(validated, key=lambda binding: binding.picture_index))
    expected_indices = tuple(range(1, len(ordered) + 1))
    if tuple(binding.picture_index for binding in ordered) != expected_indices:
        raise ValueError("reference picture indices must be contiguous from 1")
    if tuple(binding.subject_index for binding in ordered) != expected_indices:
        raise ValueError(
            "reference subject indices must be contiguous from 1 in picture order"
        )

    unique_fields = {
        "reference_id": tuple(binding.reference_id for binding in ordered),
        "asset_id": tuple(binding.asset.asset_id for binding in ordered),
        "sha256": tuple(binding.asset.sha256 for binding in ordered),
    }
    for field, values in unique_fields.items():
        if len(set(values)) != len(values):
            raise ValueError(f"reference {field} values must be unique")

    return "\n".join(
        f"<Subject {binding.subject_index}> is {binding.label} "
        f"from <Picture {binding.picture_index}>; {_REF_SUFFIX[binding.source_kind]}."
        for binding in ordered
    )


def continuity_locks_for(
    contracts: tuple[ShotContinuityContract, ...],
) -> tuple[str, ...]:
    """Project only shared identity, never promote local states to global locks.

    Full contracts (including cinematography and boundary states) are supplied
    separately as ordered JSON to the director and retained in the bundle.
    They belong in typed shot/action fields, not a second global narrative.
    Even identical endpoint positions do not imply a stationary subject.
    """
    if not contracts:
        return ()
    common = set.intersection(*(
        {subject.subject_id for subject in contract.subjects}
        for contract in contracts
    ))
    return tuple(dict.fromkeys(
        f"subject {subject.subject_id} keeps identity"
        for subject in contracts[0].subjects
        if subject.subject_id in common
    ))


def compile_shot_bundle(
    *,
    segment_id: str,
    source_shot_ids: tuple[str, ...],
    contracts: tuple[ShotContinuityContract, ...],
    optimization: H3PromptOptimizationResult,
    decision: H3ModeDecision,
    risk_report: ShotRiskReport,
    first_frame: FrameEvidence,
    adapter: Literal["base-h3", "h3-ref"],
    last_frame: FrameEvidence | None = None,
    control_frames: tuple[FrameEvidence, ...] = (),
    references: tuple[H3ReferenceBinding, ...] = (),
    diagnostics: tuple[str, ...] = (),
) -> CompiledShotBundle:
    """Compile one validated H3 optimization result into a hashed bundle."""
    if decision.mode is None:
        raise ValueError("blocked mode decision cannot compile a bundle")
    if adapter == "h3-ref":
        compile_reference_definitions(references)

    try:
        optimization_mode = H3Mode(optimization.plan.mode).value
    except (TypeError, ValueError) as exc:
        raise ValueError("optimization plan mode is not a valid H3 mode") from exc
    if optimization_mode != decision.mode:
        raise ValueError(
            "optimization plan mode must match the resolved mode decision"
        )

    contract_refs: list[ContractRef] = []
    for contract in contracts:
        if contract.revision <= 0:
            raise ValueError("compiled contracts require revision greater than zero")
        contract_refs.append(
            ContractRef(
                shot_id=contract.shot_id,
                revision=contract.revision,
                sha256=contract.contract_sha256,
            )
        )

    payload = {
        "schema_version": 1,
        "segment_id": segment_id,
        "source_shot_ids": source_shot_ids,
        "contracts": tuple(contract_refs),
        "compiler_id": "minimax-h3-shot-compiler",
        "compiler_version": H3_SHOT_COMPILER_VERSION,
        "adapter": adapter,
        "mode": decision.mode,
        "prompt": optimization.prompt,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "control_frames": control_frames,
        "references": references,
        "risk_report": risk_report,
        "mode_decision": decision,
        "diagnostics": diagnostics,
    }
    json_payload = CompiledShotBundle.model_construct(
        **payload,
        bundle_sha256="0" * 64,
    ).model_dump(mode="json", exclude={"bundle_sha256"})
    return CompiledShotBundle.model_validate(
        {**json_payload, "bundle_sha256": canonical_sha256(json_payload)}
    )


__all__ = [
    "H3_SHOT_COMPILER_VERSION",
    "compile_reference_definitions",
    "compile_shot_bundle",
    "continuity_locks_for",
]
