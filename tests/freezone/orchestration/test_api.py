import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone_orchestration as routes
    async def resolve(project, user, required_role="editor"):
        if project != "p":
            raise HTTPException(403, "project denied")
        return SimpleNamespace(project_id="p", state_dir=tmp_path), "u", "test", tmp_path, str(tmp_path)
    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(routes, "runtime_settings", lambda: {"concurrency": 2, "source": "saved"})
    monkeypatch.setattr(routes, "launch", lambda *args: None)
    app = FastAPI()
    app.state.project_dir = tmp_path
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u", "username": "u"}
    return TestClient(app)


PREFIX = "/api/v1/projects/p/freezone/orchestration"


def test_preview_is_read_only_and_rejects_legacy_narrative(client):
    client.post(PREFIX + '/canvases', json={'canvas_id': 'preview', 'name': 'preview'})
    client.post(PREFIX + '/canvases/preview/patch', json={'base_revision': 1, 'request_id': 'p',
        'operations': [{'op': 'add_node', 'node': {'id': 'board', 'type': 'storyboardGenNode',
            'data': {'storyboardMode': 'narrative', 'frames': [{'id': 's1', 'description': '动作过程'}]}}}]})
    response = client.get(PREFIX + '/canvases/preview/nodes/board/preview')
    assert response.status_code == 422, response.text
    assert 'static_contract_required' in response.text
    assert client.get(PREFIX + '/canvases/preview/runs').json()['data'] == []


def test_execution_refuses_a_stale_preview_revision(client):
    client.post(PREFIX + '/canvases', json={'canvas_id': 'stale', 'name': 'stale'})
    response = client.post(PREFIX + '/runs', json={'canvas_id': 'stale', 'target': 'board',
        'request_id': 'stale-request', 'expected_revision': 999})
    assert response.status_code == 409, response.text
    assert 'preview' in response.text


@pytest.mark.asyncio
async def test_orchestration_uses_the_same_state_root_as_web(monkeypatch, tmp_path):
    from novelvideo.api.routes import freezone_orchestration as routes
    async def resolve(*args, **kwargs):
        return SimpleNamespace(state_dir=tmp_path / "state"), "u", "p", tmp_path / "media", str(tmp_path / "media")
    monkeypatch.setattr(routes, "_resolve_media_project", resolve, raising=False)
    _, _, _, canvas_root, media_root = await routes._resolve_freezone_project("p", {"id": "u"})
    assert canvas_root == tmp_path / "state"
    assert media_root == str(tmp_path / "media")


def test_api_create_patch_run_and_status(client):
    created = client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    assert created.status_code == 200, created.text
    patch = {"base_revision": 1, "request_id": "add", "operations": [{"op": "add_node", "node": {
        "id": "text", "type": "textAnnotationNode", "data": {"text": "剧本"}, "position": {"x": 0, "y": 0}}}]}
    response = client.post(PREFIX + "/canvases/canvas/patch", json=patch)
    assert response.status_code == 200, response.text
    created = client.post(PREFIX + "/runs", json={"canvas_id": "canvas", "target": "text", "request_id": "run"})
    assert created.status_code == 202, created.text
    run_id = created.json()["data"]["id"]
    assert client.get(PREFIX + f"/runs/{run_id}").json()["data"]["status"] == "pending"
    assert client.post(PREFIX + f"/runs/{run_id}/cancel").json()["data"]["cancel_requested"]


def test_api_patch_persists_reliably_inferred_artifact_kinds(client):
    client.post(PREFIX + "/canvases", json={"canvas_id": "kinds", "name": "kinds"})
    response = client.post(PREFIX + "/canvases/kinds/patch", json={
        "base_revision": 1, "request_id": "kinds-1", "operations": [
            {"op": "add_node", "node": {"id": "idea", "type": "ideationNode", "data": {}}},
            {"op": "add_node", "node": {"id": "asset", "type": "imageGenNode",
                "data": {"assetId": "asset-1"}}},
            {"op": "add_node", "node": {"id": "ambiguous", "type": "imageGenNode",
                "data": {"prompt": "a scene"}}},
        ],
    })
    assert response.status_code == 200, response.text
    nodes = {node["id"]: node for node in response.json()["data"]["nodes"]}
    assert nodes["idea"]["data"]["artifactKind"] == "idea"
    assert nodes["asset"]["data"]["artifactKind"] == "asset"
    assert "artifactKind" not in nodes["ambiguous"]["data"]


def test_fixed_revision_operation_creates_target_only_run(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.freezone.canvas_store import read_canvas

    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda state_dir, canvas_id: {
        "canvas_id": canvas_id, "kind": "episode", "episode_id": "episode-1",
        "episode_number": 1, "status": "applied",
    })
    client.post(PREFIX + "/canvases", json={"canvas_id": "fixed", "name": "fixed"})
    patch = {"base_revision": 1, "request_id": "add", "operations": [
        {"op": "add_node", "node": {"id": "idea", "type": "ideationNode",
            "data": {"operation": "generate", "referenceOnly": True}}},
        {"op": "add_node", "node": {"id": "screenplay", "type": "screenplayNode",
            "data": {"operation": "plan", "referenceOnly": True, "episodeNumber": 1}}},
        {"op": "add_edge", "edge": {"id": "idea-screenplay", "source": "idea",
            "target": "screenplay"}},
    ]}
    created = client.post(PREFIX + "/canvases/fixed/patch", json=patch)
    assert created.status_code == 200, created.text

    response = client.post(PREFIX + "/runs", json={"canvas_id": "fixed", "target": "screenplay",
        "operation": "inspect", "request_id": "inspect"})
    assert response.status_code == 202, response.text
    run = response.json()["data"]
    assert run["order"] == ["screenplay"]
    target = next(node for node in run["snapshot"]["nodes"] if node["id"] == "screenplay")
    assert target["data"]["operation"] == "inspect"
    assert target["data"]["referenceOnly"] is True

    saved = read_canvas(client.app.state.project_dir, "fixed")
    assert next(node for node in saved["nodes"] if node["id"] == "screenplay")["data"]["operation"] == "plan"


def test_api_conflict_and_permissions_are_not_hidden(client):
    client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    response = client.post(PREFIX + "/canvases/canvas/patch", json={"base_revision": 88, "request_id": "bad", "operations": []})
    assert response.status_code == 409
    assert client.get(PREFIX.replace("/p/", "/other/") + "/capabilities").status_code == 403
    assert client.get(PREFIX + "/runs/absent").status_code == 404


def test_api_lists_all_nodes_including_blocked_operations(client):
    response = client.get(PREFIX + "/capabilities")
    assert response.status_code == 200
    nodes = response.json()["data"]["nodes"]
    assert len(nodes) == 27
    assert {'agentNode', 'narrativeGroupStoryboardNode', 'narrativeGroupVideoNode',
            'episodeComposeNode'} <= {node['node_type'] for node in nodes}
    assert {"ideationNode", "screenplayNode", "directorWorkspaceNode",
            "characterVoiceNode", "narrativeGroupStoryboardNode"} <= {
        node["node_type"] for node in nodes
    }


def test_invalid_image_size_rejected_before_run_created(client):
    client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    client.post(PREFIX + "/canvases/canvas/patch", json={"base_revision": 1, "request_id": "add", "operations": [
        {"op": "add_node", "node": {"id": "image", "type": "imageGenNode", "data": {
            "prompt": "paper", "model": "gpt-image-2", "size": "2K", "aspectRatio": "16:9",
            "request": {"provider": "grsai"}}}}]})
    response = client.post(PREFIX + "/runs", json={"canvas_id": "canvas", "target": "image", "request_id": "bad"})
    assert response.status_code == 422
    assert "does not support" in response.text
    assert client.get(PREFIX + "/canvases/canvas/runs").json()["data"] == []


