from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_adopt_director_workspace_uses_shared_activation_sync(director_context, monkeypatch):
    from novelvideo.api.routes import director_plans, director_workspace as api

    ctx, _script = director_context
    calls = []

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def activate_and_sync(received_ctx, episode, revision_id):
        calls.append((received_ctx, episode, revision_id))
        return type(
            "Revision",
            (),
            {
                "revision_id": revision_id,
                "model_dump": lambda self, **_kwargs: {"revision_id": revision_id},
            },
        )()

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(
        director_plans, "_activate_and_sync_director_plan", activate_and_sync
    )

    response = await api.adopt_director_workspace(
        "project",
        1,
        api.DirectorWorkspaceAdoptRequest(revision_id="revision-1"),
        user={},
    )

    assert calls == [(ctx, 1, "revision-1")]
    assert response["data"]["revision_id"] == "revision-1"


@pytest.mark.asyncio
async def test_shared_director_activation_sync_checks_projection_before_refresh(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_plans

    revision = SimpleNamespace(
        revision_id="revision-2",
        groups=(SimpleNamespace(id="group-1"),),
        project_style_snapshot_id="style-2",
    )
    calls = []

    class Store:
        def load_active_for_sync(self, _episode, _revision_id):
            calls.append("load_active")
            return None

        def activate(self, episode, revision_id):
            assert (episode, revision_id) == (2, "revision-2")
            calls.append("activate")
            return revision

    ctx = SimpleNamespace(output_dir=tmp_path)
    monkeypatch.setattr(director_plans, "_build_director_plan_store", lambda _ctx: Store())
    from novelvideo.narrative_groups import service as group_service
    monkeypatch.setattr(
        group_service,
        "load_materialized_groups",
        lambda _output_dir, _episode: [
            SimpleNamespace(id="group-1", style_snapshot_id="style-2")
        ],
    )

    async def refresh(_ctx, episode, revision_id):
        calls.append(("refresh", episode, revision_id))
        return 1

    monkeypatch.setattr(director_plans, "_refresh_plan_references", refresh)

    assert (
        await director_plans._activate_and_sync_director_plan(ctx, 2, "revision-2")
        is revision
    )
    assert calls == ["load_active", "activate", ("refresh", 2, "revision-2")]

    calls.clear()
    monkeypatch.setattr(
        group_service,
        "load_materialized_groups",
        lambda _output_dir, _episode: [
            SimpleNamespace(id="group-1", style_snapshot_id="stale-style")
        ],
    )
    with pytest.raises(HTTPException) as error:
        await director_plans._activate_and_sync_director_plan(ctx, 2, "revision-2")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DIRECTOR_PLAN_ACTIVATED_PROJECTION_REQUIRED"
    assert calls == ["load_active", "activate"]


@pytest.mark.asyncio
async def test_shared_activation_accepts_materialized_narrative_group_model(
    tmp_path, monkeypatch
):
    """The canonical NarrativeGroup stores style in its snapshot, not style_snapshot_id."""
    from types import SimpleNamespace

    from novelvideo.api.routes import director_plans
    from novelvideo.narrative_groups.models import GridLayout, NarrativeGroup

    revision = SimpleNamespace(
        revision_id="revision-actual-model",
        groups=(SimpleNamespace(id="group-1"),),
        project_style_snapshot_id="style-actual",
    )

    class Store:
        def load_active_for_sync(self, _episode, _revision_id):
            return revision

    materialized = NarrativeGroup(
        id="group-1",
        ordinal=1,
        beat_ids=(),
        layout=GridLayout(rows=1, columns=1, capacity=1),
        cell_to_beat=(),
        director_revision_id="revision-actual-model",
        effective_style_snapshot={"snapshot_id": "style-actual"},
    )
    monkeypatch.setattr(director_plans, "_build_director_plan_store", lambda _ctx: Store())
    from novelvideo.narrative_groups import service as group_service
    monkeypatch.setattr(group_service, "load_materialized_groups", lambda *_: [materialized])

    refreshed = []

    async def refresh(_ctx, episode, revision_id):
        refreshed.append((episode, revision_id))

    monkeypatch.setattr(director_plans, "_refresh_plan_references", refresh)
    ctx = SimpleNamespace(output_dir=tmp_path)

    assert await director_plans._activate_and_sync_director_plan(
        ctx, 1, "revision-actual-model"
    ) is revision
    assert refreshed == [(1, "revision-actual-model")]


@pytest.mark.asyncio
async def test_activation_retry_resumes_sync_for_same_active_revision(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_plans
    from novelvideo.narrative_groups import service as group_service

    revision = SimpleNamespace(
        revision_id="revision-3",
        status="active",
        groups=(SimpleNamespace(id="group-1"),),
        project_style_snapshot_id="style-3",
    )
    calls = []

    class Store:
        active = None

        def load_active_for_sync(self, episode, revision_id):
            calls.append(("load_active", episode, revision_id))
            if self.active is not None and self.active.revision_id == revision_id:
                return self.active
            return None

        def activate(self, episode, revision_id):
            assert (episode, revision_id) == (2, "revision-3")
            if self.active is not None:
                raise ValueError("only review_required or superseded revisions can be activated")
            calls.append("activate")
            self.active = revision
            return revision

    store = Store()
    ctx = SimpleNamespace(output_dir=tmp_path)
    monkeypatch.setattr(director_plans, "_build_director_plan_store", lambda _ctx: store)
    monkeypatch.setattr(
        group_service,
        "load_materialized_groups",
        lambda _output_dir, _episode: [
            SimpleNamespace(id="group-1", style_snapshot_id="style-3")
        ],
    )

    refresh_count = 0

    async def refresh(_ctx, episode, revision_id):
        nonlocal refresh_count
        refresh_count += 1
        calls.append(("refresh", episode, revision_id))
        if refresh_count == 1:
            raise ValueError("transient reference catalog failure")
        return 1

    monkeypatch.setattr(director_plans, "_refresh_plan_references", refresh)

    with pytest.raises(HTTPException) as first_error:
        await director_plans._activate_and_sync_director_plan(ctx, 2, "revision-3")
    assert first_error.value.detail["code"] == "DIRECTOR_PLAN_ACTIVATED_REFERENCE_REFRESH_REQUIRED"

    assert await director_plans._activate_and_sync_director_plan(ctx, 2, "revision-3") is revision
    assert calls == [
        ("load_active", 2, "revision-3"),
        "activate",
        ("refresh", 2, "revision-3"),
        ("load_active", 2, "revision-3"),
        ("refresh", 2, "revision-3"),
    ]


@pytest.mark.asyncio
async def test_get_director_workspace_returns_adopted_script_stages_and_scene_blocking(
    director_context, monkeypatch
):
    from novelvideo.api.routes import director_workspace as api
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    ctx, script = director_context
    DirectorWorkspaceStore(ctx.output_dir).save_scene_blocking(
        1,
        "scene-a",
        base_version=None,
        semantic_revision_id=None,
        blocking={
            "label": "仓库",
            "actors": [],
            "camera": {"x": 50, "y": 85, "facing": 270},
            "lights": [],
        },
        group_ids=(),
    )
    async def resolve(_project, _user, *, role):
        assert role == "viewer"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    response = await api.get_director_workspace("project", 1, user={})
    data = response["data"]

    assert data["episode"] == 1
    assert data["script"] == {
        "document_id": script.id,
        "revision_id": script.current_revision_id,
        "adopted_revision_id": script.current_revision_id,
        "source_sync": {
            "status": "requires_explicit_handoff",
            "source_revision": None,
            "matches_episode_source": False,
        },
    }
    assert {key: value["status"] for key, value in data["stages"].items()} == {
        "handoff": "waiting",
        "semantics": "waiting",
        "director_plan": "waiting",
        "director_version": "waiting",
        "asset_breakdown": "waiting",
        "group_plan": "waiting",
    }
    assert data["revision"] is None
    assert data["groups"] == []
    assert data["scene_blocking"]["scene-a"]["current"]["blocking"]["label"] == "仓库"


@pytest.mark.asyncio
async def test_director_workspace_returns_real_handoff_receipt_reference(director_context, monkeypatch):
    import hashlib

    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    sqlite, _documents, handoff = await api._load_handoff_services(ctx)
    try:
        prepared = await handoff.prepare(
            document_id=script.id,
            revision_id=script.current_revision_id,
            reference_revisions={},
            selected_entity_ids=[],
            update_scope={"mode": "none"},
            fact_acknowledgement={"mode": "unchecked", "reason": "用户已确认"},
            client_mutation_id="workspace-receipt-prepare",
        )
        await handoff.confirm(
            prepared["id"],
            expected_source_project_revision=prepared["expected_source_project_revision"],
            client_mutation_id="workspace-receipt-confirm",
        )
    finally:
        await sqlite.close()

    async def resolve(_project, _user, *, role):
        assert role == "viewer"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    data = (await api.get_director_workspace("project", 1, user={}))["data"]

    artifact = data["script"]["artifact"]
    assert artifact["path"] == (
        f"episodes/001/handoff-receipts/{script.current_revision_id}.md"
    )
    assert artifact["sha256"] == hashlib.sha256(
        script.revision.markdown.encode("utf-8")
    ).hexdigest()
    assert artifact["source_revision"] == data["stages"]["handoff"]["source_revision"]
    assert artifact["matches_episode_source"] is True
    assert (ctx.output_dir / artifact["path"]).read_text(encoding="utf-8") == script.revision.markdown


@pytest.mark.asyncio
async def test_workspace_marks_new_script_revision_unmatched_until_source_sync(
    director_context, monkeypatch
):
    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    sqlite, documents, handoff = await api._load_handoff_services(ctx)
    try:
        prepared = await handoff.prepare(
            document_id=script.id,
            revision_id=script.current_revision_id,
            reference_revisions={},
            selected_entity_ids=[],
            update_scope={"mode": "none"},
            fact_acknowledgement={"mode": "unchecked", "reason": "用户已确认"},
            client_mutation_id="workspace-pending-prepare",
        )
        confirmed = await handoff.confirm(
            prepared["id"],
            expected_source_project_revision=prepared["expected_source_project_revision"],
            client_mutation_id="workspace-pending-confirm",
        )
        revised = await documents.save(
            script.id,
            base_revision_id=script.current_revision_id,
            markdown="1-1 仓库 夜\n甲：这是新的正式剧本版本。",
            client_mutation_id="workspace-pending-save",
        )
    finally:
        await sqlite.close()

    async def resolve(_project, _user, *, role):
        assert role == "viewer"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    data = (await api.get_director_workspace("project", 1, user={}))["data"]

    assert data["script"]["revision_id"] == revised.current_revision_id
    assert data["script"]["source_sync"] == {
        "status": "pending_sync",
        "source_revision": confirmed["source_revision"],
        "matches_episode_source": False,
    }
    assert "artifact" not in data["script"]


def test_workspace_maps_legacy_group_scene_from_source_span_ownership():
    from types import SimpleNamespace

    from novelvideo.api.routes.director_workspace import _group_scene_id

    semantic = SimpleNamespace(
        scenes=(SimpleNamespace(id="scene-1", blocks=(SimpleNamespace(id="span-1"),)),)
    )
    group = {"source_span_ids": ["span-1"], "scene_anchor": "hallway"}

    assert _group_scene_id(group, semantic) == "scene-1"
    assert _group_scene_id({"source_span_ids": ["span-1", "missing"]}, semantic) is None


def test_workspace_does_not_report_derived_stages_completed_for_old_script_revision():
    from types import SimpleNamespace

    from novelvideo.api.routes.director_workspace import _derived_stages

    revision = SimpleNamespace(
        revision_id="old-director-revision",
        source_script_hash="old-hash",
        groups=(SimpleNamespace(shots=(SimpleNamespace(asset_requirements=(object(),)),)),),
        migration_report=SimpleNamespace(items=()),
    )

    stages = _derived_stages(
        revision, expected_source_revision=8, expected_source_hash="new-hash"
    )

    assert [stage["status"] for stage in stages] == ["needs_review"] * 3


def test_workspace_does_not_report_plan_completed_for_old_semantic_revision():
    from types import SimpleNamespace

    from novelvideo.api.routes.director_workspace import _director_plan_stage

    revision = SimpleNamespace(
        revision_id="director-old-semantic",
        source_script_hash="same-source",
        semantic_revision_id="semantic-old",
        groups=(object(),),
    )

    stage = _director_plan_stage(
        revision,
        {"status": "completed"},
        expected_source_hash="same-source",
        expected_semantic_revision_id="semantic-current",
    )
    queued = _director_plan_stage(
        revision,
        {"status": "running", "task_id": "director-running"},
        expected_source_hash="same-source",
        expected_semantic_revision_id="semantic-current",
    )

    assert stage["status"] == "needs_review"
    assert queued == {"status": "running", "task_id": "director-running"}


@pytest.mark.asyncio
async def test_put_scene_blocking_uses_revision_id_compare_and_swap(director_context, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    body = api.SceneBlockingRequest(
        base_version=None,
        blocking={
            "label": "仓库",
            "actors": [],
            "camera": {"x": 50, "y": 85, "facing": 270},
            "lights": [],
        },
        group_ids=[],
    )
    saved = await api.save_scene_blocking("project", 1, "scene-a", body, user={})
    revision_id = saved["data"]["revision_id"]
    next_body = body.model_copy(update={"base_version": revision_id})
    next_saved = await api.save_scene_blocking(
        "project", 1, "scene-a", next_body, user={}
    )
    assert next_saved["data"]["version"] == 2
    assert next_saved["data"]["revision_id"] != revision_id

    stale_body = body.model_copy(update={"base_version": revision_id})
    with pytest.raises(HTTPException) as error:
        await api.save_scene_blocking("project", 1, "scene-a", stale_body, user={})
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_save_scene_blocking_rejects_group_from_another_scene(director_context, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    scene_a = SimpleNamespace(id="scene-a", blocks=())
    scene_b = SimpleNamespace(id="scene-b", blocks=())
    semantic = SimpleNamespace(
        revision_id="semantic-current", source_hash="script-hash", scenes=(scene_a, scene_b)
    )
    group_a = SimpleNamespace(
        id="group-a",
        model_dump=lambda **_kwargs: {"id": "group-a", "creative_context": {"scene_id": "scene-a"}},
    )
    group_b = SimpleNamespace(
        id="group-b",
        model_dump=lambda **_kwargs: {"id": "group-b", "creative_context": {"scene_id": "scene-b"}},
    )
    director_revision = SimpleNamespace(
        revision_id="director-current", episode=1, semantic_revision_id=semantic.revision_id,
        source_script_hash=semantic.source_hash, groups=(group_a, group_b),
    )

    class SemanticStore:
        def __init__(self, _root):
            pass

        def load_active(self, _episode):
            return semantic

    class PlanStore:
        def __init__(self, _root):
            pass

        def load(self, episode, revision_id):
            assert episode == 1 and revision_id == director_revision.revision_id
            return director_revision

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "ScreenplaySemanticStore", SemanticStore)
    monkeypatch.setattr(api, "DirectorPlanStore", PlanStore)

    body = api.SceneBlockingRequest(
        base_version=None,
        blocking={"label": "Scene A", "actors": [], "camera": None, "lights": []},
        group_ids=["group-b"],
        director_revision_id=director_revision.revision_id,
    )
    with pytest.raises(HTTPException) as error:
        await api.save_scene_blocking("project", 1, "scene-a", body, user={})

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SCENE_BLOCKING_GROUP_SCENE_MISMATCH"


@pytest.mark.asyncio
async def test_unpin_scene_blocking_keeps_history_and_uses_current_revision_cas(director_context, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    ctx, _script = director_context
    store = DirectorWorkspaceStore(ctx.output_dir)
    saved = store.save_scene_blocking(
        1, "obsolete-scene", base_version=None, semantic_revision_id="old-semantic",
        director_revision_id="old-director", blocking={
            "label": "Old", "actors": [], "camera": None, "lights": []
        }, group_ids=("old-group",),
    )

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    response = await api.unpin_scene_blocking(
        "project", 1, "obsolete-scene",
        api.SceneBlockingUnpinRequest(base_revision_id=saved["revision_id"]), user={},
    )
    current = store.get_scene_blocking(1, "obsolete-scene")

    assert response["data"]["removed_group_ids"] == ["old-group"]
    assert current["group_references"] == []
    assert len(current["history"]) == 1

    with pytest.raises(HTTPException) as error:
        await api.unpin_scene_blocking(
            "project", 1, "obsolete-scene",
            api.SceneBlockingUnpinRequest(base_revision_id="stale-version"), user={},
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_compile_scene_blocking_saves_candidate_without_adopting_it(director_context, monkeypatch):
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api
    from novelvideo.director_plan.models import (
        AssetRequirement,
        DirectorPlanRevision,
        DirectorShotIntent,
        NarrativeGroupPlan,
        ShotCinematography,
        ShotPlan,
        ValidationReport,
    )
    from novelvideo.director_plan.workspace import DirectorWorkspaceStore

    ctx, _script = director_context
    shot = ShotPlan(
        id="shot-1", source_span_ids=("span-1",), subject="hero", action="waits",
        visible_start_state="door closed", visible_end_state="door open", duration_seconds=4,
        dramatic_beat_ids=("beat-1",),
        intent=DirectorShotIntent(
            narrative_purpose="show hesitation", audience_attention="hero",
            emotional_effect="fear", continuity_strategy="hold axis",
        ),
        asset_requirements=(AssetRequirement(kind="character_identity", entity_key="hero"),),
        cinematography=ShotCinematography(
            source="director_plan", source_ids=("span-1",), axis="doorway",
            camera_side="south", screen_direction="no travel",
            subjects=({"subject_id": "hero", "world_position": "door", "screen_position": "left",
                       "facing": "toward room", "gaze_target": "door", "motion_path": "hold"},),
            lights=({"light_id": "key", "source_type": "practical", "world_position": "above door",
                     "direction": "down", "color_temperature": "warm", "relative_intensity": "key",
                     "attachment": "fixed", "motivation": "hall lamp"},),
            key_light_id="key", shadow_direction="below", exposure_priority="face",
            transition_intent="hold",
        ),
    )
    group = NarrativeGroupPlan(
        id="group-1", ordinal=1, source_span_ids=("span-1",), scene_anchor="hallway",
        time_anchor="night", objective="enter", visible_turn="door opens",
        relation_to_previous="single", dramatic_beat_ids=("beat-1",), shots=(shot,),
    )
    active = DirectorPlanRevision.new(
        episode=1, source_script_hash="hash", director_model="director", prompt_version="v2",
        project_style_snapshot_id="style", semantic_revision_id="semantic-1", groups=(group,),
    ).model_copy(update={"status": "active", "validation_report": ValidationReport(passed=True)})
    scene = SimpleNamespace(
        id="scene-1", location="hallway", heading="hallway", time_of_day="night",
        blocks=(SimpleNamespace(id="span-1", text="hero waits", kind="action"),),
    )
    semantic = SimpleNamespace(revision_id="semantic-1", scenes=(scene,), beats=(SimpleNamespace(id="beat-1"),))
    saved_candidates = []

    class FakePlanStore:
        def __init__(self, _root):
            pass

        def load_active(self, episode):
            assert episode == 1
            return active

        def save(self, candidate):
            saved_candidates.append(candidate)

    class FakeSemanticStore:
        def __init__(self, _root):
            pass

        def load_active(self, episode):
            assert episode == 1
            return semantic

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "DirectorPlanStore", FakePlanStore)
    monkeypatch.setattr(api, "ScreenplaySemanticStore", FakeSemanticStore)
    monkeypatch.setattr(api, "_resolve", resolve)
    DirectorWorkspaceStore(ctx.output_dir).save_scene_blocking(
        1, "scene-1", base_version=None, semantic_revision_id="semantic-1",
        director_revision_id=active.revision_id,
        blocking={
            "label": "hallway", "actors": [{"id": "hero", "label": "Hero", "x": 25, "y": 40, "facing": 90}],
            "camera": {"x": 50, "y": 85, "facing": 270},
            "lights": [{"id": "key", "label": "Key", "x": 20, "y": 15, "intensity": 50}],
        },
        group_ids=("group-1",),
    )

    response = await api.compile_director_scene_blocking(
        "project", 1, api.DirectorWorkspaceCompileRequest(base_revision_id=active.revision_id), user={}
    )
    candidate = response["data"]

    assert candidate["status"] == "review_required"
    assert candidate["parent_revision_id"] == active.revision_id
    assert candidate["scene_blocking_references"][0]["group_id"] == "group-1"
    assert candidate["groups"][0]["shots"][0]["cinematography"]["source_ids"] == ["span-1"]
    assert "x=25%" in candidate["groups"][0]["shots"][0]["cinematography"]["subjects"][0]["world_position"]
    assert len(saved_candidates) == 1
    assert active.groups[0].shots[0].cinematography.camera_side == "south"


@pytest.mark.asyncio
async def test_compile_scene_blocking_requires_the_current_adopted_base(director_context, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api
    from novelvideo.director_plan.models import DirectorPlanRevision, NarrativeGroupPlan, ShotPlan, ValidationReport

    ctx, _script = director_context
    group = NarrativeGroupPlan(
        id="group-1", ordinal=1, source_span_ids=("span-1",), scene_anchor="hallway",
        time_anchor="night", objective="enter", visible_turn="door opens", relation_to_previous="single",
        shots=(ShotPlan(
            id="shot-1", source_span_ids=("span-1",), subject="door", action="waits",
            visible_start_state="closed", visible_end_state="open", duration_seconds=2,
        ),),
    )
    active = DirectorPlanRevision.new(
        episode=1, source_script_hash="hash", director_model="director", prompt_version="v2",
        project_style_snapshot_id="style", groups=(group,),
    ).model_copy(update={"status": "active", "validation_report": ValidationReport(passed=True)})

    class FakePlanStore:
        def __init__(self, _root):
            pass

        def load_active(self, _episode):
            return active

    async def resolve(_project, _user, *, role):
        return ctx

    monkeypatch.setattr(api, "DirectorPlanStore", FakePlanStore)
    monkeypatch.setattr(api, "_resolve", resolve)

    with pytest.raises(HTTPException) as error:
        await api.compile_director_scene_blocking(
            "project", 1, api.DirectorWorkspaceCompileRequest(base_revision_id="stale"), user={}
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DIRECTOR_WORKSPACE_BASE_REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_workspace_exposes_current_semantic_review_candidate(director_context, monkeypatch):
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api
    from novelvideo.screenplay_semantics.models import (
        DramaticBeat,
        Scene,
        ScreenplaySemanticRevision,
        SemanticValidationReport,
        SourceBlock,
        SourceRange,
    )
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore

    ctx, script = director_context
    scene = Scene(
        id="scene-review",
        ordinal=1,
        source_range=SourceRange(start_line=1, end_line=2),
        heading="1-1 仓库 夜",
        content_hash="scene-hash",
        blocks=(SourceBlock(
            id="span-review", ordinal=1, kind="action", text="甲走进仓库",
            source_range=SourceRange(start_line=2, end_line=2),
        ),),
    )
    beat = DramaticBeat(
        id="beat-review", ordinal=1, scene_id=scene.id,
        source_ranges=(SourceRange(start_line=2, end_line=2),),
        goal="进入仓库", obstacle="门被锁住", action="甲推门", reaction="甲停下",
        turn="门锁弹开", result="甲进入仓库", emotional_shift="迟疑转为警惕",
        estimated_duration_seconds=4, must_show=("推门",), script_facts=("甲推门",),
    )
    candidate = ScreenplaySemanticRevision.new(
        episode=1,
        source_revision=3,
        source_hash="current-source-hash",
        scenes=(scene,),
        beats=(beat,),
        validation_report=SemanticValidationReport(passed=True),
    ).model_copy(update={"status": "review_required"})
    ScreenplaySemanticStore(ctx.output_dir).save(candidate)

    source = SimpleNamespace(
        episode_number=1, source_revision=3, content_hash="current-source-hash"
    )

    class Documents:
        async def list(self):
            return [script]

    class Sources:
        async def list_sources(self):
            return [source]

    class Handoff:
        sources = Sources()

        async def list(self, _episode):
            return []

    monkeypatch.setattr(
        api, "_task_stage", lambda *_args, **_kwargs: {"status": "waiting"}
    )
    data = await api._serialize_workspace(ctx, 1, Documents(), Handoff())

    assert data["semantics"]["active_revision_id"] is None
    assert data["semantics"]["current_source_revision"] == 3
    assert data["semantics"]["candidates"][0]["revision_id"] == candidate.revision_id
    assert data["semantics"]["candidates"][0]["is_current_source"] is True
    assert data["semantics"]["candidates"][0]["can_activate"] is True
    assert data["stages"]["semantics"] == {
        "status": "needs_review", "revision_id": candidate.revision_id
    }


@pytest.mark.asyncio
async def test_initial_async_prepare_does_not_require_semantics_to_be_active(
    director_context, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    calls = []
    adopted_script = SimpleNamespace(
        kind="episode_script",
        episode_number=1,
        current_revision_id=script.current_revision_id,
        adopted_revision_id=script.current_revision_id,
        id=script.id,
    )

    class SQLite:
        async def close(self):
            pass

    class Documents:
        @asynccontextmanager
        async def _db(self):
            yield Database()

        async def get(self, _document_id):
            return adopted_script

    class Query:
        async def fetchone(self):
            return None

    class Database:
        async def execute(self, *_args):
            return Query()

    class Handoff:
        project_id = "project"

        async def _project_source_revision(self, _db):
            return 1

        async def prepare(self, **_kwargs):
            return {
                "id": "handoff-1", "status": "prepared",
                "expected_source_project_revision": 1,
                "snapshot": {"update_scope": {"mode": "all", "scene_ids": []}},
            }

        async def confirm(self, *_args, **_kwargs):
            return {"id": "handoff-1", "status": "source_written", "source_revision": 3}

        async def dispatch(self, *_args, **_kwargs):
            return {"id": "handoff-1", "status": "dispatched", "source_revision": 3}

    class SemanticStore:
        def __init__(self, _root):
            pass

        def load_active(self, _episode):
            return None

    class Backend:
        async def enqueue_project_task(self, *_args, **_kwargs):
            calls.append("director_plan")
            return SimpleNamespace(task_state=SimpleNamespace(task_id="director-task"))

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def strict_context(*_args, **_kwargs):
        return (), {}

    async def load_services(_ctx):
        return SQLite(), Documents(), Handoff()

    async def serialize(*_args, **_kwargs):
        return {
            "stages": {
                "semantics": {"status": "running", "task_id": "semantic-task"},
                "director_plan": {"status": "waiting"},
            },
            "semantics": {"active_revision_id": None, "revisions": []},
        }

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "strict_handoff_context", strict_context)
    monkeypatch.setattr(api, "_load_handoff_services", load_services)
    monkeypatch.setattr(api, "_serialize_workspace", serialize)
    monkeypatch.setattr(api, "ScreenplaySemanticStore", SemanticStore)
    monkeypatch.setattr(api, "get_task_backend", lambda: Backend())

    response = await api.prepare_director_workspace(
        "project",
        1,
        api.DirectorWorkspacePrepareRequest(
            document_id=script.id,
            revision_id=script.current_revision_id,
            expected_source_project_revision=1,
            update_scope={"mode": "all", "scene_ids": []},
            client_mutation_id="prepare-client",
            confirm_mutation_id="confirm-client",
        ),
        user={},
    )

    assert calls == []
    assert response["data"]["stages"]["semantics"]["status"] == "running"
    assert response["data"]["stages"]["director_plan"]["status"] == "waiting"


@pytest.mark.asyncio
async def test_prepare_rejects_a_stale_handoff_preview_before_writing(
    director_context, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    calls = []

    class Query:
        async def fetchone(self):
            return None

    class Database:
        async def execute(self, *_args):
            return Query()

    class SQLite:
        async def close(self):
            pass

    class Documents:
        @asynccontextmanager
        async def _db(self):
            yield Database()

        async def get(self, _document_id):
            return SimpleNamespace(
                kind="episode_script", episode_number=1,
                current_revision_id=script.current_revision_id,
                adopted_revision_id=script.current_revision_id,
            )

    class Handoff:
        project_id = "project"

        async def _project_source_revision(self, _db):
            return 12

        async def prepare(self, **_kwargs):
            calls.append("prepare")
            raise AssertionError("stale previews must not create a handoff")

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def load_services(_ctx):
        return SQLite(), Documents(), Handoff()

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "_load_handoff_services", load_services)

    with pytest.raises(HTTPException) as error:
        await api.prepare_director_workspace(
            "project", 1,
            api.DirectorWorkspacePrepareRequest(
                document_id=script.id,
                revision_id=script.current_revision_id,
                expected_source_project_revision=11,
                update_scope={"mode": "all", "scene_ids": []},
                client_mutation_id="stale-preview",
                confirm_mutation_id="stale-preview:confirm",
            ),
            user={},
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DIRECTOR_HANDOFF_PREVIEW_STALE"
    assert calls == []


@pytest.mark.asyncio
async def test_handoff_preview_returns_baseline_and_scene_ids_without_dispatch(
    director_context, monkeypatch
):
    from contextlib import asynccontextmanager

    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    calls = []

    class Query:
        async def fetchone(self):
            return None

    class Database:
        async def execute(self, *_args):
            return Query()

    class SQLite:
        async def close(self):
            pass

    class Documents:
        @asynccontextmanager
        async def _db(self):
            yield Database()

        async def get(self, _document_id):
            return script

    class Sources:
        async def list_sources(self):
            return []

    class Handoff:
        sources = Sources()

        async def _project_source_revision(self, _db):
            return 4

        def _diff(self, old, new, *, old_scene_ids, new_scene_ids):
            calls.append((old, new, old_scene_ids, new_scene_ids))
            return {
                "text": ["added"], "scenes": [], "dialogue": [],
                "available_scene_ids": ["scene-stable"],
                "available_scenes": [{"id": "scene-stable", "heading": "仓库", "location": "仓库"}],
                "needs_reparse": False,
            }

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def load_services(_ctx):
        return SQLite(), Documents(), Handoff()

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "_load_handoff_services", load_services)

    response = await api.preview_director_workspace_handoff(
        "project", 1,
        api.DirectorWorkspaceHandoffPreviewRequest(
            document_id=script.id, revision_id=script.current_revision_id
        ),
        user={},
    )

    data = response["data"]
    assert data["expected_source_project_revision"] == 4
    assert data["current_source"] is None
    assert data["available_scene_ids"] == ["scene-stable"]
    assert data["scope_options"]["all"] is True
    assert calls == [("", script.revision.markdown, None, [item.id for item in script.revision.scene_identities])]


@pytest.mark.asyncio
async def test_prepare_passes_explicit_selected_scope_and_returns_bound_handoff_receipt(
    director_context, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api

    ctx, script = director_context
    calls = []
    scope = {"mode": "selected", "scene_ids": ["scene-b", "scene-a"]}
    selected_scope = {"mode": "selected", "scene_ids": ["scene-a", "scene-b"]}

    class Query:
        async def fetchone(self):
            return None

    class Database:
        async def execute(self, *_args):
            return Query()

    class SQLite:
        async def close(self):
            pass

    class Documents:
        @asynccontextmanager
        async def _db(self):
            yield Database()

        async def get(self, _document_id):
            return SimpleNamespace(
                kind="episode_script", episode_number=1,
                current_revision_id=script.current_revision_id,
                adopted_revision_id=script.current_revision_id,
            )

    class Handoff:
        project_id = "project"

        async def _project_source_revision(self, _db):
            return 7

        async def prepare(self, **kwargs):
            requested_scope = kwargs["update_scope"]
            normalized_scope = {
                **requested_scope,
                "scene_ids": sorted(requested_scope["scene_ids"]),
            }
            calls.append(("prepare", normalized_scope))
            return {
                "id": "handoff-selected", "status": "prepared",
                "expected_source_project_revision": 7,
                "snapshot": {"update_scope": selected_scope},
            }

        async def confirm(self, handoff_id, *, expected_source_project_revision, client_mutation_id):
            calls.append(("confirm", handoff_id, expected_source_project_revision, client_mutation_id))
            return {
                "id": handoff_id, "status": "completed", "source_revision": 9,
                "source_hash": "source-hash", "snapshot": {"update_scope": selected_scope},
                "task_result": {"semantic_revision_id": "semantic-candidate"},
            }

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def strict_context(*_args, **_kwargs):
        return (), {}

    async def load_services(_ctx):
        return SQLite(), Documents(), Handoff()

    async def serialize(*_args, **_kwargs):
        return {"stages": {"director_plan": {"status": "waiting"}}}

    class SemanticStore:
        def __init__(self, _root):
            pass

        def load_active(self, _episode):
            return None

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "strict_handoff_context", strict_context)
    monkeypatch.setattr(api, "_load_handoff_services", load_services)
    monkeypatch.setattr(api, "_serialize_workspace", serialize)
    monkeypatch.setattr(api, "ScreenplaySemanticStore", SemanticStore)

    response = await api.prepare_director_workspace(
        "project", 1,
        api.DirectorWorkspacePrepareRequest(
            document_id=script.id,
            revision_id=script.current_revision_id,
            expected_source_project_revision=7,
            update_scope=scope,
            client_mutation_id="selected-prepare",
            confirm_mutation_id="selected-prepare:confirm",
        ),
        user={},
    )

    assert calls == [
        ("prepare", selected_scope),
        ("confirm", "handoff-selected", 7, "selected-prepare:confirm"),
    ]
    assert response["handoff"]["id"] == "handoff-selected"
    assert response["preparation"]["handoff_id"] == "handoff-selected"
    assert response["preparation"]["source_revision"] == 9
    assert response["preparation"]["status"] == "needs_review"
    assert response["preparation"]["director_task_scope"] is None


def test_none_handoff_receipt_is_explicitly_completed_without_downstream_dispatch():
    from novelvideo.api.routes.director_workspace import _handoff_prepare_receipt

    receipt = _handoff_prepare_receipt(
        {"id": "handoff-none", "status": "completed", "task_result": {"skipped": True}},
        {"mode": "none", "scene_ids": []},
        semantic=None,
        director_task=None,
    )

    assert receipt["status"] == "completed"
    assert receipt["downstream_dispatch"] == "skipped"
    assert receipt["semantic_task_scope"] is None
    assert receipt["director_task_scope"] is None


@pytest.mark.asyncio
async def test_semantics_activate_and_continue_are_explicit_and_source_bound(
    director_context, monkeypatch
):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    source = SimpleNamespace(source_revision=5, content_hash="source-hash")
    revision = SimpleNamespace(
        revision_id="semantic-candidate",
        source_revision=5,
        source_hash="source-hash",
        model_dump=lambda **_kwargs: {"revision_id": "semantic-candidate", "status": "active"},
    )
    calls = []
    task = SimpleNamespace(task_state=SimpleNamespace(task_id="director-task"))

    class SemanticStore:
        def __init__(self, _root):
            pass

        def activate(self, episode, revision_id, *, expected_source_revision):
            calls.append(("activate", episode, revision_id, expected_source_revision))
            return revision

        def load_active(self, episode):
            assert episode == 1
            return revision

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def current_source(_ctx, episode):
        assert episode == 1
        return source

    async def enqueue(ctx_arg, episode, semantic):
        calls.append(("continue", ctx_arg, episode, semantic.revision_id))
        return task

    async def workspace(_ctx, episode):
        assert episode == 1
        return {"stages": {"director_plan": {"status": "queued"}}}

    monkeypatch.setattr(api, "ScreenplaySemanticStore", SemanticStore)
    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "_current_episode_source", current_source)
    monkeypatch.setattr(api, "_enqueue_director_plan", enqueue)
    monkeypatch.setattr(api, "_get_workspace", workspace)

    activated = await api.activate_director_workspace_semantics(
        "project", 1, revision.revision_id, user={}
    )
    assert activated["data"]["revision_id"] == revision.revision_id

    continued = await api.continue_director_workspace_preparation(
        "project",
        1,
        api.DirectorWorkspaceContinueRequest(
            semantic_revision_id=revision.revision_id
        ),
        user={},
    )
    assert continued["data"]["stages"]["director_plan"]["task_id"] == "director-task"
    assert calls == [
        ("activate", 1, revision.revision_id, 5),
        ("continue", ctx, 1, revision.revision_id),
    ]

    with pytest.raises(HTTPException) as error:
        await api.continue_director_workspace_preparation(
            "project",
            1,
            api.DirectorWorkspaceContinueRequest(semantic_revision_id="other"),
            user={},
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DIRECTOR_WORKSPACE_CURRENT_SEMANTICS_REQUIRED"


@pytest.mark.asyncio
async def test_director_enqueue_pins_semantic_revision_in_scope_and_payload(monkeypatch):
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api

    ctx = SimpleNamespace(project_id="project-1", state_dir="state", output_dir="output")
    semantic = SimpleNamespace(source_revision=7, revision_id="semantic-a")
    calls = {}

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(task_state=SimpleNamespace(task_id="task-a"))

    monkeypatch.setattr(api, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(api, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(api, "admit_registered_episode_stage", lambda *_args, **_kwargs: None)

    task = await api._enqueue_director_plan(ctx, 1, semantic)

    assert task.task_state.task_id == "task-a"
    assert calls["scope"] == "revision:7:semantic:semantic-a"
    assert calls["payload"]["semantic_revision_id"] == "semantic-a"


@pytest.mark.asyncio
async def test_retired_production_requests_do_not_block_heartbeat_or_enqueue(
    director_context, monkeypatch
):
    import asyncio
    import threading

    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    lookup_started = threading.Event()
    release_lookup = threading.Event()
    enqueue_calls = 0
    ticks = 0

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            lookup_started.set()
            assert release_lookup.wait(timeout=10)
            return None

    class Backend:
        async def enqueue_project_task(self, *_args, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            pytest.fail("retired production endpoint must not enqueue")

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def heartbeat():
        nonlocal ticks
        while not release_lookup.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(api, "get_task_backend", lambda: Backend())

    def request(client_mutation_id):
        return api.start_director_workspace_production(
            "project",
            1,
            api.DirectorWorkspaceProductionRequest(
                revision_id="retired-revision",
                group_ids=["group-1"],
                client_mutation_id=client_mutation_id,
            ),
            user={},
        )

    first = asyncio.create_task(request("mutation-1"))
    await asyncio.wait_for(asyncio.to_thread(lookup_started.wait, 10), timeout=10)
    second = asyncio.create_task(request("mutation-2"))
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.05)
        assert ticks >= 3
        release_lookup.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True), timeout=10
        )
        for result in results:
            assert isinstance(result, HTTPException)
            assert result.status_code == 410
            assert result.detail["code"] == "DIRECTOR_WORKSPACE_PRODUCTION_RETIRED"
        assert enqueue_calls == 0
    finally:
        release_lookup.set()
        if not heartbeat_task.done():
            heartbeat_task.cancel()
