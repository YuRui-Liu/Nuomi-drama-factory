"""Deterministic dialogue normalization, splitting, and segment identity."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, model_validator

from novelvideo.media_capabilities.tts.workflow_variants import (
    EmotionMode,
    EmotionVector,
)


_ROLE_LABEL = re.compile(r"(?m)^\s*[^\s：:\n]{1,32}\s*[：:]\s*")
_STAGE_DIRECTION = re.compile(r"（[^（）]*）|\([^()]*\)|【[^【】]*】|\[[^\[\]]*\]")
_SENTENCE = re.compile(r".*?(?:……|[。！？!?；;]+|$)", re.DOTALL)
_DIGITS = re.compile(r"[0-9０-９]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'’-]*")


class EmotionRequest(BaseModel):
    """One and only one IndexTTS2 emotion branch request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: EmotionMode = EmotionMode.NEUTRAL
    text: str | None = None
    audio: str | None = None
    vector: EmotionVector | None = None

    @model_validator(mode="after")
    def validate_mode_payload(self) -> EmotionRequest:
        supplied = {
            EmotionMode.EMOTION_TEXT: bool(self.text and self.text.strip()),
            EmotionMode.EMOTION_AUDIO: bool(self.audio and self.audio.strip()),
            EmotionMode.EMOTION_VECTOR: self.vector is not None,
        }
        expected = supplied.get(self.mode, False)
        if self.mode is EmotionMode.NEUTRAL:
            if any(supplied.values()):
                raise ValueError("neutral emotion must not include a payload")
            return self
        if not expected or sum(supplied.values()) != 1:
            raise ValueError("emotion mode requires exactly its matching payload")
        return self


class DialogueSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dialogue_id: str
    segment_id: str
    segment_index: int
    text: str
    emotion: EmotionRequest

    @property
    def idempotency_key(self) -> str:
        return f"tts-segment:{self.segment_id}"


def normalize_dialogue_text(text: str) -> str:
    """Remove non-spoken labels/directions and normalize insignificant spacing."""
    if not isinstance(text, str):
        raise TypeError("dialogue text must be a string")
    without_labels = _ROLE_LABEL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    without_directions = _STAGE_DIRECTION.sub("", without_labels)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in without_directions.split("\n")]
    return "\n".join(line for line in lines if line)


def _atoms(text: str, protected_terms: Sequence[str]) -> list[str]:
    terms = tuple(
        sorted(
            {term.strip() for term in protected_terms if term.strip()},
            key=lambda term: (-len(term), term),
        )
    )
    result: list[str] = []
    offset = 0
    while offset < len(text):
        protected = next(
            (term for term in terms if text.startswith(term, offset)),
            None,
        )
        if protected is not None:
            result.append(protected)
            offset += len(protected)
            continue
        match = _DIGITS.match(text, offset) or _WORD.match(text, offset)
        if match is not None:
            result.append(match.group(0))
            offset = match.end()
            continue
        result.append(text[offset])
        offset += 1
    return result


def _split_long_sentence(
    sentence: str,
    *,
    max_chars: int,
    protected_terms: Sequence[str],
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for atom in _atoms(sentence, protected_terms):
        if current and len(current) + len(atom) > max_chars:
            chunks.append(current)
            current = ""
        current += atom
        if len(current) >= max_chars:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def split_dialogue(
    text: str,
    *,
    max_chars: int = 120,
    character_names: Sequence[str] = (),
) -> tuple[str, ...]:
    """Split normalized speech at sentence boundaries and deterministic atoms."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    normalized = normalize_dialogue_text(text)
    if not normalized:
        return ()

    sentences = [
        match.group(0).strip()
        for match in _SENTENCE.finditer(normalized.replace("\n", ""))
        if match.group(0).strip()
    ]
    chunks: list[str] = []
    for sentence in sentences:
        chunks.extend(
            _split_long_sentence(
                sentence,
                max_chars=max_chars,
                protected_terms=character_names,
            )
        )
    return tuple(chunks)


def _segment_id(
    *,
    character_id: str,
    voice_profile_version: str,
    text: str,
    emotion: EmotionRequest,
    segment_index: int,
) -> str:
    payload = {
        "character_id": character_id.strip(),
        "voice_profile_version": voice_profile_version.strip(),
        "text": text,
        "emotion": emotion.model_dump(mode="json", exclude_none=True),
        "segment_index": segment_index,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_dialogue_segments(
    *,
    dialogue_id: str,
    character_id: str,
    voice_profile_version: str,
    text: str,
    emotion: EmotionRequest,
    max_chars: int = 120,
    character_names: Sequence[str] = (),
) -> tuple[DialogueSegment, ...]:
    """Build immutable segments whose IDs are stable across retries."""
    if not dialogue_id.strip():
        raise ValueError("dialogue_id must not be empty")
    if not character_id.strip():
        raise ValueError("character_id must not be empty")
    if not voice_profile_version.strip():
        raise ValueError("voice_profile_version must not be empty")

    chunks = split_dialogue(
        text,
        max_chars=max_chars,
        character_names=character_names,
    )
    return tuple(
        DialogueSegment(
            dialogue_id=dialogue_id.strip(),
            segment_id=_segment_id(
                character_id=character_id,
                voice_profile_version=voice_profile_version,
                text=chunk,
                emotion=emotion,
                segment_index=index,
            ),
            segment_index=index,
            text=chunk,
            emotion=emotion,
        )
        for index, chunk in enumerate(chunks)
    )


__all__ = [
    "DialogueSegment",
    "EmotionRequest",
    "build_dialogue_segments",
    "normalize_dialogue_text",
    "split_dialogue",
]