def test_document_api_shares_utf8_contents_and_rejects_stale_hash(client):
    created = client.post(PREFIX + "/canvases", json={"canvas_id": "docs", "name": "docs"})
    assert created.status_code == 200, created.text

    path = PREFIX + "/canvases/docs/documents"
    first = client.put(path, json={"path": "episodes/003/script.md", "text": "初稿", "expected_hash": None})
    assert first.status_code == 200, first.text
    first_document = first.json()["data"]
    assert first_document["text"] == "初稿"
    assert first_document["hash"]

    changed = client.put(path, json={
        "path": "episodes/003/script.md", "text": "画布修改", "expected_hash": first_document["hash"],
    })
    assert changed.status_code == 200, changed.text
    stale = client.put(path, json={
        "path": "episodes/003/script.md", "text": "旧的 Codex 内容", "expected_hash": first_document["hash"],
    })
    assert stale.status_code == 409, stale.text

    reread = client.get(path, params={"path": "episodes/003/script.md"})
    assert reread.status_code == 200, reread.text
    assert reread.json()["data"]["text"] == "画布修改"


def test_document_api_rejects_unregistered_canvas_and_path_escape(client):
    missing = client.get(PREFIX + "/canvases/missing/documents", params={"path": "episodes/003/script.md"})
    assert missing.status_code == 404

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "docs", "name": "docs"})
    assert created.status_code == 200, created.text
    escaped = client.get(PREFIX + "/canvases/docs/documents", params={"path": "../outside.md"})
    assert escaped.status_code == 422


def test_document_api_checks_canvas_project_membership(client, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import freezone_orchestration as routes

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "owned", "name": "owned"})
    assert created.status_code == 200, created.text

    async def resolve_any_project(_project, _user, required_role="editor"):
        return SimpleNamespace(project_id=_project, state_dir=client.app.state.project_dir), "u", "test", \
            client.app.state.project_dir, str(client.app.state.project_dir)

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve_any_project)
    response = client.get("/api/v1/projects/other/freezone/orchestration/canvases/owned/documents",
                          params={"path": "episodes/003/script.md"})
    assert response.status_code == 403, response.text


def test_progress_api_requires_episode_canvas_membership(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "episode-canvas", "name": "episode"})
    assert created.status_code == 200, created.text
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, _canvas_id: {
        "canvas_id": "episode-canvas", "kind": "episode", "episode_id": "episode-object-3",
        "episode_number": 3, "status": "applied",
    })

    base = PREFIX + "/canvases/episode-canvas/progress"
    wrong_episode = client.get(base + "/004")
    assert wrong_episode.status_code == 409, wrong_episode.text

    configured = client.post(base + "/003/configure", json={"mode": "auto"})
    assert configured.status_code == 200, configured.text
    assert configured.json()["data"]["episode_id"] == "003"


