from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import novelvideo.shot_continuity as shot_continuity
from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptOptimizationResult,
)
from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_prompt_quality import (
    H3PromptQualityReport,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.shot_continuity import (
    BoundaryState,
    CameraLock,
    FrameEvidence,
    H3ModeDecision,
    H3ReferenceBinding,
    LightingLock,
    PropLock,
    RiskDimensionScore,
    SceneLock,
    ShotContinuityContract,
    ShotRiskReport,
    SubjectLock,
    canonical_sha256,
    compile_shot_bundle,
    continuity_locks_for,
)
from novelvideo.shot_continuity import compiler as compiler_module


_FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "runninghub"
    / "minimax_h3_ref_compiler_contract.json"
)


def _frame(asset_id: str, digest_char: str) -> FrameEvidence:
    return FrameEvidence(asset_id=asset_id, sha256=digest_char * 64)


def _contract(
    shot_id: str,
    *,
    revision: int = 1,
    subject_state: str = "coat buttoned",
) -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id=shot_id,
        scene_id="corridor",
        scene=SceneLock(
            scene_state="door closed",
            space_anchor="beside the north wall",
            axis="north-south",
        ),
        subjects=(
            SubjectLock(
                subject_id="lin",
                state=subject_state,
                screen_position="screen left",
            ),
        ),
        props=(
            PropLock(
                prop_id="cup",
                state="full",
                owner_subject_id="lin",
                held_in_hand="right",
                contact="touching chest",
            ),
        ),
        camera=CameraLock(
            shot_size="medium",
            angle="eye-level",
            composition="Lin on left third",
            motion="static",
        ),
        lighting=LightingLock(direction="camera right"),
        boundary=BoundaryState(
            carry_in="Lin faces the door",
            planned_carry_out="Lin grips the handle",
        ),
    )


def _risk_report() -> ShotRiskReport:
    return ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )


def _optimization(mode: H3Mode = H3Mode.I2VA) -> H3PromptOptimizationResult:
    final_phase = "settle" if mode is H3Mode.FL2VA else "execute"
    plan = H3DirectorPlan(
        mode=mode,
        total_frames=24,
        visual_style="cinematic realism",
        continuity_locks=("preserve identity",),
        shots=(
            H3ShotPlan(
                shot_id="1",
                start_frame=0,
                end_frame=24,
                framing="medium",
                angle="eye-level",
                focus="Lin",
                composition="Lin remains on the left third",
                camera=H3CameraPlan(type="static"),
                actions=(
                    H3ActionPlan(
                        phase="establish",
                        start_frame=0,
                        end_frame=6,
                        description="Hold the exact frame-zero pose.",
                    ),
                    H3ActionPlan(
                        phase=final_phase,
                        start_frame=6,
                        end_frame=24,
                        description="Lin turns and holds his hand on the door.",
                    ),
                ),
            ),
        ),
        frame_differences=(
            (
                H3FrameDifference(
                    description="Lin reaches the terminal pose.",
                    convergence_frame=18,
                ),
            )
            if mode is H3Mode.FL2VA
            else ()
        ),
        soundscape="Quiet corridor ambience.",
        music="No music.",
    )
    return H3PromptOptimizationResult(
        prompt="A continuity-aware H3 prompt.",
        plan=plan,
        quality_report=H3PromptQualityReport(passed=True),
        input_hash="a" * 64,
    )


def _binding(
    reference_id: str,
    *,
    picture_index: int,
    subject_index: int | None = None,
    source_kind: str = "character_identity",
    label: str = "Shen Li",
    asset_id: str | None = None,
    digest_char: str = "d",
) -> H3ReferenceBinding:
    return H3ReferenceBinding(
        reference_id=reference_id,
        source_kind=source_kind,
        subject_index=subject_index or picture_index,
        picture_index=picture_index,
        label=label,
        asset=_frame(asset_id or f"asset-{reference_id}", digest_char),
    )


