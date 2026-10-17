import pytest

from novelvideo.creation.operations import OperationError
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.models import (OrchestrationError, dependency_edge_fingerprint,
    fingerprint, frozen_node_result, input_fingerprint, media_projection, result_projection)
from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph


def _canvas(project_dir, *, downstream=False, upstream_media=False):
    create_canvas(project_dir, "canvas", project_id="p", name="test", actor_id="alice")
    source_data = {"content": "a frozen scene"}
    if upstream_media:
        source_data = {"imageUrl": "/fixed-input.png", "referenceOnly": True}
    source_type = "uploadNode" if upstream_media else "textAnnotationNode"
    nodes = [
        {"id": "source", "type": source_type, "data": source_data},
        {"id": "board", "type": "storyboardGenNode", "data": {
            "operation": "generate", "prompt": "a rainy station", "imageUrl": "/old.png",
            "frames": [{"id": "frame-1", "imageUrl": "/frame-still.png",
                "description": "train arrives", "static_panel": {
                "id": "panel-1", "shot_id": "frame-1", "anchor": "start",
                "state": "before the train", "framing": "wide", "subjects": [],
                "setting": "platform", "lighting": "soft daylight", "visible_text": [],
            }}, {"id": "frame-2", "description": "train departs",
                "static_panel": {"id": "panel-2", "shot_id": "frame-2", "anchor": "end",
                    "state": "after the train", "framing": "wide", "subjects": [],
                    "setting": "platform", "lighting": "night", "visible_text": []}}],
        }},
    ]
    edges = [{"id": "source-board", "source": "source", "target": "board"}]
    if downstream:
        nodes.append({"id": "video", "type": "videoNode", "data": {
            "operation": "generate", "prompt": "animate the board", "referenceOnly": True,
            "videoUrl": "/downstream-old.mp4",
        }})
        edges.append({"id": "board-video", "source": "board", "target": "video"})
    return patch_graph(project_dir, "canvas", base_revision=1, request_id="seed",
                       operations=[*[{"op": "add_node", "node": n} for n in nodes],
                                   *[{"op": "add_edge", "edge": e} for e in edges]])


def _contract(*, nodes=("board",), read_nodes=("source",), operations=None, max_node_runs=2):
    return {
        "project_id": "p",
        "canvas_id": "canvas",
        "delegation_id": "delegation-1",
        "root_delegation_id": "delegation-1",
        "authorization_id": "authorization-1",
        "allowed_node_ids": list(nodes),
        "read_node_ids": list(read_nodes),
        "allowed_node_operations": operations or [
            {"node_id": "board", "operation": "patch"},
            {"node_id": "board", "operation": "generate"},
        ],
        "max_node_runs": max_node_runs,
        "node_run_budget_chain": [{"delegation_id": "delegation-1", "max_node_runs": max_node_runs}],
    }


def _transition(request_id="agent-action-1"):
    return {"agent_run_id": "agent-run-1", "step_id": "step-1",
            "action_id": request_id, "action_index": 0}


def _service(project_dir, *, adapter=None, authorize_submission=None, authorize_projection=None):
    from novelvideo.creation.node_execution import NodeExecutionService

    if authorize_projection is None:
        def authorize_projection(*_):
            return True
    return NodeExecutionService(project_dir, project_id="p", canvas_id="canvas",
        actor={"id": "alice", "username": "alice"}, adapter=adapter,
        authorize_submission=authorize_submission, authorize_projection=authorize_projection)


