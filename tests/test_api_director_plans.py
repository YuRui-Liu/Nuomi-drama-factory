from __future__ import annotations

from types import SimpleNamespace
import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _revision(
    revision_id: str = "revision-1",
    *,
    status: str = "review_required",
    passed: bool = True,
):
    payload = {
        "revision_id": revision_id,
        "episode": 2,
        "status": status,
        "validation_report": {"passed": passed, "issues": []},
        "groups": [],
    }
    return SimpleNamespace(
        revision_id=revision_id,
        status=status,
        validation_report=SimpleNamespace(passed=passed),
        model_dump=lambda **_kwargs: payload,
    )


def _review_revision(
    revision_id="revision-2",
    *,
    parent_revision_id="revision-1",
    status="review_required",
    migration_items=(),
):
    from datetime import datetime, timezone

    from novelvideo.director_plan.models import (
        AssetMigrationReport,
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotPlan,
        ValidationReport,
    )

    return DirectorPlanRevision(
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        episode=2,
        status=status,
        source_script_hash="hash",
        director_model="model",
        prompt_version="v2",
        project_style_snapshot_id="style",
        groups=(
            NarrativeGroupPlan(
                id="ng-1",
                ordinal=1,
                source_span_ids=("s1", "s2"),
                scene_anchor="workshop",
                time_anchor="day",
                objective="repair radio",
                visible_turn="radio lights up",
                relation_to_previous="single",
                shots=(
                    ShotPlan(
                        id="shot-1",
                        source_span_ids=("s1",),
                        subject="A Yuan",
                        action="turns the dial",
                        visible_start_state="radio dark",
                        visible_end_state="radio lit",
                        duration_seconds=4,
                    ),
                    ShotPlan(
                        id="shot-2",
                        source_span_ids=("s2",),
                        subject="A Yuan",
                        action="looks up",
                        visible_start_state="looking down",
                        visible_end_state="looking up",
                        duration_seconds=3,
                    ),
                ),
            ),
        ),
        validation_report=ValidationReport(passed=True),
        migration_report=AssetMigrationReport(items=tuple(migration_items)),
        created_at=datetime.now(timezone.utc),
    )


def _migration_item(
    item_id,
    *,
    asset_id,
    shot_id,
    confidence="high",
    decision="review",
):
    return {
        "item_id": item_id,
        "old_asset_id": asset_id,
        "old_asset_path": f"assets/{asset_id}.png",
        "old_asset_kind": "image",
        "old_shot_id": f"old-{shot_id}",
        "new_shot_id": shot_id,
        "suggested_shot_id": shot_id,
        "score": 1.0 if confidence == "high" else 0.1,
        "confidence": confidence,
        "reuse_mode": "reuse",
        "suggested_decision": decision,
        "decision": decision,
        "manual_decision": None,
        "conflict": False,
        "evidence": {
            "source_overlap": 1.0,
            "subject_overlap": 1.0,
            "scene_match": 1.0,
            "action_similarity": 1.0,
            "shot_semantic_similarity": 1.0,
        },
    }


def _client(monkeypatch, *, store=None, source_revision=7, role="editor"):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import director_plans

    calls = []
    ctx = SimpleNamespace(
        project_id="project-1", output_dir="project-dir", state_dir="/unmanaged/test-project",
    )

    async def resolve(project, _user, *, required_role):
        calls.append((project, required_role))
        ranks = {"viewer": 1, "editor": 2}
        if ranks[role] < ranks[required_role]:
            raise HTTPException(status_code=403, detail="Insufficient project role")
        return SimpleNamespace(ctx=ctx)

    monkeypatch.setattr(director_plans, "resolve_project_scope", resolve)
    monkeypatch.setattr(
        director_plans,
        "_resolve_source_revision",
        lambda _ctx, _episode: _async(source_revision),
    )
    if store is not None:
        if hasattr(store, "__dict__") and not hasattr(store, "require_current"):
            monkeypatch.setattr(store, "require_current", lambda _revision: None, raising=False)
        monkeypatch.setattr(
            director_plans, "_build_director_plan_store", lambda _ctx: store
        )
    app = FastAPI()
    app.include_router(director_plans.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "user_id": "user-1",
        "username": "editor",
        "scopes": ["tasks:submit"],
    }
    return TestClient(app), calls, ctx, director_plans


