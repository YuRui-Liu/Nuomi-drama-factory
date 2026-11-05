"""Canvas operations backed by the project's canonical narrative-group services."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ..models import OrchestrationError, bound_narrative_group_reference, fingerprint

OPERATIONS = {
    ("narrativeGroupStoryboardNode", "render"),
    ("narrativeGroupStoryboardNode", "retry_render"),
    ("narrativeGroupVideoNode", "video"),
    ("narrativeGroupVideoNode", "retry_video"),
    ("episodeComposeNode", "compose"),
}

_SAFE_GROUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{32}$")


def _bound(data: dict[str, Any]) -> dict[str, Any]:
    reference = bound_narrative_group_reference({"data": data})
    if reference is None:
        raise OrchestrationError(
            "invalid_input",
            "A positive episodeNumber, groupId, and directorRevisionId are required",
        )
    if not _SAFE_GROUP_ID.fullmatch(reference["groupId"]):
        raise OrchestrationError("invalid_input", "Narrative group ID is malformed")
    if not _SAFE_REVISION_ID.fullmatch(reference["directorRevisionId"]):
        raise OrchestrationError("invalid_input", "Director revision ID is malformed")
    return reference


def _validate_episode_group_ids(values: Any) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > 64:
        raise OrchestrationError(
            "invalid_input", "productionGroupIds must be a non-empty list of at most 64 group IDs",
        )
    group_ids: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise OrchestrationError("invalid_input", "Production group IDs must be strings")
        group_id = value.strip()
        if not _SAFE_GROUP_ID.fullmatch(group_id):
            raise OrchestrationError("invalid_input", "Production group ID is malformed")
        group_ids.append(group_id)
    if len(group_ids) != len(set(group_ids)):
        raise OrchestrationError("invalid_input", "productionGroupIds cannot contain duplicates")
    return group_ids


def _resolve_group_from_disk(project_dir: Path, episode: int, group_id: str):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.narrative_groups.service import load_groups

    active = DirectorPlanStore(project_dir).load_active(episode)
    group = next((item for item in load_groups(project_dir, episode) if item.id == group_id), None)
    if active is None or group is None or group.director_revision_id != active.revision_id:
        return None
    return group


def _file_receipt(root: Path, raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return {
        "path": path.resolve().as_posix(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _render_reuse_matches_saved_selection(reference_audit: Any, data: dict[str, Any]) -> bool:
    """Only admit a render as free-reusable when its frozen selection is known."""
    saved = reference_audit.get("reference_selection") if isinstance(reference_audit, dict) else None
    selected_ids = data.get("selectedBindingIds")
    selected_upload_ids = data.get("selectedUploadIds", [])
    reference_revision = data.get("referenceRevision")
    if not isinstance(saved, dict):
        return False
    if (
        not isinstance(selected_ids, list)
        or any(not isinstance(item, str) or not item or item != item.strip() for item in selected_ids)
        or len(selected_ids) != len(set(selected_ids))
        or not isinstance(selected_upload_ids, list)
        or any(not isinstance(item, str) or not _SAFE_UPLOAD_ID.fullmatch(item)
               for item in selected_upload_ids)
        or len(selected_upload_ids) != len(set(selected_upload_ids))
        or not isinstance(reference_revision, str)
        or not reference_revision.strip()
    ):
        # Legacy canvas nodes and old render audits do not prove which inputs
        # produced the image. Keep them on the authorized generation path.
        return False
    saved_ids = saved.get("selected_binding_ids")
    saved_upload_ids = saved.get("upload_ids")
    return (
        isinstance(saved_ids, list)
        and saved_ids == selected_ids
        and isinstance(saved_upload_ids, list)
        and saved_upload_ids == selected_upload_ids
        and saved.get("reference_revision") == reference_revision
    )


def _render_reuse_matches_preview(
    reference_audit: Any,
    selected_binding_ids: list[str],
    reference_revision: str,
    preview_data: dict[str, Any],
    selected_upload_ids: list[str] | None = None,
) -> bool:
    """Verify the cached render's exact selected versions against the live preview."""
    saved = reference_audit.get("reference_selection") if isinstance(reference_audit, dict) else None
    if not isinstance(saved, dict):
        return False
    if (
        saved.get("selected_binding_ids") != selected_binding_ids
        or saved.get("upload_ids") != (selected_upload_ids or [])
        or saved.get("reference_revision") != reference_revision
    ):
        return False
    raw_bindings = preview_data.get("bindings")
    saved_bindings = saved.get("bindings")
    if not isinstance(raw_bindings, list) or not isinstance(saved_bindings, list):
        return False
    by_id = {
        item.get("binding_id"): item
        for item in raw_bindings
        if isinstance(item, dict) and isinstance(item.get("binding_id"), str)
    }
    saved_by_id = {
        item.get("binding_id"): item
        for item in saved_bindings
        if isinstance(item, dict) and isinstance(item.get("binding_id"), str)
    }
    if len(saved_by_id) != len(saved_bindings):
        return False
    for binding_id in selected_binding_ids:
        item = by_id.get(binding_id)
        frozen = saved_by_id.get(binding_id)
        if not isinstance(item, dict) or not isinstance(frozen, dict):
            return False
        if (
            frozen.get("version_id") != item.get("version_id")
            or frozen.get("asset_slot_id") != item.get("asset_slot_id")
        ):
            return False
        # The public preview intentionally strips filesystem paths and content
        # hashes. Its revision is computed from the complete resolved bindings,
        # including both values; compare them directly too when an internal
        # preview supplies those fields.
        if "sha256" in item and frozen.get("sha256") != item.get("sha256"):
            return False
        if "relative_path" in item and frozen.get("relative_path") != item.get("relative_path"):
            return False
    return True


