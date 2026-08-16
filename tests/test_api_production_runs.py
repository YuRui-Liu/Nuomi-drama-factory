from __future__ import annotations

from pathlib import Path
import json

import pytest
from fastapi import HTTPException

from novelvideo.media_capabilities.production.models import ProductionNodeStatus
from novelvideo.media_capabilities.production.store import ProductionStore
from novelvideo.project_context import ProjectContext


def _ctx(tmp_path: Path, *, project_id: str = "proj_123", role: str = "editor"):
    return ProjectContext(
        project_id=project_id,
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="alice",
        requester_user_id="user_editor",
        requester_username="bob",
        requester_principals=(("user", "user_editor"),),
        effective_role=role,
        home_node_id="local",
        output_dir=tmp_path / "output" / "alice" / "demo",
        state_dir=tmp_path / "state" / "alice" / "demo",
        runtime_dir=tmp_path / "runtime" / "alice" / "demo",
        is_home_node=True,
    )


def _request(*, high_cost: bool = False):
    from novelvideo.api.routes.production_runs import ProductionRunRequest

    return ProductionRunRequest.model_validate(
        {
            "node_types": {
                "image": {
                    "capability": "image.single",
                    "implementation": "image-primary",
                    "workflow_version": {"id": "image", "version": 1},
                    "unit_cost": 0.5,
                },
                "video": {
                    "capability": "video.i2va",
                    "implementation": "video-primary",
                    "workflow_version": {"id": "video", "version": 2},
                    "unit_cost": 2.0,
                },
            },
            "scope": {
                "nodes": [
                    {"id": "image-1", "node_type": "image", "inputs": {"prompt": "a"}},
                    {
                        "id": "video-1",
                        "node_type": "video",
                        "depends_on": ["image-1"],
                        "inputs": {"duration": 5},
                    },
                ]
            },
            "snapshot": {"budget": 5.0},
            "existing_artifacts": {},
            "high_cost": high_cost,
        }
    )


def _confirmed(body):
    from novelvideo.api.routes import production_runs

    body.snapshot_token = production_runs._snapshot_token(
        body, production_runs._plan(body)
    )
    return body


@pytest.mark.asyncio
async def test_preview_is_viewer_read_only_and_returns_enveloped_plan(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="viewer")

    async def resolve(**kwargs):
        assert kwargs["project_id"] == "proj_123"
        assert kwargs["required_role"] == "viewer"
        return ctx

    monkeypatch.setattr(production_runs, "resolve_project_context", resolve)

    response = await production_runs.preview_production_run(
        "proj_123", _request(), user={"username": "bob"}
    )

    assert response["ok"] is True
    assert response["data"]["counts"] == {
        "create": 2,
        "reuse": 0,
        "invalidate": 0,
        "skip": 0,
    }
    assert response["data"]["estimated_cost"] == pytest.approx(2.5)
    assert len(response["data"]["snapshot_token"]) == 64


@pytest.mark.asyncio
async def test_preview_maps_invalid_dag_to_422(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="viewer")
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )
    body = _request()
    body.scope["nodes"][0]["depends_on"] = ["video-1"]

    with pytest.raises(HTTPException) as exc:
        await production_runs.preview_production_run(
            "proj_123", body, user={"username": "bob"}
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "production_plan_invalid"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_create_requires_editor_records_operator_and_persists_dag(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="editor")
    store = ProductionStore(tmp_path / "production.db")

    async def resolve(**kwargs):
        assert kwargs["required_role"] == "editor"
        return ctx

    monkeypatch.setattr(production_runs, "resolve_project_context", resolve)

    response = await production_runs.create_production_run(
        "proj_123",
        _confirmed(_request()),
        user={"username": "bob"},
        store=store,
    )

    assert response["ok"] is True
    assert response["data"]["run"]["project_id"] == "proj_123"
    assert response["data"]["run"]["config_snapshot"]["operator"] == {
        "user_id": "user_editor",
        "username": "bob",
    }
    assert len(response["data"]["nodes"]) == 2
    assert len(response["data"]["edges"]) == 1
    assert {node["status"] for node in response["data"]["nodes"]} == {
        "ready",
        "pending",
    }


@pytest.mark.asyncio
async def test_high_cost_create_requires_project_admin_or_owner(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="editor")
    store = ProductionStore(tmp_path / "production.db")
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )

    with pytest.raises(HTTPException) as exc:
        await production_runs.create_production_run(
            "proj_123",
            _confirmed(_request(high_cost=True)),
            user={"username": "bob"},
            store=store,
        )

    assert exc.value.status_code == 403
    assert store.list_running_runs() == []


