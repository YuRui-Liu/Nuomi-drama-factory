import pytest

from novelvideo.director_plan.models import NarrativeGroupPlan
from novelvideo.director_plan.planner import DirectorPlanDraft
from tests.director_plan.creative_fixtures import creative_shot


def draft():
    shots = []
    for i in range(5):
        shot = creative_shot(f's{i}', f's{i+1}' if i < 4 else None, duration=4)
        shots.append(shot.model_copy(update={'source_span_ids': ('line-1', 'line-2', 'line-3')}))
    return DirectorPlanDraft(groups=(NarrativeGroupPlan(
        id='g1', ordinal=1, source_span_ids=('line-1', 'line-2', 'line-3'),
        scene_anchor='room', time_anchor='night', objective='确认门被锁住', visible_turn='不能离开',
        relation_to_previous='single', creative_contract_version=1, shots=tuple(shots)),))


def test_shared_director_plan_projects_to_five_shots_without_losing_decisions():
    from novelvideo.freezone.orchestration.director_bridge import project_draft
    from novelvideo.freezone.orchestration.planning import compile_plan
    script = '第一场 房间 夜\n门锁着\n【本集完】'
    blueprint = project_draft(script, draft())
    plan = compile_plan(script, blueprint, duration=20, aspect_ratio='16:9')
    board = next(n['data'] for n in plan['nodes'] if n['id'] == 'board-g1')
    assert [s['id'] for s in board['frames']] == [f's{i}' for i in range(5)]
    assert board['directorPlan']['objective'] == '确认门被锁住'
    assert board['directorPlan']['shots'][0]['creative']['handoff']['cut_trigger'] == 'wind dies'
    assert 'door closed' in board['frames'][0]['description']


def test_explicit_static_decision_survives_director_and_canvas_projection():
    from novelvideo.freezone.orchestration.director_bridge import project_draft
    from novelvideo.freezone.orchestration.planning import compile_plan
    value = draft().model_dump(mode='json')
    for shot in value['groups'][0]['shots']:
        shot['static_panel'] = {'id': shot['id'], 'shot_id': shot['id'], 'anchor': 'start',
            'state': 'closed door', 'framing': 'one wide frame', 'subjects': [],
            'setting': 'room', 'lighting': 'moonlight', 'visible_text': []}
    validated = DirectorPlanDraft.model_validate(value)
    script = '第一场 房间 夜\n门锁着\n【本集完】'
    plan = compile_plan(script, project_draft(script, validated), duration=20, aspect_ratio='16:9')
    board = next(n['data'] for n in plan['nodes'] if n['id'] == 'board-g1')
    assert board['frames'][0]['static_panel']['state'] == 'closed door'
    assert board['frames'][0]['static_source_description'] == board['frames'][0]['description']


def test_unknown_director_source_reference_cannot_be_projected():
    from novelvideo.freezone.orchestration.director_bridge import project_draft
    value = draft()
    group = value.groups[0].model_copy(update={'source_span_ids': ('line-999',)})
    with pytest.raises(ValueError, match='source'):
        project_draft('第一场 房间\n门锁着\n【本集完】', value.model_copy(update={'groups': (group,)}))


def test_character_binding_requires_sheet_not_avatar():
    from novelvideo.freezone.orchestration.director_bridge import character_asset
    current = {'url': '/portrait.png', 'version_id': 'v1', 'adoption_status': 'adopted'}
    assert character_asset('莉娅', current)['image_url'] is None
    current['sheet_url'] = '/full-design.png'
    assert character_asset('莉娅', current)['image_url'] == '/full-design.png'


def test_script_metadata_is_not_spoken_dialogue():
    from novelvideo.freezone.orchestration.director_bridge import source_spans
    spans = source_spans('第一场 房间\n场景：房间\n人物：莉娅\n莉娅（轻声）：跟我走？\n【本集完】')
    assert [s.dialogue_text for s in spans if s.dialogue_text] == ['莉娅（轻声）：跟我走？']


def test_explicit_canvas_base_design_is_verified_and_versioned(tmp_path):
    from PIL import Image
    from novelvideo.freezone.orchestration.director_bridge import bind_canvas_designs
    Image.new('RGB', (20, 10)).save(tmp_path / 'sheet.png')
    binding = {'name': '小男孩', 'role': 'base_design', 'image_url': '/static/projects/p/sheet.png'}
    result = bind_canvas_designs([], [binding], project_dir=tmp_path, project_id='p')
    assert result[0]['name'] == '小男孩'
    assert result[0]['version_id'] == 'sha256:' + result[0]['asset_sha256']
    with pytest.raises(ValueError, match='base_design'):
        bind_canvas_designs([], [{**binding, 'role': 'portrait'}], project_dir=tmp_path, project_id='p')
    with pytest.raises(Exception, match='another project'):
        bind_canvas_designs([], [{**binding, 'image_url': '/static/projects/other/sheet.png'}], project_dir=tmp_path, project_id='p')


def test_canvas_design_does_not_silently_replace_adopted_library_design(tmp_path):
    from novelvideo.freezone.orchestration.director_bridge import bind_canvas_designs
    library = [{'name': '莉娅', 'kind': 'character', 'image_url': '/adopted.png'}]
    with pytest.raises(ValueError, match='already adopted'):
        bind_canvas_designs(library, [{'name': '莉娅', 'role': 'base_design', 'image_url': '/new.png'}],
                            project_dir=tmp_path, project_id='p')


def test_missing_character_design_becomes_canvas_generation_dependency():
    from novelvideo.director_plan.models import AssetRequirement
    from novelvideo.freezone.orchestration.director_bridge import project_draft
    from novelvideo.freezone.orchestration.planning import compile_plan
    value = draft()
    group = value.groups[0]
    shot = group.shots[0].model_copy(update={'asset_requirements': (
        AssetRequirement(kind='character_identity', entity_key='小男孩', design_notes='旧绿外套，围巾'),)})
    value = value.model_copy(update={'groups': (group.model_copy(update={'shots': (shot, *group.shots[1:])}),)})
    script = '第一场 房间 夜\n门锁着\n【本集完】'
    blueprint = project_draft(script, value)
    plan = compile_plan(script, blueprint, duration=20, aspect_ratio='16:9')
    asset = next(n for n in plan['nodes'] if n['data'].get('assetKind') == 'character')
    assert not asset['data'].get('referenceOnly')
    assert '全身' in asset['data']['prompt']
    assert any(e['source'] == asset['id'] and e['target'] == 'board-g1' for e in plan['edges'])
