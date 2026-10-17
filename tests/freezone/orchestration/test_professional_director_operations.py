from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


BLOCKING = {
    "label": "仓库调度",
    "actors": [{"id": "hero", "label": "甲", "x": 25, "y": 40, "facing": 90}],
    "camera": {"x": 50, "y": 85, "facing": 270},
    "lights": [{"id": "key", "label": "顶灯", "x": 20, "y": 15, "intensity": 50}],
}


def _director_fixture(root: Path, monkeypatch):
    from novelvideo.api.routes import director_plans, director_workspace
    from novelvideo.director_plan.cinematography import (
        MotivatedLight,
        ShotCinematography,
        SubjectBlocking,
    )
    from novelvideo.director_plan.models import (
        AssetRequirement,
        DirectorPlanRevision,
        DirectorShotIntent,
        NarrativeGroupPlan,
        ShotPlan,
        ValidationReport,
    )
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.screenplay_semantics.models import (
        DramaticBeat,
        Scene,
        ScreenplaySemanticRevision,
        SemanticValidationReport,
        SourceBlock,
        SourceRange,
    )
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore

    ctx = SimpleNamespace(project_id="project-a", output_dir=root, state_dir=root / "state")

    async def resolve(_project, _user, *, role):
        assert role in {"editor", "viewer"}
        return ctx

    monkeypatch.setattr(director_workspace, "_resolve", resolve)
    monkeypatch.setattr(director_plans, "_resolve", resolve)

    source_range = SourceRange(start_line=1, end_line=2)
    source_block_range = SourceRange(start_line=2, end_line=2)
    scene = Scene(
        id="scene-1",
        ordinal=1,
        source_range=source_range,
        heading="1-1 仓库 夜",
        location="仓库",
        time_of_day="夜",
        blocks=(SourceBlock(
            id="span-1", ordinal=1, kind="action", text="甲等待", source_range=source_block_range,
        ),),
        content_hash="scene-hash",
    )
    beat = DramaticBeat(
        id="beat-1",
        ordinal=1,
        scene_id=scene.id,
        source_ranges=(source_block_range,),
        goal="进入仓库",
        obstacle="门未开",
        action="甲等待",
        reaction="甲观察门口",
        turn="门响",
        result="甲转向门口",
        emotional_shift="平静转为警觉",
        estimated_duration_seconds=4,
        must_show=("甲等待",),
        script_facts=("甲等待",),
    )
    semantic_store = ScreenplaySemanticStore(root)
    semantic = ScreenplaySemanticRevision.new(
        episode=1,
        source_revision=1,
        source_hash="source-hash",
        scenes=(scene,),
        beats=(beat,),
        validation_report=SemanticValidationReport(passed=True),
    ).model_copy(update={"status": "review_required"})
    semantic_store.save(semantic)
    semantic = semantic_store.activate(1, semantic.revision_id, expected_source_revision=1)

    shot = ShotPlan(
        id="shot-1",
        source_span_ids=("span-1",),
        subject="甲",
        action="等待",
        visible_start_state="甲站在门外",
        visible_end_state="甲转向门口",
        duration_seconds=4,
        dramatic_beat_ids=(beat.id,),
        intent=DirectorShotIntent(
            narrative_purpose="建立等待感",
            audience_attention="甲的反应",
            emotional_effect="逐渐警觉",
            continuity_strategy="保持门口轴线",
        ),
        asset_requirements=(AssetRequirement(kind="character_identity", entity_key="hero"),),
        cinematography=ShotCinematography(
            source="director_plan",
            source_ids=("span-1",),
            axis="门口",
            camera_side="南侧",
            screen_direction="向门口转身",
            subjects=(SubjectBlocking(
                subject_id="hero", world_position="门外", screen_position="左侧",
                facing="朝向门", gaze_target="门", motion_path="原地等待",
            ),),
            lights=(MotivatedLight(
                light_id="key", source_type="practical", world_position="顶灯",
                direction="向下", color_temperature="暖色", relative_intensity="主光",
                attachment="fixed", motivation="仓库顶灯",
            ),),
            key_light_id="key",
            shadow_direction="向后",
            exposure_priority="面部",
            transition_intent="保持",
        ),
    )
    group = NarrativeGroupPlan(
        id="group-1",
        ordinal=1,
        source_span_ids=("span-1",),
        dramatic_beat_ids=(beat.id,),
        scene_anchor="仓库",
        time_anchor="夜",
        objective="等待进入",
        visible_turn="甲转向门口",
        relation_to_previous="single",
        shots=(shot,),
    )
    plans = DirectorPlanStore(root)
    plan = DirectorPlanRevision.new(
        episode=1,
        source_script_hash="source-hash",
        director_model="test",
        prompt_version="director-plan-v2",
        project_style_snapshot_id="style-1",
        semantic_revision_id=semantic.revision_id,
        groups=(group,),
    ).model_copy(update={"status": "review_required", "validation_report": ValidationReport(passed=True)})
    plans.save(plan)
    active = plans.activate(1, plan.revision_id)
    return ctx, active, semantic