@pytest.mark.asyncio
async def test_create_requires_matching_preview_snapshot_token(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="editor")
    store = ProductionStore(tmp_path / "production.db")
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )

    with pytest.raises(HTTPException) as missing:
        await production_runs.create_production_run(
            "proj_123", _request(), user={"username": "bob"}, store=store
        )
    assert missing.value.status_code == 409

    changed = _confirmed(_request())
    changed.scope["nodes"][0]["inputs"]["prompt"] = "changed after preview"
    with pytest.raises(HTTPException) as stale:
        await production_runs.create_production_run(
            "proj_123", changed, user={"username": "bob"}, store=store
        )
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_create_response_and_persistence_redact_sensitive_values(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="editor")
    store = ProductionStore(tmp_path / "production.db")
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )
    body = _request()
    body.scope["nodes"][0]["inputs"].update(
        {
            "credential_ref": "vault://secret",
            "Authorization": "Bearer private",
            "Cookie": "session=private",
            "signed_url": "https://cdn.example/a.png?signature=private",
        }
    )
    body = _confirmed(body)

    response = await production_runs.create_production_run(
        "proj_123", body, user={"username": "bob"}, store=store
    )

    serialized = json.dumps(response, ensure_ascii=False).lower()
    for secret in ("vault://secret", "bearer private", "session=private", "signature=private"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_list_is_paginated_and_project_isolated(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="viewer")
    store = ProductionStore(tmp_path / "production.db")
    first = store.create_run("proj_123", {"name": "first"})
    second = store.create_run("proj_123", {"name": "second"})
    store.create_run("proj_other", {"name": "foreign"})
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )

    response = await production_runs.list_production_runs(
        "proj_123",
        page=1,
        page_size=1,
        user={"username": "bob"},
        store=store,
    )

    assert response == {
        "ok": True,
        "data": {
            "items": [production_runs._serialize(second)],
            "page": 1,
            "page_size": 1,
            "total": 2,
            "pages": 2,
        },
    }
    assert first.id != second.id


@pytest.mark.asyncio
async def test_detail_hides_foreign_project_run_as_404(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="viewer")
    store = ProductionStore(tmp_path / "production.db")
    foreign = store.create_run("proj_other", {})
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )

    with pytest.raises(HTTPException) as exc:
        await production_runs.get_production_run(
            "proj_123", foreign.id, user={"username": "bob"}, store=store
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_cancel_and_retry_node_enforce_lifecycle(tmp_path, monkeypatch):
    from novelvideo.api.routes import production_runs

    ctx = _ctx(tmp_path, role="editor")
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("proj_123", {})
    node = store.add_node(run.id, "video", "video-1", {})
    store.recompute_ready(run.id)
    monkeypatch.setattr(
        production_runs,
        "resolve_project_context",
        lambda **kwargs: _async_value(ctx),
    )

    paused = await production_runs.pause_production_run(
        "proj_123", run.id, user={"username": "bob"}, store=store
    )
    assert paused["data"]["status"] == "paused"
    resumed = await production_runs.resume_production_run(
        "proj_123", run.id, user={"username": "bob"}, store=store
    )
    assert resumed["data"]["status"] == "running"

    with pytest.raises(HTTPException) as exc:
        await production_runs.retry_production_node(
            "proj_123",
            run.id,
            production_runs.RetryNodeRequest(node_id=node.id),
            user={"username": "bob"},
            store=store,
        )
    assert exc.value.status_code == 409

    store.transition_node(node.id, ProductionNodeStatus.QUEUED)
    store.transition_node(node.id, ProductionNodeStatus.RUNNING)
    store.transition_node(node.id, ProductionNodeStatus.FAILED)
    retried = await production_runs.retry_production_node(
        "proj_123",
        run.id,
        production_runs.RetryNodeRequest(node_id=node.id),
        user={"username": "bob"},
        store=store,
    )
    assert retried["data"]["status"] == "ready"

    cancelled = await production_runs.cancel_production_run(
        "proj_123", run.id, user={"username": "bob"}, store=store
    )
    assert cancelled["data"]["status"] == "cancelling"


def test_production_routes_are_mounted_under_v1_router():
    from novelvideo.api.app import create_app

    paths = set(create_app().openapi()["paths"])

    assert "/api/v1/projects/{project}/production/runs/preview" in paths
    assert "/api/v1/projects/{project}/production/runs" in paths
    assert "/api/v1/projects/{project}/production/runs/{run_id}/retry-node" in paths
