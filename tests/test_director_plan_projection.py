"""B 切片：分镜表投影（计划 → 表行）与 GEO / 手法意图字段。"""

from __future__ import annotations

from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan


def test_group_and_shot_have_new_fields():
    assert "geo_layout" in NarrativeGroupPlan.model_fields
    assert "technique_intent" in ShotPlan.model_fields
