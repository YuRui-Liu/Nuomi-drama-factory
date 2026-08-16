from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities import (
    CapabilityImplementation,
    ImageGenerationRequest,
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    ProviderAccount,
    RoutingPolicy,
    SpeechGenerationRequest,
    VideoGenerationRequest,
    WorkflowProfile,
)


@pytest.mark.parametrize(
    ("capability", "frames"),
    [
        (MediaCapability.VIDEO_I2VA, {}),
        (MediaCapability.VIDEO_L2VA, {}),
        (MediaCapability.VIDEO_FL2VA, {"first_frame": "first.png"}),
        (MediaCapability.VIDEO_FL2VA, {"last_frame": "last.png"}),
    ],
)
def test_video_request_requires_capability_frames(
    capability: MediaCapability, frames: dict[str, str]
) -> None:
    with pytest.raises(ValidationError):
        VideoGenerationRequest(
            capability=capability,
            prompt="move",
            duration=5,
            **frames,
        )


@pytest.mark.parametrize(
    ("capability", "frames"),
    [
        (MediaCapability.VIDEO_I2VA, {"first_frame": "first.png"}),
        (MediaCapability.VIDEO_L2VA, {"last_frame": "last.png"}),
        (MediaCapability.VIDEO_T2VA, {}),
        (MediaCapability.VIDEO_REF2VA, {"reference_images": ["reference.png"]}),
    ],
)
def test_video_request_accepts_valid_frames_for_mode(
    capability: MediaCapability, frames: dict[str, object]
) -> None:
    request = VideoGenerationRequest(
        capability=capability,
        prompt="move",
        duration=5,
        **frames,
    )

    assert request.capability is capability


@pytest.mark.parametrize(
    ("capability", "inputs", "error"),
    [
        (
            MediaCapability.VIDEO_T2VA,
            {"first_frame": "first.png"},
            "video.t2va forbids frames and reference_images",
        ),
        (
            MediaCapability.VIDEO_T2VA,
            {"reference_images": ["reference.png"]},
            "video.t2va forbids frames and reference_images",
        ),
        (
            MediaCapability.VIDEO_I2VA,
            {"first_frame": "first.png", "last_frame": "last.png"},
            "video.i2va only allows first_frame",
        ),
        (
            MediaCapability.VIDEO_L2VA,
            {"first_frame": "first.png", "last_frame": "last.png"},
            "video.l2va only allows last_frame",
        ),
        (
            MediaCapability.VIDEO_FL2VA,
            {
                "first_frame": "first.png",
                "last_frame": "last.png",
                "reference_images": ["reference.png"],
            },
            "video.fl2va forbids reference_images",
        ),
        (
            MediaCapability.VIDEO_REF2VA,
            {},
            "video.ref2va requires non-empty reference_images",
        ),
        (
            MediaCapability.VIDEO_REF2VA,
            {"first_frame": "first.png", "reference_images": ["reference.png"]},
            "video.ref2va forbids first_frame and last_frame",
        ),
    ],
)
def test_video_request_rejects_inputs_for_other_modes(
    capability: MediaCapability,
    inputs: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        VideoGenerationRequest(
            capability=capability,
            prompt="move",
            duration=5,
            **inputs,
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            VideoGenerationRequest,
            {
                "capability": MediaCapability.IMAGE_SINGLE,
                "prompt": "portrait",
                "duration": 5,
            },
        ),
        (
            ImageGenerationRequest,
            {"capability": MediaCapability.VIDEO_T2VA, "prompt": "move"},
        ),
        (
            SpeechGenerationRequest,
            {"capability": MediaCapability.IMAGE_SINGLE, "text": "hello"},
        ),
    ],
)
def test_request_rejects_capability_from_another_media_category(
    model: type, payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_voice_clone_requires_reference_audio() -> None:
    with pytest.raises(ValidationError):
        SpeechGenerationRequest(
            capability=MediaCapability.TTS_VOICE_CLONE,
            text="hello",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "", "duration": 5},
        {"prompt": "move", "duration": 0},
        {"prompt": "move", "duration": 5, "fps": 0},
    ],
)
def test_video_request_rejects_invalid_generation_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        VideoGenerationRequest(
            capability=MediaCapability.VIDEO_T2VA,
            **payload,
        )


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (
            VideoGenerationRequest,
            {
                "capability": MediaCapability.VIDEO_T2VA,
                "prompt": "   ",
                "duration": 5,
            },
            "prompt",
        ),
        (
            ImageGenerationRequest,
            {"capability": MediaCapability.IMAGE_SINGLE, "prompt": "   "},
            "prompt",
        ),
        (
            SpeechGenerationRequest,
            {"capability": MediaCapability.TTS_SYNTHESIZE, "text": "   "},
            "text",
        ),
    ],
)
def test_generation_text_rejects_whitespace_only_values(
    model: type, payload: dict[str, object], field: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        model.model_validate(payload)


def test_generation_text_is_stripped() -> None:
    video = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_T2VA,
        prompt="  move  ",
        duration=5,
    )
    speech = SpeechGenerationRequest(
        capability=MediaCapability.TTS_SYNTHESIZE,
        text="  hello  ",
    )

    assert video.prompt == "move"
    assert speech.text == "hello"


