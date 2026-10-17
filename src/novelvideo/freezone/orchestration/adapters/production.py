"""Scoped Agent operations reuse the explicit production workflow endpoints.

Candidate generation is deliberately separate from human adoption/confirmation.
No arbitrary URL, task type, or provider request is accepted from node data.
"""
import asyncio
import json

from ..models import OrchestrationError, fingerprint
from . import screenplay

OPERATIONS = {('ideationNode', 'generate'), ('screenplayNode', 'plan'),
              ('directorWorkspaceNode', 'prepare'),
              ('directorWorkspaceNode', 'produce'),
              ('directorWorkspaceNode', 'retry_production'),
              ('directorWorkspaceNode', 'render_groups'),
              ('directorWorkspaceNode', 'retry_render_groups'),
              ('directorWorkspaceNode', 'preview_handoff'),
              ('directorWorkspaceNode', 'inspect')} | screenplay.OPERATIONS
LOCAL_OPERATIONS = {('screenplayNode', 'inspect'), ('directorWorkspaceNode', 'inspect'),
                    ('directorWorkspaceNode', 'preview_handoff')}


def _production_request(node, attempt, operation):
    """Use an explicit frozen selection, never infer all groups from a plan."""
    from novelvideo.api.routes import director_workspace as api
    data = node.get('data', {})
    episode = data.get('episodeNumber')
    if type(episode) is not int or episode < 1:
        raise OrchestrationError('invalid_input', 'A positive bound episodeNumber is required')
    try:
        body = api.DirectorWorkspaceProductionRequest(
            revision_id=data.get('directorRevisionId', ''),
            group_ids=data.get('productionGroupIds', []),
            client_mutation_id=attempt['id'], retry_failed=operation in {'retry_production', 'retry_render_groups'},
            through='render' if operation in {'render_groups', 'retry_render_groups'} else 'video')
        groups = [api._safe_production_group_id(value) for value in body.group_ids]
        if len(groups) != len(set(groups)):
            raise ValueError('Group selection contains duplicates')
    except (ValueError, TypeError) as exc:
        raise OrchestrationError('invalid_input', str(exc)) from exc
    scope_payload = {
        'episode': episode, 'revision_id': body.revision_id, 'group_ids': sorted(groups),
        'client_mutation_id': body.client_mutation_id, 'retry_failed': body.retry_failed,
    }
    if body.through != 'video':
        scope_payload['through'] = body.through
    scope = 'director-workspace-production:' + fingerprint(scope_payload)
    return episode, body, scope


def _production_receipt(task, episode, body, scope):
    return {'production_workflow': True, 'workflow_kind': 'group_production',
            'task_id': str(task.task_id), 'episode': episode, 'scope': scope,
            'revision_id': body.revision_id, 'group_ids': sorted(body.group_ids), 'through': body.through}


def _director_receipt(item, node):
    data = node.get('data', {})
    if (item.get('episode_number') != data.get('episodeNumber')
            or item.get('document_id') != data.get('documentId')
            or item.get('revision_id') != data.get('adoptedRevisionId')):
        raise OrchestrationError('scope_violation', 'Director handoff receipt does not match the frozen screenplay')
    return {'production_workflow': True, 'workflow_kind': 'director',
            'episode': item['episode_number'], 'handoff_id': item['id'],
            'document_id': item['document_id'], 'revision_id': item['revision_id'],
            'source_revision': item.get('source_revision'), 'source_hash': item.get('source_hash')}