def test_reference_definitions_match_local_contract_fixture() -> None:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    bindings = tuple(
        H3ReferenceBinding.model_validate(binding)
        for binding in fixture["bindings"]
    )

    definitions = compiler_module.compile_reference_definitions(bindings)

    assert set(fixture) == {
        "schema_version",
        "fixture_kind",
        "provider_payload",
        "bindings",
        "expected_reference_definitions",
    }
    assert fixture["schema_version"] == 1
    assert fixture["fixture_kind"] == "local_h3_ref_compiler_contract"
    assert fixture["provider_payload"] is False
    assert definitions.splitlines() == fixture["expected_reference_definitions"]
    assert shot_continuity.compile_reference_definitions is (
        compiler_module.compile_reference_definitions
    )


def test_reference_definitions_sort_by_picture_and_keep_label_verbatim() -> None:
    definitions = compiler_module.compile_reference_definitions(
        (
            _binding(
                "scene:atrium",
                source_kind="scene_base",
                picture_index=2,
                subject_index=2,
                label="Atrium 夜景",
                digest_char="e",
            ),
            _binding(
                "character:shen-li",
                picture_index=1,
                subject_index=1,
                label="Shen Li",
            ),
            _binding(
                "prop:jade-cup",
                source_kind="prop",
                picture_index=3,
                subject_index=3,
                label="Jade cup",
                digest_char="f",
            ),
        )
    )

    assert definitions.splitlines() == [
        "<Subject 1> is Shen Li from <Picture 1>; preserve identity, hair, and wardrobe.",
        "<Subject 2> is Atrium 夜景 from <Picture 2>; preserve architecture and set dressing.",
        "<Subject 3> is Jade cup from <Picture 3>; preserve shape, material, and visible state.",
    ]


@pytest.mark.parametrize(
    ("bindings", "message"),
    [
        ((), "non-empty"),
        (
            (
                _binding("one", picture_index=1),
                _binding("three", picture_index=3),
            ),
            "picture indices",
        ),
        (
            (
                _binding("one", picture_index=1, subject_index=2),
                _binding("two", picture_index=2, subject_index=1),
            ),
            "subject indices",
        ),
    ],
)
def test_reference_definitions_reject_empty_or_non_contiguous_indices(
    bindings: tuple[H3ReferenceBinding, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        compiler_module.compile_reference_definitions(bindings)


@pytest.mark.parametrize("duplicate_field", ["reference_id", "asset_id", "sha256"])
def test_reference_definitions_reject_duplicate_identity_fields(
    duplicate_field: str,
) -> None:
    first = _binding("one", picture_index=1, digest_char="a")
    second = _binding("two", picture_index=2, digest_char="b")
    if duplicate_field == "reference_id":
        second = second.model_copy(update={"reference_id": first.reference_id})
    elif duplicate_field == "asset_id":
        second = second.model_copy(
            update={"asset": second.asset.model_copy(update={"asset_id": first.asset.asset_id})}
        )
    else:
        second = second.model_copy(
            update={"asset": second.asset.model_copy(update={"sha256": first.asset.sha256})}
        )

    with pytest.raises(ValueError, match=duplicate_field):
        compiler_module.compile_reference_definitions((first, second))


@pytest.mark.parametrize("source_kind", ["unknown", ["character_identity"]])
def test_reference_definitions_reject_model_constructed_source_kind(
    source_kind: object,
) -> None:
    invalid = H3ReferenceBinding.model_construct(
        reference_id="bad",
        source_kind=source_kind,
        subject_index=1,
        picture_index=1,
        label="Shen Li",
        asset=_frame("bad-asset", "a"),
    )

    with pytest.raises(ValueError, match="source kind"):
        compiler_module.compile_reference_definitions((invalid,))


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("reference_id", ""),
        ("label", ""),
        ("subject_index", 0),
        ("picture_index", object()),
        ("asset", object()),
    ],
)
def test_reference_definitions_revalidate_model_constructed_binding_fields(
    field: str,
    invalid_value: object,
) -> None:
    values = {
        "reference_id": "character:shen-li",
        "source_kind": "character_identity",
        "subject_index": 1,
        "picture_index": 1,
        "label": "Shen Li",
        "asset": _frame("asset-shen-li", "a"),
    }
    values[field] = invalid_value
    invalid = H3ReferenceBinding.model_construct(**values)

    with pytest.raises(ValueError):
        compiler_module.compile_reference_definitions((invalid,))