def _selected_upload_ids(data: dict[str, Any]) -> list[str]:
    value = data.get("selectedUploadIds", [])
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not _SAFE_UPLOAD_ID.fullmatch(item) for item in value)
            or len(value) != len(set(value))):
        raise OrchestrationError("invalid_input", "Saved storyboard upload IDs are malformed")
    return list(value)


def _validate_selected_uploads(project_dir: Path, upload_ids: list[str], preview_data: dict[str, Any]) -> None:
    """Resolve every saved temporary upload against the current project files."""
    if not upload_ids:
        return
    manifest = preview_data.get("upload_manifest", preview_data.get("uploads"))
    if manifest is not None:
        if not isinstance(manifest, list):
            raise OrchestrationError("source_conflict", "Storyboard upload manifest is invalid")
        manifest_ids = {
            item.get("upload_id", item.get("id"))
            for item in manifest if isinstance(item, dict)
        }
        if not set(upload_ids).issubset(manifest_ids):
            raise OrchestrationError(
                "source_conflict", "A saved storyboard upload is no longer available for this render",
            )
    from novelvideo.narrative_groups.reference_uploads import (
        InvalidReferenceUpload, load_reference_upload, validate_reference_image,
    )

    allowed_root = Path(project_dir).resolve(strict=False) / ".runtime" / "reference_uploads"
    for upload_id in upload_ids:
        upload = load_reference_upload(project_dir, upload_id)
        if upload is None or not upload.temporary:
            raise OrchestrationError(
                "source_conflict", "A saved storyboard upload is no longer available for this render",
            )
        try:
            validate_reference_image(
                upload.image_path, allowed_roots=(allowed_root,), expected_mime=upload.mime_type,
            )
        except (InvalidReferenceUpload, OSError, ValueError) as exc:
            raise OrchestrationError(
                "source_conflict", "A saved storyboard upload is no longer a safe image",
            ) from exc


