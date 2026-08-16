"""Qwen3-TTS voice-design workflow compilation and candidate generation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.concurrency import (
    ProviderConcurrencyCoordinator,
)
from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.tts.models import VoiceSpec
from novelvideo.media_capabilities.tts.segments import DialogueSegment
from novelvideo.media_capabilities.tts.voice_prompt import compile_voice_instruction
from novelvideo.media_capabilities.tts.voice_store import (
    VoiceCandidate,
    VoiceProfileStore,
)


Workflow = dict[str, Any]
VoiceDesignRunner = Callable[[Workflow, str], Awaitable[str]]
SegmentSynthesisRunner = Callable[[DialogueSegment, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class CompiledVoiceDesignWorkflow:
    workflow: Workflow
    output: str = "18.audio"


class SegmentArtifact(BaseModel):
    """The isolated outcome of one dialogue segment synthesis attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialogue_id: str
    segment_id: str
    segment_index: int
    idempotency_key: str
    audio: str | None = None
    error: str | None = None


class BatchDubbingPipeline:
    """Synthesize segments under the process-wide provider coordinator."""

    def __init__(
        self,
        *,
        provider_id: str,
        coordinator: ProviderConcurrencyCoordinator,
        synthesize_segment: SegmentSynthesisRunner,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must not be empty")
        self._provider_id = provider_id.strip()
        self._coordinator = coordinator
        self._synthesize_segment = synthesize_segment

    async def _run(self, item: DialogueSegment) -> SegmentArtifact:
        try:
            async with self._coordinator.lease(
                self._provider_id,
                MediaCapability.TTS_SYNTHESIZE,
            ):
                audio = await self._synthesize_segment(
                    item,
                    item.idempotency_key,
                )
            return SegmentArtifact(
                dialogue_id=item.dialogue_id,
                segment_id=item.segment_id,
                segment_index=item.segment_index,
                idempotency_key=item.idempotency_key,
                audio=audio,
            )
        except Exception as exc:
            return SegmentArtifact(
                dialogue_id=item.dialogue_id,
                segment_id=item.segment_id,
                segment_index=item.segment_index,
                idempotency_key=item.idempotency_key,
                error=str(exc),
            )

    async def synthesize_batch(
        self,
        items: Sequence[DialogueSegment],
    ) -> list[SegmentArtifact]:
        """Run every item independently and return deterministic index order."""
        results = await asyncio.gather(*(self._run(item) for item in items))
        return sorted(results, key=lambda item: item.segment_index)


def _node_inputs(workflow: Workflow, node_id: str) -> dict[str, Any]:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise ValueError(f"Qwen3 voice workflow is missing node {node_id}")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError(f"Qwen3 voice workflow node {node_id} has no inputs")
    return inputs


def compile_qwen3_voice_design_workflow(
    source: Mapping[str, Any],
    *,
    text: str,
    voice_instruction: str,
    language: str,
    task_overrides: Mapping[str, Any] | None = None,
) -> CompiledVoiceDesignWorkflow:
    """Apply the fixed public bindings without permitting model replacement."""
    if task_overrides and "23" in task_overrides:
        raise ValueError("task overrides must not replace Qwen3 model node 23")

    workflow = deepcopy(dict(source))
    for node_id, override in (task_overrides or {}).items():
        node = workflow.get(node_id)
        if not isinstance(node, dict) or not isinstance(override, Mapping):
            raise ValueError(f"invalid task override for node {node_id}")
        override_copy = deepcopy(dict(override))
        override_inputs = override_copy.pop("inputs", None)
        node.update(override_copy)
        if override_inputs is not None:
            if not isinstance(override_inputs, Mapping):
                raise ValueError(f"invalid task override inputs for node {node_id}")
            _node_inputs(workflow, node_id).update(deepcopy(dict(override_inputs)))

    _node_inputs(workflow, "4")["text"] = text
    _node_inputs(workflow, "5")["text"] = voice_instruction
    _node_inputs(workflow, "2")["language"] = language
    _node_inputs(workflow, "18")
    _node_inputs(workflow, "23")
    return CompiledVoiceDesignWorkflow(workflow=workflow)


class VoiceDesignPipeline:
    def __init__(
        self,
        *,
        workflow: Mapping[str, Any],
        store: VoiceProfileStore,
        runner: VoiceDesignRunner,
        language: str,
    ) -> None:
        self._workflow = deepcopy(dict(workflow))
        self._store = store
        self._runner = runner
        self._language = language

    async def generate_candidates(
        self,
        character_id: str,
        spec: VoiceSpec,
        audition_suite: Sequence[str],
        count: int = 3,
    ) -> list[VoiceCandidate]:
        if not character_id.strip():
            raise ValueError("character_id must not be empty")
        if not audition_suite or any(not text.strip() for text in audition_suite):
            raise ValueError("audition_suite must contain non-empty text")
        if count < 1:
            raise ValueError("count must be positive")

        instruction = compile_voice_instruction(spec)
        candidates: list[VoiceCandidate] = []
        for index in range(count):
            audition_text = audition_suite[index % len(audition_suite)]
            compiled = compile_qwen3_voice_design_workflow(
                self._workflow,
                text=audition_text,
                voice_instruction=instruction,
                language=self._language,
            )
            audio = await self._runner(compiled.workflow, compiled.output)
            candidates.append(
                self._store.add_candidate(
                    character_id=character_id,
                    spec=spec,
                    audition_text=audition_text,
                    audio=audio,
                )
            )
        return candidates


__all__ = [
    "BatchDubbingPipeline",
    "CompiledVoiceDesignWorkflow",
    "SegmentArtifact",
    "VoiceDesignPipeline",
    "compile_qwen3_voice_design_workflow",
]
