"""Deterministic service workflow for author-controlled script creation."""

import pytest

from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.episode_sources import build_episode_candidate
from novelvideo.script_creation.consistency import ConsistencyService
from novelvideo.script_creation.documents import import_episode_source
from novelvideo.script_creation.entities import EntityService
from novelvideo.script_creation.generation import GenerationService
from novelvideo.script_creation.handoff import HandoffService
from novelvideo.script_creation.proposals import ProposalService
from novelvideo.script_creation.rewrite import RewriteService
from novelvideo.script_creation.store import DocumentStore
from novelvideo.sqlite_store import SQLiteStore


class TextRuntime:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts = []

    async def run_structured(self, *, prompt, output_type, **_):
        self.prompts.append(prompt)
        return output_type(markdown=self.outputs.pop(0) + "\n\n人物沿着已经建立的选择继续行动，场景中的阻力让下一步决定更明确。")


class IssueRuntime:
    def __init__(self, source, target):
        self.source, self.target = source, target

    async def run_structured(self, **_):
        def evidence(doc, quote):
            block = next(block for block in doc.revision.blocks if quote in block.markdown)
            start = block.markdown.index(quote)
            return dict(document_id=doc.id, revision_id=doc.current_revision_id,
                        block_id=block.id, start=start, end=start + len(quote), quote=quote)
        return {"issues": [dict(category="fact", kind="character_knowledge",
            explanation="阿宁的知情顺序与第一集对白冲突", suggested_action="只修改第一集对白",
            source=evidence(self.source, "第二集才知道密钥"),
            target=evidence(self.target, "我知道密钥"))]}


class RewriteRuntime:
    async def run_structured(self, **_):
        return {"after": "# 第 1 集\n\n## 1-1｜旧码头 · 夜 · 外\n\n阿宁说：我还不知道密钥。",
                "reason": "按人物弧光修正知情顺序"}


