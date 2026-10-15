from copy import deepcopy

import pytest

from novelvideo.chat.canvas_context import apply_context_edit, reference_snapshot, place_result
from novelvideo.freezone.canvas_store import read_canvas, save_canvas
from novelvideo.freezone.orchestration.models import input_fingerprint
from novelvideo.freezone.orchestration.projection import project_result
from tests.test_chat_canvas_context import canvas


def candidate(tmp_path):
    canvas(tmp_path)
    source = reference_snapshot(tmp_path, 'board', ['source'])['nodes'][0]
    selected = apply_context_edit(tmp_path, 'board', node_id='source', expected_fingerprint=source['fingerprint'],
                                  request_id='generate', action='generate', changes={'prompt': 'new'}, operation='edit')
    node = next(n for n in read_canvas(tmp_path, 'board')['nodes'] if n['id'] == selected['node_id'])
    return node


@pytest.mark.parametrize('deleted', [False, True])
def test_source_changed_preserves_media_and_removes_dangling_provenance(tmp_path, deleted):
    node = candidate(tmp_path)
    def change(existing):
        updated = deepcopy(existing)
        if deleted:
            updated['nodes'] = [item for item in updated['nodes'] if item['id'] != 'source']
        else:
            updated['nodes'][0]['data']['prompt'] = '用户的新版本'
        updated['revision'] += 1
        return updated
    save_canvas(tmp_path, 'board', base_revision=None, enforce_revision=False, build_payload=change)
    args = dict(node_id=node['id'], expected_input=input_fingerprint(node), attempt_id='paid-once', result={'imageUrl': '/result.png'})
    first = project_result(tmp_path, 'board', **args)
    second = project_result(tmp_path, 'board', **args)
    assert first == second
    result = next(n for n in first['nodes'] if n['id'] == node['id'])
    assert result['data']['imageUrl'] == '/result.png'
    assert result['data']['orchestration']['status'] == 'source_changed'
    assert result['data']['orchestration']['reason'] == ('source_deleted' if deleted else 'source_modified')
    assert result['data']['chatOrigin']['node_id'] == 'source'
    if deleted:
        assert first['edges'] == []
    else:
        assert first['nodes'][0]['data']['prompt'] == '用户的新版本'


def test_explicit_placement_validates_both_versions_and_preserves_result(tmp_path):
    node = candidate(tmp_path)
    def change(existing):
        updated = deepcopy(existing)
        updated['nodes'][0]['data']['prompt'] = 'changed'
        updated['revision'] += 1
        return updated
    save_canvas(tmp_path, 'board', base_revision=None, enforce_revision=False, build_payload=change)
    project_result(tmp_path, 'board', node_id=node['id'], expected_input=input_fingerprint(node),
                   attempt_id='paid-once', result={'imageUrl': '/result.png'})
    refs = reference_snapshot(tmp_path, 'board', [node['id'], 'source'])['nodes']
    args = dict(node_id=node['id'], expected_fingerprint=refs[0]['fingerprint'], source_node_id='source',
                expected_source_fingerprint=refs[1]['fingerprint'], request_id='place')
    with pytest.raises(ValueError, match='变化'):
        place_result(tmp_path, 'board', **{**args, 'expected_source_fingerprint': '0' * 64})
    with pytest.raises(ValueError, match='删除'):
        place_result(tmp_path, 'board', **{**args, 'source_node_id': 'another-canvas-only-node'})
    with pytest.raises(ValueError, match='自己'):
        place_result(tmp_path, 'board', **{**args, 'source_node_id': node['id']})
    one = place_result(tmp_path, 'board', **args)
    two = place_result(tmp_path, 'board', **args)
    assert one == two
    result = read_canvas(tmp_path, 'board')
    placed = next(n for n in result['nodes'] if n['id'] == node['id'])
    assert placed['data']['imageUrl'] == '/result.png'
    assert placed['data']['orchestration']['status'] == 'succeeded'
    assert '来源已变化' not in placed['data']['displayName']
    assert len(result['edges']) == 1
    assert result['edges'][0]['source'] == 'source'
    assert placed['data']['chatOrigin']['fingerprint'] == refs[1]['fingerprint']