def reuse_snapshot(project_dir: Path, node: dict, operation: str) -> dict[str, Any] | None:
    """Freeze the exact completed group output eligible for free reuse."""
    data = node.get("data") or {}
    try:
        reference = _bound(data)
        group = _resolve_group_from_disk(
            Path(project_dir), reference["episodeNumber"], reference["groupId"],
        )
        if group is None or group.director_revision_id != reference["directorRevisionId"]:
            return None
        if operation == "render":
            stage = group.stages.get("render")
            asset = getattr(stage, "grid_asset", "") if stage else ""
            if not (stage and stage.status == "completed" and not stage.needs_regeneration):
                return None
            reference_audit = stage.provider_parameters.get("reference_audit", {})
            if not _render_reuse_matches_saved_selection(reference_audit, data):
                return None
            asset_receipt = _file_receipt(Path(project_dir), asset)
            if asset_receipt is None:
                return None
            signature_data = {
                "stage": "render", "revision": stage.revision,
                "director_revision_id": group.director_revision_id,
                "asset": asset_receipt,
                "selected_storyboard_id": stage.selected_storyboard_id,
                "provider": stage.actual_provider, "model": stage.actual_model,
                "image_size": stage.actual_pixel_size or stage.requested_pixel_size,
                "reference_audit": reference_audit,
            }
        elif operation == "video":
            stage = group.stages.get("video")
            render = group.stages.get("render")
            asset = getattr(stage, "video_asset", "") if stage else ""
            manifest = getattr(stage, "manifest_asset", "") if stage else ""
            if not (stage and render and stage.status == "completed"
                    and not stage.needs_regeneration and render.status == "completed"
                    and not render.needs_regeneration
                    and stage.source_storyboard_id in ("", render.selected_storyboard_id)):
                return None
            asset_receipt = _file_receipt(Path(project_dir), asset)
            manifest_receipt = _file_receipt(Path(project_dir), manifest)
            if asset_receipt is None or manifest_receipt is None:
                return None
            from novelvideo.media_capabilities.video.h3_timeline import (
                DialogueSource, load_h3_director_manifest,
            )

            manifest_path = Path(str(manifest))
            if not manifest_path.is_absolute():
                manifest_path = Path(project_dir) / manifest_path
            output_manifest = load_h3_director_manifest(manifest_path)
            dialogue_sources = tuple(entry.dialogue_source.value for entry in output_manifest.entries)
            requested_dialogue_source = str(
                data.get("dialogueSource") or data.get("dialogue_source") or ""
            ).strip()
            if requested_dialogue_source and any(
                source != requested_dialogue_source for source in dialogue_sources
            ):
                return None
            if (requested_dialogue_source == DialogueSource.H3_NATIVE.value
                    or any(entry.dialogue_source is DialogueSource.H3_NATIVE
                           for entry in output_manifest.entries)):
                from novelvideo.task_backend.runners.video import _video_has_audio_stream

                native_video = Path(output_manifest.physical_video or "")
                if not native_video.is_absolute():
                    native_video = Path(project_dir) / native_video
                if not _video_has_audio_stream(native_video):
                    return None
            signature_data = {
                "stage": "video", "revision": stage.revision,
                "director_revision_id": group.director_revision_id,
                "asset": asset_receipt, "manifest": manifest_receipt,
                "render_revision": render.revision,
                "selected_storyboard_id": render.selected_storyboard_id,
                "source_storyboard_id": stage.source_storyboard_id,
                "plan_revision": group.video_plan.revision,
                "settings_revision": group.video_settings.revision,
                "reference_revision": group.video_reference_settings.revision,
                "dialogue_source": stage.dialogue_source,
                "manifest_dialogue_source": (
                    output_manifest.dialogue_source.value
                    if output_manifest.dialogue_source is not None else None
                ),
                "dialogue_sources": dialogue_sources,
                "workflow_id": group.video_settings.workflow_id,
                "workflow_parameters": stage.workflow_parameters,
                "provider_parameters": stage.provider_parameters,
                "provider": stage.actual_provider, "model": stage.actual_model,
                "mode": stage.actual_mode,
            }
        else:
            return None
        return {
            "signature": fingerprint(signature_data),
            "stage": signature_data["stage"],
            "group": group,
        }
    except (OSError, ValueError, TypeError, OrchestrationError):
        return None


def can_reuse(project_dir: Path, node: dict, operation: str) -> bool:
    """Recognize successful current group output before charging a node run."""
    return reuse_snapshot(project_dir, node, operation) is not None


def _existing_asset(root: Path, raw: str) -> bool:
    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    return path.is_file()


