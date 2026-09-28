"""Production handoff freezes one saved episode revision and commits it atomically."""

import pytest

from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.script_creation.handoff import HandoffService
from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentStore
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def workspace(tmp_path):
    sqlite = SQLiteStore("handoff-project", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    documents = DocumentStore(sqlite.db_path)
    await documents.initialize()
    sources = EpisodeSourceStore(sqlite)
    await sources._db()
    service = HandoffService(documents, sources, project_id="handoff-project")
    await service.initialize()
    try:
        yield documents, sources, service
    finally:
        await sqlite.close()


async def test_confirm_freezes_saved_revision_and_is_replay_safe(workspace):
    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="1-1 海边 外 夜\n甲：来了。", client_mutation_id="script")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查完成"},
        client_mutation_id="prepare-1",
    )
    confirmed = await service.confirm(prepared["id"], expected_source_project_revision=0,
                                      client_mutation_id="confirm-1")
    assert confirmed["snapshot"]["markdown"] == script.revision.markdown
    assert confirmed["status"] == "source_written"
    assert (await sources.list_sources())[0].content == script.revision.markdown
    assert (await documents.get(script.id)).adopted_revision_id == script.current_revision_id
    await documents.save(script.id, base_revision_id=script.current_revision_id,
                         markdown="改稿不应改变已交接版本", client_mutation_id="later")
    assert (await service.get(prepared["id"]))["snapshot"]["markdown"] == script.revision.markdown
    assert await service.confirm(prepared["id"], expected_source_project_revision=0,
                                 client_mutation_id="confirm-1") == confirmed
    assert len(await sources.list_sources()) == 1
    with pytest.raises(DocumentConflict):
        await service.confirm(prepared["id"], expected_source_project_revision=0,
                              client_mutation_id="confirm-2")


async def test_stale_document_confirm_rolls_back_source_and_pointer(workspace):
    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第二集", episode_number=2,
                                    markdown="原稿", client_mutation_id="script-2")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查完成"},
        client_mutation_id="prepare-2",
    )
    await documents.save(script.id, base_revision_id=script.current_revision_id,
                         markdown="新稿", client_mutation_id="save-2")
    with pytest.raises(DocumentConflict):
        await service.confirm(prepared["id"], expected_source_project_revision=0,
                              client_mutation_id="confirm-2")
    assert await sources.list_sources() == []
    assert (await documents.get(script.id)).adopted_revision_id is None
    assert (await service.get(prepared["id"]))["status"] == "prepared"


async def test_confirm_rejects_source_cas_and_keeps_prepared_snapshot(workspace):
    from novelvideo.episode_sources import build_episode_candidate

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="定稿", client_mutation_id="script-cas")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "none"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="prepare-cas",
    )
    await sources.upsert_sources([build_episode_candidate("E01.md", "第1集\n外部更新")], expected_revision=0)
    with pytest.raises(DocumentConflict):
        await service.confirm(prepared["id"], expected_source_project_revision=0,
                              client_mutation_id="confirm-cas")
    assert (await sources.list_sources())[0].content == "第1集\n外部更新"
    assert (await documents.get(script.id)).adopted_revision_id is None
    assert (await service.get(prepared["id"]))["status"] == "prepared"


async def test_prepare_freezes_only_selected_references_and_rejects_cross_project_ids(workspace, tmp_path):
    from novelvideo.script_creation.entities import EntityService

    documents, _, service = workspace
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown="1-1 室内 日\n甲：你好。", client_mutation_id="episode-refs")
    chosen = await documents.create(kind="people", title="主角", markdown="张三", client_mutation_id="chosen")
    unrelated = await documents.create(kind="people", title="未来角色", markdown="路人", client_mutation_id="unrelated")
    entity = await EntityService(documents).put(
        document_id=chosen.id, base_revision_id=chosen.current_revision_id,
        block_id=chosen.revision.blocks[0].id, name="张三", client_mutation_id="entity",
    )
    prepared = await service.prepare(
        document_id=episode.id, revision_id=episode.current_revision_id,
        reference_revisions={chosen.id: chosen.current_revision_id},
        selected_entity_ids=[entity["entity_id"]], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"},
        client_mutation_id="prepare-refs",
    )
    assert list(prepared["snapshot"]["reference_revisions"]) == [chosen.id]
    assert prepared["snapshot"]["entities"][0]["asset_id"] is None
    await documents.save(unrelated.id, base_revision_id=unrelated.current_revision_id,
                         markdown="路人改稿", client_mutation_id="unrelated-change")
    confirmed = await service.confirm(prepared["id"], expected_source_project_revision=0,
                                      client_mutation_id="confirm-refs")
    assert confirmed["status"] == "source_written"

    foreign = DocumentStore(tmp_path / "other" / "data.db")
    await foreign.initialize()
    foreign_ref = await foreign.create(kind="people", title="外项目", markdown="外人", client_mutation_id="foreign")
    with pytest.raises(DocumentNotFound):
        await service.prepare(
            document_id=episode.id, revision_id=episode.current_revision_id,
            reference_revisions={foreign_ref.id: foreign_ref.current_revision_id},
            selected_entity_ids=[], update_scope={"mode": "none"},
            fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"},
            client_mutation_id="foreign-prepare",
        )


async def test_current_fact_issue_needs_reason_and_checked_run_must_match_revisions(workspace):
    import json
    from novelvideo.script_creation.store import DocumentValidation

    documents, _, service = workspace
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown="1-1 室内 日\n甲：你好。", client_mutation_id="episode-fact")
    run = {"id": "fact-run", "episode_document_id": episode.id, "mode": "actual",
           "status": "completed", "context_revisions": {episode.id: episode.current_revision_id}}
    issue = {"id": "issue-1", "category": "fact", "proposal_id": None,
             "context_revisions": run["context_revisions"], "intentional_reason": None}
    async with documents._db() as db:
        await db.execute("INSERT INTO script_consistency_runs VALUES (?,?,?,?,?,?)",
                         (run["id"], "fact-mut", "hash", json.dumps(run), "now", "now"))
        await db.execute("INSERT INTO script_consistency_issues VALUES (?,?,?,?)",
                         (issue["id"], run["id"], json.dumps(issue), "now"))
        await db.commit()
    kwargs = dict(document_id=episode.id, revision_id=episode.current_revision_id,
                  reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "none"})
    with pytest.raises(DocumentValidation, match="intentional reason"):
        await service.prepare(**kwargs, fact_acknowledgement={"mode": "checked", "run_id": run["id"]},
                              client_mutation_id="fact-no-reason")
    prepared = await service.prepare(
        **kwargs, fact_acknowledgement={"mode": "checked", "run_id": run["id"],
                                        "issue_reasons": {issue["id"]: "刻意改动设定"}},
        client_mutation_id="fact-reason",
    )
    assert prepared["snapshot"]["fact_acknowledgement"]["known_fact_issues"][issue["id"]]["reason"] == "刻意改动设定"


