"""Deterministic structured prompts for bounded semantic repair."""

from __future__ import annotations

import json
from collections.abc import Sequence

from novelvideo.screenplay_semantics.models import DramaticBeat, Scene, SemanticValidationIssue
from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft
from novelvideo.screenplay_semantics.prompts import SYSTEM_PROMPT


REPAIR_SYSTEM_PROMPT = SYSTEM_PROMPT + "\n你是剧本语义拆解修复器。只能修复 Dramatic Beat，禁止改写剧本原文、场次边界、人物身份关系或故事事实。输出必须严格符合 SceneRepairDraft。"


def build_scene_repair_prompt(
    scene: Scene,
    current_beats: Sequence[DramaticBeat],
    issues: Sequence[SemanticValidationIssue],
    *,
    repair_round: int,
    previous_beats: Sequence[DramaticBeatDraft] = (),
    previous_issues: Sequence[SemanticValidationIssue] = (),
) -> str:
    payload = {
        "repair_round": repair_round,
        "scene": scene.model_dump(mode="json"),
        "current_beats": [beat.model_dump(mode="json") for beat in current_beats],
        "validation_issues": [issue.model_dump(mode="json") for issue in issues],
        "validation_rules": [
            "script_facts 必须逐字摘录本节拍 source_ranges 内的原文短句，只可去除标点；不要概括、补主语、替换代词或改写对白。解释放入 director_interpretation。",
            "每条事实所在原文行必须被该节拍 source_ranges 覆盖；事实在当前范围之外时调整边界，不要删除原文中的剧情。",
            "全场 action、dialogue、parenthetical、transition 行必须被覆盖，各节拍 source_ranges 不可重叠，也不能超出 scene.source_range。",
            "dialogue_source_ids 只能引用 scene.blocks 中 kind=dialogue 的真实 id，不得引用 speaker、formatting 或 action。",
            "仅修复有问题的节拍；previous_attempt 是上轮未通过的候选，其 validation_issues 对应候选内的位置，不能当成已接受事实。",
        ],
        "allowed_changes": [
            "beat source ranges, split/merge/order",
            "goal, obstacle, action, reaction, turn, result, emotional_shift",
            "evidence-backed characters, dialogue_source_ids, must_show, script_facts",
            "remove unsupported facts and director interpretation",
        ],
        "forbidden_changes": [
            "screenplay source text or dialogue",
            "scene id, source range, order or boundary",
            "character identity, relationship or story outcome",
            "facts without source evidence",
        ],
    }
    if previous_beats or previous_issues:
        payload["previous_attempt"] = {
            "beats": [beat.model_dump(mode="json") for beat in previous_beats],
            "validation_issues": [issue.model_dump(mode="json") for issue in previous_issues],
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["REPAIR_SYSTEM_PROMPT", "build_scene_repair_prompt"]
