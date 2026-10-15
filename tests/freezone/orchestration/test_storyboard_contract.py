import pytest

from novelvideo.freezone.orchestration.adapters.requests import build_request
from novelvideo.freezone.orchestration.models import OrchestrationError


def node():
    return {'id': 'board', 'type': 'storyboardGenNode', 'data': {
        'storyboardMode': 'narrative', 'gridRows': 1, 'gridCols': 1,
        'aspectRatio': '16:9', 'model': 'gpt-image-2-vip', 'size': '2K',
        'prompt': '完整剧情：最后跑到侧门',
        'frames': [{'id': 's1', 'description': '整段动作', 'static_panel': {
            'id': 'p1', 'shot_id': 's1', 'anchor': 'start', 'state': '诺尔持炭条',
            'framing': '近景', 'subjects': [{'entity_id': 'noel', 'appearance_reference': 'ref',
                'screen_position': '右侧', 'pose': '持笔', 'gaze': '雪地'}],
            'setting': '钟楼', 'lighting': '冷月光', 'visible_text': []},
            'static_source_description': '整段动作'}],
        'referenceOrder': ['ref'],
        'referenceBindings': [{'node_id': 'ref', 'entity_id': 'noel', 'name': '诺尔',
                               'role': 'base_design', 'panel_ids': ['p1']}],
        'request': {'reference_urls': ['/wrong.png']}}}


def upstream():
    return [{'node_id': 'ref', 'result': {'imageUrl': '/noel.png', 'assetVersionId': 'v1'}}]


def build(n=None, refs=None):
    return build_request(n or node(), 'generate', upstream() if refs is None else refs,
                         canvas_id='c', attempt_id='a')[1]


def test_narrative_uses_static_contract_not_full_plot_or_legacy_reference_override():
    body = build()
    assert '诺尔持炭条' in body['prompt']
    assert '跑到侧门' not in body['prompt'] and '整段动作' not in body['prompt']
    assert body['reference_urls'] == ['/noel.png']
    assert 'Reference 1: 诺尔' in body['prompt']


def test_legacy_narrative_requires_explicit_static_contract():
    n = node()
    n['data']['frames'] = [{'id': 's1', 'description': '先写字再跑走'}]
    with pytest.raises(OrchestrationError, match='static'):
        build(n)


def test_missing_real_upstream_image_is_not_a_textual_reference():
    with pytest.raises(OrchestrationError, match='reference'):
        build(refs=[])


def test_unsynchronized_director_edit_cannot_be_silently_ignored():
    n = node()
    n['data']['frames'][0]['description'] = '改成莉娅写字'
    with pytest.raises(OrchestrationError, match='edit'):
        build(n)


def test_explicit_static_edit_changes_executed_prompt():
    n = node()
    n['data']['frames'][0]['static_panel']['framing'] = '仰视'
    assert '仰视' in build(n)['prompt']
