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
    store.retry_failed(run["id"], request_id="retry-1")
    store.retry_failed(run["id"], request_id="retry-1")
    for _ in range(6):
        await scheduler.tick(run["id"])
    assert adapter.submits == ["a", "b", "b", "c"]
    assert store.get(run["id"])["status"] == "succeeded"


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
