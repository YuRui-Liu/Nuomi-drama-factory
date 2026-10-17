from __future__ import annotations

import json

import pytest


def _blocking(label="Warehouse", *, actor_x=25):
    return {
        "label": label,
        "actors": [{"id": "hero", "label": "Hero", "x": actor_x, "y": 40, "facing": 90}],
        "camera": {"x": 50, "y": 85, "facing": 270},
        "lights": [{"id": "key", "label": "Key", "x": 20, "y": 15, "intensity": 50}],
    }


def test_scene_blocking_edits_are_versioned_and_group_references_stay_pinned(tmp_path):
    from novelvideo.director_plan.workspace import (
        DirectorWorkspaceStore,
        SceneBlockingConflict,
    )

    store = DirectorWorkspaceStore(tmp_path)
    first = store.save_scene_blocking(
        1,
        "scene-a",
        base_version=0,
        semantic_revision_id="sem-1",
        director_revision_id="director-1",
        blocking=_blocking(actor_x=20),
        group_ids=("group-1", "group-2"),
    )
    second = store.save_scene_blocking(
        1,
        "scene-a",
        base_version=1,
        semantic_revision_id="sem-1",
        director_revision_id="director-1",
        blocking=_blocking(actor_x=30),
        group_ids=("group-1",),
    )

    assert second["version"] == 2
    assert second["blocking"]["actors"][0]["x"] == 30
    refs = store.list_group_references(1)
    assert refs == [
        {"scene_id": "scene-a", "group_id": "group-1", "director_revision_id": "director-1", "version": 2,
         "revision_id": second["revision_id"]},
    ]

    cleared = store.clear_scene_group_references(
        1, "scene-a", base_revision_id=second["revision_id"]
    )
    after_clear = store.get_scene_blocking(1, "scene-a")
    assert cleared["removed_group_ids"] == ["group-1"]
    assert after_clear["group_references"] == []
    assert len(after_clear["history"]) == 2
    assert after_clear["current"]["revision_id"] == second["revision_id"]

    with pytest.raises(SceneBlockingConflict, match="version"):
        store.save_scene_blocking(
            1,
            "scene-a",
            base_version=1,
            semantic_revision_id="sem-1",
            director_revision_id="director-1",
            blocking=_blocking(actor_x=45),
            group_ids=(),
        )


def test_group_reference_can_be_rebound_to_historical_blocking_version(tmp_path):
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    store = DirectorWorkspaceStore(tmp_path)
    first = store.save_scene_blocking(
        2, "scene-b", base_version=None, semantic_revision_id="sem-1",
        blocking=_blocking("First"), group_ids=(),
    )
    second = store.save_scene_blocking(
        2, "scene-b", base_version=first["revision_id"], semantic_revision_id="sem-1",
        blocking=_blocking("Second"), group_ids=(),
    )

    reference = store.set_group_reference(
        2, "scene-b", "group-x", first["revision_id"], "director-1"
    )

    assert reference == {
        "scene_id": "scene-b", "group_id": "group-x", "version": 1,
        "director_revision_id": "director-1", "revision_id": first["revision_id"],
    }
    assert store.get_scene_blocking(2, "scene-b")["current"]["revision_id"] == second["revision_id"]


def test_replacing_scene_group_references_moves_selected_groups_and_unpins_removed_groups(tmp_path):
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    store = DirectorWorkspaceStore(tmp_path)
    scene_a = store.save_scene_blocking(
        3, "scene-a", base_version=None, semantic_revision_id="sem-1",
        director_revision_id="director-1", blocking=_blocking("A"), group_ids=("old-group",),
    )
    scene_b = store.save_scene_blocking(
        3, "scene-b", base_version=None, semantic_revision_id="sem-1",
        director_revision_id="director-1", blocking=_blocking("B"), group_ids=("new-group",),
    )
    moved = store.save_scene_blocking(
        3, "scene-b", base_version=scene_b["revision_id"], semantic_revision_id="sem-1",
        director_revision_id="director-2", blocking=_blocking("B2"), group_ids=("old-group",),
    )

    assert store.get_scene_blocking(3, "scene-a")["group_references"] == []
    assert store.get_scene_blocking(3, "scene-b")["group_references"] == [
        {"scene_id": "scene-b", "group_id": "old-group", "director_revision_id": "director-2",
         "version": moved["version"], "revision_id": moved["revision_id"]},
    ]


