from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.director_plan.creative_contract import (
    CharacterPerformance,
    DecisionSource,
    LightingDecision,
    PerformanceBeat,
    PerformanceDecision,
)
from novelvideo.director_plan.models import AssetRequirement, ShotPlan, SourceSpan
from novelvideo.director_plan.planner import (
    DirectorPlanContractError,
    DirectorPlanDraft,
    DirectorPlanInput,
    DirectorPlanner,
)
from novelvideo.director_plan.professional_collaboration import (
    ProfessionalAssignmentError,
    ProfessionalOutputError,
    ROLE_MERGED_FIELDS,
    ROLE_OUTPUTS,
    merge_professional_output,
    prepare_professional_runs,
)
from novelvideo.director_plan.professional_models import (
    ArtProfessionalOutput,
    BlockingProfessionalOutput,
    CinematographyProfessionalOutput,
    LightingProfessionalOutput,
    PerformanceProfessionalOutput,
    ProfessionalAssignment,
    StoryboardProfessionalOutput,
)
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.director_plan.models import ValidationReport
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.test_models import make_group


def creative_input(chain="professional-chain"):
    return DirectorPlanInput(
        creative_contract_version=1,
        author_chain_id=chain,
        episode=1,
        source_script_hash="source-hash",
        semantic_revision_id="semantics-r1",
        source_spans=(SourceSpan(
            id="span-1", ordinal=1, scene="hallway", time="night", text="A lamp illuminates the door."
        ),),
        relevant_bible={},
        aspect_ratio="9:16",
        style_director={"projection": "realistic"},
        project_style_snapshot_id="style-r1",
    )


def planned_group():
    return make_group(
        creative_contract_version=1,
        shots=(creative_shot(shot_id="shot-1", duration=5),),
    )


def professional_group():
    shot = creative_shot(shot_id="shot-1", duration=5)
    subject = {
        "subject_id": "character:lead",
        "world_position": "at the hallway threshold",
        "screen_position": "left third",
        "facing": "toward the door",
        "gaze_target": "the door handle",
        "motion_path": "steps from the threshold toward the handle",
    }
    prop = {
        "prop_id": "prop:key",
        "world_position": "in the lead's right hand",
        "facing": "teeth toward the lock",
        "held_by": "character:lead",
        "motion_path": "moves with the hand toward the lock",
    }
    cinematography = type(shot.cinematography).model_validate({
        **shot.cinematography.model_dump(mode="python"),
        "subjects": (subject,), "props": (prop,),
    })
    performance = PerformanceDecision(
        purpose="Show the lead's hesitation before opening the door.",
        source=DecisionSource(kind="director_choice", reference_ids=("span-1",)),
        applicability="applicable",
        characters=(CharacterPerformance(
            subject_id="character:lead",
            master_profile_missing_reason="No adopted master profile was supplied.",
            start_behavior="holds the key just short of the lock",
            end_behavior="touches the key to the lock",
            goal="enter quietly",
            obstacle="fear of waking someone",
            tactics="listens before moving",
            visible_behavior="pauses, then advances the key",
            gaze="alternates between the handle and the hallway",
            contact="keeps the key in the right hand",
            held_objects="key in the right hand",
            weight_and_inertia="weight stays on the back foot until the key advances",
            beats=(PerformanceBeat(
                at_seconds=1, event_anchor="after the hallway creaks",
                behavior="holds still and listens",
            ),),
        ),),
    )
    creative = shot.creative.model_copy(update={"performance": performance})
    requirements = (
        AssetRequirement(
            kind="character_identity", entity_key="character:lead",
            evidence_source_ids=("span-1",), design_notes="dark coat", required=True,
        ),
        AssetRequirement(
            kind="prop", entity_key="prop:key",
            evidence_source_ids=("span-1",), design_notes="small brass key", required=False,
        ),
    )
    shot_values = shot.model_dump(mode="python")
    shot_values.update({
        "cinematography": cinematography,
        "creative": creative,
        "asset_requirements": requirements,
    })
    shot = ShotPlan.model_validate(shot_values)
    return make_group(creative_contract_version=1, shots=(shot,))


