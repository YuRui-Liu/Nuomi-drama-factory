"""Immutable domain contracts for shot-to-shot continuity."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .hashing import canonical_sha256
from novelvideo.director_plan.cinematography import ShotCinematography

NonEmptyStr = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
H3RequestedMode = Literal["auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"]
H3ResolvedMode = Literal["t2va", "i2va", "fl2va", "l2va", "ref2va"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_FrozenModel):
    source: Literal["explicit", "director_world", "inferred"] = "explicit"
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_inferred_confidence(self) -> Self:
        if self.source == "inferred" and self.confidence is None:
            raise ValueError("inferred evidence requires confidence")
        return self


class AssetEvidence(_FrozenModel):
    asset_id: NonEmptyStr
    sha256: Sha256


class SceneLock(_FrozenModel):
    scene_state: str = ""
    space_anchor: str = ""
    landmarks: tuple[str, ...] = ()
    axis: str = ""
    camera_side: str = ""
    screen_direction: str = ""
    assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class SubjectLock(_FrozenModel):
    subject_id: NonEmptyStr
    state: str = ""
    screen_position: str = ""
    facing: str = ""
    gaze_target: str = ""
    visible: bool = True
    identity_assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class PropLock(_FrozenModel):
    prop_id: NonEmptyStr
    state: str = ""
    owner_subject_id: str = ""
    held_in_hand: Literal["", "left", "right", "both"] = ""
    contact: str = ""
    critical: bool = False
    assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class CameraLock(_FrozenModel):
    shot_size: NonEmptyStr
    angle: NonEmptyStr
    composition: str = ""
    motion: str = "static"
    axis: str = ""
    screen_direction: str = ""
    control_frames: tuple[AssetEvidence, ...] = ()


class LightingLock(_FrozenModel):
    key_source: str = ""
    direction: str = ""
    shadow_direction: str = ""
    exposure_priority: str = ""
    color_temperature: str = ""


class BoundaryState(_FrozenModel):
    carry_in: NonEmptyStr
    planned_carry_out: NonEmptyStr
    observed_carry_out: NonEmptyStr | None = None
    deviation_accepted: bool = False
    deviation_reason: str = ""

    @model_validator(mode="after")
    def require_accepted_deviation_reason(self) -> Self:
        if self.deviation_accepted and not self.deviation_reason.strip():
            raise ValueError("accepted deviation requires a nonblank reason")
        return self


class DirectorWorldBinding(_FrozenModel):
    snapshot_sha256: Sha256
    control_frame: AssetEvidence | None = None


class ShotContinuityContract(_FrozenModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0)
    shot_id: NonEmptyStr
    scene_id: NonEmptyStr
    predecessor_shot_id: NonEmptyStr | None = None
    predecessor_revision: int | None = Field(default=None, gt=0)
    scene: SceneLock
    subjects: tuple[SubjectLock, ...] = ()
    props: tuple[PropLock, ...] = ()
    camera: CameraLock
    lighting: LightingLock = Field(default_factory=LightingLock)
    boundary: BoundaryState
    director_world: DirectorWorldBinding | None = None
    cinematography: ShotCinematography | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def require_complete_predecessor(self) -> Self:
        if (self.predecessor_shot_id is None) != (self.predecessor_revision is None):
            raise ValueError("predecessor shot id and revision must be provided together")
        return self

    @property
    def contract_sha256(self) -> str:
        return canonical_sha256(self)


class ContractRef(_FrozenModel):
    shot_id: NonEmptyStr
    revision: int = Field(gt=0)
    sha256: Sha256


class RiskDimensionScore(_FrozenModel):
    dimension: Literal["spatial", "identity", "motion", "continuity"]
    level: Literal[0, 1, 2]
    reasons: tuple[str, ...] = ()


class ShotRiskReport(_FrozenModel):
    spatial: RiskDimensionScore
    identity: RiskDimensionScore
    motion: RiskDimensionScore
    continuity: RiskDimensionScore
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_dimensions_to_match_slots(self) -> Self:
        for dimension in ("spatial", "identity", "motion", "continuity"):
            if getattr(self, dimension).dimension != dimension:
                raise ValueError(f"{dimension} risk score must use its matching dimension")
        return self


class H3ModeInputSnapshot(_FrozenModel):
    has_first_frame: bool
    has_last_frame: bool
    reference_count: int = Field(ge=0)


class H3ModeDecision(_FrozenModel):
    requested: H3RequestedMode
    mode: H3ResolvedMode | None
    input_snapshot: H3ModeInputSnapshot | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    reason_codes: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_consistent_resolution_state(self) -> Self:
        if self.mode is None:
            if not self.blockers:
                raise ValueError("blocked mode decision requires blockers")
            if self.reason_codes:
                raise ValueError("blocked mode decision forbids success reason codes")
            return self
        if self.blockers:
            raise ValueError("resolved mode decision forbids blockers")
        return self


class FrameEvidence(_FrozenModel):
    asset_id: NonEmptyStr
    sha256: Sha256


class H3ReferenceBinding(_FrozenModel):
    reference_id: NonEmptyStr
    source_kind: Literal["character_identity", "scene_base", "prop"]
    subject_index: int = Field(gt=0)
    picture_index: int = Field(gt=0)
    label: NonEmptyStr
    asset: FrameEvidence


class CompiledShotBundle(_FrozenModel):
    schema_version: Literal[1, 2] = 1
    segment_id: NonEmptyStr
    source_shot_ids: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1, max_length=2)]
    contracts: Annotated[tuple[ContractRef, ...], Field(min_length=1, max_length=2)]
    compiler_id: Literal["minimax-h3-shot-compiler"] = "minimax-h3-shot-compiler"
    compiler_version: int = Field(gt=0)
    adapter: Literal["base-h3", "h3-ref"]
    mode: H3ResolvedMode
    prompt: NonEmptyStr
    first_frame: FrameEvidence | None = None
    last_frame: FrameEvidence | None = None
    control_frames: tuple[FrameEvidence, ...] = ()
    references: tuple[H3ReferenceBinding, ...] = ()
    risk_report: ShotRiskReport
    mode_decision: H3ModeDecision
    diagnostics: tuple[str, ...] = ()
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def validate_cross_field_invariants(self) -> Self:
        contract_shot_ids = tuple(contract.shot_id for contract in self.contracts)
        if contract_shot_ids != self.source_shot_ids:
            raise ValueError("contract shot ids must match source shot ids in order")
        if self.mode_decision.mode is None:
            raise ValueError("compiled bundle requires a resolved mode decision")
        if self.mode_decision.mode != self.mode:
            raise ValueError("mode decision must match the compiled bundle mode")

        if self.schema_version == 1:
            self._validate_v1_contract()
        else:
            self._validate_v2_contract()

        payload = self.model_dump(mode="json", exclude={"bundle_sha256"})
        valid_digests = {canonical_sha256(payload)}
        if self.schema_version == 1 and self.mode_decision.input_snapshot is None:
            legacy_payload = dict(payload)
            legacy_decision = dict(legacy_payload["mode_decision"])
            legacy_decision.pop("input_snapshot", None)
            legacy_payload["mode_decision"] = legacy_decision
            valid_digests.add(canonical_sha256(legacy_payload))
        if self.bundle_sha256 not in valid_digests:
            raise ValueError("bundle sha256 must match the canonical bundle payload")
        return self

    def _validate_v1_contract(self) -> None:
        if self.mode not in {"i2va", "fl2va"}:
            raise ValueError("schema v1 only supports i2va and fl2va modes")
        if self.first_frame is None:
            raise ValueError("schema v1 requires a first frame")
        if self.mode == "fl2va" and self.last_frame is None:
            raise ValueError("fl2va mode requires a last frame")
        if self.adapter == "h3-ref" and not self.references:
            raise ValueError("h3-ref adapter requires references")

    def _validate_v2_contract(self) -> None:
        expected_adapter = "h3-ref" if self.mode == "ref2va" else "base-h3"
        if self.adapter != expected_adapter:
            raise ValueError(f"{self.mode} mode requires the {expected_adapter} adapter")

        requires_first = self.mode in {"i2va", "fl2va"}
        requires_last = self.mode in {"fl2va", "l2va"}
        requires_references = self.mode == "ref2va"
        if (self.first_frame is not None) != requires_first:
            requirement = "requires" if requires_first else "forbids"
            raise ValueError(f"{self.mode} mode {requirement} a first frame")
        if (self.last_frame is not None) != requires_last:
            requirement = "requires" if requires_last else "forbids"
            raise ValueError(f"{self.mode} mode {requirement} a last frame")
        if bool(self.references) != requires_references:
            requirement = "requires" if requires_references else "forbids"
            raise ValueError(f"{self.mode} mode {requirement} references")

        snapshot = self.mode_decision.input_snapshot
        if snapshot is None:
            raise ValueError("schema v2 requires an input snapshot")
        if snapshot.reference_count != len(self.references):
            raise ValueError("input snapshot must match bundle inputs")
        reference_priority_snapshot = (
            self.mode == "ref2va" and self.mode_decision.requested == "auto"
        )
        if not reference_priority_snapshot and (
            snapshot.has_first_frame != (self.first_frame is not None)
            or snapshot.has_last_frame != (self.last_frame is not None)
        ):
            raise ValueError("input snapshot must match bundle inputs")
