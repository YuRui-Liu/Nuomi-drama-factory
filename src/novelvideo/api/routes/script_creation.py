"""Creative document endpoints scoped to a project."""
from dataclasses import asdict
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.script_creation.entities import EntityService
from novelvideo.script_creation.documents import import_episode_source
from novelvideo.script_creation.proposals import ProposalService
from novelvideo.script_creation.rewrite import RewriteService
from novelvideo.script_creation.consistency import ConsistencyService
from novelvideo.script_creation.generation import GenerationConflict, GenerationService, GenerationValidation
from novelvideo.task_backend.client import enqueue_project_task
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentStore, DocumentValidation

router = APIRouter()
PREFIX = "/projects/{project}/script-creation"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlockBody(Strict):
    id: str = ""
    markdown: str


class CreateBody(Strict):
    kind: str
    title: str
    markdown: str = ""
    episode_number: int | None = None
    client_mutation_id: str = Field(min_length=1)
    blocks: list[BlockBody] | None = None


class SaveBody(Strict):
    base_revision_id: str
    markdown: str
    client_mutation_id: str = Field(min_length=1)
    blocks: list[BlockBody] | None = None


class RestoreBody(Strict):
    revision_id: str
    base_revision_id: str
    client_mutation_id: str = Field(min_length=1)


class ImportBody(Strict):
    episode_number: int = Field(ge=1)


def _blocks(items):
    return [item.model_dump() for item in items] if items is not None else None


def _ok(value):
    if isinstance(value, list):
        return {"ok": True, "data": [asdict(item) for item in value]}
    return {"ok": True, "data": asdict(value)}


def _error(exc):
    if isinstance(exc, DocumentNotFound):
        return HTTPException(404, detail=str(exc))
    if isinstance(exc, DocumentConflict):
        return HTTPException(409, detail={"message": str(exc), "current_revision_id": exc.current_revision_id})
    return HTTPException(422, detail=str(exc))


async def _store(project, user, role):
    resolved = await resolve_project_scope(project, user, required_role=role)
    store = DocumentStore(Path(resolved.state_dir) / "data.db")
    await store.initialize()
    return store, resolved


