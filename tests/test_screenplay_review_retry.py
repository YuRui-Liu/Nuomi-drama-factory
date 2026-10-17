"""Manual screenplay review retry and frozen-context regressions."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from novelvideo.script_creation.store import DocumentStore
from novelvideo.script_creation.workflow import CreativeWorkflowService


class FailingReviewRuntime:
    async def run_structured(self, **_):
        raise RuntimeError("review provider unavailable")


class CapturingReviewRuntime:
    def __init__(self):
        self.prompts = []

    async def run_structured(self, *, prompt, output_type, validation_context=None, **_):
        self.prompts.append(prompt)
        quote = validation_context["draft"].splitlines()[-1]
        return output_type(
            summary="人物行动与当前集目标一致",
            issues=[],
            coverage=["已检查当前集目标、人物动机与因果"],
            strengths=[{"quote": quote, "reason": "该对白推动人物作出行动"}],
        )


class HistoryReviewRuntime:
    def __init__(self, mode):
        self.mode = mode
        self.prompts = []
        self.contexts = []

    async def run_structured(self, *, prompt, output_type, validation_context=None, **_):
        context = validation_context or {}
        self.prompts.append(prompt)
        self.contexts.append(context)
        draft = context["draft"]
        quote = next(line for line in reversed(draft.splitlines()) if line.strip())
        strengths = [{"quote": quote, "reason": "当前正文中的行动推动故事"}]
        history = context.get("issue_history", [])
        if self.mode in {"blocker", "continue"}:
            prior_issue_id = history[0]["id"] if self.mode == "continue" else None
            return output_type(
                summary="待处理的问题仍影响剧情理解",
                issues=[{
                    "quote": quote,
                    "reason": "该行动的结果尚不清楚",
                    "severity": "blocker",
                    "dimension": "causality",
                    "repair_goal": "明确行动结果",
                    "repair_scope": "local_action",
                    "prior_issue_id": prior_issue_id,
                }],
                coverage=["已检查当前正文中的行动结果"],
                strengths=strengths,
            )
        resolved = [item["id"] for item in history if item["severity"] == "blocker"]
        return output_type(
            summary="本轮历史阻断问题已修复",
            issues=[],
            resolved_issue_ids=resolved,
            coverage=["已检查当前正文并复核上轮问题"],
            strengths=strengths,
        )


@pytest.fixture
async def review_context(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    brief = await store.create(
        kind="brief",
        title="创作简报",
        markdown="林夏寻找失踪的姐姐。",
        client_mutation_id="review-retry-brief",
    )
    outline = await store.create(
        kind="outline",
        title="全剧大纲",
        markdown="林夏追查姐姐失踪的线索，最后选择公开证据。",
        client_mutation_id="review-retry-outline",
    )
    synopsis = await store.create(
        kind="episode_synopsis",
        title="第 1 集计划",
        markdown="林夏找到旧手机，决定查清姐姐失踪。",
        client_mutation_id="review-retry-synopsis",
        episode_number=1,
    )
    screenplay = await store.create(
        kind="episode_script",
        title="第 1 集剧本",
        markdown="# 第 1 集\n\n## 1-1｜公寓 · 夜 · 内\n\n林夏：我会找出真相。",
        client_mutation_id="review-retry-screenplay",
        episode_number=1,
    )
    plan_run_id = "confirmed-plan-run"
    screenplay_run_id = "confirmed-screenplay-run"
    confirmed_revisions = {
        brief.id: brief.current_revision_id,
        outline.id: outline.current_revision_id,
        synopsis.id: synopsis.current_revision_id,
    }
    plan = {
        "id": plan_run_id,
        "mode": "planning",
        "status": "completed",
        "phase": "confirmed",
        "script_mode": "series",
        "episode_count": 10,
        "brief_id": brief.id,
        "brief_revision_id": brief.current_revision_id,
        "source_idea": {"final_choice": "全剧最终林夏公开真相并承担后果。"},
        "confirmed_revisions": confirmed_revisions,
        "screenplay_run_id": screenplay_run_id,
        "confirmations": {"episode-1": screenplay_run_id},
    }
    screenplay_run = {"id": screenplay_run_id, "mode": "screenplay", "plan_run_id": plan_run_id}
    stamp = datetime.now(timezone.utc).isoformat()
    async with store._db() as db:
        for run_id, mutation_id, data in (
            (plan_run_id, "review-retry-plan", plan),
            (screenplay_run_id, "review-retry-screenplay-run", screenplay_run),
        ):
            await db.execute(
                "INSERT INTO script_generation_runs VALUES (?,?,?,?,?,?)",
                (run_id, mutation_id, "test-hash", json.dumps(data, ensure_ascii=False), stamp, stamp),
            )
        await db.commit()
    return {
        "store": store,
        "workflow": CreativeWorkflowService(store),
        "plan_run_id": plan_run_id,
        "document_id": screenplay.id,
        "revision_id": screenplay.current_revision_id,
    }


@pytest.mark.asyncio
async def test_failed_manual_review_can_retry_same_revision_without_losing_old_receipt(review_context):
    workflow = review_context["workflow"]
    document_id = review_context["document_id"]
    revision_id = review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]

    first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-attempt-one",
    )
    with pytest.raises(RuntimeError, match="review provider unavailable"):
        await workflow.execute_screenplay_review(
            document_id, revision_id, task_id="review-task-one", runtime=FailingReviewRuntime(),
        )
    failed = await review_context["store"].get_screenplay_review(document_id, revision_id)
    assert failed["status"] == "failed"

    retry = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-attempt-two",
    )
    assert retry["status"] == "pending"
    assert retry["attempt_id"] != first["attempt_id"]
    assert retry["attempt_number"] == first["attempt_number"] + 1
    assert retry["task_id"] is None

    runtime = CapturingReviewRuntime()
    completed = await workflow.execute_screenplay_review(
        document_id, revision_id, task_id="review-task-two", runtime=runtime,
        attempt_id=retry["attempt_id"],
    )
    assert completed["status"] == "passed"
    assert "林夏找到旧手机，决定查清姐姐失踪。" in runtime.prompts[0]
    assert "林夏追查姐姐失踪的线索，最后选择公开证据。" in runtime.prompts[0]
    assert "全剧最终林夏公开真相并承担后果。" in runtime.prompts[0]
    assert "第 1 集只按已确认的本集计划审查" in runtime.prompts[0]
    assert "不得把全剧最终选择未在第 1 集完成视为遗漏" in runtime.prompts[0]

    replay_old = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-attempt-one",
    )
    replay_retry = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-attempt-two",
    )
    assert replay_old["attempt_id"] == first["attempt_id"]
    assert replay_old["task_id"] == "review-task-one"
    assert replay_old["status"] == "failed"
    assert replay_retry["attempt_id"] == retry["attempt_id"]
    assert replay_retry["task_id"] == "review-task-two"
    assert replay_retry["status"] == "passed"


@pytest.mark.asyncio
async def test_manual_review_inherits_only_active_blockers_and_their_prior_issue_ancestors(review_context):
    workflow = review_context["workflow"]
    store = review_context["store"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]
    original = await store.get_revision(document_id, revision_id)

    first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="history-review-first",
    )
    first_runtime = HistoryReviewRuntime("blocker")
    first_result = await workflow.execute_screenplay_review(
        document_id, revision_id, task_id="history-review-first-task", runtime=first_runtime,
        attempt_id=first["attempt_id"],
    )
    root_issue_id = first_result["result"]["issues"][0]["id"]
    assert first_result["status"] == "needs_revision"

    inserted = "灯光忽然熄灭。\n\n"
    child = await store.save(
        document_id, base_revision_id=revision_id,
        markdown=original.revision.markdown.replace("林夏：我会找出真相。", inserted + "林夏：我会找出真相。"),
        client_mutation_id="history-review-child",
    )
    second = await workflow.queue_screenplay_review(
        document_id, child.current_revision_id, plan_run_id=plan_run_id,
        client_mutation_id="history-review-second",
    )
    second_runtime = HistoryReviewRuntime("continue")
    second_result = await workflow.execute_screenplay_review(
        document_id, child.current_revision_id, task_id="history-review-second-task", runtime=second_runtime,
        attempt_id=second["attempt_id"],
    )
    child_issue_id = second_result["result"]["issues"][0]["id"]
    assert child_issue_id != root_issue_id
    assert second_result["result"]["issues"][0]["prior_issue_id"] == root_issue_id

    grandchild = await store.save(
        document_id, base_revision_id=child.current_revision_id,
        markdown=child.revision.markdown + "\n\n林夏走向门口。",
        client_mutation_id="history-review-grandchild",
    )
    third = await workflow.queue_screenplay_review(
        document_id, grandchild.current_revision_id, plan_run_id=plan_run_id,
        client_mutation_id="history-review-third",
    )
    third_runtime = HistoryReviewRuntime("resolve")
    third_result = await workflow.execute_screenplay_review(
        document_id, grandchild.current_revision_id, task_id="history-review-third-task", runtime=third_runtime,
        attempt_id=third["attempt_id"],
    )
    history_ids = {item["id"] for item in third_runtime.contexts[0]["issue_history"]}
    assert history_ids == {root_issue_id, child_issue_id}
    assert third_result["status"] == "passed"
    assert set(third_result["result"]["resolved_issue_ids"]) == history_ids
    assert child.revision.markdown in third_runtime.prompts[0]
    assert "甲、乙" in third_runtime.prompts[0]

    final_revision = await store.save(
        document_id, base_revision_id=grandchild.current_revision_id,
        markdown=grandchild.revision.markdown + "\n\n林夏关上门。",
        client_mutation_id="history-review-after-pass",
    )
    final = await workflow.queue_screenplay_review(
        document_id, final_revision.current_revision_id, plan_run_id=plan_run_id,
        client_mutation_id="history-review-after-pass-request",
    )
    final_runtime = HistoryReviewRuntime("resolve")
    final_result = await workflow.execute_screenplay_review(
        document_id, final_revision.current_revision_id, task_id="history-review-after-pass-task",
        runtime=final_runtime, attempt_id=final["attempt_id"],
    )
    assert final_runtime.contexts[0]["issue_history"] == []
    assert final_result["status"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["other_plan", "changed_dependencies", "branch"])
async def test_manual_review_does_not_inherit_history_from_other_plan_or_branch(review_context, mismatch):
    store = review_context["store"]
    workflow = review_context["workflow"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]

    first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id,
        client_mutation_id=f"history-isolation-first-{mismatch}",
    )
    await workflow.execute_screenplay_review(
        document_id, revision_id, task_id=f"history-isolation-first-task-{mismatch}",
        runtime=HistoryReviewRuntime("blocker"), attempt_id=first["attempt_id"],
    )

    selected_plan_id = plan_run_id
    parent_revision_id = revision_id
    if mismatch == "other_plan":
        selected_plan_id = "other-confirmed-plan"
        async with store._db() as db:
            plan_row = await (await db.execute(
                "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,),
            )).fetchone()
            plan = json.loads(plan_row["data"])
            plan.update(id=selected_plan_id, screenplay_run_id="other-confirmed-screenplay",
                        confirmations={"episode-1": "other-confirmed-screenplay"})
            screenplay_run = {
                "id": "other-confirmed-screenplay", "mode": "screenplay", "plan_run_id": selected_plan_id,
            }
            stamp = datetime.now(timezone.utc).isoformat()
            for run_id, mutation_id, data in (
                (selected_plan_id, "history-isolation-plan", plan),
                ("other-confirmed-screenplay", "history-isolation-screenplay", screenplay_run),
            ):
                await db.execute(
                    "INSERT INTO script_generation_runs VALUES (?,?,?,?,?,?)",
                    (run_id, mutation_id, "history-isolation-hash", json.dumps(data, ensure_ascii=False), stamp, stamp),
                )
            await db.commit()
    elif mismatch == "changed_dependencies":
        synopsis = next(doc for doc in await store.list() if doc.kind == "episode_synopsis")
        updated_synopsis = await store.save(
            synopsis.id, base_revision_id=synopsis.current_revision_id,
            markdown=synopsis.revision.markdown + "\n\n规划依赖已重新确认。",
            client_mutation_id="history-isolation-updated-synopsis",
        )
        async with store._db() as db:
            row = await (await db.execute(
                "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,),
            )).fetchone()
            plan = json.loads(row["data"])
            plan["confirmed_revisions"][synopsis.id] = updated_synopsis.current_revision_id
            await db.execute("UPDATE script_generation_runs SET data=? WHERE id=?",
                             (json.dumps(plan, ensure_ascii=False), plan_run_id))
            await db.commit()
    else:
        parent = await store.get_revision(document_id, revision_id)
        branch_revision_id = "unrelated-branch-revision"
        async with store._db() as db:
            await db.execute(
                "INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                (branch_revision_id, document_id, None, parent.revision.markdown,
                 json.dumps([{"id": block.id, "markdown": block.markdown} for block in parent.revision.blocks],
                            ensure_ascii=False), "unrelated-branch-mutation", parent.revision.created_at, None),
            )
            await db.execute("UPDATE script_documents SET current_revision_id=? WHERE id=?",
                             (branch_revision_id, document_id))
            await db.commit()
        parent_revision_id = branch_revision_id

    if mismatch != "branch":
        parent = await store.get_revision(document_id, parent_revision_id)
        changed = await store.save(
            document_id, base_revision_id=parent_revision_id,
            markdown=parent.revision.markdown + "\n\n林夏关上门。",
            client_mutation_id=f"history-isolation-child-{mismatch}",
        )
        target_revision_id = changed.current_revision_id
    else:
        target_revision_id = parent_revision_id

    target = await workflow.queue_screenplay_review(
        document_id, target_revision_id, plan_run_id=selected_plan_id,
        client_mutation_id=f"history-isolation-target-{mismatch}",
    )
    runtime = HistoryReviewRuntime("resolve")
    await workflow.execute_screenplay_review(
        document_id, target_revision_id, task_id=f"history-isolation-target-task-{mismatch}",
        runtime=runtime, attempt_id=target["attempt_id"],
    )
    assert runtime.contexts[0]["issue_history"] == []


@pytest.mark.asyncio
async def test_manual_review_cannot_pass_without_disposing_a_pending_parent_blocker(review_context):
    store = review_context["store"]
    workflow = review_context["workflow"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]
    first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="history-pending-first",
    )
    await workflow.execute_screenplay_review(
        document_id, revision_id, task_id="history-pending-first-task",
        runtime=HistoryReviewRuntime("blocker"), attempt_id=first["attempt_id"],
    )
    parent = await store.get_revision(document_id, revision_id)
    child = await store.save(
        document_id, base_revision_id=revision_id,
        markdown=parent.revision.markdown + "\n\n林夏走向门口。",
        client_mutation_id="history-pending-child",
    )
    target = await workflow.queue_screenplay_review(
        document_id, child.current_revision_id, plan_run_id=plan_run_id,
        client_mutation_id="history-pending-second",
    )

    class SilentPassRuntime:
        async def run_structured(self, *, output_type, validation_context=None, **_):
            draft = validation_context["draft"]
            quote = next(line for line in reversed(draft.splitlines()) if line.strip())
            return output_type(summary="已经通过", issues=[], coverage=["已检查剧情"],
                               strengths=[{"quote": quote, "reason": "内容清楚"}])

    with pytest.raises(ValueError, match="prior blocker missing explicit disposition"):
        await workflow.execute_screenplay_review(
            document_id, child.current_revision_id, task_id="history-pending-second-task",
            runtime=SilentPassRuntime(), attempt_id=target["attempt_id"],
        )
    failed = await store.get_screenplay_review(document_id, child.current_revision_id)
    assert failed["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_uses_reconfirmed_dependencies_after_failed_review(review_context):
    workflow = review_context["workflow"]
    store = review_context["store"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]
    first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-before-reconfirm",
    )
    with pytest.raises(RuntimeError, match="review provider unavailable"):
        await workflow.execute_screenplay_review(
            document_id, revision_id, task_id="review-fail-before-reconfirm", runtime=FailingReviewRuntime(),
        )

    synopsis = next(doc for doc in await store.list() if doc.kind == "episode_synopsis")
    updated = await store.save(
        synopsis.id, base_revision_id=synopsis.current_revision_id,
        markdown="林夏找到旧手机，也发现姐姐曾留下求救录音。",
        client_mutation_id="review-reconfirm-synopsis",
    )
    async with store._db() as db:
        row = await (await db.execute(
            "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,),
        )).fetchone()
        plan = json.loads(row["data"])
        plan["confirmed_revisions"][synopsis.id] = updated.current_revision_id
        await db.execute("UPDATE script_generation_runs SET data=? WHERE id=?",
                         (json.dumps(plan, ensure_ascii=False), plan_run_id))
        await db.commit()

    replay_first = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-before-reconfirm",
    )
    assert replay_first["attempt_id"] == first["attempt_id"]
    assert replay_first["status"] == "failed"
    assert replay_first["confirmed_revisions"][synopsis.id] != updated.current_revision_id

    retry = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=plan_run_id, client_mutation_id="review-after-reconfirm",
    )
    assert retry["confirmed_revisions"][synopsis.id] == updated.current_revision_id
    runtime = CapturingReviewRuntime()
    completed = await workflow.execute_screenplay_review(
        document_id, revision_id, task_id="review-after-reconfirm-task", runtime=runtime,
        attempt_id=retry["attempt_id"],
    )
    assert completed["status"] == "passed"
    assert "林夏找到旧手机，也发现姐姐曾留下求救录音。" in runtime.prompts[0]
    assert first["confirmed_revisions"][synopsis.id] != retry["confirmed_revisions"][synopsis.id]


@pytest.mark.asyncio
async def test_only_one_concurrent_request_can_claim_review_enqueue(review_context):
    workflow = review_context["workflow"]
    store = review_context["store"]
    review = await workflow.queue_screenplay_review(
        review_context["document_id"],
        review_context["revision_id"],
        plan_run_id=review_context["plan_run_id"],
        client_mutation_id="review-concurrent-one",
    )
    attempt_id = review["attempt_id"]
    claimed = await asyncio.gather(
        store.claim_screenplay_review_submission(
            review_context["document_id"], review_context["revision_id"], attempt_id,
        ),
        store.claim_screenplay_review_submission(
            review_context["document_id"], review_context["revision_id"], attempt_id,
        ),
    )
    assert sorted(claimed) == [False, True]


@pytest.mark.asyncio
async def test_concurrent_review_routes_enqueue_paid_attempt_once(review_context, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creative_workflow as routes

    store = review_context["store"]
    workflow = review_context["workflow"]
    project_context = SimpleNamespace(project_id="one")
    resolved = SimpleNamespace(ctx=project_context)

    async def fake_store(*_args, **_kwargs):
        return store, workflow, resolved

    class Manager:
        tasks = {}

        def get_task_for_project(self, _ctx, _task_type, _episode, beat_num=None, scope=None):
            return self.tasks.get(scope)

    manager = Manager()
    monkeypatch.setattr(routes, "_store", fake_store)
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    enqueue_calls = []

    async def enqueue(_ctx, **kwargs):
        enqueue_calls.append(kwargs)
        await asyncio.sleep(0.05)
        task = SimpleNamespace(task_id="review-paid-task", status="queued")
        manager.tasks[kwargs["scope"]] = task
        return SimpleNamespace(task_state=task, backend="inline", queue=None)

    monkeypatch.setattr(routes, "enqueue_project_task", enqueue)
    document_id = review_context["document_id"]
    revision_id = review_context["revision_id"]
    plan_run_id = review_context["plan_run_id"]
    results = await asyncio.gather(*(
        routes.review_screenplay(
            "one", document_id,
            routes.ReviewScreenplayBody(
                revision_id=revision_id, plan_run_id=plan_run_id,
                client_mutation_id=f"route-review-{index}",
            ),
            user={"role": "editor"},
        )
        for index in (1, 2)
    ))
    assert len(enqueue_calls) == 1
    assert results[0]["ok"] is results[1]["ok"] is True
    review = await store.get_screenplay_review(document_id, revision_id)
    assert review["status"] == "pending"
    assert review["task_id"] == "review-paid-task"
    assert review["attempt_id"] == enqueue_calls[0]["payload"]["attempt_id"]


@pytest.mark.asyncio
async def test_review_poll_recovers_unknown_task_receipt_without_reenqueuing(review_context, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creative_workflow as routes

    store = review_context["store"]
    workflow = review_context["workflow"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    review = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=review_context["plan_run_id"],
        client_mutation_id="review-unknown-poll",
    )
    await store.claim_screenplay_review_submission(document_id, revision_id, review["attempt_id"])
    await store.mark_screenplay_review_submission_unknown(
        document_id, revision_id, review["attempt_id"], "submission response lost",
    )
    ctx = SimpleNamespace(project_id="one")
    resolved = SimpleNamespace(ctx=ctx)
    async def fake_store(*_args, **_kwargs):
        return store, workflow, resolved

    class Manager:
        tasks = {}

        def get_task_for_project(self, _ctx, _type, _episode, beat_num=None, scope=None):
            return self.tasks.get(scope)

    manager = Manager()
    monkeypatch.setattr(routes, "_store", fake_store)
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    async def fail_if_enqueued(*_args, **_kwargs):
        raise AssertionError("GET must never enqueue a task")

    monkeypatch.setattr(routes, "enqueue_project_task", fail_if_enqueued)
    unknown = await routes.get_screenplay_review(
        "one", document_id, revision_id, user={"role": "editor"},
    )
    assert unknown["data"]["status"] == "submission_unknown"

    target_scope = f"screenplay-review:{document_id}:{revision_id}:{review['attempt_id']}"
    manager.tasks[target_scope] = SimpleNamespace(task_id="recovered-task", status="running")
    recovered = await routes.get_screenplay_review(
        "one", document_id, revision_id, user={"role": "editor"},
    )
    assert recovered["data"]["task_id"] == "recovered-task"
    assert recovered["data"]["status"] == "pending"
    assert recovered["data"]["status"] != "running"


@pytest.mark.asyncio
async def test_review_poll_marks_cancelled_task_failed_before_worker_claim(review_context, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creative_workflow as routes

    store = review_context["store"]
    workflow = review_context["workflow"]
    document_id, revision_id = review_context["document_id"], review_context["revision_id"]
    review = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=review_context["plan_run_id"],
        client_mutation_id="review-cancel-before-claim",
    )
    await store.claim_screenplay_review_submission(document_id, revision_id, review["attempt_id"])
    ctx = SimpleNamespace(project_id="one")
    resolved = SimpleNamespace(ctx=ctx)
    async def fake_store(*_args, **_kwargs):
        return store, workflow, resolved

    target_scope = f"screenplay-review:{document_id}:{revision_id}:{review['attempt_id']}"
    task = SimpleNamespace(task_id="cancelled-before-claim", status="cancelled", error="queue cancelled")
    class Manager:
        def get_task_for_project(self, _ctx, _type, _episode, beat_num=None, scope=None):
            return task if scope == target_scope else None

    monkeypatch.setattr(routes, "_store", fake_store)
    monkeypatch.setattr(routes, "get_task_manager", lambda: Manager())
    failed = await routes.get_screenplay_review(
        "one", document_id, revision_id, user={"role": "editor"},
    )
    assert failed["data"]["status"] == "failed"
    assert failed["data"]["task_id"] == task.task_id
    retry = await workflow.queue_screenplay_review(
        document_id, revision_id, plan_run_id=review_context["plan_run_id"],
        client_mutation_id="review-cancel-before-claim-retry",
    )
    assert retry["status"] == "pending"
    assert retry["attempt_id"] != review["attempt_id"]
