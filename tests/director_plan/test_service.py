from __future__ import annotations

import pytest

from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan, SourceSpan
from novelvideo.director_plan.planner import DirectorPlanDraft, DirectorPlanInput
from novelvideo.director_plan.service import (
    DirectorPlanPlanningError,
    DirectorPlanService,
)
from novelvideo.director_plan.store import DirectorPlanStore


def span(id: str, ordinal: int) -> SourceSpan:
    return SourceSpan(id=id, ordinal=ordinal, scene="A", time="NIGHT", text=id)


def group(
    id: str, ordinal: int, source_id: str, objective: str = "goal"
) -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id=id,
        ordinal=ordinal,
        source_span_ids=(source_id,),
        scene_anchor="A",
        time_anchor="NIGHT",
        objective=objective,
        visible_turn="changed",
        relation_to_previous="single" if ordinal == 1 else "progressive",
        shots=(
            ShotPlan(
                id=f"shot-{ordinal}",
                source_span_ids=(source_id,),
                subject="actor",
                action="moves",
                visible_start_state="left",
                visible_end_state="right",
                duration_seconds=2,
            ),
        ),
    )


def episode() -> DirectorPlanInput:
    return DirectorPlanInput(
        episode=1,
        source_script_hash="hash",
        source_spans=tuple(span(f"s{i}", i) for i in range(1, 4)),
        relevant_bible={},
        aspect_ratio="9:16",
        style_director={},
        project_style_snapshot_id="style-1",
    )


class FakePlanner:
    def __init__(self, initial, repairs):
        self.initial, self.repairs, self.full_calls, self.repair_calls = (
            initial,
            repairs,
            0,
            [],
        )

    async def plan_episode(self, value):
        self.full_calls += 1
        return self.initial

    async def repair_group(self, value):
        self.repair_calls.append(value)
        return self.repairs.pop(0)


@pytest.mark.asyncio
async def test_service_repairs_only_failed_group_preserves_valid_groups_and_active(
    tmp_path,
) -> None:
    good1, bad, good3 = (
        group("g1", 1, "s1"),
        group("g2", 2, "s2", " "),
        group("g3", 3, "s3"),
    )
    planner = FakePlanner(
        DirectorPlanDraft(groups=(good1, bad, good3)), [group("g2", 2, "s2", "fixed")]
    )
    store = DirectorPlanStore(tmp_path)
    result = await DirectorPlanService(store, planner).create_draft(episode())
    assert planner.full_calls == 1
    assert [call.failed_group.id for call in planner.repair_calls] == ["g2"]
    assert (
        planner.repair_calls[0].previous_group.id,
        planner.repair_calls[0].next_group.id,
    ) == ("g1", "g3")
    assert result.status == "review_required" and result.validation_report.passed
    assert result.groups[0] is good1 and result.groups[2] is good3
    assert store.load_active(1) is None
    assert [item.status for item in store.list(1)] == ["validating", "review_required"]


@pytest.mark.asyncio
async def test_service_repairs_at_most_twice_then_saves_failed(tmp_path) -> None:
    bad = group("g1", 1, "s1", "")
    planner = FakePlanner(DirectorPlanDraft(groups=(bad,)), [bad, bad])
    store = DirectorPlanStore(tmp_path)
    result = await DirectorPlanService(store, planner).create_draft(
        episode().model_copy(update={"source_spans": (span("s1", 1),)})
    )
    assert len(planner.repair_calls) == 2
    assert result.status == "failed" and not result.validation_report.passed
    assert store.load_active(1) is None


@pytest.mark.asyncio
async def test_service_saves_failed_revision_and_codes_provider_errors(
    tmp_path,
) -> None:
    class BrokenPlanner:
        async def plan_episode(self, value):
            raise RuntimeError("provider unavailable")

    store = DirectorPlanStore(tmp_path)
    with pytest.raises(DirectorPlanPlanningError) as error:
        await DirectorPlanService(store, BrokenPlanner()).create_draft(episode())
    assert error.value.code == "director_plan_provider_error"
    assert [item.status for item in store.list(1)] == ["failed"]
    assert store.load_active(1) is None
