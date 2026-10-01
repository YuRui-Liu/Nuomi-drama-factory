"""Durable one-operation generation advance; never re-submit an unknown request."""
import hashlib
import asyncio
import json
import os
from pathlib import Path
import tempfile

from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
from novelvideo.media_capabilities.runtime.configuration import load_runninghub_runtime_configuration
from novelvideo.media_capabilities.music.runninghub_acestep import DEFAULT_WORKFLOW_ID, MusicRequest, compile_request, validate_contract
from .media import persist_media


async def generation_configuration():
    workflow = os.getenv('NUOMI_MUSIC_WORKFLOW_ID', DEFAULT_WORKFLOW_ID)
    if not workflow.isdecimal():
        raise ValueError('配乐工作流 ID 必须为数字字符串')
    runtime = load_runninghub_runtime_configuration(get_media_capability_store(), get_media_credential_resolver())
    async with runtime.create_client() as client:
        graph = await client.workflow_json(workflow)
    validate_contract(graph)
    return workflow, hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()


async def advance_generation(store, project, job_id, client, root):
    job = store.job(project, job_id)
    if job['status'] in {'completed','failed','submission_unknown','cancelled'}:
        return job
    remote = job.get('remoteTaskId')
    if not remote:
        if not store.claim_job(project, job_id):
            # A prior process may have submitted before it lost its response.
            return store.job(project, job_id)
        try:
            remote = await client.submit(job['input']['workflow'], compile_request(MusicRequest.model_validate(job['input']['request'])))
        except BaseException as exc:
            store.update_job(project, job_id, status='submission_unknown', error='提交结果未知，请核对 RunningHub 任务，未自动重试')
            if isinstance(exc, asyncio.CancelledError):
                raise
            return store.job(project, job_id)
        store.update_job(project, job_id, status='running', remoteTaskId=remote)
    snapshot = await client.query(remote)
    if snapshot.status == 'failed':
        return store.update_job(project, job_id, status='failed', error='RunningHub 配乐生成失败，请查看远端任务', usage=snapshot.usage)
    if snapshot.status != 'completed':
        return store.update_job(project, job_id, status='running', usage=snapshot.usage)
    outputs = [item for item in snapshot.results if item.node_id in {'107', None} and (item.output_type or '').lower() not in {'image','video'}]
    if not outputs:
        return store.update_job(project, job_id, status='failed', error='工作流未返回预期的音频输出')
    data = await client.download(outputs[0].url)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        path = Path(folder)/'candidate.mp3'
        path.write_bytes(data)
        info = persist_media(path, root, job['input']['owner'], 'audio')
        asset = store.add_asset(job['input']['owner'], path=info['path'], sha256=info['sha256'], duration_ms=info['durationMs'], name='配乐候选 '+job_id[:6], library=False, origin='generated', description=job['input']['request']['tags'])
    store.reference(job['input']['owner'], project, asset['versionId'])
    return store.update_job(project, job_id, status='completed', candidate=asset, usage=snapshot.usage)
