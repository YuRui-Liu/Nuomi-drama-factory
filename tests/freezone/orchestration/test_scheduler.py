import pytest


def graph():
    return {"canvas_id": "canvas", "revision": 1, "nodes": [
        {"id": key, "type": "textAnnotationNode", "data": {"text": key}}
        for key in ("a", "b", "c")], "edges": [
            {"id": "ab", "source": "a", "target": "b"},
            {"id": "bc", "source": "b", "target": "c"}]}


class Adapter:
    def __init__(self):
        self.submits = []
        self.verifications = []
        self.projections = []
        self.unknown = False
        self.fail = set()
        self.conflict = False

    async def submit(self, run, attempt, upstream):
        self.submits.append(attempt["node_id"])
        if self.unknown:
            raise TimeoutError("lost response")
        return {"task_id": attempt["id"]}

    async def reconcile(self, run, attempt):
        if attempt["node_id"] in self.fail:
            return {"status": "failed", "error": {"code": "provider_error"}}
        return {"status": "succeeded", "result": {"text": "real fixture result"}}

    async def verify(self, run, attempt):
        self.verifications.append(attempt["node_id"])
        return [{"kind": "text", "sha256": "fixture"}]

    async def project(self, run, attempt):
        from novelvideo.freezone.orchestration.models import OrchestrationError
        if self.conflict:
            raise OrchestrationError("input_conflict", "input changed")
        self.projections.append(attempt["node_id"])


def new_run(tmp_path, canvas=None):
    from novelvideo.freezone.orchestration.store import RunStore
    store = RunStore(tmp_path)
    run = store.create(canvas or graph(), target="c", request_id="request", project_id="p",
                       settings={"concurrency": 3}, actor={"id": "u", "username": "u"})
    return store, run


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt_key', ['task_id', 'candidate_id', 'dubbing_task_id', 'workflow_id'])
async def test_unknown_submission_recovers_durable_receipt_without_resubmitting(tmp_path, receipt_key):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    class RecoverableAdapter(Adapter):
        async def reconcile_submission(self, run, attempt):
            return {'status': 'submitted', 'submission': {receipt_key: 'durable-task'}}
    adapter = RecoverableAdapter()
    adapter.unknown = True
    scheduler = Scheduler(store, adapter)
    unknown = await scheduler.tick(run['id'])
    assert unknown['attempts']['a'][-1]['status'] == 'submission_unknown'
    adapter.unknown = False
    recovered = await scheduler.tick(run['id'])
    assert recovered['attempts']['a'][-1]['status'] == 'succeeded'
    assert recovered['attempts']['a'][-1]['submission'][receipt_key] == 'durable-task'
    assert adapter.submits.count('a') == 1


