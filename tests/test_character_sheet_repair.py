from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image

from tests.test_character_sheet_adoption import completed_sheet
from tests.test_character_casting_generation import context


def test_assessment_schema_has_portable_array_items():
    from novelvideo.character_visual.character_repair import SheetAssessment
    from novelvideo.knowledge_runtime.codex import normalize_codex_output_schema
    schema = normalize_codex_output_schema(SheetAssessment.model_json_schema())
    array = next(v for v in schema['properties']['avatar_box']['anyOf'] if v.get('type') == 'array')
    assert array['items']['type'] == 'number'
    with pytest.raises(ValueError):
        SheetAssessment(passed=True, issues=[], avatar_box=[0, 0, .5])


def test_sheet_prompt_includes_identity_constraints():
    from novelvideo.character_visual.character_sheet import sheet_prompt
    prompt = sheet_prompt({}, '写实', [], identity_constraints={'gender': '男', 'age_group': 'youth'})
    assert '男' in prompt and 'youth' in prompt


def test_variant_inherits_gender_and_overrides_age():
    from novelvideo.character_visual.character_sheet import character_identity_constraints
    character = SimpleNamespace(name='周野', gender='男', age_group='youth',
        identities=[SimpleNamespace(identity_id='future', identity_name='十年后', age_group='middle')])
    result = character_identity_constraints(character, 'future')
    assert result['gender'] == '男' and result['age_group'] == 'middle'


def test_minor_suggestions_can_pass_quality_review():
    from novelvideo.character_visual.character_repair import SheetAssessment
    assessment = SheetAssessment(passed=True, issues=[{'region':'头像','problem':'留白较少','suggestion':'可增加留白'}], avatar_box=[0,0,.3,.7])
    assert assessment.passed


@pytest.mark.asyncio
async def test_failed_quality_still_crops_avatar_for_human_adoption(tmp_path):
    from novelvideo.character_visual.character_repair import deliver_sheet
    from novelvideo.character_visual.casting_adoption import adoption_requirements, _review_decision
    from novelvideo.character_visual.casting_models import CastingAdoption
    completed_sheet(tmp_path)
    class Runtime:
        snapshot=SimpleNamespace(model='test',runtime='test')
        async def run_structured(self,**kwargs):
            return kwargs['output_type'](passed=False,issues=[{'region':'服装','problem':'衣袖较宽','suggestion':'可收窄'}],avatar_box=[0,0,.3,.7])
    result=await deliver_sheet(ctx=context(tmp_path),candidate_id='c1',runtime=Runtime(),task_id='qc',
        generate=None,resolution=None,review_only=True,attempt_id='human-review')
    assert result.avatar_image
    assert adoption_requirements(result)['blocked_reason'] is None
    command=CastingAdoption(candidate_id='c1',expected_revision=result.snapshot.revision_id,idempotency_key='human',
        expected_asset_version=result.asset_version,expected_review_attempt_id=result.review_attempt_id,
        override_reason='用户确认当前效果可用，人工通过并采用')
    assert _review_decision(result,command)


@pytest.mark.asyncio
async def test_repair_has_full_context_and_stops_after_one_retry(tmp_path):
    from novelvideo.character_visual.character_repair import deliver_sheet
    store = completed_sheet(tmp_path)
    calls, image_calls = [], []
    class Runtime:
        snapshot = SimpleNamespace(model='test', runtime='test')
        async def run_structured(self, **kwargs):
            calls.append(kwargs)
            if kwargs['output_type'].__name__ == 'SheetRepair':
                return kwargs['output_type'](prompt='修复：无头侧面，保留原造型', changes=['去除侧面头部'])
            return kwargs['output_type'](passed=False, issues=[{'region': 'side', 'problem': '侧面出现头部',
                'suggestion': '从领口开始画身体'}], avatar_box=None)
    async def generate(**kwargs):
        image_calls.append(kwargs)
        path = Path(kwargs['output_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (120, 80)).save(path)
        return path
    result = await deliver_sheet(ctx=context(tmp_path), candidate_id='c1', runtime=Runtime(),
        task_id='task1', generate=generate, resolution=SimpleNamespace(model='test', requested_model='test', resolution_source='request'))
    assert len(image_calls) == 1
    assert result.repair_count == 1
    assert result.review_status == 'completed'
    assert result.sheet_quality['passed'] is False
    repair = next(c for c in calls if c['output_type'].__name__ == 'SheetRepair')
    for key in ['snapshot', 'generation_prompt', 'issues', 'history', 'source_material']:
        assert key in repair['prompt']
    assert repair['images']
    assert store.get('c1').repair_child_id == result.candidate_id
    again = await deliver_sheet(ctx=context(tmp_path), candidate_id='c1', runtime=Runtime(), task_id='task1',
        generate=generate, resolution=SimpleNamespace(model='test', requested_model='test', resolution_source='request'))
    assert again.candidate_id == result.candidate_id
    assert len(image_calls) == 1


@pytest.mark.asyncio
async def test_passing_sheet_crops_without_regeneration(tmp_path):
    from novelvideo.character_visual.character_repair import deliver_sheet
    completed_sheet(tmp_path)
    class Runtime:
        snapshot = SimpleNamespace(model='test', runtime='test')
        async def run_structured(self, **kwargs):
            return kwargs['output_type'](passed=True, issues=[], avatar_box=(0, 0, .3, .7))
    async def forbidden(**kwargs):
        raise AssertionError('no regeneration')
    result = await deliver_sheet(ctx=context(tmp_path), candidate_id='c1', runtime=Runtime(), task_id='task1',
        generate=forbidden, resolution=None)
    assert result.avatar_image
    assert result.asset_version == 1


@pytest.mark.asyncio
async def test_recheck_preserves_manual_crop_and_never_redraws(tmp_path):
    from novelvideo.character_visual.character_repair import deliver_sheet
    store=completed_sheet(tmp_path)
    manual=store.crop_avatar('c1',(.1,.1,.2,.4),expected_version=0)
    class Runtime:
        snapshot=SimpleNamespace(model='test',runtime='test')
        async def run_structured(self,**kwargs):
            return kwargs['output_type'](passed=True,issues=[],avatar_box=(0,0,.5,.5))
    result=await deliver_sheet(ctx=context(tmp_path),candidate_id='c1',runtime=Runtime(),task_id='recheck',
        generate=None,resolution=None,review_only=True,attempt_id='new-review')
    assert result.crop_box==manual.crop_box
    assert result.asset_version==manual.asset_version


def test_reference_budget_keeps_identity_and_direct_parent():
    from novelvideo.character_visual.character_repair import bounded_references
    refs=[{'path':str(i),'sha256':str(i)} for i in range(20)]
    bounded=bounded_references(refs)
    assert len(bounded)==6
    assert bounded[0]==refs[0]
    assert bounded[-1]==refs[-1]
    assert len(bounded_references([refs[0]]*9))==1