def test_progress_api_uses_document_outputs_and_requires_current_approval_hash(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "episode-canvas", "name": "episode"})
    assert created.status_code == 200, created.text
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, _canvas_id: {
        "canvas_id": "episode-canvas", "kind": "episode", "episode_id": "episode-object-3",
        "episode_number": 3, "status": "applied",
    })

    document = client.put(PREFIX + "/canvases/episode-canvas/documents", json={
        "path": "episodes/003/script.md", "text": "第一场：雨夜。", "expected_hash": None,
    })
    assert document.status_code == 200, document.text

    completed = client.post(PREFIX + "/canvases/episode-canvas/progress/003/complete", json={
        "stage": "screenplay", "input_hash": "draft-v1", "outputs": {"script": "episodes/003/script.md"},
    })
    assert completed.status_code == 200, completed.text
    state = completed.json()["data"]
    assert state["stages"]["screenplay"]["status"] == "awaiting_review"
    approval_hash = state["stages"]["screenplay"]["approval_hash"]

    stale = client.post(PREFIX + "/canvases/episode-canvas/progress/003/approve", json={
        "stage": "screenplay", "input_hash": "stale-hash",
    })
    assert stale.status_code == 409, stale.text
    approved = client.post(PREFIX + "/canvases/episode-canvas/progress/003/approve", json={
        "stage": "screenplay", "input_hash": approval_hash,
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["stages"]["screenplay"]["status"] == "ready"


def test_image_reference_patch_preserves_screenplay_approval_and_invalidates_assets(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "managed-assets", "name": "episode"})
    assert created.status_code == 200, created.text
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, _canvas_id: {
        "canvas_id": "managed-assets", "kind": "episode", "episode_id": "episode-object-3",
        "episode_number": 3, "status": "applied",
    })

    script_path = "episodes/003/script.md"
    script = client.put(PREFIX + "/canvases/managed-assets/documents", json={
        "path": script_path, "text": "第一场：雨夜。", "expected_hash": None,
    })
    assert script.status_code == 200, script.text
    canvas = client.post(PREFIX + "/canvases/managed-assets/patch", json={
        "base_revision": 1, "request_id": "screenplay-node", "operations": [{
            "op": "add_node", "node": {"id": "screenplay", "type": "screenplayNode",
                "data": {"operation": "plan", "episodeNumber": 3, "documentRevisionId": "source-rev-1"}},
        }],
    })
    assert canvas.status_code == 200, canvas.text

    progress_path = PREFIX + "/canvases/managed-assets/progress/003"
    screenplay = client.post(progress_path + "/complete", json={
        "stage": "screenplay", "input_hash": "source-hash-1", "outputs": {"script": script_path},
    })
    assert screenplay.status_code == 200, screenplay.text
    screenplay_approval = screenplay.json()["data"]["stages"]["screenplay"]["approval_hash"]
    approved = client.post(progress_path + "/approve", json={
        "stage": "screenplay", "input_hash": screenplay_approval,
    })
    assert approved.status_code == 200, approved.text

    asset_path = "episodes/003/assets.md"
    assets_doc = client.put(PREFIX + "/canvases/managed-assets/documents", json={
        "path": asset_path, "text": "已有资产清单。", "expected_hash": None,
    })
    assert assets_doc.status_code == 200, assets_doc.text
    assets = client.post(progress_path + "/complete", json={
        "stage": "assets", "input_hash": "asset-hash-1", "outputs": {"assets": asset_path},
    })
    assert assets.status_code == 200, assets.text
    assert assets.json()["data"]["stages"]["assets"]["status"] == "awaiting_review"

    patch = client.post(PREFIX + "/canvases/managed-assets/patch", json={
        "base_revision": 2, "request_id": "asset-reference", "operations": [
            {"op": "add_node", "node": {"id": "asset", "type": "imageGenNode",
                "data": {"operation": "generate", "episodeNumber": 3, "artifactKind": "asset",
                         "prompt": "scene reference"}}},
            {"op": "add_node", "node": {"id": "note", "type": "textAnnotationNode",
                "data": {"text": "non-execution context"}}},
            {"op": "add_edge", "edge": {"id": "asset-screenplay", "source": "asset",
                "target": "screenplay", "data": {}}},
            {"op": "add_edge", "edge": {"id": "context-screenplay", "source": "note",
                "target": "screenplay", "data": {"contextLink": True}}},
        ],
    })
    assert patch.status_code == 200, patch.text
    after_asset_reference = client.get(progress_path).json()["data"]["stages"]
    assert after_asset_reference["screenplay"]["status"] == "ready"
    assert after_asset_reference["screenplay"]["approval_hash"] == screenplay_approval
    assert after_asset_reference["assets"]["status"] == "pending"
    assert after_asset_reference["storyboard"]["status"] == "pending"

    changed_script = client.post(PREFIX + "/canvases/managed-assets/patch", json={
        "base_revision": 3, "request_id": "script-revision", "operations": [{
            "op": "update_node", "node_id": "screenplay",
            "data": {"documentRevisionId": "source-rev-2"},
        }],
    })
    assert changed_script.status_code == 200, changed_script.text
    after_script_change = client.get(progress_path).json()["data"]["stages"]
    assert after_script_change["screenplay"]["status"] == "pending"
    assert after_script_change["assets"]["status"] == "pending"


