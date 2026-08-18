from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.narrative_groups.references import (
    MAX_GROUP_IMAGE_REFERENCES,
    UnknownGroupReferenceIds,
    apply_group_reference_selection,
    resolve_group_reference_preview,
)


def _asset(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return str(path)


def _project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "alice" / "demo"
    project_dir.mkdir(parents=True)
    return project_dir


def _identity(project_dir: Path, character: str, identity: str) -> str:
    suffix = identity.removeprefix(f"{character}_")
    return _asset(
        project_dir / "assets" / "characters" / character / "identities" / f"{suffix}.png"
    )


def _portrait(project_dir: Path, character: str) -> str:
    return _asset(project_dir / "assets" / "characters" / character / "portrait.png")


def _scene(project_dir: Path, scene_id: str) -> str:
    return _asset(project_dir / "assets" / "scenes" / scene_id / "master.png")


def test_anime_style_uses_preset_positive_and_negative_constraints(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file",
        lambda username, project: {"visual_style": "anime"},
    )

    preview = resolve_group_reference_preview(project_dir, [], stage="render")

    assert preview.style.name == "anime"
    assert "2D cel animation" in preview.style.prompt
    assert "photorealistic rendering" in preview.style.prompt
    assert "3D CGI" in preview.style.prompt
    assert "finished cinematic frame" not in preview.style.prompt


def test_identity_is_preferred_and_missing_identity_falls_back_to_portrait(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    identity_path = _identity(project_dir, "阿青", "阿青_少年")
    portrait_path = _portrait(project_dir, "小白")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = [
        {
            "beat_number": 3,
            "detected_identities": ["阿青_少年", "小白_旅人"],
            "visual_description": "{{阿青_少年}}与{{小白_旅人}}相遇",
        }
    ]

    preview = resolve_group_reference_preview(project_dir, beats)
    by_name = {item.character_name: item for item in preview.image_references}

    assert by_name["阿青"].source_kind == "identity"
    assert by_name["阿青"].path == identity_path
    assert by_name["小白"].source_kind == "portrait_fallback"
    assert by_name["小白"].path == portrait_path
    assert by_name["小白"].warning


def test_scene_master_and_only_assets_appearing_in_group_are_resolved(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    scene_path = _scene(project_dir, "雨巷")
    _scene(project_dir, "未出现客厅")
    _identity(project_dir, "局外人", "局外人_默认")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )

    preview = resolve_group_reference_preview(
        project_dir, [{"beat_number": 8, "scene_id": "雨巷", "visual_description": "空镜"}]
    )

    assert len(preview.image_references) == 1
    ref = preview.image_references[0]
    assert (ref.kind, ref.source_kind, ref.scene_id, ref.path) == (
        "scene",
        "scene_master",
        "雨巷",
        scene_path,
    )


def test_references_are_deduplicated_with_beat_coverage_and_stable_order(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    _identity(project_dir, "乙", "乙_默认")
    _identity(project_dir, "甲", "甲_默认")
    _scene(project_dir, "院子")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = [
        {"beat_number": 5, "detected_identities": ["甲_默认", "乙_默认"], "scene_id": "院子"},
        {"beat_number": 2, "detected_identities": ["甲_默认"]},
        {"beat_number": 9, "visual_description": "{{甲_默认}}", "scene_id": "院子"},
    ]

    first = resolve_group_reference_preview(project_dir, beats)
    second = resolve_group_reference_preview(project_dir, list(reversed(beats)))

    assert [(r.character_name or r.scene_id, r.beat_numbers) for r in first.image_references] == [
        ("甲", (2, 5, 9)),
        ("乙", (5,)),
        ("院子", (5, 9)),
    ]
    assert [r.id for r in first.image_references] == [r.id for r in second.image_references]


def test_sorting_prioritizes_source_then_coverage_then_first_appearance(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    _identity(project_dir, "身份少", "身份少_默认")
    _identity(project_dir, "身份晚", "身份晚_默认")
    _identity(project_dir, "身份早", "身份早_默认")
    _portrait(project_dir, "肖像多")
    _portrait(project_dir, "肖像少")
    _scene(project_dir, "场景多")
    _scene(project_dir, "场景少")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = [
        {"beat_number": 1, "detected_identities": ["身份早_默认"]},
        {"beat_number": 2, "detected_identities": ["身份少_默认"]},
        {"beat_number": 3, "detected_identities": ["身份晚_默认"]},
        {"beat_number": 4, "detected_identities": ["身份早_默认", "身份晚_默认"]},
        {"beat_number": 5, "detected_identities": ["肖像多_造型", "肖像少_造型"]},
        {"beat_number": 6, "detected_identities": ["肖像多_造型"]},
        {"beat_number": 7, "scene_id": "场景少"},
        {"beat_number": 8, "scene_id": "场景多"},
        {"beat_number": 9, "scene_id": "场景多"},
    ]

    refs = resolve_group_reference_preview(project_dir, beats).image_references

    assert [(ref.source_kind, ref.character_name or ref.scene_id) for ref in refs] == [
        ("identity", "身份早"),
        ("identity", "身份晚"),
        ("identity", "身份少"),
        ("portrait_fallback", "肖像多"),
        ("portrait_fallback", "肖像少"),
        ("scene_master", "场景多"),
        ("scene_master", "场景少"),
    ]


def test_selection_none_means_all_and_empty_list_means_none(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    char_path = _identity(project_dir, "甲", "甲_默认")
    _scene(project_dir, "院子")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    preview = resolve_group_reference_preview(
        project_dir, [{"beat_number": 1, "detected_identities": ["甲_默认"], "scene_id": "院子"}]
    )

    default = apply_group_reference_selection(preview)
    characters_off = apply_group_reference_selection(
        preview, selected_character_reference_ids=[], selected_scene_reference_ids=None
    )
    all_images_off = apply_group_reference_selection(
        preview,
        use_style=False,
        selected_character_reference_ids=[],
        selected_scene_reference_ids=[],
    )

    assert len(default.image_paths) == 2
    assert characters_off.image_paths != default.image_paths
    assert char_path not in characters_off.image_paths
    assert all_images_off.image_paths == ()
    assert all_images_off.style_prompt == ""


def test_unknown_selection_ids_raise_typed_error(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    preview = resolve_group_reference_preview(project_dir, [])

    with pytest.raises(UnknownGroupReferenceIds) as raised:
        apply_group_reference_selection(preview, selected_character_reference_ids=["ghost"])

    assert raised.value.unknown_ids == ("ghost",)


def test_more_than_nine_prioritizes_characters_and_exposes_omitted(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = []
    for index in range(7):
        name = f"角色{index}"
        _identity(project_dir, name, f"{name}_默认")
        beats.append({"beat_number": index + 1, "detected_identities": [f"{name}_默认"]})
    for index in range(5):
        scene_id = f"场景{index}"
        _scene(project_dir, scene_id)
        beats.append({"beat_number": index + 20, "scene_id": scene_id})

    selection = apply_group_reference_selection(resolve_group_reference_preview(project_dir, beats))

    assert MAX_GROUP_IMAGE_REFERENCES == 9
    assert len(selection.image_paths) == 9
    assert len(selection.omitted) == 3
    assert all(ref.kind == "scene" for ref in selection.omitted)
    assert any("9" in warning for warning in selection.warnings)


def test_missing_high_priority_reference_does_not_consume_image_limit(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = []
    character_paths = []
    for index in range(7):
        name = f"角色{index}"
        character_paths.append(Path(_identity(project_dir, name, f"{name}_默认")))
        beats.append({"beat_number": index + 1, "detected_identities": [f"{name}_默认"]})
    for index in range(5):
        scene_id = f"场景{index}"
        _scene(project_dir, scene_id)
        beats.append({"beat_number": index + 20, "scene_id": scene_id})
    preview = resolve_group_reference_preview(project_dir, beats)
    character_paths[0].unlink()

    selection = apply_group_reference_selection(preview)

    assert len(selection.selected) == MAX_GROUP_IMAGE_REFERENCES
    assert len(selection.image_paths) == MAX_GROUP_IMAGE_REFERENCES
    assert any(ref.kind == "scene" for ref in selection.selected)
    assert all(Path(ref.path).is_file() for ref in selection.selected)
    assert len(selection.omitted) == 2
    assert all(Path(ref.path).is_file() for ref in selection.omitted)


def test_deleted_asset_is_not_returned_as_stale_path(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    path = Path(_identity(project_dir, "甲", "甲_默认"))
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    preview = resolve_group_reference_preview(
        project_dir, [{"beat_number": 1, "detected_identities": ["甲_默认"]}]
    )
    path.unlink()

    selection = apply_group_reference_selection(preview)

    assert selection.image_paths == ()
    assert any("不存在" in warning for warning in selection.warnings)


def test_traversal_ids_cannot_reference_files_outside_project_assets(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    escaped_identity = _asset(
        project_dir.parent / "escaped" / "identities" / "默认.png"
    )
    escaped_scene = _asset(project_dir.parent / "escaped-scene" / "master.png")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = [
        {
            "beat_number": 1,
            "detected_identities": ["../../../escaped_默认"],
            "scene_id": "../../../escaped-scene",
        }
    ]

    preview = resolve_group_reference_preview(project_dir, beats)
    selection = apply_group_reference_selection(preview)

    assert len(preview.image_references) == 2
    assert all(not ref.path for ref in preview.image_references)
    assert all(ref.warning for ref in preview.image_references)
    assert escaped_identity not in selection.image_paths
    assert escaped_scene not in selection.image_paths
    assert selection.image_paths == ()


def test_stable_ids_are_opaque_and_do_not_encode_project_path(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    _identity(project_dir, "甲", "甲_默认")
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )

    ref = resolve_group_reference_preview(
        project_dir, [{"beat_number": 1, "detected_identities": ["甲_默认"]}]
    ).image_references[0]

    assert len(ref.id) == 64
    assert all(char in "0123456789abcdef" for char in ref.id)
    assert str(project_dir) not in ref.id


def test_stable_ids_do_not_change_across_project_dirs_or_asset_availability(tmp_path, monkeypatch):
    first_project = tmp_path / "alice" / "first"
    second_project = tmp_path / "bob" / "second"
    first_project.mkdir(parents=True)
    second_project.mkdir(parents=True)
    first_path = Path(_identity(first_project, "甲", "甲_默认"))
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file", lambda *args: {}
    )
    beats = [{"beat_number": 1, "detected_identities": ["甲_默认"]}]

    with_asset = resolve_group_reference_preview(first_project, beats).image_references[0]
    without_asset_elsewhere = resolve_group_reference_preview(second_project, beats).image_references[0]
    first_path.unlink()
    after_deletion = resolve_group_reference_preview(first_project, beats).image_references[0]

    assert with_asset.id == without_asset_elsewhere.id == after_deletion.id


def test_invalid_style_falls_back_to_project_default_with_warning(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file",
        lambda *args: {"visual_style": "does-not-exist"},
    )

    preview = resolve_group_reference_preview(project_dir, [])

    assert preview.style.name != "does-not-exist"
    assert preview.style.warning
    assert preview.warnings


def test_blank_visual_style_is_invalid_and_warns_when_falling_back(tmp_path, monkeypatch):
    project_dir = _project(tmp_path)
    monkeypatch.setattr(
        "novelvideo.narrative_groups.references.load_project_config_file",
        lambda *args: {"visual_style": "   "},
    )

    preview = resolve_group_reference_preview(project_dir, [])

    assert preview.style.name == "chinese_period_drama"
    assert preview.style.id
    assert preview.style.warning
    assert preview.style.warning in preview.warnings
