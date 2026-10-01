"""Evidence-bound character extraction without a knowledge graph.

Candidates are extracted independently from deterministic source chunks.  A
candidate is accepted only when its name and every evidence quote are present
in that chunk, which prevents unsupported model output from becoming a formal
asset.  The module has no Cognee import or graph/runtime dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field, field_validator

from novelvideo.story_analysis import SourceChunk
from novelvideo.character_voice_facts import VoiceFacts, merge_voice_facts
from novelvideo.character_visual.casting_models import CastingDecision


GENERIC_ADDRESS_TERMS = {
    "母亲", "父亲", "爸爸", "妈妈", "哥哥", "姐姐", "弟弟", "妹妹",
    "医生", "护士", "老师", "司机", "老板", "警察", "士兵", "路人",
    "村民", "男人", "女人", "老人", "孩子", "男主", "女主", "主角",
    "配角", "反派", "旁白", "画外音", "众人",
}


class CharacterEvidence(BaseModel):
    quote: str = Field(description="逐字来自当前片段的原文引用")
    kind: str = Field(default="mention", description="mention/dialogue/description")
    field: str = Field(default="", description="该证据支持的人物字段")
    value: str = Field(default="", description="从证据中归纳的字段值")
    confidence: float = Field(default=1.0, ge=0, le=1)
    identity_id: str | None = None


class CharacterOutfitStateCandidate(BaseModel):
    state: str = Field(description="服装状态名称，例如 default、work、ceremony")
    description: str = Field(description="该状态下的完整服装描述")


class CharacterProposalCandidate(BaseModel):
    proposal_id: str
    title: str
    rationale: str = ""
    face_shape: str | None = None
    facial_features: list[str] = Field(default_factory=list)
    hair_style: str | None = None
    body_type: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    outfit_states: list[CharacterOutfitStateCandidate] = Field(default_factory=list)
    identity_anchors: list[str] = Field(default_factory=list)
    asymmetry_detail: str = ""
    recommended: bool = False
    casting_decisions: list[CastingDecision] = Field(default_factory=list)

    @field_validator("outfit_states", mode="before")
    @classmethod
    def accept_legacy_outfit_state_mapping(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [
                {"state": state, "description": description}
                for state, description in value.items()
            ]
        return value


class CharacterFacts(BaseModel):
    name: str
    voice_facts: VoiceFacts = Field(default_factory=VoiceFacts)
    aliases: list[str] = Field(default_factory=list)
    role: str = ""
    face: str = ""
    build: str = ""
    gender: str = ""
    description: str = ""
    biography: str = ""
    occupation: str = ""
    social_identity: str = ""
    relationships: list[str] = Field(default_factory=list)
    personality: list[str] = Field(default_factory=list)
    dramatic_function: str = ""
    evidence: list[CharacterEvidence] = Field(default_factory=list)


class CharacterCandidate(CharacterFacts):
    design_proposals: list[CharacterProposalCandidate] = Field(default_factory=list)


class ChunkCharacterFacts(BaseModel):
    characters: list[CharacterFacts] = Field(default_factory=list)


class ChunkCharacterOutput(BaseModel):
    characters: list[CharacterCandidate] = Field(default_factory=list)


@dataclass
class MergedCharacter:
    name: str
    voice_facts: VoiceFacts = field(default_factory=VoiceFacts)
    aliases: set[str] = field(default_factory=set)
    role: str = ""
    face: str = ""
    build: str = ""
    gender: str = ""
    description: str = ""
    biography: str = ""
    occupation: str = ""
    social_identity: str = ""
    relationships: list[str] = field(default_factory=list)
    personality: list[str] = field(default_factory=list)
    dramatic_function: str = ""
    design_proposals: list[dict[str, Any]] = field(default_factory=list)
    design_accepted: bool = False
    design_limitation_reason: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)


CHARACTER_EXTRACTION_SYSTEM_PROMPT = """你是剧本/小说角色事实抽取器。只根据输入片段回答。

