"""Pure domain routing and preflight rules for narrative-group MiniMax H3 video."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from novelvideo.media_capabilities.models import (
    CapabilityProfile,
    CapabilityRequirement,
    MediaCapability,
    WorkflowProfile,
)
from novelvideo.production_workflow.models import GenerationAttempt


H3_IMPLEMENTATION_ID = "runninghub:minimax-h3"
H3_SUPPORTED_ASPECT_RATIOS = frozenset({"9:16", "16:9"})
H3Mode = Literal["i2va", "fl2va"]


@dataclass(frozen=True, slots=True)
class H3FieldError:
    field: str
    code: str
    message: str


class H3PreflightError(ValueError):
    """A user-actionable H3 preflight failure raised before queueing."""

    def __init__(
        self,
        summary: str,
        field_errors: Sequence[H3FieldError],
        *,
        workflow_revision: str,
        generation_attempt: GenerationAttempt | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.field_errors = tuple(field_errors)
        self.workflow_revision = workflow_revision
        self.generation_attempt = generation_attempt


@dataclass(frozen=True, slots=True)
class H3RouteCandidate:
    """A runtime route paired with the workflow identity approved for it."""

    implementation_id: str
    capability_profile: CapabilityProfile
    approved_workflow: WorkflowProfile
    runtime_workflow: WorkflowProfile


@dataclass(frozen=True, slots=True)
class H3RoutingDecision:
    implementation_id: str
    mode: H3Mode
    capability: MediaCapability
    requirement: CapabilityRequirement
    capability_profile: CapabilityProfile
    workflow_profile: WorkflowProfile
    used_approved_fallback: bool


_MODE_CAPABILITIES: dict[H3Mode, MediaCapability] = {
    "i2va": MediaCapability.VIDEO_I2VA,
    "fl2va": MediaCapability.VIDEO_FL2VA,
}


def resolve_narrative_group_h3_route(
    *,
    requirement: CapabilityRequirement,
    recommended_mode: str,
    primary: H3RouteCandidate,
    one_time_mode: str | None = None,
    dialogue: str | None = None,
    approved_equivalents: Sequence[H3RouteCandidate] = (),
    generation_attempt: GenerationAttempt | None = None,
) -> H3RoutingDecision:
    """Resolve and preflight the frozen narrative-group H3 route.

    ``approved_equivalents`` is the complete fallback allowlist. Candidates not
    present in that sequence cannot be selected, even when technically available.
    """
    revision = primary.runtime_workflow.workflow_revision
    if primary.implementation_id != H3_IMPLEMENTATION_ID:
        _fail(
            "MiniMax H3 路由配置错误",
            "route",
            "h3.primary_route_required",
            "叙事组视频默认路由必须是 RunningHub MiniMax H3。",
            revision,
            generation_attempt,
        )

    mode = _resolve_mode(one_time_mode or recommended_mode, revision, generation_attempt)
    selected_requirement = requirement.model_copy(
        update={"mode": mode, "capability": _MODE_CAPABILITIES[mode]}
    )

    if mode == "fl2va" and selected_requirement.reference_count < 2:
        _fail(
            "MiniMax H3 参数预检失败",
            "reference_count",
            "h3.fl2va_frames_required",
            "首尾帧模式必须同时提供首帧和尾帧参考。",
            revision,
            generation_attempt,
        )
    if selected_requirement.aspect_ratio not in H3_SUPPORTED_ASPECT_RATIOS:
        _fail(
            "MiniMax H3 参数预检失败",
            "aspect_ratio",
            "h3.aspect_ratio_unsupported",
            "MiniMax H3 仅支持 9:16 或 16:9，不能改用供应商默认画幅。",
            revision,
            generation_attempt,
        )
    if selected_requirement.requires_dialogue and not str(dialogue or "").strip():
        _fail(
            "MiniMax H3 参数预检失败",
            "dialogue",
            "h3.dialogue_required",
            "已声明对白意图，但对白字段为空。",
            revision,
            generation_attempt,
        )
    if _is_available(primary.runtime_workflow) and not _is_h3_route_equivalent(
        primary, selected_requirement
    ):
        _fail(
            "MiniMax H3 能力预检失败",
            "route",
            "h3.primary_not_equivalent",
            "默认 H3 配置不具备完整 i2va/fl2va、对白、音频和画幅能力。",
            revision,
            generation_attempt,
        )

    candidate = primary
    used_fallback = False
    if not _is_available(primary.runtime_workflow):
        candidate = _first_equivalent(
            approved_equivalents,
            selected_requirement,
        )
        if candidate is None:
            _fail(
                "MiniMax H3 当前不可用",
                "route",
                "h3.no_approved_equivalent",
                "没有可用且能力等价的预批准 H3 配置，已停止排队。",
                revision,
                generation_attempt,
            )
        used_fallback = True

    _validate_workflow_identity(candidate, generation_attempt)
    _validate_candidate(candidate, selected_requirement, generation_attempt)
    return H3RoutingDecision(
        implementation_id=candidate.implementation_id,
        mode=mode,
        capability=selected_requirement.capability,
        requirement=selected_requirement,
        capability_profile=candidate.capability_profile,
        workflow_profile=candidate.runtime_workflow,
        used_approved_fallback=used_fallback,
    )


def _resolve_mode(
    value: str,
    workflow_revision: str,
    generation_attempt: GenerationAttempt | None,
) -> H3Mode:
    if value == "i2va" or value == "fl2va":
        return value
    _fail(
        "MiniMax H3 参数预检失败",
        "mode",
        "h3.mode_unsupported",
        "MiniMax H3 模式只能是 i2va 或 fl2va。",
        workflow_revision,
        generation_attempt,
    )


def _is_available(workflow: WorkflowProfile) -> bool:
    return workflow.status == "active" and workflow.health_status != "unavailable"


def _first_equivalent(
    candidates: Sequence[H3RouteCandidate],
    requirement: CapabilityRequirement,
) -> H3RouteCandidate | None:
    for candidate in candidates:
        if not _is_available(candidate.runtime_workflow):
            continue
        if _is_h3_route_equivalent(candidate, requirement):
            return candidate
    return None


def _is_h3_route_equivalent(
    candidate: H3RouteCandidate,
    requirement: CapabilityRequirement,
) -> bool:
    required_capabilities = {
        MediaCapability.VIDEO_I2VA,
        MediaCapability.VIDEO_FL2VA,
    }
    return required_capabilities.issubset(candidate.runtime_workflow.capabilities) and (
        _is_h3_equivalent(candidate.capability_profile, requirement)
    )


def _is_h3_equivalent(
    profile: CapabilityProfile,
    requirement: CapabilityRequirement,
) -> bool:
    for mode, capability, reference_count in (
        ("i2va", MediaCapability.VIDEO_I2VA, 1),
        ("fl2va", MediaCapability.VIDEO_FL2VA, 2),
    ):
        equivalent_requirement = requirement.model_copy(
            update={
                "capability": capability,
                "mode": mode,
                "reference_count": reference_count,
                "requires_dialogue": True,
                "requires_audio": True,
            }
        )
        if not profile.satisfies(equivalent_requirement):
            return False
    return True


def _validate_workflow_identity(
    candidate: H3RouteCandidate,
    generation_attempt: GenerationAttempt | None,
) -> None:
    approved = candidate.approved_workflow
    runtime = candidate.runtime_workflow
    errors: list[H3FieldError] = []
    if approved.provider != "runninghub" or runtime.provider != "runninghub":
        errors.append(
            H3FieldError(
                field="provider",
                code="h3.provider_required",
                message="MiniMax H3 核心视频路由必须使用 RunningHub 工作流。",
            )
        )
    for field in ("workflow_id", "workflow_revision", "api_schema_sha256"):
        approved_value = getattr(approved, field)
        runtime_value = getattr(runtime, field)
        if not approved_value.strip() or not runtime_value.strip():
            errors.append(
                H3FieldError(
                    field=field,
                    code=f"h3.{field}_required",
                    message=f"预批准和运行时工作流都必须提供 {field}。",
                )
            )
        elif runtime_value != approved_value:
            errors.append(
                H3FieldError(
                    field=field,
                    code=f"h3.{field}_mismatch",
                    message=f"运行时 {field} 与预批准值不一致。",
                )
            )
    if errors:
        raise H3PreflightError(
            "MiniMax H3 工作流预检失败",
            errors,
            workflow_revision=runtime.workflow_revision,
            generation_attempt=generation_attempt,
        )


def _validate_candidate(
    candidate: H3RouteCandidate,
    requirement: CapabilityRequirement,
    generation_attempt: GenerationAttempt | None,
) -> None:
    errors = list(_capability_errors(candidate.capability_profile, requirement))
    if requirement.capability not in candidate.runtime_workflow.capabilities:
        errors.append(
            H3FieldError(
                field="capability",
                code="h3.workflow_capability_unsupported",
                message="运行时工作流未声明所选 H3 能力。",
            )
        )
    if errors:
        raise H3PreflightError(
            "MiniMax H3 能力预检失败",
            errors,
            workflow_revision=candidate.runtime_workflow.workflow_revision,
            generation_attempt=generation_attempt,
        )


def _capability_errors(
    profile: CapabilityProfile,
    requirement: CapabilityRequirement,
) -> tuple[H3FieldError, ...]:
    checks = (
        (
            requirement.capability not in profile.capabilities,
            "capability",
            "h3.capability_unsupported",
            "候选配置不支持所需视频能力。",
        ),
        (
            bool(requirement.mode and requirement.mode not in profile.modes),
            "mode",
            "h3.mode_unsupported",
            "候选配置不支持所选 H3 模式。",
        ),
        (
            requirement.reference_count > profile.max_references,
            "reference_count",
            "h3.reference_count_unsupported",
            "候选配置的参考图额度不足。",
        ),
        (
            bool(
                requirement.aspect_ratio
                and requirement.aspect_ratio not in profile.aspect_ratios
            ),
            "aspect_ratio",
            "h3.aspect_ratio_unsupported",
            "候选配置不支持请求画幅。",
        ),
        (
            bool(requirement.resolution and requirement.resolution not in profile.resolutions),
            "resolution",
            "h3.resolution_unsupported",
            "候选配置不支持请求分辨率。",
        ),
        (
            bool(
                requirement.duration is not None
                and (
                    (
                        profile.min_duration is not None
                        and requirement.duration < profile.min_duration
                    )
                    or (
                        profile.max_duration is not None
                        and requirement.duration > profile.max_duration
                    )
                )
            ),
            "duration",
            "h3.duration_unsupported",
            "候选配置不支持请求时长。",
        ),
        (
            requirement.requires_dialogue and not profile.supports_dialogue,
            "dialogue",
            "h3.dialogue_unsupported",
            "候选配置不支持对白。",
        ),
        (
            requirement.requires_audio and not profile.supports_audio,
            "audio",
            "h3.audio_unsupported",
            "候选配置不支持音频。",
        ),
    )
    return tuple(
        H3FieldError(field=field, code=code, message=message)
        for failed, field, code, message in checks
        if failed
    )


def _fail(
    summary: str,
    field: str,
    code: str,
    message: str,
    workflow_revision: str,
    generation_attempt: GenerationAttempt | None,
) -> None:
    raise H3PreflightError(
        summary,
        (H3FieldError(field=field, code=code, message=message),),
        workflow_revision=workflow_revision,
        generation_attempt=generation_attempt,
    )


__all__ = [
    "H3_IMPLEMENTATION_ID",
    "H3_SUPPORTED_ASPECT_RATIOS",
    "H3FieldError",
    "H3PreflightError",
    "H3RouteCandidate",
    "H3RoutingDecision",
    "resolve_narrative_group_h3_route",
]
