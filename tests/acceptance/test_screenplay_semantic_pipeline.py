from __future__ import annotations

import pytest

from novelvideo.director_plan.models import (
    AssetRequirement,
    DirectorShotIntent,
    NarrativeGroupPlan,
    ShotPlan,
)
from novelvideo.director_plan.planner import DirectorPlanDraft, DirectorPlanInput
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.episode_source_store import EpisodeSource
from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft, SceneBeatDraft
from novelvideo.screenplay_semantics.service import ScreenplaySemanticService
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
from novelvideo.task_backend.runners.director_plan import _semantic_source_spans


SCRIPT = """---
episode: E001
title: 回声
---
# 第一章 雨夜来电
1-1 广播站 深夜 内
人物：林默
△林默撞向紧锁的门，门锁弹开。
林默：里面有人吗？
1-2 天台 黄昏 外
人物：林默
△林默扶住栏杆，抬头看见信号灯亮起。
林默：信号回来了。
"""


@pytest.mark.asyncio
async def test_existing_screenplay_reaches_reviewable_director_plan_without_paid_media(tmp_path):
    paid_media_calls: list[str] = []

    async def extract(scenes, *, concurrency):
        assert concurrency == 5
        drafts = []
        for scene in scenes:
            story = [block for block in scene.blocks if block.kind in {"action", "dialogue"}]
            drafts.append(SceneBeatDraft(
                scene_id=scene.id,
                beats=(DramaticBeatDraft(
                    source_ranges=(story[0].source_range.model_copy(
                        update={"end_line": story[-1].source_range.end_line}
                    ),),
                    characters=tuple(scene.characters),
                    goal="确认异常来源",
                    obstacle="环境阻断行动",
                    action=story[0].text.lstrip("△"),
                    reaction="角色观察到清晰变化",
                    turn="现场状态发生可见转折",
                    result=story[-1].text,
                    emotional_shift="紧张转为确认",
                    dialogue_source_ids=tuple(block.id for block in story if block.kind == "dialogue"),
                    estimated_duration_seconds=6,
                    must_show=(story[0].text.lstrip("△"),),
                    script_facts=tuple(block.text.lstrip("△") for block in story),
                ),),
            ))
        return tuple(drafts)

    source = EpisodeSource(
        episode_number=1,
        title="回声",
        content=SCRIPT,
        content_hash="source-hash",
        source_filename="E001.md",
        source_revision=1,
        downstream_stale=False,
        imported_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-01T00:00:00+00:00",
    )
    semantic_store = ScreenplaySemanticStore(tmp_path)
    semantic = await ScreenplaySemanticService(semantic_store, extractor=extract).build(source)
    active_semantic = semantic_store.activate(1, semantic.revision_id, expected_source_revision=1)

    class Planner:
        model_name = "acceptance-director"

        async def plan_episode(self, value: DirectorPlanInput):
            groups = []
            for index, scene in enumerate(value.scenes, start=1):
                scene_beats = [beat for beat in value.dramatic_beats if beat.scene_id == scene.id]
                source_ids = tuple(block.id for block in scene.blocks)
                beat_ids = tuple(beat.id for beat in scene_beats)
                groups.append(NarrativeGroupPlan(
                    id=f"ng-{index:02d}", ordinal=index,
                    source_span_ids=source_ids, dramatic_beat_ids=beat_ids,
                    scene_anchor=scene.location or scene.heading,
                    time_anchor=scene.time_of_day or "未指定",
                    objective=scene_beats[0].goal,
                    visible_turn=scene_beats[-1].turn,
                    relation_to_previous="single" if index == 1 else "progressive",
                    shots=(ShotPlan(
                        id=f"shot-{index:02d}", source_span_ids=source_ids,
                        dramatic_beat_ids=beat_ids,
                        subject=scene.characters[0], action=scene_beats[0].action,
                        visible_start_state="动作尚未完成",
                        visible_end_state=scene_beats[-1].result,
                        intent=DirectorShotIntent(
                            narrative_purpose="呈现本场核心转折",
                            audience_attention="人物动作及结果",
                            emotional_effect="让观众感到局势改变",
                            continuity_strategy="保持人物轴线和空间方向",
                        ),
                        asset_requirements=(AssetRequirement(
                            kind="scene_base", entity_key=scene.location or scene.heading,
                            evidence_source_ids=source_ids, required=True,
                        ),),
                        duration_seconds=6,
                    ),),
                ))
            return DirectorPlanDraft(groups=tuple(groups))

        async def repair_group(self, value):  # pragma: no cover - valid draft
            raise AssertionError("valid acceptance draft must not need repair")

    director = await DirectorPlanService(
        DirectorPlanStore(tmp_path), Planner()
    ).create_draft(DirectorPlanInput(
        episode=1,
        source_script_hash=source.content_hash,
        source_spans=_semantic_source_spans(active_semantic),
        semantic_revision_id=active_semantic.revision_id,
        scenes=active_semantic.scenes,
        dramatic_beats=active_semantic.beats,
        relevant_bible={}, aspect_ratio="9:16", style_director={},
        project_style_snapshot_id="style-acceptance",
    ))

    story_line_count = sum(
        1 for scene in active_semantic.scenes for block in scene.blocks
        if block.kind in {"action", "dialogue"}
    )
    assert len(active_semantic.scenes) == 2
    assert len(active_semantic.beats) < story_line_count
    assert all(beat.source_ranges for beat in active_semantic.beats)
    assert all(shot.intent is not None for group in director.groups for shot in group.shots)
    assert all(1 <= len(group.shots) <= 4 for group in director.groups)
    assert director.semantic_revision_id == active_semantic.revision_id
    assert paid_media_calls == []
