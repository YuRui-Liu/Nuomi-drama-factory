import pytest

from novelvideo.media_capabilities.models import (
    CapabilityProfile,
    CapabilityRequirement,
    MediaCapability,
    RoutingPolicy,
    WorkflowProfile,
)
from novelvideo.media_capabilities.resolver import ConfigurationError, resolve_policy_route


def _video_profile(profile_id: str, **updates) -> CapabilityProfile:
    payload = {
        "id": profile_id,
        "media_type": "video",
        "capabilities": [MediaCapability.VIDEO_I2VA, MediaCapability.VIDEO_FL2VA],
        "modes": ["i2va", "fl2va"],
        "max_references": 2,
        "aspect_ratios": ["9:16", "16:9"],
        "resolutions": ["720p", "1080p"],
        "min_duration": 1,
        "max_duration": 15,
        "supports_dialogue": True,
        "supports_audio": True,
    }
    payload.update(updates)
    return CapabilityProfile(**payload)


def test_capability_profile_describes_and_checks_hard_video_requirements():
    profile = _video_profile("runninghub:minimax-h3")
    requirement = CapabilityRequirement(
        capability=MediaCapability.VIDEO_FL2VA,
        mode="fl2va",
        reference_count=2,
        aspect_ratio="9:16",
        resolution="1080p",
        duration=10,
        requires_dialogue=True,
        requires_audio=True,
    )

    assert profile.satisfies(requirement)
    assert not profile.satisfies(requirement.model_copy(update={"aspect_ratio": "1:1"}))
    assert not profile.satisfies(requirement.model_copy(update={"reference_count": 3}))


def test_workflow_profile_freezes_provider_revision_schema_and_health():
    profile = WorkflowProfile(
        id="minimax-h3-video",
        version=3,
        provider="runninghub",
        workflow_id="2089723723468328961",
        workflow_revision="director-v5",
        api_schema_sha256="a" * 64,
        capabilities=[MediaCapability.VIDEO_I2VA, MediaCapability.VIDEO_FL2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        outputs={"video": {"node_id": "7", "media_type": "video"}},
        health_status="healthy",
    )

    restored = WorkflowProfile.model_validate_json(profile.model_dump_json())
    assert restored.provider == "runninghub"
    assert restored.workflow_revision == "director-v5"
    assert restored.health_status == "healthy"


def test_routing_priority_is_user_then_project_then_stage_then_approved_fallback():
    profiles = {
        name: _video_profile(name)
        for name in ("user", "project", "stage", "fallback")
    }
    policy = RoutingPolicy(
        capability=MediaCapability.VIDEO_FL2VA,
        default_implementation="stage",
        fallback_chain=["fallback"],
    )
    route = resolve_policy_route(
        policy,
        user_selection="user",
        project_default="project",
        profiles=profiles,
        requirement=CapabilityRequirement(
            capability=MediaCapability.VIDEO_FL2VA,
            mode="fl2va",
            aspect_ratio="9:16",
            requires_dialogue=True,
            requires_audio=True,
        ),
    )
    assert route == ("user", "project", "stage", "fallback")


@pytest.mark.parametrize(
    "updates",
    [
        {"capabilities": [MediaCapability.VIDEO_I2VA], "modes": ["i2va"]},
        {"supports_dialogue": False},
        {"supports_audio": False},
        {"aspect_ratios": ["16:9"]},
    ],
)
def test_incompatible_h3_fallback_is_not_treated_as_equivalent(updates):
    policy = RoutingPolicy(
        capability=MediaCapability.VIDEO_FL2VA,
        default_implementation="missing-primary",
        fallback_chain=["candidate"],
    )
    requirement = CapabilityRequirement(
        capability=MediaCapability.VIDEO_FL2VA,
        mode="fl2va",
        aspect_ratio="9:16",
        requires_dialogue=True,
        requires_audio=True,
    )

    with pytest.raises(ConfigurationError, match="equivalent implementation"):
        resolve_policy_route(
            policy,
            profiles={"candidate": _video_profile("candidate", **updates)},
            requirement=requirement,
        )