@pytest.mark.parametrize(
    "asset",
    [
        FrameEvidence.model_construct(asset_id="", sha256="a" * 64),
        FrameEvidence.model_construct(asset_id="asset-shen-li", sha256="bad"),
    ],
)
def test_reference_definitions_revalidate_model_constructed_nested_asset(
    asset: FrameEvidence,
) -> None:
    invalid = H3ReferenceBinding.model_construct(
        reference_id="character:shen-li",
        source_kind="character_identity",
        subject_index=1,
        picture_index=1,
        label="Shen Li",
        asset=asset,
    )

    with pytest.raises(ValueError):
        compiler_module.compile_reference_definitions((invalid,))


def test_continuity_locks_preserve_contract_and_domain_order() -> None:
    first = _contract("shot-1")
    second = _contract("shot-2")

    locks = continuity_locks_for((first, second))

    assert locks == (
        "scene state stays door closed",
        "space anchor stays beside the north wall",
        "action axis stays north-south",
        "subject lin keeps identity and coat buttoned",
        "subject lin stays screen left",
        "prop cup: full, owner lin, held in right hand, touching chest",
        "camera medium, eye-level, static; composition Lin on left third",
        "key light direction stays camera right",
        "frame 0 state: Lin faces the door",
        "planned terminal state: Lin grips the handle",
    )


def test_continuity_locks_keep_identity_without_an_empty_subject_state() -> None:
    locks = continuity_locks_for((_contract("shot-1", subject_state=""),))

    assert "subject lin keeps identity" in locks
    assert "subject lin keeps identity and " not in locks


def test_base_bundle_freezes_ordered_contract_refs_and_real_digest() -> None:
    contracts = (_contract("shot-1"), _contract("shot-2", revision=2))
    bundle = compile_shot_bundle(
        segment_id="shot-1--shot-2",
        source_shot_ids=("shot-1", "shot-2"),
        contracts=contracts,
        optimization=_optimization(H3Mode.FL2VA),
        decision=H3ModeDecision(requested="auto", mode="fl2va"),
        risk_report=_risk_report(),
        first_frame=_frame("first", "1"),
        last_frame=_frame("last", "2"),
        adapter="base-h3",
    )

    assert tuple(item.shot_id for item in bundle.contracts) == (
        "shot-1",
        "shot-2",
    )
    assert tuple(item.revision for item in bundle.contracts) == (1, 2)
    assert bundle.contracts[0].sha256 == contracts[0].contract_sha256
    assert bundle.last_frame == _frame("last", "2")
    assert bundle.bundle_sha256 == canonical_sha256(
        bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    )


def test_ref_bundle_requires_bindings_and_never_replaces_first_frame() -> None:
    arguments = {
        "segment_id": "shot-1",
        "source_shot_ids": ("shot-1",),
        "contracts": (_contract("shot-1"),),
        "optimization": _optimization(),
        "decision": H3ModeDecision(requested="auto", mode="i2va"),
        "risk_report": _risk_report(),
        "first_frame": _frame("first", "1"),
    }
    with pytest.raises(ValueError, match="reference bindings"):
        compile_shot_bundle(**arguments, adapter="h3-ref", references=())

    binding = _binding("character:lin", picture_index=1)
    bundle = compile_shot_bundle(
        **arguments, adapter="h3-ref", references=(binding,)
    )

    assert bundle.first_frame == _frame("first", "1")
    assert bundle.references == (binding,)


