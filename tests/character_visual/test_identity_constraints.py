import json
from types import SimpleNamespace
import pytest

from novelvideo.character_visual.identity_constraints import effective_identity_appearance


def fixture(tmp_path, **identity_updates):
    bible = dict(character_id='岑砚', revision_id='cast-1', status='confirmed',
        face_shape='窄脸', facial_features=['黑眼睛', '高鼻梁'], hair_style='短黑发',
        identity_anchors=['窄脸','黑眼睛','短黑发'], confirmed_by='user')
    (tmp_path/'state').mkdir(exist_ok=True)
    (tmp_path/'state/character_visual_workspaces.json').write_text(json.dumps({'岑砚':{'visual_bible':bible}}))
    identity = dict(identity_id='daily', appearance_details='黑发利落束起，灰色粗布外套，旧皮靴',
        source='identity_planner', face_prompt='', portrait_image='', age_group='youth')
    identity.update(identity_updates)
    return SimpleNamespace(name='岑砚', age_group='youth'), SimpleNamespace(**identity)


def test_adopted_hair_overrides_generated_costume_without_mutating_record(tmp_path):
    char, identity = fixture(tmp_path)
    result = effective_identity_appearance(tmp_path, char, identity)
    assert '束起' not in result and '短黑发' in result and '粗布外套' in result
    assert '束起' in identity.appearance_details


def test_manual_age_and_identity_portrait_variants_are_preserved(tmp_path):
    for override in ({'source':'user_created'}, {'age_group':'elder'},
                     {'face_prompt':'老年脸'}, {'portrait_image':'identity.png'}):
        char, identity = fixture(tmp_path, **override)
        assert effective_identity_appearance(tmp_path, char, identity).startswith(identity.appearance_details)


def test_infected_face_and_body_are_visible_to_both_generation_and_qc(tmp_path):
    char, identity = fixture(tmp_path, source='user_created',
        face_prompt='灰白肤色，浑浊双眼，保留原本五官', body_type='瘦削人形')
    result = effective_identity_appearance(tmp_path, char, identity)
    assert identity.face_prompt in result
    assert identity.body_type in result
    assert '黑眼睛' not in result


def test_identity_bible_takes_precedence(tmp_path):
    char, identity = fixture(tmp_path)
    path=tmp_path/'state/character_visual_workspaces.json'
    data=json.loads(path.read_text())
    data['岑砚']['identity_visual_bibles']={'daily':{**data['岑砚']['visual_bible'],
        'hair_style':'白色短发', 'identity_anchors':['窄脸','黑眼睛','白发']}}
    path.write_text(json.dumps(data))
    result=effective_identity_appearance(tmp_path, char, identity)
    assert '白色短发' in result and '短黑发' not in result


def test_casting_prop_anchor_does_not_become_costume_qc_requirement(tmp_path):
    char, identity = fixture(tmp_path)
    path = tmp_path/'state/character_visual_workspaces.json'
    data = json.loads(path.read_text())
    data['岑砚']['visual_bible']['identity_anchors'].append('夹克胸袋露出的折叠地图')
    path.write_text(json.dumps(data))
    result = effective_identity_appearance(tmp_path, char, identity)
    assert '折叠地图' not in result
    assert '窄脸' in result and '短黑发' in result and '粗布外套' in result


@pytest.mark.asyncio
async def test_planner_receives_adopted_design_as_costume_constraint(tmp_path, monkeypatch):
    fixture(tmp_path)
    from novelvideo.models import NovelCharacter
    from novelvideo.agents import identity_planner as module
    captured = []
    class Agent:
        async def run(self, task):
            captured.append(task)
            return SimpleNamespace(output=SimpleNamespace(appearance_details='灰色外套'))
    monkeypatch.setattr(module, '_create_identity_agent', lambda **kw: Agent())
    store = SimpleNamespace(project_dir=tmp_path,
        get_character=lambda name: NovelCharacter(name=name))
    planner = module.IdentityPlanner(store)
    await planner._generate_appearance('岑砚', '常服', '', '水站工作')
    assert '短黑发' in captured[0]
    assert '不自主设计发型' in captured[0]
