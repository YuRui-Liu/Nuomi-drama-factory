import json

import pytest

from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.projection import patch_graph
from .test_operations import setup_service, request


def projector(store):
    from novelvideo.creation.projection import ProjectionService
    return ProjectionService(store)


def move(tmp_path, **fields):
    canvas = read_canvas(tmp_path, 'canvas-1')
    return patch_graph(tmp_path, 'canvas-1', base_revision=canvas['revision'],
                       request_id=str(canvas['revision']),
                       operations=[{'op': 'update_node', 'node_id': 'n', **fields}])


def test_restart_merges_layout_and_keeps_visual_capabilities(tmp_path):
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode))
    move(tmp_path, position={'x': 100, 'y': 80}, data={'imageUrl': 'existing', 'displayName': '名字'})
    assert projector(store).recover_pending()[0].status == 'applied'
    node = read_canvas(tmp_path, 'canvas-1')['nodes'][0]
    assert node['position'] == {'x': 100, 'y': 80}
    assert node['data'] == {'prompt': '你好', 'imageUrl': 'existing', 'displayName': '名字'}
    assert projector(store).apply(pending.operation_id) == service.get('alice', pending.operation_id)


def test_crash_after_canvas_write_before_idempotency_and_outbox(tmp_path, monkeypatch):
    from novelvideo.freezone import canvas_store
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode, payload={'operations': [
        {'op': 'add_node', 'node': {'id': 'fresh', 'type': 'imageNode', 'data': {}}}],
        }, scope={'project_id': 'p', 'episode_id': episode, 'canvas_id': 'canvas-1', 'node_ids': ['fresh']}))
    original = canvas_store.append_idempotency_entry
    def fail(*args, **kwargs):
        raise OSError('simulated process loss')
    monkeypatch.setattr(canvas_store, 'append_idempotency_entry', fail)
    with pytest.raises(OSError):
        projector(store).apply(pending.operation_id)
    saved = read_canvas(tmp_path, 'canvas-1')
    monkeypatch.setattr(canvas_store, 'append_idempotency_entry', original)
    result = projector(store).apply(pending.operation_id)
    assert result.status == 'applied'
    assert result.canvas_revision == saved['revision']
    assert projector(store).apply(pending.operation_id) == result
    assert read_canvas(tmp_path, 'canvas-1') == saved
    assert len(saved['nodes']) == 2


def test_stale_input_is_durable_candidate_without_overwrite(tmp_path):
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode))
    move(tmp_path, data={'prompt': 'new human input'})
    result = projector(store).apply(pending.operation_id)
    assert result.status == 'conflict' and result.candidate_id
    assert projector(store).apply(pending.operation_id) == result
    assert read_canvas(tmp_path, 'canvas-1')['nodes'][0]['data']['prompt'] == 'new human input'
    with store.transaction() as conn:
        candidate = conn.execute('SELECT * FROM projection_candidates WHERE id=?', (result.candidate_id,)).fetchone()
        assert json.loads(candidate['patch'])['operation_id'] == pending.operation_id
        assert conn.execute("SELECT count(*) FROM objects WHERE kind='media'").fetchone()[0] == 0


