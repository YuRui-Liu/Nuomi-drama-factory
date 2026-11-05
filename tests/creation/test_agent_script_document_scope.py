from copy import deepcopy

import pytest

from novelvideo.creation import agent_context
from novelvideo.creation.agent_tools import AgentTools
from novelvideo.creation.operations import OperationError
from .test_agent_tools import ctx, ready, service  # noqa: F401 - expose fixture dependencies to pytest


async def test_fixed_script_projection_bridge_still_rejects_any_other_added_node(ctx, ready, monkeypatch):
    async def resolve(_role):
        return ctx

    tools = AgentTools(ctx, username='alice', canvas_id=ready[0], conversation_id=ready[1],
                       delegation_id='reviewer-delegation', resolve_context=resolve)
    scope = {'kind': 'canvas', 'canvas_id': ready[0]}
    script_ref = {'kind': 'script_document', 'canvas_id': ready[0],
                  'document_id': 'fixed-document', 'revision_id': 'fixed-revision'}
    parent_contract = {'inputs': []}
    contract = {
        'canvas_id': ready[0],
        'conversation_id': ready[1],
        'parent_id': 'writer-delegation',
        'allowed_operations': ['script.document.review'],
        'agent': {'effective': {'capabilities': ['proposal.create']}},
        'session_fence': {'generation': 1},
        'expanded_scope': {'scope': scope, 'node_ids': [], 'nodes': [], 'membership': []},
        'inputs': [{'ref': script_ref, 'snapshot': {'kind': 'episode_script'}}],
        'context': [],
    }
    row = {'contract': contract}
    current = {'scope': deepcopy(scope),
               'node_ids': ['formal-script-projection', 'unrelated-new-node'],
               'nodes': [], 'membership': []}

    monkeypatch.setattr(tools, '_owned', lambda _conn: row)
    monkeypatch.setattr(tools.delegations, '_active', lambda _conn, _row: True)
    monkeypatch.setattr(tools.delegations, '_get', lambda _conn, _identifier: {'contract': parent_contract})
    monkeypatch.setattr(tools.delegations, '_script_document_transition_evidence',
                        lambda *_args, **_kwargs: {'projection_node_id': 'formal-script-projection'})
    monkeypatch.setattr(tools, '_verified_asset_designs', lambda _conn, _contract: [])
    monkeypatch.setattr(agent_context, 'conversation', lambda *_args, **_kwargs: contract['session_fence'])
    monkeypatch.setattr(agent_context, 'graph_scope', lambda *_args, **_kwargs: deepcopy(current))

    with pytest.raises(OperationError) as failure:
        tools._current(None, 'script.document.review')

    assert failure.value.code == 'stale_input'
    assert tools._outputs() == []
    assert tools._receipt('must-not-be-written') is None
