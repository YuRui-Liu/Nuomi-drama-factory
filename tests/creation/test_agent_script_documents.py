import pytest

from novelvideo.creation.agent_script_documents import AgentScriptDocuments
from novelvideo.script_creation.store import DocumentConflict, DocumentStore, DocumentValidation


@pytest.mark.asyncio
async def test_agent_draft_is_a_formal_episode_script_with_trusted_lineage(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    service = AgentScriptDocuments(store)

    draft = await service.create_draft(
        request_id="draft-request",
        project_id="project-1",
        canvas_id="concept-1",
        delegation_id="delegation-writer",
        writer={"adoption_id": "writer-1", "revision": 3, "name": "主笔"},
        episode_number=2,
        title="第二集：雨夜来信",
        markdown="# 第二集\n\n## 1. 雨夜·外\n女主发现一封旧信。\n",
        source_ref={"kind": "run_output", "output_id": "trusted-output-1",
                    "source_run_id": "run-1", "source_task_key": None,
                    "source_delegation_id": "delegation-source", "source_action_request_id": "action-1",
                    "source_operation_id": "operation-1"},
    )

    document = await store.get(draft["document_ref"]["document_id"])
    assert document.kind == "episode_script"
    assert document.episode_number == 2
    assert document.adopted_revision_id is None
    assert document.current_revision_id == draft["document_ref"]["revision_id"]
    assert document.source_origin == {
        "kind": "agent_script_draft",
        "project_id": "project-1",
        "canvas_id": "concept-1",
        "delegation_id": "delegation-writer",
        "member_id": "writer-1",
        "member_revision": 3,
        "source_ref": {"kind": "run_output", "output_id": "trusted-output-1",
                        "source_run_id": "run-1", "source_task_key": None,
                        "source_delegation_id": "delegation-source", "source_action_request_id": "action-1",
                        "source_operation_id": "operation-1"},
    }


@pytest.mark.asyncio
async def test_agent_review_is_bound_to_exact_document_revision_and_independent_reviewer(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    service = AgentScriptDocuments(store)
    draft = await service.create_draft(
        request_id="draft-request",
        project_id="project-1",
        canvas_id="concept-1",
        delegation_id="delegation-writer",
        writer={"adoption_id": "writer-1", "revision": 3, "name": "主笔"},
        episode_number=2,
        title="第二集",
        markdown="# 第二集\n\n## 1. 雨夜·外\n女主发现一封旧信。\n",
    )
    ref = draft["document_ref"]
    report = {
        "conclusion": "passed",
        "summary": "因果清楚，场景可拍。",
        "issues": [],
    }

    review = await service.record_review(
        request_id="review-request",
        project_id="project-1",
        canvas_id="concept-1",
        delegation_id="delegation-reviewer",
        reviewer={"adoption_id": "reviewer-1", "revision": 2, "name": "剧本编辑"},
        document_id=ref["document_id"],
        revision_id=ref["revision_id"],
        report=report,
    )
    assert review["status"] == "passed"
    assert review["candidate_hash"]
    assert review["document_ref"] == ref
    assert await service.current_review(ref["document_id"], ref["revision_id"]) == review

    with pytest.raises(DocumentValidation, match="independent"):
        await service.record_review(
            request_id="review-by-writer",
            project_id="project-1",
            canvas_id="concept-1",
            delegation_id="delegation-writer",
            reviewer={"adoption_id": "writer-1", "revision": 3, "name": "主笔"},
            document_id=ref["document_id"],
            revision_id=ref["revision_id"],
            report=report,
        )

    changed = await store.save(
        ref["document_id"], base_revision_id=ref["revision_id"],
        markdown="# 第二集\n\n## 1. 雨夜·外\n女主把信交给弟弟。\n",
        client_mutation_id="manual-edit",
    )
    assert await service.current_review(ref["document_id"], changed.current_revision_id) is None
    assert await service.current_review(ref["document_id"], ref["revision_id"]) is None


@pytest.mark.asyncio
async def test_old_review_migration_recovers_episode_only_from_formal_document_metadata(tmp_path):
    import json

    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    source_origin = {
        "kind": "agent_script_draft", "project_id": "project-1", "canvas_id": "concept-1",
        "delegation_id": "writer-task", "member_id": "writer", "member_revision": 1,
        "source_ref": None,
    }
    document = await store.create(kind="episode_script", title="第二集", markdown="# 第二集\n正文",
        episode_number=2, source_origin=source_origin, client_mutation_id="legacy-document")
    report = {"conclusion": "passed", "summary": "旧版本审阅记录。", "issues": []}
    async with store._db() as db:
        await db.execute("""CREATE TABLE agent_script_reviews (
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
            project_id TEXT NOT NULL, canvas_id TEXT NOT NULL, document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL, candidate_hash TEXT NOT NULL, source_delegation_id TEXT NOT NULL,
            reviewer_delegation_id TEXT NOT NULL, reviewer_adoption_id TEXT NOT NULL,
            reviewer_revision INTEGER NOT NULL, reviewer_name TEXT NOT NULL,
            report TEXT NOT NULL, created_at TEXT NOT NULL)""")
        await db.execute("""INSERT INTO agent_script_reviews VALUES (
            'review-old', 'request-old', 'hash-old', 'project-1', 'concept-1', ?, ?, 'candidate-hash',
            'writer-task', 'reviewer-task', 'reviewer', 2, '剧本编辑', ?, '2026-10-18T00:00:00Z')""",
            (document.id, document.current_revision_id, json.dumps(report, ensure_ascii=False)))
        await db.commit()

    service = AgentScriptDocuments(store)
    await service.initialize()
    review = await service.current_review(document.id, document.current_revision_id)
    assert review["document_ref"]["episode_number"] == 2
    async with store._db() as db:
        column = next(row for row in await (await db.execute(
            "PRAGMA table_info(agent_script_reviews)")).fetchall() if row["name"] == "episode_number")
        assert column["notnull"] == 0
        assert column["dflt_value"] is None


@pytest.mark.asyncio
async def test_agent_draft_request_replay_is_idempotent_and_rejects_reused_payload(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    service = AgentScriptDocuments(store)
    args = dict(
        request_id="same-request",
        project_id="project-1",
        canvas_id="concept-1",
        delegation_id="delegation-writer",
        writer={"adoption_id": "writer-1", "revision": 3, "name": "主笔"},
        episode_number=2,
        title="第二集",
        markdown="# 第二集\n\n## 1. 雨夜·外\n剧情。\n",
    )
    first = await service.create_draft(**args)
    replay = await service.create_draft(**args)
    assert replay == first

    with pytest.raises(DocumentConflict, match="reused"):
        await service.create_draft(**{**args, "markdown": "different"})


@pytest.mark.asyncio
async def test_agent_draft_requires_explicit_valid_episode_identity(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    service = AgentScriptDocuments(store)
    args = dict(
        request_id="bad-request",
        project_id="project-1",
        canvas_id="concept-1",
        delegation_id="delegation-writer",
        writer={"adoption_id": "writer-1", "revision": 3, "name": "主笔"},
        episode_number=None,
        title="第二集",
        markdown="# 第二集\n内容\n",
    )

    with pytest.raises(DocumentValidation, match="episode"):
        await service.create_draft(**args)


@pytest.mark.asyncio
async def test_agent_draft_projects_a_read_only_formal_document_reference_to_concept_canvas(tmp_path):
    from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import read_canvas

    objects = ObjectStore(tmp_path)
    created = CanvasWorkspace(objects, project_id='project-1').create(
        'alice', CanvasCreate(request_id='concept', kind='concept', title='构思画布'))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]['canvas_id']

    documents = DocumentStore(tmp_path / 'data.db')
    await documents.initialize()
    service = AgentScriptDocuments(documents)
    draft = await service.create_draft(
        request_id='draft-request', project_id='project-1', canvas_id=canvas_id,
        delegation_id='writer-task', writer={'adoption_id':'writer','revision':1,'name':'主笔'},
        episode_number=2, title='第二集', markdown='# 第二集\n\n## 2-1 雨夜\n正文。\n',
    )
    projection = service.project_draft(state_dir=tmp_path, project_id='project-1',
        canvas_id=canvas_id, actor='alice', document_ref=draft['document_ref'],
        title=draft['title'], markdown=draft['markdown'], request_id='draft-request')
    assert projection['status'] == 'applied'
    graph = read_canvas(tmp_path, canvas_id)
    node = next(item for item in graph['nodes'] if item['id'] == projection['node_id'])
    assert node['type'] == 'textAnnotationNode'
    assert node['data']['referenceOnly'] is True
    assert node['data']['formalScriptRef']['document_id'] == draft['document_ref']['document_id']
    assert node['data']['formalScriptRef']['episode_number'] == 2
    replay = service.project_draft(state_dir=tmp_path, project_id='project-1',
        canvas_id=canvas_id, actor='alice', document_ref=draft['document_ref'],
        title=draft['title'], markdown=draft['markdown'], request_id='draft-request')
    assert replay == projection
    assert len(read_canvas(tmp_path, canvas_id)['nodes']) == 1