def assignments():
    return (
        ProfessionalAssignment(
            role_id="builtin:storyboard-artist", group_id="group-1",
            shot_ids=("shot-1",), task="Clarify the viewer's attention.",
            reason="The lock and door state must read before the cut.",
        ),
        ProfessionalAssignment(
            role_id="builtin:cinematographer", group_id="group-1",
            shot_ids=("shot-1",), task="Refine the shot viewpoint.",
            reason="The audience must see the lock without changing the blocking.",
        ),
        ProfessionalAssignment(
            role_id="builtin:lighting-designer", group_id="group-1",
            shot_ids=("shot-1",), task="Clarify motivated light on the lock.",
            reason="The practical lamp is an established scene source.",
        ),
    )


def all_role_assignments():
    return tuple(
        ProfessionalAssignment(
            role_id=role_id, group_id="group-1", shot_ids=("shot-1",),
            task=f"Contribute the {role_id.rsplit(':', 1)[-1]} details.",
            reason="The director identified this scoped detail as useful.",
        )
        for role_id in (
            "builtin:storyboard-artist", "builtin:blocking-designer",
            "builtin:cinematographer", "builtin:lighting-designer",
            "builtin:expression-designer", "builtin:art-asset-designer",
        )
    )


def professional_output(role_id):
    if role_id == "builtin:storyboard-artist":
        return StoryboardProfessionalOutput(summary="Direct attention to the lock.", contributions=({
            "shot_id": "shot-1",
            "narrative_purpose": "Reveal the lock as the source of danger.",
            "audience_attention": "The lock before the closed door.",
            "emotional_effect": "Build quiet suspicion.",
            "continuity_strategy": "Hold the same doorway through the cut.",
        },))
    if role_id == "builtin:cinematographer":
        return CinematographyProfessionalOutput(summary="Keep the lock readable.", contributions=({
            "shot_id": "shot-1", "shot_size": "medium close-up",
            "camera_angle": "eye level", "composition": "Lock in the upper third.",
            "camera_motion": "static", "camera_side": "south",
            "transition_intent": "Cut after the audience reads the lock.",
        },))
    if role_id == "builtin:lighting-designer":
        return LightingProfessionalOutput(summary="Use the existing warm practical.", contributions=({
            "shot_id": "shot-1",
            "lighting": LightingDecision(
                purpose="Keep the lock legible while holding the hallway at night.",
                source=DecisionSource(kind="director_choice", reference_ids=("span-1",)),
                light_ids=("lamp",), camera_relative_lighting="soft top light",
                shadow="A short shadow below the lock.", quality="soft practical spill",
                color="warm amber", subject_background_exposure="Lock slightly brighter than door.",
                eye_light="not applicable; no visible characters",
            ),
        },))
    if role_id == "builtin:blocking-designer":
        return BlockingProfessionalOutput(summary="Keep the entrance movement continuous.", contributions=({
            "shot_id": "shot-1", "axis": "hallway axis",
            "screen_direction": "movement travels left to right",
            "subjects": ({
                "subject_id": "character:lead", "world_position": "at the lock",
                "screen_position": "left third", "facing": "toward the door",
                "gaze_target": "the handle", "motion_path": "moves from the threshold to the lock",
            },),
            "props": ({
                "prop_id": "prop:key", "world_position": "in the lead's right hand",
                "facing": "teeth toward the lock", "held_by": "character:lead",
                "motion_path": "moves with the hand toward the lock",
            },),
        },))
    if role_id == "builtin:expression-designer":
        return PerformanceProfessionalOutput(summary="Play the hesitation through restrained movement.", contributions=({
            "shot_id": "shot-1", "characters": ({
                "subject_id": "character:lead",
                "start_behavior": "holds the key near the lock",
                "end_behavior": "touches the key to the lock",
                "goal": "enter quietly", "obstacle": "fear of waking someone",
                "tactics": "listens before moving",
                "visible_behavior": "pauses, then advances the key",
                "gaze": "checks the hallway before the handle",
                "contact": "right hand retains contact with the key",
                "held_objects": "key stays in the right hand",
                "weight_and_inertia": "weight shifts forward only as the key advances",
                "beats": ({
                    "at_seconds": 1, "event_anchor": "after the hallway creaks",
                    "behavior": "holds still and listens",
                },),
            },),
        },))
    return ArtProfessionalOutput(summary="Clarify the established character and prop notes.", contributions=({
        "shot_id": "shot-1", "asset_requirements": (
            {"entity_key": "character:lead", "design_notes": "dark coat with a worn cuff"},
            {"entity_key": "prop:key", "design_notes": "small brass key with a flat bow"},
        ),
    },))


