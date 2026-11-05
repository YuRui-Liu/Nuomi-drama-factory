from novelvideo.creation.core_artifacts import CORE_KINDS, is_core_artifact


def test_image_role_is_explicit_and_internal_plan_is_hidden():
    assert len(CORE_KINDS) == 10
    assert is_core_artifact({"data": {"artifactKind": "asset"}})
    assert is_core_artifact({"data": {"artifactKind": "shot"}})
    assert not is_core_artifact({"data": {"artifactKind": "execution_plan"}})


def test_known_legacy_core_types_are_projected_but_ambiguous_images_are_not_guessed():
    assert is_core_artifact({"type": "screenplayNode", "data": {}})
    assert is_core_artifact({"type": "storyboardGenNode", "data": {}})
    assert not is_core_artifact({"type": "imageGenNode", "data": {}})
    assert not is_core_artifact({"type": "unknownNode", "data": {}})


def test_standalone_or_empty_script_reference_is_not_mistaken_for_storyboard_table():
    assert not is_core_artifact({"type": "scriptNode", "data": {}})
    assert not is_core_artifact({"type": "scriptNode", "data": {"screenplaySourceNodeId": ""}})
    assert is_core_artifact({"type": "scriptNode", "data": {"screenplaySourceNodeId": "screenplay-1"}})


def test_legacy_export_images_need_a_precise_storyboard_result_kind():
    assert is_core_artifact({"type": "exportImageNode", "data": {"resultKind": "storyboardSplitExport"}})
    assert is_core_artifact({"type": "exportImageNode", "data": {"resultKind": "storyboardGenOutput"}})
    assert not is_core_artifact({"type": "exportImageNode", "data": {"resultKind": "generic"}})
    assert not is_core_artifact({"type": "exportImageNode", "data": {}})