def _group_result(project: str, root: Path, episode: int, group, stage_name: str) -> dict[str, Any]:
    from novelvideo.api.routes.narrative_groups import _asset_url

    stage = group.stages[stage_name]
    render = group.stages["render"]
    result: dict[str, Any] = {
        "episodeNumber": episode,
        "groupId": group.id,
        "directorRevisionId": group.director_revision_id,
        "stage": stage_name,
        "revision": stage.revision,
        "renderRevision": render.revision,
        "selectedStoryboardId": render.selected_storyboard_id,
        "videoPlanRevision": group.video_plan.revision,
        "videoSettingsRevision": group.video_settings.revision,
        "videoReferenceRevision": group.video_reference_settings.revision,
        "actualProvider": stage.actual_provider or None,
        "actualModel": stage.actual_model or None,
        "actualMode": stage.actual_mode or None,
        "dialogueSource": stage.dialogue_source or None,
        "workflowParameters": dict(stage.workflow_parameters or {}),
        "constraintMode": stage.constraint_mode or None,
        "requestedImageSize": stage.requested_image_size or None,
        "actualPixelSize": stage.actual_pixel_size or None,
    }
    if stage_name == "video":
        result["workflowId"] = group.video_settings.workflow_id
        result["referenceSnapshotId"] = stage.provider_parameters.get("reference_snapshot_id")
        result["referenceSnapshotDigest"] = stage.provider_parameters.get("reference_snapshot_digest")
    asset = stage.grid_asset if stage_name == "render" else stage.video_asset
    if asset:
        result["assetUrl"] = _asset_url(project, root, asset)
    if stage_name == "video" and stage.manifest_asset:
        result["manifestRevision"] = fingerprint({
            "video_revision": stage.revision,
            "manifest_asset": stage.manifest_asset,
        })
    return result


def _stage_media_result(project: str, root: Path, episode: int, group, stage_name: str) -> dict[str, Any]:
    receipt = _group_result(project, root, episode, group, stage_name)
    url = receipt.get("assetUrl")
    result = {"narrativeGroupResult": receipt}
    if stage_name == "render":
        result["imageUrl"] = url
    else:
        result["videoUrl"] = url
    return result


async def _resolve_bound_group(run: dict, data: dict[str, Any]):
    from novelvideo.api.routes.narrative_groups import _resolve_groups
    from novelvideo.director_plan.store import DirectorPlanStore

    reference = _bound(data)
    resolved, groups, _ = await _resolve_groups(
        run["project_id"], reference["episodeNumber"], run["actor"],
    )
    active = DirectorPlanStore(resolved.project_dir).load_active(reference["episodeNumber"])
    group = next((item for item in groups if item.id == reference["groupId"]), None)
    if (active is None or active.revision_id != reference["directorRevisionId"]
            or group is None or group.director_revision_id != reference["directorRevisionId"]):
        raise OrchestrationError(
            "source_conflict", "The bound group is not part of the fixed active director revision",
        )
    return reference, resolved, group


