import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from novelvideo.freezone.orchestration.graph import execution_order


def test_frozen_asset_reference_stops_dependency_execution_at_the_verified_source():
    nodes = [
        {"id": "source_prompt", "type": "textAnnotationNode", "data": {"text": "source"}},
        {"id": "reference_image", "type": "imageGenNode", "data": {"imageUrl": "/image.png"}},
        {"id": "other_input", "type": "textAnnotationNode", "data": {"text": "other"}},
        {"id": "asset_image", "type": "imageGenNode", "data": {"operation": "generate"}},
    ]
    edges = [
        {"id": "prompt-reference", "source": "source_prompt", "target": "reference_image"},
        {"id": "reference-asset", "source": "reference_image", "target": "asset_image"},
        {"id": "other-asset", "source": "other_input", "target": "asset_image"},
    ]

    order = execution_order(nodes, edges, "asset_image", operation="generate",
                            frozen_sources={"reference_image"})

    assert order == ["other_input", "asset_image"]
    # The old source's persisted node remains untouched; the special case is
    # only an execution-order cut for a server-verified asset-design receipt.
    assert "referenceOnly" not in nodes[1]["data"]
    assert execution_order(nodes, edges, "asset_image", operation="generate") == [
        "source_prompt", "other_input", "reference_image", "asset_image"
    ]


def _asset_canvas():
    return {
        "canvas_id": "canvas",
        "revision": 4,
        "nodes": [
            {"id": "source", "type": "imageGenNode", "data": {
                "imageUrl": "/static/projects/p/reference.png",
            }},
            {"id": "asset", "type": "imageGenNode", "data": {
                "operation": "generate",
                "prompt": "new design",
                "designBinding": {"references": [{
                    "kind": "canvas_node", "canvas_id": "canvas", "node_id": "source",
                    "node_type": "imageGenNode", "input_fingerprint": "verified-reference-fingerprint",
                }]},
            }},
        ],
        "edges": [{"id": "source-asset", "source": "source", "target": "asset"}],
    }


def test_asset_design_run_reuses_only_verified_source_without_changing_canvas_node(tmp_path, monkeypatch):
    from novelvideo.creation.asset_generation import AssetGeneration
    from novelvideo.freezone.orchestration.asset_design_dependencies import prepare_asset_design_execution
    from novelvideo.freezone.orchestration.models import fingerprint, frozen_node_result
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _asset_canvas()
    candidate = {
        "canvas_id": "canvas", "node_id": "asset", "node_type": "imageGenNode",
        "operation": "generate", "max_node_runs": 1,
        "source_operation_id": "asset-design-operation", "request_id": "asset-design-request",
    }
    monkeypatch.setattr(AssetGeneration, "verified_candidate_for_node",
                        classmethod(lambda cls, ctx, canvas_id, node: candidate))

    marker = prepare_asset_design_execution(object(), canvas, "asset", "generate")
    run = RunStore(tmp_path).create(canvas, target="asset", request_id="asset-run", project_id="p",
        settings={"concurrency": 2}, actor={"id": "u"}, operation="generate",
        asset_design_execution=marker)

    assert run["order"] == ["asset"]
    assert run["busy_node_ids"] == ["asset", "source"]
    assert run["asset_design_execution"]["candidate"] == candidate
    assert run["asset_design_execution"]["source_output_fingerprints"]["source"] == fingerprint(
        frozen_node_result(canvas["nodes"][0]))
    assert "referenceOnly" not in canvas["nodes"][0]["data"]
    assert "referenceOnly" not in run["snapshot"]["nodes"][0]["data"]


