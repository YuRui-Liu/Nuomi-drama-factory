from __future__ import annotations

import pytest

from novelvideo.media_capabilities.models import (
    CapabilityProfile,
    CapabilityRequirement,
    MediaCapability,
    WorkflowProfile,
)
from novelvideo.media_capabilities.video.h3_routing import (
    H3_IMPLEMENTATION_ID,
    H3PreflightError,
    H3RouteCandidate,
    resolve_narrative_group_h3_route,
)
from novelvideo.production_workflow.models import GenerationAttempt


def _capability_profile(
    profile_id: str = H3_IMPLEMENTATION_ID, **updates: object
) -> CapabilityProfile:
    values: dict[str, object] = {
        "id": profile_id,
        "media_type": "video",
        "capabilities": [
            MediaCapability.VIDEO_I2VA,
            MediaCapability.VIDEO_FL2VA,
        ],
        "modes": ["i2va", "fl2va"],
        "max_references": 2,
        "aspect_ratios": ["9:16", "16:9"],
        "resolutions": ["720p", "1080p"],
        "min_duration": 1,
        "max_duration": 15,
        "supports_dialogue": True,
        "supports_audio": True,
    }
    values.update(updates)
    return CapabilityProfile.model_validate(values)


def _workflow(
    *,
    workflow_id: str = "2089723723468328961",
    workflow_revision: str = "director-v5",
    api_schema_sha256: str = "a" * 64,
    health_status: str = "healthy",
    provider: str = "runninghub",
) -> WorkflowProfile:
    return WorkflowProfile(
        id="minimax-h3-video",
        version=2,
        provider=provider,
        workflow_id=workflow_id,
        workflow_revision=workflow_revision,
        api_schema_sha256=api_schema_sha256,
        capabilities=[
            MediaCapability.VIDEO_I2VA,
            MediaCapability.VIDEO_FL2VA,
        ],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        outputs={"video": {"node_id": "7", "media_type": "video"}},
        health_status=health_status,
    )


def _candidate(
    implementation_id: str = H3_IMPLEMENTATION_ID,
    *,
    capability_profile: CapabilityProfile | None = None,
    approved_workflow: WorkflowProfile | None = None,
    runtime_workflow: WorkflowProfile | None = None,
) -> H3RouteCandidate:
    approved = approved_workflow or _workflow()
    return H3RouteCandidate(
        implementation_id=implementation_id,
        capability_profile=capability_profile or _capability_profile(implementation_id),
        approved_workflow=approved,
        runtime_workflow=runtime_workflow or approved,
    )


def _requirement(**updates: object) -> CapabilityRequirement:
    values: dict[str, object] = {
        "capability": MediaCapability.VIDEO_I2VA,
        "mode": "i2va",
        "reference_count": 1,
        "aspect_ratio": "9:16",
        "resolution": "1080p",
        "duration": 10,
        "requires_dialogue": False,
        "requires_audio": True,
    }
    values.update(updates)
    return CapabilityRequirement.model_validate(values)


def test_narrative_group_default_resolves_only_to_runninghub_minimax_h3() -> None:
    decision = resolve_narrative_group_h3_route(
        requirement=_requirement(),
        recommended_mode="i2va",
        primary=_candidate(),
    )

    assert decision.implementation_id == H3_IMPLEMENTATION_ID
    assert decision.mode == "i2va"
    assert decision.capability is MediaCapability.VIDEO_I2VA
    assert decision.used_approved_fallback is False


def test_one_time_user_mode_overrides_i2va_fl2va_recommendation() -> None:
    decision = resolve_narrative_group_h3_route(
        requirement=_requirement(reference_count=2),
        recommended_mode="i2va",
        one_time_mode="fl2va",
        primary=_candidate(),
    )

    assert decision.mode == "fl2va"
    assert decision.capability is MediaCapability.VIDEO_FL2VA


def test_fl2va_override_requires_both_first_and_last_frame_references() -> None:
    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(reference_count=1),
            recommended_mode="i2va",
            one_time_mode="fl2va",
            primary=_candidate(),
        )

    assert captured.value.field_errors[0].field == "reference_count"
    assert captured.value.field_errors[0].code == "h3.fl2va_frames_required"


@pytest.mark.parametrize(
    ("runtime_updates", "field"),
    [
        ({"workflow_id": "999"}, "workflow_id"),
        ({"workflow_revision": "director-v6"}, "workflow_revision"),
        ({"api_schema_sha256": "b" * 64}, "api_schema_sha256"),
    ],
)
def test_workflow_identity_drift_fails_before_routing(
    runtime_updates: dict[str, str], field: str
) -> None:
    approved = _workflow()
    runtime = _workflow(**runtime_updates)

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(
                approved_workflow=approved,
                runtime_workflow=runtime,
            ),
        )

    assert captured.value.summary == "MiniMax H3 工作流预检失败"
    assert [error.field for error in captured.value.field_errors] == [field]
    assert captured.value.workflow_revision == runtime.workflow_revision


