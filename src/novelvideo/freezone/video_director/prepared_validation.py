"""Shared deterministic checks for optimizer and Codex-prepared H3 wires."""

from __future__ import annotations

import hashlib
import json
import re

from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_PROMPT_PROFILE_ID,
    H3_PROMPT_PROFILE_VERSION,
)
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire

from .capabilities import GenerationValidation, validate_generation
from .models import (
    CanvasBaseWire,
    CanvasReferenceWire,
    DirectorDraft,
    OptimizedDirector,
)


_EXPLICIT_DIALOGUE = re.compile(
    r"(?<!\w)(?P<speaker>(?:[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}|"
    r"[^\W\d_][\w-]{0,39}))\s*"
    r"(?:[（(](?P<pre_language>[^()（）\[\]\n]{1,24})[）)])?\s*"
    r"(?P<verb>(?:says?|said|asks?|asked|whispers?|whispered|shouts?|shouted|"
    r"replies|replied|sings?|sang|speaks?|spoke|说道|低声说|回答|说|问|喊|答|唱)"
    r"\s*[:：]?|[:：])\s*"
    r"(?P<quote>\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』|'[^'\n]*')"
    r"(?:\s*[（(](?P<post_language>[^()（）\[\]\n]{1,24})[）)])?",
    flags=re.IGNORECASE,
)
_UNPARSED_ENGLISH_SPEECH = re.compile(
    r"(?<!\w)(?P<speaker>(?:[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}|"
    r"[^\W\d_][\w-]{0,39}))\s+(?:says?|said|asks?|asked|whispers?|whispered|"
    r"shouts?|shouted|replies|replied|sings?|sang|speaks?|spoke)\s+"
    r"(?P<content>[^\n.!?。！？]{1,80})",
    flags=re.IGNORECASE,
)
_UNPARSED_CJK_SPEECH = re.compile(
    r"(?P<speaker>[\u3400-\u9fff]{2,12})(?:[（(][^()（）\n]{1,24}[）)])?"
    r"(?:用[\u3400-\u9fff]{1,8})?(?:说道|说|问|回答|答|喊|唱)(?![话着号])"
    r"(?P<content>[\u3400-\u9fff]{2,}|[^\s。！？.!?]{2,})"
)
_DIALOGUE_BLOCK = re.compile(
    r"<d>\s*\[(?P<language>[^\]\n]+)\]\s*(?P<text>.*?)</d>",
    flags=re.DOTALL,
)
_SPEAKER_CUE = re.compile(
    r"(?<!\w)(?P<speaker>(?:[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}|"
    r"[^\W\d_][\w-]{0,39}))\s*(?P<speaker_id>\([Ss][1-9][0-9]*\)|（[Ss][1-9][0-9]*）)?\s*"
    r"(?P<verb>(?:says?|said|asks?|asked|whispers?|whispered|shouts?|shouted|"
    r"replies|replied|sings?|sang|speaks?|spoke|说道|低声说|回答|说|问|喊|答|唱)"
    r"\s*[:：]?|[:：])\s*$",
    flags=re.IGNORECASE,
)
_LANGUAGE_ALIASES = {
    "中文": "chinese", "汉语": "chinese", "普通话": "chinese", "国语": "chinese",
    "英语": "english", "英文": "english", "日语": "japanese", "日文": "japanese",
    "韩语": "korean", "韩文": "korean", "粤语": "cantonese", "法语": "french",
    "德语": "german", "西班牙语": "spanish", "葡萄牙语": "portuguese",
    "意大利语": "italian", "俄语": "russian", "阿拉伯语": "arabic", "泰语": "thai",
    "越南语": "vietnamese", "印地语": "hindi", "chinese": "chinese",
    "mandarin": "chinese", "english": "english", "japanese": "japanese",
    "korean": "korean", "cantonese": "cantonese", "french": "french",
    "german": "german", "spanish": "spanish", "portuguese": "portuguese",
    "italian": "italian", "russian": "russian", "arabic": "arabic", "thai": "thai",
    "vietnamese": "vietnamese", "hindi": "hindi", "en": "english", "zh": "chinese",
    "ja": "japanese", "ko": "korean", "fr": "french", "de": "german",
    "es": "spanish", "pt": "portuguese", "it": "italian", "ru": "russian",
}
_SUBJECT_TAG = re.compile(r"<Subject ([1-9][0-9]*)>")
_PICTURE_TAG = re.compile(r"<Picture ([1-9][0-9]*)>")
_NON_SPEAKER_LABELS = frozenset({
    "camera", "shot", "style", "title", "music", "sfx", "镜头", "画面", "字幕", "音乐", "音效",
})


