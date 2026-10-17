"""Version-bound screenplay review and rewrite, without human decision actions."""
from __future__ import annotations

import asyncio
import json

from ..models import OrchestrationError

OPERATIONS = {('screenplayNode', 'review'), ('screenplayNode', 'rewrite'), ('screenplayNode', 'inspect')}


def _context(node, operation):
    data = node.get('data', {})
    episode = data.get('episodeNumber')
    document = data.get('documentId')
    revision = data.get('documentRevisionId')
    if type(episode) is not int or episode < 1:
        raise OrchestrationError('invalid_input', 'A positive bound screenplay episode is required')
    if any(not isinstance(value, str) or not value.strip() or len(value) > 128
           for value in (document, revision)):
        raise OrchestrationError('invalid_input', 'A bound screenplay document and revision are required')
    inputs = data.get('screenplayOperationInputs', {})
    if operation == 'inspect':
        return data, episode, document, revision, {}
    if not isinstance(inputs, dict) or inputs.get('operation') != operation:
        raise OrchestrationError('invalid_input', 'screenplayOperationInputs must match the operation')
    allowed = {'operation'} if operation == 'review' else {
        'operation', 'start', 'end', 'scope', 'mode', 'instruction', 'preserve'}
    if set(inputs) - allowed:
        raise OrchestrationError('invalid_input', 'Unsupported screenplay operation fields')
    return data, episode, document, revision, inputs


async def submit(run, attempt, node, operation):
    from novelvideo.api.routes import creative_workflow as creative
    from novelvideo.api.routes import script_creation as api
    data, episode, document, revision, inputs = _context(node, operation)
    project, actor = run['project_id'], run['actor']
    store, _ = await api._store(project, actor, 'viewer' if operation == 'inspect' else 'editor')
    current = await store.get(document)
    if current.kind != 'episode_script' or current.episode_number != episode:
        raise OrchestrationError('scope_violation', 'The bound document is not this episode screenplay')
    if operation == 'inspect':
        from dataclasses import asdict
        frozen = await store.get_revision(document, revision)
        return {'local': True, 'result': {'workflowResult': {
            'kind': 'screenplay_source', 'document_id': document, 'revision_id': revision,
            'episode': episode, 'markdown': frozen.revision.markdown,
            'blocks': [asdict(block) for block in frozen.revision.blocks],
            'current_revision_id': current.current_revision_id,
            'adopted_revision_id': current.adopted_revision_id,
        }}}
    if current.current_revision_id != revision:
        raise OrchestrationError('stale_input', 'The screenplay has changed; refresh the bound revision')
    if operation == 'review':
        body = creative.ReviewScreenplayBody(revision_id=revision,
            plan_run_id=data.get('planRunId', ''), client_mutation_id=attempt['id'])
        response = await creative.review_screenplay(project, document, body, actor)
        record = response['data']
        if not record.get('attempt_id'):
            raise OrchestrationError('submission_unknown', 'Review attempt receipt is missing')
        return {'production_workflow': True, 'workflow_kind': 'screenplay_review',
                'document_id': document, 'revision_id': revision,
                'review_attempt_id': record['attempt_id'], 'task_id': record.get('task_id')}
    if operation == 'rewrite':
        start, end = inputs.get('start'), inputs.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(current.revision.markdown):
            raise OrchestrationError('invalid_input', 'An explicit nonempty screenplay range is required')
        body = api.RewriteBody(document_id=document, base_revision_id=revision,
            start=start, end=end, scope=inputs.get('scope', 'selection'),
            mode=inputs.get('mode', 'custom'), instruction=inputs.get('instruction', ''),
            preserve=inputs.get('preserve', ''), client_mutation_id=attempt['id'])
        response = await api.create_rewrite(project, body, actor)
        record = response['data']
        return {'production_workflow': True, 'workflow_kind': 'screenplay_rewrite',
                'workflow_id': record['id'], 'document_id': document, 'revision_id': revision,
                'task_id': record.get('queued_task_id') or record.get('task_id')}
    raise OrchestrationError('unsupported_operation', 'Unsupported screenplay operation')