@pytest.mark.asyncio
async def test_scheduler_injects_frozen_asset_reference_and_never_submits_its_source(tmp_path, monkeypatch):
    from novelvideo.creation.asset_generation import AssetGeneration
    from novelvideo.freezone.orchestration.asset_design_dependencies import prepare_asset_design_execution
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _asset_canvas()
    candidate = {
        "canvas_id": "canvas", "node_id": "asset", "node_type": "imageGenNode",
        "operation": "generate", "max_node_runs": 1,
        "source_operation_id": "asset-design-operation", "request_id": "asset-design-request",
    }
    monkeypatch.setattr(AssetGeneration, "verified_candidate_for_node",
                        classmethod(lambda cls, ctx, canvas_id, node: candidate))
    marker = prepare_asset_design_execution(object(), canvas, "asset", "generate")

    class Adapter:
        def __init__(self):
            self.submitted = []
            self.verifications = 0

        async def verify_asset_design_execution(self, run):
            self.verifications += 1

        async def submit(self, run, attempt, upstream):
            self.submitted.append(attempt["node_id"])
            assert [item["node_id"] for item in upstream] == ["source"]
            assert upstream[0]["result"]["imageUrl"] == "/static/projects/p/reference.png"
            return {"task_id": "provider-task"}

        async def reconcile(self, run, attempt):
            return {"status": "succeeded", "result": {"imageUrl": "/static/projects/p/generated.png"}}

        async def verify(self, run, attempt):
            return [{"kind": "image", "sha256": "verified-output"}]

        async def project(self, run, attempt):
            return None

    store = RunStore(tmp_path)
    run = store.create(canvas, target="asset", request_id="asset-run", project_id="p",
        settings={"concurrency": 1}, actor={"id": "u"}, operation="generate",
        asset_design_execution=marker)
    adapter = Adapter()
    scheduler = Scheduler(store, adapter)

    await scheduler.tick(run["id"])
    completed = await scheduler.tick(run["id"])

    assert adapter.submitted == ["asset"]
    assert adapter.verifications == 2  # immediately before submit and before projection
    assert completed["status"] == "succeeded"


