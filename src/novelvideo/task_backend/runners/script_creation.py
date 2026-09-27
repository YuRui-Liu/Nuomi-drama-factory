"""Queued, cancellable creative writing against a frozen text-task route."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.script_creation.generation import GenerationService
from novelvideo.script_creation.store import DocumentStore
from novelvideo.task_backend.cancel import TaskCancelled, await_envelope_with_cancel_watch, is_cancel_requested, raise_if_local_task_stop_requested
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.text_task_runtime.runtime import current_text_task_runtime

TASK_TYPE = 'script_creation_generation'


async def _run_script_creation(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    payload = envelope.get('payload') or {}
    if str(payload.get('project_id')) != str(ctx.project_id):
        raise ValueError('PROJECT_SCOPE_MISMATCH')
    run_id = str(payload.get('run_id') or '')
    task_id = str(envelope.get('__run_task_id') or '')
    if not run_id or not task_id or envelope.get('scope') != f'run:{run_id}':
        raise ValueError('INVALID_GENERATION_ENVELOPE')
    store = DocumentStore(Path(ctx.state_dir) / 'data.db')
    await store.initialize()
    runtime = current_text_task_runtime()
    if runtime is None:
        message = '剧本创作模型路由不可用，请在模型设置中配置剧本创作任务'
        run = await store.generation_get(run_id)
        if run['status'] == 'pending':
            run.update(status='failed', task_id=task_id, error=message)
            if run['steps']:
                run['steps'][0].update(status='failed', task_id=task_id, error=message)
            await store.generation_update(run_id, run)
        raise RuntimeError(message)
    manager = get_task_manager()

    async def check_cancel() -> None:
        if await is_cancel_requested(project_id=str(ctx.project_id), task_type=TASK_TYPE,
                                     episode=0, task_id=task_id, scope=f'run:{run_id}'):
            raise TaskCancelled()
        state = manager.get_task_for_project(ctx, TASK_TYPE, 0, scope=f'run:{run_id}')
        if state is None or state.task_id != task_id or state.status == 'cancelled':
            raise TaskCancelled()

    def update(index: int, total: int, label: str) -> None:
        manager.update_progress_for_project(
            ctx, TASK_TYPE, 0, scope=f'run:{run_id}', progress=(index + 0.1) / total,
            current_task=f'正在创作：{label}', expected_task_id=task_id)

    result = await GenerationService(store).execute(run_id, runtime=runtime, task_id=task_id,
                                                     cancel_check=check_cancel,
                                                     commit_guard=lambda: raise_if_local_task_stop_requested(task_id),
                                                     progress=update)
    manager.update_progress_for_project(
        ctx, TASK_TYPE, 0, scope=f'run:{run_id}', progress=1.0,
        current_task='创作完成' if result['status'] == 'completed' else '参考版本已变化',
        expected_task_id=task_id)
    return {'run_id': run_id, 'status': result['status'], 'steps': result['steps']}


def run_script_creation(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return asyncio.run(await_envelope_with_cancel_watch(
        _run_script_creation(envelope, ctx), envelope, task_type=TASK_TYPE))


register_project_task_runner(TASK_TYPE, run_script_creation, text_task_role='script_creation')