def test_scene_blocking_rejects_coordinates_outside_normalized_canvas(tmp_path):
    from pydantic import ValidationError
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    store = DirectorWorkspaceStore(tmp_path)
    with pytest.raises(ValidationError):
        store.save_scene_blocking(
            1,
            "scene-a",
            base_version=0,
            semantic_revision_id="sem-1",
            blocking={**_blocking(), "actors": [{"id": "hero", "label": "Hero", "x": 101, "y": 10, "facing": 90}]},
            group_ids=(),
        )


def test_scene_blocking_rejects_non_finite_values(tmp_path):
    from pydantic import ValidationError
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    store = DirectorWorkspaceStore(tmp_path)
    with pytest.raises(ValidationError):
        store.save_scene_blocking(
            1,
            "scene-a",
            base_version=None,
            semantic_revision_id=None,
            blocking={**_blocking(), "camera": {"x": float("nan"), "y": 10, "facing": 90}},
            group_ids=(),
        )


def test_scene_blocking_compiles_to_reviewable_director_candidate_with_provenance(tmp_path):
    from novelvideo.director_plan.models import (
        AssetRequirement,
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotCinematography,
        ShotPlan,
        SourceSpan,
        ValidationReport,
    )
    from novelvideo.director_plan.workspace import (
        DirectorWorkspaceStore,
        SceneBlockingCompileConflict,
        compile_scene_blocking_candidate,
    )

    shot = ShotPlan(
        id="shot-1",
        source_span_ids=("span-1",),
        subject="hero",
        action="waits",
        visible_start_state="left",
        visible_end_state="right",
        duration_seconds=4,
        asset_requirements=(
            AssetRequirement(kind="character_identity", entity_key="hero"),
        ),
        cinematography=ShotCinematography(
            source="director_plan",
            source_ids=("span-1",),
            axis="doorway",
            camera_side="south",
            screen_direction="no travel",
            subjects=(
                {
                    "subject_id": "hero",
                    "world_position": "at doorway",
                    "screen_position": "left third",
                    "facing": "toward room",
                    "gaze_target": "door",
                    "motion_path": "hold",
                },
            ),
            lights=(
                {
                    "light_id": "key",
                    "source_type": "practical",
                    "world_position": "above door",
                    "direction": "down",
                    "color_temperature": "warm",
                    "relative_intensity": "key",
                    "attachment": "fixed",
                    "motivation": "hall lamp",
                },
            ),
            key_light_id="key",
            shadow_direction="below",
            exposure_priority="face",
            transition_intent="hold",
        ),
    )
    group = NarrativeGroupPlan(
        id="group-1",
        ordinal=1,
        source_span_ids=("span-1",),
        scene_anchor="hallway",
        time_anchor="night",
        objective="enter",
        visible_turn="door opens",
        relation_to_previous="single",
        shots=(shot,),
    )
    active = DirectorPlanRevision.new(
        episode=1,
        source_script_hash="source-hash",
        director_model="director",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(group,),
    ).model_copy(
        update={"status": "active", "validation_report": ValidationReport(passed=True)}
    )
    reference = {
        "scene_id": "scene-1",
        "group_id": "group-1",
        "director_revision_id": active.revision_id,
        "revision_id": "blocking-revision-1",
        "version": 2,
        "semantic_revision_id": None,
        "blocking": _blocking(actor_x=31),
    }

    candidate = compile_scene_blocking_candidate(
        active,
        (reference,),
        source_spans=(SourceSpan(
            id="span-1", ordinal=1, scene="hallway", time="night", text="waits"
        ),),
    )

    compiled = candidate.groups[0].shots[0].cinematography
    assert candidate.status == "review_required"
    assert candidate.parent_revision_id == active.revision_id
    assert candidate.groups[0].id == active.groups[0].id
    assert compiled.source == "director_plan"
    assert compiled.source_ids == ("span-1",)
    assert "x=31%" in compiled.subjects[0].world_position
    assert "x=50%" in compiled.camera_side
    assert "x=20%" in compiled.lights[0].world_position
    assert "50%" in compiled.lights[0].relative_intensity
    assert candidate.scene_blocking_references[0].revision_id == "blocking-revision-1"
    assert candidate.scene_blocking_references[0].director_revision_id == active.revision_id
    assert "x=31%" in compiled.prompt_facts()

    store = DirectorWorkspaceStore(tmp_path)
    first_pin = store.save_scene_blocking(
        1, "scene-1", base_version=None, semantic_revision_id=active.semantic_revision_id,
        director_revision_id=active.revision_id, blocking=_blocking(actor_x=31),
        group_ids=(active.groups[0].id,),
    )
    merged_group = active.groups[0].model_copy(update={"id": "group-merged"})
    merged_active = active.model_copy(update={
        "revision_id": "director-merged",
        "parent_revision_id": active.revision_id,
        "groups": (merged_group,),
    })
    stored_scene = store.get_scene_blocking(1, "scene-1")
    old_reference = {
        **stored_scene["group_references"][0],
        "semantic_revision_id": active.semantic_revision_id,
        "blocking": stored_scene["current"]["blocking"],
    }
    with pytest.raises(SceneBlockingCompileConflict, match="clear and re-save"):
        compile_scene_blocking_candidate(
            merged_active, (old_reference,), source_spans=(SourceSpan(
                id="span-1", ordinal=1, scene="hallway", time="night", text="waits"
            ),),
        )

    cleared = store.clear_scene_group_references(
        1, "scene-1", base_revision_id=first_pin["revision_id"]
    )
    assert cleared["removed_group_ids"] == [active.groups[0].id]
    assert store.get_scene_blocking(1, "scene-1")["history"]
    current_pin = store.save_scene_blocking(
        1, "scene-1", base_version=first_pin["revision_id"],
        semantic_revision_id=active.semantic_revision_id,
        director_revision_id=merged_active.revision_id,
        blocking=_blocking(actor_x=31), group_ids=(merged_group.id,),
    )
    reference_after_adoption = {
        **store.get_scene_blocking(1, "scene-1")["group_references"][0],
        "semantic_revision_id": active.semantic_revision_id,
        "blocking": current_pin["blocking"],
    }
    compiled_after_adoption = compile_scene_blocking_candidate(
        merged_active, (reference_after_adoption,), source_spans=(SourceSpan(
            id="span-1", ordinal=1, scene="hallway", time="night", text="waits"
        ),),
    )
    assert compiled_after_adoption.scene_blocking_references[0].director_revision_id == merged_active.revision_id
    assert compiled_after_adoption.groups[0].id == merged_group.id


