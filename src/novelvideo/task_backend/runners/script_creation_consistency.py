"""Project-scoped cancellable cross-document consistency task."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.script_creation.consistency import ConsistencyService
from novelvideo.script_creation.store import DocumentStore
from novelvideo.task_backend.cancel import TaskCancelled, await_envelope_with_cancel_watch, is_cancel_requested, raise_if_local_task_stop_requested
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.text_task_runtime.runtime import current_text_task_runtime

TASK_TYPE = 'script_creation_consistency'


async def _run(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    payload = envelope.get('payload') or {}
    if str(payload.get('project_id')) != str(ctx.project_id):
        raise ValueError('PROJECT_SCOPE_MISMATCH')
    run_id = str(payload.get('run_id') or '')
    task_id = str(envelope.get('__run_task_id') or '')
    if not run_id or not task_id or envelope.get('scope') != f'consistency:{run_id}':
        raise ValueError('INVALID_CONSISTENCY_ENVELOPE')
    store = DocumentStore(Path(ctx.state_dir) / 'data.db')
    await store.initialize()
    manager = get_task_manager()

    async def check_cancel() -> None:
        if await is_cancel_requested(project_id=str(ctx.project_id), task_type=TASK_TYPE,
                                     episode=0, task_id=task_id, scope=f'consistency:{run_id}'):
            raise TaskCancelled()
        state = manager.get_task_for_project(ctx, TASK_TYPE, 0, scope=f'consistency:{run_id}')
        if state is None or state.task_id != task_id or state.status == 'cancelled':
            raise TaskCancelled()

    result = await ConsistencyService(store).execute(run_id, runtime=current_text_task_runtime(),
        task_id=task_id, cancel_check=check_cancel,
        commit_guard=lambda: raise_if_local_task_stop_requested(task_id))
    manager.update_progress_for_project(ctx, TASK_TYPE, 0, scope=f'consistency:{run_id}',
        progress=1.0, current_task='关联检查完成' if result['status'] == 'completed' else '参考版本已变化',
        expected_task_id=task_id)
    return {'run_id': run_id, 'status': result['status'], 'issue_count': len(result['issues'])}


def run_script_creation_consistency(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return asyncio.run(await_envelope_with_cancel_watch(_run(envelope, ctx), envelope, task_type=TASK_TYPE))


register_project_task_runner(TASK_TYPE, run_script_creation_consistency, text_task_role='script_creation')
