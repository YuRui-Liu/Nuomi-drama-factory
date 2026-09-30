"""Candidate-only text jobs; deliberately never invoke production runners."""
import asyncio
from pathlib import Path

from novelvideo.agent_teams.trial_store import TrialStore
from novelvideo.agent_teams.trials import execute_side
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch, raise_if_envelope_cancel_requested, TaskTimedOut
from novelvideo.task_backend.registry import register_project_task_runner


def runner(role):
    def run(envelope,ctx):
        payload = envelope['payload']
        if payload['project_id'] != ctx.project_id:
            raise ValueError('trial project mismatch')
        store = TrialStore(Path(ctx.state_dir) / 'agent-team-trials.db')
        trial = store.get(ctx.project_id,payload['trial_id'])
        if trial is None or trial['role_id'] != role:
            raise ValueError('trial role mismatch')
        task_type = 'agent_team_trial_' + role
        async def cancel_check():
            # The shared synchronous checkpoint runs its own event loop.
            await asyncio.to_thread(raise_if_envelope_cancel_requested,envelope,task_type=task_type)
        try:
            return asyncio.run(await_envelope_with_cancel_watch(
                execute_side(store,ctx.project_id,payload['trial_id'],payload['side'],payload['attempt'],
                             cancel_check=cancel_check),
                envelope,task_type=task_type))
        except TaskTimedOut:
            store.transition(ctx.project_id,payload['trial_id'],payload['side'],payload['attempt'],
                             {'cancelled','running'},status='failed',candidate=None,error='TASK_TIMEOUT')
            raise
    return run


for role,task_role in (('writer','script_creation'),('script_parser','episode_normalization'),('director','director_plan')):
    register_project_task_runner('agent_team_trial_' + role,runner(role),text_task_role=task_role)
