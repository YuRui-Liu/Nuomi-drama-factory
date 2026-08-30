"""Evidence-bound character extraction without a knowledge graph.

Candidates are extracted independently from deterministic source chunks.  A
candidate is accepted only when its name and every evidence quote are present
in that chunk, which prevents unsupported model output from becoming a formal
asset.  The module has no Cognee import or graph/runtime dependency.
"""

from __future__ import annotations

import asyncio
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


class CharacterCandidate(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str = ""
    face: str = ""
    build: str = ""
    gender: str = ""
    description: str = ""
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
    evidence: list[dict[str, Any]] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)


CHARACTER_EXTRACTION_SYSTEM_PROMPT = """你是剧本/小说角色抽取器。只根据输入片段回答。

规则：
- name 必须逐字出现在片段中；旁白、画外音、群体和职务称呼不是角色。
- 每个角色至少提供一条 evidence.quote，引用必须逐字来自片段。
- aliases 仅填写片段明确声明的别名；不确定是否同一人就不要合并。
- role 只填写原文明确表达的剧情身份；face/build 只提取原文明示的视觉特征。
- 不得补写片段以外的角色、经历或外貌。"""


def normalize_character_name(value: str) -> str:
    cleaned = (value or "").replace("　", " ").strip()
    cleaned = cleaned.strip("：:，,。.、·「」『』\"'()（）【】[]")
    return " ".join(cleaned.split())


def is_generic_address(name: str) -> bool:
    return normalize_character_name(name) in GENERIC_ADDRESS_TERMS


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
            for alias in aliases:
                alias_owner.setdefault(alias, canonical)
    return sorted(merged.values(), key=lambda item: item.name)


def _create_agent(agent: Any = None) -> Any:
    if agent is not None:
        return agent
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
) -> list[MergedCharacter]:
    """Extract and validate characters with bounded model concurrency."""
    source_chunks = list(chunks)
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
    return merge_character_candidates(outcomes)


__all__ = [
    "CharacterCandidate",
    "CharacterEvidence",
    "ChunkCharacterOutput",
    "MergedCharacter",
    "extract_characters_from_chunks",
    "is_generic_address",
    "merge_character_candidates",
    "normalize_character_name",
    "verify_evidence",
]
