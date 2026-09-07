"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    get_media_capability_store,
    get_media_credential_resolver,
    make_sqlite_store_for_context,
    resolve_project_scope,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterError,
    resolve_workflow_parameters,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowScene,
    VideoWorkflowUnavailable,
    build_video_workflow_registry,
)
from novelvideo.narrative_groups.models import NarrativeGroup, StageName
from novelvideo.narrative_groups.references import (
    MAX_GROUP_IMAGE_REFERENCES,
    GroupReferencePreview,
    UnknownGroupReferenceIds,
    apply_group_reference_selection,
    resolve_group_reference_preview,
)
from novelvideo.narrative_groups.service import (
    advance_revision,
    generation_beats_for_group,
    load_effective_groups,
    load_group_video_prompt_manifest,
    load_materialized_groups,
    narrative_group_sidecar_guard,
    rebuild_groups,
    rollback_stage_revision,
    reserve_video_revision,
    restore_video_reservation,
    stage_history,
    update_video_manifest_dialogue_source,
    update_video_plan,
    update_video_settings,
)
from novelvideo.ports import get_task_backend
from novelvideo.shot_continuity import (
    ContinuityRevisionConflict,
    ShotContinuityContract,
    ShotContinuityStore,
)
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3ObservedBoundary,
    load_h3_director_manifest,
    save_h3_director_manifest,
)

router = APIRouter()


class NarrativeGroupGenerationRequest(BaseModel):
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None
    provider_id: str | None = None
    model: str | None = None
    image_size: Literal["1K", "2K", "4K"] | None = None
    allow_unconstrained: bool = False


def _image_binding(
    ctx, project_dir: Path, stage: StageName, request: NarrativeGroupGenerationRequest
) -> tuple[str, str, str]:
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(ctx, "state_dir", project_dir),
        username=getattr(ctx, "owner_username", ""),
        project=getattr(ctx, "project_name", ""),
    )
    prefix = "narrative_sketch" if stage == "sketch" else "narrative_render"
    provider_id = str(
        request.provider_id or config.get(f"{prefix}_provider") or "grsai-main"
    ).strip()
    default_model = "nano-banana-2" if stage == "sketch" else "gpt-image-2"
    model = str(request.model or config.get(f"{prefix}_model") or default_model).strip()
    account = get_media_capability_store().get_provider(provider_id)
    if account is None or account.provider_type != "grsai" or not account.enabled:
        raise HTTPException(
            status_code=422, detail="Selected GRSAI image provider is unavailable"
        )
    if model not in GRSAI_IMAGE_MODELS:
        raise HTTPException(status_code=422, detail="Unsupported GRSAI image model")
    image_size = "1K"
    if stage == "render":
        from novelvideo.narrative_groups.image_resolution import (
            supported_grid_image_sizes,
        )

        image_size = str(
            request.image_size
            or config.get("narrative_render_image_size")
            or ("2K" if model == "gpt-image-2-vip" else "1K")
        ).strip()
        if image_size not in supported_grid_image_sizes(model):
            raise HTTPException(
                status_code=422,
                detail="Image size is unsupported by the selected narrative render model",
            )
    return provider_id, model, image_size