def _run_and_attempt(active, operation: str, inputs: dict, *, attempt_id="attempt-a"):
    node = {
        "id": "director",
        "type": "directorWorkspaceNode",
        "data": {
            "episodeNumber": 1,
            "directorRevisionId": active.revision_id,
            "operation": operation,
            "directorOperationInputs": {"operation": operation, **deepcopy(inputs)},
        },
    }
    run = {
        "id": "run-a",
        "project_id": "project-a",
        "actor": {"id": "editor"},
        "snapshot": {"nodes": [node], "edges": []},
    }
    attempt = {"id": attempt_id, "node_id": "director"}
    return run, attempt


def _blocking_inputs(*, base_version=None):
    return {
        "sceneId": "scene-1",
        "groupIds": ["group-1"],
        "baseVersion": base_version,
        "blocking": deepcopy(BLOCKING),
    }


@pytest.mark.asyncio
async def test_save_blocking_uses_local_domain_store_cas_and_stable_attempt_receipt(tmp_path, monkeypatch):
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore
    from novelvideo.freezone.orchestration.adapters import professional_edits

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    run, attempt = _run_and_attempt(active, "save_blocking", _blocking_inputs())

    first = await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "save_blocking")
    monkeypatch.setattr(
        professional_edits,
        "_validate_scope",
        lambda *_args: pytest.fail("completed retries must recover before revalidating mutable scope"),
    )
    replay = await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "save_blocking")
    recovered = await professional_edits.reconcile_submission(run, attempt)
    scene = DirectorWorkspaceStore(tmp_path).get_scene_blocking(1, "scene-1")

    assert first == replay
    assert first["professional_edit"] is True
    assert first["operation"] == "save_blocking"
    assert first["operation_receipt"]["receipt_key"] == first["receipt_key"]
    assert first["revision_id"] == active.revision_id
    assert first["scene_id"] == "scene-1"
    assert recovered == {"status": "submitted", "submission": first}
    assert len(scene["history"]) == 1
    assert scene["current"]["blocking"]["label"] == "仓库调度"

    reconciled = await professional_edits.reconcile(run, first)
    assert reconciled["status"] == "succeeded"
    assert reconciled["result"]["directorRevisionId"] == active.revision_id
    assert reconciled["result"]["directorOperationReceipt"]["scene_blocking_revision_id"] == scene["current"]["revision_id"]
    assert reconciled["result"]["directorOperationReceipt"]["receipt_key"] == first["receipt_key"]


@pytest.mark.asyncio
async def test_save_blocking_preserves_domain_compare_and_swap_conflict(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from novelvideo.director_plan.workspace import DirectorWorkspaceStore
    from novelvideo.freezone.orchestration.adapters import professional_edits

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    store = DirectorWorkspaceStore(tmp_path)
    store.save_scene_blocking(
        1,
        "scene-1",
        base_version=None,
        semantic_revision_id=active.semantic_revision_id,
        director_revision_id=active.revision_id,
        blocking=BLOCKING,
        group_ids=("group-1",),
    )
    run, attempt = _run_and_attempt(active, "save_blocking", _blocking_inputs(base_version=0))

    with pytest.raises(HTTPException) as conflict:
        await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "save_blocking")

    assert conflict.value.status_code == 409
    assert len(store.get_scene_blocking(1, "scene-1")["history"]) == 1