def director_draft_fingerprint(draft: DirectorDraft) -> str:
    """Hash the canonical frozen draft, including reference and image hashes."""
    data = draft.model_dump(mode="json")
    # Match fingerprints created before this optional field existed. Once a
    # real context is present, it remains part of the canonical payload.
    if draft.continuity_context is None:
        data.pop("continuity_context", None)
    payload = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def locked_dialogue(prompt: str) -> list[dict[str, str]]:
    """Extract explicit dialogue and fail closed on unparseable speech cues."""
    names = re.findall(r"<Picture [1-9][0-9]*>是([^：:\n，,]{1,20})[：:]", prompt)
    matches = list(_EXPLICIT_DIALOGUE.finditer(prompt))
    spans = [(match.start(), match.end()) for match in matches]
    unparsed_cues = [cue for pattern in (_UNPARSED_ENGLISH_SPEECH, _UNPARSED_CJK_SPEECH)
                     for cue in pattern.finditer(prompt)
                     if not any(start <= cue.start() < end for start, end in spans)]
    if unparsed_cues:
        raise ValueError("source dialogue could not be parsed into a speaker and quoted line")
    locks = []
    for match in matches:
        speaker = _speaker_for_match(match)
        if speaker.casefold() in _NON_SPEAKER_LABELS:
            continue
        pre_language = _language_annotation(match.group("pre_language"))
        post_language = _language_annotation(match.group("post_language"))
        if pre_language and post_language and pre_language != post_language:
            raise ValueError("source dialogue has conflicting language annotations")
        lock = {
            "speaker": _bound_speaker(speaker, names),
            "quote": match.group("quote"),
        }
        language = pre_language or post_language
        if language:
            lock["language"] = language
        locks.append(lock)
    return locks


