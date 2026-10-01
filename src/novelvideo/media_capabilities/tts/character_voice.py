"""Prepare a validated, reproducible character voice request before billing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from novelvideo.media_capabilities.tts.models import VoiceSpec, VoiceSpecError
from novelvideo.media_capabilities.tts.voice_prompt import (
    AGE_LABELS, GENDER_LABELS, compile_voice_instruction, normalize_voice_age,
)


def character_voice_snapshot(character: Any, *, db_path=None) -> dict[str, Any]:
    facts = getattr(character, "voice_facts", None)
    if hasattr(facts, "model_dump"):
        facts_dict = facts.model_dump(mode="json")
    elif isinstance(facts, dict):
        facts_dict = facts
    else:
        facts_dict = vars(facts) if facts else {}
    from novelvideo.script_creation.asset_context import load_asset_authoring_context
    context = load_asset_authoring_context(db_path, 'character', character.name) if db_path else []
    context = [item for item in context if item['source_document'] != f'character:{character.name}']
    return {
        "name": character.name,
        "gender": getattr(character, "gender", "") or "",
        "age_group": getattr(character, "age_group", "") or "",
        "role": getattr(character, "role", "") or "",
        "voice_facts": facts_dict,
        "description": getattr(character, "description", "") or "",
        "authoring_context": context,
    }


def voice_snapshot_digest(snapshot: dict[str, Any]) -> str:
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def _mentioned_ages(text: str) -> set[str]:
    patterns = {
        "child": r"儿童|幼年|\bchild(?:ren)?\b",
        "teen": r"青少年|(?<!青)少年|\bteen(?:ager)?\b",
        "youth": r"青年|\byouth\b|\byoung(?: adult)?\b",
        "middle": r"中年|\bmiddle(?:[ -]aged)?\b",
        "elder": r"老年|\belder(?:ly)?\b|\bsenior\b",
    }
    return {age for age, pattern in patterns.items() if re.search(pattern, text, re.I)}


def prepare_character_voice_request(
    character: Any, *, slot: str, voice_description: str = "", audition_text: str = "",
    language: str = "Chinese", voice_spec: VoiceSpec | None = None, db_path=None,
) -> dict[str, Any]:
    if slot not in {"default", "child", "youth", "middle", "elder"}:
        raise VoiceSpecError(f"invalid voice slot: {slot}")
    snapshot = character_voice_snapshot(character, db_path=db_path)
    facts = snapshot["voice_facts"]
    mode = facts.get("vocalization_mode", "unknown")
    if mode not in {"dialogue", "both"} or facts.get("conflicts"):
        raise VoiceSpecError(f"vocalization_not_dialogue: {mode}; 请核对角色发声事实，非语言素材不能使用人声槽位")
    age = normalize_voice_age(slot if slot != "default" else facts.get("age_group") or snapshot["age_group"])
    custom = " ".join(voice_description.split())
    if voice_spec is not None and custom:
        raise VoiceSpecError("choose voice_spec or voice_description, not both")
    spec = voice_spec or VoiceSpec(texture=custom or str(facts.get("voice_traits") or ""))
    free_text = " ".join(
        str(v) for k, v in spec.model_dump().items()
        if k not in {"age_impression", "negative_constraints"}
    )
    mentioned = _mentioned_ages(free_text)
    profile_ages = _mentioned_ages(spec.age_impression) if slot == "default" else set()
    if len(profile_ages) > 1:
        raise VoiceSpecError("voice profile age is contradictory")
    effective_age = next(iter(profile_ages), age)
    if len(mentioned) > 1 or (effective_age and mentioned - {effective_age}):
        raise VoiceSpecError("voice age conflicts with selected age slot")
    impression = spec.age_impression
    if slot != "default" or not impression:
        impression = AGE_LABELS[age] + GENDER_LABELS.get(snapshot["gender"].strip().lower(), "")
    spec = spec.model_copy(update={"age_impression": impression})
    instruction = compile_voice_instruction(spec)
    if snapshot['description'] or snapshot['authoring_context']:
        instruction += ('\n作者业务设定，仅作为声音表演设计参考，不是已证实的声学参数或对白事实。'
            '优先遵守上面的明确声线要求，不从身份、外貌或未来计划推断年龄、性别、音高或口音：\n'
            + json.dumps({'description': snapshot['description'], 'sources': snapshot['authoring_context']}, ensure_ascii=False))
    # Check contradictions across dimensions as well as inside each dimension.
    combined = " ".join((spec.pitch, spec.texture, spec.pace, spec.resonance, spec.articulation))
    compile_voice_instruction(VoiceSpec(pitch=combined, pace=combined))
    return {
        "character_name": character.name, "slot": slot, "voice_description": instruction,
        "audition_text": audition_text.strip() or "这件事没那么简单，我们先把情况弄清楚。",
        "language": language.strip() or "Chinese",
        "voice_spec": spec.model_dump(mode="json"), "profile_snapshot": snapshot,
        "profile_digest": voice_snapshot_digest(snapshot), "compiler_version": "voice-spec-v2",
        "target_reference": (
            {"path": getattr(character, "reference_audio_path", ""), "sha256": getattr(character, "reference_audio_sha256", "")}
            if slot == "default" else dict((getattr(character, "voice_samples_by_age_group", {}) or {}).get(slot) or {})
        ),
    }
