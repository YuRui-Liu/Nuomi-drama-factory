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
RESULT_FIELDS = {
    "imageNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "imageGenNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "storyboardGenNode": {"imageUrl", "previewImageUrl", "generationBatch"},
    "videoNode": {"videoUrl", "previewImageUrl", "generationBatch", "durationMs", "widthPx", "heightPx"},
    "videoDirectorNode": {"videoUrl", "resultVideoUrl", "previewImageUrl", "attemptId", "taskKey"},
    "audioNode": {"audioUrl", "durationMs"},
    "scriptNode": {"scriptResult", "scriptTitle"},
    "videoStoryNode": {"analysisResult", "storyResult"},
    "videoComposeNode": {"resultVideoUrl", "previewImageUrl"},
    "storyboardNode": {"frames"},
    "exportImageNode": {"imageUrl", "previewImageUrl"},
    "skillNode": {"outputs", "skillRunId", "skillInputSignature", "skillIdempotencyKey"},
    "threeDWorldNode": {"plyUrl", "sources", "taskKey"},
}


def input_fingerprint(node: dict) -> str:
    data = node.get("data", {})
    # Reference-only media nodes treat their URLs as inputs, unlike generators.
    outputs = set() if data.get("referenceOnly") else RESULT_FIELDS.get(node.get("type"), set())
    return fingerprint({"type": node.get("type"), "data": {
        key: value for key, value in data.items()
        if key not in PRESENTATION_FIELDS and key not in outputs
    }})
