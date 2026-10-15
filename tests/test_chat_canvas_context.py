from copy import deepcopy

import pytest

from novelvideo.chat.canvas_context import reference_snapshot, apply_context_edit
from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
from novelvideo.freezone.canvas_store import read_canvas


def canvas(tmp_path):
    create_canvas(tmp_path, 'board', project_id='p', name='Board', actor_id='u')
    return patch_graph(tmp_path, 'board', base_revision=1, request_id='seed', operations=[{
        'op': 'add_node', 'node': {'id': 'source', 'type': 'imageGenNode',
        'position': {'x': 10, 'y': 20}, 'data': {'prompt': 'old', 'imageUrl': '/api/source.png'}}}])


def test_reference_missing_node_is_rejected(tmp_path):
    canvas(tmp_path)
    with pytest.raises(ValueError):
        reference_snapshot(tmp_path, 'board', ['missing'])


def test_candidate_preserves_source_and_retry_is_idempotent(tmp_path):
    original = canvas(tmp_path)
    snapshot = reference_snapshot(tmp_path, 'board', ['source'])
    args = dict(node_id='source', expected_fingerprint=snapshot['nodes'][0]['fingerprint'],
                request_id='turn-1', action='generate', changes={'prompt': 'new'}, operation='edit')
    first = apply_context_edit(tmp_path, 'board', **args)
    second = apply_context_edit(tmp_path, 'board', **args)
    assert first['node_id'] == second['node_id']
    stored = read_canvas(tmp_path, 'board')
    assert stored['nodes'][0] == original['nodes'][0]
    assert len(stored['nodes']) == 2
    assert stored['nodes'][1]['data']['request']['base_url'] == '/api/source.png'


def test_stale_reference_cannot_overwrite_new_input(tmp_path):
    original = canvas(tmp_path)
    snapshot = reference_snapshot(tmp_path, 'board', ['source'])
    patch_graph(tmp_path, 'board', base_revision=original['revision'], request_id='other',
                operations=[{'op': 'update_node', 'node_id': 'source', 'data': {'prompt': 'other'}}])
    with pytest.raises(ValueError, match='变化'):
        apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=snapshot['nodes'][0]['fingerprint'],
                           request_id='turn', action='update', changes={'prompt': 'overwrite'})


def test_reference_revision_includes_media_outputs(tmp_path):
    original = canvas(tmp_path)
    before = reference_snapshot(tmp_path, 'board', ['source'])
    patch_graph(tmp_path, 'board', base_revision=original['revision'], request_id='image',
                operations=[{'op': 'update_node', 'node_id': 'source', 'data': {'imageUrl': '/api/new.png'}}])
    after = reference_snapshot(tmp_path, 'board', ['source'])
    assert before['nodes'][0]['fingerprint'] != after['nodes'][0]['fingerprint']


def test_identity_and_result_fields_cannot_be_mutated(tmp_path):
    canvas(tmp_path)
    snap = reference_snapshot(tmp_path, 'board', ['source'])
    with pytest.raises(ValueError):
        apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=snap['nodes'][0]['fingerprint'],
                           request_id='turn', action='update', changes={'imageUrl': 'file:///secret'})


def test_additional_media_is_passed_to_generation_request(tmp_path):
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    canvas(tmp_path)
    source = reference_snapshot(tmp_path, 'board', ['source'])['nodes'][0]
    result = apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=source['fingerprint'],
        request_id='references', action='generate', changes={'prompt': '使用角色参考修改庭院'}, operation='edit',
        reference_media=[{'type': 'image', 'url': '/api/character.png'}])
    node = next(node for node in read_canvas(tmp_path, 'board')['nodes'] if node['id'] == result['node_id'])
    _, request = build_request(node, 'edit', [], canvas_id='board', attempt_id='attempt')
    assert request['base_url'] == '/api/source.png'
    assert request['extra_reference_urls'] == ['/api/character.png']


def test_candidate_keeps_explicit_provider_without_overriding_new_prompt(tmp_path):
    original = canvas(tmp_path)
    patch_graph(tmp_path, 'board', base_revision=original['revision'], request_id='provider', operations=[{
        'op':'update_node', 'node_id':'source', 'data':{'request':{'provider':'grsai', 'prompt':'old override'}}}])
    source = reference_snapshot(tmp_path, 'board', ['source'])['nodes'][0]
    result = apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=source['fingerprint'],
        request_id='provider-copy', action='generate', changes={'prompt':'new'}, operation='edit')
    node = next(node for node in read_canvas(tmp_path, 'board')['nodes'] if node['id'] == result['node_id'])
    assert node['data']['request']['provider'] == 'grsai'
    assert 'prompt' not in node['data']['request']


def test_candidate_run_does_not_regenerate_original_and_parallel_runs_are_independent(tmp_path):
    from novelvideo.freezone.orchestration.graph import execution_order
    from novelvideo.freezone.orchestration.store import RunStore
    canvas(tmp_path)
    snap = reference_snapshot(tmp_path, 'board', ['source'])
    ids = []
    for request_id in ('one', 'two'):
        item = apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=snap['nodes'][0]['fingerprint'],
            request_id=request_id, action='generate', changes={'prompt': request_id}, operation='edit')
        ids.append(item['node_id'])
    saved = read_canvas(tmp_path, 'board')
    store = RunStore(tmp_path)
    for node_id in ids:
        assert execution_order(saved['nodes'], saved['edges'], node_id) == [node_id]
        store.create(saved, target=node_id, request_id=node_id, project_id='p', settings={}, actor={'id': 'u'})
    with pytest.raises(ValueError, match='active run'):
        store.create(saved, target=ids[0], request_id='again', project_id='p', settings={}, actor={'id': 'u'})


@pytest.mark.asyncio
async def test_authorized_api_reuses_run_after_other_canvas_edits(monkeypatch, tmp_path):
    from novelvideo.api.routes import chat_canvas as route
    from novelvideo.freezone.orchestration.store import RunStore
    canvas(tmp_path)
    async def resolve(project, user, required_role='editor'):
        return None, 'u', 'p', tmp_path, str(tmp_path)
    async def start(project, body, user):
        saved = read_canvas(tmp_path, body.canvas_id)
        run = RunStore(tmp_path).create(saved, target=body.target, request_id=body.request_id,
                                     project_id=project, settings={}, actor=user)
        return {'data': run}
    monkeypatch.setattr(route, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(route, 'start_run', start)
    ref = reference_snapshot(tmp_path, 'board', ['source'])['nodes'][0]
    body = route.Edit(canvas_id='board', node_id='source', expected_fingerprint=ref['fingerprint'],
                      request_id='turn', action='generate', changes={'prompt': 'new'}, operation='edit')
    first = await route.edit_reference('p', body, {'id': 'u'})
    saved = read_canvas(tmp_path, 'board')
    patch_graph(tmp_path, 'board', base_revision=saved['revision'], request_id='name',
                operations=[{'op': 'update_node', 'node_id': 'source', 'data': {'displayName': 'Rename'}}])
    again = await route.edit_reference('p', body, {'id': 'u'})
    assert first == again
