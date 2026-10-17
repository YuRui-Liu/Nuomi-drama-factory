import json

import pytest

from novelvideo.creation import agent_model, node_execution
from novelvideo.freezone.canvas_store import canvas_path, read_canvas
from novelvideo.freezone.orchestration.adapters import NodeAdapter
from novelvideo.freezone.orchestration.store import RunStore
from .test_agent_profiles import configuration, save
from .test_agent_runs import decision, start_request
from .test_delegation_api import api  # noqa: F401
from .test_delegations import create, ready, selection, service, ctx  # noqa: F401
from .test_ontology import command as ontology_command


@pytest.fixture
async def node_task(ctx, ready, service):  # noqa: F811
    path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(path.read_text(encoding="utf-8"))
    target = next(node for node in graph["nodes"] if node["id"] == "a")
    target.update(type="imageGenNode", data={
        "operation": "generate", "prompt": "a rainy station", "imageUrl": "/old.png",
    })
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    agents = ready[2]
    ref = await save(agents, configuration(capabilities=[
        "context.read", "proposal.create", "node.patch", "node.run",
    ]), request_id="crew-node-agent-save")
    adoption = await agents.apply(ontology_command("adopt", {
        "ref": ref, "proof": agents.library.version("agent", ref)["proof"],
    }, "crew-node-agent-adopt"))
    grant = {
        "allowed_node_ids": ["a"],
        "allowed_node_operations": [
            {"node_id": "a", "operation": "patch"},
            {"node_id": "a", "operation": "generate"},
        ],
        "max_node_runs": 1,
    }
    return await create(service, selection(ready,
        agent={"kind": "adoption", "adoption_id": adoption["adoption_id"], "revision": 1},
        scope={"kind": "nodes", "node_ids": ["a"]},
        goal="调整画面提示词并生成授权节点",
        allowed_operations=["context.read", "proposal.create", "node.patch", "node.run"],
        execution_authorization=grant,
        acceptance=["提示词已修改且目标节点有通过验证的生成结果"],
    ), "crew-node-task")


def _install_adapter(monkeypatch, *, submission="succeeded"):
    submitted = []
    instances = []

    class RecordingProviderAdapter(NodeAdapter):
        def __init__(self, project_dir):
            super().__init__(project_dir)
            instances.append(self)

        async def submit(self, run, attempt, upstream):
            submitted.append((attempt["node_id"], attempt["id"]))
            if submission == "unknown":
                raise TimeoutError("simulated lost provider response")
            return {"task_id": attempt["id"]}

        async def reconcile(self, run, attempt):
            return {"status": "succeeded", "result": {"imageUrl": "/new.png"}}

        async def verify(self, run, attempt):
            return [{"kind": "image", "url": "/new.png", "sha256": "fake-provider-result"}]

    # Keep NodeExecutionService, RunStore, Scheduler, and canvas projection real;
    # only the provider-facing NodeAdapter boundary is deterministic here.
    monkeypatch.setattr(node_execution, "NodeAdapter", RecordingProviderAdapter)
    return submitted, instances


def _node_action(operation, payload):
    return {"operation": operation, "payload": payload}


def _enable_fake_model_route(monkeypatch):
    # API-created AgentRuns checks the configured model route before invoking
    # the deterministic model response patched by each test.
    monkeypatch.setattr(agent_model, "availability", lambda _: {
        "available": True, "code": "configured_not_verified",
        "configuration_fingerprint": "test-model-config",
        "route": {"runtime": "model_api", "model": "test", "task_role": "creation_agent_decision", "source": "global"},
    })


def _find_key(value, key):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


