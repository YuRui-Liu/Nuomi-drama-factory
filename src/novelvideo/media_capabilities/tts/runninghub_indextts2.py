"""RunningHub IndexTTS2 workflow compilation and execution helpers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.generators.tts_generator import TTSResult


class EmotionMode(StrEnum):
    NEUTRAL = "neutral"
    TEXT = "text"
    AUDIO = "audio"
    VECTOR = "vector"


_SELECTOR_VALUES = {
    EmotionMode.NEUTRAL: "1",
    EmotionMode.TEXT: "2",
    EmotionMode.AUDIO: "3",
    EmotionMode.VECTOR: "4",
}


@dataclass(frozen=True, slots=True)
class CompiledIndexTTS2Request:
    mode: EmotionMode
    node_info_list: tuple[dict[str, str], ...]

    def node_info(self, node_id: str, field_name: str) -> str:
        for item in self.node_info_list:
            if item["nodeId"] == str(node_id) and item["fieldName"] == field_name:
                return item["fieldValue"]
        raise KeyError(f"node info not found: {node_id}.{field_name}")


def _node(node_id: str, field_name: str, field_value: str) -> dict[str, str]:
    return {
        "nodeId": str(node_id),
        "fieldName": field_name,
        "fieldValue": str(field_value),
    }


def _validate_vector(value: Any) -> tuple[float, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, (list, tuple)) or len(value) != 8:
        raise ValueError("emotion vector requires exactly eight values")
    vector = tuple(float(item) for item in value)
    if any(item < 0 or item > 1 for item in vector):
        raise ValueError("emotion vector values must be between 0 and 1")
    if sum(vector) > 0.8:
        raise ValueError("emotion vector total must not exceed 0.8")
    return vector


def compile_indextts2_request(
    *,
    text: str,
    reference_file: str,
    mode: EmotionMode | str = EmotionMode.NEUTRAL,
    emotion_text: str = "",
    emotion_file: str = "",
    emotion_vector: Any = None,
    emotion_alpha: float = 1.0,
) -> CompiledIndexTTS2Request:
    """Compile nodeInfoList for the local single-node switch workflow."""

    normalized_mode = EmotionMode(mode)
    normalized_text = str(text or "").strip()
    normalized_reference = str(reference_file or "").strip()
    normalized_emotion_text = str(emotion_text or "").strip()
    normalized_emotion_file = str(emotion_file or "").strip()
    sources = sum(
        bool(value)
        for value in (
            normalized_emotion_text,
            normalized_emotion_file,
            emotion_vector is not None,
        )
    )
    if sources > 1:
        raise ValueError("emotion sources are mutually exclusive")
    if not normalized_text:
        raise ValueError("text is required")
    if not normalized_reference:
        raise ValueError("reference_file is required")
    if not 0 <= float(emotion_alpha) <= 1:
        raise ValueError("emotion_alpha must be between 0 and 1")

    if normalized_mode is EmotionMode.TEXT and not normalized_emotion_text:
        raise ValueError("text emotion mode requires emotion_text")
    if normalized_mode is EmotionMode.AUDIO and not normalized_emotion_file:
        raise ValueError("audio emotion mode requires emotion_file")
    if normalized_mode is EmotionMode.VECTOR and emotion_vector is None:
        raise ValueError("vector emotion mode requires emotion_vector")
    vector = _validate_vector(emotion_vector) if emotion_vector is not None else None

    node_info = [
        _node("103", "value", _SELECTOR_VALUES[normalized_mode]),
        _node("4", "prompt", normalized_text),
        _node("10", "audio", normalized_reference),
        _node("1", "emo_alpha", str(float(emotion_alpha))),
        _node("1", "use_random", "false"),
    ]
    if normalized_emotion_text:
        node_info.append(_node("16", "prompt", normalized_emotion_text))
    if normalized_emotion_file:
        node_info.append(_node("19", "audio", normalized_emotion_file))
    if vector is not None:
        node_info.append(_node("21", "prompt", json.dumps(vector, ensure_ascii=False)))
    return CompiledIndexTTS2Request(
        mode=normalized_mode,
        node_info_list=tuple(node_info),
    )


class _RunningHubClient(Protocol):
    async def upload(self, path: str | Path) -> str: ...

    async def submit(self, workflow_id: str, node_info: list[dict[str, str]]) -> str: ...

    async def query(self, task_id: str): ...

    async def download(self, url: str) -> bytes: ...


async def generate_indextts2_audio(
    *,
    runtime,
    text: str,
    reference_path: str | Path,
    mode: EmotionMode | str = EmotionMode.NEUTRAL,
    emotion_text: str = "",
    emotion_path: str | Path | None = None,
    emotion_vector: Any = None,
    emotion_alpha: float = 1.0,
    poll_interval: float = 2.0,
    max_polls: int = 180,
) -> bytes:
    """Run the configured RunningHub IndexTTS2 workflow and return audio bytes."""

    reference_path = Path(reference_path)
    client: _RunningHubClient = runtime.create_client()
    try:
        reference_file = await client.upload(reference_path)
        emotion_file = ""
        if emotion_path is not None:
            emotion_file = await client.upload(Path(emotion_path))
        compiled = compile_indextts2_request(
            text=text,
            reference_file=reference_file,
            mode=mode,
            emotion_text=emotion_text,
            emotion_file=emotion_file,
            emotion_vector=emotion_vector,
            emotion_alpha=emotion_alpha,
        )
        workflow_id = runtime.workflow_id(MediaCapability.TTS_VOICE_CLONE)
        task_id = await client.submit(workflow_id, list(compiled.node_info_list))
        for _ in range(max_polls):
            snapshot = await client.query(task_id)
            if snapshot.status in {"failed", "cancelled"}:
                raise RuntimeError(snapshot.provider_message or "RunningHub IndexTTS2 failed")
            if snapshot.status == "succeeded":
                if not snapshot.results:
                    raise RuntimeError("RunningHub IndexTTS2 returned no audio")
                return await client.download(snapshot.results[0].url)
            await asyncio.sleep(poll_interval)
        raise TimeoutError("RunningHub IndexTTS2 task timed out")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()


class RunningHubIndexTTS2Generator:
    """Adapt the existing Beat generator contract to RunningHub IndexTTS2."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def generate(
        self,
        *,
        prompt: str,
        reference_audio_path: str | Path,
        output_path: str | Path,
        emotion_prompt: str = "",
        emotion_audio_path: str | Path | None = None,
        emotion_vector: Any = None,
        emotion_alpha: float | None = None,
    ) -> TTSResult:
        mode = EmotionMode.NEUTRAL
        if emotion_audio_path is not None:
            mode = EmotionMode.AUDIO
        elif emotion_vector is not None:
            mode = EmotionMode.VECTOR
        elif str(emotion_prompt or "").strip():
            mode = EmotionMode.TEXT
        alpha = 0.6 if mode is EmotionMode.TEXT and emotion_alpha is None else (
            1.0 if emotion_alpha is None else float(emotion_alpha)
        )
        try:
            audio = await generate_indextts2_audio(
                runtime=self.runtime,
                text=prompt,
                reference_path=reference_audio_path,
                mode=mode,
                emotion_text=emotion_prompt,
                emotion_path=emotion_audio_path,
                emotion_vector=emotion_vector,
                emotion_alpha=alpha,
            )
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(audio)
            return TTSResult(success=True, audio_path=str(target), duration_seconds=0.0)
        except Exception as exc:
            return TTSResult(success=False, error=f"RunningHub IndexTTS2 failed: {exc}")


__all__ = [
    "CompiledIndexTTS2Request",
    "EmotionMode",
    "RunningHubIndexTTS2Generator",
    "compile_indextts2_request",
    "generate_indextts2_audio",
]