def test_scene_blocking_compile_rejects_unmapped_production_ids():
    from novelvideo.director_plan.models import (
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotCinematography,
        ShotPlan,
        SourceSpan,
        ValidationReport,
    )
    from novelvideo.director_plan.workspace import (
        SceneBlockingCompileConflict,
        compile_scene_blocking_candidate,
    )

    shot = ShotPlan(
        id="shot-1", source_span_ids=("span-1",), subject="door", action="hold",
        visible_start_state="closed", visible_end_state="closed", duration_seconds=4,
        cinematography=ShotCinematography(
            source="director_plan", source_ids=("span-1",), axis="hallway",
            camera_side="south", screen_direction="still", subjects=(),
            lights=({"light_id": "key", "source_type": "practical", "world_position": "door",
                     "direction": "down", "color_temperature": "warm", "relative_intensity": "key",
                     "attachment": "fixed", "motivation": "lamp"},), key_light_id="key",
            shadow_direction="down", exposure_priority="door", transition_intent="hold",
        ),
    )
    group = NarrativeGroupPlan(
        id="group-1", ordinal=1, source_span_ids=("span-1",), scene_anchor="hallway",
        time_anchor="night", objective="wait", visible_turn="none", relation_to_previous="single",
        shots=(shot,),
    )
    active = DirectorPlanRevision.new(
        episode=1, source_script_hash="source-hash", director_model="director", prompt_version="v2",
        project_style_snapshot_id="style-1", groups=(group,),
    ).model_copy(update={"status": "active", "validation_report": ValidationReport(passed=True)})

    with pytest.raises(SceneBlockingCompileConflict, match="actor id"):
        compile_scene_blocking_candidate(
            active,
            ({"scene_id": "scene-1", "group_id": "group-1", "director_revision_id": active.revision_id, "revision_id": "blocking-1",
              "version": 1, "semantic_revision_id": None,
              "blocking": _blocking(actor_x=12)},),
            source_spans=(SourceSpan(id="span-1", ordinal=1, scene="hallway", time="night", text="wait"),),
        )


