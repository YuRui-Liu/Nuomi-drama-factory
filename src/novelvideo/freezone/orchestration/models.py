"""Stable serialization and input identity for canvas execution."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class OrchestrationError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), **self.details}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


PRESENTATION_FIELDS = frozenset({
    "displayName", "isSizeManuallyAdjusted", "isGenerating", "isUploading",
    "generationStartedAt", "generationDurationMs", "generationError", "generationTaskKey",
    "generationTaskType", "generationTaskJobId", "orchestration", "errorMessage",
})
IMAGE_GENERATOR_PREVIEW_FIELDS = frozenset({
    "imageNaturalWidth", "imageNaturalHeight", "imageAspectRatioUpdatedAt",
})
IMAGE_GENERATOR_EMPTY_DEFAULT_FIELDS = frozenset({
    "cameraSelection", "focusRegion", "referenceImageUrl", "styleTemplateId", "marks",
})
RESULT_FIELDS = {
    "agentNode": {"agentIdentity"},
    "ideationNode": {"workflowId", "workflowResult"},
    "screenplayNode": {"workflowId", "workflowResult"},
    "directorWorkspaceNode": {"workflowResult", "directorRevisionId", "directorOperationReceipt"},
    "narrativeGroupStoryboardNode": {"narrativeGroupReference", "narrativeGroupResult", "imageUrl"},
    "narrativeGroupVideoNode": {"narrativeGroupResult", "videoUrl"},
    "episodeComposeNode": {"narrativeGroupResult", "resultVideoUrl"},
    "characterVoiceNode": {"voiceCandidates", "voiceDesignPreview"},
    "imageNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "imageGenNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "storyboardGenNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "videoNode": {"videoUrl", "previewImageUrl", "generationBatch", "durationMs", "widthPx", "heightPx"},
    "videoDirectorNode": {"videoUrl", "resultVideoUrl", "previewImageUrl", "attemptId", "taskKey"},
    "audioNode": {"audioUrl", "durationMs", "dubbingTaskId", "dubbingGroupId", "dubbingStatus", "dubbingCandidates", "failedDialogueIds", "dubbingPreparation", "auditionResult"},
    "scriptNode": {"scriptResult", "scriptTitle", "workflowResult"},
    "videoStoryNode": {"analysisResult", "storyResult"},
    "videoComposeNode": {"resultVideoUrl", "previewImageUrl"},
    "storyboardNode": {"frames"},
    "exportImageNode": {"imageUrl", "previewImageUrl"},
    "skillNode": {"outputs", "skillRunId", "skillInputSignature", "skillIdempotencyKey"},
    "threeDWorldNode": {"plyUrl", "sources", "taskKey"},
}

# Agent nodes persist runtime handles on the canvas for navigation. They do not
# alter the node's semantic input or its media evidence. Keep settings and the
# selected Agent reference inside the normal input fingerprint.
INPUT_METADATA_FIELDS = {
    "agentNode": frozenset({"conversationId", "runRefs", "adoptedRef"}),
}


def input_fingerprint(node: dict) -> str:
    data = node.get("data", {})
    # Reference-only media nodes treat their URLs as inputs, unlike generators.
    outputs = set() if data.get("referenceOnly") else RESULT_FIELDS.get(node.get("type"), set())
    metadata = INPUT_METADATA_FIELDS.get(node.get("type"), frozenset())
    presentation = PRESENTATION_FIELDS
    if node.get("type") == "imageGenNode":
        presentation |= IMAGE_GENERATOR_PREVIEW_FIELDS
    input_data = {
        key: value for key, value in data.items()
        if key not in presentation and key not in outputs and key not in metadata
    }
    if node.get("type") == "imageGenNode":
        # The editor materializes these controls with empty values on save.
        # They compile to the same generation request as an omitted value, so
        # do not stale a completed result just because the editor hydrated them.
        for key in IMAGE_GENERATOR_EMPTY_DEFAULT_FIELDS:
            value = input_data.get(key)
            is_empty = value is None or value == "" or value == [] or value == {}
            if key == "cameraSelection" and isinstance(value, dict):
                # The request adapter turns a selection containing only null
                # controls into camera=None, matching an absent selection.
                is_empty = all(item is None for item in value.values())
            if is_empty:
                input_data.pop(key, None)
        # The node adapter sends requestAspectRatio when it is explicitly set;
        # aspectRatio is then only the displayed image ratio. A reference image
        # load can update that display value without changing the generation
        # request. Preserve aspectRatio as an input for the adapter's auto/missing
        # fallback, and normalize it only when the explicit request is present.
        request_ratio = data.get("requestAspectRatio")
        if ("aspectRatio" in input_data and isinstance(request_ratio, str)
                and request_ratio and request_ratio != "auto"):
            input_data["aspectRatio"] = request_ratio
    return fingerprint({"type": node.get("type"), "data": input_data})


def frozen_node_result(node: dict) -> dict:
    """Return only already-persisted, typed output fields suitable as frozen input."""
    data = node.get("data", {})
    if node.get("type") == "narrativeGroupStoryboardNode":
        reference = bound_narrative_group_reference(node)
        result = {"narrativeGroupReference": reference} if reference else {}
        for key in ("narrativeGroupResult", "imageUrl"):
            if data.get(key) not in (None, ""):
                result[key] = data[key]
        return result
    if node.get("type") == "textAnnotationNode":
        value = data.get("content") or data.get("text")
        return {"text": value} if isinstance(value, str) and value.strip() else {}
    fields = set(RESULT_FIELDS.get(node.get("type"), ()))
    # Fixed media nodes are graph sources even though they do not themselves
    # publish through project_result. Keep only the typed media locators used by
    # existing adapters; never freeze arbitrary node data into a downstream call.
    if node.get("type") in {"uploadNode", "pano360ViewerNode"}:
        fields.update({"imageUrl", "previewImageUrl", "videoUrl", "audioUrl", "panoUrl"})
    return {key: data[key] for key in sorted(fields) if key in data and data[key] not in (None, "")}


def bound_narrative_group_reference(node: dict) -> dict | None:
    """Return the explicit, path-safe identity bound by a group storyboard node."""
    data = node.get("data", {})
    episode = data.get("episodeNumber")
    group_id = data.get("groupId")
    revision_id = data.get("directorRevisionId")
    if type(episode) is not int or episode <= 0:
        return None

    def valid_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if (not normalized or len(normalized) > 128
                or any(ord(char) < 32 or char in "/\\%?#" for char in normalized)):
            return None
        return normalized

    normalized_group = valid_id(group_id)
    normalized_revision = valid_id(revision_id)
    if not normalized_group or not normalized_revision:
        return None
    return {
        "episodeNumber": episode,
        "groupId": normalized_group,
        "directorRevisionId": normalized_revision,
    }


def result_projection(node: dict) -> dict:
    """Current typed output fields used to verify a scheduler projection."""
    data = node.get("data", {})
    return {key: data[key] for key in sorted(RESULT_FIELDS.get(node.get("type"), ())) if key in data}


def media_projection(node: dict) -> dict:
    """The same locator payload used by Agent context media-evidence receipts."""
    data = node.get("data", {})
    fields = set(RESULT_FIELDS.get(node.get("type"), ())) | {
        "imageUrl", "previewImageUrl", "panoUrl", "plyUrl", "videoUrl", "audioUrl",
        "sources", "assetRef", "assetMediaSelection", "scene", "scenesBySourceId", "activeSourceId",
    }
    return {key: data[key] for key in sorted(fields) if key in data}


def dependency_edge_projection(canvas: dict, target_node_id: str) -> list[dict]:
    """Execution-relevant direct edge identity, excluding editor presentation data."""
    projected = []
    for edge in canvas.get("edges", []):
        data = edge.get("data") or {}
        if edge.get("target") != target_node_id or data.get("contextLink"):
            continue
        item = {key: edge[key] for key in ("id", "source", "target", "sourceHandle", "targetHandle")
                if key in edge}
        ports = {key: data[key] for key in ("sourcePort", "targetPort") if key in data}
        if ports:
            item["ports"] = ports
        projected.append(item)
    return sorted(projected, key=lambda item: (str(item.get("source", "")),
                                               str(item.get("id", ""))))


def dependency_edge_fingerprint(canvas: dict, target_node_id: str) -> str:
    return fingerprint(dependency_edge_projection(canvas, target_node_id))
