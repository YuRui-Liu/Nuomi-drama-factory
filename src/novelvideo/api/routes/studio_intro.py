"""Project-scoped local title media tools and durable render candidates."""
from __future__ import annotations

import io
import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver, resolve_project_scope
from novelvideo.creative_studios.intro import IntroSpec, fonts, probe, project_file, render_frame, render_video, run
from novelvideo.task_state import get_task_manager
from novelvideo.production_workflow import ProductionWorkflowStore, production_workflow_project_lock
from novelvideo.media_capabilities.image.catalog import list_image_models
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.ports import get_task_backend

router = APIRouter(prefix='/projects/{project}/studios/intro-tools')
_slots = threading.BoundedSemaphore(2)
_lock = threading.Lock()
_instance = uuid.uuid4().hex


class FrameRequest(BaseModel):
    video: str
    time: float = Field(ge=0, le=86400)


class PreviewRequest(BaseModel):
    spec: IntroSpec
    time: float = Field(default=0, ge=0, le=30)


class RenderRequest(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    spec: IntroSpec
    source: dict[str, str | int] = Field(default_factory=dict)


class BackgroundRequest(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    prompt: str = Field(min_length=1, max_length=4000)
    model: str = Field(min_length=1, max_length=150)
    image_size: Literal['1K', '2K', '4K'] = '2K'
    quality: Literal['low', 'medium', 'high', 'auto'] = 'medium'
    ratio: Literal['16:9', '9:16', '1:1'] = '16:9'
    source: dict[str, str | int] = Field(default_factory=dict)


def _background_view(path: Path, ctx) -> dict:
    job = json.loads(path.read_text(encoding='utf-8'))
    task = get_task_manager().get_task_for_project(ctx, 'freezone_gen', 0, scope=job['id'])
    if task is not None:
        job.update(status=task.status, progress=task.progress, task_id=task.task_id, error=task.error)
        if task.status == 'completed':
            try:
                output = Path(str((task.result or {}).get('output_path', ''))).resolve()
                root = Path(ctx.output_dir).resolve()
                if not output.is_relative_to(root) or not output.is_file():
                    raise ValueError('生成结果不在当前项目内或文件已不可访问')
                with Image.open(output) as image:
                    image.verify()
                job['output'] = output.relative_to(root).as_posix()
            except (ValueError, OSError) as exc:
                job.update(status='failed', error=str(exc))
        _write(path, job)
    return job


@router.get('/background-models')
async def background_models(project: str, user: dict = Depends(get_api_user), store: MediaCapabilityStore = Depends(get_media_capability_store), resolver: CredentialResolver = Depends(get_media_credential_resolver)):
    await resolve_project_scope(project, user, required_role='viewer')
    return {'ok': True, 'data': [{'id': model.id, 'label': model.label, 'provider': model.provider, 'account': model.provider_id} for model in list_image_models(store, resolver)]}


@router.get('/background-jobs')
async def background_jobs(project: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    folder = _folder(Path(scope.project_dir), 'background-jobs')
    return {'ok': True, 'data': sorted([_background_view(path, scope.ctx) for path in folder.glob('*.json')], key=lambda item: item['created_at'], reverse=True)}


@router.post('/background-jobs')
async def submit_background(project: str, body: BackgroundRequest, user: dict = Depends(require_scope('tasks:submit')), store: MediaCapabilityStore = Depends(get_media_capability_store), resolver: CredentialResolver = Depends(get_media_credential_resolver)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    root = Path(scope.project_dir)
    folder = _folder(root, 'background-jobs')
    path = folder / f'{body.request_id}.json'
    import fcntl
    # Persist the submission intent before dispatch, then release the lock before await.
    # Repeated/unknown requests only inspect this receipt; they never submit twice.
    with _lock, (folder / '.submit.lock').open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        if path.exists():
            existing = _background_view(path, scope.ctx)
            if existing['input'] != body.model_dump():
                raise HTTPException(409, '此提交标识已用于其他背景请求')
            return {'ok': True, 'data': existing}
        model = next((item for item in list_image_models(store, resolver) if item.id == body.model), None)
        if model is None:
            raise HTTPException(422, '所选图像模型目前不可用，请刷新实际模型目录')
        if not body.prompt.strip():
            raise HTTPException(422, '请输入背景描述')
        job = dict(id=body.request_id, input=body.model_dump(), provider=model.provider, account=model.provider_id, status='submitting', progress=0, created_at=datetime.now(timezone.utc).isoformat())
        _write(path, job)
    try:
        queued = await get_task_backend().enqueue_project_task(scope.ctx, task_type='freezone_gen', queue_kind='default', episode=0, scope=body.request_id, payload={
            'job_id': body.request_id, 'project_dir': str(root), 'prompt': body.prompt,
            'aspect_ratio': body.ratio, 'image_size': body.image_size, 'reference_paths': [],
            'provider': None, 'model': body.model, 'quality': body.quality,
            'model_id': body.model, 'gen_mode': 'text_to_image', 'canvas_id': '', 'node_id': '',
            'task_family': 'creative_studios', 'task_label': '片头 AI 背景', 'display_name': '片头 AI 背景',
            'studio_source': body.source,
        })
        job.update(task_id=queued.task_state.task_id, status=queued.task_state.status)
    except HTTPException as exc:
        job.update(status='failed' if exc.status_code < 500 else 'unknown', error=str(exc.detail))
    except Exception as exc:
        job.update(status='unknown', error=f'提交结果待确认，请刷新任务回执，不要重复提交：{exc}')
    _write(path, job)
    return {'ok': True, 'data': _background_view(path, scope.ctx)}


def _asset(root: Path, extension: str) -> Path:
    folder = _folder(root, 'assets')
    return folder / f'{uuid.uuid4().hex}{extension}'


def _folder(root: Path, kind: str) -> Path:
    folder = (root / 'studios' / 'intro' / kind).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise HTTPException(403, '工作室输出目录越出项目范围')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write(path: Path, job: dict) -> None:
    job['updated_at'] = datetime.now(timezone.utc).isoformat()
    temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def _read(path: Path) -> dict:
    job = json.loads(path.read_text(encoding='utf-8'))
    if job['status'] in ('queued', 'running'):
        try:
            pid = job.get('pid', 0)
            if pid <= 0 or (pid == os.getpid() and job.get('instance') != _instance):
                raise ProcessLookupError()
            os.kill(pid, 0)
        except ProcessLookupError:
            job.update(status='failed', error='渲染进程已中断；输入已保留，可重试本地渲染。')
            _write(path, job)
    return job


def _validate(root: Path, spec: IntroSpec) -> None:
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('服务器未安装 ffmpeg / ffprobe，本地视频渲染不可用')
    if spec.font not in {f['id'] for f in fonts()}:
        raise ValueError('所选字体不可用，请重新选择')
    if spec.background:
        with Image.open(project_file(root, spec.background)) as image:
            image.verify()
    if spec.insert_video:
        path = project_file(root, spec.insert_video)
        # Insert only an existing episode output, never an arbitrary project file.
        if path.parent != (root / 'videos' / 'episodes').resolve() or path.suffix.lower() != '.mp4':
            raise ValueError('请选择当前项目已有的完整成片')
        if not any(s['codec_type'] == 'video' for s in probe(path)['streams']):
            raise ValueError('成片中没有可读取的视频轨道')


def _launch(root: Path, path: Path, job: dict, ctx) -> None:
    def work():
        with _slots:
            manager = get_task_manager()
            try:
                job.update(status='running', progress=0)
                _write(path, job)
                output = _folder(root, 'outputs') / f"{job['id']}.mp4"
                def progress(value):
                    job['progress'] = value
                    _write(path, job)
                    state = manager.get_task_for_project(ctx, 'studio_intro_render', 0, scope=job['id'])
                    if state and (state.status == 'cancelled' or state.cancel_requested_at):
                        raise ValueError('用户已取消片头渲染；可保留输入并重试。')
                    manager.update_progress_for_project(ctx, 'studio_intro_render', 0, scope=job['id'], progress=value, current_task='本地片头渲染', expected_task_id=job['task_id'])
                render_video(root, IntroSpec.model_validate(job['spec']), output, progress)
                if not any(stream['codec_type'] == 'video' for stream in probe(output)['streams']):
                    raise ValueError('导出文件没有可播放的视频轨道')
                job.update(status='completed', progress=1, output=output.relative_to(root).as_posix())
                with production_workflow_project_lock(ctx.state_dir):
                    store = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
                    store.register_candidate_version(slot_id='studio:intro:output', asset_kind='intro_video', version_id=job['id'], asset_path=job['output'], source_attempt_id=job['task_id'], qc_passed=True, generation_metadata={'render_spec': job['spec'], 'source': job.get('source', {}), 'render_type': 'local', 'technical_validation': 'ffprobe'}, actor=ctx.requester_username, at=datetime.now(timezone.utc), auto_provisional=False)
                manager.complete_task_for_project(ctx, 'studio_intro_render', 0, scope=job['id'], result={'output_path': str(output), 'candidate_id': job['id']}, current_task='完成', expected_task_id=job['task_id'])
            except Exception as exc:
                job.update(status='failed', error=str(exc)[:1600])
                manager.fail_task_for_project(ctx, 'studio_intro_render', 0, scope=job['id'], error=job['error'], expected_task_id=job['task_id'])
            finally:
                _write(path, job)
    threading.Thread(target=work, name=f"intro-{job['id']}", daemon=True).start()


@router.get('/capabilities')
async def capabilities(project: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    root = Path(scope.project_dir)
    videos = [p.relative_to(root).as_posix() for p in sorted((root / 'videos' / 'episodes').glob('*_final.mp4')) if p.is_file() and p.resolve().is_relative_to(root.resolve())]
    return {'ok': True, 'data': {
        'fonts': [{k: f[k] for k in ('id', 'name')} for f in fonts()],
        'videos': videos,
        'local_render': bool(shutil.which('ffmpeg') and shutil.which('ffprobe') and fonts()),
        'ai_effects': False,
        'ai_reason': '此片头编辑器尚未接入可确认能力和计价的 AI 效果服务。',
    }}


@router.post('/upload')
async def upload(project: str, file: UploadFile, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, '底图不能超过 20 MB')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 24_000_000:
                raise ValueError('底图不能超过 2400 万像素')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((3840, 3840))
            path = _asset(Path(scope.project_dir), '.png')
            image.save(path, format='PNG')
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, f'无法读取图片：{exc}') from exc
    return {'ok': True, 'data': {'path': path.relative_to(scope.project_dir).as_posix()}}


@router.post('/frame')
async def extract_frame(project: str, body: FrameRequest, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    root = Path(scope.project_dir)
    try:
        source = project_file(root, body.video)
        if source.parent != (root / 'videos' / 'episodes').resolve() or source.suffix.lower() != '.mp4':
            raise ValueError('请选择当前项目已有成片')
        info = await run_in_threadpool(probe, source)
        if body.time >= float(info['format']['duration']):
            raise ValueError('选帧时间超出成片时长')
        path = _asset(root, '.png')
        await run_in_threadpool(run, ['-ss', str(body.time), '-i', str(source), '-frames:v', '1', '-vf', "scale='min(1920,iw)':-2", str(path)])
        if not path.is_file():
            raise ValueError('该时刻没有可提取的画面')
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'ok': True, 'data': {'path': path.relative_to(root).as_posix()}}


@router.post('/preview')
async def preview(project: str, body: PreviewRequest, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    ratio = {'16:9': (640, 360), '9:16': (360, 640), '1:1': (640, 640)}[body.spec.ratio]
    try:
        if body.spec.background:
            with Image.open(project_file(Path(scope.project_dir), body.spec.background)) as source:
                background = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGB'), ratio)
        else:
            background = Image.new('RGB', ratio, '#111827')
        image = await run_in_threadpool(render_frame, background, body.spec, body.time)
        output = io.BytesIO()
        image.save(output, format='PNG')
        return Response(output.getvalue(), media_type='image/png', headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/jobs')
async def jobs(project: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    folder = _folder(Path(scope.project_dir), 'jobs')
    values = [_read(p) for p in folder.glob('*.json')]
    for job in values:
        if job['status'] == 'failed' and job.get('task_id'):
            get_task_manager().fail_task_for_project(scope.ctx, 'studio_intro_render', 0, scope=job['id'], error=job.get('error'), expected_task_id=job['task_id'])
    return {'ok': True, 'data': sorted(values, key=lambda j: j['created_at'], reverse=True)}


@router.post('/jobs')
async def submit(project: str, body: RenderRequest, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    root = Path(scope.project_dir)
    folder = _folder(root, 'jobs')
    path = folder / f'{body.request_id}.json'
    if not path.exists():
        try:
            await run_in_threadpool(_validate, root, body.spec)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
    # OS lock makes idempotency hold across multiple API worker processes.
    import fcntl
    with _lock, (folder / '.submit.lock').open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        if path.exists():
            job = _read(path)
            if job['spec'] != body.spec.model_dump() or job.get('source', {}) != body.source:
                raise HTTPException(409, '提交标识已用于其他片头，请重新提交')
            return {'ok': True, 'data': job}
        active = sum(_read(p)['status'] in ('queued', 'running') for p in folder.glob('*.json'))
        if active >= 4:
            raise HTTPException(429, '项目已有 4 个本地渲染任务，请等待完成')
        state = get_task_manager().create_task_for_project(scope.ctx, 'studio_intro_render', 0, scope=body.request_id, metadata={'task_family': 'creative_studios', 'display_name': '片头本地渲染', 'task_label': '片头本地渲染', 'studio': 'intro'})
        job = dict(id=body.request_id, task_id=state.task_id, spec=body.spec.model_dump(), source=body.source, status='queued', progress=0, pid=os.getpid(), instance=_instance, created_at=datetime.now(timezone.utc).isoformat())
        _write(path, job)
        _launch(root, path, job, scope.ctx)
    return {'ok': True, 'data': job}
