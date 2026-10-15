"""Explicit model processing contracts on the existing persistent task queue."""
from fastapi import APIRouter, Depends, HTTPException

from novelvideo.api.deps import get_api_user
from novelvideo.api.schemas import FreezoneVideoProcessingRequest
from novelvideo.freezone.paths import resolve_static_url_to_path
from novelvideo.freezone.video_processing import capabilities, fingerprint, require_capability, validate_range
from novelvideo.freezone.canvas_store import CanvasIdempotencyConflict

router = APIRouter()


@router.get("/projects/{project}/freezone/video/processing-capabilities")
async def processing_capabilities(project: str, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.freezone import _resolve_freezone_project
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    return {"ok": True, "data": capabilities(root)}


@router.get("/projects/{project}/freezone/video/processing-source")
async def processing_source(project: str, source_url: str, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.freezone import _resolve_freezone_project
    from novelvideo.freezone.jobs import _probe_video_duration, _probe_video_size
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    try:
        source = resolve_static_url_to_path(source_url, root)
        if not source.is_file():
            raise HTTPException(404, "视频不存在")
        duration = await _probe_video_duration(str(source))
        width, height = await _probe_video_size(str(source))
        return {"ok": True, "data": {"source_fingerprint": fingerprint(source), "duration": duration,
                                     "width": width, "height": height}}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project}/freezone/video/process")
async def process_video(project: str, body: FreezoneVideoProcessingRequest, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.freezone import _resolve_freezone_project, _enqueue_or_start_freezone_media_job, _project_job_response, _canvas_state_project_dir
    from novelvideo.freezone.video_processing_projection import ProcessingRequests, prepare_processing_candidate
    from novelvideo.freezone.orchestration.models import fingerprint as request_fingerprint
    from novelvideo.freezone.jobs import _probe_video_duration, _probe_video_size
    ctx, username, project_name, root, _ = await _resolve_freezone_project(project, user)
    if ctx is None:
        raise HTTPException(409, "请先保存项目，再提交视频处理任务")
    requests = ProcessingRequests(root)
    signature = request_fingerprint(body.model_dump())
    async def replay(previous):
        if previous['response']:
            return previous['response']
        from novelvideo.task_state import get_task_manager
        import asyncio
        task = await asyncio.to_thread(get_task_manager().get_task_for_project, ctx, 'freezone_video_upscale', 0, scope=previous['job_id'])
        if task is None:
            raise HTTPException(409, '提交正在确认中，请使用同一请求重试；不会重复创建付费任务')
        response = _project_job_response(task_type='freezone_video_upscale', ctx=ctx, job_id=previous['job_id'],
            backend=(task.metadata or {}).get('backend', 'inline'), queue=(task.metadata or {}).get('queue_kind'), task_id=task.task_id)
        response['data'].update(canvas_id=body.canvas_id, node_id='processing-' + previous['job_id'])
        requests.complete(username, body.request_id, response)
        return response
    try:
        previous = requests.lookup(username, body.request_id, signature)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if previous:
        return await replay(previous)
    try:
        source = resolve_static_url_to_path(body.source_url, root)
        if not source.is_file():
            raise HTTPException(404, "视频不存在")
        if fingerprint(source) != body.source_fingerprint:
            raise HTTPException(409, "原视频已变化，请重新选择来源")
        duration = await _probe_video_duration(str(source))
        width, height = await _probe_video_size(str(source))
        payload = body.model_dump()
        state_root = _canvas_state_project_dir(ctx, root)
        payload.update(source_path=str(source), aspect_ratio=f"{width}:{height}", canvas_state_root=str(state_root), request_signature=signature)
        from novelvideo.freezone.video_processing_newapi import read_config
        payload['model_config_snapshot'] = read_config(root).model_dump()
        stages = [body.operation]
        if body.operation == "enhance":
            stages = (["upscale"] if body.resolution else []) + (["interpolate"] if body.fps else [])
            if not stages:
                raise ValueError("请选择超分或插帧")
        if body.operation in {"retake", "adopt"}:
            validate_range(body.start, body.end, duration)
        if body.operation == "adopt":
            if not body.candidate_url or not body.candidate_fingerprint:
                raise ValueError("采纳需要候选视频及其版本")
            candidate = resolve_static_url_to_path(body.candidate_url, root)
            if not candidate.is_file():
                raise HTTPException(404, "候选视频不存在")
            if fingerprint(candidate) != body.candidate_fingerprint:
                raise HTTPException(409, "候选视频已变化")
            if abs(await _probe_video_duration(str(candidate)) - (body.end - body.start)) > 0.08:
                raise ValueError("候选时长与选区不符")
            payload["candidate_path"] = str(candidate)
        else:
            for stage in stages:
                adapter = require_capability(stage, root)
                if adapter.max_input_seconds and duration > adapter.max_input_seconds:
                    raise ValueError("输入视频超过模型允许的时长")
                if stage == "upscale" and body.resolution not in adapter.resolutions:
                    raise ValueError("模型不支持该分辨率")
                if stage == "interpolate" and body.fps not in adapter.frame_rates:
                    raise ValueError("模型不支持该帧率")
        job_id = 'vp-' + request_fingerprint([username, body.request_id])[:28]
        payload['task_key'] = _project_job_response(task_type='freezone_video_upscale', ctx=ctx,
            job_id=job_id, backend='inline', queue=None, task_id=None)['data']['task_key']
        result_id = prepare_processing_candidate(state_root, job_id, payload, media_root=root)
        if requests.claim(username, body.request_id, signature) is None:
            return await replay(requests.lookup(username, body.request_id, signature))
        response = await _enqueue_or_start_freezone_media_job(
            ctx=ctx, username=username, project=project_name, project_dir=root,
            task_type="freezone_video_upscale", job_id=job_id,
            payload={"processing": payload}, queue_kind="ffmpeg" if body.operation == "adopt" else "default")
        response['data'].update(canvas_id=body.canvas_id, node_id=result_id)
        requests.complete(username, body.request_id, response)
        return response
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except CanvasIdempotencyConflict as exc:
        raise HTTPException(409, '同一请求不能用于不同处理参数') from exc