def test_explicit_image_generation_overrides_reuse_only_for_target(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore
    canvas = graph()
    for node in canvas['nodes']:
        node['type'] = 'storyboardGenNode'
        node['data']['referenceOnly'] = True
    run = RunStore(tmp_path).create(canvas, target='c', request_id='regenerate', project_id='p',
        settings={}, actor={'id':'u'}, operation='generate')
    assert [n['data']['referenceOnly'] for n in run['snapshot']['nodes']] == [True, True, False]
    assert all(n['data']['referenceOnly'] for n in canvas['nodes'])


@pytest.mark.asyncio
async def test_typed_unknown_submission_cannot_create_a_new_paid_attempt(tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)

    class ReservedAdapter(Adapter):
        async def submit(self, run, attempt, upstream):
            self.submits.append(attempt['node_id'])
            raise OrchestrationError('submission_unknown', 'Reservation exists without a task receipt')

    adapter = ReservedAdapter()
    scheduler = Scheduler(store, adapter)
    first = await scheduler.tick(run['id'])
    assert first['attempts']['a'][-1]['status'] == 'submission_unknown'
    with pytest.raises(OrchestrationError, match='Only a failed, settled run'):
        store.retry_failed(run['id'], request_id='retry')
    await scheduler.tick(run['id'])
    assert adapter.submits == ['a']
    assert len(store.get(run['id'])['attempts']['a']) == 1


@pytest.mark.asyncio
async def test_cancel_releases_verified_output_conflict_without_overwriting_canvas(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    adapter.conflict = True
    scheduler = Scheduler(store, adapter)
    await scheduler.tick(run['id'])
    blocked = await scheduler.tick(run['id'])
    assert blocked['attempts']['a'][-1]['status'] == 'verifying'
    blocked['cancel_requested'] = True
    store.save(blocked, 'cancel_requested')
    result = await scheduler.tick(run['id'])
    assert result['status'] == 'cancelled'
    assert result['attempts']['a'][-1]['artifacts']
    assert adapter.projections == []
    assert adapter.submits == ['a']


def test_run_create_is_idempotent_and_rejects_other_live_run(tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError
    store, run = new_run(tmp_path)
    assert new_run(tmp_path)[1]["id"] == run["id"]
    with pytest.raises(OrchestrationError, match="active"):
        store.create(graph(), target="c", request_id="other", project_id="p", settings={}, actor={"id": "u"})


@pytest.mark.asyncio
async def test_independent_runs_on_one_canvas_can_submit_concurrently(tmp_path):
    import asyncio
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    board = graph()
    board['edges'] = []
    store = RunStore(tmp_path)
    runs = [store.create(board, target=target, request_id=target, project_id='p', settings={}, actor={'id':'u'}) for target in ('a','b')]
    both = asyncio.Event()
    entered = []
    class SlowAdapter(Adapter):
        async def submit(self, run, attempt, upstream):
            entered.append(attempt['node_id'])
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return {'task_id':attempt['id']}
    scheduler = Scheduler(store, SlowAdapter())
    results = await asyncio.gather(*(scheduler.tick(run['id']) for run in runs))
    assert sorted(entered) == ['a', 'b']
    assert all(run['status'] == 'running' for run in results)


@pytest.mark.asyncio
async def test_restart_reconciles_submitted_attempt_without_resubmitting(tmp_path):
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    await Scheduler(store, adapter).tick(run["id"])
    assert adapter.submits == ["a"]
    restarted = Scheduler(RunStore(tmp_path), adapter)
    for _ in range(8):
        await restarted.tick(run["id"])
    final = store.get(run["id"])
    assert final["status"] == "succeeded"
    assert adapter.submits == ["a", "b", "c"]
    assert adapter.verifications == adapter.projections == ["a", "b", "c"]
    assert len(store.events(run["id"])) >= 6


@pytest.mark.asyncio
async def test_unknown_submission_never_retries_on_resume(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    adapter.unknown = True
    for _ in range(3):
        await Scheduler(store, adapter).tick(run["id"])
    assert adapter.submits == ["a"]
    assert store.get(run["id"])["attempts"]["a"][-1]["status"] == "submission_unknown"


@pytest.mark.asyncio
async def test_known_queue_capacity_rejection_waits_without_unknown_submission(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.models import OrchestrationError
    store, run = new_run(tmp_path)
    class Busy(Adapter):
        async def submit(self, run, attempt, upstream):
            raise OrchestrationError("capacity_wait", "lane full before enqueue")
    result = await Scheduler(store, Busy()).tick(run["id"])
    assert result["attempts"]["a"][-1]["status"] == "pending"
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_projection_retry_does_not_generate_again(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    adapter.conflict = True
    scheduler = Scheduler(store, adapter)
    await scheduler.tick(run["id"])
    await scheduler.tick(run["id"])
    assert store.get(run["id"])["attempts"]["a"][-1]["status"] == "verifying"
    adapter.conflict = False
    for _ in range(7):
        await scheduler.tick(run["id"])
    assert adapter.submits == ["a", "b", "c"]
    assert store.get(run["id"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_stale_verified_result_is_retained_without_replacing_current_node(tmp_path):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.projection import (
        create_canvas, patch_graph, project_result,
    )
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.orchestration.models import input_fingerprint

    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[
        {"op": "add_node", "node": {
            "id": "image", "type": "imageGenNode", "data": {"prompt": "a cat"},
            "position": {"x": 0, "y": 0},
        }},
    ])
    original = read_canvas(tmp_path, "canvas")
    original_node = original["nodes"][0]
    project_result(
        tmp_path,
        "canvas",
        node_id="image",
        expected_input=input_fingerprint(original_node),
        attempt_id="attempt-prior",
        result={"imageUrl": "/output/current.png"},
    )
    store = RunStore(tmp_path)
    run = store.create(read_canvas(tmp_path, "canvas"), target="image", request_id="image-run",
                       project_id="p", settings={"concurrency": 1}, actor={"id": "u"})
    patch_graph(tmp_path, "canvas", base_revision=3, request_id="change-input", operations=[
        {"op": "update_node", "node_id": "image", "data": {"prompt": "a dog"}},
    ])

    class ImageAdapter:
        async def submit(self, run, attempt, upstream):
            return {"task_id": attempt["id"]}

        async def reconcile(self, run, attempt):
            return {"status": "succeeded", "result": {"imageUrl": "/output/old-input.png"}}

        async def verify(self, run, attempt):
            return [{"kind": "image", "sha256": "verified"}]

        async def project(self, run, attempt):
            return project_result(
                tmp_path,
                run["canvas_id"],
                node_id="image",
                expected_input=run["input_fingerprints"]["image"],
                attempt_id=attempt["id"],
                result=attempt["result"],
            )

    scheduler = Scheduler(store, ImageAdapter())
    await scheduler.tick(run["id"])
    completed = await scheduler.tick(run["id"])

    saved = read_canvas(tmp_path, "canvas")
    node = next(item for item in saved["nodes"] if item["id"] == "image")
    attempt = completed["attempts"]["image"][-1]
    assert completed["status"] == "succeeded"
    assert attempt["status"] == "succeeded"
    assert attempt["projection_status"] == "stale"
    assert attempt["result"]["imageUrl"] == "/output/old-input.png"
    assert node["data"]["prompt"] == "a dog"
    assert node["data"]["imageUrl"] == "/output/current.png"
    assert node["data"]["orchestration"]["status"] == "stale"
    assert [item["id"] for item in saved["nodes"]] == ["image"]


@pytest.mark.asyncio
async def test_failed_new_task_preserves_existing_media(tmp_path):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph, project_result
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.orchestration.models import input_fingerprint

    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[
        {"op": "add_node", "node": {
            "id": "image", "type": "imageGenNode", "data": {"prompt": "a cat"},
            "position": {"x": 0, "y": 0},
        }},
    ])
    canvas = read_canvas(tmp_path, "canvas")
    project_result(
        tmp_path,
        "canvas",
        node_id="image",
        expected_input=input_fingerprint(canvas["nodes"][0]),
        attempt_id="attempt-prior",
        result={"imageUrl": "/output/current.png"},
    )
    store = RunStore(tmp_path)
    run = store.create(read_canvas(tmp_path, "canvas"), target="image", request_id="image-run",
                       project_id="p", settings={"concurrency": 1}, actor={"id": "u"})

    class FailedImageAdapter:
        async def submit(self, run, attempt, upstream):
            return {"task_id": attempt["id"]}

        async def reconcile(self, run, attempt):
            return {"status": "failed", "error": {"code": "provider_error"}}

    scheduler = Scheduler(store, FailedImageAdapter())
    await scheduler.tick(run["id"])
    failed = await scheduler.tick(run["id"])

    node = read_canvas(tmp_path, "canvas")["nodes"][0]
    assert failed["status"] == "failed"
    assert failed["attempts"]["image"][-1]["status"] == "failed"
    assert node["data"]["imageUrl"] == "/output/current.png"


@pytest.mark.asyncio
async def test_failed_branch_blocks_descendants_retry_only_failed(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    adapter.fail.add("b")
    scheduler = Scheduler(store, adapter)
    for _ in range(5):
        await scheduler.tick(run["id"])
    assert adapter.submits == ["a", "b"]
    assert store.get(run["id"])["status"] == "failed"
    adapter.fail.clear()
    retried = store.retry_failed(run["id"], request_id="retry-1")
    assert retried['attempts']['b'][-1]['explicit_retry'] is True
    assert not retried['attempts']['c'][-1].get('explicit_retry')
    assert retried['snapshot'] == run['snapshot']
    store.retry_failed(run["id"], request_id="retry-1")
    for _ in range(6):
        await scheduler.tick(run["id"])
    assert adapter.submits == ["a", "b", "b", "c"]
    assert store.get(run["id"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_director_review_retry_reconciles_original_receipt_without_resubmit(
    tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    from novelvideo.api.routes import director_workspace as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    node = {"id": "director", "type": "directorWorkspaceNode", "data": {
        "operation": "prepare", "episodeNumber": 1, "documentId": "doc-1",
        "adoptedRevisionId": "script-rev-1",
        "handoffSelection": {
            "expected_source_project_revision": 0,
            "update_scope": {"mode": "all"},
        },
    }}
    canvas = {"canvas_id": "canvas", "revision": 1, "nodes": [node], "edges": []}
    store = RunStore(tmp_path)
    run = store.create(canvas, target="director", request_id="request-1", project_id="p",
                       settings={"concurrency": 1}, actor={"id": "user"})
    attempt = run["attempts"]["director"][-1]
    receipt = {
        "production_workflow": True, "workflow_kind": "director", "episode": 1,
        "handoff_id": "handoff-1", "document_id": "doc-1",
        "revision_id": "script-rev-1", "source_revision": 1,
        "source_hash": "source-hash-1",
    }
    attempt.update(status="failed", provider_status="failed",
                   error="semantics: needs_review", submission=receipt)
    run["status"] = "failed"
    store.save(run, "legacy_needs_review_failure")
    original_id = attempt["id"]
    original_snapshot = run["snapshot"]

    ctx = object()
    handoff_item = {
        "id": "handoff-1", "episode_number": 1, "document_id": "doc-1",
        "revision_id": "script-rev-1", "status": "completed",
        "source_revision": 1, "source_hash": "source-hash-1",
        "task_result": {"semantic_revision_id": "semantic-rev-1"},
    }
    phase = {"value": "needs_review"}

    async def resolve(*args, **kwargs):
        return ctx

    class HandoffStore:
        async def get(self, handoff_id):
            assert handoff_id == "handoff-1"
            return handoff_item

    class Sqlite:
        async def close(self):
            pass

    async def load_services(_ctx):
        return Sqlite(), None, HandoffStore()

    async def current_source(_ctx, episode):
        assert episode == 1
        return SimpleNamespace(source_revision=1, content_hash="source-hash-1")

    async def workspace(_project, episode, _actor):
        assert episode == 1
        if phase["value"] == "needs_review":
            stages = {
                "semantics": {"status": "needs_review", "revision_id": "semantic-rev-1"},
                "director_plan": {"status": "waiting"},
                "director_version": {"status": "waiting"},
                "asset_breakdown": {"status": "waiting"},
                "group_plan": {"status": "waiting"},
            }
        else:  # The user activated this semantic candidate and continued its plan.
            stages = {
                "semantics": {"status": "completed", "revision_id": "semantic-rev-1"},
                "director_plan": {"status": "completed", "revision_id": "director-rev-1"},
                "director_version": {"status": "completed", "revision_id": "director-rev-1"},
                "asset_breakdown": {"status": "completed", "revision_id": "director-rev-1"},
                "group_plan": {"status": "completed", "revision_id": "director-rev-1"},
            }
        return {"ok": True, "data": {"stages": stages}}

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "_load_handoff_services", load_services)
    monkeypatch.setattr(api, "_current_episode_source", current_source)
    monkeypatch.setattr(api, "get_director_workspace", workspace)

    retried = store.retry_failed(run["id"], request_id="review-reconcile")
    assert len(retried["attempts"]["director"]) == 1
    assert retried["attempts"]["director"][-1]["id"] == original_id
    assert retried["attempts"]["director"][-1]["submission"] == receipt
    assert retried["snapshot"] == original_snapshot

    class CountingAdapter(NodeAdapter):
        def __init__(self, root):
            super().__init__(root)
            self.submit_count = 0

        async def submit(self, run, attempt, upstream):
            self.submit_count += 1
            raise AssertionError("A review hold must reconcile the saved handoff, not submit again")

        async def verify(self, run, attempt):
            return [{"kind": "json", "sha256": "verified-workflow-result"}]

        async def project(self, run, attempt, *, authorize_projection=None):
            return None

    adapter = CountingAdapter(tmp_path)
    scheduler = Scheduler(store, adapter)

    waiting = await scheduler.tick(run["id"])
    held = waiting["attempts"]["director"][-1]
    assert waiting["status"] == "running"
    assert held["status"] == "running"
    assert held["provider_status"] == "needs_review", (
        held.get("status"), held.get("provider_status"), held.get("error")
    )
    assert held["id"] == original_id
    assert held["submission"] == receipt
    assert adapter.submit_count == 0

    phase["value"] = "activated_and_continued"
    completed = await scheduler.tick(run["id"])
    settled = completed["attempts"]["director"][-1]
    assert completed["status"] == "succeeded"
    assert settled["status"] == "succeeded"
    assert settled["id"] == original_id
    assert settled["submission"] == receipt
    assert adapter.submit_count == 0


@pytest.mark.asyncio
async def test_cancel_reconciles_inflight_without_starting_downstream(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    store, run = new_run(tmp_path)
    adapter = Adapter()
    scheduler = Scheduler(store, adapter)
    await scheduler.tick(run["id"])
    store.cancel(run["id"])
    for _ in range(4):
        await scheduler.tick(run["id"])
    final = store.get(run["id"])
    assert adapter.submits == ["a"]
    assert final["status"] == "cancelled"
    assert final["attempts"]["a"][-1]["result"]
    store.resume(run["id"])
    resumed = await scheduler.drive(run["id"], poll_seconds=0.001)
    assert resumed["status"] == "succeeded"
    assert adapter.submits == ["a", "b", "c"]


def test_retry_cannot_overlap_another_active_run(tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError
    store, run = new_run(tmp_path)
    run["status"] = "failed"
    store.save(run, "fixture_failure")
    store.create(graph(), target="c", request_id="new", project_id="p", settings={}, actor={"id": "u"})
    with pytest.raises(OrchestrationError, match="active"):
        store.retry_failed(run["id"], request_id="retry")


@pytest.mark.asyncio
async def test_independent_branches_continue_after_failure(tmp_path):
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    canvas = graph()
    canvas["edges"] = [{"id": "ac", "source": "a", "target": "c"},
                       {"id": "bc", "source": "b", "target": "c"}]
    store, run = new_run(tmp_path, canvas)
    adapter = Adapter()
    adapter.fail.add("a")
    scheduler = Scheduler(store, adapter)
    for _ in range(4):
        await scheduler.tick(run["id"])
    final = store.get(run["id"])
    assert final["attempts"]["b"][-1]["status"] == "succeeded"
    assert "c" not in adapter.submits


def test_request_key_cannot_be_reused_for_changed_snapshot(tmp_path):
    from novelvideo.freezone.orchestration.models import OrchestrationError
    store, run = new_run(tmp_path)
    changed = graph()
    changed["nodes"][0]["data"]["text"] = "different"
    with pytest.raises(OrchestrationError, match="different"):
        store.create(changed, target="c", request_id="request", project_id="p",
                     settings={"concurrency": 3}, actor={"id": "u", "username": "u"})
