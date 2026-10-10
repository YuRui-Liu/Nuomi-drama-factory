import hashlib

import pytest
from PIL import Image


def test_image_references_follow_explicit_shot_scope_after_creature_exits(tmp_path):
    from novelvideo.task_backend.runners.narrative_group import _snapshot_generation_input
    root = tmp_path / 'assets'
    root.mkdir()
    images = []
    for entity, scope in [('forest_creature', ['shot-07']), ('traveler', ['shot-07', 'shot-08']),
                          ('unmapped', None)]:
        path = root / f'{entity}.png'
        Image.new('RGB', (32, 32), 'red').save(path)
        images.append({'image_path': str(path), 'entity_id': entity,
            'asset_kind': 'character_identity', 'resolution': 'matched', 'shot_ids': scope})
    snapshot = {'schema_version': 'narrative-reference-decision/v1', 'id': 'original',
        'images': images, 'ignored_requirement_ids': []}
    def render(shot):
        return _snapshot_generation_input({'project_dir': str(tmp_path),
            'beats': [{'id': shot, 'beat_number': 8 if shot == 'shot-08' else 3,
                       'visible_start_state': 'traveler alone' if shot == 'shot-08'
                       else 'creature escaping beside traveler'}]}, snapshot)
    earlier, later = render('shot-07'), render('shot-08')
    assert len(earlier.references) == 3
    assert len(later.references) == 2
    assert 'forest_creature' not in later.prompt
    assert 'traveler' in later.prompt
    assert any('unmapped' in path for path in later.references)
    assert later.reference_audit['out_of_scope'] == 1
    assert 'Panel 1:' in later.prompt
    assert 'use for panels 1.' in later.prompt
    assert 'use for panels 8.' not in later.prompt
    beats = [{'id': 'shot-07', 'beat_number': 3}, {'id': 'shot-08', 'beat_number': 8}]
    full = _snapshot_generation_input({'project_dir': str(tmp_path), 'beats': beats}, snapshot)
    assert 'use for panels 1, 2.' in full.prompt
    assert [beat['beat_number'] for beat in beats] == [3, 8]


@pytest.mark.parametrize("character_name", [None, "林舟", "林_舟"])
@pytest.mark.parametrize("portrait", [False, True])
def test_verified_render_snapshot_reaches_video_asset_evidence(tmp_path, monkeypatch, character_name, portrait):
    from novelvideo.task_backend.runners import narrative_group as image_runner
    from novelvideo.task_backend.runners.narrative_group_video import _explicit_asset_evidence

    path = tmp_path / "assets" / "lamp.png"
    path.parent.mkdir()
    Image.new("RGB", (32, 32), "red").save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "project_dir": str(tmp_path), "project_id": "project-1", "episode": 1,
        "group_id": "group-1", "reference_scope": {"beat_ids": ["shot-1"], "shot_ids": ["shot-1"]},
        "beats": [{"id": "shot-1", "beat_number": 1}],
    }
    snapshot = {
        "schema_version": "narrative-reference-decision/v2", "id": "refsnap_original",
        "ignored_requirement_ids": [], "images": [{
            "image_path": str(path), "relative_path": "assets/lamp.png", "sha256": digest,
            "project_id": "project-1", "episode_number": 1, "group_ids": ["group-1"],
            "beat_ids": ["shot-1"], "shot_ids": ["shot-1"], "entity_id": "lamp",
            "asset_kind": "prop", "binding_id": "binding-1", "asset_slot_id": "prop:lamp:reference",
            "version_id": "version-1", "source_id": "version-1", "resolution": "matched",
        }],
    }
    if character_name:
        identity_id = f"{character_name}_青年时期"
        snapshot["images"][0].update({
            "entity_id": identity_id,
            "asset_kind": "character_identity",
            "asset_slot_id": (f"character:{character_name}:portrait" if portrait else
                              f"character:{character_name}:state:{identity_id}"),
        })
    generated = image_runner._snapshot_generation_input(payload, snapshot)
    if character_name:
        assert f"character {character_name}," in generated.prompt
        if portrait:
            assert "face identity only" in generated.prompt
            assert "not a wardrobe reference" in generated.prompt
        else:
            assert "wardrobe reference" in generated.prompt
    render = {
        "cell_assets": [{"beat_id": "shot-1", "path": "frames/1.png"}],
        "provider_parameters": {"reference_audit": dict(generated.reference_audit)},
    }
    evidence = _explicit_asset_evidence(tmp_path, render)
    entity_key = character_name or "lamp"
    assert evidence[entity_key].asset_id == "version-1"
    assert evidence[entity_key].sha256 == digest
    if character_name:
        assert evidence[identity_id] == evidence[character_name]
    render["cell_assets"].append({"beat_id": "shot-other", "path": "frames/2.png"})
    assert _explicit_asset_evidence(tmp_path, render, shot_id="shot-1")[entity_key] == evidence[entity_key]
    assert _explicit_asset_evidence(tmp_path, render, shot_id="shot-other") == {}
    # Replaced bytes cannot inherit the original verified provenance.
    Image.new("RGB", (32, 32), "blue").save(path)
    assert _explicit_asset_evidence(tmp_path, render) == {}


def test_render_snapshot_evidence_does_not_cross_shot_scope(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _explicit_asset_evidence

    path = tmp_path / "asset.png"
    path.write_bytes(b"asset")
    render = {
        "cell_assets": [{"beat_id": "shot-other"}],
        "provider_parameters": {"reference_audit": {
            "snapshot_id": "refsnap_original", "asset_references": [{
                "entity_key": "lamp", "asset_id": "version-1", "asset_path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "shot_ids": ["shot-1"],
            }],
        }},
    }
    assert _explicit_asset_evidence(tmp_path, render) == {}
