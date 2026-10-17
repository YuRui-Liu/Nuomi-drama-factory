"""Regression coverage for the explicit creative planning workflow."""

import asyncio

import pytest

from novelvideo.script_creation.proposals import ProposalService
from novelvideo.script_creation.store import DocumentConflict, DocumentStore, DocumentValidation
from novelvideo.script_creation.workflow import CreativeWorkflowService


class CraftRuntime:
    """A structured provider boundary that exercises the real craft pipeline."""

    def __init__(self, drafts=()):
        self.drafts = iter(drafts)
        self.calls = 0

    async def run_structured(self, *, prompt, output_type, validation_context=None, **_):
        self.calls += 1
        if output_type.__name__ == "CraftReview":
            quote = validation_context["draft"].splitlines()[-1]
            return output_type(summary="结构完整", issues=[], coverage=["已检查人物动机与因果"],
                               strengths=[{"quote": quote, "reason": "该内容推动人物行动"}])
        if output_type.__name__ == "IdeationBatch":
            return output_type(candidates=[
                {"one_line": f"方向{i}：一个人作出选择", "protagonist_conflict": f"主角{i}与规则冲突",
                 "highlights": f"看点{i}", "final_choice": f"最终选择{i}"}
                for i in range(1, 4)
            ])
        return output_type(markdown=next(self.drafts))


