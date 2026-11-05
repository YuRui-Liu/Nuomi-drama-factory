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
    crop = Image.open(BytesIO(result['cells'][0]['crop']))
    assert crop.size == (48, 27)
    assert crop.width * 9 == crop.height * 16
    assert result['cells'][0]['rect'] == [0, 0, 50, 28]
    assert result['cells'][0]['cropRect'] == [1, 0, 49, 27]
    normalized = [Image.open(BytesIO(c['normalized'])) for c in result['cells']]
    assert len({image.size for image in normalized}) == 1
    assert all(image.width * 9 == image.height * 16 for image in normalized)


def test_cell_ratio_outside_safe_crop_limit_returns_visible_error():
    from novelvideo.freezone.video_director.storyboard_adaptation import adapt_image
    image = BytesIO()
    Image.new('RGB', (100, 100), 'red').save(image, 'PNG')
    with pytest.raises(ValueError, match='cell 1 aspect ratio.*16:9'):
        adapt_image(image.getvalue(), 1, 1, '16:9')


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


def test_director_reference_projection_merges_image_identity_and_reference_facts():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs
    from novelvideo.freezone.video_director.models import DirectorDraft
    from novelvideo.freezone.video_director.prepared_validation import reference_facts

    node = {'id': 'd', 'data': {'activeInputMode': 'ref', 'draft': {
        'revision': 3, 'aspect_ratio': '16:9', 'resolution': '720p',
        'references': [
            {'image_id': 'front', 'url': '/old/front.png', 'character_id': 'hero',
             'variant_id': 'day', 'variant_label': '日装正面', 'asset_kind': 'portrait'},
            {'image_id': 'profile', 'url': '/old/profile.png', 'character_id': 'hero',
             'variant_id': 'night', 'variant_label': '夜装侧面', 'asset_kind': 'costume'},
            {'image_id': 'other', 'url': '/old/other.png', 'character_id': 'outdated',
             'variant_id': 'old', 'variant_label': '旧身份', 'asset_kind': 'portrait'},
        ],
    }}}
    upstream = [
        {'node_id': 'front', 'result': {'imageUrl': '/current/front.png'}},
        {'node_id': 'profile', 'result': {
            'imageUrl': '/current/profile.png', 'characterId': 'hero', 'variantId': 'profile-v2',
            'variantLabel': '实际侧面', 'assetKind': 'portrait',
        }},
        {'node_id': 'other', 'result': {
            'imageUrl': '/current/other.png', 'characterId': 'villain', 'variantId': 'villain-v3',
            'variantLabel': '反派新身份', 'assetKind': 'identity',
        }},
        {'node_id': 'unlabeled', 'result': {'imageUrl': '/current/unlabeled.png'}},
    ]
    edges = [{'source': item['node_id'], 'target': 'd', 'data': {'slot': {'kind': 'reference'}}}
             for item in upstream]

    projected = project_director_inputs(node, upstream, edges)
    references = projected['references']
    assert references == [
        {'image_id': 'front', 'url': '/current/front.png', 'character_id': 'hero',
         'variant_id': 'day', 'variant_label': '日装正面', 'asset_kind': 'portrait'},
        {'image_id': 'profile', 'url': '/current/profile.png', 'character_id': 'hero',
         'variant_id': 'profile-v2', 'variant_label': '实际侧面', 'asset_kind': 'portrait'},
        {'image_id': 'other', 'url': '/current/other.png', 'character_id': 'villain',
         'variant_id': 'villain-v3', 'variant_label': '反派新身份', 'asset_kind': 'identity'},
        {'image_id': 'unlabeled', 'url': '/current/unlabeled.png'},
    ]

    facts = reference_facts(DirectorDraft.model_validate(projected))
    assert [fact['subject_tag'] for fact in facts] == [
        '<Subject 1>', '<Subject 1>', '<Subject 2>', '<Subject 3>',
    ]
    assert [fact['variant_id'] for fact in facts] == ['day', 'profile-v2', 'villain-v3', None]


def test_director_reference_url_refresh_drops_stale_sha256_without_a_new_hash():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs

    node = {'id': 'd', 'data': {'activeInputMode': 'ref', 'draft': {
        'references': [{'image_id': 'image', 'url': '/old.png', 'sha256': 'old-digest'}],
    }}}
    upstream = [{'node_id': 'image', 'result': {'imageUrl': '/current.png'}}]
    edges = [{'source': 'image', 'target': 'd', 'data': {'slot': {'kind': 'reference'}}}]

    projected = project_director_inputs(node, upstream, edges)

    assert projected['references'] == [{'image_id': 'image', 'url': '/current.png'}]


def test_director_reference_character_switch_clears_previous_costume_variant():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs

    node = {'id': 'd', 'data': {'activeInputMode': 'ref', 'draft': {
        'references': [{'image_id': 'switch', 'url': '/old.png', 'character_id': 'former',
                        'variant_id': 'former-costume', 'variant_label': '旧角色服装',
                        'asset_kind': 'identity_costume'}],
    }}}
    upstream = [{'node_id': 'switch', 'result': {
        'imageUrl': '/current.png', 'characterId': 'replacement',
    }}]
    edges = [{'source': 'switch', 'target': 'd', 'data': {'slot': {'kind': 'reference'}}}]

    projected = project_director_inputs(node, upstream, edges)

    assert projected['references'] == [{
        'image_id': 'switch', 'url': '/current.png', 'character_id': 'replacement',
        'asset_kind': 'identity_costume',
    }]


def test_director_reference_variant_switch_clears_old_label_without_a_new_label():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs

    node = {'id': 'd', 'data': {'activeInputMode': 'ref', 'draft': {
        'references': [{'image_id': 'image', 'url': '/day.png', 'character_id': 'hero',
                        'variant_id': 'day', 'variant_label': '白衣日装'}],
    }}}
    upstream = [{'node_id': 'image', 'result': {
        'imageUrl': '/night.png', 'variantId': 'night',
    }}]
    edges = [{'source': 'image', 'target': 'd', 'data': {'slot': {'kind': 'reference'}}}]

    projected = project_director_inputs(node, upstream, edges)

    assert projected['references'] == [{
        'image_id': 'image', 'url': '/night.png', 'character_id': 'hero', 'variant_id': 'night',
    }]


def test_director_reference_character_and_variant_switch_clears_old_label():
    from novelvideo.freezone.orchestration.adapters.requests import project_director_inputs

    node = {'id': 'd', 'data': {'activeInputMode': 'ref', 'draft': {
        'references': [{'image_id': 'image', 'url': '/old.png', 'character_id': 'former',
                        'variant_id': 'day', 'variant_label': '白衣日装'}],
    }}}
    upstream = [{'node_id': 'image', 'result': {
        'imageUrl': '/replacement-night.png', 'characterId': 'replacement', 'variantId': 'night',
    }}]
    edges = [{'source': 'image', 'target': 'd', 'data': {'slot': {'kind': 'reference'}}}]

    projected = project_director_inputs(node, upstream, edges)

    assert projected['references'] == [{
        'image_id': 'image', 'url': '/replacement-night.png',
        'character_id': 'replacement', 'variant_id': 'night',
    }]


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