@pytest.mark.parametrize("field", ["speed", "volume", "sample_rate"])
@pytest.mark.parametrize("value", [0, -1])
def test_speech_numeric_controls_are_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError, match=field):
        SpeechGenerationRequest.model_validate(
            {
                "capability": MediaCapability.TTS_SYNTHESIZE,
                "text": "hello",
                field: value,
            }
        )


def test_provider_account_rejects_inline_api_key() -> None:
    with pytest.raises(ValidationError):
        ProviderAccount(
            id="primary",
            provider_type="example",
            credential_ref="secret://media/primary",
            api_key="not-allowed",
        )


@pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2-vip", "nano-banana-2"])
def test_grsai_provider_accepts_documented_image_models(model: str) -> None:
    account = ProviderAccount(
        id="grsai-main",
        provider_type="grsai",
        credential_ref="secret://grsai-main",
        model=model,
    )
    assert account.model == model


def test_grsai_provider_rejects_unknown_image_model() -> None:
    with pytest.raises(ValidationError, match="model"):
        ProviderAccount(
            id="grsai-main",
            provider_type="grsai",
            credential_ref="secret://grsai-main",
            model="made-up-model",
        )


@pytest.mark.parametrize("field", ["max_concurrency", "poll_concurrency", "queue_limit"])
@pytest.mark.parametrize("value", [0, -1])
def test_provider_account_concurrency_limits_are_positive(field: str, value: int) -> None:
    payload = {
        "id": "primary",
        "provider_type": "example",
        "credential_ref": "secret://media/primary",
        field: value,
    }
    with pytest.raises(ValidationError):
        ProviderAccount.model_validate(payload)


@pytest.mark.parametrize("value", [0, -1])
def test_provider_account_capability_limits_are_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        ProviderAccount(
            id="primary",
            provider_type="example",
            credential_ref="secret://media/primary",
            capability_limits={"video.*": value},
        )


@pytest.mark.parametrize(
    "credential_ref",
    ["env://MEDIA_API_KEY", "keyring://dramaclaw/media", "secret://media/primary"],
)
def test_provider_account_accepts_controlled_credential_references(
    credential_ref: str,
) -> None:
    account = ProviderAccount(
        id="primary",
        provider_type="example",
        credential_ref=credential_ref,
    )

    assert account.credential_ref == credential_ref


@pytest.mark.parametrize(
    "credential_ref",
    ["", "   ", "plain-api-key", "MEDIA_API_KEY", "https://secret.invalid/key"],
)
def test_provider_account_rejects_uncontrolled_credential_references(
    credential_ref: str,
) -> None:
    with pytest.raises(ValidationError, match="credential_ref"):
        ProviderAccount(
            id="primary",
            provider_type="example",
            credential_ref=credential_ref,
        )


def test_workflow_profile_version_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        WorkflowProfile(
            id="storyboard-v0",
            version=0,
            workflow_id="storyboard",
        )


