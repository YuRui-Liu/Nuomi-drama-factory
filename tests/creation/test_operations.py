import json
import pytest

from novelvideo.creation.models import OperationRequest
from novelvideo.creation.store import ObjectStore
from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph


def setup_service(tmp_path):
    from novelvideo.creation.operations import OperationService
    store = ObjectStore(tmp_path)
    episode = store.create('episode', {}, 'alice')
    with store.transaction() as conn:
        conn.execute('INSERT INTO episode_canvases VALUES (?, ?)', (episode['id'], 'canvas-1'))
        conn.execute("INSERT INTO canvas_workspaces VALUES ('canvas-1', 'episode', ?, '集', 'fixture', 'fixture')", (episode['id'],))
    create_canvas(tmp_path, 'canvas-1', project_id='p', name='集', actor_id='alice')
    patch_graph(tmp_path, 'canvas-1', base_revision=1, request_id='seed', operations=[
        {'op': 'add_node', 'node': {'id': 'n', 'type': 'imageNode', 'data': {}, 'position': {'x': 0, 'y': 0}}}])
    service = OperationService(store, project_id='p', authorize=lambda actor, project: actor == 'alice' and project == 'p')
    return store, service, episode['id']


def request(episode, **changes):
    body = dict(request_id='r', kind='graph.patch', scope=dict(project_id='p', episode_id=episode, canvas_id='canvas-1', node_ids=['n']),
                expected_revisions={'canvas-1': 2}, payload={'operations': [{'op': 'update_node', 'node_id': 'n', 'data': {'prompt': '你好'}}]})
    body.update(changes)
    return OperationRequest(**body)


def test_pending_outbox_and_retry_after_revision_changes(tmp_path):
    store, service, episode = setup_service(tmp_path)
    req = request(episode)
    result = service.apply('alice', req)
    assert result.status == 'running'
    patch_graph(tmp_path, 'canvas-1', base_revision=2, request_id='move', operations=[{'op': 'update_node', 'node_id': 'n', 'position': {'x': 3, 'y': 2}}])
    assert service.apply('alice', req) == result
    with store.transaction() as conn:
        patch = json.loads(conn.execute('SELECT patch FROM projection_outbox').fetchone()[0])
    assert patch['operation_id'] == result.operation_id
    assert patch['before']['nodes']['n']['data'] == {}


@pytest.mark.parametrize('data', [{'imageUrl': 'forged'}, {'orchestration': {'status': 'succeeded'}}, {'assetRef': {}}])
def test_cannot_forge_node_results(tmp_path, data):
    from novelvideo.creation.operations import OperationError
    _, service, episode = setup_service(tmp_path)
    with pytest.raises(OperationError, match='字段'):
        service.apply('alice', request(episode, payload={'operations': [{'op': 'update_node', 'node_id': 'n', 'data': data}]}))


def test_auth_and_idempotency_conflicts(tmp_path):
    from novelvideo.creation.operations import OperationError
    _, service, episode = setup_service(tmp_path)
    with pytest.raises(OperationError) as exc:
        service.apply('mallory', request(episode))
    assert exc.value.code == 'scope_violation'
    service.apply('alice', request(episode))
    with pytest.raises(OperationError) as exc:
        service.apply('alice', request(episode, payload={'operations': []}))
    assert exc.value.code == 'idempotency_conflict'


@pytest.mark.parametrize('changes,code', [
    ({'expected_revisions': {}}, 'invalid_contract'),
    ({'expected_revisions': {'canvas-1': 1}}, 'revision_conflict'),
    ({'payload': {'operations': [{'op': 'update_node', 'node_id': 'other', 'data': {'prompt': 'x'}}]}}, 'scope_violation'),
    ({'payload': {'operations': [{'op': 'add_node', 'node': {'id': 'unscoped', 'type': 'imageNode', 'data': {}}}]}}, 'scope_violation'),
    ({'proposal_id': 'forged'}, 'approval_stale'),
])
def test_invalid_operations_are_atomic(tmp_path, changes, code):
    from novelvideo.creation.operations import OperationError
    store, service, episode = setup_service(tmp_path)
    with pytest.raises(OperationError) as exc:
        service.apply('alice', request(episode, **changes))
    assert exc.value.code == code
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM operations').fetchone()[0] == 0


def test_bind_is_episode_local_fixed_revision_and_retry_safe(tmp_path):
    store, service, episode = setup_service(tmp_path)
    base = store.create('character', {'name': '角色'}, 'alice')
    variant = store.create('character', {'name': '冬装'}, 'alice')
    with store.transaction() as conn:
        conn.execute('UPDATE objects SET base_asset_id=?, variant_id=? WHERE id=?', (base['id'], variant['id'], variant['id']))
    ref = {'base_asset_id': base['id'], 'variant_id': variant['id'], 'revision': 1}
    req = request(episode, kind='asset.bind', payload={'node_id': 'n', 'asset_ref': ref})
    result = service.apply('alice', req)
    store.update(variant['id'], 1, {'name': '春装'}, 'alice')
    assert service.apply('alice', req) == result
    assert service.asset_uses('alice', base['id'])[0]['asset_ref']['revision'] == 1
    assert store.read(base['id'])['revision'] == 1
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 1


