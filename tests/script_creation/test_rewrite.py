import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentConflict
from novelvideo.script_creation.rewrite import RewriteService


class Runtime:
    async def run_structured(self, **kwargs):
        return {"after": "新台词✨", "reason": "冲突更清晰"}


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_xyq_scene_scope_preserves_metadata_and_exact_offsets(newline):
    from novelvideo.script_creation.rewrite import _range
    first = newline.join(['第2集 2-1', '场景：屋内 夜', '人物：甲', '△甲停笔。', '甲：等等。', ''])
    second = newline.join(['第二集 2-2', '场景：屋外 夜', '人物：甲', '△甲出门。'])
    prefix = '# 第二集😀' + newline + newline
    text = prefix + first + second
    start, end = _range(text, 'scene', text.index('等等'), text.index('等等'))
    assert text[start:end] == first
    assert start == len(prefix)
    assert end == len(prefix + first)


@pytest.mark.parametrize('kind,expected', [('episode_script', '# 短剧节奏'), ('people', '# 人物与对白')])
async def test_rewrite_uses_document_kind_methods_without_reformatting_selection(tmp_path, kind, expected):
    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    doc = await store.create(kind=kind, title='正文', markdown='甲：等等。', client_mutation_id='create')
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
                              start=0, end=len(doc.revision.markdown), scope='selection', mode='subtext', instruction='',
                              preserve='', client_mutation_id='rewrite')
    class CapturingRuntime(Runtime):
        async def run_structured(self, **kwargs):
            self.call = kwargs
            return await super().run_structured(**kwargs)
    runtime = CapturingRuntime()
    await service.execute(job['id'], runtime=runtime, task_id='task')
    assert expected in runtime.call['system_prompt']
    assert '保持原文已有场号与格式' in runtime.call['system_prompt']
    assert '只返回 JSON' in runtime.call['prompt']
    assert '第1集 1-1' not in runtime.call['prompt']


async def test_rewrite_custom_skill_replaces_default_once_for_target_kind(tmp_path, monkeypatch):
    from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig, ResourceRef, ResourceVersion
    from novelvideo.agent_teams.runtime import method_scope
    from novelvideo.text_task_runtime.models import AgentTaskRoute

    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    doc = await store.create(kind='people', title='人物', markdown='甲很谨慎。', client_mutation_id='create')
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
                              start=0, end=len(doc.revision.markdown), scope='selection', mode='custom',
                              instruction='', preserve='', client_mutation_id='rewrite')
    resource = ResourceVersion(id='custom', revision=1, kind='skill', owner='u',
                               content='独特人物方法', content_hash='h')
    method = ExecutionSnapshot(id='s', project_id='p', template_id='t', template_revision=1,
                               active_revision=1, role_id='writer', subtask_id='people',
                               input_revision='r', input_hash='h', resolved_model=AgentTaskRoute(model='custom-model'),
                               resolved_method=MethodConfig(skills=(ResourceRef(id='custom', revision=1),)),
                               resource_snapshots=(resource,))
    class CapturingRuntime(Runtime):
        snapshot = None
        async def run_structured(self, **kwargs):
            self.call = kwargs
            return await super().run_structured(**kwargs)
    chosen = CapturingRuntime()
    def build(route):
        assert route.model == 'custom-model'
        return chosen
    monkeypatch.setattr('novelvideo.agent_teams.adapters.build_text_task_runtime', build)
    with method_scope([method], project_id='p', task_type='script_creation_rewrite'):
        await service.execute(job['id'], runtime=Runtime(), task_id='task')
    assert chosen.call['prompt'].count('独特人物方法') == 1
    assert '# 人物与对白' not in chosen.call['system_prompt']
    assert '保持原文已有场号与格式' in chosen.call['system_prompt']


async def test_rewrite_selected_second_occurrence_and_cross_block(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="同句😀\n\n同句😀", client_mutation_id="create")
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=5, end=8, scope="selection", mode="dialogue", instruction="自然一些",
        preserve="保留笑点", client_mutation_id="rewrite")
    result = await service.execute(job["id"], runtime=Runtime(), task_id="task")
    proposals = await service.proposals.list(doc.id)
    assert result["status"] == "completed"
    assert proposals[0]["block_id"] == doc.revision.blocks[1].id
    assert proposals[0]["start"] == 0 and proposals[0]["end"] == 3
    assert proposals[0]["before"] == "同句😀"
    assert proposals[0]["after"] == "新台词✨"
    with pytest.raises(DocumentConflict):
        await service.start(document_id=doc.id, base_revision_id="stale", start=0, end=3,
            scope="selection", mode="dialogue", instruction="", preserve="", client_mutation_id="stale")


