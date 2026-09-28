import asyncio
import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentConflict, DocumentValidation, DocumentNotFound
from novelvideo.script_creation.consistency import ConsistencyService
from novelvideo.script_creation.proposals import ProposalService


async def setup(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    people = await store.create(kind="people", title="人物", markdown="阿宁不知密钥😀。", client_mutation_id="p")
    scenes = await store.create(kind="scenes", title="场景", markdown="后门锁死。", client_mutation_id="s")
    props = await store.create(kind="props", title="道具", markdown="钥匙在阿宁手中。", client_mutation_id="o")
    episode = await store.create(kind="episode_script", title="第二集", episode_number=2,
        markdown="阿宁说：我知道密钥😀。\n\n阿宁从后门进来，交出钥匙。", client_mutation_id="e")
    return store, people, scenes, props, episode


def ref(doc, quote):
    block = next(b for b in doc.revision.blocks if quote in b.markdown)
    start = block.markdown.index(quote)
    return dict(document_id=doc.id, revision_id=doc.current_revision_id, block_id=block.id,
                start=start, end=start + len(quote), quote=quote)


class Runtime:
    def __init__(self, issues): self.issues = issues
    async def run_structured(self, **kwargs):
        self.prompt = kwargs["prompt"]
        return {"issues": self.issues}


def issue(kind, source, target, source_quote, target_quote):
    return dict(category="fact", kind=kind, explanation="事实冲突", source=ref(source, source_quote),
                target=ref(target, target_quote), suggested_action="检查关系")


async def test_fact_checks_keep_exact_evidence_for_arc_layout_and_prop(tmp_path):
    store, people, scenes, props, episode = await setup(tmp_path)
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, scenes, props, episode)},
                              client_mutation_id="check")
    output = [issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀"),
              issue("scene_path", scenes, episode, "后门锁死", "阿宁从后门进来"),
              issue("prop_possession", props, episode, "钥匙在阿宁手中", "交出钥匙")]
    done = await service.execute(run["id"], runtime=Runtime(output), task_id="task")
    assert done["status"] == "completed"
    assert [i["kind"] for i in done["issues"]] == ["character_knowledge", "scene_path", "prop_possession"]
    assert all(not i["stale"] for i in (await service.get(run["id"]))["issues"])