@pytest.fixture
async def documents(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    return store


async def test_planning_confirm_freezes_revisions_before_screenplay(documents):
    brief = await documents.create(kind="brief", title="简报", markdown="都市悬疑，林夏寻找失踪的姐姐。",
                                   client_mutation_id="brief")
    outline = await documents.create(kind="outline", title="既有大纲", markdown="# 既有大纲\n\n林夏追查一条线索。",
                                     client_mutation_id="existing-outline")
    workflow = CreativeWorkflowService(documents)
    ideation = await workflow.create_ideation(brief_document_id=brief.id,
        brief_revision_id=brief.current_revision_id, client_mutation_id="ideation", generate=False)
    assert ideation["status"] == "completed"
    assert ideation["candidates"] == []

    own = await workflow.create_idea_candidate(ideation["id"], one_line="林夏追查姐姐失踪真相。",
        protagonist_conflict="林夏必须在保护家人和公开证据间选择。", highlights="每条线索都指向她自己的决定。",
        final_choice="她公开证据并承担后果。", client_mutation_id="own-idea")
    adopted = await workflow.adopt_idea(ideation["id"], candidate_id=own["id"],
        revision_id=own["revision_id"], client_mutation_id="adopt-idea")
    assert adopted["adopted"]["revision_id"] == own["revision_id"]

    plan = await workflow.start_plan(ideation_id=ideation["id"], candidate_id=own["id"],
        idea_revision_id=own["revision_id"], brief_document_id=brief.id,
        brief_revision_id=brief.current_revision_id, script_mode="single", episode_count=1,
        instruction="克制现实风格", client_mutation_id="plan")
    assert plan["mode"] == "planning"
    assert all(step["kind"] != "episode_script" for step in plan["steps"])
    assert not [doc for doc in await documents.list() if doc.kind == "episode_script"]

    plan = await workflow.execute(plan["id"], runtime=CraftRuntime([
        "# 故事大纲\n\n林夏循线追查姐姐的失踪，并发现自己曾替姐姐隐瞒证据。",
        "# 人物小传\n\n林夏把保护家人当作责任，最终学会承担公开真相的后果。",
        "# 场景设计\n\n## 旧公寓\n\n狭窄的楼道保存了姐姐离开的痕迹。",
        "# 道具设计\n\n## 旧手机\n\n手机中的录音证明林夏曾经隐瞒事实。",
        "# 第 1 集计划\n\n林夏找到旧手机，面对是否公开录音的冲突，最终选择公开。",
    ]), task_id="plan-task")
    assert plan["status"] == "completed"
    assert len(plan["steps"]) == 5
    proposal_service = ProposalService(documents)
    plan_revisions = {}
    for step in plan["steps"]:
        output = step["output"]
        if output["kind"] == "document":
            plan_revisions[output["document_id"]] = output["revision_id"]
            continue
        assert output["kind"] == "candidate"
        candidate = await documents.generation_candidate(output["candidate_id"])
        proposal = await proposal_service.from_candidate(output["candidate_id"])
        destination = await documents.get(candidate["target_document_id"])
        accepted = await proposal_service.accept(
            [proposal["id"]], base_revision_id=destination.current_revision_id,
            client_mutation_id=f"accept-plan-{step['key']}")
        plan_revisions[destination.id] = accepted.current_revision_id
    accepted_outline = await documents.get(outline.id)
    edited_outline = await documents.save(
        outline.id, base_revision_id=accepted_outline.current_revision_id,
        markdown=accepted_outline.revision.markdown + "\n\n用户确认后补充的行动线索。",
        client_mutation_id="edit-plan-outline-before-confirm")
    plan_revisions[outline.id] = edited_outline.current_revision_id
    candidate = await documents.get(own["id"])
    frozen = {brief.id: brief.current_revision_id, candidate.id: own["revision_id"], **plan_revisions}

    with pytest.raises((DocumentConflict, DocumentValidation)):
        await workflow.confirm_plan(plan["id"], plan_revision_ids={**plan_revisions, "unexpected": "revision"},
                                    client_mutation_id="confirm-extra-output")

    confirmed, concurrent_confirm = await asyncio.gather(
        workflow.confirm_plan(plan["id"], plan_revision_ids=plan_revisions, client_mutation_id="confirm-plan"),
        workflow.confirm_plan(plan["id"], plan_revision_ids=plan_revisions, client_mutation_id="confirm-retry"),
    )
    assert confirmed["plan_run"]["phase"] == "confirmed"
    assert confirmed["screenplay_run"]["mode"] == "screenplay"
    assert confirmed["screenplay_run"]["confirmed_revisions"] == frozen
    assert concurrent_confirm["screenplay_run"]["id"] == confirmed["screenplay_run"]["id"]
    assert len([run for run in await documents.generation_list() if run["mode"] == "screenplay"]) == 1

    runtime = CraftRuntime(["# 第 1 集\n\n## 1-1｜公寓 · 夜 · 内\n\n林夏推开房门，手电照见地上的旧手机。她停下脚步，听见楼上传来姐姐熟悉的敲击声。\n\n林夏：这一次，我不会再替你隐瞒。"])
    screenplay = await workflow.execute(confirmed["screenplay_run"]["id"], runtime=runtime,
                                        task_id="screenplay-task")
    assert screenplay["status"] == "completed"
    script_output = next(step["output"] for step in screenplay["steps"]
                         if step["output"]["kind"] == "document")
    adopted_script = await workflow.adopt_screenplay(
        script_output["document_id"], revision_id=script_output["revision_id"],
        client_mutation_id="adopt-reviewed-screenplay")
    assert adopted_script["adopted_revision_id"] == script_output["revision_id"]

    script_doc = await documents.get(script_output["document_id"])
    manual_revision = await documents.save(
        script_doc.id, base_revision_id=script_doc.current_revision_id,
        markdown=script_doc.revision.markdown + "\n\n林夏把手机装进口袋，转身走向楼梯。",
        client_mutation_id="manual-screenplay-revision")
    review = await workflow.queue_screenplay_review(
        script_doc.id, manual_revision.current_revision_id, plan_run_id=plan["id"],
        client_mutation_id="review-manual-screenplay")
    assert review["status"] == "pending"
    reviewed = await workflow.execute_screenplay_review(
        script_doc.id, manual_revision.current_revision_id, task_id="manual-review-task",
        runtime=CraftRuntime())
    assert reviewed["status"] == "passed"
    adopted_manual = await workflow.adopt_screenplay(
        script_doc.id, revision_id=manual_revision.current_revision_id,
        client_mutation_id="adopt-manually-reviewed-screenplay")
    assert adopted_manual["adopted_revision_id"] == manual_revision.current_revision_id

    outline = await documents.get(outline.id)
    changed = await documents.save(outline.id, base_revision_id=outline.current_revision_id,
        markdown=outline.revision.markdown + "\n\n新增了未经确认的设定。", client_mutation_id="change-plan")
    with pytest.raises(DocumentConflict):
        await workflow.adopt_screenplay(script_output["document_id"],
            revision_id=script_output["revision_id"], client_mutation_id="adopt-after-plan-change")

    with pytest.raises(DocumentConflict):
        await workflow.confirm_plan(plan["id"], plan_revision_ids={**frozen, outline.id: changed.current_revision_id},
                                    client_mutation_id="confirm-changed")


async def test_ideation_generation_is_three_candidates_and_adoption_is_not_handoff(documents):
    brief = await documents.create(kind="brief", title="简报", markdown="现实悬疑，女主寻找姐姐。",
                                   client_mutation_id="brief-ideation")
    workflow = CreativeWorkflowService(documents)
    ideation = await workflow.create_ideation(brief_document_id=brief.id,
        brief_revision_id=brief.current_revision_id, client_mutation_id="generated-ideation", generate=True)
    runtime = CraftRuntime()
    finished = await workflow.execute_ideation(ideation["id"], runtime=runtime, task_id="idea-task")
    assert len(finished["candidates"]) == 3
    chosen = finished["candidates"][1]
    adopted = await workflow.adopt_idea(ideation["id"], candidate_id=chosen["id"],
        revision_id=chosen["revision_id"], client_mutation_id="adopt-second")
    replay = await workflow.adopt_idea(ideation["id"], candidate_id=chosen["id"],
        revision_id=chosen["revision_id"], client_mutation_id="adopt-second")
    assert replay["adopted"]["revision_id"] == adopted["adopted"]["revision_id"]
    assert len((await workflow.get_ideation(ideation["id"]))["candidates"]) == 3
    assert not hasattr(workflow, "handoff")


async def test_candidate_edit_replay_returns_original_result_after_revision_moves(documents):
    brief = await documents.create(kind="brief", title="简报", markdown="现实悬疑。",
                                   client_mutation_id="brief-edit-replay")
    workflow = CreativeWorkflowService(documents)
    ideation = await workflow.create_ideation(brief_document_id=brief.id,
        brief_revision_id=brief.current_revision_id, client_mutation_id="ideation-edit-replay")
    candidate = await workflow.add_candidate(ideation["id"], one_line="雨夜救援。",
        protagonist_conflict="林夏必须承担风险。", highlights="她发现证据与自己有关。",
        final_choice="她公开真相。", client_mutation_id="add-edit-replay")
    edit = dict(candidate_id=candidate["id"], base_revision_id=candidate["revision_id"],
        one_line="雨夜追查。", protagonist_conflict="林夏要在承诺与真相间选择。",
        highlights="线索逐步揭开隐瞒。", final_choice="她承担公开的后果。",
        client_mutation_id="edit-once")
    first = await workflow.edit_candidate(ideation["id"], **edit)
    replay = await workflow.edit_candidate(ideation["id"], **edit)
    assert replay == first
    assert replay["revision_id"] != candidate["revision_id"]


async def test_unreviewed_independent_screenplay_cannot_be_adopted(documents):
    document = await documents.create(kind="episode_script", title="手写剧本",
        markdown="# 第 1 集\n\n## 1-1｜旧公寓 · 夜 · 内\n\n林夏推门。",
        episode_number=1, client_mutation_id="manual-screenplay")
    workflow = CreativeWorkflowService(documents)
    with pytest.raises(DocumentValidation):
        await workflow.adopt_screenplay(document.id, revision_id=document.current_revision_id,
                                        client_mutation_id="adopt-unreviewed")


async def test_queue_receipt_cannot_overwrite_completed_ideation_state(documents, monkeypatch):
    brief = await documents.create(kind="brief", title="简报", markdown="寻找失踪的姐姐。",
                                   client_mutation_id="brief-race")
    workflow = CreativeWorkflowService(documents)
    ideation = await workflow.create_ideation(brief_document_id=brief.id,
        brief_revision_id=brief.current_revision_id, client_mutation_id="ideation-race", generate=True)
    completed = await workflow.execute_ideation(ideation["id"], runtime=CraftRuntime(), task_id="idea-task")
    assert completed["status"] == "completed"
    stale = {**completed, "status": "pending", "candidates": [], "task_id": None}
    original_load = workflow._load
    monkeypatch.setattr(workflow, "_load", lambda _id: asyncio.sleep(0, result=stale))
    await workflow.record_ideation_task(ideation["id"], "idea-task")
    monkeypatch.setattr(workflow, "_load", original_load)
    saved = await workflow.get_ideation(ideation["id"])
    assert saved["status"] == "completed"
    assert len(saved["candidates"]) == 3

