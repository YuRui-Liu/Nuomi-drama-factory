from __future__ import annotations

import pytest


def _node(node_type: str, operation: str) -> dict:
    data = {"operation": operation, "referenceOnly": True}
    if node_type == "directorWorkspaceNode":
        data.update(episodeNumber=1, directorRevisionId="revision-1")
    return {"id": "target", "type": node_type, "position": {"x": 0, "y": 0}, "data": data}


def _agent_marker(canvas: dict, node: dict, operation: str) -> dict:
    from novelvideo.freezone.orchestration.models import (
        dependency_edge_fingerprint,
        fingerprint,
        media_projection,
        result_projection,
    )

    chain = [{"delegation_id": "delegation-1", "max_node_runs": 1}]
    return {
        "target_node_id": node["id"],
        "operation": operation,
        "project_id": "project-1",
        "canvas_id": canvas["canvas_id"],
        "actor_id": "user-1",
        "delegation_id": "delegation-1",
        "root_delegation_id": "delegation-1",
        "authorization_id": "authorization-1",
        "allowed_node_ids": [node["id"]],
        "allowed_node_operations": [{"node_id": node["id"], "operation": operation}],
        "read_node_ids": [],
        "max_node_runs": 1,
        "root_max_node_runs": 1,
        "node_run_budget_chain": chain,
        "target_result_fingerprint": fingerprint(result_projection(node)),
        "target_media_fingerprint": fingerprint(media_projection(node)),
        "dependency_edge_fingerprint": dependency_edge_fingerprint(canvas, node["id"]),
        "source_output_fingerprints": {},
        "frozen_inputs": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("node_type", "operation"), [
    ("ideationNode", "generate"),
    ("screenplayNode", "plan"),
    ("directorWorkspaceNode", "prepare"),
    ("characterVoiceNode", "design"),
    ("audioNode", "prepare_dubbing"),
    ("audioNode", "generate_dubbing"),
    ("audioNode", "group_dubbing"),
    ("directorWorkspaceNode", "save_blocking"),
    ("directorWorkspaceNode", "compile_blocking"),
    ("directorWorkspaceNode", "edit_shot"),
])
async def test_reference_only_specialized_submissions_consume_node_run_budget(
    tmp_path, monkeypatch, node_type, operation,
):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.adapters import NodeAdapter, dubbing, production, professional_edits, voice
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    create_canvas(tmp_path, "canvas", project_id="project-1", name="budget", actor_id="user-1")
    target = _node(node_type, operation)
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[
        {"op": "add_node", "node": target},
    ])
    canvas = read_canvas(tmp_path, "canvas")
    target = canvas["nodes"][0]

    async def lost_reply(*_args, **_kwargs):
        raise TimeoutError("accepted operation response was lost")

    for module in (production, voice, dubbing, professional_edits):
        monkeypatch.setattr(module, "submit", lost_reply)

    store = RunStore(tmp_path)
    run = store.create(
        canvas,
        target=target["id"],
        target_only=True,
        agent_execution=_agent_marker(canvas, target, operation),
        request_id=f"run-{node_type}-{operation}",
        project_id="project-1",
        settings={"concurrency": 1},
        actor={"id": "user-1"},
    )

    async def authorized(*_args):
        return None

    adapter = NodeAdapter(tmp_path)
    scheduler = Scheduler(store, adapter, authorize_submission=authorized)
    result = await scheduler.tick(run["id"])

    assert result["attempts"][target["id"]][-1]["status"] == "submission_unknown"
    assert store.node_run_count("delegation-1") == 1


def test_reference_only_does_not_charge_pure_voice_preparation(tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter

    run = {"snapshot": {"nodes": [
        _node("characterVoiceNode", "prepare_design"),
    ]}}
    attempt = {"node_id": "target"}

    assert NodeAdapter(tmp_path).requires_external_submission(run, attempt) is False

