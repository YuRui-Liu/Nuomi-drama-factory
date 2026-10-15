from io import BytesIO

import pytest
from PIL import Image


def test_odd_grid_and_preserved_crops():
    from novelvideo.freezone.video_director.storyboard_adaptation import adapt_image, grid_rects
    assert grid_rects(101, 57, 2, 2) == [(0, 0, 50, 28), (50, 0, 101, 28), (0, 28, 50, 57), (50, 28, 101, 57)]
    image = BytesIO()
    Image.new('RGB', (101, 57), 'red').save(image, 'PNG')
    result = adapt_image(image.getvalue(), 2, 2, '16:9')
    assert result['fingerprint'] == adapt_image(image.getvalue(), 2, 2, '16:9')['fingerprint']
    assert Image.open(BytesIO(result['cells'][0]['crop'])).size == (50, 28)
    assert len({Image.open(BytesIO(c['normalized'])).size for c in result['cells']}) == 1


@pytest.mark.parametrize('args', [(0, 10, 2, 2), (10, 10, 0, 2), (1, 10, 2, 2)])
def test_invalid_grid(args):
    from novelvideo.freezone.video_director.storyboard_adaptation import grid_rects
    with pytest.raises(ValueError):
        grid_rects(*args)


def test_invalid_image_and_rects():
    from novelvideo.freezone.video_director.storyboard_adaptation import adapt_image
    with pytest.raises(ValueError):
        adapt_image(b'bad', 2, 2, '16:9')
    image = BytesIO()
    Image.new('RGB', (10, 10)).save(image, 'PNG')
    with pytest.raises(ValueError):
        adapt_image(image.getvalue(), 1, 1, '16:9', [[0, 0, 11, 10]])


def test_descriptions_required_and_stable_segments():
    from novelvideo.freezone.video_director.storyboard_adaptation import build_draft
    template = {'revision': 0, 'aspect_ratio': '16:9', 'resolution': '720p'}
    with pytest.raises(ValueError, match='description'):
        build_draft('board', template, ['a', 'b'], [], 10)
    draft = build_draft('board', template, ['a', 'b'], [{'description': 'one'}, {'description': 'two'}], 10)
    assert [s['id'] for s in draft['segments']] == ['board-cell-1', 'board-cell-2']
    assert sum(s['duration_seconds'] for s in draft['segments']) == 10
    assert draft['references'] == []
    assert draft['segments'][0]['last_frame'] is None


def test_director_shot_identity_survives_reordering_panels():
    from novelvideo.freezone.video_director.storyboard_adaptation import build_draft
    shots = [{'id': 'reaction', 'description': '反应', 'duration_seconds': 4},
             {'id': 'action', 'description': '动作', 'duration_seconds': 6}]
    draft = build_draft('board', {}, ['b', 'a'], shots, 10)
    assert [s['id'] for s in draft['segments']] == ['reaction', 'action']


def test_planning_selected_panels_use_ref_until_endpoints_are_designed():
    from novelvideo.freezone.orchestration.planning import compile_plan
    plan = compile_plan('第一场 房间\n动作\n【本集完】', {
        'title': 't', 'assets': [{'id': 'a', 'name': 'a', 'kind': 'scene', 'prompt': 'room'}],
        'groups': [{'id': 'g', 'scene_id': 'scene-1', 'start_line': 1, 'end_line': 3, 'duration': 10,
                    'prompt': 'group', 'asset_ids': ['a'], 'shots': [{'description': f'shot{i}'} for i in range(4)]}]},
        duration=10, aspect_ratio='16:9')
    board = next(n for n in plan['nodes'] if n['id'] == 'board-g')
    director = next(n for n in plan['nodes'] if n['id'] == 'video-g')
    assert len(board['data']['frames']) == 4
    assert director['data']['autoStoryboard'] == {'sourceNodeId': 'board-g'}
    assert director['data']['activeInputMode'] == 'ref'


def test_explicit_frame_pairs_bind_endpoints_without_adjacent_shot_guessing():
    from novelvideo.freezone.video_director.storyboard_adaptation import build_draft
    cells = [{'id': 'a', 'description': '手放下'}, {'id': 'b', 'description': '另一镜'},
             {'id': 'c', 'description': '手举起'}]
    draft = build_draft('board', {}, ['a.png', 'b.png', 'c.png'], cells, 4,
        frame_pairs=[{'id': 'raise-hand', 'first_frame_id': 'a', 'last_frame_id': 'c',
                      'description': '抬起手', 'duration_seconds': 4}])
    assert draft['segments'][0]['first_frame']['url'] == 'a.png'
    assert draft['segments'][0]['last_frame']['url'] == 'c.png'
    with pytest.raises(ValueError, match='endpoint'):
        build_draft('board', {}, ['a.png', 'b.png', 'c.png'], cells, 4,
            frame_pairs=[{'id': 'bad', 'first_frame_id': 'a', 'last_frame_id': 'missing',
                          'description': 'move', 'duration_seconds': 4}])


