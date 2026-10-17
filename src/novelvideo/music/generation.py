"""Durable one-operation generation advance; never re-submit an unknown request."""
import hashlib
import asyncio
import json
import os
from pathlib import Path
import tempfile

from novelvideo.media_capabilities.runtime.configuration import load_runninghub_runtime_configuration
from novelvideo.media_capabilities.music.runninghub_acestep import MusicRequest, compile_request, validate_contract
from .media import persist_media


def get_media_capability_store():
    from novelvideo import config
    from novelvideo.media_capabilities.store import MediaCapabilityStore
    return MediaCapabilityStore(Path(config.STATE_DIR) / 'local' / 'settings.db')


def get_media_credential_resolver():
    from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
    from novelvideo.media_capabilities.runtime.credential_store import create_credential_store
    credentials = create_credential_store()
    return CredentialResolver(keyring_reader=credentials.get, secret_reader=credentials.get)


async def generation_configuration():
    runtime = load_runninghub_runtime_configuration(get_media_capability_store(), get_media_credential_resolver())
    workflow = os.getenv('NUOMI_MUSIC_WORKFLOW_ID', runtime.workflows.music_acestep)
    if not workflow.isdecimal():
        raise ValueError('配乐工作流 ID 必须为数字字符串')
    async with runtime.create_client() as client:
        graph = await client.workflow_json(workflow)
    validate_contract(graph)
    return workflow, hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()


async def advance_generation(store, project, job_id, client, root, *, workflow_service=None, runtime=None):
    job = store.job(project, job_id)
    if job['status'] in {'completed','failed','submission_unknown','cancelled'}:
        return job
    if job['input'].get('workflowPackage'):
        return await _advance_registered(store, project, job_id, client, Path(root),
                                         workflow_service=workflow_service, runtime=runtime)
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
    if snapshot.status != 'succeeded':
        return store.update_job(project, job_id, status='running', error=None, usage=snapshot.usage)
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
    return store.update_job(project, job_id, status='completed', error=None, candidate=asset, usage=snapshot.usage)


def registered_music_package(identifier: str, version: int) -> dict:
    from novelvideo.media_capabilities.workflows.registry import WorkflowRegistry
    from novelvideo.media_capabilities.workflows.adapters import validate_integration
    registry = WorkflowRegistry(get_media_capability_store())
    manifest, graph = registry.get(identifier, version)
    integration = next((i for i in manifest.integrations if i.capability == 'music.generate' and i.scene == 'music_desk'), None)
    if manifest.status != 'active' or integration is None:
        raise ValueError('工作流未注册为配乐能力')
    validate_integration(integration, manifest)
    return {'manifest': manifest.snapshot(), 'graph': graph, 'account_id': 'runninghub-main'}


async def _advance_registered(store, project, job_id, client, root, *, workflow_service=None, runtime=None):
    from novelvideo.media_capabilities.models import MediaCapability
    from novelvideo.media_capabilities.workflows.models import WorkflowManifest
    job = store.job(project, job_id)
    package = job['input']['workflowPackage']
    if workflow_service is None:
        from types import SimpleNamespace
        from novelvideo.media_capabilities.workflows.service import WorkflowService
        from novelvideo.media_capabilities.task_store import TaskStore
        from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
        from novelvideo.media_capabilities.video.runtime import get_h3_concurrency_coordinator
        manifest = WorkflowManifest.model_validate(package['manifest'])
        account = package.get('account_id', 'runninghub-main')
        runtime = runtime or load_runninghub_runtime_configuration(get_media_capability_store(), get_media_credential_resolver(), provider_id=account)
        if runtime.account.id != account:
            raise ValueError('配乐任务账号与快照不一致')
        concurrency = get_h3_concurrency_coordinator(account)
        concurrency.configure(account, runtime.account.max_concurrency, runtime.account.capability_limits, runtime.account.queue_limit)
        frozen_registry = SimpleNamespace(get=lambda identifier, version: (manifest, package['graph']))
        workflow_service = WorkflowService(frozen_registry, TaskStore(root / 'workflow-tasks.db'), client,
            ArtifactStore(root / 'workflow-artifacts'), concurrency, account_id=account)
    if job.get('workflowRunId'):
        result = await workflow_service.resume(job['workflowRunId'])
    else:
        request = MusicRequest.model_validate(job['input']['request'])
        manifest_data = package['manifest']
        result = await workflow_service.run(manifest_data['id'], manifest_data['version'], {
            'tags': f'Instrumental, no vocals. {request.tags}', 'lyrics': '[Instrumental]',
            'bpm': request.bpm, 'duration': request.durationSeconds, 'seed': request.seed,
        }, capability=MediaCapability.MUSIC_GENERATE, idempotency_key=f'music:{project}:{job_id}')
    changes = {'workflowRunId': result.run_id, 'remoteTaskId': result.provider_task_id}
    if result.status == 'unknown':
        return store.update_job(project, job_id, **changes, status='submission_unknown', error='提交结果未知，请先核对远端任务')
    if result.status in {'failed', 'quality_failed', 'cancelled'}:
        return store.update_job(project, job_id, **changes, status='failed', error='注册工作流执行失败，请检查任务记录')
    if result.status != 'succeeded':
        return store.update_job(project, job_id, **changes, status='running', error=None)
    artifact = result.artifacts['audio'][0]
    source = root / 'workflow-artifacts' / artifact['local_path']
    info = persist_media(source, root, job['input']['owner'], 'audio')
    asset = store.add_asset(job['input']['owner'], path=info['path'], sha256=info['sha256'], duration_ms=info['durationMs'],
        name='配乐候选 ' + job_id[:6], library=False, origin='generated', description=job['input']['request']['tags'])
    store.reference(job['input']['owner'], project, asset['versionId'])
    return store.update_job(project, job_id, **changes, status='completed', error=None, candidate=asset)
