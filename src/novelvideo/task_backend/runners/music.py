"""Music desk tasks run on the existing durable project queue."""
import asyncio
from pathlib import Path

from novelvideo.music.service import get_store, media_root
from novelvideo.music.models import MusicPlan
from novelvideo.music.media import digest
from novelvideo.music.mix import render_mix, mux_video
from novelvideo.task_backend.registry import register_project_task_runner


def run_music_render(envelope, ctx):
    from novelvideo.task_backend.cancel import raise_if_envelope_cancel_requested, TaskCancelled
    check_cancel = lambda: raise_if_envelope_cancel_requested(envelope)
    store = get_store()
    job_id = envelope.get('payload', {}).get('job_id')
    project = ctx.project_id
    job = store.job(project, job_id)
    if job['status'] == 'completed':
        return {'output_path': job.get('path') or job['audioPath']}
    try:
        store.update_job(project, job_id, status='running')
        plan = MusicPlan.model_validate(job['input']['plan'])
        source = store.version(plan.source.assetVersionId, project=project)
        video = Path(source['path'])
        if digest(video) != plan.source.sha256:
            raise ValueError('源视频版本已变化')
        paths = {c.assetVersionId: Path(store.version(c.assetVersionId, project=project)['path']) for t in plan.tracks for c in t.clips}
        import hashlib
        folder = media_root() / 'renders' / hashlib.sha256(project.encode()).hexdigest() / job_id
        folder.mkdir(parents=True, exist_ok=True)
        audio = folder / 'mix.wav'
        frozen_mix = plan.model_dump(exclude={'revision'})
        cached = next((candidate.get('audioPath') for candidate in store.jobs(project)
            if candidate['status'] == 'completed' and candidate['kind'] == 'render'
            and {k:v for k,v in candidate['input']['plan'].items() if k != 'revision'} == frozen_mix
            and candidate.get('audioPath') and Path(candidate['audioPath']).is_file()), None)
        if cached:
            audio = Path(cached)
        else:
            render_mix(plan, video, paths, audio, check_cancel)
        changes = {'audioPath': str(audio)}
        if not job['input']['preview']:
            output = folder / 'scored.mp4'
            mux_video(video, audio, output, plan.source.durationMs, check_cancel)
            changes['path'] = str(output)
        store.update_job(project, job_id, status='completed', **changes)
        return {'output_path': changes.get('path') or str(audio)}
    except TaskCancelled:
        store.update_job(project, job_id, status='cancelled', error='已取消本地混音')
        raise
    except Exception as exc:
        from novelvideo.utils.error_redaction import safe_exception_message
        store.update_job(project, job_id, status='failed', error=safe_exception_message(exc))
        raise


async def _generate(envelope, ctx):
    from novelvideo.music.generation import advance_generation, generation_configuration
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_runninghub_runtime_configuration, RUNNINGHUB_DOWNLOAD_HOSTS
    from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient
    store, job_id = get_store(), envelope['payload']['job_id']
    job = store.job(ctx.project_id, job_id)
    workflow = job['input']['workflow']
    if not job.get('remoteTaskId'):
        configured_workflow, fingerprint = await generation_configuration()
        if configured_workflow != workflow or fingerprint != job['input']['contractHash']:
            store.update_job(ctx.project_id, job_id, status='failed', error='工作流配置已变化，请重新审阅请求')
            raise ValueError('配乐工作流配置已变化')
    runtime = load_runninghub_runtime_configuration(get_media_capability_store(), get_media_credential_resolver())
    async with RunningHubClient(runtime.api_key, account_id=runtime.account.id,
        base_url=runtime.account.base_url or RunningHubClient.DEFAULT_BASE_URL,
        workflow_media={workflow:'audio'}, download_allowed_hosts=RUNNINGHUB_DOWNLOAD_HOSTS) as client:
        for _ in range(240):
            result = await advance_generation(store, ctx.project_id, job_id, client, media_root())
            if result['status'] == 'completed':
                return {'music_version_id': result['candidate']['versionId'], 'provider_task_id':result.get('remoteTaskId')}
            if result['status'] in {'failed','submission_unknown','submitting','cancelled'}:
                raise ValueError(result.get('error') or '提交状态待核对，未重复提交')
            await asyncio.sleep(5)
    raise TimeoutError('远端配乐仍在运行，可从任务列表恢复查询')


def run_music_generate(envelope, ctx):
    from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
    try:
        return asyncio.run(await_envelope_with_cancel_watch(_generate(envelope, ctx), envelope))
    except BaseException:
        store, job_id = get_store(), envelope['payload']['job_id']
        job = store.job(ctx.project_id, job_id)
        if job['status'] not in {'completed', 'failed', 'submission_unknown'}:
            store.update_job(ctx.project_id, job_id, status='paused' if job.get('remoteTaskId') else 'failed', error='本地查询已停止；远端任务可能仍在运行，可恢复查询')
        raise


register_project_task_runner('music_render', run_music_render)
register_project_task_runner('music_generate', run_music_generate)