async def reconcile_submission(run, attempt):
    """Recover an existing operation receipt without submitting another task."""
    from novelvideo.api.routes import creative_workflow as api
    node = next(node for node in run['snapshot']['nodes'] if node['id'] == attempt['node_id'])
    operation = node.get('data', {}).get('operation')
    if (node['type'], operation) in screenplay.OPERATIONS:
        return await screenplay.reconcile_submission(run, attempt, node, operation)
    if (node['type'], operation) == ('directorWorkspaceNode', 'prepare'):
        from novelvideo.api.routes import director_workspace as director
        ctx = await director._resolve(run['project_id'], run['actor'], role='viewer')
        sqlite, documents, handoff = await director._load_handoff_services(ctx)
        try:
            async with documents._db() as db:
                row = await (await db.execute(
                    'SELECT data FROM script_handoffs WHERE project_id=? AND prepare_mutation_id=?',
                    (handoff.project_id, attempt['id']))).fetchone()
            item = json.loads(row['data']) if row is not None else None
            if item is None or item.get('status') in {'prepared', 'source_written', 'dispatching'}:
                return {'status': 'submission_unknown'}
            return {'status': 'submitted', 'submission': _director_receipt(item, node)}
        finally:
            await sqlite.close()
    if node['type'] == 'directorWorkspaceNode' and operation in {'produce', 'retry_production', 'render_groups', 'retry_render_groups'}:
        from novelvideo.api.routes import director_workspace as director
        from novelvideo.task_state import get_task_manager
        episode, body, scope = _production_request(node, attempt, operation)
        ctx = await director._resolve(run['project_id'], run['actor'], role='editor')
        task = await asyncio.to_thread(get_task_manager().get_task_for_project,
            ctx, 'director_workspace_production', episode, scope=scope)
        if task is None:
            return {'status': 'submission_unknown'}
        return {'status': 'submitted', 'submission': _production_receipt(task, episode, body, scope)}
    if node['type'] not in {'ideationNode', 'screenplayNode'}:
        return {'status': 'submission_unknown'}
    store, _, _ = await api._store(run['project_id'], run['actor'])
    kind = 'ideation' if node['type'] == 'ideationNode' else 'planning'
    if kind == 'ideation':
        query, mutation = 'SELECT data FROM script_ideation_runs WHERE mutation_id=?', attempt['id']
    else:
        query, mutation = 'SELECT data FROM script_generation_runs WHERE mutation_id=?', f"creative-plan:{attempt['id']}"
    async with store._db() as db:
        row = await (await db.execute(query, (mutation,))).fetchone()
    if row is None:
        return {'status': 'submission_unknown'}
    receipt = json.loads(row['data'])
    # A created run without enqueue evidence is not proof that the remote task
    # was accepted. Keep that ambiguous window visible instead of resubmitting.
    if not receipt.get('task_id') and receipt.get('status') not in {'running', 'completed', 'failed', 'needs_review', 'paused'}:
        return {'status': 'submission_unknown'}
    return {'status': 'submitted', 'submission': {'production_workflow': True,
            'workflow_kind': kind, 'workflow_id': receipt['id'], 'task_id': receipt.get('task_id')}}