def test_progress_api_accepts_verified_script_and_storyboard_output_receipts(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes

    created = client.post(PREFIX + "/canvases", json={"canvas_id": "episode-canvas", "name": "episode"})
    assert created.status_code == 200, created.text
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, _canvas_id: {
        "canvas_id": "episode-canvas", "kind": "episode", "episode_id": "episode-object-3",
        "episode_number": 3, "status": "applied",
    })
    base = PREFIX + "/canvases/episode-canvas/progress/003"

    script = "第一场：雨夜，主角推门进入。"
    script_doc = client.put(PREFIX + "/canvases/episode-canvas/documents", json={
        "path": "episodes/003/script.md", "text": script, "expected_hash": None,
    })
    assert script_doc.status_code == 200, script_doc.text
    script_ref = {
        "path": "episodes/003/script.md",
        "sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
    }
    screenplay = client.post(base + "/complete", json={
        "stage": "screenplay", "input_hash": "source-revision-1", "outputs": {"script": script_ref},
    })
    assert screenplay.status_code == 200, screenplay.text
    assert screenplay.json()["data"]["stages"]["screenplay"]["outputs"]["script"] == script_ref

    stale = client.post(base + "/complete", json={
        "stage": "screenplay", "input_hash": "source-revision-1",
        "outputs": {"script": {**script_ref, "sha256": "0" * 64}},
    })
    assert stale.status_code == 422, stale.text
    unchanged = client.get(base)
    assert unchanged.json()["data"]["stages"]["screenplay"]["outputs"]["script"] == script_ref

    table_text = "镜号 | 景别 | 画面\n01 | 中景 | 主角推门"
    table_doc = client.put(PREFIX + "/canvases/episode-canvas/documents", json={
        "path": "episodes/003/storyboard/table.md", "text": table_text, "expected_hash": None,
    })
    assert table_doc.status_code == 200, table_doc.text
    grid_path = "episodes/003/storyboard/all-grid.png"
    grid_bytes = b"real storyboard grid bytes"
    grid_file = client.app.state.project_dir / grid_path
    grid_file.parent.mkdir(parents=True, exist_ok=True)
    grid_file.write_bytes(grid_bytes)
    storyboard = client.post(base + "/complete", json={
        "stage": "storyboard", "input_hash": "board-revision-1", "outputs": {
            "table": {
                "path": "episodes/003/storyboard/table.md",
                "sha256": hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
            },
            "grid": {
                "path": grid_path,
                "sha256": hashlib.sha256(grid_bytes).hexdigest(),
            },
        },
    })
    assert storyboard.status_code == 200, storyboard.text
    assert storyboard.json()["data"]["stages"]["storyboard"]["status"] == "awaiting_review"
    assert storyboard.json()["data"]["stages"]["storyboard"]["outputs"] == {
        "table": {
            "path": "episodes/003/storyboard/table.md",
            "sha256": hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
        },
        "grid": {"path": grid_path, "sha256": hashlib.sha256(grid_bytes).hexdigest()},
    }


def test_managed_video_run_waits_for_current_storyboard_approval(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.creation.production_progress import ProductionProgress

    client.post(PREFIX + "/canvases", json={"canvas_id": "managed", "name": "managed"})
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, canvas_id: {
        "canvas_id": canvas_id, "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "applied",
    })
    client.post(PREFIX + "/canvases/managed/patch", json={
        "base_revision": 1, "request_id": "nodes", "operations": [
            {"op": "add_node", "node": {"id": "video", "type": "videoDirectorNode",
                "data": {"operation": "generate", "episodeNumber": 3}}},
        ],
    })
    progress = ProductionProgress(client.app.state.project_dir,
                                  output_root=client.app.state.project_dir)
    progress.configure("003", mode="review")

    def complete(stage, outputs):
        for path in outputs.values():
            target = client.app.state.project_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stage, encoding="utf-8")
        return progress.complete("003", stage, f"untrusted-{stage}-input", outputs)

    screenplay = complete("screenplay", {"script": "episodes/003/script.md"})
    progress.approve("003", "screenplay", screenplay["stages"]["screenplay"]["approval_hash"])
    assets = complete("assets", {"reuse_checklist": "episodes/003/assets.md"})
    progress.approve("003", "assets", assets["stages"]["assets"]["approval_hash"])
    complete("storyboard", {
        "table": "episodes/003/storyboard.json",
        "grid": "episodes/003/storyboard.png",
    })

    response = client.post(PREFIX + "/runs", json={
        "canvas_id": "managed", "target": "video", "request_id": "video-run",
    })

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "production_stage_blocked"
    assert detail["stage"] == "video"
    assert detail["required_stage"] == "storyboard"
    assert detail["status"] == "awaiting_review"
    assert detail["reason"] == "awaiting_review"
    assert client.get(PREFIX + "/canvases/managed/runs").json()["data"] == []


