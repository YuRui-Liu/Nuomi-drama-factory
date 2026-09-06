from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from novelvideo.shot_continuity import (
    AssetEvidence,
    BoundaryState,
    CameraLock,
    CompiledShotBundle,
    ContractRef,
    Evidence,
    FrameEvidence,
    H3ModeDecision,
    H3ReferenceBinding,
    PropLock,
    RiskDimensionScore,
    SceneLock,
    ShotContinuityContract,
    ShotRiskReport,
    SubjectLock,
    canonical_sha256,
)
from novelvideo.shot_continuity.hashing import canonical_json


def _contract(*, revision: int = 1, shot_id: str = "shot-1") -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id=shot_id,
        scene_id="scene-1",
        scene=SceneLock(),
        camera=CameraLock(shot_size="medium", angle="eye-level"),
        boundary=BoundaryState(carry_in="door closed", planned_carry_out="door open"),
    )


def _risk_report() -> ShotRiskReport:
    return ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )


def _bundle(**overrides: object) -> CompiledShotBundle:
    contract = _contract()
    values: dict[str, object] = {
        "segment_id": "segment-1",
        "source_shot_ids": (contract.shot_id,),
        "contracts": (
            ContractRef(
                shot_id=contract.shot_id,
                revision=contract.revision,
                sha256=contract.contract_sha256,
            ),
        ),
        "compiler_version": 1,
        "adapter": "base-h3",
        "mode": "i2va",
        "prompt": "A stable tracking shot.",
        "first_frame": FrameEvidence(asset_id="frame-1", sha256="a" * 64),
        "risk_report": _risk_report(),
    }
    values.update(overrides)
    values.setdefault(
        "mode_decision",
        H3ModeDecision(requested="auto", mode=values["mode"]),
    )
    if "bundle_sha256" not in values:
        unhashed_bundle = CompiledShotBundle.model_construct(**values)
        values["bundle_sha256"] = canonical_sha256(
            unhashed_bundle.model_dump(mode="json", exclude={"bundle_sha256"})
        )
    return CompiledShotBundle(**values)


def test_canonical_sha256_is_mapping_order_independent() -> None:
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    assert canonical_json({"text": "中文"}) == '{"text":"中文"}'


def test_contract_revision_changes_canonical_hash() -> None:
    assert _contract(revision=1).contract_sha256 != _contract(revision=2).contract_sha256


def test_hashing_serializes_objects_via_json_model_dump() -> None:
    @dataclass
    class DumpProbe:
        mode: str | None = None

        def model_dump(self, *, mode: str) -> dict[str, str]:
            self.mode = mode
            return {"value": "ok"}

    probe = DumpProbe()
    assert canonical_json(probe) == '{"value":"ok"}'
    assert probe.mode == "json"


def test_inferred_evidence_requires_confidence() -> None:
    with pytest.raises(ValidationError):
        Evidence(source="inferred")

    assert Evidence(source="inferred", confidence=0.7).confidence == 0.7


def test_h3_mode_decision_requires_mode_even_when_it_is_none() -> None:
    with pytest.raises(ValidationError):
        H3ModeDecision(requested="auto")

    assert H3ModeDecision(requested="auto", mode=None).mode is None


def test_planned_and_observed_carry_out_are_separate() -> None:
    boundary = BoundaryState(carry_in="sitting", planned_carry_out="standing")

    assert boundary.planned_carry_out == "standing"
    assert boundary.observed_carry_out is None


def test_nonempty_text_fields_preserve_surrounding_whitespace() -> None:
    asset = AssetEvidence(asset_id=" asset-1 ", sha256="a" * 64)
    boundary = BoundaryState(
        carry_in=" carry in ",
        planned_carry_out=" carry out ",
    )
    bundle = _bundle(prompt=" prompt ")

    assert asset.asset_id == " asset-1 "
    assert boundary.carry_in == " carry in "
    assert boundary.planned_carry_out == " carry out "
    assert bundle.prompt == " prompt "


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (AssetEvidence(asset_id="asset", sha256="a" * 64), "asset_id"),
        (SubjectLock(subject_id="subject"), "subject_id"),
        (PropLock(prop_id="prop"), "prop_id"),
    ],
)
def test_models_are_frozen(model: object, field: str) -> None:
    with pytest.raises(ValidationError):
        setattr(model, field, "changed")


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SceneLock(unknown="value")


