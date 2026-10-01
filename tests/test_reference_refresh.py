from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from novelvideo.director_plan.models import ShotPlan, AssetRequirement
from novelvideo.narrative_groups.reference_refresh import refresh_active_references


class Store:
    def __init__(self):
        self.published = None
    async def get_episode_from_graph(self, episode):
        return SimpleNamespace(identity_ids=[], identity_default_map={})
    def get_all_characters(self):
        return []
    async def list_scenes(self):
        return []
    async def list_props(self):
        return []
    async def replace_planned_reference_bindings_atomic(self, episode, kinds, bindings):
        self.published = (episode, kinds, bindings)


def context(tmp_path):
    return SimpleNamespace(project_id='p', state_dir=tmp_path, output_dir=tmp_path)


def plan_store():
    shot = ShotPlan(id='s', source_span_ids=('line-1',), subject='杯子', action='静置',
        visible_start_state='静置', visible_end_state='静置', duration_seconds=2,
        asset_requirements=(AssetRequirement(kind='prop', entity_key='杯子', required=False),))
    plan = SimpleNamespace(revision_id='new', groups=(SimpleNamespace(id='g', shots=(shot,), scene_anchor='room'),))
    return SimpleNamespace(lock_active_revision=lambda episode: nullcontext(plan))


@pytest.mark.asyncio
async def test_refresh_reprojects_current_revision_and_preserves_optional_missing(tmp_path):
    store = Store()
    await refresh_active_references(context(tmp_path), store, plan_store(), 1, 'new')
    episode, kinds, bindings = store.published
    assert episode == 1 and set(kinds) == {'character_identity','scene_base','scene_variant','prop'}
    assert bindings and all(b.source_plan_revision_id == 'new' for b in bindings)
    assert bindings[0].required is False
    assert bindings[0].status == 'missing_asset'


@pytest.mark.asyncio
async def test_refresh_rejects_activation_race_without_publication(tmp_path):
    store = Store()
    with pytest.raises(ValueError, match='ACTIVE_DIRECTOR_PLAN_STALE'):
        await refresh_active_references(context(tmp_path), store, plan_store(), 1, 'old')
    assert store.published is None


@pytest.mark.asyncio
async def test_refresh_keeps_explicit_identity_choice_without_creating_media(tmp_path):
    import json
    from novelvideo.models import NovelCharacter
    store = Store()
    character = NovelCharacter(name='甲', identities_json=json.dumps([
        dict(identity_id='daily', character_name='甲', identity_name='常服'),
        dict(identity_id='chosen', character_name='甲', identity_name='已选服装')]))
    store.get_all_characters = lambda: [character]
    async def episode_data(episode):
        return SimpleNamespace(identity_ids=['chosen'], identity_default_map={'甲':'chosen'})
    store.get_episode_from_graph = episode_data
    shot = ShotPlan(id='s', source_span_ids=('line-1',), subject='甲', action='站立',
        visible_start_state='站立', visible_end_state='站立', duration_seconds=2,
        asset_requirements=(AssetRequirement(kind='character_identity', entity_key='甲'),))
    plan = SimpleNamespace(revision_id='new', groups=(SimpleNamespace(id='g', shots=(shot,), scene_anchor='room'),))
    await refresh_active_references(context(tmp_path), store,
        SimpleNamespace(lock_active_revision=lambda ep: nullcontext(plan)), 1, 'new')
    binding = store.published[2][0]
    assert binding.entity_id == 'chosen'
    assert binding.status == 'missing_image'
    assert not (tmp_path/'assets').exists()


def test_character_availability_reads_existing_current_without_rewriting(tmp_path):
    from PIL import Image
    from novelvideo.models import NovelCharacter
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.narrative_groups.reference_refresh import _current_character_media
    path = tmp_path/'assets/characters/甲/portrait.png'
    path.parent.mkdir(parents=True)
    Image.new('RGB', (64,64)).save(path)
    workflow = ProductionWorkflowStore(tmp_path/'production_workflow.json')
    workflow.materialize_legacy_current(slot_id='character:甲:portrait', asset_kind='character_portrait',
        asset_path=path.relative_to(tmp_path).as_posix())
    before = workflow.state_path.read_bytes()
    portraits, identities = _current_character_media(context(tmp_path), [NovelCharacter(name='甲')])
    assert portraits == {'甲'} and not identities
    assert workflow.state_path.read_bytes() == before
