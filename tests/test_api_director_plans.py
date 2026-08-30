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


async def _async(value):
    return value
