import pytest

from novelvideo.character_visual.models import CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from tests.character_visual.test_casting_compiler import inputs


def test_atomic_stage_draft_preserves_base_and_other_stage(tmp_path):
    revision, proposal, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile,
        design_proposals=[proposal], selected_proposal_id=proposal.proposal_id, casting_revision=revision))
    def change(workspace):
        workspace.identity_design_proposals['older'] = [proposal.model_copy(update={'title': 'older'})]
        workspace.identity_selected_proposal_ids['older'] = proposal.proposal_id
        workspace.identity_casting_revisions['older'] = revision.model_copy(update={'identity_id': 'older'})
    assert hasattr(store, 'mutate'), 'workspace requires atomic draft mutation'
    saved = store.mutate(profile.character_id, identity_id='older', expected_revision=None, change=change)
    assert saved.design_proposals == [proposal]
    assert saved.casting_revision == revision
    assert saved.visual_bible is None
    assert saved.identity_design_proposals['older'][0].title == 'older'
    with pytest.raises(ValueError, match='revision conflict'):
        store.mutate(profile.character_id, identity_id='older', expected_revision=None, change=change)
    store.save_many([CharacterVisualWorkspace(character_id=profile.character_id, profile=profile)])
    assert store.get(profile.character_id).identity_design_proposals == saved.identity_design_proposals


def test_authoritative_task_can_bind_token_once(tmp_path):
    from tests.character_visual.test_casting_store import store_at, pending
    store = store_at(tmp_path)
    assert hasattr(store, 'bind_generation_task'), 'runner needs token-bound task assignment'
    candidate = pending().model_copy(update={'task_id': 'submission:token', 'submission_token': 'token'})
    store.create_pending(candidate)
    bound = store.bind_generation_task('c1', submission_token='token', task_id='actual-task')
    assert bound.task_id == 'actual-task'
    assert store.bind_generation_task('c1', submission_token='token', task_id='actual-task') == bound
    with pytest.raises(ValueError):
        store.bind_generation_task('c1', submission_token='token', task_id='other-task')
    with pytest.raises(ValueError):
        store.bind_generation_task('c1', submission_token='wrong', task_id='actual-task')


def test_automatic_build_does_not_replace_explicit_unselected_recast(tmp_path):
    revision, proposal, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path)
    workspace = CharacterVisualWorkspace(character_id=profile.character_id, profile=profile,
        design_proposals=[proposal], casting_revision=revision,
        casting_limitation_reasons={'base': ''})
    store.save(workspace)
    replacement = workspace.model_copy(deep=True)
    replacement.design_proposals[0].title = 'new automatic proposal'
    replacement.casting_revision.revision_id = 'new-automatic-revision'
    store.save_many([replacement])
    assert store.get(profile.character_id) == workspace