async def test_dispatch_reconciles_enqueue_before_ack_and_persists_completed_result(workspace, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, _, service = workspace
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown="1-1 室内 日\n甲：你好。", client_mutation_id="episode-queue")
    prepared = await service.prepare(
        document_id=episode.id, revision_id=episode.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="prepare-queue",
    )
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="confirm-queue")
    ctx = SimpleNamespace(project_id="handoff-project")
    state = None
    queued_count = 0

    class Manager:
        def get_task_for_project(self, _ctx, _type, _episode, *, scope):
            return state

    async def enqueue(_ctx, **kwargs):
        nonlocal state, queued_count
        queued_count += 1
        assert kwargs["payload"]["handoff_id"] == prepared["id"]
        state = SimpleNamespace(task_id="task-1", status="queued", result={})
        return SimpleNamespace(task_state=state)

    monkeypatch.setattr(module, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(module, "enqueue_project_task", enqueue)
    original_set = service._set_dispatch
    async def fail_after_enqueue(*args, **kwargs):
        raise RuntimeError("crashed after enqueue")
    monkeypatch.setattr(service, "_set_dispatch", fail_after_enqueue)
    with pytest.raises(RuntimeError, match="crashed"):
        await service.dispatch(prepared["id"], ctx)
    monkeypatch.setattr(service, "_set_dispatch", original_set)
    recovered = await service.retry(prepared["id"], ctx)
    assert recovered["task_id"] == "task-1"
    assert queued_count == 1
    state.status = "completed"
    state.result = {"semantic_revision_id": "sem-1"}
    completed = await service.retry(prepared["id"], ctx)
    assert completed["status"] == "completed"
    assert completed["task_result"]["semantic_revision_id"] == "sem-1"
    state = None  # terminal task state expired, durable business result remains
    assert (await service.retry(prepared["id"], ctx))["status"] == "completed"
    assert queued_count == 1


async def test_retry_claims_new_task_before_enqueue_returns_and_rejects_old_result(workspace, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, _, service = workspace
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown="1-1 室内 日\n甲：你好。", client_mutation_id="episode-inline")
    prepared = await service.prepare(
        document_id=episode.id, revision_id=episode.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="prepare-inline",
    )
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="confirm-inline")
    ctx = SimpleNamespace(project_id="handoff-project")
    state = None
    dispatches = 0

    class Manager:
        def get_task_for_project(self, _ctx, _type, _episode, *, scope):
            return state

    async def enqueue(_ctx, **kwargs):
        nonlocal state, dispatches
        dispatches += 1
        task_id = f"task-{dispatches}"
        state = SimpleNamespace(task_id=task_id, status="running", result={})
        await service.claim(prepared["id"], task_id=task_id,
                            dispatch_token=kwargs["payload"]["dispatch_token"])
        return SimpleNamespace(task_state=state)

    monkeypatch.setattr(module, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(module, "enqueue_project_task", enqueue)
    first = await service.dispatch(prepared["id"], ctx)
    assert first["task_id"] == "task-1"
    state.status = "failed"
    await service.fail(prepared["id"], task_id="task-1", error="temporary model failure")
    second = await service.retry(prepared["id"], ctx)
    assert second["task_id"] == "task-2"
    assert dispatches == 2
    with pytest.raises(DocumentConflict, match="superseded"):
        await service.complete(prepared["id"], task_id="task-1", result={"semantic_revision_id": "old"})
    completed = await service.complete(prepared["id"], task_id="task-2",
                                       result={"semantic_revision_id": "new"})
    assert completed["task_result"]["semantic_revision_id"] == "new"


async def test_selected_scope_accepts_only_reliable_parsed_scene_ids(workspace):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document
    from novelvideo.script_creation.store import DocumentValidation

    documents, _, service = workspace
    content = "## 1-1｜账房 · 夜 · 内\n甲：你好。\n\n## 1-2｜海边 · 夜 · 外\n乙：再见。"
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown=content, client_mutation_id="episode-scenes")
    scene_ids = [scene.id for scene in parse_screenplay_document(content).scenes]
    kwargs = dict(document_id=episode.id, revision_id=episode.current_revision_id,
                  reference_revisions={}, selected_entity_ids=[],
                  fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"})
    with pytest.raises(DocumentValidation, match="scene"):
        await service.prepare(**kwargs, update_scope={"mode": "selected", "scene_ids": ["imaginary"]},
                              client_mutation_id="bad-scenes")
    prepared = await service.prepare(**kwargs, update_scope={"mode": "selected", "scene_ids": [scene_ids[0]]},
                                     client_mutation_id="good-scenes")
    assert prepared["diff"]["available_scene_ids"] == scene_ids


async def test_diff_exposes_dialogue_references_reuse_and_retained_stale_media(workspace):
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    documents, sources, service = workspace
    old = "## 1-1｜账房 · 夜 · 内\n甲：旧台词。\n\n## 1-2｜海边 · 夜 · 外\n乙：再见。"
    new = old.replace("旧台词", "新台词")
    await sources.upsert_sources([build_episode_candidate("E01.md", old)], expected_revision=0)
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown=new, client_mutation_id="script-diff")
    people = await documents.create(kind="people", title="人物", markdown="甲", client_mutation_id="people-diff")
    async with documents._db() as db:
        await db.execute("INSERT INTO episode_stage_revisions VALUES (1,'media',1,0,'now')")
        await db.commit()
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={people.id: people.current_revision_id}, selected_entity_ids=[],
        update_scope={"mode": "selected", "scene_ids": [parse_screenplay_document(new).scenes[0].id]},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="prepare-diff",
    )
    assert prepared["diff"]["dialogue"]
    assert prepared["diff"]["references"]
    assert prepared["diff"]["reused_scenes"][0]["source_revision"] == 1
    assert prepared["diff"]["reused_scenes"][0]["new_scene_id"] == parse_screenplay_document(new).scenes[1].id
    assert prepared["snapshot"]["previous_stage_revisions"]["media"]["consumed_revision"] == 1
    await service.confirm(prepared["id"], expected_source_project_revision=1,
                          client_mutation_id="confirm-diff")
    async with documents._db() as db:
        media = await (await db.execute(
            "SELECT consumed_revision,stale FROM episode_stage_revisions WHERE episode_number=1 AND stage='media'"
        )).fetchone()
    assert tuple(media) == (1, 1)