def test_ref_bundle_rejects_invalid_bindings_at_compile_boundary() -> None:
    invalid = H3ReferenceBinding.model_construct(
        reference_id="character:shen-li",
        source_kind="not-a-source-kind",
        subject_index=1,
        picture_index=1,
        label="Shen Li",
        asset=_frame("asset-shen-li", "d"),
    )

    with pytest.raises(ValueError, match="source kind"):
        compile_shot_bundle(
            segment_id="shot-1",
            source_shot_ids=("shot-1",),
            contracts=(_contract("shot-1"),),
            optimization=_optimization(),
            decision=H3ModeDecision(requested="auto", mode="i2va"),
            risk_report=_risk_report(),
            first_frame=_frame("first", "1"),
            adapter="h3-ref",
            references=(invalid,),
        )


def test_bundle_rejects_zero_revision_and_unresolved_decision() -> None:
    arguments = {
        "segment_id": "shot-1",
        "source_shot_ids": ("shot-1",),
        "optimization": _optimization(),
        "risk_report": _risk_report(),
        "first_frame": _frame("first", "1"),
        "adapter": "base-h3",
    }
    with pytest.raises(ValueError, match="revision"):
        compile_shot_bundle(
            **arguments,
            contracts=(_contract("shot-1", revision=0),),
            decision=H3ModeDecision(requested="auto", mode="i2va"),
        )
    with pytest.raises(ValueError, match="blocked mode decision"):
        compile_shot_bundle(
            **arguments,
            contracts=(_contract("shot-1"),),
            decision=H3ModeDecision(requested="auto", mode=None),
        )


def test_bundle_rejects_optimization_mode_mismatch_for_enum_and_string() -> None:
    arguments = {
        "segment_id": "shot-1",
        "source_shot_ids": ("shot-1",),
        "contracts": (_contract("shot-1"),),
        "decision": H3ModeDecision(requested="auto", mode="fl2va"),
        "risk_report": _risk_report(),
        "first_frame": _frame("first", "1"),
        "last_frame": _frame("last", "2"),
        "adapter": "base-h3",
    }
    with pytest.raises(ValueError, match="optimization plan mode"):
        compile_shot_bundle(
            **arguments, optimization=_optimization(H3Mode.I2VA)
        )
    with pytest.raises(ValueError, match="optimization plan mode"):
        compile_shot_bundle(
            **arguments,
            optimization=H3PromptOptimizationResult.model_construct(
                prompt="Prompt.", plan=SimpleNamespace(mode="i2va")
            ),
        )


def test_bundle_hash_tracks_references_while_preserving_all_frames() -> None:
    last_frame = _frame("last", "2")
    control_frames = (_frame("control-1", "3"), _frame("control-2", "4"))
    base_arguments = {
        "segment_id": "shot-1",
        "source_shot_ids": ("shot-1",),
        "contracts": (_contract("shot-1"),),
        "optimization": _optimization(),
        "decision": H3ModeDecision(requested="auto", mode="i2va"),
        "risk_report": _risk_report(),
        "first_frame": _frame("first", "1"),
        "last_frame": last_frame,
        "control_frames": control_frames,
        "adapter": "h3-ref",
    }
    first = _binding("one", picture_index=1)
    second = _binding("two", picture_index=2, digest_char="e")

    bundle = compile_shot_bundle(**base_arguments, references=(first, second))
    original = bundle.bundle_sha256
    fewer = compile_shot_bundle(
        **base_arguments, references=(first,)
    ).bundle_sha256
    reordered = compile_shot_bundle(
        **base_arguments, references=(second, first)
    ).bundle_sha256
    relabeled = compile_shot_bundle(
        **base_arguments,
        references=(first.model_copy(update={"label": "Shen Li variant"}), second),
    ).bundle_sha256
    rehashed = compile_shot_bundle(
        **base_arguments,
        references=(
            first.model_copy(update={"asset": _frame("asset-one", "f")}),
            second,
        ),
    ).bundle_sha256

    assert len({original, fewer, reordered, relabeled, rehashed}) == 5
    assert bundle.first_frame == _frame("first", "1")
    assert bundle.last_frame == last_frame
    assert bundle.control_frames == control_frames
