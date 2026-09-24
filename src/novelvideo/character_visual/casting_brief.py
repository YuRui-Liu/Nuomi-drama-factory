"""Evidence-bound casting dossiers built from verified, already merged facts."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from .casting_models import CastingDecision, CastingRevision
from .models import CharacterNarrativeFact, CharacterNarrativeProfile, CharacterVisualWorkspace, SourceSpan

DESIGN_PROMPT_VERSION = "casting-dossier-v1"
_AGE_FIELDS = {"age_group", "age_range"}
_SENSITIVE = {"scar", "disability", "injury_state", "biography", "life_history"}
_PRESENTATION_FIELDS = {"grooming", "maintenance", "posture", "clothing_state", "hair_style", "presentation"}
_INTERPRETATION_SOURCES = {"occupation", "work_habits", "environment", "personality", "social_identity", "behavior"}
_ALIASES = {"elder": ("老人", "老年", "七十", "八十"), "youth": ("少年", "青年", "十九", "二十二"),
            "human": ("人类",), "cat": ("猫",), "dog": ("狗", "犬"),
            "male": ("男人", "男性", "男孩"), "female": ("女人", "女性", "女孩")}


class CastingDossier(BaseModel):
    character_id: str
    identity_id: str | None = None
    source_revision: str
    style_revision: str
    narrative: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: list[CharacterNarrativeFact] = Field(default_factory=list)
    interpretations: list[CharacterNarrativeFact] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    dossier_hash: str = ""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def fact_applies(fact: CharacterNarrativeFact, identity_id: str | None) -> bool:
    return fact.identity_id is None or fact.identity_id == identity_id


def _negated_at(text: str, start: int) -> bool:
    """Recognize local negation only; not a general entailment classifier."""
    return bool(re.search(r"(?:不(?:是|算|根据)?|并非|没有|非|not|never)\s*(?:一[只个位名]|an?|the)?\s*$",
                          text[max(0, start - 16):start], flags=re.IGNORECASE))


def evidence_supports(fact: CharacterNarrativeFact) -> bool:
    """Conservative lexical check; unverifiable paraphrases require review, never promotion."""
    if not fact.evidence.strip() or not fact.value.strip():
        return False
    matches = [match for term in (fact.value, *_ALIASES.get(fact.value.casefold(), ()))
               for match in re.finditer(re.escape(term), fact.evidence, flags=re.IGNORECASE)]
    return bool(matches) and not any(_negated_at(fact.evidence, match.start()) for match in matches)


def _age_interval(value: str) -> tuple[int, int] | None:
    groups = {"youth": (13, 35), "青年": (18, 35), "少年": (13, 18), "elder": (60, 120), "老年": (60, 120),
              "middle_aged": (36, 59), "中年": (36, 59), "child": (0, 12), "儿童": (0, 12)}
    if value in groups:
        return groups[value]
    number = re.search(r"(\d+)岁", value)
    if number:
        age = int(number[1])
        return age, age
    number = re.search(r"([零一二三四五六七八九十两]+)岁", value)
    if number:
        digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
        digits["两"] = 2
        text = number[1]
        if "十" in text:
            high, low = text.split("十", 1)
            age = digits.get(high, 1) * 10 + digits.get(low, 0)
        else:
            age = digits.get(text, 0)
        return age, age
    return None


def _facts_conflict(field: str, facts: list[CharacterNarrativeFact]) -> bool:
    if len({f.value for f in facts}) < 2:
        return False
    if field == "age":
        intervals = [_age_interval(f.value) for f in facts]
        if all(interval is not None for interval in intervals):
            return max(x[0] for x in intervals) > min(x[1] for x in intervals)
    return True


def build_casting_dossier(profile: CharacterNarrativeProfile, identity_id: str | None,
                          source_revision: str, style_revision: str) -> CastingDossier:
    dossier = CastingDossier(character_id=profile.character_id, identity_id=identity_id,
                            source_revision=source_revision, style_revision=style_revision,
                            narrative=profile.model_dump(exclude={"facts"}))
    by_field: dict[str, list[CharacterNarrativeFact]] = defaultdict(list)
    visual_ids = {f.fact_id for f in profile.visual_constraints()}
    for fact in profile.facts:
        if fact.identity_id and identity_id is None:
            dossier.issues.append(f"identity_required:{fact.fact_id}")
        if not fact_applies(fact, identity_id):
            continue
        if fact.trust != "trusted":
            dossier.issues.append(f"untrusted:{fact.fact_id}")
            continue
        if fact.source_revision and fact.source_revision != source_revision:
            dossier.issues.append(f"stale_source:{fact.fact_id}")
            continue
        if not evidence_supports(fact):
            dossier.issues.append(f"unverified_evidence:{fact.fact_id}")
            continue
        sourced = fact.model_copy(update={"source_revision": fact.source_revision or source_revision})
        if fact.fact_id in visual_ids:
            dossier.hard_constraints.append(sourced)
            by_field["age" if fact.field in _AGE_FIELDS else fact.field].append(sourced)
        else:
            dossier.interpretations.append(sourced)
    for field, facts in by_field.items():
        if field in {"age", "gender", "species", "face_shape", "body_type"} and _facts_conflict(field, facts):
            dossier.issues.append(f"conflicting:{field}:" + ",".join(f.fact_id for f in facts))
    if not dossier.hard_constraints:
        dossier.issues.append("missing:visual_evidence")
    dossier.dossier_hash = _digest(dossier.model_dump(exclude={"dossier_hash"}))
    return dossier


def validate_casting_decisions(profile: CharacterNarrativeProfile, decisions: list[CastingDecision],
                               identity_id: str | None) -> list[str]:
    issues = []
    facts = {f.fact_id: f for f in profile.facts}
    for decision in decisions:
        if decision.basis == "creative_choice":
            positive = re.sub(r"(?:无|没有|不要|不得|不添加|避免)(?:任何)?(?:伤疤|疤痕|残疾|失明|截肢)", "", decision.value)
            if decision.attribute in _SENSITIVE or any(term in positive for term in ("伤疤", "有疤", "疤痕", "残疾", "失明", "截肢", "童年遭", "幼年遭")):
                issues.append(f"invented_history:{decision.decision_id}")
            for fact in profile.visual_constraints():
                same_field = decision.attribute == fact.field or {decision.attribute, fact.field} <= _AGE_FIELDS
                if fact_applies(fact, identity_id) and evidence_supports(fact) and same_field and decision.value != fact.value:
                    issues.append(f"creative_overrides_fact:{decision.decision_id}:{fact.fact_id}")
            if decision.attribute in {"face", "build", "appearance", "face_shape", "facial_feature", "facial_features", "body_type", "skin_color", "scar", "disability", "distinctive_feature"}:
                if profile.occupation and profile.occupation in decision.reason:
                    issues.append(f"occupational_phenotype:{decision.decision_id}")
                personality_terms = {"反派", "邪恶", "恶毒", "善良", "正派", "正义", "奸诈", "villain", "evil",
                                     *profile.personality, profile.dramatic_function} - {""}
                if any(not _negated_at(decision.reason, match.start())
                       for term in personality_terms
                       for match in re.finditer(re.escape(term), decision.reason, flags=re.IGNORECASE)):
                    issues.append(f"personality_phenotype:{decision.decision_id}")
            continue
        for fact_id in decision.fact_ids:
            fact = facts.get(fact_id)
            if fact is None:
                issues.append(f"unknown_fact:{fact_id}")
                continue
            if not fact_applies(fact, identity_id):
                issues.append(f"wrong_identity:{fact_id}")
            if fact.trust != "trusted" or not evidence_supports(fact):
                issues.append(f"invalid_evidence:{fact_id}")
            compatible = decision.attribute == fact.field or {decision.attribute, fact.field} <= _AGE_FIELDS
            interpretation = decision.attribute in _PRESENTATION_FIELDS and fact.field in _INTERPRETATION_SOURCES
            if not compatible and not interpretation:
                issues.append(f"unsupported_attribute:{decision.decision_id}:{fact_id}")
            if fact.assertion == "explicit" and compatible and decision.value != fact.value:
                issues.append(f"changed_explicit_fact:{decision.decision_id}:{fact_id}")
    return list(dict.fromkeys(issues))


def build_casting_revision(workspace: CharacterVisualWorkspace, identity_id: str | None,
                           source_revision: str, style_revision: str) -> CastingRevision:
    from .casting_compiler import snapshot_digest

    dossier = build_casting_dossier(workspace.profile, identity_id, source_revision, style_revision)
    blocking = [issue for issue in dossier.issues if issue.startswith(("conflicting:", "identity_required:"))]
    if blocking:
        raise ValueError("; ".join(blocking))
    proposal_hashes = {p.proposal_id: snapshot_digest(p.model_dump(mode="json")) for p in workspace.design_proposals}
    selected = workspace.selected_proposal_id if workspace.selected_proposal_id in proposal_hashes else None
    revision_hash = snapshot_digest({"dossier_hash": dossier.dossier_hash,
        "proposal_hashes": proposal_hashes, "selected_proposal_id": selected})
    return CastingRevision(revision_id="casting-" + revision_hash[:24], character_id=workspace.character_id,
                           identity_id=identity_id, source_revision=source_revision, style_revision=style_revision,
                           profile_hash=dossier.dossier_hash,
                           decisions=[CastingDecision(decision_id="fact-" + f.fact_id, attribute=f.field,
                                      value=f.value, reason=f.evidence, basis="evidence", fact_ids=[f.fact_id])
                                      for f in dossier.hard_constraints],
                           proposal_ids=[p.proposal_id for p in workspace.design_proposals],
                           proposal_hashes=proposal_hashes, selected_proposal_id=selected)


def profile_from_merged(item: Any, source_revision: str, source_text: str | None = None) -> CharacterNarrativeProfile:
    """Reuse verified offsets; never infer facts from legacy descriptions or age defaults."""
    facts = []
    evidence_rows = list(item.evidence)
    voice = getattr(item, "voice_facts", None)
    if voice is not None and voice.provenance in {"source", "human"}:
        for field in ("species", "age_group"):
            value = getattr(voice, field)
            if not value or any(str(c).startswith(field + ":") for c in voice.conflicts):
                continue
            for quote in voice.evidence:
                if not (value in quote or any(term in quote for term in _ALIASES.get(value.casefold(), ()))):
                    continue
                if any(e.get("field") == field and e.get("value") == value for e in evidence_rows):
                    break
                offset = source_text.find(quote) if source_text is not None else -1
                matched = next((e for e in item.evidence if e.get("evidence_text") == quote), None)
                if offset < 0 and matched is None:
                    continue  # A verified location is required; do not fabricate one.
                evidence_rows.append(dict(field=field, value=value, evidence_text=quote,
                    source_start=offset if offset >= 0 else matched["source_start"],
                    source_end=offset + len(quote) if offset >= 0 else matched["source_end"]))
                break
    for index, evidence in enumerate(evidence_rows):
        field, value, quote = (str(evidence.get(k) or "").strip() for k in ("field", "value", "evidence_text"))
        if not field or not value or not quote:
            continue
        start, end = int(evidence.get("source_start", 0)), int(evidence.get("source_end", 0))
        start_line = source_text.count("\n", 0, start) + 1 if source_text is not None else 1
        end_line = source_text.count("\n", 0, end) + 1 if source_text is not None else start_line
        facts.append(CharacterNarrativeFact(fact_id=f"{item.name}-fact-{index + 1}", field=field, value=value,
                     evidence=quote, confidence=float(evidence.get("confidence", 1)),
                     source_span=SourceSpan(start_line=start_line, end_line=max(start_line, end_line)),
                     source_start=start, source_end=end, source_document=evidence.get("source_document") or "novel.txt",
                     source_revision=evidence.get("source_revision") or source_revision,
                     identity_id=evidence.get("identity_id"),
                     trust="legacy_untrusted" if source_text is not None and source_text[start:end] != quote else "trusted"))
    return CharacterNarrativeProfile(character_id=item.name, name=item.name, aliases=sorted(item.aliases),
                                    biography=item.biography or item.description, occupation=item.occupation or item.role,
                                    social_identity=item.social_identity, relationships=item.relationships,
                                    personality=item.personality, dramatic_function=item.dramatic_function, facts=facts)
