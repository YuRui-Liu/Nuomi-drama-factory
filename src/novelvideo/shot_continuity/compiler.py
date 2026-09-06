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


def continuity_locks_for(
    contracts: tuple[ShotContinuityContract, ...],
) -> tuple[str, ...]:
    """Return stable, de-duplicated prompt locks from ordered contracts."""
    values: list[str] = []
    for contract in contracts:
        scene = contract.scene
        if scene.scene_state:
            values.append(f"scene state stays {scene.scene_state}")
        if scene.space_anchor:
            values.append(f"space anchor stays {scene.space_anchor}")
        if scene.axis:
            values.append(f"action axis stays {scene.axis}")

        for subject in contract.subjects:
            identity = f"subject {subject.subject_id} keeps identity"
            if subject.state:
                identity = f"{identity} and {subject.state}"
            values.append(identity)
            if subject.screen_position:
                values.append(
                    f"subject {subject.subject_id} stays {subject.screen_position}"
                )

        for prop in contract.props:
            prop_state = ", ".join(
                value
                for value in (
                    prop.state,
                    f"owner {prop.owner_subject_id}" if prop.owner_subject_id else "",
                    f"held in {prop.held_in_hand} hand" if prop.held_in_hand else "",
                    prop.contact,
                )
                if value
            )
            if prop_state:
                values.append(f"prop {prop.prop_id}: {prop_state}")

        camera = contract.camera
        values.append(
            f"camera {camera.shot_size}, {camera.angle}, {camera.motion}; "
            f"composition {camera.composition or 'unchanged'}"
        )
        if contract.lighting.direction:
            values.append(
                f"key light direction stays {contract.lighting.direction}"
            )
        if contract.boundary.carry_in:
            values.append(f"frame 0 state: {contract.boundary.carry_in}")
        if contract.boundary.planned_carry_out:
            values.append(
                "planned terminal state: "
                f"{contract.boundary.planned_carry_out}"
            )
    return tuple(dict.fromkeys(values))


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
    if adapter == "h3-ref" and not references:
        raise ValueError("h3-ref adapter requires reference bindings")

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
    "compile_shot_bundle",
    "continuity_locks_for",
]