def test_managed_run_gates_every_external_ancestor_before_target_audio(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.creation.production_progress import ProductionProgress

    client.post(PREFIX + "/canvases", json={"canvas_id": "managed-chain", "name": "managed"})
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, canvas_id: {
        "canvas_id": canvas_id, "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "applied",
    })
    client.post(PREFIX + "/canvases/managed-chain/patch", json={
        "base_revision": 1, "request_id": "nodes", "operations": [
            {"op": "add_node", "node": {"id": "video", "type": "videoNode",
                "data": {"operation": "generate", "episodeNumber": 3, "prompt": "walk"}}},
            {"op": "add_node", "node": {"id": "audio", "type": "videoNode",
                "data": {"operation": "separate_audio", "episodeNumber": 3}}},
            {"op": "add_edge", "edge": {"id": "video-audio", "source": "video", "target": "audio"}},
        ],
    })
    progress = ProductionProgress(client.app.state.project_dir,
                                  output_root=client.app.state.project_dir)
    progress.configure("003", mode="review")
    for stage, outputs in (
        ("screenplay", {"script": "episodes/003/script.md"}),
        ("assets", {"reuse_checklist": "episodes/003/assets.md"}),
    ):
        for path in outputs.values():
            target = client.app.state.project_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(stage, encoding="utf-8")
        state = progress.complete("003", stage, f"untrusted-{stage}", outputs)
        progress.approve("003", stage, state["stages"][stage]["approval_hash"])
    for path in ("episodes/003/storyboard.json", "episodes/003/storyboard.png"):
        target = client.app.state.project_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("storyboard", encoding="utf-8")
    progress.complete("003", "storyboard", "untrusted-storyboard", {
        "table": "episodes/003/storyboard.json", "grid": "episodes/003/storyboard.png",
    })

    response = client.post(PREFIX + "/runs", json={
        "canvas_id": "managed-chain", "target": "audio", "operation": "separate_audio",
        "request_id": "audio-after-video",
    })

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "production_stage_blocked"
    assert detail["stage"] == "video"
    assert detail["required_stage"] == "storyboard"
    assert detail["reason"] == "awaiting_review"
    assert client.get(PREFIX + "/canvases/managed-chain/runs").json()["data"] == []


def test_progress_configuration_is_opt_in_for_episode_experiments(client, monkeypatch):
    from novelvideo.api.routes import freezone_orchestration as routes

    client.post(PREFIX + "/canvases", json={"canvas_id": "unmanaged", "name": "unmanaged"})
    monkeypatch.setattr(routes, "resolve_canvas_scope", lambda _state_dir, canvas_id: {
        "canvas_id": canvas_id, "kind": "episode", "episode_id": "episode-3",
        "episode_number": 3, "status": "applied",
    })
    client.post(PREFIX + "/canvases/unmanaged/patch", json={
        "base_revision": 1, "request_id": "nodes", "operations": [
            {"op": "add_node", "node": {"id": "script", "type": "screenplayNode",
                "data": {"operation": "inspect", "episodeNumber": 3, "referenceOnly": True}}},
        ],
    })

    response = client.post(PREFIX + "/runs", json={
        "canvas_id": "unmanaged", "target": "script", "request_id": "experiment",
    })

    assert response.status_code == 202, response.text
