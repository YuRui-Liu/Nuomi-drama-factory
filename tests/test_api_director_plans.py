from __future__ import annotations

from types import SimpleNamespace

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
        "score": 1.0 if confidence == "high" else 0.1,
        "confidence": confidence,
        "reuse_mode": "formal",
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
    ctx = SimpleNamespace(project_id="project-1", output_dir="project-dir")

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


def test_activation_allows_explicit_superseded_restore(monkeypatch):
    restored = _revision(status="active")

    class Store:
        def activate(self, episode, revision_id):
            assert (episode, revision_id) == (2, "old-revision")
            return restored

    client, calls, *_ = _client(monkeypatch, store=Store())

    response = client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/old-revision/activate"
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "active"
    assert calls == [("project-1", "editor")]


def test_viewer_cannot_create_or_activate(monkeypatch):
    client, _calls, *_ = _client(monkeypatch, store=object(), role="viewer")

    assert client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans"
    ).status_code == 403
    assert client.post(
        "/api/v1/projects/project-1/episodes/2/director-plans/r/activate"
    ).status_code == 403


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