class SpecialistPlanner:
    model_name = "test-director-model"

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def plan_episode(self, _input):
        return DirectorPlanDraft(
            groups=(planned_group(),),
            professional_decision="Storyboard, photography and lighting need separate shot-scoped input.",
            professional_assignments=assignments(),
        )

    async def run_professional_assignment(self, _input, groups, assignment, run_id):
        self.calls.append((assignment.role_id, run_id))
        if self.fail:
            raise TimeoutError("provider result may be unknown")
        from novelvideo.director_plan.professional_collaboration import merge_professional_output

        output = professional_output(assignment.role_id)
        merged, fields = merge_professional_output(groups, assignment, output)
        return output, merged, fields


@pytest.mark.parametrize(
    ("role_id", "expected_field"),
    [
        ("builtin:storyboard-artist", "intent"),
        ("builtin:blocking-designer", "cinematography.subjects"),
        ("builtin:cinematographer", "cinematography.camera_side"),
        ("builtin:lighting-designer", "creative.lighting"),
        ("builtin:expression-designer", "creative.performance.characters"),
        ("builtin:art-asset-designer", "asset_requirements.design_notes"),
    ],
)
def test_specialist_outputs_are_typed_scoped_and_merge_only_their_fields(role_id, expected_field):
    assignment = next((item for item in assignments() if item.role_id == role_id), None)
    if assignment is None:
        assignment = ProfessionalAssignment(
            role_id=role_id, group_id="group-1", shot_ids=("shot-1",),
            task="Contribute the assigned production details.",
            reason="The director plan has an unresolved detail in this role's scope.",
        )
    base = (professional_group() if role_id in {
        "builtin:blocking-designer", "builtin:expression-designer", "builtin:art-asset-designer",
    } else planned_group(),)
    merged, fields = merge_professional_output(base, assignment, professional_output(role_id))
    before = base[0].shots[0]
    after = merged[0].shots[0]
    assert expected_field in fields
    assert (after.id, after.source_span_ids, after.duration_seconds, after.action) == (
        before.id, before.source_span_ids, before.duration_seconds, before.action
    )
    assert (merged[0].id, merged[0].ordinal, merged[0].shots[0].id) == (
        base[0].id, base[0].ordinal, before.id
    )
    if role_id == "builtin:storyboard-artist":
        assert after.intent.narrative_purpose.startswith("Reveal")
    elif role_id == "builtin:blocking-designer":
        assert after.cinematography.subjects[0].world_position == "at the lock"
        assert after.cinematography.subjects[0].subject_id == before.cinematography.subjects[0].subject_id
        assert after.cinematography.source_ids == before.cinematography.source_ids
    elif role_id == "builtin:cinematographer":
        assert after.cinematography.axis == before.cinematography.axis
        assert after.cinematography.lights == before.cinematography.lights
    elif role_id == "builtin:lighting-designer":
        assert after.creative.lighting.color == "warm amber"
    elif role_id == "builtin:expression-designer":
        assert after.creative.performance.characters[0].subject_id == "character:lead"
        assert after.creative.performance.characters[0].master_profile_missing_reason == before.creative.performance.characters[0].master_profile_missing_reason
        assert after.creative.performance.characters[0].visible_behavior.startswith("pauses")
    else:
        before_requirement = before.asset_requirements[0]
        after_requirement = after.asset_requirements[0]
        assert after_requirement.design_notes.endswith("worn cuff")
        assert (after_requirement.entity_key, after_requirement.kind, after_requirement.required) == (
            before_requirement.entity_key, before_requirement.kind, before_requirement.required,
        )