规则：
- name 必须逐字出现在片段中；旁白、画外音、群体和职务称呼不是角色。
- 每个角色至少提供一条 evidence.quote，引用必须逐字来自片段。
- aliases 仅填写片段明确声明的别名；不确定是否同一人就不要合并。
- role 只填写原文明确表达的剧情身份；face/build 只提取原文明示的视觉特征。
- 为角色整理人物小传：biography、occupation、social_identity、relationships、
  personality、dramatic_function；事实必须能由 evidence 支持，推断必须克制。
- 不得根据姓名猜测地域、阶层或外貌，不得补写片段以外的事实。
- voice_facts 只记原文明示的物种 species、年龄段 age_group、声音特征 voice_traits，
  以及发声模式 vocalization_mode（dialogue/nonverbal/both/none/unknown）。
  每项声音事实必须有 voice_facts.evidence 中逐字原文支持；年龄不明留空，发声不明用 unknown。
  只有明确不会说话、只发叫声才记 nonverbal；只有明确无发声才记 none。
  不得由物种、性别、外貌推断会不会说话；非人类也可以 dialogue。
  provenance 使用 source；人工确认 human 不能由抽取器填写。矛盾写入 conflicts，不擅自取舍。
- 本阶段只提取事实，不生成视觉设计、服装创作或外貌备选方案。"""


def redact_locked_characters(
    chunks: Iterable[SourceChunk], excluded_names: set[str] | None
) -> list[SourceChunk]:
    """Hide locked names from model input while preserving source offsets."""
    names = sorted(
        {
            normalize_character_name(name)
            for name in (excluded_names or set())
            if normalize_character_name(name)
        },
        key=len,
        reverse=True,
    )
    if not names:
        return list(chunks)
    redacted: list[SourceChunk] = []
    for chunk in chunks:
        text = chunk.text
        for name in names:
            text = text.replace(name, "□" * len(name))
        redacted.append(replace(chunk, text=text))
    return redacted


def normalize_character_name(value: str) -> str:
    cleaned = (value or "").replace("　", " ").strip()
    cleaned = cleaned.strip("：:，,。.、·「」『』\"'()（）【】[]")
    return " ".join(cleaned.split())


def is_generic_address(name: str) -> bool:
    normalized = normalize_character_name(name)
    return (
        normalized in GENERIC_ADDRESS_TERMS
        or bool(normalized) and set(normalized) <= {"□", "■", "�"}
    )


def _name_attested(name: str, text: str) -> bool:
    normalized = normalize_character_name(name)
    return bool(normalized and normalized in text)


def verify_evidence(candidate: CharacterCandidate, chunk: SourceChunk) -> list[dict[str, Any]]:
    """Return exact source spans for supported evidence, otherwise no evidence."""
    if is_generic_address(candidate.name) or not _name_attested(candidate.name, chunk.text):
        return []
    verified: list[dict[str, Any]] = []
    cursor = 0
    for item in candidate.evidence:
        quote = str(item.quote or "").strip()
        if not quote:
            continue
        local = chunk.text.find(quote, cursor)
        if local < 0:
            local = chunk.text.find(quote)
        if local < 0:
            continue
        cursor = local + len(quote)
        verified.append(
            {
                "chunk_id": chunk.chunk_id,
                "source_start": chunk.source_start + local,
                "source_end": chunk.source_start + local + len(quote),
                "evidence_kind": str(item.kind or "mention"),
                "evidence_text": quote,
                "field": str(item.field or "").strip(),
                "value": str(item.value or "").strip(),
                "confidence": float(item.confidence),
                "identity_id": item.identity_id,
            }
        )
    return verified


def merge_character_candidates(
    outcomes: Iterable[tuple[SourceChunk, ChunkCharacterOutput]],
) -> list[MergedCharacter]:
    """Conservatively merge exact names and explicit aliases only."""
    merged: dict[str, MergedCharacter] = {}
    alias_owner: dict[str, str] = {}
    for chunk, output in outcomes:
        for candidate in output.characters:
            name = normalize_character_name(candidate.name)
            evidence = verify_evidence(candidate, chunk)
            if not name or not evidence:
                continue
            aliases = {
                normalized
                for value in candidate.aliases
                if (normalized := normalize_character_name(value))
                and normalized != name
                and _name_attested(normalized, chunk.text)
            }
            canonical = alias_owner.get(name, name)
            item = merged.setdefault(canonical, MergedCharacter(name=canonical))
            item.aliases.update(aliases)
            item.chunk_ids.add(chunk.chunk_id)
            item.evidence.extend(evidence)
            voice = candidate.voice_facts
            if voice.evidence and all(
                quote.strip() and quote in chunk.text
                and any(_name_attested(anchor, quote) for anchor in {name, *aliases})
                for quote in voice.evidence
            ):
                item.voice_facts = merge_voice_facts(
                    item.voice_facts, voice.model_copy(update={"provenance": "source"})
                )
            if candidate.role and not item.role:
                item.role = candidate.role.strip()
            if candidate.face and len(candidate.face.strip()) > len(item.face):
                item.face = candidate.face.strip()
            if candidate.build and len(candidate.build.strip()) > len(item.build):
                item.build = candidate.build.strip()
            if candidate.gender and not item.gender:
                item.gender = candidate.gender
            if candidate.description and len(candidate.description) > len(item.description):
                item.description = candidate.description
            if candidate.biography and len(candidate.biography) > len(item.biography):
                item.biography = candidate.biography.strip()
            if candidate.occupation and not item.occupation:
                item.occupation = candidate.occupation.strip()
            if candidate.social_identity and not item.social_identity:
                item.social_identity = candidate.social_identity.strip()
            for relationship in candidate.relationships:
                value = relationship.strip()
                if value and value not in item.relationships:
                    item.relationships.append(value)
            for trait in candidate.personality:
                value = trait.strip()
                if value and value not in item.personality:
                    item.personality.append(value)
            if (
                candidate.dramatic_function
                and len(candidate.dramatic_function) > len(item.dramatic_function)
            ):
                item.dramatic_function = candidate.dramatic_function.strip()
            proposals: list[dict[str, Any]] = []
            for proposal in candidate.design_proposals:
                payload = proposal.model_dump(mode="json")
                payload["outfit_states"] = {
                    outfit.state.strip(): outfit.description.strip()
                    for outfit in proposal.outfit_states
                    if outfit.state.strip()
                }
                proposals.append(payload)
            if len(proposals) == 3 and (
                not item.design_proposals
                or sum(len(str(value)) for value in proposals)
                > sum(len(str(value)) for value in item.design_proposals)
            ):
                item.design_proposals = proposals
            for alias in aliases:
                alias_owner.setdefault(alias, canonical)
    return sorted(merged.values(), key=lambda item: item.name)


def _create_agent(
    agent: Any = None, *, output_type: type[BaseModel] = ChunkCharacterFacts,
    system_prompt: str = CHARACTER_EXTRACTION_SYSTEM_PROMPT,
) -> Any:
    if agent is not None:
        return agent
    from novelvideo.text_task_runtime.runtime import (
        StructuredRuntimeAgent,
        current_text_task_runtime,
    )

    runtime = current_text_task_runtime()
    if runtime is not None:
        return StructuredRuntimeAgent(
            runtime,
            output_type=output_type,
            system_prompt=system_prompt,
        )
    from pydantic_ai import Agent, PromptedOutput
    from novelvideo.config import (
        get_newapi_text_pydantic_model,
        get_newapi_text_pydantic_model_settings,
    )

    return Agent(
        get_newapi_text_pydantic_model(
            "CHARACTER_BUILD_MODEL", "gemini-3-flash-preview"
        ),
        system_prompt=system_prompt,
        model_settings=get_newapi_text_pydantic_model_settings(
            "CHARACTER_BUILD_THINKING_LEVEL", "low"
        ),
        # DeepSeek thinking models reject native tool output because it adds
        # tool_choice. PromptedOutput preserves typed validation without tools.
        output_type=PromptedOutput(output_type),
        name="Structured Character Extractor",
    )


def _visual_proposal_quality_issues(
    output: ChunkCharacterOutput, *, existing_proposals: Any = (),
) -> dict[str, Any]:
    """Return deterministic proposal issues suitable for a focused model retry."""
    from novelvideo.character_visual.models import CharacterDesignProposal
    from novelvideo.character_visual.proposals import (
        ProposalQualityError,
        validate_design_proposals,
    )

    rejected: dict[str, Any] = {}
    for character in output.characters:
        if not character.design_proposals:
            continue
        proposals = [
            CharacterDesignProposal.model_validate(
                {
                    **proposal.model_dump(mode="json"),
                    "outfit_states": {
                        item.state.strip(): item.description.strip()
                        for item in proposal.outfit_states
                        if item.state.strip()
                    },
                }
            )
            for proposal in character.design_proposals
        ]
        try:
            validate_design_proposals(proposals, existing_proposals=existing_proposals)
        except ProposalQualityError as exc:
            rejected[character.name] = {
                proposal.proposal_id: proposal.quality_issues
                for proposal in exc.proposals
                if proposal.quality_issues
            }
        except ValueError as exc:
            rejected[character.name] = {"proposal_set": [str(exc)]}
    return rejected


async def extract_characters_from_chunks(
    chunks: Iterable[SourceChunk],
    *,
    agent: Any = None,
    concurrency: int = 3,
    on_log: Callable[[str], None] | None = None,
    excluded_names: set[str] | None = None,
    design_agent: Any = None,
    on_progress: Callable[[float, str], None] | None = None,
    load_checkpoint: Any = None,
    save_checkpoint: Any = None,
    existing_designs: dict[str, list[dict[str, Any]]] | None = None,
    roster_designs: dict[str, list[dict[str, Any]]] | None = None,
    source_revision: str | None = None,
    project_style: str = "",
    source_text: str | None = None,
) -> list[MergedCharacter]:
    """Extract facts, merge evidence, then design each unique character once."""
    from novelvideo.character_design_stage import design_merged_characters

    excluded = {
        normalize_character_name(name)
        for name in (excluded_names or set())
        if normalize_character_name(name)
    }
    source_chunks = redact_locked_characters(chunks, excluded)
    if not source_chunks:
        return []
    runner = _create_agent(agent)
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    completed = 0

    async def one(chunk: SourceChunk) -> tuple[SourceChunk, ChunkCharacterOutput]:
        nonlocal completed
        key = "character-facts-v3-voice:" + hashlib.sha256(chunk.text.encode()).hexdigest()
        async with semaphore:
            cached = await load_checkpoint(key) if load_checkpoint else ""
            facts = None
            if cached:
                try:
                    facts = ChunkCharacterFacts.model_validate_json(cached)
                except ValueError:
                    pass
            if facts is None:
                if on_log:
                    on_log(f"正在提取 {chunk.section_label} 的角色事实（共 {len(source_chunks)} 段）")
                result = await runner.run(chunk.text)
                raw = getattr(result, "output", result)
                if isinstance(raw, BaseModel):
                    raw = raw.model_dump()
                facts = ChunkCharacterFacts.model_validate(raw)
                if save_checkpoint:
                    await save_checkpoint(key, facts.model_dump_json())
        output = ChunkCharacterOutput.model_validate(facts.model_dump())
        completed += 1
        message = f"角色事实已分析 {completed}/{len(source_chunks)}：{chunk.section_label}"
        if on_progress:
            on_progress(0.1 + 0.45 * completed / len(source_chunks), message)
        elif on_log:
            on_log(message)
        return chunk, output

    tasks = [asyncio.create_task(one(chunk)) for chunk in source_chunks]
    try:
        outcomes = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    merged = [
        item
        for item in merge_character_candidates(outcomes)
        if normalize_character_name(item.name) not in excluded
    ]
    await design_merged_characters(
        merged, agent=design_agent, concurrency=concurrency,
        on_log=on_log, on_progress=on_progress,
        load_checkpoint=load_checkpoint, save_checkpoint=save_checkpoint,
        existing_designs=existing_designs,
        roster_designs=roster_designs,
        source_revision=source_revision or hashlib.sha256("".join(chunk.text for chunk in source_chunks).encode()).hexdigest(),
        project_style=project_style,
        source_text=source_text,
    )
    return merged


__all__ = [
    "CharacterCandidate",
    "CharacterEvidence",
    "CharacterOutfitStateCandidate",
    "CharacterProposalCandidate",
    "ChunkCharacterOutput",
    "MergedCharacter",
    "extract_characters_from_chunks",
    "is_generic_address",
    "merge_character_candidates",
    "normalize_character_name",
    "redact_locked_characters",
    "verify_evidence",
]
