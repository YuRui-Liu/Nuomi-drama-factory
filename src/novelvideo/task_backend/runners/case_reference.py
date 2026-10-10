"""Text-only case proposals; no media provider is called."""
import asyncio

from novelvideo.case_reference.service import ProposalService
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


async def _run(envelope, ctx):
    payload = envelope['payload']
    service = ProposalService(ctx)
    try:
        result = await service.run(payload['proposal_id'], payload['owner'],
            current_text_task_runtime(), envelope.get('agent_route_snapshot'))
    except asyncio.CancelledError:
        service.store.transition(payload['proposal_id'], payload['owner'], ('queued', 'running'), 'cancelled')
        raise
    except Exception as exc:
        service.store.transition(payload['proposal_id'], payload['owner'], ('queued', 'running'),
            'failed', error=str(exc)[:2000])
        raise
    if result['status'] == 'failed':
        raise RuntimeError(result['error'])
    return {'proposal_id': result['id'], 'status': result['status']}


def run_case_reference(envelope, ctx):
    return asyncio.run(await_envelope_with_cancel_watch(_run(envelope, ctx), envelope, task_type='case_reference'))


register_project_task_runner('case_reference', run_case_reference, text_task_role='director_plan')