@pytest.mark.parametrize(
    ("role_id", "error_code"),
    [
        ("builtin:blocking-designer", "professional_blocking_subject_scope_mismatch"),
        ("builtin:expression-designer", "professional_performance_subject_scope_mismatch"),
        ("builtin:art-asset-designer", "professional_art_asset_scope_mismatch"),
    ],
)
def test_specialists_cannot_add_new_subjects_or_asset_identity(role_id, error_code):
    assignment = next(item for item in all_role_assignments() if item.role_id == role_id)
    output = professional_output(role_id)
    contribution = output.contributions[0]
    if role_id == "builtin:blocking-designer":
        subjects = (contribution.subjects[0].model_copy(update={"subject_id": "character:new"}),)
        contribution = contribution.model_copy(update={"subjects": subjects})
    elif role_id == "builtin:expression-designer":
        characters = (contribution.characters[0].model_copy(update={"subject_id": "character:new"}),)
        contribution = contribution.model_copy(update={"characters": characters})
    else:
        requirements = (
            contribution.asset_requirements[0].model_copy(update={"entity_key": "character:new"}),
            contribution.asset_requirements[1],
        )
        contribution = contribution.model_copy(update={"asset_requirements": requirements})
    output = output.model_copy(update={"contributions": (contribution,)})
    with pytest.raises(ProfessionalOutputError, match=error_code):
        merge_professional_output((professional_group(),), assignment, output)


def test_blocking_allows_a_character_to_move_between_shots():
    first_group = professional_group()
    first_shot = first_group.shots[0]
    second_values = first_shot.model_dump(mode="python")
    second_values["id"] = "shot-2"
    second_cinematography = first_shot.cinematography.model_copy(update={
        "subjects": (first_shot.cinematography.subjects[0].model_copy(update={
            "world_position": "beside the door handle",
            "motion_path": "arrives from the hallway threshold",
        }),),
    })
    second_values["cinematography"] = second_cinematography
    second_shot = ShotPlan.model_validate(second_values)
    group = make_group(creative_contract_version=1, shots=(first_shot, second_shot))
    assignment = ProfessionalAssignment(
        role_id="builtin:blocking-designer", group_id=group.id,
        shot_ids=("shot-1", "shot-2"), task="Keep the entrance move continuous.",
        reason="The lead advances from the threshold to the door.",
    )
    first_contribution = professional_output("builtin:blocking-designer").contributions[0]
    second_contribution = first_contribution.model_copy(update={
        "shot_id": "shot-2",
        "subjects": (first_contribution.subjects[0].model_copy(update={
            "world_position": "beside the door handle",
        }),),
    })
    output = BlockingProfessionalOutput(
        summary="The lead advances into the doorway.",
        contributions=(first_contribution, second_contribution),
    )
    updated, _fields = merge_professional_output((group,), assignment, output)
    positions = tuple(shot.cinematography.subjects[0].world_position for shot in updated[0].shots)
    assert positions == ("at the lock", "beside the door handle")


def test_director_assignments_are_frozen_to_existing_group_and_shot_ids():
    value = creative_input()
    draft = DirectorPlanDraft(
        groups=(planned_group(),),
        professional_decision="Photography is needed.",
        professional_assignments=(assignments()[1],),
    )
    receipts = prepare_professional_runs(value, draft, model="codex-role-model")
    receipt = receipts[0]
    assert receipt.profile_revision == 1
    assert receipt.profile_source["kind"] == "builtin"
    assert receipt.profile_source["content_hash"]
    assert receipt.status == "pending"
    assert receipt.source_script_hash == value.source_script_hash
    assert receipt.semantic_revision_id == value.semantic_revision_id

    invalid = draft.model_copy(update={"professional_assignments": (
        assignments()[1].model_copy(update={"shot_ids": ("unplanned-shot",)}),
    )})
    with pytest.raises(ProfessionalAssignmentError, match="shot_out_of_scope"):
        prepare_professional_runs(value, invalid)