async def test_invalid_quote_and_changed_reference_publish_no_issues(tmp_path):
    store, people, _, _, episode = await setup(tmp_path)
    service = ConsistencyService(store)
    refs = {d.id: d.current_revision_id for d in (people, episode)}
    run = await service.start(episode.id, context_revisions=refs, client_mutation_id="bad")
    broken = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    broken["source"]["quote"] = "模型捏造"
    with pytest.raises(DocumentValidation):
        await service.execute(run["id"], runtime=Runtime([broken]), task_id="task")
    assert (await service.get(run["id"]))["issues"] == []
    next_run = await service.start(episode.id, context_revisions=refs, client_mutation_id="changed")
    class Changing(Runtime):
        async def run_structured(self, **kwargs):
            await store.save(people.id, base_revision_id=people.current_revision_id, markdown="阿宁已知密钥。", client_mutation_id="edit")
            return await super().run_structured(**kwargs)
    with pytest.raises(DocumentConflict):
        await service.execute(next_run["id"], runtime=Changing([issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")]), task_id="other")
    assert (await service.get(next_run["id"]))["issues"] == []


async def test_hypothetical_is_distinct_and_intentional_becomes_stale(tmp_path):
    store, people, _, _, episode = await setup(tmp_path)
    proposal = await ProposalService(store).create(document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=None, start=0, end=len(people.revision.markdown), before=people.revision.markdown,
        after="阿宁已知密钥😀。", reason="改变知情时间", round_id="round", client_mutation_id="candidate")
    service = ConsistencyService(store)
    refs = {d.id: d.current_revision_id for d in (people, episode)}
    run = await service.start(episode.id, context_revisions=refs, proposal_id=proposal["id"], client_mutation_id="hyp")
    assert run["mode"] == "hypothetical" and "阿宁已知密钥" in run["hypothetical_markdown"]
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    finding["hypothetical_quote"] = "阿宁已知密钥😀"
    done = await service.execute(run["id"], runtime=Runtime([finding]), task_id="task")
    flagged = await service.mark_intentional(done["issues"][0]["id"], reason="观众先知，角色未知")
    assert flagged["intentional_reason"] == "观众先知，角色未知"
    with pytest.raises(DocumentValidation):
        await service.mark_intentional(done["issues"][0]["id"], reason=" ")
    await ProposalService(store).accept([proposal["id"]], base_revision_id=people.current_revision_id, client_mutation_id="adopt")
    assert (await service.get(run["id"]))["issues"][0]["stale"] is True
    with pytest.raises(DocumentConflict):
        await service.mark_intentional(done["issues"][0]["id"], reason="旧证据不再成立")
    actual = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in await store.list()}, client_mutation_id="actual")
    assert actual["mode"] == "actual" and actual["proposal_id"] is None


async def test_targets_require_explicit_selection_and_only_selected_jobs(tmp_path):
    store, people, scenes, _, episode = await setup(tmp_path)
    service = ConsistencyService(store)
    refs = {d.id: d.current_revision_id for d in (people, scenes, episode)}
    run = await service.start(episode.id, context_revisions=refs, client_mutation_id="check")
    done = await service.execute(run["id"], runtime=Runtime([issue("scene_path", scenes, episode, "后门锁死", "阿宁从后门进来")]), task_id="task")
    with pytest.raises(DocumentValidation):
        await service.create_target_rewrites(done["issues"][0]["id"], target_document_ids=[])
    jobs = await service.create_target_rewrites(done["issues"][0]["id"], target_document_ids=[episode.id])
    assert len(jobs) == 1 and jobs[0]["document_id"] == episode.id
    assert await service.target_selections(done["issues"][0]["id"]) == [episode.id]
    assert (await service.get(run["id"]))["issues"][0]["selected_target_document_ids"] == [episode.id]
    assert await service.proposals.list(scenes.id) == []
    with pytest.raises(DocumentNotFound):
        await service.target_selections("foreign-issue")


async def test_hypothetical_target_proposal_rejects_discarded_source(tmp_path):
    from novelvideo.script_creation.rewrite import RewriteService
    store, people, _, _, episode = await setup(tmp_path)
    source = await ProposalService(store).create(document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=None, start=0, end=len(people.revision.markdown), before=people.revision.markdown,
        after="阿宁已知密钥😀。", reason="知情变化", round_id="source-round", client_mutation_id="source")
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, episode)},
                              proposal_id=source["id"], client_mutation_id="check")
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    finding["hypothetical_quote"] = "阿宁已知密钥😀"
    result = await service.execute(run["id"], runtime=Runtime([finding]), task_id="check-task")
    job = (await service.create_target_rewrites(result["issues"][0]["id"], target_document_ids=[episode.id]))[0]
    class RewriteRuntime:
        async def run_structured(self, **kwargs):
            return {"after": "阿宁说：我仍不知密钥。", "reason": "匹配知情顺序"}
    await RewriteService(store).execute(job["id"], runtime=RewriteRuntime(), task_id="rewrite-task")
    target = (await ProposalService(store).list(episode.id))[0]
    await ProposalService(store).discard(source["id"])
    assert (await service.get(run["id"]))["issues"][0]["stale"] is True
    with pytest.raises(DocumentConflict):
        await service.mark_intentional(result["issues"][0]["id"], reason="来源已失效")
    with pytest.raises(DocumentConflict):
        await ProposalService(store).accept([target["id"]], base_revision_id=episode.current_revision_id,
                                            client_mutation_id="accept")


async def test_cancelled_check_never_publishes_issues(tmp_path):
    from novelvideo.task_backend.cancel import TaskCancelled
    store, people, _, _, episode = await setup(tmp_path)
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, episode)},
                              client_mutation_id="cancel")
    checks = 0
    async def cancel_after_model():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        await service.execute(run["id"], runtime=Runtime([issue("character_knowledge", people, episode,
            "阿宁不知密钥😀", "我知道密钥😀")]), task_id="task", cancel_check=cancel_after_model)
    persisted = await service.get(run["id"])
    assert persisted["issues"] == [] and persisted["status"] == "failed"


async def test_consistency_runner_rejects_foreign_project_and_wrong_scope(tmp_path):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners.script_creation_consistency import _run
    context = SimpleNamespace(project_id="mine", state_dir=tmp_path)
    with pytest.raises(ValueError, match="PROJECT_SCOPE_MISMATCH"):
        await _run({"payload": {"project_id": "other", "run_id": "run"},
                    "scope": "consistency:run", "__run_task_id": "task"}, context)
    with pytest.raises(ValueError, match="INVALID_CONSISTENCY_ENVELOPE"):
        await _run({"payload": {"project_id": "mine", "run_id": "run"},
                    "scope": "consistency:foreign", "__run_task_id": "task"}, context)


async def test_hypothetical_fact_must_quote_candidate_text(tmp_path):
    store, people, _, _, episode = await setup(tmp_path)
    source = await ProposalService(store).create(document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=None, start=0, end=len(people.revision.markdown), before=people.revision.markdown,
        after="阿宁已知密钥😀。", reason="知情变化", round_id="source-round", client_mutation_id="source")
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, episode)},
                              proposal_id=source["id"], client_mutation_id="check")
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    finding["hypothetical_quote"] = "不存在的候选内容"
    with pytest.raises(DocumentValidation):
        await service.execute(run["id"], runtime=Runtime([finding]), task_id="task")
    assert (await service.get(run["id"]))["issues"] == []