async def submit(run, attempt, node, operation):
    from novelvideo.api.routes import creative_workflow as api
    data = node.get('data', {})
    project, actor, mutation = run['project_id'], run['actor'], attempt['id']
    if (node['type'], operation) in screenplay.OPERATIONS:
        return await screenplay.submit(run, attempt, node, operation)
    if (node['type'], operation) == ('directorWorkspaceNode', 'preview_handoff'):
        from novelvideo.api.routes import director_workspace as director
        episode = data.get('episodeNumber')
        if type(episode) is not int or episode < 1:
            raise OrchestrationError('invalid_input', 'A positive episode number is required')
        response = await director.preview_director_workspace_handoff(project, episode,
            director.DirectorWorkspaceHandoffPreviewRequest(document_id=data.get('documentId', ''),
                revision_id=data.get('adoptedRevisionId', '')), actor)
        return {'local': True, 'result': {'workflowResult': response['data']}}
    if (node['type'], operation) == ('directorWorkspaceNode', 'inspect'):
        from novelvideo.api.routes import director_workspace as director
        from novelvideo.director_plan.store import DirectorPlanStore
        episode = data.get('episodeNumber')
        revision_id = data.get('directorRevisionId')
        if type(episode) is not int or episode < 1 or not isinstance(revision_id, str) or not revision_id:
            raise OrchestrationError('invalid_input', 'A fixed episode and director revision are required')
        ctx = await director._resolve(project, actor, role='viewer')
        revision = DirectorPlanStore(ctx.output_dir).load(episode, revision_id)
        if revision.episode != episode:
            raise OrchestrationError('scope_violation', 'Director revision episode does not match')
        return {'local': True, 'result': {'workflowResult': revision.model_dump(mode='json')}}
    if node['type'] == 'directorWorkspaceNode' and operation in {'produce', 'retry_production', 'render_groups', 'retry_render_groups'}:
        from novelvideo.api.routes import director_workspace as director
        episode, body, scope = _production_request(node, attempt, operation)
        response = await director.start_director_workspace_production(project, episode, body, actor)
        receipt = response['data']
        if not receipt.get('task_id') or receipt.get('scope') != scope:
            raise OrchestrationError('submission_unknown', 'Production task receipt does not match frozen scope')
        return {'production_workflow': True, 'workflow_kind': 'group_production',
                'task_id': receipt['task_id'], 'episode': episode, 'scope': scope,
                'revision_id': body.revision_id, 'group_ids': sorted(body.group_ids), 'through': body.through}
    if (node['type'], operation) == ('ideationNode', 'generate'):
        body = api.CreateIdeationBody(brief_document_id=data.get('briefId', ''),
            brief_revision_id=data.get('briefRevisionId', ''), generate=True,
            client_mutation_id=mutation)
        response = await api.create_ideation(project, body, actor)
        queued = response['data']
        return {'production_workflow': True, 'workflow_kind': 'ideation',
                'workflow_id': queued['run']['id'], 'task_id': queued.get('task_id')}
    if (node['type'], operation) == ('screenplayNode', 'plan'):
        body = api.StartPlanBody(ideation_id=data.get('ideationId', ''),
            candidate_id=data.get('ideaCandidateId', ''), idea_revision_id=data.get('ideaRevisionId', ''),
            brief_document_id=data.get('briefId', ''), brief_revision_id=data.get('briefRevisionId', ''),
            script_mode=data.get('scriptMode', 'single'), episode_count=data.get('episodeCount', 1),
            instruction=data.get('instruction', ''), client_mutation_id=mutation)
        response = await api.create_plan(project, body, actor)
        queued = response['data']
        return {'production_workflow': True, 'workflow_kind': 'planning',
                'workflow_id': queued['run']['id'], 'task_id': queued.get('task_id')}
    if (node['type'], operation) == ('directorWorkspaceNode', 'prepare'):
        from novelvideo.api.routes import director_workspace as director
        episode = data.get('episodeNumber')
        if type(episode) is not int or episode < 1:
            raise OrchestrationError('invalid_input', 'A positive episode number is required')
        selection = data.get('handoffSelection')
        if not isinstance(selection, dict) or set(selection) != {'expected_source_project_revision', 'update_scope'}:
            raise OrchestrationError('input_required', 'Preview the handoff and explicitly select its source-update scope')
        baseline = selection['expected_source_project_revision']
        if type(baseline) is not int or baseline < 0:
            raise OrchestrationError('invalid_input', 'A fixed handoff source baseline is required')
        body = director.DirectorWorkspacePrepareRequest(document_id=data.get('documentId', ''),
            revision_id=data.get('adoptedRevisionId', ''), client_mutation_id=mutation,
            confirm_mutation_id=f'{mutation}:confirm', expected_source_project_revision=baseline,
            update_scope=selection['update_scope'])
        response = await director.prepare_director_workspace(project, episode, body, actor)
        return _director_receipt(response['handoff'], node)
    raise OrchestrationError('unsupported_operation', 'Unsupported production node operation')