@router.get(PREFIX + "/documents")
async def list_documents(project: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    return _ok(await store.list())


@router.post(PREFIX + "/documents")
async def create_document(project: str, body: CreateBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.create(**body.model_dump(exclude={"blocks"}), blocks=_blocks(body.blocks)))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/documents/{document_id}")
async def get_document(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    try:
        return _ok(await store.get(document_id))
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.put(PREFIX + "/documents/{document_id}")
async def save_document(project: str, document_id: str, body: SaveBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.save(document_id, **body.model_dump(exclude={"blocks"}), blocks=_blocks(body.blocks)))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/documents/{document_id}/revisions")
async def list_revisions(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    try:
        return _ok(await store.revisions(document_id))
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/documents/{document_id}/restore")
async def restore_document(project: str, document_id: str, body: RestoreBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.restore(document_id, **body.model_dump()))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/imports")
async def import_document(project: str, body: ImportBody, user: dict = Depends(get_api_user)):
    store, resolved = await _store(project, user, "editor")
    sqlite = await make_sqlite_store_for_context(resolved.ctx)
    try:
        return _ok(await import_episode_source(store, EpisodeSourceStore(sqlite), body.episode_number))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc
    finally:
        await sqlite.close()


class GenerationBody(Strict):
    mode: str
    brief_id: str = Field(min_length=1)
    script_mode: str
    episode_count: int = Field(ge=1, le=100)
    episode_number: int = Field(default=1, ge=1)
    instruction: str = Field(default='', max_length=8000)
    client_mutation_id: str = Field(min_length=1, max_length=128)


class RebaseBody(Strict):
    client_mutation_id: str = Field(min_length=1, max_length=128)


def _generation_error(exc):
    if isinstance(exc, (GenerationConflict, DocumentConflict)):
        return HTTPException(409, detail={'code': 'GENERATION_CONFLICT', 'message': str(exc)})
    return _error(exc)


def _saved_settings(brief):
    match = re.search(r'<!-- nuomi-script-settings\n([\s\S]*?)\n-->', brief.revision.markdown)
    if not match:
        return None
    try:
        settings = json.loads(match.group(1))
    except (ValueError, TypeError) as exc:
        raise GenerationValidation('saved creative settings are invalid') from exc
    if settings.get('mode') not in {'single', 'series'} or not isinstance(settings.get('episodeCount'), int):
        raise GenerationValidation('saved creative settings are invalid')
    return settings


def _validate_saved_settings(brief, body: GenerationBody):
    settings = _saved_settings(brief)
    if settings and (settings['mode'] != body.script_mode or settings['episodeCount'] != body.episode_count):
        raise GenerationValidation('saved creative settings changed; refresh before generating')


async def _queue_generation(resolved, run, *, requeue=False):
    if resolved.ctx is None:
        raise HTTPException(409, detail={'code': 'project_context_required'})
    if run['status'] in {'completed', 'failed', 'paused'} and not requeue:
        return {'run': run, 'task_id': run.get('task_id'), 'task_type': 'script_creation_generation'}
    scope = f"run:{run['id']}"
    if run['status'] in {'failed', 'paused'}:
        store = DocumentStore(Path(resolved.state_dir) / 'data.db')
        run = await store.generation_requeue(run['id'])
    try:
        queued = await enqueue_project_task(
            resolved.ctx, task_type='script_creation_generation', queue_kind='default', episode=0,
            scope=scope, payload={'project_id': str(resolved.ctx.project_id), 'run_id': run['id']})
    except Exception as exc:
        if run['status'] in {'pending', 'failed', 'paused'}:
            store = DocumentStore(Path(resolved.state_dir) / 'data.db')
            current = await store.generation_get(run['id'])
            if current['status'] != 'running':
                current.update(status='failed', error=f'任务提交失败：{exc}')
                await store.generation_update(run['id'], current)
        raise
    return {'run': run, 'task_id': queued.task_state.task_id, 'task_type': 'script_creation_generation',
            'task_key': project_task_state_key('script_creation_generation', str(resolved.ctx.project_id), 0, scope=scope),
            'scope': scope, 'backend': queued.backend, 'queue': queued.queue}


@router.get(PREFIX + '/generations')
async def list_generations(project: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    return {'ok': True, 'data': await GenerationService(store).list()}


@router.post(PREFIX + '/generations', status_code=status.HTTP_202_ACCEPTED)
async def create_generation(project: str, body: GenerationBody,
                            user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    try:
        brief = await store.get(body.brief_id)
        _validate_saved_settings(brief, body)
        run = await GenerationService(store).start(
            mode=body.mode, brief_id=body.brief_id, script_mode=body.script_mode,
            episode_count=body.episode_count, episode_number=body.episode_number,
            instruction=body.instruction, mutation_id=body.client_mutation_id)
        return {'ok': True, 'data': await _queue_generation(resolved, run)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, GenerationConflict, GenerationValidation) as exc:
        raise _generation_error(exc) from exc


@router.get(PREFIX + '/generations/{run_id}')
async def get_generation(project: str, run_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await GenerationService(store).get(run_id)}
    except DocumentNotFound as exc:
        raise _generation_error(exc) from exc


@router.post(PREFIX + '/generations/{run_id}/retry', status_code=status.HTTP_202_ACCEPTED)
async def retry_generation(project: str, run_id: str,
                           user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    service = GenerationService(store)
    try:
        run = await service.get(run_id)
        if run['status'] == 'running':
            state = get_task_manager().get_task_for_project(
                resolved.ctx, 'script_creation_generation', 0, scope=f'run:{run_id}')
            if state and state.status in {'submitting', 'queued', 'running'}:
                raise GenerationConflict('generation is still running')
            run['status'] = 'paused'
            run = await store.generation_update(run_id, run, expected_task_id=run['task_id'])
        if run['status'] not in {'failed', 'paused', 'pending'}:
            raise GenerationConflict('only failed or paused generation can retry')
        return {'ok': True, 'data': await _queue_generation(resolved, run, requeue=True)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _generation_error(exc) from exc


@router.post(PREFIX + '/generations/{run_id}/rebase', status_code=status.HTTP_202_ACCEPTED)
async def rebase_generation(project: str, run_id: str, body: RebaseBody,
                            user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    service = GenerationService(store)
    try:
        old = await service.get(run_id)
        if old['status'] not in {'needs_rebase', 'failed', 'paused'}:
            raise GenerationConflict('run does not need rebase')
        brief = await store.get(old['brief_id'])
        settings = _saved_settings(brief)
        script_mode = settings['mode'] if settings else old['script_mode']
        episode_count = settings['episodeCount'] if settings else old['episode_count']
        if old['mode'] == 'continue' and (script_mode != 'series' or old['episode_number'] > episode_count):
            raise GenerationValidation('current settings no longer include the target episode')
        run = await service.start(mode=old['mode'], brief_id=old['brief_id'],
            script_mode=script_mode, episode_count=episode_count,
            episode_number=old['episode_number'], instruction=old['instruction'],
            mutation_id=body.client_mutation_id)
        return {'ok': True, 'data': await _queue_generation(resolved, run)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _generation_error(exc) from exc


@router.get(PREFIX + '/candidates/{candidate_id}')
async def get_generation_candidate(project: str, candidate_id: str,
                                   user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await store.generation_candidate(candidate_id)}
    except DocumentNotFound as exc:
        raise _generation_error(exc) from exc


class RewriteBody(Strict):
    document_id: str
    base_revision_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    scope: str
    mode: str
    instruction: str = Field(default='', max_length=8000)
    preserve: str = Field(default='', max_length=4000)
    context_revisions: dict[str, str] = Field(default_factory=dict)
    reference_proposal_id: str | None = None
    client_mutation_id: str = Field(min_length=1, max_length=128)


class AcceptProposalsBody(Strict):
    proposal_ids: list[str] = Field(min_length=1)
    base_revision_id: str
    client_mutation_id: str = Field(min_length=1)


@router.post(PREFIX + '/rewrites', status_code=status.HTTP_202_ACCEPTED)
async def create_rewrite(project: str, body: RewriteBody,
                         user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    try:
        job = await RewriteService(store).start(**body.model_dump())
        if job['status'] != 'pending':
            return {'ok': True, 'data': job}
        queued = await enqueue_project_task(
            resolved.ctx, task_type='script_creation_rewrite', queue_kind='default', episode=0,
            scope=f"rewrite:{job['id']}",
            payload={'project_id': str(resolved.ctx.project_id), 'job_id': job['id']})
        return {'ok': True, 'data': {**job, 'queued_task_id': queued.task_state.task_id}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + '/rewrites/{job_id}')
async def get_rewrite(project: str, job_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await RewriteService(store).get(job_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.get(PREFIX + '/documents/{document_id}/rewrites')
async def list_rewrites(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await RewriteService(store).list(document_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.get(PREFIX + '/documents/{document_id}/proposals')
async def list_proposals(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await ProposalService(store).list(document_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/candidates/{candidate_id}/review')
async def review_generation_candidate(project: str, candidate_id: str,
                                      user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'editor')
    try:
        return {'ok': True, 'data': await ProposalService(store).from_candidate(candidate_id)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/proposals/accept')
async def accept_proposals(project: str, body: AcceptProposalsBody,
                           user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'editor')
    try:
        return _ok(await ProposalService(store).accept(body.proposal_ids,
            base_revision_id=body.base_revision_id, client_mutation_id=body.client_mutation_id))
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/proposals/{proposal_id}/discard')
async def discard_proposal(project: str, proposal_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'editor')
    try:
        return {'ok': True, 'data': await ProposalService(store).discard(proposal_id)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


class ConsistencyBody(Strict):
    episode_document_id: str
    context_revisions: dict[str, str]
    proposal_id: str | None = None
    client_mutation_id: str = Field(min_length=1)


class IntentionalBody(Strict):
    reason: str = Field(min_length=1)


class TargetRewritesBody(Strict):
    target_document_ids: list[str] = Field(min_length=1)


@router.get(PREFIX + '/consistency-runs')
async def list_consistency_runs(project: str, episode_document_id: str | None = None,
                                user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await ConsistencyService(store).list(episode_document_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/consistency-runs', status_code=status.HTTP_202_ACCEPTED)
async def create_consistency_run(project: str, body: ConsistencyBody,
                                 user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    try:
        run = await ConsistencyService(store).start(**body.model_dump())
        if run['status'] != 'pending':
            return {'ok': True, 'data': run}
        queued = await enqueue_project_task(resolved.ctx, task_type='script_creation_consistency',
            queue_kind='default', episode=0, scope=f"consistency:{run['id']}",
            payload={'project_id': str(resolved.ctx.project_id), 'run_id': run['id']})
        return {'ok': True, 'data': {**run, 'queued_task_id': queued.task_state.task_id}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + '/consistency-runs/{run_id}')
async def get_consistency_run(project: str, run_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await ConsistencyService(store).get(run_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/consistency-issues/{issue_id}/intentional')
async def mark_consistency_intentional(project: str, issue_id: str, body: IntentionalBody,
                                       user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'editor')
    try:
        return {'ok': True, 'data': await ConsistencyService(store).mark_intentional(issue_id, reason=body.reason)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + '/consistency-issues/{issue_id}/target-rewrites')
async def list_consistency_targets(project: str, issue_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, 'viewer')
    try:
        return {'ok': True, 'data': await ConsistencyService(store).target_selections(issue_id)}
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + '/consistency-issues/{issue_id}/target-rewrites', status_code=status.HTTP_202_ACCEPTED)
async def create_consistency_targets(project: str, issue_id: str, body: TargetRewritesBody,
                                     user: dict = Depends(require_scope('tasks:submit'))):
    store, resolved = await _store(project, user, 'editor')
    try:
        jobs = await ConsistencyService(store).create_target_rewrites(issue_id,
            target_document_ids=body.target_document_ids)
        result = []
        for job in jobs:
            if job['status'] == 'pending':
                queued = await enqueue_project_task(resolved.ctx, task_type='script_creation_rewrite',
                    queue_kind='default', episode=0, scope=f"rewrite:{job['id']}",
                    payload={'project_id': str(resolved.ctx.project_id), 'job_id': job['id']})
                result.append({**job, 'queued_task_id': queued.task_state.task_id})
            else:
                result.append(job)
        return {'ok': True, 'data': result}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


class EntityRelationBody(Strict):
    kind: str
    entity_id: str


class EntityAppearanceBody(Strict):
    kind: str
    status: str
    episode_number: int | None = None
    document_id: str | None = None
    revision_id: str | None = None


class TextAssetBody(Strict):
    name: str = Field(min_length=1)
    description: str = ""


class EntityBody(Strict):
    document_id: str
    base_revision_id: str
    block_id: str
    name: str = Field(min_length=1)
    client_mutation_id: str = Field(min_length=1)
    entity_id: str | None = None
    asset_id: str | None = None
    create_text: TextAssetBody | None = None
    relations: list[EntityRelationBody] | None = None
    appearances: list[EntityAppearanceBody] | None = None


async def _entities(project, user, role):
    store, resolved = await _store(project, user, role)
    sqlite = await make_sqlite_store_for_context(resolved.ctx)
    try:
        await sqlite.initialize()
    finally:
        await sqlite.close()
    service = EntityService(store)
    await service.initialize()
    return service


@router.get(PREFIX + "/entities")
async def list_entities(project: str, document_id: str | None = None, user: dict = Depends(get_api_user)):
    service = await _entities(project, user, "viewer")
    return {"ok": True, "data": await service.list(document_id)}


@router.get(PREFIX + "/assets")
async def list_entity_assets(project: str, asset_type: str, user: dict = Depends(get_api_user)):
    service = await _entities(project, user, "viewer")
    try:
        return {"ok": True, "data": await service.assets(asset_type)}
    except DocumentValidation as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/entities")
async def put_entity(project: str, body: EntityBody, user: dict = Depends(get_api_user)):
    service = await _entities(project, user, "editor")
    try:
        return {"ok": True, "data": await service.put(**body.model_dump())}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc
