"""Compile mutually exclusive IndexTTS2 workflow branches."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class EmotionMode(StrEnum):
    NEUTRAL = "neutral"
    EMOTION_TEXT = "emotion_text"
    EMOTION_AUDIO = "emotion_audio"
    EMOTION_VECTOR = "emotion_vector"


class EmotionVector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[float, ...]

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if len(values) != 8 or any(value < 0 or value > 1 for value in values):
            raise ValueError("emotion vector requires eight values in range 0..1")
        return values


class WorkflowVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: EmotionMode
    runner_node_id: str
    output_node_id: str
    workflow: dict[str, dict[str, Any]]


_BRANCHES = {
    EmotionMode.NEUTRAL: ("1", "5"),
    EmotionMode.EMOTION_TEXT: ("14", "15"),
    EmotionMode.EMOTION_AUDIO: ("17", "18"),
    EmotionMode.EMOTION_VECTOR: ("20", "22"),
}


def _dependencies(node: dict[str, Any]) -> set[str]:
    dependencies: set[str] = set()
    for value in node.get("inputs", {}).values():
        if (
            isinstance(value, list)
            and len(value) == 2
            and isinstance(value[0], str)
        ):
            dependencies.add(value[0])
    return dependencies


def build_indextts2_variant(
    source: dict[str, dict[str, Any]], mode: EmotionMode
) -> WorkflowVariant:
    runner, output = _BRANCHES[mode]
    if runner not in source or output not in source:
        raise ValueError("indextts2.missing_branch")

    keep: set[str] = set()
    pending = [output]
    while pending:
        node_id = pending.pop()
        if node_id in keep:
            continue
        node = source.get(node_id)
        if node is None:
            raise ValueError("indextts2.missing_dependency")
        keep.add(node_id)
        pending.extend(_dependencies(node))

    workflow = {node_id: deepcopy(source[node_id]) for node_id in keep}
    workflow[runner].setdefault("inputs", {})["use_random"] = False
    return WorkflowVariant(
        mode=mode,
        runner_node_id=runner,
        output_node_id=output,
        workflow=workflow,
    )


def save_audio_nodes(workflow: dict[str, dict[str, Any]]) -> set[str]:
    return {
        node_id
        for node_id, node in workflow.items()
        if node.get("class_type") == "SaveAudio"
    }


__all__ = [
    "EmotionMode",
    "EmotionVector",
    "WorkflowVariant",
    "build_indextts2_variant",
    "save_audio_nodes",
]