async def test_guarded_semantic_save_rejects_changed_source_and_superseded_consumer(workspace):
    from types import SimpleNamespace
    from novelvideo.episode_sources import build_episode_candidate

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n甲：你好。", client_mutation_id="script-guard")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="prepare-guard",
    )
    confirmed = await service.confirm(prepared["id"], expected_source_project_revision=0,
                                      client_mutation_id="confirm-guard")
    _, envelope = await service._begin_dispatch(prepared["id"])
    await service.claim(prepared["id"], task_id="task-old", dispatch_token=envelope["dispatch_token"])
    saved = []
    semantic_store = SimpleNamespace(save=lambda revision: saved.append(revision) or revision)
    revision = SimpleNamespace(episode=1, source_revision=confirmed["source_revision"],
                               source_hash=confirmed["source_hash"], revision_id="semantic-old")
    service.save_semantic_if_current(revision, task_id="task-old",
                                    dispatch_token=envelope["dispatch_token"], semantic_store=semantic_store)
    assert saved == [revision]
    await sources.upsert_sources([build_episode_candidate("E01.md", "第1集\n后来重写")], expected_revision=1)
    with pytest.raises(DocumentConflict):
        service.save_semantic_if_current(revision, task_id="task-old",
                                        dispatch_token=envelope["dispatch_token"], semantic_store=semantic_store)
    assert saved == [revision]


async def test_unselected_design_document_cannot_hide_current_known_fact_issue(workspace):
    import json
    from novelvideo.script_creation.store import DocumentValidation

    documents, _, service = workspace
    episode = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                     markdown="## 1-1｜账房 · 夜 · 内\n甲：你好。", client_mutation_id="episode-hidden-fact")
    design = await documents.create(kind="people", title="人物", markdown="甲的设定", client_mutation_id="design-hidden-fact")
    context = {episode.id: episode.current_revision_id, design.id: design.current_revision_id}
    run = {"id": "hidden-run", "episode_document_id": episode.id, "mode": "actual",
           "status": "completed", "context_revisions": context}
    issue = {"id": "hidden-issue", "category": "fact", "proposal_id": None,
             "context_revisions": context, "intentional_reason": None}
    async with documents._db() as db:
        await db.execute("INSERT INTO script_consistency_runs VALUES (?,?,?,?,?,?)",
                         (run["id"], "hidden-run-mut", "hash", json.dumps(run), "now", "now"))
        await db.execute("INSERT INTO script_consistency_issues VALUES (?,?,?,?)",
                         (issue["id"], run["id"], json.dumps(issue), "now"))
        await db.commit()
    with pytest.raises(DocumentValidation, match="intentional reason"):
        await service.prepare(
            document_id=episode.id, revision_id=episode.current_revision_id,
            reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "none"},
            fact_acknowledgement={"mode": "unchecked", "reason": "未运行检查"},
            client_mutation_id="hidden-fact-prepare",
        )


async def test_selected_design_content_is_frozen_into_durable_downstream_envelope(workspace):
    import json

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n林川：快走。", client_mutation_id="episode-context")
    design = await documents.create(kind="people", title="角色设定", markdown="林川喜红色斗篷",
                                    client_mutation_id="design-context")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={design.id: design.current_revision_id}, selected_entity_ids=[],
        update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"},
        client_mutation_id="prepare-context",
    )
    assert prepared["snapshot"]["references"][0]["markdown"] == "林川喜红色斗篷"
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="confirm-context")
    await documents.save(design.id, base_revision_id=design.current_revision_id,
                         markdown="林川改穿蓝色斗篷", client_mutation_id="design-later")
    await documents.save(script.id, base_revision_id=script.current_revision_id,
                         markdown="后来剧本也修改", client_mutation_id="script-later")
    async with documents._db() as db:
        row = await (await db.execute(
            "SELECT envelope FROM script_handoff_outbox WHERE handoff_id=?", (prepared["id"],)
        )).fetchone()
    envelope = json.loads(row["envelope"])
    assert envelope["reference_context"]["documents"][0]["markdown"] == "林川喜红色斗篷"
    assert (await sources.list_sources())[0].content == prepared["snapshot"]["markdown"]


