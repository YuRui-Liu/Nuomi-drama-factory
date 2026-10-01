from types import SimpleNamespace
from novelvideo.narrative_groups.planned_binding_service import _required_binding_is_published

def test_character_name_requirement_matches_published_identity_slot():
    binding = SimpleNamespace(required=True, asset_kind='character_identity', entity_id='朏朏_默认',
        base_entity_id='', variant_id='', asset_slot_id='character:朏朏:state:朏朏_默认')
    assert _required_binding_is_published(('character_identity','朏朏',''), [binding])
    assert not _required_binding_is_published(('character_identity','步知遥',''), [binding])

def test_explicit_portrait_fallback_counts_as_published_character():
    binding = SimpleNamespace(required=True, asset_kind='character_identity', entity_id='朏朏_默认',
        base_entity_id='', variant_id='', asset_slot_id='character:朏朏:portrait', resolution='explicit_fallback')
    assert _required_binding_is_published(('character_identity','朏朏',''), [binding])
    binding.resolution = 'auto_matched'
    assert not _required_binding_is_published(('character_identity','朏朏',''), [binding])