@pytest.mark.parametrize('same_group', [False, True])
def test_shared_scene_blocking_accepts_union_but_projects_only_group_assets(same_group):
    from novelvideo.director_plan.models import (
        AssetRequirement,
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotCinematography,
        ShotPlan,
        SourceSpan,
        ValidationReport,
    )
    from novelvideo.director_plan.workspace import compile_scene_blocking_candidate

    def group(group_id, ordinal, subject_id, light_id, prop_id, span_id):
        shot = ShotPlan(
            id=f"shot-{group_id}",
            source_span_ids=(span_id,),
            subject=subject_id,
            action="crosses the room",
            visible_start_state="near entrance",
            visible_end_state="at table",
            duration_seconds=3,
            asset_requirements=(
                AssetRequirement(kind="character_identity", entity_key=subject_id),
                AssetRequirement(kind="prop", entity_key=prop_id),
            ),
            cinematography=ShotCinematography(
                source="director_plan",
                source_ids=(span_id,),
                axis="table edge",
                camera_side="south",
                screen_direction="left to right",
                subjects=(
                    {
                        "subject_id": subject_id,
                        "world_position": "near entrance",
                        "screen_position": "center",
                        "facing": "toward table",
                        "gaze_target": "table",
                        "motion_path": "crosses room",
                    },
                ),
                lights=(
                    {
                        "light_id": light_id,
                        "source_type": "practical",
                        "world_position": "above table",
                        "direction": "down",
                        "color_temperature": "warm",
                        "relative_intensity": "key",
                        "attachment": "fixed",
                        "motivation": "table lamp",
                    },
                ),
                key_light_id=light_id,
                shadow_direction="away from table",
                exposure_priority=subject_id,
                transition_intent="hold",
            ),
        )
        return NarrativeGroupPlan(
            id=group_id,
            ordinal=ordinal,
            source_span_ids=(span_id,),
            scene_anchor="shared room",
            time_anchor="night",
            objective=f"follow {subject_id}",
            visible_turn="arrives at table",
            relation_to_previous="progressive",
            shots=(shot,),
        )

    spans = (
        SourceSpan(id="span-1", ordinal=1, scene="room", time="night", text="hero arrives"),
        SourceSpan(id="span-2", ordinal=2, scene="room", time="night", text="visitor arrives"),
    )
    groups = (
        group("group-1", 1, "hero", "key", "wallet", "span-1"),
        group("group-2", 2, "visitor", "fill", "letter", "span-2"),
    )
    if same_group:
        groups = (groups[0].model_copy(update={
            'source_span_ids': ('span-1', 'span-2'),
            'shots': (*groups[0].shots, *groups[1].shots),
        }),)
    active = DirectorPlanRevision.new(
        episode=1,
        source_script_hash="source-hash",
        director_model="director",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=groups,
    ).model_copy(update={"status": "active", "validation_report": ValidationReport(passed=True)})
    blocking = {
        "label": "Shared room",
        "actors": [
            {"id": "hero", "label": "Hero", "x": 20, "y": 40, "facing": 90, "gaze_target": "visitor"},
            {"id": "visitor", "label": "Visitor", "x": 80, "y": 40, "facing": 270},
        ],
        "props": [
            {"id": "wallet", "label": "Wallet", "x": 40, "y": 55, "facing": 0, "held_by": "visitor"},
            {"id": "letter", "label": "Letter", "x": 60, "y": 55, "facing": 0},
        ],
        "camera": {"x": 50, "y": 90, "facing": 270},
        "lights": [
            {"id": "key", "label": "Key", "x": 20, "y": 15, "facing": 90, "intensity": 60},
            {"id": "fill", "label": "Fill", "x": 80, "y": 15, "facing": 270, "intensity": 30},
        ],
    }
    references = tuple({
        "scene_id": "scene-shared",
        "group_id": group_id,
        "director_revision_id": active.revision_id,
        "revision_id": "shared-blocking-revision",
        "version": 1,
        "semantic_revision_id": None,
        "blocking": blocking,
    } for group_id in (group.id for group in groups))

    candidate = compile_scene_blocking_candidate(active, references, source_spans=spans)

    first, second = [shot.cinematography for group in candidate.groups for shot in group.shots]
    assert first.subjects[0].gaze_target == "visitor"
    assert first.props[0].prop_id == "wallet"
    assert "Visitor (visitor)" in first.props[0].held_by
    assert [item.light_id for item in first.lights] == ["key"]
    assert [item.prop_id for item in second.props] == ["letter"]
    assert [item.light_id for item in second.lights] == ["fill"]