def test_bind_rejects_foreign_assets_and_rollback_on_revision_conflict(tmp_path):
    from novelvideo.creation.operations import OperationError
    store, service, episode = setup_service(tmp_path)
    base = store.create('scene', {}, 'alice')
    for asset_id, revisions, code in [('foreign', {'canvas-1': 2}, 'scope_violation'), (base['id'], {'canvas-1': 1}, 'revision_conflict')]:
        with pytest.raises(OperationError) as exc:
            service.apply('alice', request(episode, kind='asset.bind', expected_revisions=revisions,
                payload={'node_id': 'n', 'asset_ref': {'base_asset_id': asset_id, 'revision': 1}}))
        assert exc.value.code == code
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 0


def test_semantic_object_versions_and_forbidden_outputs(tmp_path):
    from novelvideo.creation.operations import OperationError
    store, service, episode = setup_service(tmp_path)
    created = service.apply('alice', request(episode, kind='object.create', payload={'kind': 'story_fragment', 'data': {'text': '开场'}}))
    object_id = created.object_refs[0]['id']
    assert store.read(object_id)['payload'] == {'text': '开场', 'episode_id': episode, 'canvas_id': 'canvas-1'}
    update = request(episode, request_id='edit', kind='object.update', expected_revisions={'canvas-1': 2, object_id: 1},
                     payload={'object_id': object_id, 'data': {'text': '结束'}})
    result = service.apply('alice', update)
    assert service.apply('alice', update) == result
    assert store.read(object_id, 1)['payload']['text'] == '开场'
    assert store.read(object_id)['revision'] == 2
    with pytest.raises(OperationError) as exc:
        service.apply('alice', request(episode, request_id='forged', kind='object.update',
            expected_revisions={'canvas-1': 2, object_id: 2}, payload={'object_id': object_id, 'data': {'imageUrl': 'fake'}}))
    assert exc.value.code == 'invalid_contract'


def test_pending_bind_compensation_uses_operation_owned_relation_cas(tmp_path):
    store, service, episode = setup_service(tmp_path)
    first_asset = store.create('character', {}, 'alice')
    second_asset = store.create('character', {}, 'alice')
    def bind(asset, request_id):
        return service.apply('alice', request(episode, request_id=request_id, kind='asset.bind',
            payload={'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}}))
    first = bind(first_asset, 'first')
    second = bind(second_asset, 'second')
    with store.transaction() as conn:
        patches = {row['operation_id']: json.loads(row['patch']) for row in conn.execute('SELECT * FROM projection_outbox')}
        first_change = patches[first.operation_id]['relation_changes'][0]
        second_change = patches[second.operation_id]['relation_changes'][0]
        assert first_change['before'] is None
        assert second_change['before'] == first_change['after']
        assert json.loads(second_change['after']['payload'])['operation_id'] == second.operation_id
        # A failed earlier projection cannot remove the newer binding.
        first_after = first_change['after']
        deleted = conn.execute('DELETE FROM relations WHERE id=? AND payload=?',
                               (first_after['id'], first_after['payload'])).rowcount
        assert deleted == 0
        # The latest operation can compensate using its exact after snapshot.
        before, after = second_change['before'], second_change['after']
        restored = conn.execute('UPDATE relations SET target_id=?, payload=? WHERE id=? AND payload=?',
            (before['target_id'], before['payload'], after['id'], after['payload'])).rowcount
        assert restored == 1
        assert dict(conn.execute('SELECT * FROM relations').fetchone()) == first_change['after']


@pytest.mark.parametrize('fields', [
    {'position': None}, {'position': {'x': 1}}, {'position': {'x': True, 'y': 2}},
    {'position': {'x': float('inf'), 'y': 2}}, {'width': -1}, {'height': False},
    {'style': {'backgroundImage': 'url(x)'}}, {'style': {'width': '100%'}},
    {'parentId': 123}, {'data': {'prompt': {'unexpected': 'object'}}},
    {'data': {'duration': -100}}, {'data': {'seed': True}},
    {'data': {'temperature': float('nan')}}, {'data': {'prompt': 'x' * 100001}},
])
@pytest.mark.parametrize('operation', ['add_node', 'update_node'])
def test_graph_rejects_invalid_field_values_atomically(tmp_path, fields, operation):
    from novelvideo.creation.operations import OperationError
    store, service, episode = setup_service(tmp_path)
    if operation == 'add_node':
        patch = {'op': operation, 'node': {'id': 'fresh', 'type': 'imageNode', 'data': {}, **fields}}
        node_ids = ['fresh']
    else:
        patch = {'op': operation, 'node_id': 'n', **fields}
        node_ids = ['n']
    req = request(episode, scope={'project_id': 'p', 'episode_id': episode, 'canvas_id': 'canvas-1', 'node_ids': node_ids},
                  payload={'operations': [patch]})
    with store.transaction() as conn:
        before = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('objects', 'object_versions', 'operations', 'projection_outbox', 'relations')}
    with pytest.raises(OperationError) as exc:
        service.apply('alice', req)
    assert exc.value.code == 'invalid_contract'
    with store.transaction() as conn:
        assert before == {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in before}


def test_graph_accepts_valid_input_and_layout_values(tmp_path):
    _, service, episode = setup_service(tmp_path)
    result = service.apply('alice', request(episode, payload={'operations': [{
        'op': 'update_node', 'node_id': 'n', 'position': {'x': -200.5, 'y': 0},
        'width': 320, 'height': 180, 'style': {'width': 320, 'height': 180}, 'parentId': None,
        'data': {'prompt': '', 'duration': 5, 'seed': -1, 'temperature': 0, 'width': 1024, 'height': 1024},
    }]}))
    assert result.status == 'running'