def test_saved_mode_is_preserved_without_a_ui_intent_flag():
    from novelvideo.freezone.video_director.storyboard_preparation import _mode
    assert _mode({'activeInputMode': 'ref'}) == 'ref'
    assert _mode({'activeInputMode': 'ref', 'inputModeIntent': 'manual'}) == 'ref'
    assert _mode({'activeInputMode': 'frames'}) == 'frames'
    assert _mode({}) == 'ref'


def test_explicit_first_only_does_not_require_or_invent_a_tail():
    from novelvideo.freezone.video_director.storyboard_adaptation import build_draft
    cells = [{'id': 'a', 'description': '起点', 'static_panel': {'anchor': 'selected'}},
             {'id': 'b', 'description': '另一镜'}]
    draft = build_draft('board', {}, ['a.png', 'b.png'], cells, 5,
        frame_pairs=[{'id': 'g1', 'first_frame_id': 'a', 'last_frame_id': '',
                      'description': '向前行走', 'duration_seconds': 5}])
    assert draft['segments'][0]['first_frame']['url'] == 'a.png'
    assert draft['segments'][0]['last_frame'] is None


def test_director_slots_are_mode_specific():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs
    node = {'id': 'd', 'data': {'activeInputMode': 'frames', 'draft': {'segments': [{'id': 's'}], 'references': [{'image_id': 'old'}]}}}
    edge = {'source': 'a', 'target': 'd', 'data': {'slot': {'kind': 'firstFrame', 'segmentId': 's'}}}
    inputs = [{'node_id': 'a', 'result': {'imageUrl': '/a.png'}}]
    draft = project_director_inputs(node, inputs, [edge])
    assert draft['references'] == []
    assert draft['segments'][0]['first_frame']['url'] == '/a.png'
    node['data']['activeInputMode'] = 'ref'
    node['data']['draft'] = draft
    edge['data']['slot'] = {'kind': 'reference'}
    draft = project_director_inputs(node, inputs, [edge])
    assert draft['segments'][0]['first_frame']['url'] == '/a.png'
    assert len(draft['references']) == 1


@pytest.mark.asyncio
async def test_new_blueprint_requires_shared_director_contract(monkeypatch):
    from types import SimpleNamespace
    from novelvideo.freezone.orchestration.planning import generate_blueprint, Blueprint
    from novelvideo.freezone.orchestration.models import OrchestrationError
    import novelvideo.text_task_runtime as runtime
    import novelvideo.api.deps as deps
    import novelvideo.media_capabilities.image.catalog as catalog
    import novelvideo.freezone.orchestration.director_bridge as bridge
    blueprint = Blueprint.model_validate({'title': 't', 'assets': [{'id': 'a', 'name': 'a', 'kind': 'scene', 'prompt': 'room'}],
        'groups': [{'id': 'g', 'scene_id': 'scene-1', 'start_line': 1, 'end_line': 3, 'duration': 10,
                    'prompt': 'group', 'asset_ids': ['a']}]})
    calls = []
    async def structured(**kwargs):
        calls.append(kwargs)
        return blueprint
    async def direct(*args, **kwargs):
        calls.append(kwargs)
        return blueprint.model_dump()
    monkeypatch.setattr(bridge, 'direct_canvas', direct)
    monkeypatch.setattr(runtime, 'current_text_task_runtime', lambda: SimpleNamespace(run_structured=structured,
        snapshot=SimpleNamespace(model_dump=lambda **kwargs: {})))
    monkeypatch.setattr(deps, 'get_media_capability_store', lambda: None)
    monkeypatch.setattr(deps, 'get_media_credential_resolver', lambda: None)
    monkeypatch.setattr(catalog, 'list_image_models', lambda *args: [SimpleNamespace(provider='p', id='m')])
    with pytest.raises(OrchestrationError, match='director'):
        await generate_blueprint('第一场 房间\n动作\n【本集完】', duration=10, aspect_ratio='16:9')
    assert len(calls) == 3
