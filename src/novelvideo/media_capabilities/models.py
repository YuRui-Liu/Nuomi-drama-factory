from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    JsonValue,
    PositiveInt,
    StringConstraints,
    model_validator,
)


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
CredentialReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^(env|keyring|secret)://[^\s]+$",
    ),
]

DEFAULT_GRSAI_IMAGE_MODEL = "gpt-image-2"
GRSAI_IMAGE_MODELS = frozenset(
    {
        "gpt-image-2",
        "gpt-image-2-vip",
        "nano-banana",
        "nano-banana-fast",
        "nano-banana-2",
        "nano-banana-2-cl",
        "nano-banana-2-2k-cl",
        "nano-banana-2-4k-cl",
        "nano-banana-pro",
        "nano-banana-pro-vt",
        "nano-banana-pro-cl",
        "nano-banana-pro-vip",
        "nano-banana-pro-4k-vip",
    }
)


class MediaCapability(StrEnum):
    IMAGE_STORYBOARD_GRID = "image.storyboard_grid"
    IMAGE_SINGLE = "image.single"
    IMAGE_GRID_UPSCALE_SPLIT = "image.grid_upscale_split"
    VIDEO_T2VA = "video.t2va"
    VIDEO_I2VA = "video.i2va"
    VIDEO_L2VA = "video.l2va"
    VIDEO_FL2VA = "video.fl2va"
    VIDEO_REF2VA = "video.ref2va"
    TTS_SYNTHESIZE = "tts.synthesize"
    TTS_VOICE_DESIGN = "tts.voice_design"
    TTS_VOICE_CLONE = "tts.voice_clone"


class MediaTaskStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    RUNNING = "running"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    RETRY_WAIT = "retry_wait"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUALITY_FAILED = "quality_failed"
    CANCELLED = "cancelled"


class _ExternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoGenerationRequest(_ExternalModel):
    capability: MediaCapability
    prompt: NonEmptyText
    duration: float = Field(gt=0)
    first_frame: str | None = None
    last_frame: str | None = None
    reference_images: list[str] = Field(default_factory=list)
    aspect_ratio: str = "9:16"
    resolution: str | None = None
    fps: int | None = Field(default=None, gt=0)
    seed: int | None = None
    generate_audio: bool | None = None

    @model_validator(mode="after")
    def validate_capability_and_frames(self) -> Self:
        if not self.capability.value.startswith("video."):
            raise ValueError("capability must be a video capability")
        if self.capability == MediaCapability.VIDEO_T2VA:
            if self.first_frame or self.last_frame or self.reference_images:
                raise ValueError("video.t2va forbids frames and reference_images")
        if self.capability == MediaCapability.VIDEO_I2VA:
            if not self.first_frame:
                raise ValueError("video.i2va requires first_frame")
            if self.last_frame or self.reference_images:
                raise ValueError("video.i2va only allows first_frame")
        if self.capability == MediaCapability.VIDEO_L2VA:
            if not self.last_frame:
                raise ValueError("video.l2va requires last_frame")
            if self.first_frame or self.reference_images:
                raise ValueError("video.l2va only allows last_frame")
        if self.capability == MediaCapability.VIDEO_FL2VA:
            if not self.first_frame or not self.last_frame:
                raise ValueError("video.fl2va requires first_frame and last_frame")
            if self.reference_images:
                raise ValueError("video.fl2va forbids reference_images")
        if self.capability == MediaCapability.VIDEO_REF2VA:
            if self.first_frame or self.last_frame:
                raise ValueError("video.ref2va forbids first_frame and last_frame")
            if not self.reference_images:
                raise ValueError("video.ref2va requires non-empty reference_images")
        return self


class ImageGenerationRequest(_ExternalModel):
    capability: MediaCapability
    prompt: NonEmptyText
    model: str | None = None
    references: list[str] = Field(default_factory=list)
    aspect_ratio: str | None = None
    image_size: str | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def validate_capability(self) -> Self:
        if not self.capability.value.startswith("image."):
            raise ValueError("capability must be an image capability")
        return self


class SpeechGenerationRequest(_ExternalModel):
    capability: MediaCapability
    text: NonEmptyText
    language: str | None = None
    speaker_id: str | None = None
    emotion: str | None = None
    voice_style: str | None = None
    reference_audio: str | None = None
    speed: float = Field(default=1.0, gt=0)
    volume: float = Field(default=1.0, gt=0)
    sample_rate: int | None = Field(default=None, gt=0)
    output_format: str | None = None

    @model_validator(mode="after")
    def validate_capability_and_reference(self) -> Self:
        if not self.capability.value.startswith("tts."):
            raise ValueError("capability must be a tts capability")
        if self.capability == MediaCapability.TTS_VOICE_CLONE and not self.reference_audio:
            raise ValueError("tts.voice_clone requires reference_audio")
        return self


class ProviderAccount(_ExternalModel):
    id: str
    provider_type: str
    base_url: str | None = None
    model: str | None = None
    credential_ref: CredentialReference
    enabled: bool = True
    max_concurrency: int = Field(default=1, gt=0)
    poll_concurrency: int = Field(default=1, gt=0)
    queue_limit: int = Field(default=1, gt=0)
    capability_limits: dict[str, PositiveInt] = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def validate_grsai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized not in GRSAI_IMAGE_MODELS:
            raise ValueError("model must be a documented GRSAI image model")
        return normalized


class RunningHubWorkflowSettings(_ExternalModel):
    """Remote workflow IDs for the RunningHub capabilities shipped by CE."""

    image_upscale: str = ""
    video_minimax_h3: str = "2087934731806658562"
    tts_qwen3_voice_design: str = ""
    tts_indextts2_voice_clone: str = ""

    @field_validator(
        "image_upscale",
        "video_minimax_h3",
        "tts_qwen3_voice_design",
        "tts_indextts2_voice_clone",
        mode="before",
    )
    @classmethod
    def validate_workflow_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("workflow ID must be a string")
        normalized = value.strip()
        if normalized and not normalized.isdecimal():
            raise ValueError("workflow ID must contain digits only")
        return normalized


class WorkflowProfile(_ExternalModel):
    id: str
    version: int = Field(ge=1)
    workflow_id: str
    capabilities: list[MediaCapability] = Field(default_factory=list)
    bindings: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: dict[str, JsonValue] = Field(default_factory=dict)
    constraints: dict[str, JsonValue] = Field(default_factory=dict)
    source_sha256: str = ""
    status: str = "active"


class CapabilityImplementation(_ExternalModel):
    id: str
    capability: MediaCapability
    provider_account: str
    workflow_profile: str | None = None
    prompt_profile: str | None = None


class RoutingPolicy(_ExternalModel):
    capability: MediaCapability
    default_implementation: str
    fallback_chain: list[str] = Field(default_factory=list)
    concurrency_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_fallbacks(self) -> Self:
        if self.default_implementation in self.fallback_chain:
            raise ValueError("fallback_chain must not repeat the default implementation")
        if len(self.fallback_chain) != len(set(self.fallback_chain)):
            raise ValueError("fallback_chain must not contain duplicates")
        return self


class MediaArtifact(_ExternalModel):
    id: str
    media_type: str
    local_path: str
    content_sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)
