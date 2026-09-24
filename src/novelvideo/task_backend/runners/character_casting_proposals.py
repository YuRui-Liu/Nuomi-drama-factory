"""One character, one bounded source extraction, one design call; never adopts."""
import asyncio

from novelvideo.character_visual.casting_compiler import snapshot_digest
from novelvideo.character_visual.casting_service import resolved_style, design_and_publish
from novelvideo.character_visual.casting_source import load_sources, ground_profile, reuse_artifact_facts, assert_live_sources
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from novelvideo.character_visual.casting_submission import SubmissionJournal
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch, raise_if_envelope_cancel_requested, raise_if_local_task_stop_requested
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_current_project_task_id
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


def run_character_casting_proposals(envelope, ctx):
    runtime = current_text_task_runtime()
    if runtime is None or runtime.snapshot.task_role != 'knowledge_extraction':
        raise ValueError('knowledge_extraction runtime required')
    if envelope.get('project_id') != ctx.project_id:
        raise ValueError('casting project mismatch')
    task_id = get_current_project_task_id()
    if not task_id:
        raise ValueError('casting requires authoritative task context')
    payload = envelope['payload']
    journal = SubmissionJournal(ctx)
    row = journal.claim_execution(request_id=payload['submission_request_id'], submission_token=payload['submission_token'],
        task_id=task_id, task_type='character_casting_proposals', scope=envelope.get('scope'), payload=payload)
    if row.get('execution_status') == 'completed':
        return row['result']
    def cancel():
        raise_if_envelope_cancel_requested(envelope, task_type='character_casting_proposals')
    cancel()
    async def run():
        from novelvideo.api.deps import make_sqlite_store_for_context
        sql = await make_sqlite_store_for_context(ctx)
        try:
            name, identity_id = payload['character_id'], payload.get('identity_id')
            character = sql.get_character(name)
            if character is None or (identity_id and not any(i.identity_id == identity_id for i in character.identities)):
                raise ValueError('character or identity no longer exists')
            store = CharacterVisualWorkspaceStore(ctx.output_dir, state_dir=ctx.state_dir)
            workspace = store.get(name)
            if workspace is None or snapshot_digest(workspace.model_dump(mode='json')) != payload['workspace_hash']:
                raise ValueError('casting inputs changed before recast')
            documents, revision = await load_sources(ctx.output_dir, sql)
            if revision != payload['source_revision'] or resolved_style(ctx) != payload['style']:
                raise ValueError('source/style changed before recast')
            artifact_facts = await reuse_artifact_facts(workspace.profile, documents, revision, sql)
            profile = await ground_profile(workspace.profile, documents, revision, runtime=runtime,
                identity_id=identity_id, artifact_facts=artifact_facts)
            async def assert_live():
                await asyncio.to_thread(cancel)
                _, current = await load_sources(ctx.output_dir, sql)
                if current != revision or resolved_style(ctx) != payload['style']:
                    raise ValueError('source/style changed during recast')
            def before_commit():
                raise_if_local_task_stop_requested(task_id)
                assert_live_sources(ctx, sql, documents, name, identity_id)
                if resolved_style(ctx) != payload['style']:
                    raise ValueError('style changed during recast')
            result = await design_and_publish(store=store, character_id=name, identity_id=identity_id,
                expected_revision=payload.get('expected_revision'), grounded_profile=profile,
                source_revision=revision, style=payload['style'], runtime=runtime, assert_live=assert_live,
                before_commit=before_commit, original_hash=payload['workspace_hash'])
            selected = result.casting_revision if identity_id is None else result.identity_casting_revisions[identity_id]
            return {'character_id': name, 'identity_id': identity_id, 'revision_id': selected.revision_id}
        finally:
            await sql.close()
    async def watched():
        return await await_envelope_with_cancel_watch(run(), envelope, task_type='character_casting_proposals')
    try:
        result = asyncio.run(watched())
    except BaseException:
        journal.finish_execution(payload['submission_request_id'], task_id=task_id,
            error={'code': 'CASTING_RECAST_FAILED', 'message': '重新选角未能完成：原文、角色或草案可能已变化，或提案未通过验证。当前形象未改变；请检查输入后主动重新选角。'})
        raise
    journal.finish_execution(payload['submission_request_id'], task_id=task_id, result=result)
    return result


register_project_task_runner('character_casting_proposals', run_character_casting_proposals, text_task_role='knowledge_extraction')