class NarrativeGroupVideoRequest(BaseModel):
    """Client input deliberately excludes mutable frame and beat payloads."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="runninghub:minimax-h3", min_length=1)
    mode: Literal["auto", "i2va", "fl2va"] = "auto"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: str | None = None
    revision: int = Field(ge=0)
    plan_revision: int = Field(ge=1)
    settings_revision: int | None = Field(default=None, ge=0)


class NarrativeGroupVideoSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    workflow_id: str = Field(min_length=1)
    overrides: dict[str, str] = Field(default_factory=dict)


class NarrativeGroupVideoPlanUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beat_ids: list[str] = Field(min_length=1, max_length=2)


class NarrativeGroupVideoPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    units: list[NarrativeGroupVideoPlanUnitRequest] = Field(min_length=1)


class NarrativeGroupDialogueSourceRequest(BaseModel):
    """A composition preference for one logical span of a director output."""

    model_config = ConfigDict(extra="forbid")
    span_index: int = Field(ge=0)
    dialogue_source: Literal["external_tts", "h3_native"]
    revision: int = Field(ge=1)


class NarrativeGroupContinuityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_revision: int = Field(ge=1)
    observed_carry_out: str = Field(min_length=1, max_length=2000)
    accept_deviation: bool = False
    deviation_reason: str = Field(default="", max_length=2000)
    lock_violations: tuple[
        Literal["identity", "spatial", "prop", "camera", "lighting"], ...
    ] = ()

    @field_validator("observed_carry_out", "deviation_reason", mode="before")
    @classmethod
    def trim_continuity_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_explained_deviation(self) -> "NarrativeGroupContinuityRequest":
        if self.accept_deviation and not self.deviation_reason:
            raise ValueError("accepted deviation requires a nonblank reason")
        return self


class NarrativeGroupStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style_id: str | None = Field(default=None, min_length=1)
    action: Literal["restyle", "redirect"] = "restyle"


async def _resolve_groups(project: str, episode: int, user: dict, *, rebuild: bool = False):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    beats = await store.get_beats_as_dicts(episode)
    if rebuild:
        if DirectorPlanStore(resolved.project_dir).load_active(episode) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DIRECTOR_PLAN_ACTIVE",
                    "message": "Active director plan controls narrative groups",
                },
            )
        groups = rebuild_groups(resolved.project_dir, episode, beats)
    else:
        groups = load_effective_groups(resolved.project_dir, episode, beats)
    return resolved, groups, beats


def _project_video_workflow_defaults(resolved, workflow) -> dict[str, str]:
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(resolved.ctx, "state_dir", resolved.project_dir),
        username=getattr(resolved.ctx, "owner_username", ""),
        project=getattr(resolved.ctx, "project_name", ""),
    )
    namespaces = config.get("video_workflow_parameters")
    raw = namespaces.get(workflow.id) if isinstance(namespaces, Mapping) else None
    overrides = dict(raw) if isinstance(raw, Mapping) else {}
    if (
        workflow.id == "runninghub:minimax-h3"
        and not overrides
        and config.get("video_resolution") in {"720p", "1080p"}
    ):
        overrides["resolution"] = config["video_resolution"]
    return resolve_workflow_parameters(workflow, overrides)


def _asset_url(project: str, project_dir: Path, value: str) -> str:
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        relative = candidate.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return ""
    encoded_project = quote(project, safe="")
    encoded_path = quote(relative.as_posix(), safe="/")
    return f"/api/v1/projects/{encoded_project}/media/{encoded_path}"


def _serialize(project: str, project_dir: Path, groups: list[NarrativeGroup]) -> list[dict]:
    result = []
    for group in groups:
        item = group.to_dict()
        effective_style = dict(item.get("effective_style_snapshot") or {})
        snapshot_id = str(effective_style.get("snapshot_id") or "project-default")
        effective_style.update({
            "snapshot_id": snapshot_id,
            "style_id": str(effective_style.get("style_id") or snapshot_id),
            "style_version": str(effective_style.get("style_version") or "1"),
            "catalog_hash": str(effective_style.get("catalog_hash") or snapshot_id),
            "style_hash": str(effective_style.get("style_hash") or snapshot_id),
            "inherited": effective_style.get("source", "inherited") == "inherited",
        })
        item["effective_style_snapshot"] = effective_style
        for stage_name, state in item["stages"].items():
            state.pop("revision_history", None)
            if stage_name == "video":
                manifest_name = str(state.get("manifest_asset") or "").strip()
                if manifest_name:
                    try:
                        from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest

                        manifest = load_h3_director_manifest(manifest_name)
                        state["video_spans"] = [
                            {
                                "span_index": index,
                                "beat_numbers": [entry.segment.beat_number],
                                "start_seconds": entry.start_seconds,
                                "end_seconds": entry.end_seconds,
                                "dialogue_source": entry.dialogue_source.value,
                            }
                            for index, entry in enumerate(manifest.entries)
                        ]
                        if not state.get("video_asset"):
                            state["video_asset"] = manifest.physical_video
                        if not state.get("original_audio_path"):
                            state["original_audio_path"] = manifest.original_audio_path or ""
                        if not state.get("dialogue_stem_path"):
                            state["dialogue_stem_path"] = manifest.dialogue_stem_path or ""
                        if not state.get("ambience_stem_path"):
                            state["ambience_stem_path"] = manifest.ambience_stem_path or ""
                    except (OSError, ValueError):
                        state["video_spans"] = []
                else:
                    state["video_spans"] = []
            state["grid_asset"] = _asset_url(
                project, project_dir, state.get("grid_asset", "")
            )
            for cell in state.get("cell_assets") or []:
                url = _asset_url(project, project_dir, str(cell.get("path") or ""))
                cell["path"] = url
                cell["url"] = url
            for field in (
                "video_asset", "manifest_asset", "original_audio_path",
                "dialogue_stem_path", "ambience_stem_path",
            ):
                state[field] = _asset_url(project, project_dir, state.get(field, ""))
        result.append(item)
    return result


def _group_beats(
    groups: list[NarrativeGroup], beats: list[dict], group_id: str
) -> tuple[NarrativeGroup, list[dict]]:
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise KeyError(group_id)
    beat_by_id = {
        str(beat.get("id") or beat.get("beat_id") or beat.get("beat_number")): beat
        for beat in beats
    }
    return group, [beat_by_id[beat_id] for beat_id in group.beat_ids if beat_id in beat_by_id]


def _serialize_reference_preview(
    project: str, project_dir: Path, preview: GroupReferencePreview
) -> dict:
    selection = apply_group_reference_selection(preview)
    selected_ids = {reference.id for reference in selection.selected}
    omitted_ids = [reference.id for reference in selection.omitted]

    def image_item(reference) -> dict:
        return {
            "id": reference.id,
            "kind": reference.kind,
            "source_kind": reference.source_kind,
            "label": reference.label,
            "thumbnail_url": _asset_url(project, project_dir, reference.path),
            "beat_numbers": list(reference.beat_numbers),
            "enabled_by_default": reference.id in selected_ids,
            "character_name": reference.character_name or None,
            "identity_id": reference.identity_id or None,
            "scene_id": reference.scene_id or None,
            "warning": reference.warning,
        }

    return {
        "style": {
            "id": preview.style.id,
            "label": preview.style.name,
            "prompt": preview.style.prompt,
            "enabled_by_default": True,
            "warning": preview.style.warning,
        },
        "character_references": [
            image_item(reference)
            for reference in preview.image_references
            if reference.kind == "character"
        ],
        "scene_references": [
            image_item(reference)
            for reference in preview.image_references
            if reference.kind == "scene"
        ],
        "limits": {
            "max_images": MAX_GROUP_IMAGE_REFERENCES,
            "selected_images": len(selection.selected),
            "omitted_reference_ids": omitted_ids,
        },
        "warnings": list(selection.warnings),
    }


_REJECTED_REVIEW_VALUE = object()
_SAFE_TEXT = object()
_SAFE_INT = object()
_SAFE_NUMBER = object()
_SAFE_BOOL = object()
_SAFE_OPTIONAL_TEXT = object()
_SAFE_OPTIONAL_INT = object()
_MAX_REVIEW_ID_LENGTH = 256
_MAX_REVIEW_TEXT_LENGTH = 16 * 1024
_MAX_REVIEW_PROMPT_LENGTH = 256 * 1024
_MAX_REVIEW_BEAT_IDS = 32
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_CAMERA_PLAN_SCHEMA = {
    "type": _SAFE_TEXT,
    "direction": _SAFE_TEXT,
    "amplitude": _SAFE_TEXT,
    "speed": _SAFE_TEXT,
}
_ACTION_PLAN_SCHEMA = {
    "phase": _SAFE_TEXT,
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "description": _SAFE_TEXT,
}
_DIALOGUE_CUE_SCHEMA = {
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "speaker": _SAFE_TEXT,
    "speaker_id": _SAFE_TEXT,
    "text": _SAFE_TEXT,
    "language": _SAFE_TEXT,
    "continuation": _SAFE_BOOL,
    "truncated": _SAFE_BOOL,
}
_SHOT_PLAN_SCHEMA = {
    "shot_id": _SAFE_TEXT,
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "framing": _SAFE_TEXT,
    "angle": _SAFE_TEXT,
    "focus": _SAFE_TEXT,
    "composition": _SAFE_TEXT,
    "camera": _CAMERA_PLAN_SCHEMA,
    "actions": [_ACTION_PLAN_SCHEMA],
    "dialogue": [_DIALOGUE_CUE_SCHEMA],
}
_DIRECTOR_PLAN_SCHEMA = {
    "mode": _SAFE_TEXT,
    "fps": _SAFE_INT,
    "total_frames": _SAFE_INT,
    "visual_style": _SAFE_TEXT,
    "continuity_locks": [_SAFE_TEXT],
    "shots": [_SHOT_PLAN_SCHEMA],
    "frame_differences": [{
        "description": _SAFE_TEXT,
        "convergence_frame": _SAFE_INT,
    }],
    "soundscape": _SAFE_TEXT,
    "music": _SAFE_TEXT,
}
_PROMPT_PROFILE_SCHEMA = {
    "id": _SAFE_TEXT,
    "version": _SAFE_INT,
    "compiler_version": _SAFE_INT,
}
_QUALITY_REPORT_SCHEMA = {
    "passed": _SAFE_BOOL,
    "issues": [{
        "code": _SAFE_TEXT,
        "message": _SAFE_TEXT,
        "severity": _SAFE_TEXT,
        "location": _SAFE_TEXT,
    }],
    "version": _SAFE_INT,
}
_INPUT_SUMMARY_SCHEMA = {
    "beat_ids": [_SAFE_TEXT],
    "mode": _SAFE_TEXT,
    "duration_seconds": _SAFE_NUMBER,
    "aspect_ratio": _SAFE_TEXT,
    "resolution": _SAFE_TEXT,
    "first_frame_sha256": _SAFE_TEXT,
    "last_frame_sha256": _SAFE_TEXT,
}
_OBSERVED_BOUNDARY_SCHEMA = {
    "value": _SAFE_TEXT,
    "source_contract_revision": _SAFE_INT,
    "result_contract_revision": _SAFE_INT,
    "accepted": _SAFE_BOOL,
    "deviation_reason": _SAFE_TEXT,
    "lock_violations": [_SAFE_TEXT],
}
_CONTINUITY_BOUNDARY_SCHEMA = {
    "carry_in": _SAFE_TEXT,
    "planned_carry_out": _SAFE_TEXT,
    "observed_carry_out": _SAFE_OPTIONAL_TEXT,
    "deviation_accepted": _SAFE_BOOL,
    "deviation_reason": _SAFE_TEXT,
}
_CONTINUITY_CONTRACT_SCHEMA = {
    "revision": _SAFE_INT,
    "shot_id": _SAFE_TEXT,
    "scene_id": _SAFE_TEXT,
    "predecessor_shot_id": _SAFE_OPTIONAL_TEXT,
    "predecessor_revision": _SAFE_OPTIONAL_INT,
    "boundary": _CONTINUITY_BOUNDARY_SCHEMA,
}
_RISK_DIMENSION_SCHEMA = {
    "dimension": _SAFE_TEXT,
    "level": _SAFE_INT,
    "reasons": [_SAFE_TEXT],
}
_RISK_REPORT_SCHEMA = {
    "spatial": _RISK_DIMENSION_SCHEMA,
    "identity": _RISK_DIMENSION_SCHEMA,
    "motion": _RISK_DIMENSION_SCHEMA,
    "continuity": _RISK_DIMENSION_SCHEMA,
    "blockers": [_SAFE_TEXT],
}
_MODE_DECISION_SCHEMA = {
    "requested": _SAFE_TEXT,
    "mode": _SAFE_OPTIONAL_TEXT,
    "reason_codes": [_SAFE_TEXT],
    "blockers": [_SAFE_TEXT],
}
_COMPILED_BUNDLE_SCHEMA = {
    "adapter": _SAFE_TEXT,
    "mode": _SAFE_TEXT,
    "compiler_id": _SAFE_TEXT,
    "compiler_version": _SAFE_INT,
    "diagnostics": [_SAFE_TEXT],
    "bundle_sha256": _SAFE_TEXT,
}


def _manifest_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(mode="json")) if callable(dump) else {}


def _project_review_value(value: Any, schema: Any) -> Any:
    if schema is _SAFE_OPTIONAL_TEXT:
        return None if value is None else _project_review_value(value, _SAFE_TEXT)
    if schema is _SAFE_OPTIONAL_INT:
        return None if value is None else _project_review_value(value, _SAFE_INT)
    if schema is _SAFE_TEXT:
        if (
            isinstance(value, str)
            and len(value) <= _MAX_REVIEW_TEXT_LENGTH
            and not _is_path_or_uri(value)
        ):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_INT:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_NUMBER:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_BOOL:
        if isinstance(value, bool):
            return value
        return _REJECTED_REVIEW_VALUE
    if isinstance(schema, dict):
        source = _manifest_mapping(value)
        projected = {}
        for key, child_schema in schema.items():
            if key not in source:
                continue
            child = _project_review_value(source[key], child_schema)
            if child is not _REJECTED_REVIEW_VALUE:
                projected[key] = child
        return projected
    if isinstance(schema, list) and len(schema) == 1:
        if not isinstance(value, (list, tuple)):
            return []
        return [
            child
            for item in value
            if (child := _project_review_value(item, schema[0]))
            is not _REJECTED_REVIEW_VALUE
        ]
    return _REJECTED_REVIEW_VALUE


def _is_path_or_uri(value: str) -> bool:
    stripped = value.strip()
    return bool(
        _URI_RE.match(stripped)
        or stripped.casefold().startswith("file://")
        or stripped.startswith(("\\\\", "//"))
        or PureWindowsPath(stripped).drive
        or PureWindowsPath(stripped).is_absolute()
        or PurePosixPath(stripped).is_absolute()
    )


def _safe_review_string(value: Any, *, max_length: int = _MAX_REVIEW_ID_LENGTH) -> str:
    if (
        isinstance(value, str)
        and len(value) <= max_length
        and not _is_path_or_uri(value)
    ):
        return value
    return ""


def _first_safe_review_string(*values: Any) -> str:
    for value in values:
        safe = _safe_review_string(value)
        if safe:
            return safe
    return ""


def _safe_prompt(value: Any) -> str:
    return value if isinstance(value, str) and len(value) <= _MAX_REVIEW_PROMPT_LENGTH else ""


def _safe_duration(*values: Any) -> float | int:
    for value in values:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 3600
        ):
            return value
    return 0


def _safe_frame_reference(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    stripped = value.strip()
    if (
        _URI_RE.match(stripped)
        or stripped.casefold().startswith("file://")
        or stripped.startswith(("\\\\", "//"))
        or PureWindowsPath(stripped).drive
        or PureWindowsPath(stripped).is_absolute()
    ):
        return ""
    return value


def _review_beat_ids(entry: Mapping[str, Any], segment: Mapping[str, Any]) -> list[str]:
    summary = _project_review_value(
        entry.get("input_summary"), _INPUT_SUMMARY_SCHEMA
    )
    beat_ids = [
        safe
        for value in (summary.get("beat_ids") or ())[:_MAX_REVIEW_BEAT_IDS]
        if (safe := _safe_review_string(value))
    ]
    if beat_ids:
        return beat_ids
    segment_id = _safe_review_string(segment.get("segment_id"))
    if segment_id:
        return [part for part in segment_id.split("--") if part]
    beat_number = segment.get("beat_number")
    return (
        [f"beat-{beat_number}"]
        if isinstance(beat_number, int) and not isinstance(beat_number, bool)
        else []
    )


def _review_label(beat_ids: list[str]) -> str:
    labels = []
    for beat_id in beat_ids:
        suffix = beat_id[5:] if beat_id.lower().startswith("beat-") else beat_id
        labels.append(f"Beat {suffix}")
    return " → ".join(labels)


def _serialize_prompt_review(
    project: str,
    project_dir: Path,
    manifest: Mapping[str, Any],
    stage: Any,
) -> dict[str, Any]:
    units = []
    for raw_entry in manifest.get("entries") or ():
        entry = _manifest_mapping(raw_entry)
        segment = _manifest_mapping(entry.get("segment"))
        continuity_contracts = entry.get("continuity_contracts") or ()
        terminal_contract = _manifest_mapping(
            continuity_contracts[-1]
            if isinstance(continuity_contracts, (list, tuple))
            and continuity_contracts
            else None
        )
        terminal_boundary = _manifest_mapping(terminal_contract.get("boundary"))
        summary = _project_review_value(
            entry.get("input_summary"), _INPUT_SUMMARY_SCHEMA
        )
        plan = _project_review_value(
            entry.get("director_plan"), _DIRECTOR_PLAN_SCHEMA
        )
        beat_ids = _review_beat_ids(entry, segment)
        first_frame = _safe_frame_reference(segment.get("first_frame"))
        last_frame = _safe_frame_reference(segment.get("last_frame"))
        mode = next(
            (
                value for value in (
                    summary.get("mode"), plan.get("mode"),
                    getattr(stage, "actual_mode", ""),
                )
                if isinstance(value, str) and value in {"auto", "i2va", "fl2va"}
            ),
            "fl2va" if last_frame else "i2va",
        )
        duration = _safe_duration(
            summary.get("duration_seconds"),
            entry.get("actual_duration_seconds"),
            segment.get("duration_seconds"),
        )
        units.append({
            "segment_id": _safe_review_string(segment.get("segment_id")),
            "beat_ids": beat_ids,
            "label": _review_label(beat_ids),
            "mode": mode,
            "duration_seconds": duration,
            "first_frame_url": _asset_url(project, project_dir, first_frame),
            "last_frame_url": _asset_url(project, project_dir, last_frame),
            "director_plan": (
                plan if entry.get("director_plan") is not None else None
            ),
            "final_prompt": _safe_prompt(segment.get("prompt")),
            "prompt_profile": (
                _project_review_value(
                    entry.get("prompt_profile"), _PROMPT_PROFILE_SCHEMA
                )
                if entry.get("prompt_profile") is not None else None
            ),
            "quality_report": (
                _project_review_value(
                    entry.get("quality_report"), _QUALITY_REPORT_SCHEMA
                )
                if entry.get("quality_report") is not None else None
            ),
            "input_summary": summary,
            "workflow": _first_safe_review_string(
                entry.get("workflow_id"), manifest.get("workflow_id")
            ),
            "model": _first_safe_review_string(
                entry.get("model"), manifest.get("model"),
                getattr(stage, "actual_model", ""),
            ),
            "provider": _safe_review_string(
                getattr(stage, "actual_provider", "") or ""
            ),
            "provider_task_id": _first_safe_review_string(
                entry.get("provider_task_id"), manifest.get("provider_task_id")
            ),
            "continuity_contracts": _project_review_value(
                continuity_contracts, [_CONTINUITY_CONTRACT_SCHEMA]
            ),
            "risk_report": (
                _project_review_value(entry.get("risk_report"), _RISK_REPORT_SCHEMA)
                if entry.get("risk_report") is not None
                else None
            ),
            "mode_decision": (
                _project_review_value(
                    entry.get("mode_decision"), _MODE_DECISION_SCHEMA
                )
                if entry.get("mode_decision") is not None
                else None
            ),
            "compiled_bundle": (
                _project_review_value(
                    entry.get("compiled_bundle"), _COMPILED_BUNDLE_SCHEMA
                )
                if entry.get("compiled_bundle") is not None
                else None
            ),
            "planned_carry_out": _safe_review_string(
                terminal_boundary.get("planned_carry_out"), max_length=2000
            ),
            "observed_carry_out": (
                _project_review_value(
                    entry.get("observed_carry_out"), _OBSERVED_BOUNDARY_SCHEMA
                )
                if entry.get("observed_carry_out") is not None
                else None
            ),
        })
    def snapshot(name: str) -> dict[str, Any]:
        value = manifest.get(name)
        return dict(value) if isinstance(value, Mapping) else {}

    return {
        "format_version": (
            manifest.get("format_version")
            if isinstance(manifest.get("format_version"), int)
            and not isinstance(manifest.get("format_version"), bool)
            else 1
        ),
        "workflow_parameters": snapshot("workflow_parameters"),
        "provider_parameters": snapshot("provider_parameters"),
        "actual_output": snapshot("actual_output"),
        "units": units,
    }


@router.get("/projects/{project}/episodes/{episode}/narrative-groups")
async def list_narrative_groups(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    return {"ok": True, "data": _serialize(project, resolved.project_dir, groups)}


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/video/prompts"
)
async def get_group_video_prompts(
    project: str,
    episode: int,
    group_id: str,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    try:
        manifest, stage = load_group_video_prompt_manifest(
            resolved.project_dir, episode, group_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Video prompt manifest not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Video prompt manifest is invalid") from exc
    return {
        "ok": True,
        "data": _serialize_prompt_review(
            project, resolved.project_dir, manifest, stage
        ),
    }


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/"
    "video/segments/{segment_id}/continuity"
)
async def put_group_video_segment_continuity(
    project: str,
    episode: int,
    group_id: str,
    segment_id: str,
    request: NarrativeGroupContinuityRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    root = Path(resolved.project_dir).resolve()
    with narrative_group_sidecar_guard(root, episode):
        expected_stage = _load_current_video_stage(root, episode, group_id)
        if expected_stage.status not in _POSTFLIGHT_STAGE_STATUSES:
            raise HTTPException(
                status_code=409, detail="Video stage is not ready for review"
            )
        expected_fingerprint = _postflight_stage_fingerprint(root, expected_stage)
        stage = _load_postflight_stage(root, episode, group_id)
        if _postflight_stage_fingerprint(root, stage) != expected_fingerprint:
            raise HTTPException(
                status_code=409, detail="Narrative group video stage changed"
            )
        return _put_group_video_segment_continuity_locked(
            project, root, episode, segment_id, request, stage
        )


_POSTFLIGHT_STAGE_STATUSES = {
    "review",
    "completed",
    "partial_failure",
    "failed",
}
_POSTFLIGHT_MANIFEST_STATUSES = {
    "completed",
    "partial_failure",
    "quality_rejected",
    "transport_failed",
    "postprocess_failed",
    "quality_mismatch",
}


def _load_postflight_stage(project_dir: Path, episode: int, group_id: str):
    try:
        _, stage = load_group_video_prompt_manifest(project_dir, episode, group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Video manifest not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Video manifest is invalid") from exc
    return stage


def _load_current_video_stage(project_dir: Path, episode: int, group_id: str):
    group = next(
        (
            item
            for item in load_materialized_groups(project_dir, episode)
            if item.id == group_id
        ),
        None,
    )
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    return group.stages["video"]


def _postflight_manifest_path(project_dir: Path, stage: Any) -> Path:
    stored_path = Path(str(stage.manifest_asset).strip())
    candidate = stored_path if stored_path.is_absolute() else project_dir / stored_path
    manifest_path = candidate.resolve()
    if not manifest_path.is_relative_to(project_dir) or not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Video manifest not found")
    return manifest_path


def _postflight_stage_fingerprint(project_dir: Path, stage: Any) -> tuple[int, str, str]:
    stored_path = Path(str(stage.manifest_asset).strip())
    candidate = stored_path if stored_path.is_absolute() else project_dir / stored_path
    manifest_path = candidate.resolve()
    return (
        int(stage.revision),
        str(stage.status),
        str(manifest_path),
    )


def _put_group_video_segment_continuity_locked(
    project: str,
    root: Path,
    episode: int,
    segment_id: str,
    request: NarrativeGroupContinuityRequest,
    stage: Any,
):
    if stage.status not in _POSTFLIGHT_STAGE_STATUSES:
        raise HTTPException(status_code=409, detail="Video stage is not ready for review")

    manifest_path = _postflight_manifest_path(root, stage)
    try:
        manifest = load_h3_director_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Video manifest is invalid") from exc
    if (
        manifest.format_version < 2
        or manifest.status not in _POSTFLIGHT_MANIFEST_STATUSES
    ):
        raise HTTPException(
            status_code=409, detail="Video manifest is not terminal review evidence"
        )

    matches = [
        (index, entry)
        for index, entry in enumerate(manifest.entries)
        if entry.segment.segment_id == segment_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="Video segment not found")
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="Video segment evidence is ambiguous")
    entry_index, entry = matches[0]
    if entry.status not in _POSTFLIGHT_MANIFEST_STATUSES:
        raise HTTPException(
            status_code=409, detail="Video segment is not terminal review evidence"
        )
    if not entry.continuity_contracts:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is missing")
    try:
        contract = ShotContinuityContract.model_validate(
            entry.continuity_contracts[-1]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is invalid") from exc
    if contract.revision != request.contract_revision:
        raise HTTPException(status_code=409, detail="Continuity contract revision is stale")
    if contract.shot_id != segment_id.split("--")[-1]:
        raise HTTPException(status_code=409, detail="Continuity contract does not match video segment")

    planned = contract.boundary.planned_carry_out
    is_deviation = request.observed_carry_out != planned
    if is_deviation and not request.accept_deviation:
        raise HTTPException(status_code=409, detail="Observed boundary deviates from the plan")
    reason = request.deviation_reason if request.accept_deviation else ""
    boundary = contract.boundary.model_copy(update={
        "observed_carry_out": request.observed_carry_out,
        "deviation_accepted": request.accept_deviation,
        "deviation_reason": reason,
    })
    candidate = contract.model_copy(update={"boundary": boundary})
    continuity_store = ShotContinuityStore(root)
    try:
        active = continuity_store.load_active(episode, contract.shot_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is invalid") from exc
    candidate_semantics = candidate.model_copy(update={"revision": 0})
    if (
        active is not None
        and active.revision == request.contract_revision
        and active.contract_sha256 == contract.contract_sha256
    ):
        try:
            saved = continuity_store.put(
                episode, candidate, expected_revision=request.contract_revision
            )
        except ContinuityRevisionConflict as exc:
            raise HTTPException(status_code=409, detail="Continuity contract revision is stale") from exc
    elif (
        active is not None
        and active.model_copy(update={"revision": 0}) == candidate_semantics
    ):
        saved = active
    else:
        raise HTTPException(status_code=409, detail="Continuity contract revision is stale")

    observed = H3ObservedBoundary(
        value=request.observed_carry_out,
        source_contract_revision=request.contract_revision,
        result_contract_revision=saved.revision,
        accepted=request.accept_deviation,
        deviation_reason=reason,
        lock_violations=request.lock_violations,
    )
    entries = list(manifest.entries)
    entries[entry_index] = entry.model_copy(update={"observed_carry_out": observed})
    updated_manifest = H3DirectorOutputManifest(
        **manifest.model_dump(exclude={"entries"}), entries=tuple(entries)
    )
    try:
        save_h3_director_manifest(manifest_path, updated_manifest)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Continuity revision was saved but the video manifest update failed; retry is safe",
        ) from exc

    data = _serialize_prompt_review(
        project,
        root,
        updated_manifest.model_dump(mode="json"),
        stage,
    )
    data["stale_dependent_shot_ids"] = sorted(
        item.shot_id
        for item in continuity_store.stale_dependents(episode, contract.shot_id)
    )
    return {"ok": True, "data": data}


@router.post("/projects/{project}/episodes/{episode}/narrative-groups/rebuild")
async def rebuild_narrative_groups(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user, rebuild=True)
    return {"ok": True, "data": _serialize(project, resolved.project_dir, groups)}


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/references"
)
async def preview_group_references(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    try:
        _, selected_beats = _group_beats(groups, beats, group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    preview = resolve_group_reference_preview(
        resolved.project_dir, selected_beats, stage=stage_name
    )
    return {
        "ok": True,
        "data": _serialize_reference_preview(project, resolved.project_dir, preview),
    }


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/revisions"
)
async def list_stage_revisions(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    items = stage_history(resolved.project_dir, episode, group_id, stage_name)
    for item in items:
        item["grid_asset"] = _asset_url(
            project, resolved.project_dir, item.get("grid_asset", "")
        )
        for cell in item.get("cell_assets") or []:
            url = _asset_url(project, resolved.project_dir, str(cell.get("path") or ""))
            cell["path"] = url
            cell["url"] = url
    return {
        "ok": True,
        "data": {
            "items": items,
            "current_revision": group.stages[stage_name].revision,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/revisions/{revision}/rollback"
)
async def rollback_stage(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    revision: int,
    user: dict = Depends(get_api_user),
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    try:
        group = rollback_stage_revision(
            resolved.project_dir,
            episode,
            group_id,
            stage_name,
            revision=revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group revision not found") from exc
    return {"ok": True, "data": _serialize(project, resolved.project_dir, [group])[0]}


async def _enqueue_group_action(
    project: str,
    episode: int,
    group_id: str,
    stage: StageName,
    user: dict,
    *,
    regenerate: bool = False,
    split_only: bool = False,
    generation_request: NarrativeGroupGenerationRequest | None = None,
):
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    try:
        source_group, selected_beats = _group_beats(groups, beats, group_id)
        selected_beats = generation_beats_for_group(
            resolved.project_dir,
            episode,
            group_id,
            selected_beats,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Narrative group '{group_id}' not found"
        ) from exc

    request = generation_request or NarrativeGroupGenerationRequest()
    reference_selection = None
    if not split_only:
        preview = resolve_group_reference_preview(
            resolved.project_dir, selected_beats, stage=stage
        )
        try:
            apply_group_reference_selection(
                preview,
                use_style=request.use_style,
                selected_character_reference_ids=request.selected_character_reference_ids,
                selected_scene_reference_ids=request.selected_scene_reference_ids,
            )
        except UnknownGroupReferenceIds as exc:
            raise HTTPException(
                status_code=422,
                detail={"unknown_reference_ids": list(exc.unknown_ids)},
            ) from exc
        reference_selection = request.model_dump(
            exclude={
                "aspect_ratio",
                "provider_id",
                "model",
                "image_size",
                "allow_unconstrained",
            }
        )
    provider_id = model = ""
    image_size = "1K"
    constraint_mode = ""
    source_sketch_revision = 0
    source_sketch_asset = ""
    if not split_only:
        provider_id, model, image_size = _image_binding(
            resolved.ctx, resolved.project_dir, stage, request
        )
        if stage == "render":
            sketch = source_group.stages["sketch"]
            sketch_path = Path(sketch.grid_asset) if sketch.grid_asset else None
            sketch_ready = (
                sketch.status == "completed"
                and sketch.revision > 0
                and sketch_path is not None
                and sketch_path.is_file()
            )
            if not sketch_ready and not request.allow_unconstrained:
                raise HTTPException(
                    status_code=409,
                    detail="请先完成当前叙事组草图，或明确选择无草图约束生成",
                )
            if sketch_ready:
                constraint_mode = "strong_sketch"
                source_sketch_revision = sketch.revision
                source_sketch_asset = str(sketch_path)
            else:
                constraint_mode = "unconstrained"
    try:
        group, revision = advance_revision(
            resolved.project_dir,
            episode,
            group_id,
            stage,
            regenerate=regenerate,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc

    scope = f"group_{group_id}_{stage}_r{revision}"
    mapping = [item.__dict__ for item in group.cell_to_beat]
    task_type = "narrative_group_split" if split_only else "narrative_group_grid"
    payload = {
        "episode": episode,
        "output_dir": resolved.output_dir,
        "project_dir": str(resolved.project_dir),
        "group_id": group_id,
        "stage": stage,
        "revision": revision,
        "layout": group.layout.__dict__,
        "aspect_ratio": request.aspect_ratio,
        "beat_ids": list(group.beat_ids),
        "cell_to_beat": mapping,
        "beats": selected_beats,
        "split_only": split_only,
    }
    if reference_selection is not None:
        payload["reference_selection"] = reference_selection
        payload.update(
            {
                "provider_id": provider_id,
                "model": model,
                "image_size": image_size,
                "constraint_mode": constraint_mode,
                "source_sketch_revision": source_sketch_revision,
                "source_sketch_asset": source_sketch_asset,
            }
        )
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx, task_type=task_type, queue_kind="default", episode=episode,
        scope=scope, payload=payload,
    )
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id, "scope": scope,
        "backend": queued.backend, "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": stage, "revision": revision},
    }}


async def _enqueue_group_video(
    project: str,
    episode: int,
    group_id: str,
    user: dict,
    request: NarrativeGroupVideoRequest,
    media_store: MediaCapabilityStore,
    credential_resolver: CredentialResolver,
    *,
    segment_id: str = "",
):
    registry = build_video_workflow_registry(media_store, credential_resolver)
    try:
        workflow = registry.resolve(request.model, VideoWorkflowScene.NARRATIVE_GROUP)
    except VideoWorkflowUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail="Video workflow is unavailable for narrative groups",
        ) from exc
    if request.mode not in workflow.supported_modes:
        raise HTTPException(
            status_code=422,
            detail="Video mode is unsupported by the selected workflow",
        )
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    source_group = next((item for item in groups if item.id == group_id), None)
    if source_group is None:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found")
    settings = source_group.video_settings
    if request.settings_revision is not None:
        if request.settings_revision != settings.revision:
            raise HTTPException(
                status_code=409,
                detail="Narrative group video settings revision is stale",
            )
        if workflow.id != settings.workflow_id:
            raise HTTPException(
                status_code=409,
                detail="Narrative group video workflow does not match saved settings",
            )
    try:
        project_defaults = _project_video_workflow_defaults(resolved, workflow)
        parameter_overrides = dict(settings.overrides)
        if request.settings_revision is None and request.resolution is not None:
            parameter_overrides["resolution"] = request.resolution
        workflow_parameters = resolve_workflow_parameters(
            workflow,
            {**project_defaults, **parameter_overrides},
        )
    except VideoWorkflowParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        group, reservation = reserve_video_revision(
            resolved.project_dir, episode, group_id,
            expected_revision=request.revision,
            expected_plan_revision=request.plan_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Narrative group video revision is stale")
    revision = reservation.revision
    scope = f"group_{group_id}_video_r{revision}"
    payload = {
        "episode": episode,
        "group_id": group.id,
        "revision": revision,
        "plan_revision": request.plan_revision,
        "model": request.model,
        "mode": request.mode,
        "aspect_ratio": request.aspect_ratio,
        "workflow_parameters": workflow_parameters,
        "settings_revision": settings.revision,
    }
    if segment_id:
        payload["segment_id"] = segment_id
    try:
        queued = await get_task_backend().enqueue_project_task(
            resolved.ctx, task_type="narrative_group_video", queue_kind="video",
            episode=episode, scope=scope, payload=payload,
        )
    except Exception as exc:
        restore_video_reservation(resolved.project_dir, episode, reservation)
        raise HTTPException(status_code=503, detail="Narrative group video queue is unavailable") from exc
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id, "scope": scope,
        "backend": queued.backend, "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": "video", "revision": revision},
    }}
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/sketch/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/sketch-grid/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_sketch_group(
    project: str,
    episode: int,
    group_id: str,
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, "sketch", user, generation_request=body
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render-grid/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_render_group(
    project: str,
    episode: int,
    group_id: str,
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, "render", user, generation_request=body
    )


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/settings"
)
async def put_group_video_settings(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoSettingsRequest,
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    registry = build_video_workflow_registry(media_store, credential_resolver)
    try:
        workflow = registry.resolve(
            request.workflow_id, VideoWorkflowScene.NARRATIVE_GROUP
        )
        project_defaults = _project_video_workflow_defaults(resolved, workflow)
        resolved_values = resolve_workflow_parameters(
            workflow, {**project_defaults, **request.overrides}
        )
        normalized_overrides = {
            key: value
            for key, value in resolved_values.items()
            if project_defaults.get(key) != value
        }
        group = update_video_settings(
            resolved.project_dir,
            episode,
            group_id,
            expected_revision=request.expected_revision,
            workflow_id=workflow.id,
            overrides=normalized_overrides,
            project_defaults=project_defaults,
        )
    except VideoWorkflowUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VideoWorkflowParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except (RuntimeError, TypeError) as exc:
        status_code = 409 if isinstance(exc, RuntimeError) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": _serialize(project, resolved.project_dir, [group])[0],
    }


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/plan"
)
async def put_group_video_plan(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoPlanRequest,
    user: dict = Depends(get_api_user),
):
    resolved, _, beats = await _resolve_groups(project, episode, user)
    try:
        plan_beats = generation_beats_for_group(
            resolved.project_dir, episode, group_id, beats
        )
        group = update_video_plan(
            resolved.project_dir,
            episode,
            group_id,
            plan_beats,
            expected_revision=request.expected_revision,
            units=[unit.model_dump() for unit in request.units],
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Narrative group not found"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": _serialize(project, resolved.project_dir, [group])[0],
    }


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_video_group(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoRequest = Body(default_factory=NarrativeGroupVideoRequest),
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_video(
        project,
        episode,
        group_id,
        user,
        request,
        media_store,
        credential_resolver,
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/segments/{segment_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_video_segment(
    project: str, episode: int, group_id: str, segment_id: str,
    request: NarrativeGroupVideoRequest = Body(default_factory=NarrativeGroupVideoRequest),
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    _, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    if segment_id not in {str(item.get("id")) for item in group.video_segments}:
        raise HTTPException(status_code=404, detail="Video segment not found")
    return await _enqueue_group_video(
        project, episode, group_id, user, request, media_store,
        credential_resolver, segment_id=segment_id,
    )


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/style"
)
async def put_group_style(
    project: str, episode: int, group_id: str,
    request: NarrativeGroupStyleRequest,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    if not any(item.id == group_id for item in groups):
        raise HTTPException(status_code=404, detail="Narrative group not found")
    from novelvideo.services.style_service import StyleService
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(resolved.ctx, "state_dir", resolved.project_dir),
        username=resolved.ctx.owner_username,
        project=resolved.ctx.project_name,
    )
    project_style = str(config.get("visual_style") or "chinese_period_drama")
    snapshot = StyleService.resolve_style_snapshot(
        project_style, request.style_id, username=resolved.ctx.owner_username,
        project=resolved.ctx.project_name, project_dir=resolved.project_dir,
    )
    store = DirectorPlanStore(resolved.project_dir)
    active = store.load_active(episode)
    if active is None:
        raise HTTPException(status_code=409, detail="Active director plan required")
    child = active.new(
        episode=active.episode, source_script_hash=active.source_script_hash,
        director_model=active.director_model, prompt_version=active.prompt_version,
        project_style_snapshot_id=snapshot.snapshot_id,
        project_style_snapshot=snapshot,
        groups=tuple(
            item.model_copy(update={"style_snapshot_id": snapshot.snapshot_id})
            if item.id == group_id else item for item in active.groups
        ),
        parent_revision_id=active.revision_id,
    ).model_copy(update={
        "status": "review_required", "validation_report": active.validation_report
    })
    store.save(child)
    if request.action == "restyle":
        child = store.activate(episode, child.revision_id)
    task_data: dict[str, Any] = {}
    if request.action == "redirect":
        source_store = EpisodeSourceStore(
            await make_sqlite_store_for_context(resolved.ctx)
        )
        source = next(
            (
                item for item in await source_store.list_sources()
                if int(item.episode_number) == episode
            ),
            None,
        )
        if source is None:
            raise HTTPException(status_code=404, detail="Episode source not found")
        scope = f"style:{snapshot.style_hash}:revision:{source.source_revision}"
        queued = await get_task_backend().enqueue_project_task(
            resolved.ctx,
            task_type="director_plan",
            queue_kind="default",
            episode=episode,
            scope=scope,
            payload={
                "project_id": str(resolved.ctx.project_id),
                "episode": episode,
                "source_revision": int(source.source_revision),
                "style_id": snapshot.style_id,
                "style_snapshot_id": snapshot.snapshot_id,
            },
        )
        task_data = {
            "task_id": queued.task_state.task_id,
            "backend": queued.backend,
            "queue": queued.queue,
            "scope": scope,
        }
    return {"ok": True, "data": {
        "revision_id": child.revision_id, "status": child.status,
        "action": request.action,
        "style_snapshot": snapshot.model_dump(mode="json"),
        **task_data,
    }}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/dialogue-source",
    status_code=status.HTTP_202_ACCEPTED,
)
async def change_group_video_dialogue_source(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupDialogueSourceRequest,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    try:
        update_video_manifest_dialogue_source(
            resolved.project_dir,
            episode,
            group_id,
            span_index=request.span_index,
            dialogue_source=request.dialogue_source,
            expected_revision=request.revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except (FileNotFoundError, IndexError) as exc:
        raise HTTPException(status_code=404, detail="Narrative group video span not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    revision = request.revision
    scope = f"group_{group_id}_video_compose_r{revision}_s{request.span_index}"
    payload = {
        "episode": episode,
        "group_id": group_id,
        "revision": revision,
        "span_index": request.span_index,
        "dialogue_source": request.dialogue_source,
    }
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type="narrative_group_video_compose",
        queue_kind="default",
        episode=episode,
        scope=scope,
        payload=payload,
    )
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id,
        "scope": scope,
        "backend": queued.backend,
        "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": "video", "revision": revision},
    }}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/{stage_name}/split",
    status_code=status.HTTP_202_ACCEPTED,
)
async def split_group(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project,
        episode,
        group_id,
        stage_name,
        user,
        split_only=True,
        generation_request=body,
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/{stage_name}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_group(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project,
        episode,
        group_id,
        stage_name,
        user,
        regenerate=True,
        generation_request=body,
    )
