"""Post-merge character design; repair one proposal set, never re-extract source."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field
from novelvideo.character_visual.casting_brief import (
    DESIGN_PROMPT_VERSION, build_casting_dossier, profile_from_merged,
)
from novelvideo.character_visual.casting_proposals import validate_casting_proposals, blocking_casting_issues

from novelvideo.structured_extraction import (
    CharacterCandidate, CharacterProposalCandidate, ChunkCharacterOutput,
    MergedCharacter, _create_agent, _visual_proposal_quality_issues,
)


class CharacterDesignOutput(BaseModel):
    design_proposals: list[CharacterProposalCandidate] = Field(default_factory=list)
    limitation_reason: str = ""


CHARACTER_DESIGN_SYSTEM_PROMPT = """你是角色视觉设计师。输入是已从全篇原文合并并核验的单个角色事实。
尽可能返回该角色三套 creative design 提案，不改写人物事实，不再提取原文。
如果硬约束使三套实质差异不可行，可返回一到两套并在 limitation_reason 解释，禁止强行改变事实。
casting_dossier.hard_constraints 是不可更改的原文明示约束；interpretations 是有证据但不确定的解释。
每套必须给出 rationale、具体视觉细节和 casting_decisions：decision_id、attribute、value、reason、basis、fact_ids。
basis=evidence 必须引用实际 fact_ids，保持明示 value；basis=creative_choice 不得引用 fact_ids。
缺失或矛盾证据写明不足，不选一边。职业与反派身份不决定脸型、肤色、凶恶面相或残疾。
不得虚构疤痕、残疾或人生经历；原文明示漂亮/精致必须保留，并具体设计五官，禁止强制去美化。
剧情经历、身世与受伤史只能写进 rationale 作为设计依据；脸型、五官、发型、体态、个体特征、
asymmetry_detail 与 outfit_states 只描述图像上看得见的结构，不得夹带经历、来历或因果叙事。
恰好一套 recommended=true；每套至少三个 identity_anchors。
脸型、五官、发型、个体识别特征这四类中至少三类必须有实质差异，不能只换服装或颜色。
明确给出落在眉、眼、鼻、唇、嘴、耳、颧、颌、下巴、额头、发际线等位置的
asymmetry_detail，不能只写略有不对称。不得使用明星姓名或空泛审美词。
不得改变原文明示的物种、年龄、性别、伤疤、残疾或制服。
中性不等于人类；非人形动物保持本来解剖结构，无服饰依据不得添加衣领、衣服或配饰。
非人角色的三套方案必须在头部结构（角、喙、吻、颅型、耳形）、体态（体型、四肢、尾、翼）
与皮肤纹理（毛、羽、鳞、斑纹）上给出明显不同的选择，禁止只给三张相似的人脸；
每套都要用 casting_decisions 声明 species，并给出 facial_features 与 body_type。
outfit_states 使用 [{"state":"default","description":"服装或无服饰状态"}] 数组。
收到质量问题时只修正该角色的提案，不补写其他角色或故事事实。"""


def design_quality_issues(
    name: str, output: CharacterDesignOutput, *, other_proposals: Any = (),
    profile: Any = None, source_revision: str = "unspecified", style_revision: str = "unspecified",
    allow_legacy: bool = False,
) -> dict[str, Any]:
    from novelvideo.character_visual.models import CharacterDesignProposal

    if profile is not None and not allow_legacy:
        proposals = []
        for candidate in output.design_proposals:
            payload = candidate.model_dump()
            payload["outfit_states"] = {x.state: x.description for x in candidate.outfit_states}
            proposals.append(CharacterDesignProposal.model_validate(payload))
        issues = validate_casting_proposals(profile, proposals, None, limitation_reason=output.limitation_reason,
                   source_revision=source_revision, style_revision=style_revision,
                   existing_proposals=[CharacterDesignProposal.model_validate(p) for p in other_proposals])
        blocking = blocking_casting_issues(issues)
        return {name: {"casting": blocking}} if blocking else {}

    if len(output.design_proposals) != 3:
        return {name: {"proposal_set": ["exactly_three_proposals_required"]}}
    return _visual_proposal_quality_issues(ChunkCharacterOutput(characters=[
        CharacterCandidate(name=name, design_proposals=output.design_proposals)
    ]), existing_proposals=[CharacterDesignProposal.model_validate(p) for p in other_proposals])


async def design_merged_characters(
    characters: list[MergedCharacter], *, agent: Any = None, concurrency: int = 3,
    on_log: Any = None, on_progress: Any = None,
    load_checkpoint: Any = None, save_checkpoint: Any = None,
    existing_designs: dict[str, list[dict[str, Any]]] | None = None,
    roster_designs: dict[str, list[dict[str, Any]]] | None = None,
    source_revision: str | None = None, project_style: str = "",
    source_text: str | None = None,
) -> None:
    if not characters:
        return
    runner = None
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    completed = 0
    active_names = {item.name for item in characters}
    accepted = {name: proposals for name, proposals in (roster_designs or {}).items()
                if name not in active_names}
    accepted.update(existing_designs or {})
    profiles = {}
    revisions = {}

    def quality(name: str, output: CharacterDesignOutput, *, other_proposals: Any = (), allow_legacy: bool = False) -> dict[str, Any]:
        return design_quality_issues(name, output, other_proposals=other_proposals,
                                     profile=profiles[name], source_revision=revisions[name], style_revision=project_style or "unspecified", allow_legacy=allow_legacy)

    def other_proposals(name: str) -> list[dict[str, Any]]:
        return [p for other, proposals in accepted.items() if other != name for p in proposals]

    def payloads(output: CharacterDesignOutput) -> list[dict[str, Any]]:
        result = []
        for proposal in output.design_proposals:
            payload = proposal.model_dump(mode="json")
            payload["outfit_states"] = {x.state: x.description for x in proposal.outfit_states}
            result.append(payload)
        return result

    # Load completed designs before starting new calls. Otherwise a fast new
    # response can displace a valid checkpoint whose disk read finishes later.
    prepared: dict[str, tuple[str, str, CharacterDesignOutput | None, bool]] = {}
    for item in characters:
        facts = asdict(item)
        facts["voice_facts"] = item.voice_facts.model_dump(mode="json")
        facts.pop("design_proposals")
        facts.pop("design_accepted")
        facts.pop("design_limitation_reason")
        facts.pop("chunk_ids")
        facts["aliases"] = sorted(item.aliases)
        revision = source_revision or hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        profiles[item.name] = profile_from_merged(item, revision, source_text)
        revisions[item.name] = revision
        dossier = build_casting_dossier(profiles[item.name], None, revision, project_style or "unspecified")
        facts.update(casting_dossier=dossier.model_dump(mode="json"), source_revision=revision,
                     project_style=project_style, design_prompt_version=DESIGN_PROMPT_VERSION)
        prompt = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        key = "character-design-casting:" + hashlib.sha256(prompt.encode()).hexdigest()
        output = None
        legacy = False
        previous = (existing_designs or {}).get(item.name)
        if previous:
            candidate = CharacterDesignOutput(design_proposals=previous)
            if not quality(item.name, candidate, allow_legacy=True):
                output = candidate
                legacy = True
        cached = await load_checkpoint(key) if output is None and load_checkpoint else ""
        if cached:
            try:
                candidate = CharacterDesignOutput.model_validate_json(cached)
                if not quality(item.name, candidate, other_proposals=other_proposals(item.name)):
                    output = candidate
            except ValueError:
                pass
        if output is not None:
            accepted[item.name] = payloads(output)
        prepared[item.name] = (prompt, key, output, legacy)

    async def one(item: MergedCharacter) -> None:
        nonlocal runner, completed
        prompt, key, output, legacy = prepared[item.name]
        async with semaphore:
            if output is None:
                if runner is None:
                    runner = _create_agent(agent, output_type=CharacterDesignOutput,
                                           system_prompt=CHARACTER_DESIGN_SYSTEM_PROMPT)
                attempt_prompt = prompt
                for attempt in range(2):
                    if on_log:
                        on_log(f"正在设计角色 {item.name}（共 {len(characters)} 个去重角色）")
                    result = await runner.run(attempt_prompt)
                    raw = getattr(result, "output", result)
                    if isinstance(raw, BaseModel):
                        raw = raw.model_dump()
                    output = CharacterDesignOutput.model_validate(raw)
                    issues = quality(item.name, output, other_proposals=other_proposals(item.name))
                    if not issues:
                        # Reserve before the next await so concurrent completions
                        # cannot both accept mutually colliding designs.
                        accepted[item.name] = payloads(output)
                        if save_checkpoint:
                            await save_checkpoint(key, output.model_dump_json())
                        break
                    if on_log:
                        on_log(f"角色 {item.name} 视觉提案质量问题：{json.dumps(issues, ensure_ascii=False)}"
                               + ("；仅修正该角色（1/1）" if attempt == 0 else "；保留待处理，不重跑事实提取"))
                    attempt_prompt = (prompt + "\n仅修正视觉提案：" + json.dumps(issues, ensure_ascii=False)
                                      + "\n避免与其他角色方案撞脸：" + json.dumps(other_proposals(item.name), ensure_ascii=False))
            item.design_proposals = payloads(output)
            item.design_limitation_reason = output.limitation_reason
            item.design_accepted = not quality(
                item.name, output, other_proposals=other_proposals(item.name), allow_legacy=legacy
            )
            if item.design_accepted:
                accepted[item.name] = item.design_proposals
        completed += 1
        if on_progress:
            on_progress(0.55 + 0.2 * completed / len(characters),
                        f"角色设计已处理 {completed}/{len(characters)}：{item.name}")

    tasks = [asyncio.create_task(one(item)) for item in characters]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