def test_create_director_plan_enqueues_without_running_llm(monkeypatch):
    client, calls, ctx, module = _client(monkeypatch)
    enqueued = []

    async def enqueue(ctx_arg, **kwargs):
        assert ctx_arg is ctx
        enqueued.append(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="inline",
            queue="default",
        )

    monkeypatch.setattr(
        module,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )
    monkeypatch.setattr(
        module,
        "get_task_manager",
        lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None),
    )

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans"
    )

    assert response.status_code == 202
    assert response.json()["task_id"] == "task-1"
    assert enqueued == [
        {
            "task_type": "director_plan",
            "queue_kind": "default",
            "episode": 2,
            "scope": "revision:7",
            "payload": {
                "project_id": "project-1",
                "episode": 2,
                "source_revision": 7,
            },
        }
    ]
    assert calls == [("project-1", "editor")]


def test_list_and_detail_require_viewer_and_serialize_revisions(monkeypatch):
    revision = _revision()

    class Store:
        def list(self, episode):
            assert episode == 2
            return [revision]

        def load(self, episode, revision_id):
            assert (episode, revision_id) == (2, "revision-1")
            return revision

    client, calls, *_ = _client(monkeypatch, store=Store(), role="viewer")

    listed = client.get("/api/v1/projects/project-1/episodes/2/director-plans")
    detailed = client.get(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-1"
    )

    assert listed.status_code == detailed.status_code == 200
    assert listed.json()["data"][0]["revision_id"] == "revision-1"
    assert detailed.json()["data"]["status"] == "review_required"
    assert calls == [("project-1", "viewer"), ("project-1", "viewer")]


def test_list_and_detail_redact_failed_revision_validation_issue_messages(
    monkeypatch,
):
    revision = _revision(status="failed", passed=False)
    revision.model_dump = lambda **_kwargs: {
        "revision_id": "revision-1",
        "episode": 2,
        "status": "failed",
        "validation_report": {
            "passed": False,
            "issues": [
                {
                    "code": "provider_rejected",
                    "location": "groups.0",
                    "severity": "error",
                    "message": "provider rejected sk-live-secret",
                }
            ],
        },
        "groups": [],
    }

    class Store:
        def list(self, _episode):
            return [revision]

        def load(self, _episode, _revision_id):
            return revision

    client, _calls, *_ = _client(monkeypatch, store=Store(), role="viewer")

    listed = client.get("/api/v1/projects/project-1/episodes/2/director-plans")
    detailed = client.get(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-1"
    )

    for response in (listed, detailed):
        assert response.status_code == 200
        assert "sk-live-secret" not in response.text
        data = response.json()["data"]
        revision_data = data[0] if isinstance(data, list) else data
        issue = revision_data["validation_report"]["issues"][0]
        assert issue == {
            "code": "provider_rejected",
            "location": "groups.0",
            "severity": "error",
            "message": "provider rejected [redacted]",
        }


def test_detail_missing_is_404_and_invalid_activation_is_409(monkeypatch):
    class Store:
        def load(self, _episode, _revision_id):
            raise FileNotFoundError("missing")

        def load_active_for_sync(self, _episode, _revision_id):
            return None

        def activate(self, _episode, _revision_id):
            raise ValueError("revision validation must pass before activation")

    client, _calls, *_ = _client(monkeypatch, store=Store())

    missing = client.get(
        "/api/v1/projects/project-1/episodes/2/director-plans/missing"
    )
    conflict = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-1/activate"
    )

    assert missing.status_code == 404
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "DIRECTOR_PLAN_ACTIVATION_CONFLICT"


@pytest.mark.parametrize('refresh_failure', [False, True])
def test_activation_allows_explicit_superseded_restore(monkeypatch, refresh_failure):
    restored = _revision(status="active")

    class Store:
        def load_active_for_sync(self, _episode, _revision_id):
            return None

        def activate(self, episode, revision_id):
            assert (episode, revision_id) == (2, "old-revision")
            return restored

    client, calls, *_ = _client(monkeypatch, store=Store())
    from novelvideo.api.routes import director_plans
    refreshed = []
    async def refresh(ctx, episode, revision_id):
        refreshed.append((episode, revision_id))
        if refresh_failure:
            raise ValueError('catalog changed')
        return 0
    monkeypatch.setattr(director_plans, '_refresh_plan_references', refresh)

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/old-revision/activate"
    )

    if refresh_failure:
        assert response.status_code == 409
        assert response.json()['detail']['active_revision_id'] == 'revision-1'
        assert response.json()['detail']['code'] == 'DIRECTOR_PLAN_ACTIVATED_REFERENCE_REFRESH_REQUIRED'
        return
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "active"
    assert refreshed == [(2, 'revision-1')]
    assert calls == [("project-1", "editor")]


