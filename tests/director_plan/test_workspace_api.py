from __future__ import annotations

import pytest


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

    ctx = SimpleNamespace(project_id="project-1")
    semantic = SimpleNamespace(source_revision=7, revision_id="semantic-a")
    calls = {}

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(task_state=SimpleNamespace(task_id="task-a"))

    monkeypatch.setattr(api, "get_task_backend", lambda: Backend())

    task = await api._enqueue_director_plan(ctx, 1, semantic)

    assert task.task_state.task_id == "task-a"
    assert calls["scope"] == "revision:7:semantic:semantic-a"
    assert calls["payload"]["semantic_revision_id"] == "semantic-a"


@pytest.mark.asyncio
async def test_concurrent_production_enqueue_requests_do_not_block_heartbeat(
    director_context, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    active = SimpleNamespace(
        revision_id="active-director-revision",
        status="active",
        groups=(SimpleNamespace(id="group-1"),),
    )
    tasks = []
    first_enqueue_started = asyncio.Event()
    release_first_enqueue = asyncio.Event()
    backend_calls = 0
    ticks = 0

    class PlanStore:
        def __init__(self, _root):
            pass

        def load_active(self, _episode):
            return active

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

        def list_tasks_for_project(self, _ctx):
            return list(tasks)

    class Backend:
        async def enqueue_project_task(self, _ctx, *, task_type, episode, scope, **_kwargs):
            nonlocal backend_calls
            backend_calls += 1
            task = SimpleNamespace(
                task_id=f"task-{backend_calls}",
                task_type=task_type,
                episode=episode,
                status="queued",
                scope=scope,
                queue_kind="world",
                metadata={"backend": "inline"},
            )
            tasks.append(task)
            if backend_calls == 1:
                first_enqueue_started.set()
                await release_first_enqueue.wait()
            return SimpleNamespace(task_state=task, backend="inline", queue="world")

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    async def heartbeat():
        nonlocal ticks
        while not release_first_enqueue.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    monkeypatch.setattr(api, "DirectorPlanStore", PlanStore)
    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(api, "get_task_backend", lambda: Backend())

    def request(client_mutation_id):
        return api.start_director_workspace_production(
            "project",
            1,
            api.DirectorWorkspaceProductionRequest(
                revision_id=active.revision_id,
                group_ids=["group-1"],
                client_mutation_id=client_mutation_id,
            ),
            user={},
        )

    first = asyncio.create_task(request("mutation-1"))
    await asyncio.wait_for(first_enqueue_started.wait(), timeout=10)
    second = asyncio.create_task(request("mutation-2"))
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.05)
        assert ticks >= 3
        release_first_enqueue.set()
        response = await asyncio.wait_for(first, timeout=10)
        assert response["data"]["task_id"] == "task-1"
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(second, timeout=10)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "DIRECTOR_WORKSPACE_PRODUCTION_ALREADY_RUNNING"
    finally:
        release_first_enqueue.set()
        if not heartbeat_task.done():
            heartbeat_task.cancel()
