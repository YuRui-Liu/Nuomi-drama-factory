import pytest

from .test_delegation_api import api
from .test_agent_tools import ctx, ready, service, task
from .test_ideation_service import body_candidate, create_output


async def test_ideation_routes_query_preview_edit_and_receipt(api, ctx, ready, task):
    client, user = api
    ref, _ = await create_output(ctx, ready, task)
    prefix = f'/projects/p/creation/ideation/{ref["id"]}'
    query = {'canvas_id': ready[0], 'conversation_id': ready[1], 'node_id': 'a'}

    response = await client.get(prefix, params=query)
    assert response.status_code == 200, response.text
    assert response.json()['data']['origin'] == {'object_id': ref['id'], 'revision': ref['revision']}

    preview = await client.post(prefix + '/preview', json={**query, 'action': 'edit',
        'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    assert preview.status_code == 200, preview.text
    request = {'request_id': 'api-edit', **query, 'expected_revision': 1,
        'candidate_id': 'idea-1', 'proof': preview.json()['data']['proof'], 'candidate': body_candidate()}
    edited = await client.post(prefix + '/edit', json=request)
    assert edited.status_code == 200, edited.text
    assert edited.json()['data']['object_ref'] == {'object_id': ref['id'], 'revision': 2}
    assert (await client.get(prefix, params={**query, 'revision': 1})).json()['data']['revision'] == 1
    receipt = await client.get('/projects/p/creation/ideation-requests/api-edit', params=query)
    assert receipt.status_code == 200 and receipt.json()['data'] == edited.json()['data']

    user['credential_kind'] = 'agent_session'
    agent_preview = await client.post(prefix + '/preview', json={**query, 'action': 'edit',
        'expected_revision': 2, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    assert agent_preview.status_code == 200, agent_preview.text
    agent_edit = {'request_id': 'agent-edit', **query, 'expected_revision': 2,
        'candidate_id': 'idea-1', 'proof': agent_preview.json()['data']['proof'],
        'candidate': body_candidate()}
    assert (await client.post(prefix + '/edit', json=agent_edit)).status_code == 403
    assert (await client.post(prefix + '/adopt', json={'request_id': 'agent-adopt', **query,
        'expected_revision': 2, 'candidate_id': 'idea-1', 'proof': 'unused'})).status_code == 403


async def test_ideation_routes_reject_other_conversation_and_unknown_receipt(api, ready, ctx, task):
    client, _ = api
    ref, _ = await create_output(ctx, ready, task)
    prefix = '/projects/p/creation'
    wrong = {'canvas_id': ready[0], 'conversation_id': 'other', 'node_id': 'a'}
    response = await client.get(f'{prefix}/ideation/{ref["id"]}', params=wrong)
    assert response.status_code in (403, 422)
    missing = await client.get(f'{prefix}/ideation-requests/missing', params=wrong)
    assert missing.status_code in (403, 404, 422)