@pytest.mark.asyncio
async def test_scheduler_blocks_asset_generation_when_frozen_receipt_changes_before_submit(tmp_path, monkeypatch):
    from novelvideo.creation.asset_generation import AssetGeneration
    from novelvideo.freezone.orchestration.asset_design_dependencies import prepare_asset_design_execution
    from novelvideo.freezone.orchestration.models import OrchestrationError
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    canvas = _asset_canvas()
    candidate = {
        "canvas_id": "canvas", "node_id": "asset", "node_type": "imageGenNode",
        "operation": "generate", "max_node_runs": 1,
        "source_operation_id": "asset-design-operation", "request_id": "asset-design-request",
    }
    monkeypatch.setattr(AssetGeneration, "verified_candidate_for_node",
                        classmethod(lambda cls, ctx, canvas_id, node: candidate))
    marker = prepare_asset_design_execution(object(), canvas, "asset", "generate")

    class InvalidatedAdapter:
        submitted = False

        async def verify_asset_design_execution(self, run):
            raise OrchestrationError("input_conflict", "frozen reference changed")

        async def submit(self, run, attempt, upstream):
            self.submitted = True
            return {"task_id": "must-not-submit"}

    store = RunStore(tmp_path)
    run = store.create(canvas, target="asset", request_id="asset-run", project_id="p",
        settings={"concurrency": 1}, actor={"id": "u"}, operation="generate",
        asset_design_execution=marker)
    adapter = InvalidatedAdapter()
    blocked = await Scheduler(store, adapter).tick(run["id"])

    assert adapter.submitted is False
    assert blocked["attempts"]["asset"][-1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_real_asset_design_receipt_generates_from_frozen_image_without_resubmitting_source(
        tmp_path, monkeypatch):
    from novelvideo.creation.asset_generation import AssetGeneration
    from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.asset_design_dependencies import (
        prepare_asset_design_execution, verify_asset_design_execution,
    )
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.paths import canvas_path

    state_dir = tmp_path / "state"
    output_dir = tmp_path / "media"
    state_dir.mkdir()
    output_dir.mkdir()
    ctx = SimpleNamespace(project_id="p", requester_user_id="alice",
                          state_dir=state_dir, output_dir=output_dir)
    image_path = output_dir / "assets" / "reference.png"
    image_path.parent.mkdir()
    Image.new("RGB", (16, 12), color=(30, 90, 140)).save(image_path)
    image_url = "/static/projects/p/assets/reference.png"

    objects = ObjectStore(state_dir)
    created = CanvasWorkspace(objects, project_id="p").create(
        "alice", CanvasCreate(request_id="asset-design-source-canvas", kind="concept", title="资产设计"))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]["canvas_id"]
    canvas = read_canvas(state_dir, canvas_id)
    canvas["nodes"] = [
        {"id": "old-prompt", "type": "textAnnotationNode", "data": {"content": "旧图提示词"}},
        {"id": "old-image", "type": "imageGenNode", "data": {
            "operation": "generate", "prompt": "旧图提示词", "imageUrl": image_url,
        }},
    ]
    canvas["edges"] = [{"id": "old-prompt-image", "source": "old-prompt", "target": "old-image"}]
    canvas["revision"] += 1
    canvas_path(state_dir, canvas_id).write_text(json.dumps(canvas), encoding="utf-8")
    original_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()

    prepared = AssetGeneration(ctx).prepare("alice", {
        "request_id": "asset-design-with-frozen-reference",
        "canvas_id": canvas_id,
        "expected_canvas_revision": canvas["revision"],
        "kind": "character",
        "name": "林岚",
        "description": "夜班记者",
        "prompt": "保留参考图的脸部身份",
        "source_proposal": {"title": "人物设定", "text": "林岚是夜班记者。"},
        "references": [{"kind": "canvas_node", "node_id": "old-image"}],
    })
    assert prepared["projection_status"] == "applied"
    live_canvas = read_canvas(state_dir, canvas_id)
    marker = prepare_asset_design_execution(
        ctx, live_canvas, prepared["image_node_id"], "generate")

    class RequestAdapter:
        def __init__(self):
            self.submitted = []
            self.reference_urls = []

        async def verify_asset_design_execution(self, run):
            verify_asset_design_execution(ctx, run, read_canvas(state_dir, canvas_id))

        async def submit(self, run, attempt, upstream):
            node = next(item for item in run["snapshot"]["nodes"] if item["id"] == attempt["node_id"])
            _, request = build_request(node, "generate", upstream,
                                       canvas_id=canvas_id, attempt_id=attempt["id"])
            self.submitted.append(attempt["node_id"])
            self.reference_urls = request["reference_urls"]
            return {"task_id": "paid-call-fixture"}

        async def reconcile(self, run, attempt):
            return {"status": "succeeded", "result": {"imageUrl": "/static/projects/p/generated.png"}}

        async def verify(self, run, attempt):
            return [{"kind": "image", "sha256": "generated-fixture"}]

        async def project(self, run, attempt):
            return None

    store = RunStore(tmp_path / "run-state")
    run = store.create(live_canvas, target=prepared["image_node_id"],
        request_id="asset-image-run", project_id="p", settings={"concurrency": 1},
        actor={"id": "alice"}, operation="generate", asset_design_execution=marker)
    adapter = RequestAdapter()
    scheduler = Scheduler(store, adapter)
    await scheduler.tick(run["id"])
    completed = await scheduler.tick(run["id"])

    assert run["order"] == [prepared["image_node_id"]]
    assert adapter.submitted == [prepared["image_node_id"]]
    assert adapter.reference_urls == [image_url]
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == original_sha
    assert completed["status"] == "succeeded"

    # The adapter's canvas CAS fence checks the actual reference media digest,
    # not only the source URL stored on its node.
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.models import OrchestrationError

    async def resolve_project(project_id, actor, required_role="editor"):
        assert project_id == "p" and actor["id"] == "alice" and required_role == "viewer"
        return ctx, "alice", "test", state_dir, str(output_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    Image.new("RGB", (16, 12), color=(200, 10, 20)).save(image_path)
    with pytest.raises(OrchestrationError) as conflict:
        await NodeAdapter(state_dir).project(run, {
            "id": "projection-attempt", "node_id": prepared["image_node_id"],
            "result": {"imageUrl": "/static/projects/p/generated.png"},
        })
    assert conflict.value.code == "asset_design_binding_unverified"
    projected = read_canvas(state_dir, canvas_id)
    image_node = next(node for node in projected["nodes"] if node["id"] == prepared["image_node_id"])
    assert "imageUrl" not in image_node["data"]
