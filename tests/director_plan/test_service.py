from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.cinematography import (
    MotivatedLight,
    ShotCinematography,
    SubjectBlocking,
)
from novelvideo.director_plan.models import AssetRequirement, NarrativeGroupPlan, ShotPlan, SourceSpan
from novelvideo.director_plan.planner import (
    DirectorPlanDraft,
    DirectorPlanInput,
    DirectorPlanner,
)
from novelvideo.director_plan.service import (
    DirectorPlanPlanningError,
    DirectorPlanService,
)
from novelvideo.director_plan.store import DirectorPlanStore


def span(id: str, ordinal: int) -> SourceSpan:
    return SourceSpan(id=id, ordinal=ordinal, scene="A", time="NIGHT", text=id)


def shot_cinematography(source_id: str) -> ShotCinematography:
    return ShotCinematography(
        source="director_plan",
        source_ids=(source_id,),
        axis="actor-platform axis",
        camera_side="platform side",
        screen_direction="left to right",
        subjects=(
            SubjectBlocking(
                subject_id="actor",
                world_position="one step left of the platform column",
                screen_position="left third of frame",
                facing="toward camera right",
                gaze_target="the arriving train",
                motion_path="takes one step toward the platform edge",
            ),
        ),
        lights=(
            MotivatedLight(
                light_id="platform-key",
                source_type="practical",
                world_position="overhead platform fixture",
                direction="down and across the actor's face",
                color_temperature="cool white",
                relative_intensity="key, one stop above ambient",
                attachment="fixed",
                motivation="the visible station fixture",
            ),
        ),
        key_light_id="platform-key",
        shadow_direction="toward the platform wall",
        exposure_priority="preserve the actor's face against the dark tracks",
        transition_intent="cut on the actor's step",
    )


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
                cinematography=shot_cinematography(source_id),
                asset_requirements=(
                    AssetRequirement(
                        kind="character_identity",
                        entity_key="actor",
                        evidence_source_ids=(source_id,),
                    ),
                ),
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
async def test_service_uses_planner_resolved_model_for_prompt_and_revision(tmp_path) -> None:
    planned = group("g1", 1, "s1")

    class AuditedPlanner(FakePlanner):
        model_name = "configured-director-model"

        async def plan_episode(self, value):
            assert value.director_model == self.model_name
            return await super().plan_episode(value)

    planner = AuditedPlanner(DirectorPlanDraft(groups=(planned,)), [])
    value = episode().model_copy(
        update={
            "source_spans": (span("s1", 1),),
            "director_model": "stale-default",
        }
    )

    result = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(
        value
    )

    assert result.director_model == "configured-director-model"


@pytest.mark.asyncio
async def test_service_reports_planning_and_validation_at_real_boundaries(tmp_path) -> None:
    events: list[str] = []
    planned = group("g1", 1, "s1")
    planner = FakePlanner(DirectorPlanDraft(groups=(planned,)), [])

    result = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(
        episode().model_copy(update={"source_spans": (span("s1", 1),)}),
        on_stage=events.append,
    )

    assert result.status == "review_required"
    assert events == ["episode_planned", "validated"]


