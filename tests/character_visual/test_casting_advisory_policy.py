import pytest
from types import SimpleNamespace

from novelvideo.character_visual import casting_service
from novelvideo.character_visual.casting_adoption import adoption_requirements, _review_decision
from novelvideo.character_visual.models import CharacterNarrativeProfile, CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from novelvideo.character_design_stage import CharacterDesignOutput
from tests.test_character_build_stages import proposals as valid_payloads


def test_initial_design_stage_uses_same_advisory_filter():
    from novelvideo.character_design_stage import design_quality_issues
    assert design_quality_issues('甲', CharacterDesignOutput(design_proposals=valid_payloads()[:1]),
        profile=CharacterNarrativeProfile(character_id='甲', name='甲')) == {}


def test_diagnostic_names_used_as_ids_cannot_hide_hard_errors():
    from novelvideo.character_visual.casting_proposals import blocking_casting_issues
    issues = ['structure_collision:unknown_fact:f', 'unknown_fact:structure_collision',
              'structure_collision:new_contract:error', 'new_contract:structure_collision', 'new_contract:error']
    assert blocking_casting_issues(issues) == issues


@pytest.mark.asyncio
async def test_single_advisory_proposal_generates_selects_and_compiles_once(tmp_path):
    profile = CharacterNarrativeProfile(character_id='甲', name='甲')
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id='甲', profile=profile))
    calls = []
    async def design(**kwargs):
        calls.append(kwargs)
        payload = valid_payloads()[0]
        payload['distinctive_features'] = ['左脸有疤痕']
        return CharacterDesignOutput(design_proposals=[payload])
    workspace = await casting_service.design_and_publish(store=store, character_id='甲', identity_id=None,
        expected_revision=None, grounded_profile=profile, source_revision='source1', style='水墨',
        runtime=SimpleNamespace(run_structured=design, snapshot=SimpleNamespace(task_role='knowledge_extraction')),
        assert_live=lambda: None)
    assert len(calls) == 1
    assert workspace.design_proposals[0].quality_issues
    workspace = casting_service.revise_draft(store, '甲', identity_id=None,
        expected_revision=workspace.casting_revision.revision_id,
        selected_proposal_id=workspace.design_proposals[0].proposal_id)
    assert casting_service.compile_current(workspace, None, workspace.casting_revision.revision_id, 'source1', '水墨')


@pytest.mark.parametrize('status', ['not_started', 'running', 'failed'])
def test_review_state_is_advisory(status):
    candidate = SimpleNamespace(generation_status='succeeded', review_status=status, review_attempt_id='new')
    command = SimpleNamespace(expected_review_attempt_id='old', acknowledged_findings=[], override_reason=None)
    requirements = adoption_requirements(candidate)
    assert requirements['blocked_reason'] is None
    assert requirements['required_acknowledgements'] == []
    _review_decision(candidate, command)


@pytest.mark.asyncio
async def test_structural_budget_survives_retries_and_new_store(tmp_path):
    profile = CharacterNarrativeProfile(character_id='甲', name='甲')
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id='甲', profile=profile))
    calls = []
    async def design(**kwargs):
        calls.append(kwargs)
        payloads = valid_payloads()
        for p in payloads:
            p['proposal_id'] = 'duplicate'
        return CharacterDesignOutput(design_proposals=payloads)
    kwargs = dict(character_id='甲', identity_id=None, expected_revision=None, grounded_profile=profile,
        source_revision='source1', style='水墨', assert_live=lambda: None,
        runtime=SimpleNamespace(run_structured=design, snapshot=SimpleNamespace(task_role='knowledge_extraction')))
    for _ in range(2):
        with pytest.raises(ValueError, match='casting proposals rejected|budget'):
            await casting_service.design_and_publish(store=CharacterVisualWorkspaceStore(tmp_path), **kwargs)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        await casting_service.design_and_publish(store=store, **{**kwargs, 'style': '油画'})
    assert len(calls) == 4