@pytest.mark.parametrize('reverse', [False, True])
def test_two_failed_pending_bindings_never_restore_failed_predecessor(tmp_path, reverse):
    store, service, episode = setup_service(tmp_path)
    asset = store.create('character', {}, 'alice')
    pending = [service.apply('alice', request(episode, request_id=str(index), kind='asset.bind',
        payload={'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}})) for index in range(2)]
    move(tmp_path, data={'prompt': 'changed'})
    for operation in reversed(pending) if reverse else pending:
        assert projector(store).apply(operation.operation_id).status == 'conflict'
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 0


def test_legacy_history_restore_rejected_without_file_changes(tmp_path):
    from novelvideo.freezone.canvas_store import restore_canvas_version
    setup_service(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='局部撤销'):
        restore_canvas_version(tmp_path, 'canvas-1', history_id='anything', base_revision=2,
                               build_payload=lambda _, old: old)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_multiple_updates_and_new_node_updates_are_one_atomic_patch(tmp_path):
    store, service, episode = setup_service(tmp_path)
    req = request(episode, scope={'project_id': 'p', 'episode_id': episode, 'canvas_id': 'canvas-1', 'node_ids': ['n', 'new']},
        payload={'operations': [
            {'op': 'update_node', 'node_id': 'n', 'data': {'prompt': 'first'}},
            {'op': 'update_node', 'node_id': 'n', 'data': {'prompt': 'last'}},
            {'op': 'add_node', 'node': {'id': 'new', 'type': 'imageNode', 'data': {'prompt': 'new'}}},
            {'op': 'update_node', 'node_id': 'new', 'data': {'prompt': 'edited new'}},
        ]})
    assert projector(store).apply(service.apply('alice', req).operation_id).status == 'applied'
    assert [n['data']['prompt'] for n in read_canvas(tmp_path, 'canvas-1')['nodes']] == ['last', 'edited new']


def test_pending_atomic_receipt_survives_autosave_and_cannot_be_forged(tmp_path, monkeypatch):
    from copy import deepcopy
    from novelvideo.freezone import canvas_store
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode))
    original = canvas_store.append_idempotency_entry
    monkeypatch.setattr(canvas_store, 'append_idempotency_entry', lambda *a, **k: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):
        projector(store).apply(pending.operation_id)
    saved = read_canvas(tmp_path, 'canvas-1')
    monkeypatch.setattr(canvas_store, 'append_idempotency_entry', original)
    def autosave(existing):
        body = deepcopy(existing)
        body['metadata'] = {'creation_projection_receipts': {'forged': {}}}
        body['revision'] += 1
        body['nodes'][0]['position'] = {'x': 12, 'y': 34}
        return body
    canvas_store.save_canvas(tmp_path, 'canvas-1', base_revision=saved['revision'], build_payload=autosave)
    current = read_canvas(tmp_path, 'canvas-1')
    assert current['metadata']['creation_projection_receipts'] == saved['metadata']['creation_projection_receipts']
    result = projector(store).apply(pending.operation_id)
    assert result.canvas_revision == saved['revision']
    assert current == read_canvas(tmp_path, 'canvas-1')


@pytest.mark.asyncio
async def test_startup_rechecks_actor_project_home_mode_and_recovers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creation
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode))
    calls = []
    async def resolve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(project_id='p', requester_user_id='alice', state_dir=tmp_path, is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    monkeypatch.setattr(creation, 'load_project_config_file_from_state_dir', lambda _: {'creation_mode': 'agent_canvas_v1'})
    await creation.recover_pending_projections(tmp_path)
    assert calls == [{'user': {'id': 'alice'}, 'project_id': 'p', 'required_role': 'editor'}]
    assert service.get('alice', pending.operation_id).status == 'applied'


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', ['legacy', 'foreign_path', 'denied', 'remote'])
async def test_startup_recovery_does_not_write_unauthorized_project(tmp_path, monkeypatch, blocked):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from novelvideo.api.routes import creation
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode))
    async def resolve(**kwargs):
        if blocked == 'denied':
            raise HTTPException(403, 'denied')
        return SimpleNamespace(project_id='p', requester_user_id='alice',
            state_dir=tmp_path / 'other' if blocked == 'foreign_path' else tmp_path,
            is_home_node=blocked != 'remote')
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    monkeypatch.setattr(creation, 'load_project_config_file_from_state_dir',
                        lambda _: {} if blocked == 'legacy' else {'creation_mode': 'agent_canvas_v1'})
    await creation.recover_pending_projections(tmp_path)
    assert service.get('alice', pending.operation_id).status == 'running'
    assert read_canvas(tmp_path, 'canvas-1')['revision'] == 2


def test_remove_final_node_and_recovery_does_not_recreate_it(tmp_path):
    store, service, episode = setup_service(tmp_path)
    pending = service.apply('alice', request(episode, payload={'operations': [{'op': 'remove_node', 'node_id': 'n'}]}))
    result = projector(store).apply(pending.operation_id)
    assert result.status == 'applied'
    assert read_canvas(tmp_path, 'canvas-1')['nodes'] == []
    assert projector(store).apply(pending.operation_id) == result


def test_history_rejection_has_actionable_api_status():
    from fastapi import HTTPException
    from novelvideo.api.routes.freezone import _raise_canvas_store_http
    from novelvideo.freezone.canvas_store import CanvasSemanticRestoreRequired
    with pytest.raises(HTTPException) as exc:
        _raise_canvas_store_http(CanvasSemanticRestoreRequired('use scoped undo'))
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'scoped_undo_required'


def test_normal_canvas_with_null_metadata_remains_supported(tmp_path):
    from novelvideo.freezone.canvas_store import default_canvas_payload, save_canvas
    payload = default_canvas_payload(project_id='legacy', actor_id='alice')
    assert payload['metadata'] is None
    save_canvas(tmp_path, 'legacy', base_revision=None, build_payload=lambda _: payload)
    assert read_canvas(tmp_path, 'legacy')['metadata'] is None


@pytest.mark.parametrize('new_graph', [False, True])
def test_remove_edges_and_nodes_in_same_patch(tmp_path, new_graph):
    store, service, episode = setup_service(tmp_path)
    additions = [
        {'op': 'add_node', 'node': {'id': 'other', 'type': 'imageNode', 'data': {}}},
        {'op': 'add_edge', 'edge': {'id': 'edge', 'source': 'n', 'target': 'other'}},
    ]
    revision = 2
    if not new_graph:
        patch_graph(tmp_path, 'canvas-1', base_revision=2, request_id='add-edge', operations=additions)
        revision = 3
    req = request(episode, scope={'project_id': 'p', 'episode_id': episode, 'canvas_id': 'canvas-1', 'node_ids': ['n', 'other']},
        expected_revisions={'canvas-1': revision}, payload={'operations': (additions if new_graph else []) + [
            {'op': 'remove_edge', 'edge_id': 'edge'}, {'op': 'remove_node', 'node_id': 'other'}]})
    result = projector(store).apply(service.apply('alice', req).operation_id)
    assert result.status == 'applied'
    canvas = read_canvas(tmp_path, 'canvas-1')
    assert [n['id'] for n in canvas['nodes']] == ['n']
    assert canvas['edges'] == []


