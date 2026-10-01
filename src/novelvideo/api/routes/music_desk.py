"""Authenticated library and optional post-production soundtrack desk."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import resolve_project_scope
from novelvideo.freezone.paths import resolve_static_url_to_path
from novelvideo.media_capabilities.music.runninghub_acestep import MusicRequest
from novelvideo.music.media import persist_media, digest
from novelvideo.music.models import Model, MusicPlan, validate_sources
from novelvideo.music.store import Conflict
from novelvideo.music.service import get_store, media_root, public_asset
from novelvideo.music.matching import match_assets

router = APIRouter()
PREFIX = '/projects/{project}/music'


@contextmanager
def errors():
    try:
        yield
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


def owner(user):
    value = str(user.get('id') or user.get('username') or '')
    if not value:
        raise HTTPException(401, '需要登录')
    return value


def personal(user):
    if user.get('credential_kind') == 'agent_session':
        raise HTTPException(403, '项目代理不能访问整个个人音乐库')
    return owner(user)


class AssetPatch(Model):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=50)
    archived: bool | None = None
    library: bool | None = None
    vocals: str | None = Field(default=None, pattern='^(none|present|unknown)$')


@router.get('/music-library/assets')
def library(user: dict = Depends(get_api_user)):
    return {'ok':True, 'data':[public_asset(a) for a in get_store().list_assets(personal(user))]}


@router.post('/music-library/assets')
async def upload(file: UploadFile, user: dict = Depends(get_api_user)):
    who = personal(user)
    root = media_root(); root.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or 'music.wav').suffix.lower()
    if suffix not in {'.wav','.mp3','.m4a','.aac','.ogg','.flac'}:
        raise HTTPException(422, '请选择 WAV、MP3、M4A、AAC、OGG 或 FLAC')
    with tempfile.TemporaryDirectory(dir=root) as folder:
        path = Path(folder)/('upload'+suffix)
        size = 0
        with path.open('wb') as stream:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > 100*1024*1024:
                    raise HTTPException(413, '音乐文件不能超过 100 MB')
                stream.write(chunk)
        with errors():
            info = await run_in_threadpool(persist_media, path, root, who, 'audio')
            asset = get_store().add_asset(who, path=info['path'], sha256=info['sha256'], duration_ms=info['durationMs'], name=Path(file.filename or '音乐').stem[:200], origin='upload')
    return {'ok':True, 'data':public_asset(asset)}


@router.patch('/music-library/assets/{asset_id}')
def patch_asset(asset_id: str, body: AssetPatch, user: dict = Depends(get_api_user)):
    with errors():
        result = get_store().update_asset(personal(user), asset_id, body.revision, body.model_dump(exclude_none=True, exclude={'revision'}))
    return {'ok':True, 'data':public_asset(result)}


@router.get('/music-library/versions/{version_id}/audio')
def personal_audio(version_id: str, user: dict = Depends(get_api_user)):
    with errors():
        version = get_store().version(version_id, owner=personal(user))
        return FileResponse(version['path'], headers={'X-Content-Type-Options':'nosniff'})


@router.get(PREFIX + '/versions/{version_id}/media')
async def project_media(project: str, version_id: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    with errors():
        version = get_store().version(version_id, project=project)
        return FileResponse(version['path'], headers={'X-Content-Type-Options':'nosniff'})


class VersionRequest(Model):
    versionId: str = Field(min_length=1, max_length=128)


@router.post(PREFIX + '/favorites')
async def favorite(project: str, body: VersionRequest, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='editor')
    with errors():
        asset = get_store().reference(owner(user), project, body.versionId, favorite=True)
    return {'ok':True, 'data':public_asset(asset, project)}


@router.get(PREFIX + '/favorites')
async def favorites(project: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    return {'ok':True, 'data':[public_asset(a, project) for a in get_store().favorites(project)]}


class MatchRequest(Model):
    query: str = Field(default='', max_length=1000)
    durationMs: int = Field(default=0, ge=0)
    instrumentalOnly: bool = False


@router.post(PREFIX + '/matches')
async def matches(project: str, body: MatchRequest, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    store = get_store()
    assets = {a['versionId']:public_asset(a, project) for a in store.favorites(project)}
    if user.get('credential_kind') != 'agent_session':
        assets.update({a['versionId']:public_asset(a) for a in store.list_assets(owner(user))})
    return {'ok':True, 'data':match_assets(assets.values(), body.query, body.durationMs, body.instrumentalOnly)}


class SourceRequest(Model):
    url: str = Field(min_length=1, max_length=4000)


@router.post(PREFIX + '/sources')
async def source(project: str, body: SourceRequest, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    if urlsplit(body.url).scheme or urlsplit(body.url).netloc:
        raise HTTPException(422, '请使用项目中已完成的成片')
    with errors():
        path = resolve_static_url_to_path(body.url, Path(scope.project_dir))
        info = await run_in_threadpool(persist_media, path, media_root(), owner(user), 'video')
        store = get_store()
        asset = store.add_asset(owner(user), path=info['path'], sha256=info['sha256'], duration_ms=info['durationMs'], name=path.stem, kind='video', library=False)
        result = store.reference(owner(user), project, asset['versionId'])
    return {'ok':True, 'data':public_asset(result, project)}


def validate_plan(store, project, who, plan, *, grant=False):
    source = store.version(plan.source.assetVersionId, project=project)
    if source['kind'] != 'video' or source['sha256'] != plan.source.sha256 or source['durationMs'] != plan.source.durationMs or digest(Path(source['path'])) != plan.source.sha256:
        raise Conflict('源成片版本已变化，请重新接入并核对配乐')
    versions = {}
    for track in plan.tracks:
        for clip in track.clips:
            asset = store.version(clip.assetVersionId, owner=who, project=project)
            if asset['kind'] != 'audio' or not Path(asset['path']).is_file():
                raise ValueError('配乐来源失效')
            versions[clip.assetVersionId] = asset
    validate_sources(plan, {k:a['durationMs'] for k,a in versions.items()})
    if grant:
        for version in versions:
            store.reference(who, project, version)
    return source, versions


@router.get(PREFIX + '/plans/{canvas}/{node}')
async def get_plan(project: str, canvas: str, node: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    store = get_store()
    plan = store.plan(project, canvas, node)
    versions = {c['assetVersionId'] for t in plan['tracks'] for c in t['clips']} if plan else set()
    with errors():
        assets = [public_asset(store.version(v, project=project), project) for v in versions]
    return {'ok':True, 'data':{'plan':plan,'assets':assets}}


@router.put(PREFIX + '/plans/{canvas}/{node}')
async def save_plan(project: str, canvas: str, node: str, body: MusicPlan, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='editor')
    with errors():
        store = get_store()
        await run_in_threadpool(validate_plan, store, project, owner(user), body, grant=True)
        result = store.save_plan(project, canvas, node, body.model_dump(), body.revision)
    return {'ok':True, 'data':result}


class RenderRequest(Model):
    requestId: str = Field(pattern=r'^[a-f0-9]{32}$')
    plan: MusicPlan
    preview: bool = False


async def enqueue(scope, store, project, job, created):
    if created:
        from novelvideo.ports import get_task_backend
        try:
            queued = await get_task_backend().enqueue_project_task(scope.ctx, task_type='music_generate' if job['kind']=='generate' else 'music_render', queue_kind='default' if job['kind']=='generate' else 'ffmpeg', episode=0, scope=job['id'], payload={'job_id':job['id']})
            store.update_job(project, job['id'], taskId=queued.task_state.task_id)
        except Exception:
            store.update_job(project, job['id'], status='dispatch_unknown', error='任务提交状态未知，请查看任务列表，勿重复提交')
            raise HTTPException(503, '任务提交状态未知，已保留回执')
    return {'ok':True, 'data':public_job(store.job(project, job['id']), project)}


def public_job(job, project):
    data = {k:v for k,v in job.items() if k not in {'path', 'audioPath', 'input'}}
    if job.get('status') == 'completed' and job.get('kind') == 'render':
        from urllib.parse import quote
        prefix = f'/api/v1/projects/{quote(project,safe="")}/music/jobs/{job["id"]}'
        data['audioUrl'] = prefix + '/audio'
        if job.get('path'):
            data['videoUrl'] = prefix + '/video'
    if job.get('candidate'):
        data['candidate'] = public_asset(job['candidate'], project)
    return data


@router.post(PREFIX + '/render-jobs')
async def render(project: str, body: RenderRequest, user: dict = Depends(require_scope('tasks:submit'))):
    scope = await resolve_project_scope(project, user, required_role='editor')
    store = get_store()
    with errors():
        await run_in_threadpool(validate_plan, store, project, owner(user), body.plan, grant=True)
        job, created = store.create_job(project, body.requestId, 'render', body.model_dump(exclude={'requestId'}))
    return await enqueue(scope, store, project, job, created)


class GenerateRequest(Model):
    requestId: str = Field(pattern=r'^[a-f0-9]{32}$')
    request: MusicRequest


@router.get(PREFIX + '/capabilities')
async def capabilities(project: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    from novelvideo.music.generation import generation_configuration
    try:
        workflow, _ = await generation_configuration()
        return {'ok':True, 'data':{'generation':True, 'workflowId':workflow}}
    except (ValueError, RuntimeError, OSError) as exc:
        return {'ok':True, 'data':{'generation':False, 'reason':str(exc), 'workflowId':'2059090557116440578'}}


@router.post(PREFIX + '/generation-jobs')
async def generate(project: str, body: GenerateRequest, user: dict = Depends(require_scope('tasks:submit'))):
    scope = await resolve_project_scope(project, user, required_role='editor')
    from novelvideo.music.generation import generation_configuration
    with errors():
        try:
            workflow, fingerprint = await generation_configuration()
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from exc
        store = get_store()
        job, created = store.create_job(project, body.requestId, 'generate', {'request':body.request.model_dump(), 'workflow':workflow, 'contractHash':fingerprint, 'owner':owner(user)})
    return await enqueue(scope, store, project, job, created)


@router.get(PREFIX + '/jobs')
async def jobs(project: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    return {'ok':True, 'data':[public_job(j, project) for j in get_store().jobs(project)]}


@router.post(PREFIX + '/jobs/{job_id}/resume')
async def resume(project: str, job_id: str, user: dict = Depends(require_scope('tasks:submit'))):
    scope = await resolve_project_scope(project, user, required_role='editor')
    store = get_store()
    with errors():
        job = store.job(project, job_id)
        if job['kind'] != 'generate' or not job.get('remoteTaskId'):
            raise ValueError('仅支持恢复已有远端任务的查询，不会重新生成')
        if not store.claim_job(project, job_id, expected='paused', status='running'):
            raise Conflict('任务正在运行或不支持恢复')
    return await enqueue(scope, store, project, job, True)


@router.get(PREFIX + '/jobs/{job_id}/{kind}')
async def output(project: str, job_id: str, kind: str, user: dict = Depends(get_api_user)):
    await resolve_project_scope(project, user, required_role='viewer')
    with errors():
        job = get_store().job(project, job_id)
        path = job.get('audioPath' if kind=='audio' else 'path' if kind=='video' else '')
        if job['status'] != 'completed' or not path:
            raise ValueError('输出尚未完成')
        return FileResponse(path, headers={'X-Content-Type-Options':'nosniff'})