async def test_concurrent_dispatch_reuses_one_durable_attempt_before_queue_state_exists(workspace, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, _, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n林川：快走。", client_mutation_id="episode-concurrent")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"},
        client_mutation_id="prepare-concurrent",
    )
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="confirm-concurrent")
    ctx = SimpleNamespace(project_id="handoff-project")
    reached = asyncio.Event()
    release = asyncio.Event()
    tokens = []
    state = None

    class Manager:
        def get_task_for_project(self, _ctx, _type, _episode, *, scope):
            return state

    async def enqueue(_ctx, **kwargs):
        nonlocal state
        tokens.append(kwargs["payload"]["dispatch_token"])
        if len(tokens) == 2:
            reached.set()
        await release.wait()
        state = state or SimpleNamespace(task_id="task-one", status="queued", result={})
        return SimpleNamespace(task_state=state)

    monkeypatch.setattr(module, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(module, "enqueue_project_task", enqueue)
    first = asyncio.create_task(service.dispatch(prepared["id"], ctx))
    second = asyncio.create_task(service.dispatch(prepared["id"], ctx))
    await asyncio.wait_for(reached.wait(), timeout=2)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, dict) and result["task_id"] == "task-one" for result in results)
    assert tokens == [tokens[0], tokens[0]]


async def test_recent_claim_is_not_superseded_when_task_state_is_temporarily_invisible(workspace, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, _, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n林川：快走。", client_mutation_id="episode-invisible")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"},
        client_mutation_id="prepare-invisible",
    )
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="confirm-invisible")
    _, envelope = await service._begin_dispatch(prepared["id"])
    await service.claim(prepared["id"], task_id="active-task",
                        dispatch_token=envelope["dispatch_token"])
    monkeypatch.setattr(module, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *args, **kwargs: None))
    async def forbidden_enqueue(*args, **kwargs):
        raise AssertionError("recent claim must not be superseded")
    monkeypatch.setattr(module, "enqueue_project_task", forbidden_enqueue)
    current = await service.retry(prepared["id"], SimpleNamespace(project_id="handoff-project"))
    assert current["task_id"] == "active-task"
    assert current["dispatch_token"] == envelope["dispatch_token"]


async def test_selected_relation_requires_explicit_target_design_and_entity(workspace):
    from novelvideo.script_creation.entities import EntityService
    from novelvideo.script_creation.store import DocumentValidation

    documents, _, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n林川：快走。", client_mutation_id="episode-relation")
    people = await documents.create(kind="people", title="人物", markdown="林川", client_mutation_id="people-relation")
    props = await documents.create(kind="props", title="道具", markdown="铜钥匙", client_mutation_id="props-relation")
    entities = EntityService(documents)
    key = await entities.put(document_id=props.id, base_revision_id=props.current_revision_id,
                             block_id=props.revision.blocks[0].id, name="铜钥匙",
                             client_mutation_id="key-entity")
    person = await entities.put(document_id=people.id, base_revision_id=people.current_revision_id,
                                block_id=people.revision.blocks[0].id, name="林川",
                                client_mutation_id="person-entity",
                                relations=[{"kind": "holding", "entity_id": key["entity_id"]}])
    kwargs = dict(document_id=script.id, revision_id=script.current_revision_id,
                  update_scope={"mode": "none"},
                  fact_acknowledgement={"mode": "unchecked", "reason": "人工确认"})
    with pytest.raises(DocumentValidation, match="relation"):
        await service.prepare(**kwargs, reference_revisions={people.id: people.current_revision_id},
                              selected_entity_ids=[person["entity_id"]],
                              client_mutation_id="relation-missing")
    prepared = await service.prepare(
        **kwargs, reference_revisions={people.id: people.current_revision_id,
                                       props.id: props.current_revision_id},
        selected_entity_ids=[person["entity_id"], key["entity_id"]],
        client_mutation_id="relation-complete",
    )
    assert {e["entity_id"] for e in prepared["snapshot"]["entities"]} == {
        person["entity_id"], key["entity_id"]}


async def test_prepare_rebases_after_other_episode_source_changes_without_rewriting_completed_handoff(workspace):
    from novelvideo.episode_sources import build_episode_candidate

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="1-1 屋内 日 内\n甲：你好。", client_mutation_id="rebase-script")
    kwargs = dict(document_id=script.id, revision_id=script.current_revision_id,
                  reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "none"},
                  fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"})
    old = await service.prepare(**kwargs, client_mutation_id="rebase-prepare-old")
    await sources.upsert_sources([build_episode_candidate("E02.md", "第2集\n第二集")], expected_revision=0)
    fresh = await service.prepare(**kwargs, client_mutation_id="rebase-prepare-fresh")
    assert fresh["id"] != old["id"]
    assert fresh["expected_source_project_revision"] == 1
    assert (await service.prepare(**kwargs, client_mutation_id="rebase-prepare-old"))["id"] == old["id"]
    confirmed = await service.confirm(fresh["id"], expected_source_project_revision=1,
                                      client_mutation_id="rebase-confirm")
    assert confirmed["status"] == "completed"
    await sources.upsert_sources([build_episode_candidate("E02.md", "第2集\n第二集再改")], expected_revision=2)
    same = await service.prepare(**kwargs, client_mutation_id="rebase-prepare-again")
    assert same["id"] == confirmed["id"]
    assert len([item for item in await sources.list_sources() if item.episode_number == 1]) == 1


async def test_prepare_identity_changes_with_selected_entity_and_asset_record(workspace):
    from novelvideo.script_creation.entities import EntityService

    documents, _, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="1-1 屋内 日 内\n甲：你好。", client_mutation_id="entity-id-script")
    people = await documents.create(kind="people", title="人物", markdown="甲", client_mutation_id="entity-id-people")
    entity = await EntityService(documents).put(
        document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=people.revision.blocks[0].id, name="甲", create_text={"name": "甲资产", "description": "旧描述"},
        client_mutation_id="entity-id-create")
    kwargs = dict(document_id=script.id, revision_id=script.current_revision_id,
                  reference_revisions={people.id: people.current_revision_id},
                  selected_entity_ids=[entity["entity_id"]], update_scope={"mode": "none"},
                  fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"})
    first = await service.prepare(**kwargs, client_mutation_id="entity-id-prepare-1")
    async with documents._db() as db:
        await db.execute("UPDATE characters SET description=? WHERE name=?", ("新描述", "甲资产"))
        await db.commit()
    second = await service.prepare(**kwargs, client_mutation_id="entity-id-prepare-2")
    assert second["id"] != first["id"]
    assert second["snapshot"]["entities"][0]["asset_record"]["description"] == "新描述"
    renamed = await EntityService(documents).put(
        document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=people.revision.blocks[0].id, name="甲别名", entity_id=entity["entity_id"],
        client_mutation_id="entity-id-rename")
    third = await service.prepare(**kwargs, client_mutation_id="entity-id-prepare-3")
    assert third["id"] not in {first["id"], second["id"]}
    assert third["snapshot"]["entities"][0]["name"] == "甲别名"
    assert renamed["entity_id"] == entity["entity_id"]
    assert (await service.prepare(**kwargs, client_mutation_id="entity-id-prepare-1"))["id"] == first["id"]


async def test_prepare_diff_exposes_titled_scene_choices(workspace):
    documents, _, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="## 1-1｜账房 · 夜 · 内\n甲：你好。", client_mutation_id="titles-script")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="titles-prepare")
    assert prepared["diff"]["available_scenes"] == [{
        "id": prepared["diff"]["available_scene_ids"][0],
        "heading": "1-1｜账房 · 夜 · 内", "location": "账房"}]


async def test_extraction_failure_keeps_result_but_retries_without_rewriting_source(workspace, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="1-1 屋内 日 内\n甲：你好。", client_mutation_id="failed-result-script")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="failed-result-prepare")
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="failed-result-confirm")
    _, envelope = await service._begin_dispatch(prepared["id"])
    await service.claim(prepared["id"], task_id="task-old", dispatch_token=envelope["dispatch_token"])
    failed_result = {"semantic_revision_id": "semantic-failed", "status": "review_required",
                     "succeeded_scenes": 0, "failed_scenes": 1,
                     "validation_report": {"passed": False, "issues": [
                         {"code": "scene_extraction_failed", "message": "API key 未配置"}]}}
    failed = await service.complete(prepared["id"], task_id="task-old", result=failed_result)
    assert failed["status"] == "failed"
    assert failed["task_result"] == failed_result
    assert "API key 未配置" in failed["error"]
    async with documents._db() as db:
        row = await (await db.execute("SELECT status,result FROM script_handoff_outbox WHERE handoff_id=?",
                                      (prepared["id"],))).fetchone()
    assert row["status"] == "failed"
    assert "semantic-failed" in row["result"]

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(task_id="task-old", status="completed", result=failed_result)

    async def enqueue(_ctx, **kwargs):
        assert kwargs["payload"]["dispatch_token"] != envelope["dispatch_token"]
        return SimpleNamespace(task_state=SimpleNamespace(task_id="task-new"))

    monkeypatch.setattr(module, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(module, "enqueue_project_task", enqueue)
    ctx = SimpleNamespace(project_id="handoff-project")
    retried = await service.retry(prepared["id"], ctx)
    assert retried["task_id"] == "task-new"
    assert retried["status"] == "dispatched"
    assert (await sources.list_sources())[0].source_revision == failed["source_revision"]
    review = {"semantic_revision_id": "semantic-review", "status": "review_required",
              "succeeded_scenes": 1, "failed_scenes": 0,
              "validation_report": {"passed": False, "issues": [
                  {"code": "creative_warning", "message": "需要人工审阅"}]}}
    complete = await service.complete(prepared["id"], task_id="task-new", result=review)
    assert complete["status"] == "completed"
    assert complete["task_result"] == review


async def test_retry_reclassifies_legacy_completed_extraction_failure(workspace, monkeypatch):
    import json
    from types import SimpleNamespace
    from novelvideo.script_creation import handoff as module

    documents, sources, service = workspace
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown="1-1 屋内 日 内\n甲：你好。", client_mutation_id="legacy-failed-script")
    prepared = await service.prepare(
        document_id=script.id, revision_id=script.current_revision_id,
        reference_revisions={}, selected_entity_ids=[], update_scope={"mode": "all"},
        fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"},
        client_mutation_id="legacy-failed-prepare")
    await service.confirm(prepared["id"], expected_source_project_revision=0,
                          client_mutation_id="legacy-failed-confirm")
    _, envelope = await service._begin_dispatch(prepared["id"])
    await service.claim(prepared["id"], task_id="legacy-task", dispatch_token=envelope["dispatch_token"])
    legacy_result = {"semantic_revision_id": "semantic-legacy", "status": "review_required",
                     "succeeded_scenes": 0, "failed_scenes": 1,
                     "validation_report": {"passed": False, "issues": [
                         {"code": "scene_extraction_failed", "message": "旧配置缺少 API key"}]}}
    item = await service.get(prepared["id"])
    item.update(status="completed", task_result=legacy_result, error=None)
    async with documents._db() as db:
        await db.execute("UPDATE script_handoffs SET data=? WHERE id=?", (json.dumps(item), item["id"]))
        await db.execute("UPDATE script_handoff_outbox SET status='completed',result=? WHERE handoff_id=?",
                         (json.dumps(legacy_result), item["id"]))
        await db.commit()

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(task_id="legacy-task", status="completed", result=legacy_result)

    async def enqueue(_ctx, **kwargs):
        return SimpleNamespace(task_state=SimpleNamespace(task_id="fresh-task"))

    monkeypatch.setattr(module, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(module, "enqueue_project_task", enqueue)
    retried = await service.retry(prepared["id"], SimpleNamespace(project_id="handoff-project"))
    assert retried["task_id"] == "fresh-task"
    assert retried["status"] == "dispatched"
    assert (await sources.list_sources())[0].source_revision == item["source_revision"]


async def test_selected_scene_order_is_one_handoff_action(workspace):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    documents, _, service = workspace
    content = "## 1-1｜账房 · 夜 · 内\n甲：你好。\n\n## 1-2｜海边 · 夜 · 外\n乙：再见。"
    script = await documents.create(kind="episode_script", title="第一集", episode_number=1,
                                    markdown=content, client_mutation_id="scene-order-script")
    ids = [scene.id for scene in parse_screenplay_document(content).scenes]
    kwargs = dict(document_id=script.id, revision_id=script.current_revision_id,
                  reference_revisions={}, selected_entity_ids=[],
                  fact_acknowledgement={"mode": "unchecked", "reason": "人工检查"})
    first = await service.prepare(**kwargs, update_scope={"mode": "selected", "scene_ids": ids},
                                  client_mutation_id="scene-order-first")
    second = await service.prepare(**kwargs, update_scope={"mode": "selected", "scene_ids": list(reversed(ids))},
                                   client_mutation_id="scene-order-second")
    assert second["id"] == first["id"]
    assert second["snapshot"]["update_scope"]["scene_ids"] == sorted(ids)
