"""Deterministic structured prompts for bounded semantic repair."""

from __future__ import annotations

import json
from collections.abc import Sequence

from novelvideo.screenplay_semantics.models import DramaticBeat, Scene, SemanticValidationIssue


REPAIR_SYSTEM_PROMPT = """你是剧本语义拆解修复器。只能修复 Dramatic Beat，禁止改写剧本原文、场次边界、人物身份关系或故事事实。输出必须严格符合 SceneRepairDraft。"""


def build_scene_repair_prompt(
    scene: Scene,
    current_beats: Sequence[DramaticBeat],
    issues: Sequence[SemanticValidationIssue],
    *,
    repair_round: int,
) -> str:
    payload = {
        "repair_round": repair_round,
        "scene": scene.model_dump(mode="json"),
        "current_beats": [beat.model_dump(mode="json") for beat in current_beats],
        "validation_issues": [issue.model_dump(mode="json") for issue in issues],
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
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["REPAIR_SYSTEM_PROMPT", "build_scene_repair_prompt"]
