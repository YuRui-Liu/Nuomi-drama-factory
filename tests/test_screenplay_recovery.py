"""Durable recovery of a retained screenplay craft draft."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from novelvideo.script_creation.store import DocumentConflict, DocumentStore, DocumentValidation
from novelvideo.script_creation.workflow import CreativeWorkflowService


@pytest.fixture
async def recovery_context(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    brief = await store.create(
        kind="brief", title="简报", markdown="林夏寻找姐姐。", client_mutation_id="recover-brief",
    )
    synopsis = await store.create(
        kind="episode_synopsis", title="第 1 集梗概", markdown="林夏发现姐姐留下的手机。",
        client_mutation_id="recover-synopsis", episode_number=1,
    )
    run_id, plan_run_id = "screenplay-run", "plan-run"
    confirmed = {brief.id: brief.current_revision_id, synopsis.id: synopsis.current_revision_id}
    stamp = datetime.now(timezone.utc).isoformat()
    run = {
        "id": run_id,
        "mode": "screenplay",
        "status": "needs_review",
        "plan_run_id": plan_run_id,
        "script_mode": "series",
        "episode_number": 1,
        "confirmed_revisions": confirmed,
        "steps": [{"key": "episode_script:1", "kind": "episode_script", "episode_number": 1,
                   "title": "第 1 集", "status": "needs_review", "output": None}],
    }
    plan = {
        "id": plan_run_id,
        "mode": "planning",
        "status": "completed",
        "phase": "confirmed",
        "confirmed_revisions": confirmed,
        "screenplay_run_id": run_id,
        "confirmations": {"confirm-1": run_id},
    }
    async with store._db() as db:
        for owner_id, mutation_id, data in (
            (run_id, "recover-run-mutation", run),
            (plan_run_id, "recover-plan-mutation", plan),
        ):
            await db.execute(
                "INSERT INTO script_generation_runs VALUES (?,?,?,?,?,?)",
                (owner_id, mutation_id, "test-hash", json.dumps(data, ensure_ascii=False), stamp, stamp),
            )
        for stage, markdown in (
            ("draft", "# 第 1 集\n\n林夏发现手机，决定追查姐姐的失踪。"),
            ("structure_repair_1", "# 第 1 集\n\n林夏发现手机，向保安询问姐姐的去向。"),
        ):
            payload = {"markdown": markdown}
            await db.execute(
                "INSERT INTO script_craft_records VALUES (?,?,?,?,?,?)",
                ("generation", run_id, "episode_script:1", stage,
                 json.dumps({"stage": stage, "output": payload}, ensure_ascii=False), stamp),
            )
        await db.commit()
    return store, run_id


@pytest.mark.asyncio
async def test_recovery_pins_first_draft_per_run_episode_and_replays_same_selection(recovery_context):
    store, run_id = recovery_context
    first, second = await asyncio.gather(
        store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-tab-one"),
        store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-tab-two"),
    )
    assert first["document"].id == second["document"].id
    assert first["document"].revision.markdown == "# 第 1 集\n\n林夏发现手机，决定追查姐姐的失踪。"
    assert first["draft_index"] == 0
    assert first["run_id"] == run_id
    docs = await store.list()
    assert len([doc for doc in docs if doc.kind == "episode_script" and doc.episode_number == 1]) == 1


@pytest.mark.asyncio
async def test_recovery_conflicts_when_tabs_choose_different_drafts(recovery_context):
    store, run_id = recovery_context
    results = await asyncio.gather(
        store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-different-one"),
        store.recover_screenplay_draft(run_id, draft_index=1, mutation_id="recover-different-two"),
        return_exceptions=True,
    )
    successes = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == len(errors) == 1
    assert isinstance(errors[0], DocumentConflict)
    docs = await store.list()
    assert len([doc for doc in docs if doc.kind == "episode_script" and doc.episode_number == 1]) == 1


@pytest.mark.asyncio
async def test_recovery_rejects_failed_run_and_stale_confirmed_dependency(recovery_context):
    store, run_id = recovery_context
    async with store._db() as db:
        row = await (await db.execute(
            "SELECT data FROM script_generation_runs WHERE id=?", (run_id,),
        )).fetchone()
        data = json.loads(row["data"])
        data["status"] = "failed"
        await db.execute("UPDATE script_generation_runs SET data=? WHERE id=?",
                         (json.dumps(data, ensure_ascii=False), run_id))
        await db.commit()
    with pytest.raises(DocumentValidation):
        await store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-failed")


@pytest.mark.asyncio
async def test_recovery_rejects_stale_confirmed_dependency(recovery_context):
    store, run_id = recovery_context
    synopsis = next(doc for doc in await store.list() if doc.kind == "episode_synopsis")
    await store.save(synopsis.id, base_revision_id=synopsis.current_revision_id,
                     markdown="更新后的梗概。", client_mutation_id="recover-stale-synopsis")
    with pytest.raises(DocumentConflict, match="confirmed planning dependency changed"):
        await store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-stale")


@pytest.mark.asyncio
async def test_recovery_conflicts_with_an_existing_episode_screenplay(recovery_context):
    store, run_id = recovery_context
    await store.create(
        kind="episode_script", title="已有第 1 集", markdown="现有正文。",
        episode_number=1, client_mutation_id="existing-screenplay",
    )
    with pytest.raises(DocumentConflict, match="an episode screenplay already exists"):
        await store.recover_screenplay_draft(run_id, draft_index=0, mutation_id="recover-existing")


@pytest.mark.asyncio
async def test_recovery_route_returns_document_receipt(recovery_context, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creative_workflow as routes

    store, run_id = recovery_context
    service = CreativeWorkflowService(store)
    resolved = SimpleNamespace(ctx=SimpleNamespace(project_id="one"))

    async def fake_store(*_args, **_kwargs):
        return store, service, resolved

    monkeypatch.setattr(routes, "_store", fake_store)
    result = await routes.recover_screenplay(
        "one", run_id,
        routes.RecoverScreenplayBody(draft_index=0, client_mutation_id="recover-route"),
        user={"role": "editor"},
    )
    assert result["ok"] is True
    assert result["data"]["run_id"] == run_id
    assert result["data"]["draft_index"] == 0
    assert result["data"]["document"].kind == "episode_script"
    assert result["data"]["document"].adopted_revision_id is None