def test_patch_storyboard_lighting_is_scoped_audited_and_idempotent(tmp_path):
    canvas = _canvas(tmp_path)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    before_fingerprint = input_fingerprint(board)
    service = _service(tmp_path)
    changes = [{"path": ["frames", 0, "static_panel", "lighting"],
                "value": "cool reflected station light"}]

    result = service.patch_node(request_id="patch-1", node_id="board", expected_revision=canvas["revision"],
        expected_input_fingerprint=before_fingerprint, changes=changes, contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("patch-1"))

    assert result["operation"] == "node.patch"
    assert result["before"]["input_fingerprint"] == before_fingerprint
    assert result["after"]["input_fingerprint"] != before_fingerprint
    assert result["changes"] == [{"path": ["frames", 0, "static_panel", "lighting"],
                                  "before": "soft daylight", "after": "cool reflected station light"}]
    assert service.patch_node(request_id="patch-1", node_id="board", expected_revision=canvas["revision"],
        expected_input_fingerprint=before_fingerprint, changes=changes, contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("patch-1")) == result
    saved = read_canvas(tmp_path, "canvas")
    assert saved["revision"] == canvas["revision"] + 1
    assert saved["nodes"][1]["data"]["frames"][0]["static_panel"]["lighting"] == "cool reflected station light"
    assert saved["nodes"][1]["data"]["frames"][0]["imageUrl"] == "/frame-still.png"
    assert saved["nodes"][1]["data"]["frames"][0]["static_panel"]["id"] == "panel-1"
    assert saved["nodes"][1]["data"]["frames"][1]["static_panel"]["lighting"] == "night"


@pytest.mark.parametrize("change", [
    {"path": ["imageUrl"], "value": "https://example.com/new.png"},
    {"path": ["videoUrl"], "value": "/static/projects/p/output.mp4"},
    {"path": ["credentials", "api_key"], "value": "secret"},
    {"path": ["frames", 0, "imageUrl"], "value": "https://example.com/panel.png"},
    {"path": ["data", "operation"], "value": "erase"},
])
def test_patch_rejects_output_url_and_credential_fields(tmp_path, change):
    canvas = _canvas(tmp_path)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    service = _service(tmp_path)
    with pytest.raises(OperationError):
        service.patch_node(request_id="invalid", node_id="board", expected_revision=canvas["revision"],
            expected_input_fingerprint=input_fingerprint(board), changes=[change], contract=_contract(),
            authorization_id="authorization-1", accepted_transition=_transition("invalid"))
    assert read_canvas(tmp_path, "canvas")["revision"] == canvas["revision"]


def test_patch_rechecks_semantic_fingerprint_and_node_grant(tmp_path):
    canvas = _canvas(tmp_path)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    service = _service(tmp_path)
    changes = [{"path": ["prompt"], "value": "changed prompt"}]
    wrong_grant = _contract(operations=[{"node_id": "board", "operation": "generate"}])
    with pytest.raises(OperationError):
        service.patch_node(request_id="denied", node_id="board", expected_revision=canvas["revision"],
            expected_input_fingerprint=input_fingerprint(board), changes=changes, contract=wrong_grant,
            authorization_id="authorization-1", accepted_transition=_transition("denied"))
    with pytest.raises(OperationError) as stale:
        service.patch_node(request_id="stale", node_id="board", expected_revision=canvas["revision"],
            expected_input_fingerprint="0" * 64, changes=changes, contract=_contract(),
            authorization_id="authorization-1", accepted_transition=_transition("stale"))
    assert stale.value.code == "stale_input"


class _RecordingAdapter:
    def __init__(self):
        self.submitted = []

    async def submit(self, run, attempt, upstream):
        self.submitted.append((attempt["node_id"], upstream))
        return {"task_id": attempt["id"]}

    async def reconcile(self, run, attempt):
        return {"status": "succeeded", "result": {"imageUrl": "/new.png"}}

    async def verify(self, run, attempt):
        return [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]

    async def project(self, run, attempt, *, authorize_projection=None):
        from novelvideo.freezone.orchestration.projection import project_result
        project_result(self.project_dir, run["canvas_id"], node_id=attempt["node_id"],
            expected_input=run["input_fingerprints"][attempt["node_id"]], attempt_id=attempt["id"],
            result=attempt["result"], history_entry={
                "attempt_id": attempt["id"], "status": "verified_projected",
                "artifacts": attempt["artifacts"],
                "previous_media": run.get("agent_execution", {}).get("previous_media", []),
            } if run.get("agent_execution") else None,
            expected_result_fingerprint=run.get("agent_execution", {}).get("target_result_fingerprint"),
            expected_media_fingerprint=run.get("agent_execution", {}).get("target_media_fingerprint"),
            expected_source_output_fingerprints=run.get("agent_execution", {}).get("source_output_fingerprints"),
            expected_dependency_edge_fingerprint=run.get("agent_execution", {}).get("dependency_edge_fingerprint"),
            authorize_projection=authorize_projection)