def _language_annotation(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[\s_-]+", "", value).casefold()
    if compact in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[compact]
    if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", compact):
        return compact
    if compact.endswith(("语", "文", "話", "话", "language")):
        return compact
    return None


def _spoken_text(quote: str) -> str:
    return quote[1:-1].strip()


def spoken_text(quote: str) -> str:
    """Return the utterance inside the source's paired quotation marks."""
    return _spoken_text(quote)


def h3_dialogue_language(lock: dict[str, str]) -> str:
    """Return a deterministic H3 language tag for compiler-owned dialogue."""
    language = lock.get("language")
    if language:
        canonical = _LANGUAGE_ALIASES.get(language.casefold(), language)
        return canonical[:1].upper() + canonical[1:]
    text = _spoken_text(lock["quote"])
    if re.search(r"[\u3040-\u30ff]", text):
        return "Japanese"
    if re.search(r"[\uac00-\ud7af]", text):
        return "Korean"
    if re.search(r"[\u3400-\u9fff]", text):
        return "Chinese"
    if re.search(r"[\u0600-\u06ff]", text):
        return "Arabic"
    if re.search(r"[\u0e00-\u0e7f]", text):
        return "Thai"
    if re.search(r"[\u0400-\u04ff]", text):
        return "Russian"
    return "English"


def bound_speaker(value: str, names: list[str]) -> str:
    for name in sorted(names, key=len, reverse=True):
        if value == name:
            return name
        if value.startswith(name) and value[len(name):].startswith((
            "画外", "看着", "坚定", "轻声", "低声", "大声", "高声", "平静", "温柔", "哭着", "笑着",
        )):
            return name
    return value


def _bound_speaker(value: str, names: list[str]) -> str:
    return bound_speaker(value, names)


def _speaker_for_match(match: re.Match[str]) -> str:
    speaker = match.group("speaker")
    if match.group("verb") in (":", "："):
        for suffix in ("低声说", "说道", "回答", "说", "问", "喊", "答"):
            if speaker.endswith(suffix) and len(speaker) > len(suffix):
                return speaker[:-len(suffix)]
        speaker = re.sub(
            r"\s+(?:whispered|whispers?|replied|replies|shouted|shouts?|"
            r"spoke|speaks?|sang|sings?|asked|asks?|said|says?)$",
            "",
            speaker,
            flags=re.IGNORECASE,
        )
    return speaker


def check_dialogue_identity(
    wire: CanvasBaseWire | CanvasReferenceWire,
    locks: list[dict[str, str]],
) -> None:
    description = (wire.integrated_multimodal_description
                   if isinstance(wire, CanvasBaseWire) else wire.detailed_description)
    if not locks:
        if _DIALOGUE_BLOCK.search(description):
            raise ValueError("optimized dialogue has no explicit source dialogue to preserve")
        return
    if _DIALOGUE_BLOCK.search(wire.overall_soundscape):
        raise ValueError("optimized dialogue markup must be in the description, not soundscape")
    names = list(dict.fromkeys(lock["speaker"] for lock in locks))
    observed = []
    for block in _DIALOGUE_BLOCK.finditer(description):
        prefix = description[max(0, block.start() - 160):block.start()]
        speaker_matches = list(_SPEAKER_CUE.finditer(prefix))
        if not speaker_matches:
            raise ValueError("optimized dialogue markup must have an explicit source speaker")
        speaker_match = speaker_matches[-1]
        speaker = _bound_speaker(_speaker_for_match(speaker_match), names)
        observed.append({
            "speaker": speaker,
            "text": block.group("text").strip(),
            "language": _language_annotation(block.group("language")),
        })
    plain_description = _DIALOGUE_BLOCK.sub("", description)
    for field in (plain_description, wire.overall_soundscape):
        for match in _EXPLICIT_DIALOGUE.finditer(field):
            speaker = _bound_speaker(_speaker_for_match(match), names)
            if any(lock["speaker"] == speaker
                   and _spoken_text(lock["quote"]) == _spoken_text(match.group("quote"))
                   for lock in locks):
                raise ValueError("optimized dialogue must use official <d> markup")
    if len(observed) != len(locks):
        raise ValueError("optimized dialogue count differs from source")
    for lock, item in zip(locks, observed, strict=True):
        expected_language = lock.get("language")
        if (item["speaker"] != lock["speaker"]
                or item["text"] != _spoken_text(lock["quote"])
                or (expected_language is not None and item["language"] != expected_language)):
            raise ValueError(f"optimized dialogue differs from source: {lock['speaker']}")


def check_reference_identity(wire: CanvasReferenceWire, facts: list[dict[str, object]]) -> None:
    """Validate Picture/Subject identities against actual attached input order."""
    by_subject: dict[str, list[dict[str, object]]] = {}
    for fact in facts:
        by_subject.setdefault(str(fact["subject_tag"]), []).append(fact)
    lines = [line.strip() for line in wire.subject_definitions.splitlines() if line.strip()]
    if len(lines) != len(by_subject):
        raise ValueError("reference subject count differs from input images")
    for line, (subject, subject_facts) in zip(lines, by_subject.items(), strict=True):
        labels = _SUBJECT_TAG.findall(line)
        if not line.startswith(subject) or labels != [subject[9:-1]]:
            raise ValueError("reference subject numbering differs from input images")
        picture_matches = list(_PICTURE_TAG.finditer(line))
        pictures = [match.group() for match in picture_matches]
        expected = [str(fact["picture_tag"]) for fact in subject_facts]
        if pictures != expected:
            raise ValueError("reference picture order or subject mapping differs from input images")
        for picture_index, fact in enumerate(subject_facts):
            label = fact["variant_label"]
            if label:
                start = picture_matches[picture_index - 1].end() if picture_index else len(subject)
                preceding_picture_span = line[start:picture_matches[picture_index].start()]
                if str(label) not in preceding_picture_span:
                    raise ValueError("reference variant label differs from input image")
    expected_numbers = {int(str(fact["picture_tag"])[9:-1]) for fact in facts}
    all_text = "\n".join((
        wire.subject_definitions,
        wire.summary,
        wire.detailed_description,
        wire.overall_soundscape,
        wire.non_diegetic_music,
    ))
    if any(int(number) not in expected_numbers for number in _PICTURE_TAG.findall(all_text)):
        raise ValueError("reference output invented a picture")


def reference_facts(draft: DirectorDraft) -> list[dict[str, object]]:
    seen: set[str] = set()
    subjects: dict[str, int] = {}
    facts: list[dict[str, object]] = []
    for reference in draft.references:
        if reference.image_id in seen:
            continue
        seen.add(reference.image_id)
        subject_key = reference.character_id or f"image:{reference.image_id}"
        subject_number = subjects.setdefault(subject_key, len(subjects) + 1)
        facts.append({
            "picture_tag": f"<Picture {len(facts) + 1}>",
            "subject_tag": f"<Subject {subject_number}>",
            "image_id": reference.image_id,
            "asset_id": reference.asset_id,
            "character_id": reference.character_id,
            "variant_id": reference.variant_id,
            "variant_label": reference.variant_label,
            "asset_kind": reference.asset_kind,
        })
    return facts


def validate_optimized_director(
    draft: DirectorDraft,
    optimized: OptimizedDirector,
    capabilities: GenerationValidation | None = None,
    *,
    require_fingerprint: bool = False,
) -> GenerationValidation:
    """Validate shared source-to-wire invariants before preview or compilation."""
    validation = capabilities or validate_generation(draft)
    if (optimized.profile_id != H3_PROMPT_PROFILE_ID
            or optimized.profile_version != H3_PROMPT_PROFILE_VERSION):
        raise ValueError("optimized H3 profile does not match current writing rules")
    if optimized.revision != draft.revision:
        raise ValueError("optimized revision does not match draft revision")
    expected_fingerprint = director_draft_fingerprint(draft)
    if require_fingerprint and not optimized.draft_fingerprint:
        raise ValueError("prepared H3 input must include a draft fingerprint")
    if optimized.draft_fingerprint and optimized.draft_fingerprint != expected_fingerprint:
        raise ValueError("prepared H3 draft fingerprint does not match frozen draft")
    if optimized.route != validation.route:
        raise ValueError("optimized route does not match draft route")
    if len(optimized.segments) != len(draft.segments):
        raise ValueError("optimized segment count does not match draft")

    facts = reference_facts(draft) if validation.route == "h3_ref" else []
    for index, (source, result, aligned) in enumerate(zip(
        draft.segments, optimized.segments, validation.timeline, strict=True
    )):
        expected_mode = (
            "ref2va" if validation.route == "h3_ref"
            else {"i2v": "i2va", "fl2v": "fl2va"}[aligned.mode]
        )
        if result.segment_id != source.id:
            raise ValueError(f"optimized segment ID mismatch at segment {index}")
        if result.mode != expected_mode or result.wire.mode != expected_mode:
            raise ValueError(f"optimized segment mode mismatch at segment {index}")
        if abs(result.requested_duration_seconds - source.duration_seconds) > 1e-9:
            raise ValueError(f"optimized requested duration mismatch at segment {index}")
        if (result.frames != aligned.frames
                or abs(result.duration_seconds - aligned.duration_seconds) > 1e-9
                or abs(result.wire.duration_seconds - aligned.duration_seconds) > 1e-9):
            raise ValueError(f"optimized timing mismatch at segment {index}")
        if result.prompt != compile_h3_wire(result.wire):
            raise ValueError(f"optimized prompt does not match H3 wire at segment {index}")
        check_dialogue_identity(result.wire, locked_dialogue(source.prompt))
        if isinstance(result.wire, CanvasReferenceWire):
            check_reference_identity(result.wire, facts)
    return validation


def validate_prepared(
    draft: DirectorDraft,
    prepared: OptimizedDirector,
    capabilities: GenerationValidation | None = None,
) -> OptimizedDirector:
    """Fail-closed validation for an externally prepared structured H3 input."""
    validate_optimized_director(
        draft,
        prepared,
        capabilities,
        require_fingerprint=True,
    )
    return prepared


__all__ = [
    "director_draft_fingerprint",
    "locked_dialogue",
    "spoken_text",
    "h3_dialogue_language",
    "reference_facts",
    "check_dialogue_identity",
    "check_reference_identity",
    "validate_optimized_director",
    "validate_prepared",
]