async def test_refined_consistency_rewrite_inherits_source_and_all_original_refs(tmp_path):
    from novelvideo.script_creation.rewrite import RewriteService
    store, people, scenes, _, episode = await setup(tmp_path)
    source = await ProposalService(store).create(document_id=people.id, base_revision_id=people.current_revision_id,
        block_id=None, start=0, end=len(people.revision.markdown), before=people.revision.markdown,
        after="阿宁已知密钥😀。", reason="知情变化", round_id="source-round", client_mutation_id="source")
    service = ConsistencyService(store)
    refs = {d.id: d.current_revision_id for d in (people, scenes, episode)}
    run = await service.start(episode.id, context_revisions=refs, proposal_id=source["id"], client_mutation_id="check")
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    finding["hypothetical_quote"] = "阿宁已知密钥😀"
    result = await service.execute(run["id"], runtime=Runtime([finding]), task_id="check-task")
    original = (await service.create_target_rewrites(result["issues"][0]["id"], target_document_ids=[episode.id]))[0]
    class RewriteRuntime:
        async def run_structured(self, **kwargs):
            return {"after": "阿宁说：我仍不知密钥。", "reason": "修复知情顺序"}
    rewrite = RewriteService(store)
    await rewrite.execute(original["id"], runtime=RewriteRuntime(), task_id="first-task")
    first = (await ProposalService(store).list(episode.id))[0]
    refined = await rewrite.start(document_id=episode.id, base_revision_id=episode.current_revision_id,
        start=0, end=len(episode.revision.markdown), scope="episode", mode="custom", instruction="继续调整",
        preserve="", context_revisions={}, reference_proposal_id=first["id"], client_mutation_id="refine")
    assert refined["context_revisions"][people.id] == people.current_revision_id
    assert refined["context_revisions"][scenes.id] == scenes.current_revision_id
    await rewrite.execute(refined["id"], runtime=RewriteRuntime(), task_id="second-task")
    derived = next(p for p in await ProposalService(store).list(episode.id) if p["round_id"] == refined["id"])
    await ProposalService(store).discard(source["id"])
    with pytest.raises(DocumentConflict):
        await ProposalService(store).accept([derived["id"]], base_revision_id=episode.current_revision_id,
                                            client_mutation_id="accept-derived")


async def test_hypothetical_quote_cannot_use_unchanged_block(tmp_path):
    store, people, _, _, episode = await setup(tmp_path)
    block = episode.revision.blocks[0]
    before = "我知道密钥😀"
    start = block.markdown.index(before)
    source = await ProposalService(store).create(document_id=episode.id, base_revision_id=episode.current_revision_id,
        block_id=block.id, start=start, end=start + len(before), before=before,
        after="我还不知道密钥", reason="知情变化", round_id="source-round", client_mutation_id="source")
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, episode)},
                              proposal_id=source["id"], client_mutation_id="check")
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", before)
    finding["hypothetical_quote"] = "阿宁从后门进来"
    with pytest.raises(DocumentValidation):
        await service.execute(run["id"], runtime=Runtime([finding]), task_id="task")
    assert (await service.get(run["id"]))["issues"] == []


async def test_hypothetical_quote_cannot_use_unchanged_suffix_of_cross_block_patch(tmp_path):
    from novelvideo.script_creation.rewrite import RewriteService
    store, people, _, _, episode = await setup(tmp_path)
    text = episode.revision.markdown
    rewrite = RewriteService(store)
    job = await rewrite.start(document_id=episode.id, base_revision_id=episode.current_revision_id,
        start=text.index("我知道密钥😀"), end=text.index("阿宁从后门进来") + len("阿宁从后门进来"),
        scope="selection", mode="custom", instruction="改知情", preserve="", client_mutation_id="rewrite")
    class RewriteRuntime:
        async def run_structured(self, **kwargs):
            return {"after": "我还不知道密钥。阿宁从正门进来", "reason": "修复知情"}
    await rewrite.execute(job["id"], runtime=RewriteRuntime(), task_id="rewrite-task")
    proposal = (await ProposalService(store).list(episode.id))[0]
    assert proposal["block_id"] is None and "交出钥匙" in proposal["after"]
    service = ConsistencyService(store)
    run = await service.start(episode.id, context_revisions={d.id: d.current_revision_id for d in (people, episode)},
                              proposal_id=proposal["id"], client_mutation_id="check")
    finding = issue("character_knowledge", people, episode, "阿宁不知密钥😀", "我知道密钥😀")
    finding["hypothetical_quote"] = "交出钥匙"
    with pytest.raises(DocumentValidation):
        await service.execute(run["id"], runtime=Runtime([finding]), task_id="check-task")
    assert (await service.get(run["id"]))["issues"] == []