@pytest.mark.asyncio
async def test_compile_blocking_pins_bound_active_revision_and_keeps_candidate_unadopted(tmp_path, monkeypatch):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore
    from novelvideo.freezone.orchestration.adapters import professional_edits

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    DirectorWorkspaceStore(tmp_path).save_scene_blocking(
        1,
        "scene-1",
        base_version=None,
        semantic_revision_id=active.semantic_revision_id,
        director_revision_id=active.revision_id,
        blocking=BLOCKING,
        group_ids=("group-1",),
    )
    run, attempt = _run_and_attempt(active, "compile_blocking", {"baseRevisionId": active.revision_id})

    submission = await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "compile_blocking")
    candidate = DirectorPlanStore(tmp_path).load(1, submission["revision_id"])

    assert candidate.status == "review_required"
    assert candidate.parent_revision_id == active.revision_id
    assert candidate.scene_blocking_references[0].group_id == "group-1"
    assert DirectorPlanStore(tmp_path).load_active(1).revision_id == active.revision_id


@pytest.mark.asyncio
async def test_edit_shot_only_creates_update_shot_candidate_and_rejects_group_mismatch(tmp_path, monkeypatch):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.freezone.orchestration.adapters import professional_edits
    from novelvideo.freezone.orchestration.models import OrchestrationError

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    run, attempt = _run_and_attempt(active, "edit_shot", {
        "revisionId": active.revision_id,
        "groupId": "group-1",
        "shotId": "shot-1",
        "action": "停下并观察门口",
    })
    submission = await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "edit_shot")
    candidate = DirectorPlanStore(tmp_path).load(1, submission["revision_id"])

    assert candidate.status == "review_required"
    assert candidate.parent_revision_id == active.revision_id
    assert candidate.groups[0].shots[0].action == "停下并观察门口"
    assert DirectorPlanStore(tmp_path).load_active(1).revision_id == active.revision_id

    wrong_group, wrong_attempt = _run_and_attempt(active, "edit_shot", {
        "revisionId": active.revision_id,
        "groupId": "group-other",
        "shotId": "shot-1",
        "action": "越界编辑",
    }, attempt_id="attempt-b")
    with pytest.raises(OrchestrationError, match="group"):
        await professional_edits.submit(
            wrong_group, wrong_attempt, wrong_group["snapshot"]["nodes"][0], "edit_shot"
        )


@pytest.mark.asyncio
async def test_unknown_scene_write_is_read_only_reconciled_and_never_replayed(tmp_path, monkeypatch):
    from novelvideo.api.routes import director_workspace
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore
    from novelvideo.freezone.orchestration.adapters import professional_edits
    from novelvideo.freezone.orchestration.models import OrchestrationError

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    run, attempt = _run_and_attempt(active, "save_blocking", _blocking_inputs())
    original = director_workspace.save_scene_blocking
    calls = 0

    async def write_then_lose_reply(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original(*args, **kwargs)
        raise RuntimeError("simulated lost adapter response")

    monkeypatch.setattr(director_workspace, "save_scene_blocking", write_then_lose_reply)
    with pytest.raises(RuntimeError, match="lost adapter response"):
        await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "save_blocking")

    assert await professional_edits.reconcile_submission(run, attempt) == {"status": "submission_unknown"}
    with pytest.raises(OrchestrationError, match="unknown"):
        await professional_edits.submit(run, attempt, run["snapshot"]["nodes"][0], "save_blocking")
    assert calls == 1
    assert len(DirectorWorkspaceStore(tmp_path).get_scene_blocking(1, "scene-1")["history"]) == 1


@pytest.mark.asyncio
async def test_same_attempt_with_different_request_is_rejected_before_another_write(tmp_path, monkeypatch):
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore
    from novelvideo.freezone.orchestration.adapters import professional_edits
    from novelvideo.freezone.orchestration.models import OrchestrationError

    _ctx, active, _semantic = _director_fixture(tmp_path, monkeypatch)
    run, attempt = _run_and_attempt(active, "save_blocking", _blocking_inputs())
    node = run["snapshot"]["nodes"][0]
    first = await professional_edits.submit(run, attempt, node, "save_blocking")
    changed = deepcopy(run)
    changed_node = changed["snapshot"]["nodes"][0]
    changed_node["data"]["directorOperationInputs"]["blocking"]["label"] = "不同调度"

    with pytest.raises(OrchestrationError, match="idempotency"):
        await professional_edits.submit(changed, attempt, changed_node, "save_blocking")
    assert first["professional_edit"] is True
    assert len(DirectorWorkspaceStore(tmp_path).get_scene_blocking(1, "scene-1")["history"]) == 1