def test_all_six_director_selected_roles_resolve_fixed_revision_profiles():
    draft = DirectorPlanDraft(
        groups=(professional_group(),),
        professional_decision="Each listed gap benefits from a separate scoped contribution.",
        professional_assignments=all_role_assignments(),
    )
    receipts = prepare_professional_runs(creative_input(), draft, model="codex-role-model")
    assert tuple(run.role_id for run in receipts) == tuple(
        assignment.role_id for assignment in all_role_assignments()
    )
    assert all(run.profile_revision == 1 for run in receipts)
    assert all(run.profile_source["kind"] == "builtin" for run in receipts)
    assert all(run.status == "pending" for run in receipts)


@pytest.mark.parametrize("role_id", [
    "builtin:storyboard-artist", "builtin:blocking-designer",
    "builtin:cinematographer", "builtin:lighting-designer",
    "builtin:expression-designer", "builtin:art-asset-designer",
])
@pytest.mark.asyncio
async def test_director_planner_runs_a_real_fixed_role_handler_with_typed_output(role_id):
    value = creative_input()
    assignment = next((item for item in assignments() if item.role_id == role_id), None)
    if assignment is None:
        assignment = next(item for item in all_role_assignments() if item.role_id == role_id)
    base = (professional_group() if role_id in {
        "builtin:blocking-designer", "builtin:expression-designer", "builtin:art-asset-designer",
    } else planned_group(),)
    output = professional_output(assignment.role_id)
    captured = {}

    class Runtime:
        snapshot = SimpleNamespace(runtime="model_api", model="role-model")

        async def run_structured(self, *, prompt, output_type):
            captured["prompt"] = prompt
            captured["output_type"] = output_type
            return output

    planner = DirectorPlanner(agent=SimpleNamespace(model_name="role-model"))
    planner._runtime = Runtime()
    parsed, groups, merged_fields = await planner.run_professional_assignment(
        value, base, assignment, "01K00000000000000000000000"
    )
    assert parsed == output
    assert captured["output_type"] is ROLE_OUTPUTS[role_id]
    assert role_id in captured["prompt"]
    assert "semantics-r1" in captured["prompt"]
    assert merged_fields == ROLE_MERGED_FIELDS[role_id]
    assert groups[0].shots[0].id == "shot-1"


@pytest.mark.asyncio
async def test_director_candidate_persists_real_role_receipts_and_scoped_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )
    planner = SpecialistPlanner()
    value = creative_input()
    revision = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(value)

    assert revision.status == "review_required"
    assert revision.professional_status == "complete"
    assert len(planner.calls) == 3
    assert len({run_id for _role, run_id in planner.calls}) == 3
    assert {run.role_id for run in revision.professional_runs} == {
        "builtin:storyboard-artist", "builtin:cinematographer", "builtin:lighting-designer"
    }
    assert all(run.status == "completed" and run.summary for run in revision.professional_runs)
    assert all(run.profile_source["kind"] == "builtin" for run in revision.professional_runs)
    assert revision.groups[0].shots[0].intent is not None
    assert revision.groups[0].shots[0].shot_size == "medium close-up"
    assert revision.groups[0].shots[0].creative.lighting.color == "warm amber"
    stored = DirectorPlanStore(tmp_path).load(1, revision.revision_id)
    assert stored.professional_runs == revision.professional_runs