async def test_scene_scope_uses_caret_and_stops_at_next_scene(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    text = "# 第一集\n\n## 1-1｜室内\n甲说话。\n\n## 1-2｜室外\n乙说话。"
    doc = await store.create(kind="episode_script", title="一", markdown=text, client_mutation_id="create")
    caret = text.index("甲说话")
    job = await RewriteService(store).start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=caret, end=caret, scope="scene", mode="conflict", instruction="", preserve="",
        client_mutation_id="scene")
    assert job["before"].startswith("## 1-1｜室内")
    assert "## 1-2" not in job["before"]


async def test_scene_scope_accepts_generated_script_scene_headings(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    text = "# 第一集\n\n### 1-1 夜 外 船厂账房门前\n甲说话。\n\n### 1-2 夜 内 账房\n乙说话。"
    doc = await store.create(kind="episode_script", title="一", markdown=text, client_mutation_id="create")
    caret = text.index("甲说话")
    job = await RewriteService(store).start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=caret, end=caret, scope="scene", mode="conflict", instruction="", preserve="",
        client_mutation_id="scene")
    assert job["before"].startswith("### 1-1 夜 外")
    assert "### 1-2" not in job["before"]


async def test_cross_block_selection_keeps_exact_global_offsets(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="甲😀\n\n乙✨", client_mutation_id="create")
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=1, end=6, scope="selection", mode="dialogue", instruction="", preserve="",
        client_mutation_id="cross")
    assert job["before"] == "😀\n\n乙✨"
    assert job["block_id"] is None
    await service.execute(job["id"], runtime=Runtime(), task_id="task")
    proposal = (await service.proposals.list(doc.id))[0]
    assert proposal["before"] == doc.revision.markdown
    changed = await service.proposals.accept([proposal["id"]], base_revision_id=doc.current_revision_id,
                                             client_mutation_id="adopt")
    assert changed.revision.markdown == "甲新台词✨"


async def test_completed_rewrite_cannot_publish_second_proposal(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="旧句", client_mutation_id="create")
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=0, end=2, scope="selection", mode="dialogue", instruction="", preserve="",
        client_mutation_id="rewrite")
    first = await service.execute(job["id"], runtime=Runtime(), task_id="task")
    again = await service.execute(job["id"], runtime=Runtime(), task_id="task")
    assert again["proposal_id"] == first["proposal_id"]
    assert len(await service.proposals.list(doc.id)) == 1
    with pytest.raises(DocumentConflict):
        await service.execute(job["id"], runtime=Runtime(), task_id="other")


async def test_rewrite_freezes_referenced_text_and_rejects_changed_context(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    script = await store.create(kind="episode_script", title="一", markdown="旧句", client_mutation_id="script")
    people = await store.create(kind="people", title="人物", markdown="主角谨慎。", client_mutation_id="people")
    service = RewriteService(store)
    job = await service.start(document_id=script.id, base_revision_id=script.current_revision_id,
        start=0, end=2, scope="selection", mode="dialogue", instruction="", preserve="",
        context_revisions={people.id: people.current_revision_id}, client_mutation_id="rewrite")
    class CapturingRuntime(Runtime):
        async def run_structured(self, **kwargs):
            self.prompt = kwargs["prompt"]
            return await super().run_structured(**kwargs)
    runtime = CapturingRuntime()
    await service.execute(job["id"], runtime=runtime, task_id="task")
    assert "主角谨慎" in runtime.prompt
    changed = await store.save(people.id, base_revision_id=people.current_revision_id,
                               markdown="主角冲动。", client_mutation_id="edit")
    with pytest.raises(DocumentConflict):
        await service.proposals.accept([(await service.proposals.list(script.id))[0]["id"]],
            base_revision_id=script.current_revision_id, client_mutation_id="accept")
    assert changed.revision.markdown == "主角冲动。"


async def test_concurrent_same_task_cannot_clobber_completed_job(tmp_path):
    import asyncio
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="旧句", client_mutation_id="create")
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=0, end=2, scope="selection", mode="dialogue", instruction="", preserve="",
        client_mutation_id="rewrite")
    entered = 0
    both = asyncio.Event()
    class WaitingRuntime(Runtime):
        async def run_structured(self, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await both.wait()
            return await super().run_structured(**kwargs)
    results = await asyncio.gather(
        service.execute(job["id"], runtime=WaitingRuntime(), task_id="task"),
        service.execute(job["id"], runtime=WaitingRuntime(), task_id="task"),
        return_exceptions=True)
    assert any(isinstance(result, dict) and result["status"] == "completed" for result in results)
    assert (await service.get(job["id"]))["status"] == "completed"
    assert len(await service.proposals.list(doc.id)) == 1


async def test_list_rewrite_jobs_by_document_for_panel_recovery(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    first = await store.create(kind="episode_script", title="一", markdown="甲", client_mutation_id="d1")
    second = await store.create(kind="episode_script", title="二", markdown="乙", client_mutation_id="d2")
    service = RewriteService(store)
    job = await service.start(document_id=first.id, base_revision_id=first.current_revision_id,
        start=0, end=1, scope="selection", mode="dialogue", instruction="", preserve="",
        client_mutation_id="rewrite")
    assert [item["id"] for item in await service.list(first.id)] == [job["id"]]
    assert await service.list(second.id) == []


async def test_refine_candidate_must_use_its_exact_document_range(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="同句😀\n\n同句😀", client_mutation_id="create")
    service = RewriteService(store)
    second = doc.revision.blocks[1]
    proposal = await service.proposals.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=second.id, start=0, end=3, before="同句😀", after="新句", reason="自然",
        round_id="round", client_mutation_id="p")
    with pytest.raises(DocumentConflict):
        await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
            start=0, end=3, scope="selection", mode="dialogue", instruction="再改", preserve="",
            reference_proposal_id=proposal["id"], client_mutation_id="wrong")
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=5, end=8, scope="selection", mode="dialogue", instruction="再改", preserve="",
        reference_proposal_id=proposal["id"], client_mutation_id="correct")
    assert job["before"] == "同句😀"
    assert job["reference"]["after"] == "新句"