def test_viewer_cannot_create_or_activate(monkeypatch):
    client, _calls, *_ = _client(monkeypatch, store=object(), role="viewer")

    assert client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans"
    ).status_code == 403
    assert client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/r/activate"
    ).status_code == 403


def test_atomic_requirement_edit_creates_one_child(monkeypatch):
    parent = _review_revision()
    saved = []
    store = SimpleNamespace(load=lambda *args: parent, save=saved.append)
    client, *_ = _client(monkeypatch, store=store)
    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/edits",
        json={"kind": "update_shots", "updates": [
            {"kind": "update_shot", "shot_id": "shot-1", "asset_requirements": []},
            {"kind": "update_shot", "shot_id": "shot-2", "asset_requirements": []},
        ]},
    )
    assert response.status_code == 201, response.text
    assert len(saved) == 1
    assert response.json()["data"]["parent_revision_id"] == parent.revision_id


def test_edit_creates_immutable_child_revision(monkeypatch):
    parent = _review_revision()

    class Store:
        def __init__(self):
            self.saved = []

        def load(self, episode, revision_id):
            assert (episode, revision_id) == (2, "revision-2")
            return parent

        def save(self, revision):
            self.saved.append(revision)

    store = Store()
    client, calls, *_ = _client(monkeypatch, store=store)

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/edits",
        json={
            "kind": "split_group",
            "group_id": "ng-1",
            "before_shot_id": "shot-2",
        },
    )

    assert response.status_code == 201
    child = response.json()["data"]
    assert child["parent_revision_id"] == "revision-2"
    assert child["revision_id"] != "revision-2"
    assert child["status"] == "review_required"
    assert store.saved[0].revision_id == child["revision_id"]
    assert parent.groups[0].shots[1].id == "shot-2"
    assert calls == [("project-1", "editor")]


def test_edit_conflict_redacts_credentials_from_validation_report(monkeypatch):
    parent = _review_revision()

    class Store:
        def load(self, _episode, _revision_id):
            return parent

    client, _calls, *_ = _client(monkeypatch, store=Store())

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/edits",
        json={
            "kind": "split_group",
            "group_id": "sk-live-secret",
            "before_shot_id": "shot-2",
        },
    )

    assert response.status_code == 409
    assert "sk-live-secret" not in response.text
    assert "[redacted]" in response.text


def test_comparison_and_migration_are_viewer_safe(monkeypatch):
    base = _review_revision("revision-1", parent_revision_id=None, status="superseded")
    candidate = _review_revision(
        migration_items=(
            _migration_item(
                "asset-1--shot-1",
                asset_id="asset-1",
                shot_id="shot-1",
                decision="accepted",
            ),
        )
    )

    class Store:
        def load(self, _episode, revision_id):
            return {"revision-1": base, "revision-2": candidate}[revision_id]

    client, calls, *_ = _client(monkeypatch, store=Store(), role="viewer")

    comparison = client.get(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/comparison",
        params={"base": "revision-1"},
    )
    migration = client.get(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/migration"
    )

    assert comparison.status_code == migration.status_code == 200
    assert comparison.json()["data"]["base"]["revision_id"] == "revision-1"
    assert comparison.json()["data"]["candidate"]["revision_id"] == "revision-2"
    assert migration.json()["data"]["items"][0]["item_id"] == "asset-1--shot-1"
    assert calls == [("project-1", "viewer"), ("project-1", "viewer")]