@pytest.mark.asyncio
async def test_all_optional_specialists_run_and_merge_through_director_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )

    class AllRolesPlanner(SpecialistPlanner):
        async def plan_episode(self, _input):
            return DirectorPlanDraft(
                groups=(professional_group(),),
                professional_decision="The director selected all six specific contributions for this test plan.",
                professional_assignments=all_role_assignments(),
            )

        async def run_professional_assignment(self, _input, groups, assignment, run_id):
            self.calls.append((assignment.role_id, run_id))
            from novelvideo.director_plan.professional_collaboration import merge_professional_output

            output = professional_output(assignment.role_id)
            merged, fields = merge_professional_output(groups, assignment, output)
            return output, merged, fields

    planner = AllRolesPlanner()
    revision = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(
        creative_input("all-specialists-chain")
    )
    assert revision.professional_status == "complete"
    assert {run.role_id for run in revision.professional_runs} == {
        assignment.role_id for assignment in all_role_assignments()
    }
    assert len(planner.calls) == 6
    assert all(run.status == "completed" and run.summary for run in revision.professional_runs)
    shot = revision.groups[0].shots[0]
    assert shot.cinematography.subjects[0].world_position == "at the lock"
    assert shot.cinematography.axis == "hallway axis"
    assert shot.creative.performance.characters[0].subject_id == "character:lead"
    assert shot.asset_requirements[0].design_notes.endswith("worn cuff")


@pytest.mark.asyncio
async def test_unknown_specialist_outcome_is_not_replayed_and_stays_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )
    planner = SpecialistPlanner(fail=True)
    service = DirectorPlanService(DirectorPlanStore(tmp_path), planner)
    value = creative_input("unknown-chain")
    first = await service.create_draft(value)
    assert first.professional_status == "unknown"
    assert len(first.professional_runs) == 3
    assert all(run.status == "unknown" for run in first.professional_runs)
    assert len(planner.calls) == 3


@pytest.mark.asyncio
async def test_returned_invalid_specialist_schema_is_failed_without_provider_details(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )

    class InvalidOutputPlanner(SpecialistPlanner):
        async def run_professional_assignment(self, _input, _groups, assignment, run_id):
            self.calls.append((assignment.role_id, run_id))
            return {"contributions": []}, (), ()

    planner = InvalidOutputPlanner()
    result = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(
        creative_input("invalid-output-chain")
    )
    assert result.professional_status == "partial"
    assert all(run.status == "failed" for run in result.professional_runs)
    assert all(
        run.error_code == "professional_output_schema_invalid"
        for run in result.professional_runs
    )


@pytest.mark.asyncio
async def test_checkpoint_ahead_of_revision_projection_recovers_without_resending(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )
    monkeypatch.setattr(
        "novelvideo.task_concurrency_settings.load_task_concurrency_settings",
        lambda: SimpleNamespace(lanes={"default": 1}),
    )

    class FailOnceStore(DirectorPlanStore):
        failed_write = False

        def save_professional_candidate(self, revision):
            if not self.failed_write and any(
                run.status == "completed" for run in revision.professional_runs
            ):
                self.failed_write = True
                raise OSError("simulated projection write failure")
            return super().save_professional_candidate(revision)

    store = FailOnceStore(tmp_path)

    class PausedSiblingPlanner(SpecialistPlanner):
        async def run_professional_assignment(self, _input, groups, assignment, run_id):
            if assignment.role_id != "builtin:storyboard-artist":
                self.calls.append((assignment.role_id, run_id))
                await asyncio.Event().wait()
            return await super().run_professional_assignment(
                _input, groups, assignment, run_id
            )

    planner = PausedSiblingPlanner()
    value = creative_input("projection-gap-chain")
    service = DirectorPlanService(store, planner)
    with pytest.raises(OSError, match="projection write failure"):
        await service.create_draft(value)

    checkpoint = store.load_creative_checkpoint(1, value.author_chain_id)
    assert any(
        run["status"] == "completed"
        for run in checkpoint["candidate"]["professional_runs"]
    )
    assert all(
        run.status == "pending"
        for run in store.list(1)[0].professional_runs
    ), [run.status for run in store.list(1)[0].professional_runs]

    calls_before_recovery = len(planner.calls)
    recovered = await service.create_draft(value)
    assert len(planner.calls) == calls_before_recovery
    assert recovered.professional_runs[0].status == "completed"
    assert all(run.status == "unknown" for run in recovered.professional_runs[1:])