def _task_submission(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") == "submission_unknown" or not data.get("task_id"):
        raise OrchestrationError(
            "submission_unknown",
            "A durable group reservation exists but its original task cannot be recovered",
            scope=data.get("scope"),
        )
    return data


def _compose_request(data: dict[str, Any], attempt_id: str):
    from novelvideo.api.schemas import VideoComposeRequest

    values = {
        "client_mutation_id": attempt_id,
        "director_revision_id": data.get("directorRevisionId"),
        "production_group_ids": _validate_episode_group_ids(data.get("productionGroupIds")),
        "add_subtitles": data.get("addSubtitles", True),
        "add_bgm": data.get("addBgm", False),
    }
    if "resolution" in data and data["resolution"] is not None:
        values["resolution"] = data["resolution"]
    return VideoComposeRequest(**values)


def _compose_snapshot(project_dir: Path, episode: int) -> dict[str, Any]:
    from novelvideo.narrative_groups.service import load_materialized_groups
    from novelvideo.task_backend.runners.video import director_composition_snapshot

    return director_composition_snapshot(
        project_dir, episode,
        groups=load_materialized_groups(project_dir, episode),
        require_current=True,
    )


def _compose_request_fingerprint(episode: int, snapshot: dict[str, Any], body) -> str:
    return fingerprint({
        "episode": episode,
        "director_composition": snapshot,
        "body": body.model_dump(mode="json", exclude={"client_mutation_id"}),
    })


async def submit(run: dict, attempt: dict, node: dict, operation: str):
    from novelvideo.api.routes import generation
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.api.schemas import NarrativeReferenceResolutionRequest

    data = node.get("data") or {}
    kind = node.get("type")
    if kind == "episodeComposeNode":
        episode = data.get("episodeNumber")
        revision_id = data.get("directorRevisionId")
        group_ids = _validate_episode_group_ids(data.get("productionGroupIds"))
        if type(episode) is not int or episode <= 0 or not isinstance(revision_id, str) or not _SAFE_REVISION_ID.fullmatch(revision_id):
            raise OrchestrationError("invalid_input", "A fixed episodeNumber and directorRevisionId are required")
        body = _compose_request(data, attempt["id"])
        response = await generation.compose_video(run["project_id"], episode, body, run["actor"])
        if not response.get("task_id") or response.get("status") == "submission_unknown":
            raise OrchestrationError("submission_unknown", "Episode composition task receipt is unavailable")
        return {
            "narrative_group": True, "stage": "compose", "task_type": "compose_episode",
            "task_id": str(response["task_id"]), "scope": response.get("scope", ""),
            "episode": episode, "director_revision_id": revision_id,
            "group_ids": group_ids, "request_fingerprint": response.get("request_fingerprint"),
            "compose_request": body.model_dump(mode="json", exclude={"client_mutation_id"}),
        }

    reference, resolved, group = await _resolve_bound_group(run, data)
    episode, group_id = reference["episodeNumber"], reference["groupId"]
    if kind == "narrativeGroupStoryboardNode":
        stage_status = group.stages["render"].status
        if (attempt.get("explicit_retry") is True and operation == "render"
                and stage_status == "failed"):
            operation = "retry_render"
        retry_operation = operation == "retry_render"
        if retry_operation and stage_status != "failed":
            raise OrchestrationError("request_rejected", "retry_render requires a failed render stage")
        if not retry_operation and stage_status == "failed":
            raise OrchestrationError("request_rejected", "A failed render stage requires explicit retry_render")
    else:
        stage_status = group.stages["video"].status
        if (attempt.get("explicit_retry") is True and operation == "video"
                and stage_status == "failed"):
            operation = "retry_video"
        retry_operation = operation == "retry_video"
        if retry_operation and stage_status != "failed":
            raise OrchestrationError("request_rejected", "retry_video requires a failed video stage")
        if not retry_operation and stage_status == "failed":
            raise OrchestrationError("request_rejected", "A failed video stage requires explicit retry_video")

    current_reuse = reuse_snapshot(resolved.project_dir, node, operation)
    admission = attempt.get("narrative_group_admission")
    if isinstance(admission, dict) and admission.get("external") is False:
        if (current_reuse is None
                or current_reuse.get("signature") != admission.get("reuse_signature")):
            raise OrchestrationError(
                "source_conflict",
                "The completed group output changed after free-reuse admission",
            )
    if current_reuse is not None and kind != "narrativeGroupStoryboardNode":
        group = current_reuse["group"]
        return {"local": True, "result": _stage_media_result(
            run["project_id"], Path(resolved.project_dir), episode, group,
            "render" if kind == "narrativeGroupStoryboardNode" else "video",
        )}

    if kind == "narrativeGroupStoryboardNode":
        preview = await api.preview_group_references(
            run["project_id"], episode, group_id, "render", run["actor"],
        )
        preview_data = preview.get("data") or {}
        saved_selection = data.get("selectedBindingIds")
        selected_upload_ids = _selected_upload_ids(data)
        saved_reference_revision = data.get("referenceRevision")
        if saved_selection is None and saved_reference_revision is None:
            if selected_upload_ids:
                raise OrchestrationError(
                    "invalid_input",
                    "Saved storyboard uploads require selectedBindingIds and referenceRevision",
                )
            # Keep old canvases working with the historical automatic selection.
            selected_ids = []
            for item in preview_data.get("bindings", []):
                if not isinstance(item, dict):
                    continue
                binding_id = item.get("binding_id")
                version_id = item.get("version_id")
                if (
                    item.get("selected_by_default") is True
                    and item.get("status") == "ready"
                    and isinstance(binding_id, str)
                    and binding_id
                    and binding_id == binding_id.strip()
                    and isinstance(version_id, str)
                    and version_id
                    and version_id == version_id.strip()
                ):
                    selected_ids.append(binding_id)
        else:
            if (not isinstance(saved_selection, list)
                    or not isinstance(saved_reference_revision, str)
                    or not saved_reference_revision.strip()):
                raise OrchestrationError(
                    "invalid_input",
                    "Saved storyboard references require selectedBindingIds and referenceRevision",
                )
            if saved_reference_revision != preview_data.get("reference_revision"):
                raise OrchestrationError(
                    "source_conflict",
                    "Storyboard reference preview changed; reopen the node reference settings and save them again",
                )
            source_revision = preview_data.get("source_plan_revision_id")
            if source_revision and source_revision != reference["directorRevisionId"]:
                raise OrchestrationError(
                    "source_conflict",
                    "Storyboard reference preview does not match the node's fixed director revision",
                )
            if (any(not isinstance(binding_id, str) or not binding_id or binding_id != binding_id.strip()
                    for binding_id in saved_selection)
                    or len(set(saved_selection)) != len(saved_selection)):
                raise OrchestrationError("invalid_input", "Saved storyboard reference IDs are malformed")
            bindings = {
                item.get("binding_id"): item
                for item in preview_data.get("bindings", [])
                if isinstance(item, dict) and isinstance(item.get("binding_id"), str)
            }
            selected = [bindings.get(binding_id) for binding_id in saved_selection]
            if any(item is None or item.get("status") != "ready"
                   or not isinstance(item.get("version_id"), str)
                   or not item.get("version_id", "").strip() for item in selected):
                raise OrchestrationError(
                    "source_conflict",
                    "A saved storyboard reference is no longer ready; refresh references and save the node again",
                )
            if len(saved_selection) > int(preview_data.get("max_images") or 0):
                raise OrchestrationError("invalid_input", "Saved storyboard references exceed the image limit")
            selected_ids = list(saved_selection)
        max_images = int(preview_data.get("max_images") or 0)
        if len(selected_ids) + len(selected_upload_ids) > max_images:
            raise OrchestrationError("invalid_input", "Saved storyboard references exceed the image limit")
        _validate_selected_uploads(Path(resolved.project_dir), selected_upload_ids, preview_data)
        request = api.NarrativeGroupGenerationRequest(
            director_revision_id=reference["directorRevisionId"],
            allow_unconstrained=bool(data.get("allowUnconstrained", True)),
            reference_resolution=NarrativeReferenceResolutionRequest(
                selected_binding_ids=selected_ids,
                upload_ids=selected_upload_ids,
                reference_revision=preview_data.get("reference_revision", ""),
            ),
            client_mutation_id=attempt["id"],
        )
        if current_reuse is not None:
            cached_group = current_reuse["group"]
            cached_stage = cached_group.stages.get("render")
            reference_audit = (
                cached_stage.provider_parameters.get("reference_audit", {})
                if cached_stage is not None else {}
            )
            if _render_reuse_matches_preview(
                reference_audit,
                selected_ids,
                str(preview_data.get("reference_revision") or ""),
                preview_data,
                selected_upload_ids,
            ):
                return {"local": True, "result": _stage_media_result(
                    run["project_id"], Path(resolved.project_dir), episode, cached_group,
                    "render",
                )}
            if isinstance(admission, dict) and admission.get("external") is False:
                raise OrchestrationError(
                    "source_conflict",
                    "The completed storyboard no longer matches the current reference inputs",
                )
        if operation == "retry_render":
            response = await api._enqueue_group_action(
                run["project_id"], episode, group_id, "render", run["actor"],
                regenerate=True, explicit_retry=True, generation_request=request,
            )
        else:
            response = await api.generate_render_group(
                run["project_id"], episode, group_id, request, run["actor"],
            )
        queued = _task_submission(response.get("data") or {})
        return {
            "narrative_group": True, "stage": "render", "task_type": "narrative_group_grid",
            "task_id": str(queued["task_id"]), "scope": queued["scope"],
            "episode": episode, "group_id": group_id,
            "director_revision_id": reference["directorRevisionId"],
            "revision": int((queued.get("metadata") or {}).get("revision") or 0),
            "request_receipt": dict(queued.get("metadata") or {}),
        }

    from novelvideo.project_config import load_project_config_from_state_dir
    from typing import get_args
    from novelvideo.shot_continuity.models import H3RequestedMode

    config = load_project_config_from_state_dir(
        getattr(resolved.ctx, "state_dir", resolved.project_dir),
        username=getattr(resolved.ctx, "owner_username", ""),
        project=getattr(resolved.ctx, "project_name", ""),
    )
    mode = str(config.get("h3_mode") or "auto")
    if mode not in get_args(H3RequestedMode):
        mode = "auto"
    request = api.NarrativeGroupVideoRequest(
        model=group.video_settings.workflow_id,
        mode=mode,
        revision=group.stages["video"].revision,
        plan_revision=max(1, group.video_plan.revision),
        settings_revision=group.video_settings.revision,
        reference_revision=group.video_reference_settings.revision,
        **({"dialogue_source": data.get("dialogueSource") or data.get("dialogue_source")}
           if data.get("dialogueSource") or data.get("dialogue_source") else {}),
        client_mutation_id=attempt["id"],
    )
    media_store = api.get_media_capability_store()
    credential_resolver = api.get_media_credential_resolver()
    response = await api._enqueue_group_video(
        run["project_id"], episode, group_id, run["actor"], request,
        media_store, credential_resolver, explicit_retry=operation == "retry_video",
    )
    queued = _task_submission(response.get("data") or {})
    return {
        "narrative_group": True, "stage": "video", "task_type": "narrative_group_video",
        "task_id": str(queued["task_id"]), "scope": queued["scope"],
        "episode": episode, "group_id": group_id,
        "director_revision_id": reference["directorRevisionId"],
        "revision": int((queued.get("metadata") or {}).get("revision") or 0),
        "request_receipt": dict(queued.get("metadata") or {}),
    }


async def reconcile_submission(run: dict, attempt: dict, node: dict, operation: str):
    """Recover only persisted request receipts; absence remains unknown."""
    from novelvideo.api.routes import freezone
    from novelvideo.task_state import get_task_manager
    from novelvideo.api.routes import narrative_groups as api

    data = node.get("data") or {}
    if node.get("type") == "episodeComposeNode":
        episode = data.get("episodeNumber")
        if type(episode) is not int or episode <= 0:
            return {"status": "submission_unknown"}
        ctx, *_ = await freezone._resolve_freezone_project(
            run["project_id"], run["actor"], required_role="viewer",
        )
        scope = f"episode_{episode}_canvas_compose_{attempt['id']}"
        try:
            task = await asyncio.to_thread(
                get_task_manager().get_task_for_project,
                ctx, "compose_episode", episode, scope=scope,
            )
        except Exception:
            task = None
        if task is None:
            return {"status": "submission_unknown"}
        metadata = dict(task.metadata or {})
        try:
            body = _compose_request(data, attempt["id"])
            current = _compose_snapshot(Path(ctx.output_dir), episode)
            request_fingerprint = _compose_request_fingerprint(episode, current, body)
        except Exception:
            return {"status": "submission_unknown"}
        if metadata.get("request_fingerprint") != request_fingerprint:
            return {"status": "submission_unknown"}
        return {"status": "submitted", "submission": {
            "narrative_group": True, "stage": "compose", "task_type": "compose_episode",
            "task_id": str(task.task_id), "scope": scope, "episode": episode,
            "director_revision_id": data.get("directorRevisionId"),
            "group_ids": data.get("productionGroupIds") or [],
            "request_fingerprint": request_fingerprint,
            "compose_request": body.model_dump(mode="json", exclude={"client_mutation_id"}),
        }}

    try:
        reference = _bound(data)
        stage_name = "render" if node.get("type") == "narrativeGroupStoryboardNode" else "video"
        from novelvideo.narrative_groups.service import find_stage_submission
        resolved, _, _ = await api._resolve_groups(
            run["project_id"], reference["episodeNumber"], run["actor"],
        )
        record = await asyncio.to_thread(
            find_stage_submission, resolved.project_dir, reference["episodeNumber"],
            reference["groupId"], stage_name, attempt["id"],
        )
        if record is None:
            return {"status": "submission_unknown"}
        task_type = str(record.get("task_type") or "")
        scope = str(record.get("scope") or "")
        if not task_type or not scope:
            return {"status": "submission_unknown"}
        task = await asyncio.to_thread(
            get_task_manager().get_task_for_project,
            resolved.ctx, task_type, reference["episodeNumber"], scope=scope,
        )
    except Exception:
        return {"status": "submission_unknown"}
    if task is None:
        return {"status": "submission_unknown"}
    recorded_task_id = str(record.get("task_id") or "")
    if recorded_task_id and recorded_task_id != str(task.task_id):
        return {"status": "submission_unknown"}
    submission = {
        "narrative_group": True, "stage": stage_name, "task_type": task_type,
        "task_id": str(task.task_id), "scope": scope,
        "episode": reference["episodeNumber"], "group_id": reference["groupId"],
        "director_revision_id": reference["directorRevisionId"],
        "revision": int(record.get("revision") or 0),
        "request_receipt": dict(record.get("receipt") or {}),
    }
    return {"status": "submitted", "submission": submission}


async def reconcile(run: dict, submission: dict):
    from novelvideo.api.routes import freezone, narrative_groups as api
    from novelvideo.task_state import get_task_manager

    episode = int(submission["episode"])
    ctx, *_ = await freezone._resolve_freezone_project(
        run["project_id"], run["actor"], required_role="viewer",
    )
    task = await asyncio.to_thread(
        get_task_manager().get_task_for_project,
        ctx, submission["task_type"], episode, scope=submission["scope"],
    )
    if task is None:
        return {"status": "submission_unknown", "error": "Original task receipt is no longer available"}
    if str(getattr(task, "task_id", "")) != str(submission.get("task_id", "")):
        return {"status": "submission_unknown", "error": "Task scope now points to a different task receipt"}
    if task.status in {"queued", "running", "pending", "submitting", "retryable"}:
        return {"status": "running"}
    if task.status in {"failed", "cancelled", "canceled", "partial_failure", "review"}:
        return {"status": "failed", "error": task.error or f"Group operation ended: {task.status}"}
    result = task.result or {}
    if submission["stage"] == "compose":
        metadata = dict(task.metadata or {})
        if metadata.get("request_fingerprint") != submission.get("request_fingerprint"):
            return {"status": "submission_unknown", "error": "Composition task no longer matches its request receipt"}
        try:
            current = _compose_snapshot(Path(ctx.output_dir), episode)
        except Exception as exc:
            return {"status": "failed", "error": f"Current production group set cannot be verified: {exc}"}
        request_data = submission.get("compose_request")
        if not isinstance(request_data, dict):
            return {"status": "submission_unknown", "error": "Composition request body is missing from its receipt"}
        try:
            from novelvideo.api.schemas import VideoComposeRequest
            body = VideoComposeRequest.model_validate(request_data)
            current_fingerprint = _compose_request_fingerprint(episode, current, body)
        except Exception as exc:
            return {"status": "submission_unknown", "error": f"Composition receipt cannot be verified: {exc}"}
        if current_fingerprint != submission.get("request_fingerprint"):
            return {"status": "failed", "error": "Composition inputs changed since submission"}
        if (current["revision_id"] != submission["director_revision_id"]
                or current["group_ids"] != submission["group_ids"]):
            return {"status": "failed", "error": "Active director revision or complete group set changed"}
        if task.status != "completed":
            return {"status": "failed", "error": "Episode composition task did not complete"}
        output = result.get("video_path") or (result.get("result") or {}).get("video_path")
        if not output:
            return {"status": "failed", "error": "Composition completed without a final video path"}
        from novelvideo.api.routes.narrative_groups import _asset_url
        media_url = _asset_url(run["project_id"], Path(ctx.output_dir), str(output))
        return {"status": "succeeded", "result": {
            "resultVideoUrl": media_url,
            "narrativeGroupResult": {
                "episodeNumber": episode,
                "directorRevisionId": submission["director_revision_id"],
                "productionGroupIds": submission["group_ids"],
                "compositionScope": fingerprint([submission["scope"], submission["task_id"]]),
            },
        }}

    resolved, groups, _ = await api._resolve_groups(
        run["project_id"], episode, run["actor"],
    )
    group = next((item for item in groups if item.id == submission["group_id"]), None)
    if group is None or group.director_revision_id != submission["director_revision_id"]:
        return {"status": "failed", "error": "Group no longer belongs to the fixed director revision"}
    stage = group.stages[submission["stage"]]
    if int(stage.revision) != int(submission["revision"]):
        return {"status": "failed", "error": "Group stage revision changed after submission"}
    receipt = submission.get("request_receipt") or {}
    receipt_checks = []
    if submission["stage"] == "video":
        receipt_checks.extend((
            ("video_plan_revision", group.video_plan.revision),
            ("video_settings_revision", group.video_settings.revision),
            ("video_reference_revision", group.video_reference_settings.revision),
            ("render_revision", group.stages["render"].revision),
            ("selected_storyboard_id", group.stages["render"].selected_storyboard_id),
        ))
    for receipt_key, current in receipt_checks:
        if receipt_key in receipt and receipt[receipt_key] != current:
            return {"status": "failed", "error": f"Frozen group input changed: {receipt_key}"}
    if task.status != "completed" or stage.status != "completed" or stage.needs_regeneration:
        return {"status": "failed", "error": stage.error or "Group task has no current completed stage"}
    media = _stage_media_result(
        run["project_id"], Path(resolved.project_dir), episode, group, submission["stage"],
    )
    if media.get("narrativeGroupResult") is not None:
        media["narrativeGroupResult"]["submissionReceipt"] = receipt
    return {"status": "succeeded", "result": media}
