from dataclasses import replace
from types import SimpleNamespace
import pytest

from novelvideo.narrative_groups.service import group_beats, save_groups, load_groups
from novelvideo.narrative_groups.wardrobe_dependencies import reconcile_wardrobe_dependencies


def test_portrait_fallback_is_stale_when_identity_sheet_becomes_available(tmp_path):
    group = group_beats([{'id': 's'}])[0]
    stages = dict(group.stages)
    stages['render'] = replace(stages['render'], status='completed', revision=3,
        grid_asset='old.png', provider_parameters={'reference_audit': {'asset_references': [
            {'entity_key': '岑_砚_常服', 'asset_slot_id': 'character:岑_砚:portrait', 'asset_id': 'face'}]}})
    stages['video'] = replace(stages['video'], status='completed', revision=2, video_asset='old.mp4')
    save_groups(tmp_path, 1, [replace(group, stages=stages)])
    calls = []
    class Workflow:
        def get_slot(self, slot_id):
            calls.append(slot_id)
            return SimpleNamespace(current_version_id='sheet'), {'sheet': SimpleNamespace(
                slot_id=slot_id, asset_path='sheet.png', adoption_status=SimpleNamespace(value='provisional'))}
    (tmp_path/'sheet.png').write_bytes(b'existing asset')
    result = reconcile_wardrobe_dependencies(tmp_path, 1, Workflow())
    assert calls == ['character:岑_砚:state:岑_砚_常服']
    assert result[0].stages['render'].needs_regeneration
    assert result[0].stages['video'].needs_regeneration
    assert result[0].stages['render'].grid_asset == 'old.png'
    assert load_groups(tmp_path, 1)[0].stages['video'].video_asset == 'old.mp4'


def test_no_available_sheet_does_not_invent_a_blocker(tmp_path):
    group = group_beats([{'id': 's'}])[0]
    stages = dict(group.stages)
    stages['render'] = replace(stages['render'], status='completed', revision=1,
        provider_parameters={'reference_audit': {'asset_references': [
            {'entity_key': '甲_常服', 'asset_slot_id': 'character:甲:portrait'}]}})
    save_groups(tmp_path, 1, [replace(group, stages=stages)])
    class Workflow:
        def get_slot(self, slot_id):
            raise KeyError(slot_id)
    assert not reconcile_wardrobe_dependencies(tmp_path, 1, Workflow())[0].stages['render'].needs_regeneration


@pytest.mark.parametrize('status,slot', [('running', 'character:甲:portrait'),
    ('completed', 'character:甲:state:甲_常服')])
def test_live_generation_and_existing_identity_sheet_are_untouched(tmp_path, status, slot):
    group = group_beats([{'id': 's'}])[0]
    stages = dict(group.stages)
    stages['render'] = replace(stages['render'], status=status, revision=1,
        provider_parameters={'reference_audit': {'asset_references': [
            {'entity_key': '甲_常服', 'asset_slot_id': slot}]}})
    save_groups(tmp_path, 1, [replace(group, stages=stages)])
    class Workflow:
        def get_slot(self, slot_id):
            raise AssertionError('Already complete identity references and active tasks need no migration')
    assert not reconcile_wardrobe_dependencies(tmp_path, 1, Workflow())[0].stages['render'].needs_regeneration