@pytest.fixture
async def checked_handoff(tmp_path):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.script_creation.handoff import HandoffService
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore("director-workspace", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    documents = DocumentStore(sqlite.db_path)
    await documents.initialize()
    sources = EpisodeSourceStore(sqlite)
    service = HandoffService(documents, sources, project_id="director-workspace")
    await service.initialize()
    script = await documents.create(
        kind="episode_script",
        title="第一集",
        episode_number=1,
        markdown="1-1 仓库 夜\n甲：你来了。",
        client_mutation_id="script",
    )
    try:
        yield documents, service, script
    finally:
        await sqlite.close()


async def _save_consistency_run(documents, script, *, issue_reason=None):
    run_id = f"consistency-{script.current_revision_id}"
    run = {
        "id": run_id,
        "episode_document_id": script.id,
        "mode": "actual",
        "status": "completed",
        "context_revisions": {script.id: script.current_revision_id},
    }
    async with documents._db() as db:
        await db.execute(
            "INSERT INTO script_consistency_runs VALUES (?,?,?,?,?,?)",
            (run["id"], f"consistency-mut-{script.current_revision_id}", "hash", json.dumps(run), "now", "now"),
        )
        if issue_reason is not None:
            issue = {
                "id": f"fact-{script.current_revision_id}",
                "category": "fact",
                "proposal_id": None,
                "context_revisions": run["context_revisions"],
                "intentional_reason": issue_reason,
            }
            await db.execute(
                "INSERT INTO script_consistency_issues VALUES (?,?,?,?)",
                (issue["id"], run["id"], json.dumps(issue), "now"),
            )
        await db.commit()
    return run_id


async def test_strict_handoff_context_requires_current_consistency_run(checked_handoff):
    from novelvideo.director_plan.workspace import StrictHandoffReviewRequired, strict_handoff_context

    _documents, handoff, script = checked_handoff
    with pytest.raises(StrictHandoffReviewRequired):
        await strict_handoff_context(handoff, script.id, script.current_revision_id)


async def test_strict_handoff_context_uses_checked_run_and_refuses_unresolved_fact(checked_handoff):
    from novelvideo.director_plan.workspace import StrictHandoffReviewRequired, strict_handoff_context

    documents, handoff, script = checked_handoff
    run_id = await _save_consistency_run(documents, script)

    refs, acknowledgement = await strict_handoff_context(
        handoff, script.id, script.current_revision_id
    )
    assert refs == {}
    assert acknowledgement == {"mode": "checked", "run_id": run_id}

    await documents.save(
        script.id,
        base_revision_id=script.current_revision_id,
        markdown=script.revision.markdown + "\n甲：有事。",
        client_mutation_id="script-next",
    )
    current = await documents.get(script.id)
    await _save_consistency_run(documents, current, issue_reason="")
    with pytest.raises(StrictHandoffReviewRequired):
        await strict_handoff_context(handoff, current.id, current.current_revision_id)


async def test_strict_handoff_context_accepts_persisted_intentional_fact_reason(checked_handoff):
    from novelvideo.director_plan.workspace import strict_handoff_context

    documents, handoff, script = checked_handoff
    run_id = await _save_consistency_run(
        documents, script, issue_reason="观众先知，角色未知"
    )

    refs, acknowledgement = await strict_handoff_context(
        handoff, script.id, script.current_revision_id
    )

    assert refs == {}
    assert acknowledgement == {"mode": "checked", "run_id": run_id}