@pytest.mark.parametrize("field", ["bindings", "outputs", "constraints"])
def test_workflow_profile_rejects_non_json_values(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        WorkflowProfile.model_validate(
            {
                "id": "workflow",
                "version": 1,
                "workflow_id": "remote-workflow",
                field: {"invalid": object()},
            }
        )


def test_routing_policy_rejects_default_in_fallbacks() -> None:
    with pytest.raises(ValidationError):
        RoutingPolicy(
            capability=MediaCapability.IMAGE_SINGLE,
            default_implementation="primary",
            fallback_chain=["secondary", "primary"],
        )


def test_routing_policy_rejects_duplicate_fallbacks() -> None:
    with pytest.raises(ValidationError):
        RoutingPolicy(
            capability=MediaCapability.IMAGE_SINGLE,
            default_implementation="primary",
            fallback_chain=["secondary", "secondary"],
        )


@pytest.mark.parametrize("value", [0, -1])
def test_routing_policy_concurrency_limit_is_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        RoutingPolicy(
            capability=MediaCapability.IMAGE_SINGLE,
            default_implementation="primary",
            concurrency_limit=value,
        )


def test_media_capabilities_are_the_exact_public_set() -> None:
    assert set(MediaCapability) == {
        MediaCapability.IMAGE_STORYBOARD_GRID,
        MediaCapability.IMAGE_SINGLE,
        MediaCapability.IMAGE_GRID_UPSCALE_SPLIT,
        MediaCapability.VIDEO_T2VA,
        MediaCapability.VIDEO_I2VA,
        MediaCapability.VIDEO_L2VA,
        MediaCapability.VIDEO_FL2VA,
        MediaCapability.VIDEO_REF2VA,
        MediaCapability.TTS_SYNTHESIZE,
        MediaCapability.TTS_VOICE_DESIGN,
        MediaCapability.TTS_VOICE_CLONE,
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            VideoGenerationRequest,
            {
                "capability": MediaCapability.VIDEO_T2VA,
                "prompt": "move",
                "duration": 5,
            },
        ),
        (
            ImageGenerationRequest,
            {"capability": MediaCapability.IMAGE_SINGLE, "prompt": "portrait"},
        ),
        (
            SpeechGenerationRequest,
            {"capability": MediaCapability.TTS_SYNTHESIZE, "text": "hello"},
        ),
        (
            ProviderAccount,
            {
                "id": "primary",
                "provider_type": "example",
                "credential_ref": "secret://media/primary",
            },
        ),
        (
            WorkflowProfile,
            {"id": "workflow", "version": 1, "workflow_id": "remote-workflow"},
        ),
        (
            CapabilityImplementation,
            {
                "id": "implementation",
                "capability": MediaCapability.IMAGE_SINGLE,
                "provider_account": "primary",
            },
        ),
        (
            RoutingPolicy,
            {
                "capability": MediaCapability.IMAGE_SINGLE,
                "default_implementation": "implementation",
            },
        ),
        (
            MediaArtifact,
            {
                "id": "artifact",
                "media_type": "image",
                "local_path": "artifact.png",
                "content_sha256": "a" * 64,
            },
        ),
    ],
)
def test_external_models_forbid_extra_fields(model: type, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "unexpected": True})


def test_public_models_round_trip() -> None:
    video = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_FL2VA,
        prompt="camera orbit",
        duration=5.0,
        first_frame="first.png",
        last_frame="last.png",
        aspect_ratio="16:9",
        resolution="1080p",
        fps=24,
        seed=7,
        generate_audio=True,
    )
    image = ImageGenerationRequest(
        capability=MediaCapability.IMAGE_STORYBOARD_GRID,
        prompt="four panels",
        references=["character.png"],
        aspect_ratio="16:9",
        image_size="2048x2048",
        seed=9,
    )
    speech = SpeechGenerationRequest(
        capability=MediaCapability.TTS_VOICE_CLONE,
        text="hello",
        language="en",
        speaker_id="narrator",
        emotion="calm",
        voice_style="documentary",
        reference_audio="voice.wav",
        speed=1.0,
        volume=0.8,
        sample_rate=48000,
        output_format="wav",
    )
    provider = ProviderAccount(
        id="primary",
        provider_type="example",
        base_url="https://media.invalid/v1",
        credential_ref="secret://media/primary",
        enabled=True,
        max_concurrency=4,
        poll_concurrency=2,
        queue_limit=20,
        capability_limits={"video.*": 3, MediaCapability.VIDEO_FL2VA: 2},
    )
    workflow = WorkflowProfile(
        id="storyboard-v1",
        version=1,
        workflow_id="storyboard",
        capabilities=[MediaCapability.IMAGE_STORYBOARD_GRID],
        bindings={"prompt": "positive_prompt"},
        outputs={"images": "$.outputs"},
        constraints={"max_references": 4},
        source_sha256="a" * 64,
        status="active",
    )
    implementation = CapabilityImplementation(
        id="primary",
        capability=MediaCapability.IMAGE_STORYBOARD_GRID,
        provider_account="primary",
        workflow_profile="storyboard-v1",
        prompt_profile="storyboard-prompt-v1",
    )
    routing = RoutingPolicy(
        capability=MediaCapability.IMAGE_STORYBOARD_GRID,
        default_implementation="primary",
        fallback_chain=["secondary"],
        concurrency_limit=2,
    )
    artifact = MediaArtifact(
        id="artifact-1",
        media_type="image",
        local_path="artifacts/grid.png",
        content_sha256="b" * 64,
        metadata={"width": 2048},
    )

    for model in (
        video,
        image,
        speech,
        provider,
        workflow,
        implementation,
        routing,
        artifact,
    ):
        assert type(model).model_validate_json(model.model_dump_json()) == model

    assert MediaTaskStatus.QUEUED == "queued"
    assert MediaTaskStatus.QUALITY_FAILED == "quality_failed"
    assert MediaCapability.IMAGE_GRID_UPSCALE_SPLIT == "image.grid_upscale_split"