@pytest.mark.parametrize(
    "field",
    ["workflow_id", "workflow_revision", "api_schema_sha256"],
)
def test_unpinned_workflow_identity_fails_before_routing(field: str) -> None:
    workflow_values = {field: ""}
    unpinned = _workflow(**workflow_values)

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(
                approved_workflow=unpinned,
                runtime_workflow=unpinned,
            ),
        )

    assert [error.field for error in captured.value.field_errors] == [field]
    assert captured.value.field_errors[0].code == f"h3.{field}_required"


def test_dialogue_intent_without_dialogue_returns_field_level_error() -> None:
    attempt = GenerationAttempt(attempt_id="attempt-1", slot_id="group:g1:video")

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(requires_dialogue=True),
            recommended_mode="i2va",
            dialogue="   ",
            primary=_candidate(),
            generation_attempt=attempt,
        )

    error = captured.value
    assert error.field_errors[0].field == "dialogue"
    assert error.field_errors[0].code == "h3.dialogue_required"
    assert error.generation_attempt is attempt


def test_unsupported_aspect_is_blocked_without_provider_default_substitution() -> None:
    requirement = _requirement(aspect_ratio="1:1")

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=requirement,
            recommended_mode="i2va",
            primary=_candidate(),
        )

    assert captured.value.field_errors[0].field == "aspect_ratio"
    assert captured.value.field_errors[0].code == "h3.aspect_ratio_unsupported"
    assert requirement.aspect_ratio == "1:1"


def test_unavailable_h3_without_approved_equivalent_stops() -> None:
    unavailable = _workflow(health_status="unavailable")

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(
                approved_workflow=_workflow(),
                runtime_workflow=unavailable,
            ),
        )

    assert captured.value.field_errors[0].code == "h3.no_approved_equivalent"


def test_unavailable_h3_may_use_explicitly_approved_equivalent() -> None:
    unavailable = _workflow(health_status="unavailable")
    fallback = _candidate(
        "runninghub:minimax-h3-secondary",
        approved_workflow=_workflow(workflow_id="3001"),
        runtime_workflow=_workflow(workflow_id="3001"),
    )

    decision = resolve_narrative_group_h3_route(
        requirement=_requirement(),
        recommended_mode="i2va",
        primary=_candidate(
            approved_workflow=_workflow(),
            runtime_workflow=unavailable,
        ),
        approved_equivalents=(fallback,),
    )

    assert decision.implementation_id == "runninghub:minimax-h3-secondary"
    assert decision.used_approved_fallback is True


def test_ordinary_i2v_is_not_an_equivalent_h3_fallback() -> None:
    unavailable = _workflow(health_status="unavailable")
    ordinary_i2v = _candidate(
        "provider:ordinary-i2v",
        capability_profile=_capability_profile(
            "provider:ordinary-i2v",
            capabilities=[MediaCapability.VIDEO_I2VA],
            modes=["i2va"],
            max_references=1,
            supports_dialogue=False,
            supports_audio=False,
        ),
        approved_workflow=_workflow(workflow_id="4001", provider="provider"),
        runtime_workflow=_workflow(workflow_id="4001", provider="provider"),
    )

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(
                approved_workflow=_workflow(),
                runtime_workflow=unavailable,
            ),
            approved_equivalents=(ordinary_i2v,),
        )

    assert captured.value.field_errors[0].code == "h3.no_approved_equivalent"


def test_non_h3_primary_is_rejected_instead_of_becoming_the_default() -> None:
    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate("provider:ordinary-i2v"),
        )

    assert captured.value.field_errors[0].code == "h3.primary_route_required"


def test_h3_label_cannot_hide_a_non_runninghub_workflow() -> None:
    foreign = _workflow(provider="other-provider")

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(
                approved_workflow=foreign,
                runtime_workflow=foreign,
            ),
        )

    assert captured.value.field_errors[0].field == "provider"
    assert captured.value.field_errors[0].code == "h3.provider_required"


def test_h3_id_cannot_hide_an_ordinary_i2v_primary_profile() -> None:
    ordinary_i2v_profile = _capability_profile(
        capabilities=[MediaCapability.VIDEO_I2VA],
        modes=["i2va"],
        max_references=1,
    )

    with pytest.raises(H3PreflightError) as captured:
        resolve_narrative_group_h3_route(
            requirement=_requirement(),
            recommended_mode="i2va",
            primary=_candidate(capability_profile=ordinary_i2v_profile),
        )

    assert captured.value.field_errors[0].code == "h3.primary_not_equivalent"
