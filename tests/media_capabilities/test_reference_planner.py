from __future__ import annotations

from novelvideo.media_capabilities.models import CapabilityProfile, MediaCapability
from novelvideo.media_capabilities.reference_planner import (
    ReferenceCandidate,
    ReferenceKind,
    plan_references,
)
from novelvideo.production_workflow.models import GenerationAttempt


def _profile(max_references: int) -> CapabilityProfile:
    return CapabilityProfile(
        id="image-primary",
        media_type="image",
        capabilities=[MediaCapability.IMAGE_SINGLE],
        modes=["reference"],
        max_references=max_references,
    )


def _candidate(reference_id: str, kind: ReferenceKind, priority: int) -> ReferenceCandidate:
    return ReferenceCandidate(
        reference_id=reference_id,
        kind=kind,
        asset_path=f"assets/{reference_id}.png",
        priority=priority,
    )


def test_reference_kind_contract_covers_all_production_reference_types() -> None:
    assert {kind.value for kind in ReferenceKind} == {
        "style", "character_identity", "character_state", "scene_base",
        "scene_state", "prop", "previous_shot", "first_frame", "last_frame",
    }


def test_default_plan_assembles_style_cast_scene_and_story_props() -> None:
    candidates = [
        _candidate("style", ReferenceKind.STYLE, 10),
        _candidate("hero", ReferenceKind.CHARACTER_STATE, 20),
        _candidate("room", ReferenceKind.SCENE_STATE, 30),
        _candidate("letter", ReferenceKind.PROP, 40),
    ]
    plan = plan_references(candidates, capability_profile=_profile(4))
    assert [item.reference_id for item in plan.selected] == ["style", "hero", "room", "letter"]
    assert plan.excluded == ()


def test_user_exclusion_is_sticky_and_never_reintroduced_downstream() -> None:
    candidates = [
        _candidate("style", ReferenceKind.STYLE, 10),
        _candidate("hero", ReferenceKind.CHARACTER_IDENTITY, 20),
    ]
    plan = plan_references(candidates, capability_profile=_profile(2), excluded_by_user={"hero"})
    assert [item.reference_id for item in plan.selected] == ["style"]
    assert [(item.reference.reference_id, item.reason) for item in plan.excluded] == [
        ("hero", "excluded_by_user")
    ]
    assert plan.excluded_by_user == frozenset({"hero"})


def test_capacity_exclusion_is_explicit_stable_and_profile_driven() -> None:
    candidates = [
        _candidate("scene", ReferenceKind.SCENE_BASE, 30),
        _candidate("first", ReferenceKind.FIRST_FRAME, 1),
        _candidate("hero", ReferenceKind.CHARACTER_STATE, 20),
    ]
    plan = plan_references(candidates, capability_profile=_profile(2))
    assert [item.reference_id for item in plan.selected] == ["first", "hero"]
    exclusion = plan.excluded[0]
    assert exclusion.reference.reference_id == "scene"
    assert exclusion.reason == "capacity_exceeded"
    assert exclusion.priority == 30
    assert plan.capacity == 2


def test_plan_summary_can_be_frozen_on_generation_attempt() -> None:
    plan = plan_references(
        [
            _candidate("first", ReferenceKind.FIRST_FRAME, 1),
            _candidate("last", ReferenceKind.LAST_FRAME, 2),
        ],
        capability_profile=_profile(2),
    )
    attempt = GenerationAttempt(
        attempt_id="attempt-1",
        slot_id="group:g1:video",
        reference_summary=plan.to_summary(),
    )
    assert attempt.reference_summary["capacity"] == 2
    assert [item["reference_id"] for item in attempt.reference_summary["selected"]] == ["first", "last"]