@pytest.mark.asyncio
async def test_style_refresh_creates_qc_only_child_and_preserves_structure(tmp_path, monkeypatch) -> None:
    from novelvideo.director_plan.creative_review import CreativeQCResult
    from novelvideo.director_plan.models import (
        SceneBlockingRevisionReference, StyleProjections, StyleSnapshot,
        ValidationReport,
    )
    from tests.director_plan.creative_qc_fixtures import complete_assessments
    from tests.director_plan.test_creative_invalidation import revision as creative_revision

    old_style = StyleSnapshot(
        snapshot_id="style-old", style_id="old", style_version="1",
        catalog_hash="catalog-old", style_hash="hash-old",
        projections=StyleProjections(director="old", image="old", video="old", panel_tag="old"),
    )
    new_style = StyleSnapshot(
        snapshot_id="style-new", style_id="new", style_version="1",
        catalog_hash="catalog-new", style_hash="hash-new",
        projections=StyleProjections(director="new", image="new", video="new", panel_tag="new"),
    )
    reference = SceneBlockingRevisionReference(
        scene_id="scene-1", group_id="group-1", revision_id="blocking-r2", version=2,
        semantic_revision_id="semantic-1",
    )
    candidate = creative_revision().model_copy(update={
        "status": "review_required", "source_script_hash": "hash",
        "semantic_revision_id": "semantic-1", "project_style_snapshot_id": "style-old",
        "project_style_snapshot": old_style,
        "groups": tuple(group.model_copy(update={"style_snapshot_id": "style-old"})
                         for group in creative_revision().groups),
        "scene_blocking_references": (reference,),
        "validation_report": ValidationReport(passed=True),
    })
    input_value = episode().model_copy(update={
        "semantic_revision_id": "semantic-1", "project_style_snapshot_id": "style-new",
        "project_style_snapshot": new_style,
    })
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )

    class QCOnlyPlanner:
        def __init__(self):
            self.reviewed = []

        async def review_creative(self, value, plan, history):
            self.reviewed.append((value, plan, history))
            return CreativeQCResult(assessments=complete_assessments(plan))

        async def plan_episode(self, _value):
            raise AssertionError("style refresh must not replan the episode")

        async def repair_creative(self, *_args):
            raise AssertionError("style refresh must not run author repair")

    store = DirectorPlanStore(tmp_path)
    monkeypatch.setattr(store, "require_current", lambda _revision: None)
    planner = QCOnlyPlanner()
    child = await DirectorPlanService(store, planner).refresh_style_revision(
        input_value, candidate,
    )

    assert child.parent_revision_id == candidate.revision_id
    assert child.project_style_snapshot_id == "style-new"
    assert child.scene_blocking_references == candidate.scene_blocking_references
    assert len(child.groups) == len(candidate.groups)
    for old_group, new_group in zip(candidate.groups, child.groups):
        assert new_group.style_snapshot_id == "style-new"
        assert new_group.model_dump(mode="json") | {"style_snapshot_id": "style-old"} == old_group.model_dump(mode="json")
    assert len(planner.reviewed) == 1
    assert child.status == "review_required"


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
async def test_service_does_not_confuse_group_1_with_group_10(tmp_path) -> None:
    spans = tuple(span(f"s{i}", i) for i in range(1, 12))
    groups = tuple(
        group(
            f"g{i}",
            i,
            f"s{i}",
            "" if i in {2, 11} else "goal",
        )
        for i in range(1, 12)
    )
    planner = FakePlanner(
        DirectorPlanDraft(groups=groups),
        [group("g2", 2, "s2", "fixed-2"), group("g11", 11, "s11", "fixed-11")],
    )
    store = DirectorPlanStore(tmp_path)

    result = await DirectorPlanService(store, planner).create_draft(
        episode().model_copy(update={"source_spans": spans})
    )

    assert [call.failed_group.id for call in planner.repair_calls] == ["g2", "g11"]
    repair_issue_locations = [
        tuple(issue["location"] for issue in call.issues)
        for call in planner.repair_calls
    ]
    assert repair_issue_locations[0]
    assert all(
        location == "groups.1" or location.startswith("groups.1.")
        for location in repair_issue_locations[0]
    )
    assert repair_issue_locations[1]
    assert all(
        location == "groups.10" or location.startswith("groups.10.")
        for location in repair_issue_locations[1]
    )
    assert result.status == "review_required"
    assert result.validation_report.passed is True


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
    revisions = store.list(1)
    assert [item.status for item in revisions] == ["failed"]
    assert [
        issue.model_dump(mode="json") for issue in revisions[0].validation_report.issues
    ] == [
        {
            "code": "director_plan_provider_error",
            "message": "provider unavailable",
            "location": "planner",
            "severity": "error",
        }
    ]
    assert store.load_active(1) is None


@pytest.mark.asyncio
async def test_service_preserves_invalid_provider_output_as_contract_error(
    tmp_path,
) -> None:
    class InvalidPlanner:
        async def plan_episode(self, value):
            return {"groups": "not-a-list"}

    store = DirectorPlanStore(tmp_path)
    with pytest.raises(DirectorPlanPlanningError) as error:
        await DirectorPlanService(store, InvalidPlanner()).create_draft(episode())

    assert error.value.code == "director_plan_contract_error"
    assert isinstance(error.value.__cause__, ValidationError)
    assert store.list(1)[0].validation_report.issues[0].code == (
        "director_plan_contract_error"
    )


@pytest.mark.asyncio
async def test_service_classifies_real_planner_schema_failure_as_contract_error(
    tmp_path,
) -> None:
    class Agent:
        async def run(self, _prompt):
            return type("Response", (), {"output": {"groups": "not-a-list"}})()

    with pytest.raises(DirectorPlanPlanningError) as error:
        await DirectorPlanService(
            DirectorPlanStore(tmp_path), DirectorPlanner(agent=Agent())
        ).create_draft(episode())

    assert error.value.code == "director_plan_contract_error"


@pytest.mark.asyncio
async def test_service_does_not_retry_or_mask_storage_failure() -> None:
    class FailingStore:
        def __init__(self):
            self.calls = 0

        def save(self, revision):
            self.calls += 1
            raise OSError("disk unavailable")

    store = FailingStore()
    planner = FakePlanner(
        DirectorPlanDraft(groups=(group("g1", 1, "s1"),)), []
    )

    with pytest.raises(OSError, match="disk unavailable"):
        await DirectorPlanService(store, planner).create_draft(
            episode().model_copy(update={"source_spans": (span("s1", 1),)})
        )

    assert store.calls == 1
