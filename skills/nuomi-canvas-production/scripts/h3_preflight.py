#!/usr/bin/env python3
"""Read-only local preflight for externally authored H3 plans.

The checker consumes the public prepared-context response and a prepared-plan
import envelope. It never calls a model or a provider and never writes to the
project. A clean result verifies the local inputs and compiler gate; it cannot
guarantee the video provider's generated content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPO_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from novelvideo.media_capabilities.video.h3_director_plan import H3DirectorPlan  # noqa: E402
from novelvideo.media_capabilities.video.h3_prepared_plans import (  # noqa: E402
    EMPTY_H3_REFERENCE_BINDING_SHA256,
    EMPTY_H3_SOURCE_REUSE_DIGEST,
    H3PreparedPlanImport,
    H3PreparedPlanPins,
    prepared_h3_context_digest,
)
from novelvideo.media_capabilities.video.h3_prompt_optimizer import (  # noqa: E402
    H3PromptContext,
    compile_and_gate_h3_plan,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment  # noqa: E402
from novelvideo.media_capabilities.video.models import H3Mode  # noqa: E402
from novelvideo.narrative_groups.video_repairs import VideoRepairSnapshot  # noqa: E402


def _issue(code: str, group_id: str | None, segment_id: str | None, field: str, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "group_id": group_id,
        "segment_id": segment_id,
        "field": field,
        "message": message,
    }


def _aspect_ratio(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        return None
    width, height = (float(part) for part in match.groups())
    if width <= 0 or height <= 0:
        return None
    return width / height


def _frame_path(raw_path: object, project_root: Path | None) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if not path.is_absolute() and project_root is not None:
        root = project_root.resolve()
        path = (root / path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path
    return path.resolve()


def _character_names(bundle: dict[str, Any]) -> tuple[str, ...]:
    raw = bundle.get("character_names", ())
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(name.strip() for name in raw if isinstance(name, str) and name.strip())


def _text_has_name(text: str, name: str) -> bool:
    if name.isascii() and re.fullmatch(r"[A-Za-z0-9_]+", name):
        return re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        ) is not None
    return name.casefold() in text.casefold()


def _check_frame(
    *,
    raw_path: object,
    expected_sha256: str | None,
    target_ratio: float | None,
    project_root: Path | None,
    group_id: str,
    segment_id: str,
    field: str,
    blockers: list[dict[str, Any]],
) -> None:
    path = _frame_path(raw_path, project_root)
    if path is None:
        blockers.append(_issue("frame_path_unverifiable", group_id, segment_id, field, "Frame path is missing or escapes the supplied project root."))
        return
    try:
        payload = path.read_bytes()
    except OSError:
        blockers.append(_issue("frame_unreadable", group_id, segment_id, field, "Frame file could not be read."))
        return
    actual_sha = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is None or actual_sha != expected_sha256:
        blockers.append(_issue("frame_hash_mismatch", group_id, segment_id, field, "Frame bytes do not match the prepared-context SHA-256."))
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except (OSError, UnidentifiedImageError, ValueError):
        blockers.append(_issue("frame_not_decodable", group_id, segment_id, field, "Frame is not a decodable image."))
        return
    if target_ratio is not None:
        actual_ratio = width / height if height else 0
        if actual_ratio <= 0 or abs(actual_ratio / target_ratio - 1) > 0.03:
            blockers.append(_issue("frame_aspect_ratio_mismatch", group_id, segment_id, field, "Frame aspect ratio differs from the requested output by more than 3%."))


def _evaluate_group(
    *,
    group: object,
    target_ratio: float | None,
    character_names: tuple[str, ...],
    project_root: Path | None,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(group, dict):
        blockers.append(_issue("invalid_group", None, None, "groups", "Each group must contain context and prepared objects."))
        return None
    context_payload = group.get("context")
    prepared_payload = group.get("prepared")
    if not isinstance(context_payload, dict) or not isinstance(prepared_payload, dict):
        blockers.append(_issue("missing_group_inputs", None, None, "groups", "Each group requires public prepared-context data and a prepared plan package."))
        return None

    raw_pins = context_payload.get("pins")
    group_id = raw_pins.get("group_id") if isinstance(raw_pins, dict) else None
    if not isinstance(group_id, str) or not group_id:
        group_id = "unknown"
    try:
        pins = H3PreparedPlanPins.model_validate(raw_pins)
        prepared = H3PreparedPlanImport.model_validate(prepared_payload)
    except ValidationError:
        blockers.append(_issue("invalid_prepared_envelope", group_id, None, "prepared", "Prepared context pins or plan import envelope failed schema validation."))
        return None

    if context_payload.get("schema_version") != 1:
        blockers.append(_issue("unsupported_context_schema", group_id, None, "context.schema_version", "Only prepared-context schema version 1 is supported."))
    if context_payload.get("workflow_id") != pins.workflow_id:
        blockers.append(_issue("context_workflow_mismatch", group_id, None, "context.workflow_id", "Workflow ID does not match the pinned workflow."))
    if prepared.pins != pins:
        blockers.append(_issue("pin_mismatch", group_id, None, "prepared.pins", "Prepared plan pins differ from the live prepared-context pins."))
    if prepared.preparation_sha256 != context_payload.get("preparation_sha256"):
        blockers.append(_issue("preparation_digest_mismatch", group_id, None, "prepared.preparation_sha256", "Prepared plan digest differs from the live prepared-context digest."))

    context_segments = context_payload.get("segments")
    if not isinstance(context_segments, list) or not context_segments:
        blockers.append(_issue("missing_context_segments", group_id, None, "context.segments", "Prepared context contains no ordered segments."))
        return {"group_id": group_id, "segment_count": 0, "identity_reference_count": 0, "conditioning": "unknown"}
    raw_reuse_snapshot = context_payload.get("source_reuse_snapshot")
    if raw_reuse_snapshot is None:
        if pins.source_reuse_digest != EMPTY_H3_SOURCE_REUSE_DIGEST:
            blockers.append(_issue("source_reuse_snapshot_missing", group_id, None, "context.source_reuse_snapshot", "A non-empty source-reuse pin requires its prepared snapshot."))
    else:
        try:
            reuse_snapshot = VideoRepairSnapshot.model_validate(raw_reuse_snapshot)
        except ValidationError:
            reuse_snapshot = None
            blockers.append(_issue("invalid_source_reuse_snapshot", group_id, None, "context.source_reuse_snapshot", "Source-reuse snapshot failed schema or digest validation."))
        if reuse_snapshot is not None:
            if pins.source_reuse_digest != reuse_snapshot.snapshot_sha256:
                blockers.append(_issue("source_reuse_digest_mismatch", group_id, None, "pins.source_reuse_digest", "Source-reuse pin differs from the prepared snapshot digest."))
            if (
                reuse_snapshot.group_id != pins.group_id
                or reuse_snapshot.episode != pins.episode
                or reuse_snapshot.director_revision_id != pins.director_revision_id
            ):
                blockers.append(_issue("source_reuse_scope_mismatch", group_id, None, "context.source_reuse_snapshot", "Source-reuse snapshot scope differs from the prepared group pins."))
            prior_source_ids = tuple(
                shot_id
                for entry in reuse_snapshot.prior_entries
                for shot_id in entry.source_shot_ids
            )
            repaired_entries = tuple(
                entry for entry in reuse_snapshot.prior_entries if not entry.reuse
            )
            repaired_source_ids = tuple(
                shot_id
                for entry in repaired_entries
                for shot_id in entry.source_shot_ids
            )
            context_source_ids = tuple(
                shot_id
                for item in context_segments
                if isinstance(item, dict)
                for shot_id in item.get("source_shot_ids", ())
                if isinstance(shot_id, str)
            )
            context_repair_segment_ids = tuple(
                item.get("segment_id")
                for item in context_segments
                if isinstance(item, dict)
            )
            if (
                prior_source_ids != reuse_snapshot.expected_source_shot_ids
                or len(repaired_source_ids) != len(reuse_snapshot.repair_shot_ids)
                or set(repaired_source_ids) != set(reuse_snapshot.repair_shot_ids)
                or len(context_source_ids) != len(reuse_snapshot.repair_shot_ids)
                or set(context_source_ids) != set(reuse_snapshot.repair_shot_ids)
                or context_repair_segment_ids
                != tuple(entry.segment_id for entry in repaired_entries)
            ):
                blockers.append(_issue("source_reuse_scope_mismatch", group_id, None, "context.segments", "Prepared segments must contain exactly the source shots and entries selected for repair."))
    context_ids = [item.get("segment_id") for item in context_segments if isinstance(item, dict)]
    prepared_ids = [item.segment_id for item in prepared.segments]
    if len(context_ids) != len(context_segments) or context_ids != prepared_ids:
        blockers.append(_issue("segment_order_mismatch", group_id, None, "prepared.segments", "Prepared plan segment IDs must exactly match the ordered context segments."))

    digest_segments: list[tuple[str, str]] = []
    for item in prepared.segments:
        digest_segments.append((item.segment_id, item.input_hash))
    try:
        recomputed_digest = prepared_h3_context_digest(pins, digest_segments)
        if recomputed_digest != prepared.preparation_sha256:
            blockers.append(_issue("preparation_digest_mismatch", group_id, None, "prepared.preparation_sha256", "Digest does not match the pinned ordered segment input hashes."))
    except (TypeError, ValueError):
        blockers.append(_issue("preparation_digest_unverifiable", group_id, None, "prepared.preparation_sha256", "Preparation digest could not be recomputed."))

    prepared_by_id = {item.segment_id: item for item in prepared.segments}
    selected_reference_count = 0
    identity_reference_count = 0
    workflow_id = pins.workflow_id
    for context_segment in context_segments:
        if not isinstance(context_segment, dict):
            continue
        segment_id = context_segment.get("segment_id")
        if not isinstance(segment_id, str):
            continue
        imported_segment = prepared_by_id.get(segment_id)
        if imported_segment is None:
            continue
        if imported_segment.input_hash != context_segment.get("input_hash"):
            blockers.append(_issue("segment_input_hash_mismatch", group_id, segment_id, "input_hash", "Prepared segment input hash differs from the current context."))
        try:
            source_segment = H3DirectorSegment.model_validate(context_segment.get("source_segment"))
            compiler_context = H3PromptContext.model_validate(context_segment.get("compiler_context"))
            plan = H3DirectorPlan.model_validate(imported_segment.director_plan)
            mode = H3Mode(context_segment.get("mode"))
        except (ValidationError, ValueError, TypeError):
            blockers.append(_issue("invalid_segment_context", group_id, segment_id, "context.segments", "Source segment, compiler context, mode, or director plan failed schema validation."))
            continue

        expected_mode = context_segment.get("mode")
        if plan.mode.value != expected_mode:
            blockers.append(_issue("plan_mode_mismatch", group_id, segment_id, "director_plan.mode", "Director plan mode differs from the prepared context."))
        expected_duration = context_segment.get("duration_seconds")
        expected_frames = context_segment.get("total_frames")
        if not isinstance(expected_duration, int | float) or not math.isfinite(expected_duration):
            blockers.append(_issue("invalid_context_timing", group_id, segment_id, "duration_seconds", "Prepared duration is not finite numeric timing."))
            continue
        frames_from_duration = round(float(expected_duration) * plan.fps)
        if source_segment.duration_seconds != expected_duration or plan.total_frames != expected_frames or plan.total_frames != frames_from_duration:
            blockers.append(_issue("plan_timing_mismatch", group_id, segment_id, "director_plan.total_frames", "Source duration, authoritative frame count, and plan duration must agree at 24 fps."))

        first_sha = context_segment.get("first_frame_sha256")
        last_sha = context_segment.get("last_frame_sha256")
        if compiler_context.first_frame_sha256 != first_sha or compiler_context.last_frame_sha256 != last_sha:
            blockers.append(_issue("compiler_frame_hash_mismatch", group_id, segment_id, "compiler_context", "Compiler-context frame hashes differ from the prepared context."))
        first_anchor = plan.first_frame_anchor
        last_anchor = plan.last_frame_anchor
        requires_first_anchor = mode in {H3Mode.I2VA, H3Mode.FL2VA}
        requires_last_anchor = mode in {H3Mode.FL2VA, H3Mode.L2VA}
        if requires_first_anchor and (first_anchor is None or first_anchor.sha256 != first_sha):
            blockers.append(_issue("plan_frame_anchor_mismatch", group_id, segment_id, "director_plan.first_frame_anchor", "Plan first-frame anchor does not match the current prepared frame."))
        if not requires_first_anchor and first_anchor is not None:
            blockers.append(_issue("plan_frame_anchor_mismatch", group_id, segment_id, "director_plan.first_frame_anchor", "Plan contains a first-frame anchor forbidden by the selected H3 mode."))
        if requires_last_anchor and last_sha is not None and (last_anchor is None or last_anchor.sha256 != last_sha):
            blockers.append(_issue("plan_frame_anchor_mismatch", group_id, segment_id, "director_plan.last_frame_anchor", "Plan last-frame anchor does not match the current prepared frame."))
        if requires_last_anchor and last_sha is None:
            blockers.append(_issue("plan_frame_anchor_mismatch", group_id, segment_id, "director_plan.last_frame_anchor", "Selected H3 mode requires a last-frame anchor but the prepared context has no last frame."))
        if not requires_last_anchor and last_anchor is not None:
            blockers.append(_issue("plan_frame_anchor_mismatch", group_id, segment_id, "director_plan.last_frame_anchor", "Plan contains a last-frame anchor absent from the prepared context."))
        _check_frame(
            raw_path=source_segment.first_frame,
            expected_sha256=first_sha,
            target_ratio=target_ratio,
            project_root=project_root,
            group_id=group_id,
            segment_id=segment_id,
            field="source_segment.first_frame",
            blockers=blockers,
        )
        if last_sha is not None:
            _check_frame(
                raw_path=source_segment.last_frame,
                expected_sha256=last_sha,
                target_ratio=target_ratio,
                project_root=project_root,
                group_id=group_id,
                segment_id=segment_id,
                field="source_segment.last_frame",
                blockers=blockers,
            )
        elif source_segment.last_frame is not None:
            blockers.append(_issue("frame_binding_mismatch", group_id, segment_id, "source_segment.last_frame", "Source segment has a last frame not present in the prepared context."))

        actual_active_ids = set(compiler_context.active_character_ids)
        known_folded = {name.casefold() for name in character_names}
        scene_active = set(plan.rigid_prompt.scene_context.active_characters) if plan.rigid_prompt else set()
        if plan.rigid_prompt is not None:
            scene = plan.rigid_prompt.scene_context
            if scene_active != actual_active_ids or scene.exact_character_count != len(actual_active_ids):
                blockers.append(_issue("active_character_inventory_mismatch", group_id, segment_id, "rigid_prompt.scene_context", "Plan active-character names/count differ from the prepared compiler context."))
            named_active_entities = set(scene_active)
            named_active_entities.update(
                item.character_id for item in plan.rigid_prompt.character_acting
            )
            named_active_entities.update(
                subject.character_id
                for blocking in plan.rigid_prompt.spatial_blocking
                for subject in blocking.subjects
            )
            if actual_active_ids and not character_names:
                blockers.append(_issue("missing_character_inventory", group_id, segment_id, "character_names", "Named active characters cannot be checked because the bundle has no character inventory."))
            for active_name in named_active_entities:
                if active_name not in actual_active_ids:
                    blockers.append(_issue("unknown_active_character", group_id, segment_id, "rigid_prompt.active_character_names", "Plan names an active character absent from the prepared compiler context."))
                if character_names and active_name.casefold() not in known_folded:
                    blockers.append(_issue("unknown_active_character", group_id, segment_id, "rigid_prompt.scene_context.active_characters", "Plan names an active character absent from the supplied character inventory."))
            physics_statements = plan.rigid_prompt.physics.statements
            quality_requirements = plan.rigid_prompt.quality.requirements
            for known_name in character_names:
                if known_name.casefold() in {active.casefold() for active in actual_active_ids}:
                    continue
                if any(_text_has_name(text, known_name) for text in (*physics_statements, *quality_requirements)):
                    blockers.append(_issue("inactive_character_leak", group_id, segment_id, "rigid_prompt.physics_or_quality", "An inactive named character appears in positive physics or quality requirements."))
            character_constraints = [constraint for constraint in plan.rigid_prompt.positive_constraints if constraint.target == "characters"]
            if character_constraints and any(constraint.count is not None and constraint.count != len(actual_active_ids) for constraint in character_constraints):
                blockers.append(_issue("positive_character_count_mismatch", group_id, segment_id, "rigid_prompt.positive_constraints", "Positive character-count constraints differ from the prepared active-character inventory."))

        selected_facts = tuple(compiler_context.resolved_references)
        selected_tags = {fact.tag for fact in selected_facts}
        plan_tags = {reference.tag for reference in plan.rigid_prompt.active_references} if plan.rigid_prompt else set()
        if not plan_tags.issubset(selected_tags):
            blockers.append(_issue("plan_reference_not_selected", group_id, segment_id, "rigid_prompt.active_references", "Plan references include tags absent from the prepared compiler context."))
        if selected_facts and pins.reference_binding_sha256 == EMPTY_H3_REFERENCE_BINDING_SHA256:
            blockers.append(_issue("reference_binding_missing", group_id, segment_id, "pins.reference_binding_sha256", "Selected image references require a non-empty pinned reference binding."))
        if not selected_facts and pins.reference_binding_sha256 != EMPTY_H3_REFERENCE_BINDING_SHA256:
            blockers.append(_issue("reference_binding_mismatch", group_id, segment_id, "pins.reference_binding_sha256", "Reference binding is non-empty but the prepared compiler context selects no references."))
        if mode is H3Mode.REF2VA:
            if not selected_facts:
                blockers.append(_issue("ref2va_without_selected_references", group_id, segment_id, "compiler_context.resolved_references", "REF2VA requires selected references in the prepared compiler context."))
            selected_subjects = tuple(
                int(match.group(1))
                for fact in selected_facts
                if (match := re.fullmatch(r"<Subject ([1-9][0-9]*)>", fact.provider_subject))
            )
            plan_subjects = tuple(subject.subject_index for subject in plan.reference_subjects)
            if selected_subjects != plan_subjects:
                blockers.append(_issue("plan_reference_subject_mismatch", group_id, segment_id, "director_plan.reference_subjects", "Plan subject indexes must map one-to-one, in order, to the selected provider references."))
        if pins.workflow_id == "runninghub:minimax-h3-ref" and not selected_facts:
            blockers.append(_issue("h3_ref_without_selected_references", group_id, segment_id, "compiler_context.resolved_references", "The H3 reference workflow requires at least one selected reference."))
        selected_reference_count += len(selected_facts)
        identity_reference_count += sum(fact.kind == "character" for fact in selected_facts)
        try:
            result = compile_and_gate_h3_plan(
                plan,
                segment=source_segment,
                context=compiler_context,
                mode=mode,
                input_hash=imported_segment.input_hash,
            )
        except Exception:
            blockers.append(_issue("compiler_gate_failed", group_id, segment_id, "director_plan", "Local H3 compiler or quality gate rejected this plan."))
        else:
            for quality_issue in result.quality_report.issues:
                item = _issue(quality_issue.code, group_id, segment_id, quality_issue.field or "director_plan", quality_issue.message)
                if quality_issue.severity == "warning":
                    warnings.append(item)
                else:
                    blockers.append(item)

    if selected_reference_count == 0:
        warnings.append(_issue("base_frame_only_conditioning", group_id, None, "compiler_context.resolved_references", "No selected identity or other image references are recorded; conditioning is limited to the bound first/last frames."))
    conditioning = "frame_and_selected_references" if selected_reference_count else "first_last_frames_only"
    return {
        "group_id": group_id,
        "workflow_id": workflow_id,
        "segment_count": len(context_segments),
        "selected_reference_count": selected_reference_count,
        "identity_reference_count": identity_reference_count,
        "conditioning": conditioning,
    }


def evaluate_bundle(bundle: object, project_root: Path | None = None) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(bundle, dict):
        return {"ok": False, "blockers": [_issue("invalid_bundle", None, None, "bundle", "Bundle root must be a JSON object.")], "warnings": [], "groups": []}
    target_ratio = _aspect_ratio(bundle.get("target_aspect_ratio"))
    if target_ratio is None:
        blockers.append(_issue("invalid_target_aspect_ratio", None, None, "target_aspect_ratio", "Target aspect ratio must use a positive W:H value."))
    raw_groups = bundle.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        blockers.append(_issue("missing_groups", None, None, "groups", "Bundle must contain at least one group."))
        raw_groups = []
    groups = []
    names = _character_names(bundle)
    for group in raw_groups:
        result = _evaluate_group(
            group=group,
            target_ratio=target_ratio,
            character_names=names,
            project_root=project_root,
            blockers=blockers,
            warnings=warnings,
        )
        if result is not None:
            groups.append(result)
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "groups": groups}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="JSON file containing prepared context and prepared plan packages")
    parser.add_argument("--project-root", type=Path, help="Resolve relative frame paths under this directory")
    args = parser.parse_args(argv)
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = {"ok": False, "blockers": [_issue("bundle_unreadable", None, None, "bundle", "Bundle file could not be read as UTF-8 JSON.")], "warnings": [], "groups": []}
    else:
        try:
            report = evaluate_bundle(bundle, args.project_root)
        except Exception:
            report = {"ok": False, "blockers": [_issue("preflight_error", None, None, "bundle", "Preflight could not safely evaluate the supplied bundle.")], "warnings": [], "groups": []}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