async def reconcile_submission(run, attempt, node, operation):
    from novelvideo.api.routes import script_creation as api
    from novelvideo.task_state import get_task_manager
    _, _, document, revision, _ = _context(node, operation)
    store, resolved = await api._store(run['project_id'], run['actor'], 'viewer')
    if operation == 'review':
        async with store._db() as db:
            request = await (await db.execute(
                'SELECT * FROM script_screenplay_review_requests WHERE mutation_id=?',
                (attempt['id'],))).fetchone()
        if request is None:
            return {'status': 'submission_unknown'}
        if request['document_id'] != document or request['revision_id'] != revision:
            raise OrchestrationError('scope_violation', 'Review receipt belongs to another input')
        review = await store.get_screenplay_review(document, revision)
        record = next((entry for entry in (review or {}).get('attempts', [])
                       if entry['attempt_id'] == request['attempt_id']), None)
        if record is None or (not record.get('task_id') and record['status'] not in {'completed', 'failed', 'needs_revision'}):
            return {'status': 'submission_unknown'}
        return {'status': 'submitted', 'submission': {
            'production_workflow': True, 'workflow_kind': 'screenplay_review',
            'document_id': document, 'revision_id': revision,
            'review_attempt_id': record['attempt_id'], 'task_id': record.get('task_id')}}
    async with store._db() as db:
        row = await (await db.execute('SELECT data FROM script_rewrite_jobs WHERE mutation_id=?',
                                     (attempt['id'],))).fetchone()
    if row is None:
        return {'status': 'submission_unknown'}
    record = json.loads(row['data'])
    if record['document_id'] != document or record['base_revision_id'] != revision:
        raise OrchestrationError('scope_violation', 'Rewrite receipt belongs to another input')
    task_id = record.get('task_id')
    if not task_id and resolved.ctx is not None:
        task = await asyncio.to_thread(get_task_manager().get_task_for_project,
            resolved.ctx, 'script_creation_rewrite', 0, scope=f"rewrite:{record['id']}")
        task_id = task.task_id if task is not None else None
    if not task_id and record['status'] not in {'completed', 'failed', 'needs_review'}:
        return {'status': 'submission_unknown'}
    return {'status': 'submitted', 'submission': {
        'production_workflow': True, 'workflow_kind': 'screenplay_rewrite',
        'workflow_id': record['id'], 'document_id': document, 'revision_id': revision, 'task_id': task_id}}


async def reconcile(run, submission):
    from novelvideo.api.routes import creative_workflow as creative
    from novelvideo.api.routes import script_creation as api
    project, actor = run['project_id'], run['actor']
    if submission['workflow_kind'] == 'screenplay_review':
        response = await creative.get_screenplay_review(project, submission['document_id'],
                                                        submission['revision_id'], actor)
        record = next((entry for entry in (response['data'] or {}).get('attempts', [])
                       if entry['attempt_id'] == submission['review_attempt_id']), None)
    else:
        response = await api.get_rewrite(project, submission['workflow_id'], actor)
        record = response['data']
    if record is None:
        return {'status': 'failed', 'error': 'Original screenplay operation receipt is missing'}
    if record.get('status') in {'failed', 'cancelled', 'needs_rebase', 'paused', 'submission_unknown', 'needs_review'}:
        return {'status': 'failed', 'error': record.get('error') or f"Screenplay operation requires attention: {record['status']}"}
    # A valid review requesting revision is a delivered report, not adopted prose.
    if record.get('status') not in {'completed', 'passed', 'needs_revision'}:
        return {'status': 'running'}
    if submission['workflow_kind'] == 'screenplay_rewrite' and not record.get('proposal_id'):
        return {'status': 'failed', 'error': 'Rewrite completed without a reviewable proposal'}
    return {'status': 'succeeded', 'result': {'workflowResult': record}}