@pytest.mark.parametrize("scope", ["selection", "scene"])
async def test_refine_whole_patch_preserves_original_authorized_scope(tmp_path, scope):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    if scope == "selection":
        text = "\u524d\u6587\u7532\n\n\u4e59\u540e\u6587"
        start, end = 2, 6
    else:
        text = "# \u7b2c\u4e00\u96c6\n\n### 1-1 \u591c \u5185 \u8d26\u623f\n\u7532\n\n\u4e59\n\n### 1-2 \u591c \u5916 \u7801\u5934\n\u540e\u6587"
        start = text.index("\u7532")
        end = start
    doc = await store.create(kind="episode_script", title="one", markdown=text, client_mutation_id="create")
    service = RewriteService(store)
    first = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=start, end=end, scope=scope, mode="dialogue", instruction="", preserve="",
        client_mutation_id="first")
    await service.execute(first["id"], runtime=Runtime(), task_id="first-task")
    proposal = (await service.proposals.list(doc.id))[0]
    assert proposal["block_id"] is None
    assert proposal["before"] == text
    with pytest.raises(DocumentConflict):
        await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
            start=0, end=len(text), scope="episode", mode="dialogue", instruction="", preserve="",
            reference_proposal_id=proposal["id"], client_mutation_id="widen")
    refined = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=first["start"], end=first["end"], scope=scope, mode="dialogue",
        instruction="refine", preserve="", reference_proposal_id=proposal["id"],
        client_mutation_id="refine")
    assert refined["scope"] == scope
    assert refined["before"] == first["before"]
    assert refined["reference"]["before"] == first["before"]
    assert refined["reference"]["after"] == "新台词" + chr(0x2728)
    if scope == "scene":
        with pytest.raises(DocumentConflict):
            await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
                start=first["start"], end=first["end"], scope="selection", mode="dialogue",
                instruction="", preserve="", reference_proposal_id=proposal["id"],
                client_mutation_id="scope-switch")
    await service.execute(refined["id"], runtime=Runtime(), task_id="refine-task")
    updated = (await service.proposals.list(doc.id))[-1]
    assert updated["before"] == text
    assert updated["after"] == text[:first["start"]] + "\u65b0\u53f0\u8bcd" + chr(0x2728) + text[first["end"]:]


async def test_scene_scope_accepts_existing_single_number_pipe_heading(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    text = "# 第一集\n\n## 1｜账房外·夜·外\n甲说话。\n\n## 2｜码头·日·外\n乙说话。"
    doc = await store.create(kind="episode_script", title="一", markdown=text, client_mutation_id="create")
    caret = text.index("甲说话")
    job = await RewriteService(store).start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=caret, end=caret, scope="scene", mode="dialogue", instruction="", preserve="",
        client_mutation_id="scene")
    assert job["before"].startswith("## 1｜账房外")
    assert "## 2｜" not in job["before"]
