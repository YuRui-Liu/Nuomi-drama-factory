"""Evidence-bound character extraction without a knowledge graph.

Candidates are extracted independently from deterministic source chunks.  A
candidate is accepted only when its name and every evidence quote are present
in that chunk, which prevents unsupported model output from becoming a formal
asset.  The module has no Cognee import or graph/runtime dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field

from novelvideo.story_analysis import SourceChunk


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


class CharacterProposalCandidate(BaseModel):
    proposal_id: str
    title: str
    rationale: str = ""
    face_shape: str | None = None
    facial_features: list[str] = Field(default_factory=list)
    hair_style: str | None = None
    body_type: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    outfit_states: dict[str, str] = Field(default_factory=dict)
    identity_anchors: list[str] = Field(default_factory=list)
    asymmetry_detail: str = ""
    recommended: bool = False


class CharacterCandidate(BaseModel):
    name: str
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
    design_proposals: list[CharacterProposalCandidate] = Field(default_factory=list)
    evidence: list[CharacterEvidence] = Field(default_factory=list)


class ChunkCharacterOutput(BaseModel):
    characters: list[CharacterCandidate] = Field(default_factory=list)


@dataclass
class MergedCharacter:
    name: str
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
    evidence: list[dict[str, Any]] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)


CHARACTER_EXTRACTION_SYSTEM_PROMPT = """你是剧本/小说角色抽取器。只根据输入片段回答。

规则：
- name 必须逐字出现在片段中；旁白、画外音、群体和职务称呼不是角色。
- 每个角色至少提供一条 evidence.quote，引用必须逐字来自片段。
- aliases 仅填写片段明确声明的别名；不确定是否同一人就不要合并。
- role 只填写原文明确表达的剧情身份；face/build 只提取原文明示的视觉特征。
- 为角色整理人物小传：biography、occupation、social_identity、relationships、
  personality、dramatic_function；事实必须能由 evidence 支持，推断必须克制。
- 在剧本明示视觉事实约束下，提供三套结构差异明显的 creative design 提案，
  其中恰好一套 recommended=true。每套至少 3 个 identity_anchors，并包含
  asymmetry_detail 或其他可重复识别的非对称细节。
- 不得根据姓名猜测地域、阶层或外貌；不得使用明星姓名；不得只写“漂亮、帅气、
  高级脸”等空泛审美词。提案不得改变剧本明示的年龄、性别、伤疤、残疾或制服。
- 不得补写片段以外的剧情事实。创意外貌必须明确放在 design_proposals 中，
  不得伪装成剧本明示事实。"""


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
            proposals = [
                proposal.model_dump(mode="json") for proposal in candidate.design_proposals
            ]
            if len(proposals) == 3 and (
                not item.design_proposals
                or sum(len(str(value)) for value in proposals)
                > sum(len(str(value)) for value in item.design_proposals)
            ):
                item.design_proposals = proposals
            for alias in aliases:
                alias_owner.setdefault(alias, canonical)
    return sorted(merged.values(), key=lambda item: item.name)


def _create_agent(agent: Any = None) -> Any:
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
            output_type=ChunkCharacterOutput,
            system_prompt=CHARACTER_EXTRACTION_SYSTEM_PROMPT,
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
        system_prompt=CHARACTER_EXTRACTION_SYSTEM_PROMPT,
        model_settings=get_newapi_text_pydantic_model_settings(
            "CHARACTER_BUILD_THINKING_LEVEL", "low"
        ),
        # DeepSeek thinking models reject native tool output because it adds
        # tool_choice. PromptedOutput preserves typed validation without tools.
        output_type=PromptedOutput(ChunkCharacterOutput),
        name="Structured Character Extractor",
    )


async def extract_characters_from_chunks(
    chunks: Iterable[SourceChunk],
    *,
    agent: Any = None,
    concurrency: int = 3,
    on_log: Callable[[str], None] | None = None,
    excluded_names: set[str] | None = None,
) -> list[MergedCharacter]:
    """Extract and validate characters with bounded model concurrency."""
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

    async def one(chunk: SourceChunk) -> tuple[SourceChunk, ChunkCharacterOutput]:
        async with semaphore:
            result = await runner.run(chunk.text)
        output = getattr(result, "output", result)
        if not isinstance(output, ChunkCharacterOutput):
            output = ChunkCharacterOutput.model_validate(output)
        if on_log:
            on_log(f"已分析 {chunk.section_label}")
        return chunk, output

    outcomes = await asyncio.gather(*(one(chunk) for chunk in source_chunks))
    return [
        item
        for item in merge_character_candidates(outcomes)
        if normalize_character_name(item.name) not in excluded
    ]


__all__ = [
    "CharacterCandidate",
    "CharacterEvidence",
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
