"""Compile performance requests into parameters supported by the TTS provider.

The compiler deliberately keeps the script line and the director's notes in
separate fields. Only an explicitly selected IndexTTS2 emotion branch is sent
to the provider; intention, emphasis, and pauses remain review criteria.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ValidationError

from novelvideo.media_capabilities.tts.models import VoiceSpec
from novelvideo.media_capabilities.tts.runninghub_indextts2 import (
    EmotionMode as IndexEmotionMode,
    compile_indextts2_request,
)
from novelvideo.media_capabilities.tts.segments import EmotionRequest
from novelvideo.media_capabilities.tts.voice_prompt import compile_voice_instruction
from novelvideo.media_capabilities.tts.workflow_variants import EmotionMode, EmotionVector


_MODE_ALIASES = {
    "neutral": EmotionMode.NEUTRAL,
    "text": EmotionMode.EMOTION_TEXT,
    "emotion_text": EmotionMode.EMOTION_TEXT,
    "audio": EmotionMode.EMOTION_AUDIO,
    "emotion_audio": EmotionMode.EMOTION_AUDIO,
    "vector": EmotionMode.EMOTION_VECTOR,
    "emotion_vector": EmotionMode.EMOTION_VECTOR,
}
_INDEX_MODE = {
    EmotionMode.NEUTRAL: IndexEmotionMode.NEUTRAL,
    EmotionMode.EMOTION_TEXT: IndexEmotionMode.TEXT,
    EmotionMode.EMOTION_AUDIO: IndexEmotionMode.AUDIO,
    EmotionMode.EMOTION_VECTOR: IndexEmotionMode.VECTOR,
}
_DURATION_KEYS = frozenset({"duration", "target_duration", "target_duration_seconds"})
PERFORMANCE_COMPILER_VERSION = "1"


def _mode_value(value: Any) -> EmotionMode:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ValueError("emotion_mode_not_supported")
    try:
        return _MODE_ALIASES[raw.strip().lower()]
    except KeyError as exc:
        raise ValueError("emotion_mode_not_supported") from exc


def _supported_mode_values(supported_modes: Iterable[Any]) -> frozenset[EmotionMode]:
    if isinstance(supported_modes, (str, bytes)):
        raise ValueError("invalid_supported_modes")
    try:
        return frozenset(_mode_value(value) for value in supported_modes)
    except TypeError as exc:
        raise ValueError("invalid_supported_modes") from exc


def _direction_dict(direction: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(direction, BaseModel):
        values = direction.model_dump(mode="python", exclude_none=True)
    elif isinstance(direction, Mapping):
        values = dict(direction)
    else:
        raise TypeError("direction must be a mapping or a Pydantic model")
    if any(not isinstance(key, str) or not key.strip() for key in values):
        raise ValueError("direction fields must have non-empty string keys")
    if _DURATION_KEYS.intersection(key.strip().lower() for key in values):
        raise ValueError("duration_not_supported")
    return deepcopy(values)


def _emotion_request(mode: EmotionMode, payload: Any) -> EmotionRequest:
    request_data: dict[str, Any] = {"mode": mode}
    if isinstance(payload, Mapping):
        # A mapping is useful for deserialized requests and intentionally goes
        # through EmotionRequest's one-branch validator without field aliases.
        if "mode" in payload and _mode_value(payload["mode"]) is not mode:
            raise ValueError("emotion_payload_mismatch")
        request_data.update(payload)
        request_data["mode"] = mode
    elif payload is not None:
        payload_field = {
            EmotionMode.NEUTRAL: None,
            EmotionMode.EMOTION_TEXT: "text",
            EmotionMode.EMOTION_AUDIO: "audio",
            EmotionMode.EMOTION_VECTOR: "vector",
        }[mode]
        if payload_field is None:
            raise ValueError("emotion_payload_mismatch")
        request_data[payload_field] = payload
    try:
        vector = request_data.get("vector")
        if isinstance(vector, (list, tuple)):
            request_data["vector"] = EmotionVector(values=tuple(vector))
        return EmotionRequest.model_validate(request_data)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError("emotion_payload_mismatch") from exc


def _apply_pronunciation_replacements(
    text: str, replacements: Mapping[str, str] | None
) -> tuple[str, dict[str, str]]:
    if replacements is None:
        return text, {}
    if not isinstance(replacements, Mapping):
        raise TypeError("pronunciation_replacements must be a mapping")

    clean: dict[str, str] = {}
    for source, target in replacements.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError("pronunciation replacement source must be non-empty")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("pronunciation replacement target must be non-empty")
        clean[source] = target

    if not clean:
        return text, {}

    # Match the longest source at each position, and never reprocess generated
    # replacement text. This keeps overlapping mappings deterministic.
    sources = sorted(clean, key=lambda value: (-len(value), value))
    spoken: list[str] = []
    offset = 0
    while offset < len(text):
        source = next((item for item in sources if text.startswith(item, offset)), None)
        if source is None:
            spoken.append(text[offset])
            offset += 1
        else:
            spoken.append(clean[source])
            offset += len(source)
    return "".join(spoken), clean


def compile_performance(
    text: str,
    direction: Mapping[str, Any] | BaseModel,
    *,
    mode: str | EmotionMode,
    payload: Any,
    supported_modes: Iterable[str | Any],
    provider: str = "indextts2",
    target_duration: float | None = None,
    pronunciation_replacements: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return provider parameters, preserved director notes, and source text.

    ``supported_modes`` must come from the selected adapter's declared
    capabilities. The provider can therefore only compile branches it has
    explicitly advertised. For IndexTTS2, the ``effective`` mapping follows
    :func:`generate_indextts2_audio`'s public argument names.
    """

    normalized_provider = str(provider).strip().lower()
    if normalized_provider not in {"indextts2", "index_tts2"}:
        raise ValueError("provider_not_supported")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text_required")
    if target_duration is not None:
        raise ValueError("duration_not_supported")

    notes = _direction_dict(direction)
    requested_mode = _mode_value(mode)
    declared_modes = _supported_mode_values(supported_modes)
    if requested_mode not in declared_modes:
        raise ValueError("emotion_mode_not_supported")

    emotion = _emotion_request(requested_mode, payload)
    spoken_text, replacements = _apply_pronunciation_replacements(
        text, pronunciation_replacements
    )

    # Use the existing provider compiler as the final validation boundary. In
    # particular, its vector limit is stricter than the generic EmotionVector
    # shape check and must be enforced before the paid request is submitted.
    index_mode = _INDEX_MODE[emotion.mode]
    compile_indextts2_request(
        text=spoken_text,
        reference_file="validation-reference.wav",
        mode=index_mode,
        emotion_text=emotion.text or "",
        emotion_file=emotion.audio or "",
        emotion_vector=(list(emotion.vector.values) if emotion.vector is not None else None),
    )

    effective: dict[str, Any] = {"text": spoken_text, "mode": index_mode.value}
    if emotion.text is not None:
        effective["emotion_text"] = emotion.text
    if emotion.audio is not None:
        effective["emotion_path"] = emotion.audio
    if emotion.vector is not None:
        effective["emotion_vector"] = list(emotion.vector.values)

    return {
        "compiler_version": PERFORMANCE_COMPILER_VERSION,
        "provider": "indextts2",
        "source_text": text,
        "effective": effective,
        "review_only": notes,
        "pronunciation_replacements": replacements,
    }


def compile_voice_design_preview(
    *,
    audition_text: str,
    voice_spec: VoiceSpec,
    language: str | None = None,
) -> dict[str, Any]:
    """Compile the exact text/instruct fields for the published Qwen workflow.

    The workflow fixes language to Auto. Other language requests are rejected
    instead of being shown as effective controls that the workflow ignores.
    """

    if not isinstance(audition_text, str) or not audition_text.strip():
        raise ValueError("audition_text_required")
    if language is not None and language.strip().lower() != "auto":
        raise ValueError("language_control_not_supported")
    if not isinstance(voice_spec, VoiceSpec):
        raise TypeError("voice_spec must be a VoiceSpec")

    return {
        "provider": "qwen_voice_design",
        "effective": {
            "language": "Auto",
            "text": audition_text.strip(),
            "instruct": compile_voice_instruction(voice_spec),
        },
        "review_only": {},
    }


__all__ = ["PERFORMANCE_COMPILER_VERSION", "compile_performance", "compile_voice_design_preview"]
