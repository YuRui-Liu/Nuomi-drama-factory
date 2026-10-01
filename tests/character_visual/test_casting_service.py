import importlib.util
import pytest
from types import SimpleNamespace
from novelvideo.character_visual.models import CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from tests.character_visual.test_casting_compiler import inputs


def service():
    assert importlib.util.find_spec('novelvideo.character_visual.casting_service'), 'casting service missing'
    from novelvideo.character_visual import casting_service
    return casting_service


def test_selection_revision_cas_and_no_adoption(tmp_path):
    m = service()
    revision, proposal, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path)
    workspace = CharacterVisualWorkspace(character_id=profile.character_id, profile=profile,
        design_proposals=[proposal], casting_revision=revision.model_copy(update={'selected_proposal_id': None}))
    store.save(workspace)
    result = m.revise_draft(store, profile.character_id, identity_id=None, expected_revision=revision.revision_id,
                            selected_proposal_id=proposal.proposal_id)
    assert result.selected_proposal_id == proposal.proposal_id
    assert result.visual_bible is None
    with pytest.raises(ValueError, match='revision conflict'):
        m.revise_draft(store, profile.character_id, identity_id=None, expected_revision='old', selected_proposal_id=proposal.proposal_id)


@pytest.mark.asyncio
async def test_recast_rejects_changed_inputs_before_publication(tmp_path):
    m = service()
    revision, proposal, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile))
    async def design(**kw):
        store.save(CharacterVisualWorkspace(character_id=profile.character_id,
            profile=profile.model_copy(update={'biography': 'edited during model call'})))
        return SimpleNamespace(design_proposals=[], limitation_reason='no proposals')
    with pytest.raises(ValueError, match='changed'):
        await m.design_and_publish(store=store, character_id=profile.character_id, identity_id=None,
            expected_revision=None, grounded_profile=profile, source_revision='source1', style='水墨',
            runtime=SimpleNamespace(run_structured=design, snapshot=SimpleNamespace(task_role='knowledge_extraction')),
            assert_live=lambda: None)
    assert store.get(profile.character_id).casting_revision is None


@pytest.mark.asyncio
async def test_design_runtime_receives_authored_context_with_fact_boundary(tmp_path):
    import json
    m = service()
    _, _, profile = inputs()
    profile.authoring_context = [{'kind': 'authoring_context', 'text': '近未来末世；旧城测绘员'}]
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile))
    async def design(**kw):
        payload = json.loads(kw['prompt'])
        assert payload['casting_dossier']['narrative']['authoring_context'] == profile.authoring_context
        assert 'authoring_context' in kw['system_prompt']
        assert '不是原文事实' in kw['system_prompt']
        raise RuntimeError('captured design input')
    with pytest.raises(RuntimeError, match='captured design input'):
        await m.design_and_publish(store=store, character_id=profile.character_id, identity_id=None,
            expected_revision=None, grounded_profile=profile, source_revision='source1', style='3D国漫',
            runtime=SimpleNamespace(run_structured=design, snapshot=SimpleNamespace(task_role='knowledge_extraction')),
            assert_live=lambda: None)