@pytest.mark.asyncio
async def test_execute_submits_only_target_with_frozen_actual_inputs_and_keeps_old_media(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path, downstream=True)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    checks = []

    async def authorize_submission(run, attempt):
        checks.append((run["agent_execution"]["delegation_id"], attempt["node_id"]))

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorize_submission)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="run-1", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1", accepted_transition=_transition("run-1"))

    run = RunStore(tmp_path).get(receipt["run_id"])
    assert run["order"] == ["board"]
    assert set(run["attempts"]) == {"board"}
    assert receipt["previous_media"][0]["url"] == "/old.png"
    assert adapter.submitted == [("board", [{"node_id": "source", "result": {"text": "a frozen scene"}, "artifacts": []}])]
    assert checks and all(node_id == "board" for _, node_id in checks)
    replay = await service.execute_node(request_id="run-1", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1", accepted_transition=_transition("run-1"))
    assert replay["run_id"] == receipt["run_id"]
    assert replay["status"] == "succeeded"
    assert len(adapter.submitted) == 1
    result = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert result["status"] == "succeeded"
    assert adapter.submitted and len(adapter.submitted) == 1
    final = await service.read_run(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert final["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    assert final["projection"]["status"] == "verified_projected"
    assert final["projection"]["projected_media_fingerprint"]
    saved = read_canvas(tmp_path, "canvas")
    nodes = {node["id"]: node for node in saved["nodes"]}
    assert nodes["board"]["data"]["imageUrl"] == "/new.png"
    history = nodes["board"]["data"]["orchestration"]["result_history"]
    assert history[-1]["previous_media"][0]["url"] == "/old.png"
    assert nodes["video"]["data"]["videoUrl"] == "/downstream-old.mp4"


@pytest.mark.asyncio
async def test_stop_after_submission_keeps_provider_result_and_artifacts_without_adopting(tmp_path):
    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    accepted = await service.execute_node(request_id="stop-after-submit", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("stop-after-submit"))

    found = service.find_request_run("stop-after-submit", contract=_contract(),
                                     authorization_id="authorization-1")
    assert found["run_id"] == accepted["run_id"]
    await service.stop_run(found["run_id"], contract=_contract(), authorization_id="authorization-1")
    stopped = await service.read_run(found["run_id"], contract=_contract(), authorization_id="authorization-1")

    assert stopped["status"] == "cancelled"
    assert stopped["attempt_status"] == "cancelled"
    assert stopped["provider_status"] == "succeeded"
    assert stopped["cancel_reason"] == "adoption_stopped"
    assert stopped["result"] == {"imageUrl": "/new.png"}
    assert stopped["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    current = read_canvas(tmp_path, "canvas")
    current_board = next(node for node in current["nodes"] if node["id"] == "board")
    assert current_board["data"]["imageUrl"] == "/old.png"

    resumed = await service.resume_run(found["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert resumed["status"] == "succeeded"
    assert resumed["projection"]["status"] == "verified_projected"
    assert len(adapter.submitted) == 1


@pytest.mark.asyncio
async def test_execute_freezes_existing_media_and_never_submits_unscoped_ancestors(tmp_path):
    canvas = _canvas(tmp_path, upstream_media=True)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    with pytest.raises(OperationError) as denied:
        await service.execute_node(request_id="scope-denied", node_id="board", operation="generate",
            expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
            contract=_contract(read_nodes=()), authorization_id="authorization-1",
            accepted_transition=_transition("scope-denied"))
    assert denied.value.code == "dependency_scope_denied"
    assert adapter.submitted == []

    receipt = await service.execute_node(request_id="media-source", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1", accepted_transition=_transition("media-source"))
    assert adapter.submitted[0][1] == [{"node_id": "source", "result": {"imageUrl": "/fixed-input.png"}, "artifacts": []}]
    assert receipt["status"] == "running"


@pytest.mark.asyncio
async def test_child_run_keeps_its_own_cap_after_root_used_one_attempt(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    store = RunStore(tmp_path)
    root_chain = [{"delegation_id": "root", "max_node_runs": 3}]
    assert store.reserve_node_run("root", "root-prior", budget_chain=root_chain)
    store.consume_node_run("root", "root-prior")
    child_contract = _contract(max_node_runs=1)
    child_contract.update(delegation_id="child", root_delegation_id="root",
                          authorization_id="child-auth",
                          node_run_budget_chain=[*root_chain,
                              {"delegation_id": "child", "max_node_runs": 1}])
    board = next(node for node in canvas["nodes"] if node["id"] == "board")

    receipt = await service.execute_node(request_id="child-run", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=child_contract, authorization_id="child-auth", accepted_transition=_transition("child-run"))
    assert receipt["status"] == "running"
    assert receipt["usage"] == {"kind": "node_run_count", "delegation_used": 1,
                                "delegation_limit": 1, "root_used": 2, "root_limit": 3}
    assert len(adapter.submitted) == 1


@pytest.mark.asyncio
async def test_execute_blocks_when_frozen_source_has_no_existing_output(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso

    canvas = _canvas(tmp_path)
    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][0]["data"].pop("content")
    save_canvas(tmp_path, "canvas", base_revision=canvas["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    canvas = read_canvas(tmp_path, "canvas")
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)

    with pytest.raises(OperationError) as blocked:
        await service.execute_node(request_id="no-source-output", node_id="board", operation="generate",
            expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
            contract=_contract(), authorization_id="authorization-1",
            accepted_transition=_transition("no-source-output"))
    assert blocked.value.code == "dependency_input_unavailable"
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_lost_execute_response_replays_same_run_after_source_changes(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    request = dict(request_id="response-lost", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1",
        accepted_transition=_transition("response-lost"))

    # The first receipt is intentionally discarded to model a transport loss.
    await service.execute_node(**request)
    persisted = RunStore(tmp_path).find_request("canvas", "agent-" + fingerprint(
        ["delegation-1", "response-lost"])[:64])
    assert persisted is not None
    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][0]["data"]["content"] = "later upstream edit"
    save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")

    recovered = await service.execute_node(**request)
    assert recovered["run_id"] == persisted["id"]
    assert recovered["status"] == "blocked"
    assert recovered["projection"]["status"] == "stale"
    assert len(adapter.submitted) == 1
    assert adapter.submitted[0][1] == [{"node_id": "source", "result": {"text": "a frozen scene"}, "artifacts": []}]


@pytest.mark.asyncio
async def test_projection_conflict_preserves_user_adopted_media_and_run_artifacts(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="manual-media-race", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1",
        accepted_transition=_transition("manual-media-race"))
    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][1]["data"]["imageUrl"] = "/user-adopted.png"
    save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")

    terminal = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert terminal["status"] == "blocked"
    assert terminal["attempt_status"] == "verifying", terminal
    assert terminal["projection"]["status"] == "stale"
    assert terminal["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    assert terminal["projection"]["current_media"] == {"imageUrl": "/user-adopted.png"}
    saved = read_canvas(tmp_path, "canvas")
    board_after = next(node for node in saved["nodes"] if node["id"] == "board")
    assert board_after["data"]["imageUrl"] == "/user-adopted.png"
    assert board_after["data"].get("orchestration", {}).get("result_history") is None


@pytest.mark.asyncio
async def test_run_receipt_detects_external_output_edit_after_projection(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="tamper-projection", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1",
        accepted_transition=_transition("tamper-projection"))
    receipt = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert receipt["status"] == "succeeded"

    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][1]["data"]["imageUrl"] = "/external-edit.png"
    save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    reread = await service.read_run(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert reread["status"] == "succeeded"
    assert reread["projection"]["status"] == "stale"


@pytest.mark.asyncio
async def test_run_receipt_detects_changed_dependency_after_projection(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="dependency-after-project", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("dependency-after-project"))
    await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")

    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][0]["data"]["content"] = "later dependency edit"
    save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    reread = await service.read_run(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert reread["projection"]["status"] == "stale"


@pytest.mark.asyncio
async def test_resume_reprojects_conflicted_paid_result_without_resubmitting(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="resume-stale-result", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("resume-stale-result"))
    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"][1]["data"]["imageUrl"] = "/user-adopted.png"
    save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    stale = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert stale["projection"]["status"] == "stale"
    await service.stop_run(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    cancelled = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert cancelled["status"] == "cancelled"

    restored = read_canvas(tmp_path, "canvas")
    restored["nodes"][1]["data"]["imageUrl"] = "/old.png"
    save_canvas(tmp_path, "canvas", base_revision=restored["revision"],
        build_payload=lambda existing: {**restored, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    resumed = service.store.resume(receipt["run_id"])
    assert resumed["attempts"]["board"][-1]["status"] == "verifying"
    projected = await service.tick(receipt["run_id"], contract=_contract(), authorization_id="authorization-1")
    assert projected["status"] == "succeeded"
    assert len(adapter.submitted) == 1


def test_dependency_edge_fingerprint_tracks_ports_but_ignores_editor_style(tmp_path):
    from novelvideo.freezone.orchestration.models import dependency_edge_fingerprint

    canvas = _canvas(tmp_path)
    original = dependency_edge_fingerprint(canvas, "board")
    canvas["edges"][0]["style"] = {"stroke": "pink"}
    canvas["edges"][0]["label"] = "editor label"
    assert dependency_edge_fingerprint(canvas, "board") == original
    canvas["edges"][0].setdefault("data", {})["sourcePort"] = "different-port"
    assert dependency_edge_fingerprint(canvas, "board") != original


def test_agent_compose_projection_does_not_write_ungranted_companion_node(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso
    from novelvideo.freezone.orchestration.projection import project_result

    canvas = _canvas(tmp_path)
    output_id = "output-" + fingerprint(["canvas", "compose", "video"])[:24]
    compose = {"id": "compose", "type": "videoComposeNode", "data": {
        "operation": "compose", "resultVideoUrl": "/old-compose.mp4"}}
    companion = {"id": output_id, "type": "videoNode", "data": {
        "videoUrl": "/manually-adopted.mp4", "referenceOnly": True}}
    changed = read_canvas(tmp_path, "canvas")
    changed["nodes"].extend([compose, companion])
    save_canvas(tmp_path, "canvas", base_revision=canvas["revision"],
        build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                        "updated_at": utc_now_iso()}, save_source="orchestration")
    compose = next(node for node in changed["nodes"] if node["id"] == "compose")
    project_result(tmp_path, "canvas", node_id="compose", expected_input=input_fingerprint(compose),
        attempt_id="agent-compose-attempt", result={"resultVideoUrl": "/new-compose.mp4"},
        history_entry={"attempt_id": "agent-compose-attempt", "status": "verified_projected"})
    projected = read_canvas(tmp_path, "canvas")
    nodes = {node["id"]: node for node in projected["nodes"]}
    assert nodes["compose"]["data"]["resultVideoUrl"] == "/new-compose.mp4"
    assert nodes[output_id]["data"]["videoUrl"] == "/manually-adopted.mp4"


def test_node_run_budget_reserves_root_and_every_nested_delegation_atomically(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore

    store = RunStore(tmp_path)
    root_chain = [{"delegation_id": "root", "max_node_runs": 2}]
    child_chain = [*root_chain, {"delegation_id": "child", "max_node_runs": 1}]
    assert store.reserve_node_run("root", "root-attempt", budget_chain=root_chain)
    store.consume_node_run("root", "root-attempt")
    assert store.reserve_node_run("root", "child-attempt", delegation_id="child", budget_chain=child_chain)
    store.consume_node_run("root", "child-attempt")
    assert store.node_run_count("root") == 2
    assert store.node_run_count("root", "child") == 1
    with pytest.raises(OrchestrationError) as root_exhausted:
        store.reserve_node_run("root", "other-attempt", delegation_id="other",
            budget_chain=[*root_chain, {"delegation_id": "other", "max_node_runs": 1}])
    assert root_exhausted.value.code == "node_run_budget_exhausted"
    with pytest.raises(OrchestrationError) as child_exhausted:
        store.reserve_node_run("root", "grandchild-attempt", delegation_id="grandchild",
            budget_chain=[*root_chain, {"delegation_id": "child", "max_node_runs": 1},
                          {"delegation_id": "grandchild", "max_node_runs": 1}])
    assert child_exhausted.value.code == "node_run_budget_exhausted"


@pytest.mark.asyncio
async def test_scheduler_reconciles_existing_attempt_reservation_before_retry(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)
    target = next(node for node in canvas["nodes"] if node["id"] == "board")
    source = next(node for node in canvas["nodes"] if node["id"] == "source")
    chain = [{"delegation_id": "delegation-1", "max_node_runs": 2}]
    marker = {
        "target_node_id": "board", "operation": "generate", "project_id": "p",
        "canvas_id": "canvas", "actor_id": "alice", "delegation_id": "delegation-1",
        "root_delegation_id": "delegation-1", "authorization_id": "authorization-1",
        "allowed_node_ids": ["board"],
        "allowed_node_operations": [{"node_id": "board", "operation": "generate"}],
        "read_node_ids": ["source"], "max_node_runs": 2, "root_max_node_runs": 2,
        "node_run_budget_chain": chain, "target_result_fingerprint": fingerprint(result_projection(target)),
        "target_media_fingerprint": fingerprint(media_projection(target)),
        "dependency_edge_fingerprint": dependency_edge_fingerprint(canvas, "board"),
        "source_output_fingerprints": {"source": fingerprint(frozen_node_result(source))},
        "frozen_inputs": [{"node_id": "source", "result": frozen_node_result(source), "artifacts": []}],
    }
    store = RunStore(tmp_path)
    run = store.create(canvas, target="board", target_only=True, agent_execution=marker,
        request_id="interrupted-reservation", project_id="p", settings={"concurrency": 1},
        actor={"id": "alice"}, operation="generate")
    attempt_id = run["attempts"]["board"][-1]["id"]
    assert store.reserve_node_run("delegation-1", attempt_id, budget_chain=chain)
    store.consume_node_run("delegation-1", attempt_id)

    class _ReconcileNotSubmitted(_RecordingAdapter):
        async def reconcile_submission(self, run, attempt):
            return {"status": "not_submitted", "evidence": {"source": "provider lookup"}}

    adapter = _ReconcileNotSubmitted()
    adapter.project_dir = tmp_path

    async def authorized(*_):
        return None

    scheduler = Scheduler(store, adapter, authorize_submission=authorized)
    recovered = await scheduler.tick(run["id"])
    assert recovered["status"] == "submission_unknown"
    assert adapter.submitted == []
    recovered = await scheduler.tick(run["id"])
    assert recovered["status"] == "running"
    assert len(adapter.submitted) == 1


@pytest.mark.asyncio
async def test_live_authorization_is_rechecked_before_projection(tmp_path):
    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    checks = 0

    async def authorize(*_):
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise OrchestrationError("authorization_revoked", "Grant was revoked")

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorize)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    submitted = await service.execute_node(request_id="projection-auth-revoke", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("projection-auth-revoke"))
    blocked = await service.read_run(submitted["run_id"], contract=_contract(),
                                     authorization_id="authorization-1")

    assert checks == 3
    assert blocked["status"] == "blocked"
    assert blocked["attempt_status"] == "verifying"
    assert blocked["error"]["code"] == "authorization_revoked"
    assert blocked["result"] == {"imageUrl": "/new.png"}
    assert blocked["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    current = read_canvas(tmp_path, "canvas")
    current_board = next(node for node in current["nodes"] if node["id"] == "board")
    assert current_board["data"]["imageUrl"] == "/old.png"


@pytest.mark.asyncio
async def test_projection_authorization_fence_runs_inside_canvas_cas(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    fence_checks = []

    async def authorize_submission(*_):
        return None

    def authorize_projection(run, attempt):
        fence_checks.append((run["id"], attempt["id"]))
        raise OrchestrationError("authorization_revoked", "Canvas was revoked before projection")

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorize_submission,
                       authorize_projection=authorize_projection)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    accepted = await service.execute_node(request_id="cas-auth-fence", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1", accepted_transition=_transition("cas-auth-fence"))
    blocked = await service.read_run(accepted["run_id"], contract=_contract(),
                                     authorization_id="authorization-1")

    attempt_id = RunStore(tmp_path).get(accepted["run_id"])["attempts"]["board"][-1]["id"]
    assert fence_checks == [(accepted["run_id"], attempt_id)]
    assert blocked["status"] == "blocked"
    assert blocked["error"]["code"] == "authorization_revoked"
    assert blocked["result"] == {"imageUrl": "/new.png"}
    assert blocked["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    current = read_canvas(tmp_path, "canvas")
    current_board = next(node for node in current["nodes"] if node["id"] == "board")
    assert current_board["data"]["imageUrl"] == "/old.png"


@pytest.mark.asyncio
async def test_projection_save_recovery_recognizes_same_attempt_after_journal_crash(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)

    class _CrashAfterCanvasSave(_RecordingAdapter):
        async def project(self, run, attempt, *, authorize_projection=None):
            await super().project(run, attempt, authorize_projection=authorize_projection)
            raise RuntimeError("simulated process loss after canvas save")

    crashing = _CrashAfterCanvasSave()
    crashing.project_dir = tmp_path

    async def authorize_submission(*_):
        return None

    service = _service(tmp_path, adapter=crashing, authorize_submission=authorize_submission)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    accepted = await service.execute_node(request_id="crash-after-project-save", node_id="board",
        operation="generate", expected_revision=canvas["revision"],
        expected_input_fingerprint=input_fingerprint(board), contract=_contract(),
        authorization_id="authorization-1",
        accepted_transition=_transition("crash-after-project-save"))

    interrupted = await service.tick(accepted["run_id"], contract=_contract(),
                                     authorization_id="authorization-1")
    assert interrupted["status"] == "blocked"
    assert interrupted["attempt_status"] == "verifying"
    after_save = read_canvas(tmp_path, "canvas")
    saved_board = next(node for node in after_save["nodes"] if node["id"] == "board")
    assert saved_board["data"]["imageUrl"] == "/new.png"
    attempt_id = RunStore(tmp_path).get(accepted["run_id"])["attempts"]["board"][-1]["id"]
    assert saved_board["data"]["orchestration"]["attempt_id"] == attempt_id

    replay = _RecordingAdapter()
    replay.project_dir = tmp_path
    replay.submitted = crashing.submitted
    service.scheduler.adapter = replay
    recovered = await service.tick(accepted["run_id"], contract=_contract(),
                                   authorization_id="authorization-1")

    assert recovered["status"] == "succeeded"
    assert recovered["projection"]["status"] == "verified_projected"
    assert recovered["artifacts"] == [{"kind": "image", "url": "/new.png", "sha256": "new-hash"}]
    assert len(replay.submitted) == 1


@pytest.mark.asyncio
async def test_agent_run_without_recovery_authorizer_never_submits(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _canvas(tmp_path)
    marker = {"delegation_id": "delegation-1", "root_delegation_id": "delegation-1",
        "authorization_id": "authorization-1", "target_node_id": "board", "operation": "generate",
        "contract_fingerprint": "trusted", "max_node_runs": 1,
        "accepted_transition": _transition(), "frozen_inputs": [{"node_id": "source", "result": {"text": "a frozen scene"}}]}
    store = RunStore(tmp_path)
    run = store.create(canvas, target="board", target_only=True, agent_execution=marker,
        request_id="recovered", project_id="p", settings={"concurrency": 1}, actor={"id": "alice"},
        operation="generate")
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    recovered = await Scheduler(RunStore(tmp_path), adapter).tick(run["id"])
    assert recovered["status"] == "blocked"
    assert recovered["attempts"]["board"][-1]["error"]["code"] == "authorization_unavailable"
    assert adapter.submitted == []
    assert RunStore(tmp_path).node_run_count("delegation-1") == 0


@pytest.mark.asyncio
async def test_revoked_grant_blocks_before_reservation_and_submit(tmp_path):
    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path

    async def revoked(run, attempt):
        raise OrchestrationError("authorization_revoked", "grant is no longer active")

    service = _service(tmp_path, adapter=adapter, authorize_submission=revoked)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="revoked", node_id="board", operation="generate",
        expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1", accepted_transition=_transition("revoked"))
    assert receipt["status"] == "blocked"
    assert adapter.submitted == []
    assert service.store.node_run_count("delegation-1") == 0


@pytest.mark.asyncio
async def test_execute_rejects_missing_operation_grant_and_changed_source_before_submit(tmp_path):
    canvas = _canvas(tmp_path)
    adapter = _RecordingAdapter()
    adapter.project_dir = tmp_path
    authorization_calls = 0

    async def authorized(*_):
        nonlocal authorization_calls
        authorization_calls += 1
        if authorization_calls == 1:
            from novelvideo.freezone.canvas_store import save_canvas, utc_now_iso
            changed = read_canvas(tmp_path, "canvas")
            changed["nodes"][0]["data"]["content"] = "changed between admission and submit"
            save_canvas(tmp_path, "canvas", base_revision=changed["revision"],
                build_payload=lambda existing: {**changed, "revision": existing["revision"] + 1,
                                                "updated_at": utc_now_iso()}, save_source="orchestration")
        return None

    service = _service(tmp_path, adapter=adapter, authorize_submission=authorized)
    board = next(node for node in canvas["nodes"] if node["id"] == "board")
    no_operation = _contract(operations=[{"node_id": "board", "operation": "patch"}])
    with pytest.raises(OperationError):
        await service.execute_node(request_id="wrong-op", node_id="board", operation="generate",
            expected_revision=canvas["revision"], expected_input_fingerprint=input_fingerprint(board),
            contract=no_operation, authorization_id="authorization-1", accepted_transition=_transition("wrong-op"))

    changed = read_canvas(tmp_path, "canvas")
    board = next(node for node in changed["nodes"] if node["id"] == "board")
    receipt = await service.execute_node(request_id="changed-source", node_id="board", operation="generate",
        expected_revision=changed["revision"], expected_input_fingerprint=input_fingerprint(board),
        contract=_contract(), authorization_id="authorization-1", accepted_transition=_transition("changed-source"))
    assert receipt["status"] == "blocked"
    assert receipt["error"]["code"] == "input_conflict"
    assert adapter.submitted == []