@pytest.fixture
async def workspace(tmp_path):
    sqlite = SQLiteStore("creation-acceptance", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    documents = DocumentStore(sqlite.db_path)
    await documents.initialize()
    sources = EpisodeSourceStore(sqlite)
    await sources._db()
    entities = EntityService(documents)
    await entities.initialize()
    handoffs = HandoffService(documents, sources, project_id="creation-acceptance")
    await handoffs.initialize()
    try:
        yield documents, sources, entities, handoffs
    finally:
        await sqlite.close()


async def test_three_episode_creation_review_links_and_frozen_handoff(workspace):
    documents, sources, entities, handoffs = workspace
    brief = await documents.create(kind="brief", title="创作简报",
        markdown="# 创作简报\n\n模式：三集连续剧\n风格：克制悬疑\n每集一个明确悬念。",
        client_mutation_id="custom-settings")
    generation = GenerationService(documents)
    bootstrap = await generation.start(mode="bootstrap", brief_id=brief.id, script_mode="series",
        episode_count=3, instruction="克制悬疑", mutation_id="bootstrap")
    text = TextRuntime(
        "# 故事大纲\n\n阿宁在旧城寻找失踪父亲。",
        "# 分集梗概\n\n## 第 1 集\n\n阿宁在码头找到钥匙。\n\n## 第 2 集\n\n阿宁在旧仓库追查密钥来源。\n\n## 第 3 集\n\n阿宁在旧仓库找到父亲并揭晓真相。",
        "# 人物小传\n\n## 阿宁\n\n阿宁直到第二集才知道密钥。",
        "# 场景设计\n\n## 旧码头\n\n首次出场计划第一集，潮湿狭窄。",
        "# 道具设计\n\n## 铜钥匙\n\n首次出现计划第一集，由阿宁持有。",
        "# 第 1 集\n\n## 1-1｜旧码头 · 夜 · 外\n\n阿宁说：我知道密钥。",
    )
    finished = await generation.execute(bootstrap["id"], runtime=text, task_id="bootstrap-task")
    assert finished["status"] == "completed" and len(text.prompts) == 6
    docs = await documents.list()
    assert [doc.kind for doc in docs] == ["brief", "outline", "episode_synopsis", "people", "scenes", "props", "episode_script"]
    assert [doc.episode_number for doc in docs if doc.kind == "episode_script"] == [1]
    by_kind = {doc.kind: doc for doc in docs}
    people = await documents.save(by_kind["people"].id,
        base_revision_id=by_kind["people"].current_revision_id,
        markdown="# 人物小传\n\n## 阿宁\n\n弧光：阿宁直到第二集才知道密钥，第一集只拿到钥匙。",
        client_mutation_id="people-arc-edit")
    first = by_kind["episode_script"]
    consistency = ConsistencyService(documents)
    refs = {doc.id: doc.current_revision_id for doc in await documents.list()}
    check = await consistency.start(first.id, context_revisions=refs, client_mutation_id="actual-check")
    checked = await consistency.execute(check["id"], runtime=IssueRuntime(people, first), task_id="check-task")
    assert checked["mode"] == "actual" and len(checked["issues"]) == 1
    issue = checked["issues"][0]
    jobs = await consistency.create_target_rewrites(issue["id"], target_document_ids=[first.id])
    assert len(jobs) == 1 and jobs[0]["document_id"] == first.id
    assert await consistency.target_selections(issue["id"]) == [first.id]
    rewritten = await RewriteService(documents).execute(jobs[0]["id"], runtime=RewriteRuntime(), task_id="rewrite-task")
    assert rewritten["status"] == "completed"
    proposal = (await ProposalService(documents).list(first.id))[0]
    assert proposal["status"] == "pending"
    assert (await documents.get(first.id)).revision.markdown == first.revision.markdown
    adopted = await ProposalService(documents).accept([proposal["id"]],
        base_revision_id=first.current_revision_id, client_mutation_id="adopt-reviewed")
    assert "还不知道密钥" in adopted.revision.markdown
    assert len(await documents.revisions(first.id)) == 2

    for number, story in ((2, "阿宁在仓库追查密钥。"), (3, "阿宁与父亲在仓库重逢。")):
        continuation = await generation.start(mode="continue", brief_id=brief.id, script_mode="series",
            episode_count=3, episode_number=number, instruction="保留克制风格",
            mutation_id=f"continue-{number}")
        assert not any(doc.kind == "episode_script" and doc.episode_number == number for doc in await documents.list())
        result = await generation.execute(continuation["id"], runtime=TextRuntime(
            f"# 第 {number} 集\n\n## {number}-1｜旧仓库 · 夜 · 内\n\n{story}"),
            task_id=f"continue-task-{number}")
        assert result["status"] == "completed"
    scripts = [doc for doc in await documents.list() if doc.kind == "episode_script"]
    assert [doc.episode_number for doc in scripts] == [1, 2, 3]

    scene = by_kind["scenes"]
    prop = by_kind["props"]
    scene_link = await entities.put(document_id=scene.id, base_revision_id=scene.current_revision_id,
        block_id=scene.revision.blocks[1].id, name="旧码头", create_text={"name": "旧码头"},
        client_mutation_id="scene-text-link")
    prop_link = await entities.put(document_id=prop.id, base_revision_id=prop.current_revision_id,
        block_id=prop.revision.blocks[1].id, name="铜钥匙", create_text={"name": "铜钥匙"},
        client_mutation_id="prop-text-link")
    assert scene_link["asset_id"] and prop_link["asset_id"]
    assert (await entities.list(scene.id))[0]["asset_id"] == scene_link["asset_id"]
    assert (await entities.list(prop.id))[0]["asset_id"] == prop_link["asset_id"]

    prepared = await handoffs.prepare(document_id=adopted.id, revision_id=adopted.current_revision_id,
        reference_revisions={people.id: people.current_revision_id, scene.id: scene.current_revision_id,
                             prop.id: prop.current_revision_id},
        selected_entity_ids=[scene_link["entity_id"], prop_link["entity_id"]],
        update_scope={"mode": "none"},
        fact_acknowledgement={"mode": "unchecked", "reason": "已人工核对采纳后的正文"},
        client_mutation_id="prepare-frozen")
    assert prepared["snapshot"]["revision_id"] == adopted.current_revision_id
    assert len(prepared["snapshot"]["entities"]) == 2
    assert await sources.list_sources() == []
    assert (await documents.get(adopted.id)).adopted_revision_id is None
    confirmed = await handoffs.confirm(prepared["id"], expected_source_project_revision=0,
                                       client_mutation_id="confirm-frozen")
    assert confirmed["status"] == "completed"
    assert (await sources.list_sources())[0].content == adopted.revision.markdown
    assert (await documents.get(adopted.id)).adopted_revision_id == adopted.current_revision_id
    assert (await handoffs.get(prepared["id"]))["snapshot"]["markdown"] == adopted.revision.markdown


async def test_single_mode_generates_only_one_episode(workspace):
    documents, _, _, _ = workspace
    brief = await documents.create(kind="brief", title="单集设定", markdown="独立短剧，自定义人物与节奏",
                                   client_mutation_id="single-brief")
    generation = GenerationService(documents)
    run = await generation.start(mode="bootstrap", brief_id=brief.id, script_mode="single",
                                 episode_count=1, instruction="独立完成", mutation_id="single-start")
    result = await generation.execute(run["id"], runtime=TextRuntime(
        "# 故事大纲\n\n阿宁完成一次选择。", "# 人物小传\n\n## 阿宁\n\n她选择离开。",
        "# 场景设计\n\n## 家\n\n清晨。", "# 道具设计\n\n## 信\n\n藏着秘密。",
        "# 剧本正文\n\n## 1-1｜家 · 日 · 内\n\n阿宁打开信。"), task_id="single-task")
    assert result["status"] == "completed"
    assert [doc.kind for doc in await documents.list()] == ["brief", "outline", "people", "scenes", "props", "episode_script"]
    assert [doc.episode_number for doc in await documents.list() if doc.kind == "episode_script"] == [1]


async def test_existing_episode_import_is_editable_and_never_overwrites_source(workspace):
    documents, sources, _, _ = workspace
    source_text = "# 第 1 集\n\n## 1-1｜旧城 · 夜 · 外\n\n阿宁找到线索。"
    await sources.upsert_sources([build_episode_candidate("E01.md", source_text)], expected_revision=0)
    imported = await import_episode_source(documents, sources, 1)
    assert imported.source_origin and imported.revision.markdown == source_text
    revised = await documents.save(imported.id, base_revision_id=imported.current_revision_id,
                                   markdown=source_text + "\n\n她决定继续调查。", client_mutation_id="import-edit")
    assert revised.current_revision_id != imported.current_revision_id
    assert (await import_episode_source(documents, sources, 1)).id == imported.id
    assert (await sources.list_sources())[0].content == source_text
    assert (await documents.get(imported.id)).adopted_revision_id is None
