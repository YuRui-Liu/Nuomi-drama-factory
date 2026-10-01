"""Authenticated directing-method import. Parsed text remains inert user data."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import require_scope
from novelvideo.api.routes.director_plans import _resolve, _resolve_source_revision
from novelvideo.api.routes.creative_studios import DocumentId, _store
from novelvideo.creative_studios.director import claim_submission, director_snapshot, extract_method, structure_method

router = APIRouter()


class ImportTeamMethod(BaseModel):
    document_id: DocumentId
    expected_document_revision: int = Field(strict=True, gt=0)
    expected_draft_revision: int = Field(strict=True, ge=0)


@router.post('/projects/{project}/agent-team/import-director-method')
async def import_team_method(project: str, body: ImportTeamMethod, user: dict = Depends(require_scope('tasks:submit'))):
    from novelvideo.api.routes.agent_teams import project_service, errors
    from novelvideo.agent_teams.director_bridge import import_director_method
    service = await project_service(project, user, 'editor')
    store = await _store(project, user)
    document = store.get('director', body.document_id)
    if document is None:
        raise HTTPException(404, '导演配置不存在')
    with errors():
        return import_director_method(service, service.project_id, document, body.expected_document_revision, body.expected_draft_revision)


class TeamPlanRequest(BaseModel):
    episode: int = Field(strict=True, gt=0)
    expected_active_revision: int = Field(strict=True, gt=0)


@router.post('/projects/{project}/agent-team/director-plan')
async def plan_with_team(project: str, body: TeamPlanRequest, user: dict = Depends(require_scope('tasks:submit'))):
    from novelvideo.api.routes.agent_teams import project_service, errors
    from novelvideo.agent_teams.store import RevisionConflict
    from novelvideo.ports import get_task_backend
    service = await project_service(project, user, 'editor')
    ctx = await _resolve(project, user, role='editor')
    active = service.store.get_binding(service.project_id)
    with errors():
        if not active or active['active_revision'] != body.expected_active_revision:
            raise RevisionConflict('团队启用版本已变化，请重新加载')
        source_revision = await _resolve_source_revision(ctx, body.episode)
        from novelvideo.task_identity import project_task_state_key
        from novelvideo.task_state import get_task_manager
        scope = f"team:{body.expected_active_revision}:source:{source_revision}"
        task_key = project_task_state_key('director_plan', str(ctx.project_id), body.episode, scope=scope)
        existing = get_task_manager().get_task_for_project(ctx, 'director_plan', body.episode, scope=scope)
        if existing is not None:
            return {'ok': True, 'task_id': existing.task_id, 'status': str(existing.status), 'reused': True}
        if not claim_submission(service.store.path, task_key):
            raise HTTPException(409, '此团队和剧本版本已提交，请先查看任务中心与方案历史')
        queued = await get_task_backend().enqueue_project_task(ctx, task_type='director_plan', queue_kind='default', episode=body.episode,
            scope=scope,
            payload={'project_id': str(ctx.project_id), 'episode': body.episode, 'source_revision': source_revision,
                     'expected_agent_team_active_revision': body.expected_active_revision,
                     'display_name': f'导演方法 · 团队启用 v{body.expected_active_revision}'})
    return {'ok': True, 'task_id': queued.task_state.task_id, 'status': str(queued.task_state.status), 'reused': False}


class MethodText(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


@router.post("/projects/{project}/studios/director/import-text")
async def import_text(project: str, body: MethodText, user: dict = Depends(require_scope("tasks:submit"))):
    await _resolve(project, user, role="editor")
    try:
        return {"ok": True, "data": structure_method(body.text)}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/projects/{project}/studios/director/import-file")
async def import_file(project: str, file: UploadFile = File(...), user: dict = Depends(require_scope("tasks:submit"))):
    await _resolve(project, user, role="editor")
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        text = await run_in_threadpool(extract_method, file.filename or "", content)
        return {"ok": True, "data": structure_method(text)}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    finally:
        await file.close()


class StudioPlanRequest(BaseModel):
    episode: int = Field(gt=0)
    expected_revision: int = Field(gt=0)
    allow_adaptation: bool = False


@router.post("/projects/{project}/studios/director/{document_id}/plan")
async def plan_with_method(project: str, document_id: DocumentId, body: StudioPlanRequest, user: dict = Depends(require_scope("tasks:submit"))):
    from novelvideo.ports import get_task_backend
    from novelvideo.task_identity import project_task_state_key
    from novelvideo.task_state import get_task_manager
    ctx = await _resolve(project, user, role="editor")
    from pathlib import Path
    from novelvideo.agent_teams.store import AgentTeamStore
    team_path = Path(ctx.state_dir) / 'agent-team.db'
    if team_path.is_file() and AgentTeamStore(team_path).get_binding(ctx.project_id) is not None:
        raise HTTPException(409, '项目已启用团队方法，请使用团队导演规划入口；旧配置仅可导入共享草稿')
    try:
        store = await _store(project, user)
        document = store.get("director", document_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if document is None:
        raise HTTPException(404, "导演配置不存在")
    if document["revision"] != body.expected_revision:
        raise HTTPException(409, "导演配置已有新版本，请重新加载")
    try:
        snapshot = director_snapshot(document["data"], document_id, body.expected_revision)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    source_revision = await _resolve_source_revision(ctx, body.episode)
    task_type = "director_studio_adaptation" if body.allow_adaptation else "director_plan"
    if body.allow_adaptation and document["data"].get("allow_adaptation") is not True:
        raise HTTPException(409, "请先明确开启允许改编并保存配置")
    scope = f"studio:{document_id}:{body.expected_revision}:source:{source_revision}"
    task_key = project_task_state_key(task_type, str(ctx.project_id), body.episode, scope=scope)
    # Completed and failed tasks are also returned: uncertain submissions never
    # blindly create another paid run. Explicit new config versions create new runs.
    existing = get_task_manager().get_task_for_project(ctx, task_type, body.episode, scope=scope)
    if existing is not None:
        return {"ok": True, "task_id": existing.task_id, "task_key": task_key, "status": str(existing.status), "reused": True}
    if not claim_submission(store.path, task_key):
        raise HTTPException(409, "此配置版本已经提交；任务结果未知或已清理，请先查看任务中心与方案历史。确认需重新规划后保存一个新配置版本。")
    from hashlib import sha256
    from novelvideo.api.routes.agent_teams import errors
    with errors():
        queued = await get_task_backend().enqueue_project_task(ctx, task_type=task_type, queue_kind="default", episode=body.episode, scope=scope, payload={"project_id": str(ctx.project_id), "episode": body.episode, "source_revision": source_revision, "expected_agent_team_active_revision": 0, "studio_config": snapshot, "allow_adaptation": body.allow_adaptation, "result_document_id": "adaptation-" + sha256(task_key.encode()).hexdigest()[:32], "display_name": f"导演方法 · {document['name']}"})
    return {"ok": True, "task_id": queued.task_state.task_id, "task_key": task_key, "status": str(queued.task_state.status), "reused": False}


class AdoptAdaptations(BaseModel):
    expected_revision: int = Field(gt=0)
    source_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/projects/{project}/studios/director/adaptations/{document_id}/adopt")
async def adopt_adaptations(project: str, document_id: DocumentId, body: AdoptAdaptations, user: dict = Depends(require_scope("tasks:submit"))):
    from novelvideo.creative_studios.store import RevisionConflict
    ctx = await _resolve(project, user, role="editor")
    store = await _store(project, user, write=True)
    doc = store.get("director", document_id)
    if doc is None or doc["data"].get("purpose") != "director-adaptation":
        raise HTTPException(404, "改编建议不存在")
    data = doc["data"]
    if await _resolve_source_revision(ctx, int(data["episode"])) != data["source_revision"]:
        raise HTTPException(409, "剧本原文已有新版本，请重新生成建议")
    available = {item["source_span_id"] for item in data["suggestions"]}
    if not set(body.source_ids) <= available:
        raise HTTPException(422, "采纳范围包含未知原文段落")
    try:
        adopted = store.save("director", document_id, doc["name"], {**data, "accepted_source_ids": list(dict.fromkeys(body.source_ids)), "status": "adopted_draft"}, body.expected_revision)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "data": adopted, "source_modified": False}
