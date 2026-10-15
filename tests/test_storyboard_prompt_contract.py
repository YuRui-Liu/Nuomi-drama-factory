from copy import deepcopy

import pytest


def panel():
    return {'id': 'p1', 'shot_id': 's1', 'anchor': 'start',
            'state': '诺尔蓝袖手持炭条停在雪地上', 'framing': '俯视近景',
            'subjects': [{'entity_id': 'noel', 'appearance_reference': 'noel-sheet',
                          'screen_position': '右侧手部', 'pose': '持炭条', 'gaze': '雪地'}],
            'setting': '钟楼内', 'lighting': '冷月光', 'visible_text': ['孩子']}


def reference():
    return {'id': 'noel-sheet', 'entity_id': 'noel', 'name': '诺尔',
            'role': 'base_design', 'url': '/noel.png', 'version': 'sha256:abc', 'panel_ids': ['p1']}


def compile_(panels=None, references=None, **kwargs):
    from novelvideo.narrative_groups.storyboard_prompt import compile_storyboard
    return compile_storyboard(panels or [panel()], references if references is not None else [reference()],
                              rows=1, cols=1, aspect_ratio='16:9', **kwargs)


def test_compile_selects_one_static_instant_and_maps_design_not_actors():
    result = compile_()
    assert '诺尔蓝袖手' in result['prompt']
    assert 'Reference 1' in result['prompt'] and '诺尔' in result['prompt']
    assert 'multiple views of ONE character' in result['prompt']
    assert 'Do not turn dialogue into writing' in result['prompt']
    assert '孩子' in result['prompt']
    assert len(result['dependency_hash']) == 64


def test_future_action_cannot_enter_static_contract():
    value = panel()
    value['action'] = '然后跑到侧门卡栓'
    with pytest.raises(ValueError):
        compile_([value])


@pytest.mark.parametrize('change', ['missing_state', 'missing_reference', 'portrait', 'wrong_scope'])
def test_invalid_character_contract_is_rejected(change):
    p, r = panel(), reference()
    if change == 'missing_state':
        p['state'] = ' '
    elif change == 'missing_reference':
        p['subjects'][0]['appearance_reference'] = 'missing'
    elif change == 'portrait':
        r['role'] = 'portrait'
    else:
        r['panel_ids'] = ['other-panel']
    with pytest.raises(ValueError):
        compile_([p], [r])


def test_attachment_order_is_the_mapping_order():
    refs = [dict(reference(), id='room', entity_id='room', name='钟楼', role='scene', url='/room.png'), reference()]
    result = compile_(references=refs)
    assert [r['index'] for r in result['references']] == [1, 2]
    assert 'Reference 2: 诺尔' in result['prompt']


def test_same_picture_cannot_represent_different_characters():
    r = reference()
    with pytest.raises(ValueError, match='ambiguous'):
        compile_(references=[r, dict(r, id='lia', entity_id='lia', name='莉娅')])


def test_capacity_is_checked_without_silent_reference_truncation():
    with pytest.raises(ValueError, match='capacity'):
        compile_(max_references=0)


def test_dependency_hash_changes_with_user_edit_but_is_deterministic():
    before = compile_()
    assert before['dependency_hash'] == compile_()['dependency_hash']
    changed = deepcopy(panel())
    changed['framing'] = '平视中景'
    assert before['dependency_hash'] != compile_([changed])['dependency_hash']


def test_duplicate_panel_ids_and_unused_cells_are_rejected():
    from novelvideo.narrative_groups.storyboard_prompt import compile_storyboard
    with pytest.raises(ValueError):
        compile_storyboard([panel(), panel()], [reference()], rows=1, cols=2, aspect_ratio='16:9')
    with pytest.raises(ValueError):
        compile_storyboard([panel()], [reference()], rows=2, cols=2, aspect_ratio='16:9')


def test_mainline_uses_same_compiler_for_explicit_static_panels():
    from novelvideo.task_backend.runners.narrative_group import _grid_prompt
    result = _grid_prompt({'layout': {'rows': 1, 'columns': 1}, 'aspect_ratio': '16:9',
        'static_panels': [panel()], 'static_references': [reference()],
        'beats': [{'visual_description': '后来跑到侧门'}]})
    assert result == compile_()['prompt']
    assert '跑到侧门' not in result


def test_execution_rejects_static_manifest_that_differs_from_actual_attachments():
    from novelvideo.task_backend.runners.narrative_group import _validate_static_attachments
    payload = {'static_panels': [panel()], 'static_references': [reference()]}
    with pytest.raises(ValueError, match='attachment'):
        _validate_static_attachments(payload, ('/different.png',))
    _validate_static_attachments(payload, ('/noel.png',))


def test_director_edit_accepts_explicit_static_panel():
    from novelvideo.director_plan.models import UpdateShot
    edit = UpdateShot(shot_id='s1', static_panel=panel())
    assert edit.static_panel.state == panel()['state']


def test_panel_actor_resolves_to_numbered_visual_identity_not_opaque_asset_id():
    ref = dict(reference(), identity_anchor='银蓝长发、蓝色披肩的少女')
    result = compile_(references=[ref])
    actor = result['prompt'].split('Panel 1: ')[1]
    assert 'Reference 1' in actor and '诺尔' in actor
    assert '银蓝长发、蓝色披肩的少女' in actor