def test_migration_decision_updates_review_item_but_unmatched_is_read_only(monkeypatch):
    item = _migration_item(
        "asset-1--shot-1", asset_id="asset-1", shot_id="shot-1"
    )
    low = _migration_item(
        "asset-2--shot-2",
        asset_id="asset-2",
        shot_id="shot-2",
        confidence="low",
        decision="unmatched",
    )
    current = _review_revision(migration_items=(item, low))

    class Store:
        def __init__(self):
            self.current = current

        def load(self, _episode, _revision_id):
            return self.current

        def replace(self, revision):
            self.current = revision

    store = Store()
    client, _calls, *_ = _client(monkeypatch, store=store)

    updated = client.put(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/"
        "migration/asset-1--shot-1",
        json={"decision": "reference_only"},
    )
    immutable = client.put(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/"
        "migration/asset-2--shot-2",
        json={"decision": "accepted"},
    )
    invalid = client.put(
        "/api/v1/projects/project-1/episodes/2/director-plans/revision-2/"
        "migration/asset-1--shot-1",
        json={"decision": "unmatched"},
    )

    assert updated.status_code == 200
    assert updated.json()["data"]["decision"] == "reference_only"
    assert updated.json()["data"]["manual_decision"] == "reference_only"
    assert immutable.status_code == 409
    assert invalid.status_code == 422


def test_abandon_only_allows_unactivated_review_revisions(monkeypatch):
    class Store:
        def __init__(self):
            self.revisions = {
                "review": _review_revision("review"),
                "active": _review_revision("active", status="active"),
                "superseded": _review_revision("superseded", status="superseded"),
            }

        def load(self, _episode, revision_id):
            return self.revisions[revision_id]

        def replace(self, revision):
            self.revisions[revision.revision_id] = revision

    client, _calls, *_ = _client(monkeypatch, store=Store())

    abandoned = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/review/abandon"
    )
    active = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/active/abandon"
    )
    superseded = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/superseded/abandon"
    )

    assert abandoned.status_code == 200
    assert abandoned.json()["data"]["status"] == "abandoned"
    assert active.status_code == superseded.status_code == 409


async def _async(value):
    return value


def _external_director_groups():
    return [
        {
            "id": "group-1",
            "ordinal": 1,
            "source_span_ids": ["span-1"],
            "dramatic_beat_ids": ["beat-1"],
            "scene_anchor": "workshop",
            "time_anchor": "night",
            "objective": "Find the lost record",
            "visible_turn": "The lamp reveals a mark",
            "relation_to_previous": "single",
            "shots": [
                {
                    "id": "shot-1",
                    "source_span_ids": ["span-1"],
                    "dramatic_beat_ids": ["beat-1"],
                    "subject": "The apprentice",
                    "action": "turns the lamp toward the wall",
                    "visible_start_state": "the mark is hidden",
                    "visible_end_state": "the mark catches the light",
                    "duration_seconds": 5,
                    "intent": {
                        "narrative_purpose": "Reveal the clue",
                        "audience_attention": "The wall behind the lamp",
                        "emotional_effect": "Curiosity replaces doubt",
                        "continuity_strategy": "Hold the same side of the workbench",
                    },
                    "cinematography": {
                        "source": "director_plan",
                        "source_ids": ["span-1"],
                        "axis": "along the workbench",
                        "camera_side": "south",
                        "screen_direction": "lamp moves left to right",
                        "lights": [
                            {
                                "light_id": "lamp",
                                "source_type": "practical",
                                "world_position": "on the workbench",
                                "direction": "toward the wall",
                                "color_temperature": "warm",
                                "relative_intensity": "key",
                                "attachment": "fixed",
                                "motivation": "the apprentice's oil lamp",
                            }
                        ],
                        "key_light_id": "lamp",
                        "shadow_direction": "behind the mark",
                        "exposure_priority": "the mark",
                        "transition_intent": "hold for the reveal",
                    },
                }
            ],
        }
    ]


def _external_director_request(**overrides):
    import hashlib

    payload = {
        "expected_source_revision": 7,
        "expected_source_hash": "sha256:" + hashlib.sha256(b"current episode source").hexdigest(),
        "semantic_revision_id": "semantic-7",
        "client_mutation_id": "codex-director-seed-1",
        "groups": _external_director_groups(),
    }
    payload.update(overrides)
    return payload