def test_partial_style_edit_preserves_theme_and_independent_size(tmp_path):
    store, service, episode = setup_service(tmp_path)
    move(tmp_path, style={'width': 100, 'height': 200, 'background': 'purple'})
    req = request(episode, expected_revisions={'canvas-1': 3},
        payload={'operations': [{'op': 'update_node', 'node_id': 'n', 'style': {'width': 300}}]})
    pending = service.apply('alice', req)
    move(tmp_path, style={'width': 100, 'height': 250, 'background': 'orange'})
    assert projector(store).apply(pending.operation_id).status == 'applied'
    assert read_canvas(tmp_path, 'canvas-1')['nodes'][0]['style'] == {
        'width': 300, 'height': 250, 'background': 'orange'}


def test_null_style_can_be_updated_and_retried(tmp_path):
    store, service, episode = setup_service(tmp_path)
    move(tmp_path, style=None)
    req = request(episode, expected_revisions={'canvas-1': 3},
        payload={'operations': [{'op': 'update_node', 'node_id': 'n', 'style': {'width': 300}}]})
    pending = service.apply('alice', req)
    result = projector(store).apply(pending.operation_id)
    assert result.status == 'applied'
    canvas = read_canvas(tmp_path, 'canvas-1')
    assert canvas['nodes'][0]['style'] == {'width': 300}
    assert service.apply('alice', req) == result
    assert projector(store).apply(pending.operation_id) == result
    assert read_canvas(tmp_path, 'canvas-1') == canvas


def test_crash_after_idempotency_log_before_semantic_ack(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store, service, episode = setup_service(tmp_path)
    asset = store.create('character', {}, 'alice')
    pending = service.apply('alice', request(episode, kind='asset.bind',
        payload={'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}}))
    original = store.transaction
    @contextmanager
    def fail():
        raise OSError('lost acknowledgement')
        yield
    monkeypatch.setattr(store, 'transaction', fail)
    with pytest.raises(OSError):
        projector(store).apply(pending.operation_id)
    saved = read_canvas(tmp_path, 'canvas-1')
    monkeypatch.setattr(store, 'transaction', original)
    assert projector(store).apply(pending.operation_id).status == 'applied'
    assert projector(store).apply(pending.operation_id).status == 'applied'
    assert read_canvas(tmp_path, 'canvas-1') == saved
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 1


def test_earlier_conflict_does_not_undo_later_successful_binding(tmp_path):
    store, service, episode = setup_service(tmp_path)
    asset = store.create('character', {}, 'alice')
    payload = {'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}}
    old = service.apply('alice', request(episode, kind='asset.bind', payload=payload))
    move(tmp_path, data={'prompt': 'new input'})
    new = service.apply('alice', request(episode, request_id='new', kind='asset.bind',
        expected_revisions={'canvas-1': 3}, payload=payload))
    assert projector(store).apply(new.operation_id).status == 'applied'
    assert projector(store).apply(old.operation_id).status == 'conflict'
    assert service.asset_uses('alice', asset['id'])[0]['operation_id'] == new.operation_id


@pytest.mark.parametrize('kind', ['graph.patch', 'asset.bind'])
def test_stale_inflight_retry_cannot_reopen_terminal_conflict(tmp_path, monkeypatch, kind):
    from novelvideo.creation import projection
    from novelvideo.creation.store import ObjectStore
    store, service, episode = setup_service(tmp_path)
    move(tmp_path, data={'prompt': 'original'})
    changes = {'kind': kind, 'expected_revisions': {'canvas-1': 3}}
    if kind == 'asset.bind':
        asset = store.create('character', {}, 'alice')
        changes['payload'] = {'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}}
    pending = service.apply('alice', request(episode, **changes))
    original_save = projection.save_canvas
    intervening = {}

    def delayed_save(*args, **kwargs):
        if not intervening:
            intervening['entered'] = True
            # The outer retry already read pending, but has not acquired the
            # canvas lock. Another worker makes the operation terminal first.
            move(tmp_path, data={'prompt': 'changed'})
            intervening['result'] = projector(ObjectStore(tmp_path)).apply(pending.operation_id)
            assert intervening['result'].status == 'conflict'
            move(tmp_path, data={'prompt': 'original'})
            intervening['canvas'] = read_canvas(tmp_path, 'canvas-1')
        return original_save(*args, **kwargs)

    monkeypatch.setattr(projection, 'save_canvas', delayed_save)
    assert projector(store).apply(pending.operation_id) == intervening['result']
    assert service.get('alice', pending.operation_id) == intervening['result']
    assert read_canvas(tmp_path, 'canvas-1') == intervening['canvas']
    with store.transaction() as conn:
        assert conn.execute('SELECT applied_revision FROM projection_outbox').fetchone()[0] is None
        assert conn.execute('SELECT count(*) FROM projection_candidates').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 0