@pytest.mark.asyncio
async def test_interrupted_pending_calls_become_unknown_and_are_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )

    class InterruptedPlanner(SpecialistPlanner):
        async def run_professional_assignment(self, *_args):
            self.calls.append("submitted-or-pending")
            raise asyncio.CancelledError()

    planner = InterruptedPlanner()
    service = DirectorPlanService(DirectorPlanStore(tmp_path), planner)
    value = creative_input("interrupted-chain")
    with pytest.raises(asyncio.CancelledError):
        await service.create_draft(value)
    calls_before_recovery = len(planner.calls)

    recovered = await service.create_draft(value)
    assert recovered.professional_status == "unknown"
    assert len(recovered.professional_runs) == 3
    assert all(run.status == "unknown" for run in recovered.professional_runs)
    assert len(planner.calls) == calls_before_recovery

    replay = await service.create_draft(value)
    assert replay.revision_id == recovered.revision_id
    assert all(run.status == "unknown" for run in replay.professional_runs)
    assert len(planner.calls) == 3


@pytest.mark.asyncio
async def test_pending_professional_candidate_is_visible_and_only_allowlisted_updates_persist(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "novelvideo.director_plan.service.validate_director_plan",
        lambda *_args: ValidationReport(passed=True),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    class PausedPlanner(SpecialistPlanner):
        async def run_professional_assignment(self, _input, groups, assignment, run_id):
            self.calls.append((assignment.role_id, run_id))
            started.set()
            await release.wait()
            from novelvideo.director_plan.professional_collaboration import merge_professional_output

            output = professional_output(assignment.role_id)
            merged, fields = merge_professional_output(groups, assignment, output)
            return output, merged, fields

    store = DirectorPlanStore(tmp_path)
    service = DirectorPlanService(store, PausedPlanner())
    task = asyncio.create_task(service.create_draft(creative_input("visible-chain")))
    await asyncio.wait_for(started.wait(), timeout=2)

    provisional = store.list(1)
    assert len(provisional) == 1
    candidate = provisional[0]
    assert candidate.status == "validating"
    assert candidate.professional_status == "pending"
    assert all(run.status == "pending" for run in candidate.professional_runs)

    edited_group = candidate.groups[0].model_copy(update={"objective": "unauthorized rewrite"})
    with pytest.raises(ValueError, match="groups_changed_outside_allowlist"):
        store.save_professional_candidate(candidate.model_copy(update={"groups": (edited_group,)}))

    release.set()
    final = await task
    assert final.status == "review_required"
    assert final.professional_status == "complete"
    assert store.load(1, final.revision_id) == final


def test_specialist_result_must_cover_exact_shot_scope():
    assignment = assignments()[0]
    output = professional_output(assignment.role_id).model_copy(update={
        "contributions": (professional_output(assignment.role_id).contributions[0].model_copy(
            update={"shot_id": "other-shot"}
        ),)
    })
    with pytest.raises(ProfessionalOutputError, match="shot_scope_mismatch"):
        merge_professional_output((planned_group(),), assignment, output)


@pytest.mark.asyncio
async def test_director_must_explain_empty_specialist_list_but_is_not_forced_to_assign_roles():
    class Agent:
        def __init__(self, output):
            self.output = output

        async def run(self, _prompt):
            return SimpleNamespace(output=self.output)

    base = {"groups": [planned_group().model_dump(mode="json")]}
    planner = DirectorPlanner(agent=Agent(base))
    with pytest.raises(DirectorPlanContractError, match="professional delegation decision"):
        await planner.plan_episode(creative_input())

    no_specialist_needed = {
        **base,
        "professional_decision": "The director's shot plan already resolves this simple coverage.",
        "professional_assignments": [],
    }
    result = await DirectorPlanner(agent=Agent(no_specialist_needed)).plan_episode(creative_input())
    assert result.professional_assignments == ()
    assert "The director's shot plan" in result.professional_decision