def test_nonempty_identifiers_reject_empty_values() -> None:
    with pytest.raises(ValidationError):
        AssetEvidence(asset_id="", sha256="a" * 64)
    with pytest.raises(ValidationError):
        SubjectLock(subject_id="")
    with pytest.raises(ValidationError):
        PropLock(prop_id="")


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64])
def test_sha256_fields_require_lowercase_hex(value: str) -> None:
    with pytest.raises(ValidationError):
        AssetEvidence(asset_id="asset", sha256=value)


def test_accepted_boundary_deviation_requires_reason() -> None:
    with pytest.raises(ValidationError):
        BoundaryState(
            carry_in="inside",
            planned_carry_out="outside",
            deviation_accepted=True,
            deviation_reason=" ",
        )


def test_predecessor_fields_must_be_provided_together() -> None:
    with pytest.raises(ValidationError):
        _contract().model_copy(update={"predecessor_shot_id": "shot-0"}).model_validate(
            _contract().model_copy(update={"predecessor_shot_id": "shot-0"}).model_dump()
        )

    contract = ShotContinuityContract(
        revision=1,
        shot_id="shot-2",
        scene_id="scene-1",
        predecessor_shot_id="shot-1",
        predecessor_revision=1,
        scene=SceneLock(),
        camera=CameraLock(shot_size="close-up", angle="low"),
        boundary=BoundaryState(carry_in="moving", planned_carry_out="stopped"),
    )
    assert contract.predecessor_revision == 1


def test_bundle_contract_order_must_match_source_shot_ids() -> None:
    with pytest.raises(ValidationError):
        _bundle(source_shot_ids=("different-shot",))


def test_bundle_rejects_digest_from_an_older_payload() -> None:
    original_digest = _bundle().bundle_sha256

    with pytest.raises(ValidationError):
        _bundle(prompt="Changed prompt.", bundle_sha256=original_digest)


@pytest.mark.parametrize(
    "mode_decision",
    [
        H3ModeDecision(requested="auto", mode=None),
        H3ModeDecision(requested="auto", mode="fl2va"),
    ],
)
def test_bundle_requires_resolved_matching_mode_decision(
    mode_decision: H3ModeDecision,
) -> None:
    with pytest.raises(ValidationError):
        _bundle(mode_decision=mode_decision)


@pytest.mark.parametrize(
    ("slot", "wrong_dimension"),
    [
        ("spatial", "identity"),
        ("identity", "motion"),
        ("motion", "continuity"),
        ("continuity", "spatial"),
    ],
)
def test_risk_report_dimension_must_match_its_slot(
    slot: str,
    wrong_dimension: str,
) -> None:
    scores = {
        "spatial": RiskDimensionScore(dimension="spatial", level=0),
        "identity": RiskDimensionScore(dimension="identity", level=0),
        "motion": RiskDimensionScore(dimension="motion", level=0),
        "continuity": RiskDimensionScore(dimension="continuity", level=0),
    }
    scores[slot] = RiskDimensionScore(dimension=wrong_dimension, level=0)

    with pytest.raises(ValidationError):
        ShotRiskReport(**scores)


def test_fl2va_bundle_requires_last_frame() -> None:
    with pytest.raises(ValidationError):
        _bundle(mode="fl2va", last_frame=None)

    bundle = _bundle(
        mode="fl2va",
        last_frame=FrameEvidence(asset_id="frame-2", sha256="c" * 64),
    )
    assert bundle.last_frame is not None


def test_h3_ref_bundle_requires_references() -> None:
    with pytest.raises(ValidationError):
        _bundle(adapter="h3-ref")

    reference = H3ReferenceBinding(
        reference_id="reference-1",
        source_kind="character_identity",
        subject_index=1,
        picture_index=1,
        label="hero",
        asset=FrameEvidence(asset_id="reference-frame", sha256="d" * 64),
    )
    assert _bundle(adapter="h3-ref", references=(reference,)).references == (reference,)


def test_bundle_has_contract_refs_instead_of_single_contract_revision() -> None:
    assert "contract_revision" not in CompiledShotBundle.model_fields
    with pytest.raises(ValidationError):
        _bundle(contract_revision=1)