async def reconcile(run, submission):
    from novelvideo.api.routes import creative_workflow as api
    project, actor = run['project_id'], run['actor']
    kind = submission['workflow_kind']
    if kind in {'screenplay_review', 'screenplay_rewrite'}:
        return await screenplay.reconcile(run, submission)
    if kind == 'group_production':
        from novelvideo.api.routes import director_workspace as director
        from novelvideo.task_state import get_task_manager
        ctx = await director._resolve(project, actor, role='viewer')
        task = await asyncio.to_thread(get_task_manager().get_task_for_project,
            ctx, 'director_workspace_production', submission['episode'], scope=submission['scope'])
        if task is None or str(task.task_id) != submission['task_id']:
            return {'status': 'failed', 'error': 'Production task receipt is missing or changed; inspect original task'}
        if task.status in {'failed', 'cancelled', 'canceled', 'partial_failure'}:
            return {'status': 'failed', 'error': task.error or f'Production ended: {task.status}'}
        if task.status != 'completed':
            return {'status': 'running'}
        result = task.result or {}
        if (result.get('status') != 'completed' or result.get('through') != submission.get('through', 'video')
                or result.get('revision_id') != submission['revision_id']
                or (result.get('summary') or {}).get('ok') is not True):
            return {'status': 'failed', 'error': 'Production result is partial or does not match frozen revision'}
        return {'status': 'succeeded', 'result': {'workflowResult': {
            'task_id': submission['task_id'], 'episode': submission['episode'],
            'group_ids': submission['group_ids'], **result}}}
    if kind == 'ideation':
        response = await api.get_ideation(project, submission['workflow_id'], actor)
    elif kind == 'planning':
        response = await api.get_run(project, submission['workflow_id'], actor)
    elif kind == 'director':
        from novelvideo.api.routes import director_workspace as director
        if not submission.get('handoff_id'):
            return {'status': 'failed', 'error': 'Legacy director receipt has no fixed handoff; inspect the original operation'}
        ctx = await director._resolve(project, actor, role='viewer')
        sqlite, _, handoff = await director._load_handoff_services(ctx)
        try:
            original = await handoff.get(submission['handoff_id'])
        finally:
            await sqlite.close()
        if (original.get('document_id') != submission['document_id']
                or original.get('revision_id') != submission['revision_id']
                or original.get('episode_number') != submission['episode']):
            return {'status': 'failed', 'error': 'Director handoff identity changed'}
        if original.get('status') == 'failed':
            return {'status': 'failed', 'error': original.get('error') or 'Director handoff failed'}
        if original.get('status') == 'completed' and (original.get('task_result') or {}).get('skipped'):
            return {'status': 'succeeded', 'result': {'workflowResult': {
                'handoff': original, 'source_update_skipped': True}}}
        source = await director._current_episode_source(ctx, submission['episode'])
        if original.get('source_revision') is not None and (
                source is None or source.source_revision != original['source_revision']
                or source.content_hash != original.get('source_hash')):
            return {'status': 'failed', 'error': 'Director handoff source was superseded; inspect the original operation'}
        response = await director.get_director_workspace(project, submission['episode'], actor)
    else:
        raise OrchestrationError('invalid_submission', 'Unknown production workflow receipt')
    value = response['data']
    if kind == 'director':
        stages = value.get('stages', {})
        required = ['director_version', 'asset_breakdown', 'group_plan']
        errors = [stage.get('error') or f"{key}: {stage.get('status')}" for key, stage in stages.items()
                  if stage.get('status') in {'failed', 'needs_review', 'partial'}]
        if errors:
            return {'status': 'failed', 'error': '; '.join(str(error) for error in errors)}
        complete = all(stages.get(key, {}).get('status') == 'completed' for key in required)
        status = 'completed' if complete else 'running'
    else:
        status = value.get('status')
    if status in {'failed', 'cancelled', 'needs_rebase', 'needs_review', 'paused'}:
        return {'status': 'failed', 'error': value.get('error') or f'Workflow requires attention: {status}'}
    if status != 'completed':
        return {'status': 'running'}
    result = {'workflowResult': value}
    if kind != 'director':
        result['workflowId'] = submission['workflow_id']
    return {'status': 'succeeded', 'result': result}