async def test_route_patch_then_run_uses_real_node_service_and_projects_target(api, ctx, ready, node_task, monkeypatch):  # noqa: F811
    client, _ = api
    submitted, adapters = _install_adapter(monkeypatch)
    _enable_fake_model_route(monkeypatch)
    model_calls = []
    revision = read_canvas(ctx.state_dir, ready[0])["revision"]

    patch = _node_action("node.patch", {
        "node_id": "a", "expected_revision": revision,
        "changes": [{"path": ["prompt"], "value": "a moonlit station with reflected blue light"}],
    })
    run = _node_action("node.run", {
        "node_id": "a", "operation": "generate", "expected_revision": revision,
    })

    async def model(*args, **kwargs):
        model_calls.append((args, kwargs))
        return decision(patch, run)

    monkeypatch.setattr(agent_model, "run_decision", model)
    query = {"canvas_id": ready[0], "conversation_id": ready[1]}
    url = f"/projects/p/creation/delegations/{node_task['delegation_id']}/runs"
    body = start_request(node_task, request_id="crew-node-start")
    body.pop("delegation_id")  # The API takes this identifier from the route.
    started = await client.post(url, params=query, json=body)
    assert started.status_code == 200, started.text
    outer_id = started.json()["data"]["run"]["id"]

    current = (await client.get(f"/projects/p/creation/agent-runs/{outer_id}", params=query)).json()["data"]
    assert current["state"] == "delivered", current
    assert len(model_calls) == 1
    patch_output = next(output for output in current["outputs"] if output["operation"] == "node.patch")
    run_output = next(output for output in current["outputs"] if output["operation"] == "node.run")
    assert patch_output["node_id"] == run_output["node_id"] == "a"
    assert patch_output["state"] == "patched"
    assert patch_output["changes"] == [{
        "path": ["prompt"], "before": "a rainy station",
        "after": "a moonlit station with reflected blue light",
    }]
    assert patch_output["before"]["media_fingerprint"] == patch_output["after"]["media_fingerprint"]
    assert run_output["status"] == "succeeded"
    assert run_output["projection"]["status"] == "verified_projected"
    assert run_output["previous_media"] == [{"field": "imageUrl", "url": "/old.png"}]
    assert len(submitted) == 1 and submitted[0][0] == "a"

    inner = RunStore(ctx.state_dir).get(run_output["node_run_id"])
    assert inner["status"] == "succeeded"
    assert inner["order"] == ["a"]
    assert inner["agent_execution"]["target_node_id"] == "a"
    assert len(adapters) >= 1
    graph = read_canvas(ctx.state_dir, ready[0])
    node = next(node for node in graph["nodes"] if node["id"] == "a")
    assert node["data"]["prompt"] == "a moonlit station with reflected blue light"
    assert node["data"]["imageUrl"] == "/new.png"
    assert node["data"]["orchestration"]["result_history"][-1]["previous_media"][0]["url"] == "/old.png"

    replay = await client.post(url, params=query, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == started.json()["data"]
    assert len(model_calls) == 1
    assert len(submitted) == 1


async def test_unknown_node_submission_stays_blocked_on_resume_without_resend(api, ctx, ready, node_task, monkeypatch):  # noqa: F811
    client, _ = api
    submitted, _ = _install_adapter(monkeypatch, submission="unknown")
    _enable_fake_model_route(monkeypatch)
    model_calls = []
    revision = read_canvas(ctx.state_dir, ready[0])["revision"]
    proposal = _node_action("proposal.create", {
        "target_node_ids": ["a"], "input_indices": [], "acceptance_indices": [0],
        "title": "开场候选", "text": "她在午夜站台捡到一张旧车票。",
    })
    run = _node_action("node.run", {
        "node_id": "a", "operation": "generate", "expected_revision": revision,
    })

    async def model(*args, **kwargs):
        model_calls.append((args, kwargs))
        return decision(proposal, run)

    monkeypatch.setattr(agent_model, "run_decision", model)
    query = {"canvas_id": ready[0], "conversation_id": ready[1]}
    base = "/projects/p/creation"
    url = f"{base}/delegations/{node_task['delegation_id']}/runs"
    body = start_request(node_task, request_id="crew-node-unknown")
    body.pop("delegation_id")
    started = await client.post(url, params=query,
        json=body)
    assert started.status_code == 200, started.text
    outer_id = started.json()["data"]["run"]["id"]

    current = (await client.get(f"{base}/agent-runs/{outer_id}", params=query)).json()["data"]
    assert current["state"] == "blocked", current
    assert current["rounds"][-1]["phase"] == "decision_saved"
    assert current["rounds"][-1]["actions_done"] == [0]
    assert current["rounds"][-1]["actions_admitted"] == [1]
    assert len(current["outputs"]) == 1 and current["outputs"][0]["operation"] == "proposal.create"
    node_run_id = _find_key(current["error"], "node_run_id")
    assert node_run_id
    assert len(model_calls) == 1
    assert len(submitted) == 1
    inner = RunStore(ctx.state_dir).get(node_run_id)
    assert inner["status"] == "submission_unknown"

    resumed = await client.post(f"{base}/agent-runs/{outer_id}/resume", params=query,
        json={"request_id": "crew-node-unknown-resume", "expected_revision": current["revision"]})
    assert resumed.status_code == 200, resumed.text
    after_resume = (await client.get(f"{base}/agent-runs/{outer_id}", params=query)).json()["data"]
    assert after_resume["state"] == "blocked", after_resume
    assert after_resume["rounds"][-1]["phase"] == "decision_saved"
    assert after_resume["rounds"][-1]["actions_admitted"] == [1]
    assert _find_key(after_resume["error"], "node_run_id") == node_run_id
    assert len(model_calls) == 1
    assert len(submitted) == 1
    assert RunStore(ctx.state_dir).get(node_run_id)["status"] == "submission_unknown"

    stopped = await client.post(f"{base}/agent-runs/{outer_id}/stop", params=query,
        json={"request_id": "crew-node-unknown-stop", "expected_revision": after_resume["revision"]})
    assert stopped.status_code == 200, stopped.text
    final = (await client.get(f"{base}/agent-runs/{outer_id}", params=query)).json()["data"]
    assert final["state"] == "stopped", final
    inner_after_stop = RunStore(ctx.state_dir).get(node_run_id)
    assert inner_after_stop["cancel_requested"] is True
    assert inner_after_stop["status"] in {"submission_unknown", "cancel_requested", "cancelled"}
    node = next(node for node in read_canvas(ctx.state_dir, ready[0])["nodes"] if node["id"] == "a")
    assert node["data"]["imageUrl"] == "/old.png"
    history = node["data"].get("orchestration", {}).get("result_history", [])
    attempt_id = inner_after_stop["attempts"]["a"][-1]["id"]
    assert all(entry.get("attempt_id") != attempt_id for entry in history)
