"""Candidate and approved master storage for designed character voices."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.tts.models import VoiceProfileStatus, VoiceSpec


class VoiceStoreError(ValueError):
    """Raised when a voice candidate cannot be stored or approved."""


class VoiceCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    character_id: str
    spec: VoiceSpec
    audition_text: str
    audio: str
    status: VoiceProfileStatus = VoiceProfileStatus.CANDIDATE


class VoiceProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    character_id: str
    source_candidate_id: str
    spec: VoiceSpec
    audio: str
    status: VoiceProfileStatus = VoiceProfileStatus.APPROVED


class VoiceProfileStore:
    """Keep candidates separate from immutable, explicitly approved masters."""

    def __init__(self) -> None:
        self._candidates: dict[str, VoiceCandidate] = {}
        self._masters: dict[str, VoiceProfile] = {}

    def add_candidate(
        self,
        *,
        character_id: str,
        spec: VoiceSpec,
        audition_text: str,
        audio: str,
    ) -> VoiceCandidate:
        candidate = VoiceCandidate(
            candidate_id=uuid4().hex,
            character_id=character_id,
            spec=spec,
            audition_text=audition_text,
            audio=audio,
        )
        self._candidates[candidate.candidate_id] = candidate
        return candidate

    def approve(self, candidate_id: str) -> VoiceProfile:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise VoiceStoreError(f"unknown voice candidate: {candidate_id}")

        existing = self._masters.get(candidate.character_id)
        if existing is not None:
            if existing.source_candidate_id == candidate_id:
                return existing
            raise VoiceStoreError(
                f"character already has an approved voice: {candidate.character_id}"
            )

        master = VoiceProfile(
            profile_id=uuid4().hex,
            character_id=candidate.character_id,
            source_candidate_id=candidate.candidate_id,
            spec=candidate.spec,
            audio=candidate.audio,
        )
        self._masters[candidate.character_id] = master
        return master

    def get_master(self, character_id: str) -> VoiceProfile | None:
        return self._masters.get(character_id)

    def list_candidates(self, character_id: str) -> tuple[VoiceCandidate, ...]:
        return tuple(
            candidate
            for candidate in self._candidates.values()
            if candidate.character_id == character_id
        )


__all__ = [
    "VoiceCandidate",
    "VoiceProfile",
    "VoiceProfileStore",
    "VoiceStoreError",
]