def _external_director_environment(monkeypatch, tmp_path, *, source_revision=7):
    import hashlib

    from novelvideo.api.routes import director_plans
    from novelvideo.director_plan.models import StyleProjections, StyleSnapshot

    source_hash = "sha256:" + hashlib.sha256(b"current episode source").hexdigest()
    source = SimpleNamespace(
        episode_number=2,
        source_revision=source_revision,
        content_hash=source_hash,
    )
    semantic = SimpleNamespace(
        revision_id="semantic-7",
        source_revision=source_revision,
        source_hash=source_hash,
        status="active",
        scenes=(SimpleNamespace(
            id="scene-1",
            location="workshop",
            heading="INT. WORKSHOP",
            time_of_day="night",
            blocks=(SimpleNamespace(
                id="span-1",
                text="The apprentice turns the lamp toward the wall.",
                kind="action",
            ),),
        ),),
        beats=(SimpleNamespace(id="beat-1"),),
    )

    class SourceRepository:
        async def list_sources(self):
            return [source]

    class SemanticRepository:
        def load_active(self, episode):
            assert episode == 2
            return semantic

        def load(self, episode, revision_id):
            return semantic if episode == 2 and revision_id == semantic.revision_id else None

    monkeypatch.setattr(
        director_plans,
        "make_sqlite_store_for_context",
        lambda _ctx: _async(SimpleNamespace()),
    )
    monkeypatch.setattr(director_plans, "EpisodeSourceStore", lambda _db: SourceRepository())
    monkeypatch.setattr(
        director_plans,
        "ScreenplaySemanticStore",
        lambda _root: SemanticRepository(),
        raising=False,
    )
    monkeypatch.setattr(
        director_plans,
        "_build_director_plan_store",
        lambda _ctx: director_plans.DirectorPlanStore(tmp_path),
    )
    snapshot = StyleSnapshot(
        snapshot_id="style-current",
        style_id="period-drama",
        style_version="1",
        catalog_hash="catalog-hash",
        style_hash="style-hash",
        projections=StyleProjections(
            director="cinematic period drama",
            image="painted period drama",
            video="grounded movement",
            panel_tag="period",
        ),
    )
    from novelvideo.director_plan import style_resolution

    monkeypatch.setattr(
        style_resolution, "resolve_project_style_snapshot", lambda _ctx: snapshot
    )
    return director_plans, source_hash


def test_external_director_draft_is_saved_for_review_without_enqueueing(
    monkeypatch, tmp_path
):
    from novelvideo.director_plan.store import DirectorPlanStore

    module, source_hash = _external_director_environment(monkeypatch, tmp_path)
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(
        module,
        "get_task_backend",
        lambda: pytest.fail("external draft import must not enqueue a task"),
    )

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/external-drafts",
        json=_external_director_request(),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["replayed"] is False
    assert data["revision"]["status"] == "review_required"
    assert data["revision"]["source_script_hash"] == source_hash
    assert data["revision"]["semantic_revision_id"] == "semantic-7"
    assert data["revision"]["director_model"] == "external:codex"
    assert data["revision"]["prompt_version"] == "director-plan-v3-keyframe-instant"
    assert data["revision"]["edit_source"] == "human"
    assert data["revision"]["project_style_snapshot_id"] == "style-current"
    assert data["revision"]["validation_report"]["passed"] is True
    assert DirectorPlanStore(tmp_path).load(2, data["revision"]["revision_id"]).status == "review_required"
    assert DirectorPlanStore(tmp_path).load_active(2) is None


def test_external_director_draft_uses_stable_idempotency_and_detects_key_reuse(
    monkeypatch, tmp_path
):
    _external_director_environment(monkeypatch, tmp_path)
    client, *_ = _client(monkeypatch)
    path = "/api/v1/projects/project-1/episodes/2/director-plans/external-drafts"

    first = client.post(path, json=_external_director_request())
    replay = client.post(path, json=_external_director_request())
    changed = _external_director_groups()
    changed[0]["objective"] = "A different objective"
    conflict = client.post(
        path,
        json=_external_director_request(groups=changed),
    )

    assert first.status_code == replay.status_code == 201
    assert first.json()["data"]["revision"]["revision_id"] == replay.json()["data"]["revision"]["revision_id"]
    assert replay.json()["data"]["replayed"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "DIRECTOR_PLAN_EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"


def test_external_director_draft_rejects_stale_source_and_caller_owned_revision_flags(
    monkeypatch, tmp_path
):
    _external_director_environment(monkeypatch, tmp_path)
    client, *_ = _client(monkeypatch)
    path = "/api/v1/projects/project-1/episodes/2/director-plans/external-drafts"

    stale = client.post(
        path,
        json=_external_director_request(expected_source_revision=6),
    )
    forged = client.post(
        path,
        json=_external_director_request(status="active", validation_report={"passed": True}),
    )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "DIRECTOR_PLAN_SOURCE_CONFLICT"
    assert forged.status_code == 422
