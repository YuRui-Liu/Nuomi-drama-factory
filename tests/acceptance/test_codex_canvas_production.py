"""Integrated Codex canvas production flow using local services and a fake provider."""

from __future__ import annotations

import pytest


def _write_bytes(root, relative_path: str, data: bytes) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.mark.asyncio
async def test_codex_production_documents_review_checkpoints_auto_and_recovery(tmp_path):
    from novelvideo.creation.production_documents import ProductionDocuments
    from novelvideo.creation.production_progress import ProductionProgress, should_pause_after
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.models import fingerprint
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph, project_result
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    state_root.mkdir()
    project_root.mkdir()
    documents = ProductionDocuments(project_root)
    progress = ProductionProgress(state_root, output_root=project_root)

    # The screenplay has one authoritative file. Progress accepts its real hash,
    # and each review checkpoint approves exactly the result just recorded.
    create_canvas(state_root, "episode-003", project_id="p", name="E03", actor_id="codex")
    patch_graph(state_root, "episode-003", base_revision=1, request_id="seed-episode-003", operations=[
        {"op": "add_node", "node": {
            "id": "screenplay", "type": "scriptNode", "position": {"x": 0, "y": 0},
            "data": {"operation": "generate", "prompt": "episode 003"},
        }},
        {"op": "add_node", "node": {
            "id": "assets", "type": "imageGenNode", "position": {"x": 400, "y": 0},
            "data": {"operation": "generate", "prompt": "episode 003 look"},
        }},
        {"op": "add_node", "node": {
            "id": "storyboard", "type": "storyboardNode", "position": {"x": 800, "y": 0},
            "data": {"operation": "split"},
        }},
        {"op": "add_edge", "edge": {
            "id": "screenplay-assets", "source": "screenplay", "target": "assets",
            "data": {"sourcePort": "text", "targetPort": "text"},
        }},
        {"op": "add_edge", "edge": {
            "id": "assets-storyboard", "source": "assets", "target": "storyboard",
            "data": {"sourcePort": "image", "targetPort": "image"},
        }},
    ])
    canvas = read_canvas(state_root, "episode-003")

    class FakeProvider:
        """Models provider receipts and one settled failure without network calls."""

        def __init__(self):
            self.submissions: list[str] = []

        async def submit(self, run, attempt, upstream):
            self.submissions.append(attempt["node_id"])
            return {"fake_provider": True, "task_id": attempt["id"]}

        async def reconcile(self, run, attempt):
            node_id = attempt["node_id"]
            if node_id == "assets" and attempt["number"] == 1:
                return {"status": "failed", "error": {"code": "fake_provider_failure"}}
            if node_id == "screenplay":
                written = documents.write(
                    "episodes/003/script.md", "夜班列车停在旧车站。", expected_hash=None,
                )
                return {"status": "succeeded", "result": {
                    "scriptTitle": "旧车站", "scriptResult": {"path": written["path"], "hash": written["hash"]},
                }}
            if node_id == "assets":
                documents.write("episodes/003/assets.md", "复用清单：沿用旧车站站牌。", expected_hash=None)
                _write_bytes(project_root, "episodes/003/asset.png", b"fake-image")
                return {"status": "succeeded", "result": {"imageUrl": "episodes/003/asset.png"}}
            assert node_id == "storyboard"
            _write_bytes(project_root, "episodes/003/storyboard.json", b'{"shots":["station"]}')
            _write_bytes(project_root, "episodes/003/storyboard-grid.png", b"fake-grid")
            return {"status": "succeeded", "result": {"frames": [{"shotId": "station"}]}}

        async def verify(self, run, attempt):
            return [{"kind": "json", "sha256": fingerprint(attempt["result"])}]

        async def project(self, run, attempt):
            node_id = attempt["node_id"]
            project_result(
                state_root,
                run["canvas_id"],
                node_id=node_id,
                expected_input=run["input_fingerprints"][node_id],
                attempt_id=attempt["id"],
                result=attempt["result"],
            )

    store = RunStore(state_root / "runs")
    run = store.create(
        canvas,
        target="storyboard",
        request_id="episode-003-run",
        project_id="p",
        settings={"concurrency": 1},
        actor={"id": "codex"},
    )
    provider = FakeProvider()
    scheduler = Scheduler(store, provider)

    for _ in range(12):
        run = await scheduler.tick(run["id"])
        if run["status"] in {"succeeded", "failed", "blocked", "submission_unknown"}:
            break
    assert run["status"] == "failed"
    assert run["attempts"]["screenplay"][-1]["status"] == "succeeded"
    assert run["attempts"]["assets"][-1]["status"] == "failed"

    reviewed = progress.get("003")
    script = documents.read("episodes/003/script.md")
    reviewed = progress.complete("003", "screenplay", script["hash"], {"script": script["path"]})
    assert reviewed["stages"]["screenplay"]["status"] == "awaiting_review"
    assert should_pause_after(reviewed, "screenplay")
    reviewed = progress.approve(
        "003", "screenplay", reviewed["stages"]["screenplay"]["approval_hash"],
    )
    assert reviewed["stages"]["screenplay"]["status"] == "ready"

    failed_progress = progress.fail("003", "assets", "fake provider failed", input_hash="assets-input-v1")
    assert failed_progress["stages"]["assets"]["status"] == "failed"
    retry = store.retry_failed(run["id"], request_id="episode-003-retry-assets")
    assert retry["attempts"]["screenplay"][-1]["status"] == "succeeded"
    assert retry["attempts"]["assets"][-1]["explicit_retry"] is True
    for _ in range(12):
        run = await scheduler.tick(run["id"])
        if run["status"] in {"succeeded", "failed", "blocked", "submission_unknown"}:
            break
    assert run["status"] == "succeeded"
    assert provider.submissions == ["screenplay", "assets", "assets", "storyboard"]
    assert len(run["attempts"]["screenplay"]) == 1

    assets = progress.complete(
        "003", "assets", fingerprint(run["attempts"]["assets"][-1]["result"]),
        {"reuse_checklist": "episodes/003/assets.md"},
    )
    assert assets["stages"]["assets"]["status"] == "awaiting_review"
    assert should_pause_after(assets, "assets")
    assets = progress.approve("003", "assets", assets["stages"]["assets"]["approval_hash"])
    storyboard = progress.complete(
        "003", "storyboard", fingerprint(run["attempts"]["storyboard"][-1]["result"]),
        {"table": "episodes/003/storyboard.json", "grid": "episodes/003/storyboard-grid.png"},
    )
    assert storyboard["stages"]["storyboard"]["status"] == "awaiting_review"
    assert should_pause_after(storyboard, "storyboard")
    storyboard = progress.approve(
        "003", "storyboard", storyboard["stages"]["storyboard"]["approval_hash"],
    )
    assert storyboard["stages"]["storyboard"]["status"] == "ready"

    final_canvas = read_canvas(state_root, "episode-003")
    assert {node["id"] for node in final_canvas["nodes"]} == {"screenplay", "assets", "storyboard"}
    assert next(node for node in final_canvas["nodes"] if node["id"] == "screenplay")["data"]["scriptTitle"] == "旧车站"
    assert next(node for node in final_canvas["nodes"] if node["id"] == "assets")["data"]["imageUrl"] == "episodes/003/asset.png"

    # A second episode in auto mode records the same three stages as ready with
    # no review pause. It still uses the same on-disk documents and output checks.
    progress.configure("004", mode="auto")
    script_004 = documents.write("episodes/004/script.md", "雨停后，主人公离开车站。", expected_hash=None)
    assets_004 = documents.write("episodes/004/assets.md", "复用清单：蓝色雨伞。", expected_hash=None)
    _write_bytes(project_root, "episodes/004/storyboard.json", b'{"shots":["exit"]}')
    _write_bytes(project_root, "episodes/004/storyboard-grid.png", b"fake-grid-004")
    auto = progress.complete("004", "screenplay", script_004["hash"], {"script": script_004["path"]})
    assert not should_pause_after(auto, "screenplay")
    auto = progress.complete("004", "assets", assets_004["hash"], {"reuse_checklist": assets_004["path"]})
    assert not should_pause_after(auto, "assets")
    auto = progress.complete(
        "004", "storyboard", "storyboard-004",
        {"table": "episodes/004/storyboard.json", "grid": "episodes/004/storyboard-grid.png"},
    )
    assert [auto["stages"][stage]["status"] for stage in ("screenplay", "assets", "storyboard")] == [
        "ready", "ready", "ready",
    ]
    assert not should_pause_after(auto, "storyboard")

    bounded = progress.configure("005", mode="auto", stop_after="storyboard")
    _write_bytes(project_root, "episodes/005/storyboard.json", b'{"shots":["end"]}')
    _write_bytes(project_root, "episodes/005/storyboard-grid.png", b"fake-grid-005")
    bounded = progress.complete(
        "005", "storyboard", "storyboard-005",
        {"table": "episodes/005/storyboard.json", "grid": "episodes/005/storyboard-grid.png"},
    )
    assert bounded["stages"]["storyboard"]["status"] == "ready"
    assert should_pause_after(bounded, "storyboard")
