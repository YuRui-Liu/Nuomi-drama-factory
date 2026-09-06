import pytest

from novelvideo.character_visual import CharacterVisualBible, compile_visual_prompt_snapshot


def test_compiler_requires_confirmed_visual_bible():
    bible = CharacterVisualBible(
        character_id="lin-mo", revision_id="vb-1", status="draft", face_shape="窄长脸"
    )
    with pytest.raises(ValueError, match="confirmed"):
        compile_visual_prompt_snapshot(
            bible=bible, project_style="二维动漫悬疑", reference_paths=[]
        )


def test_compiler_uses_only_visual_bible_style_and_explicit_references():
    bible = CharacterVisualBible(
        character_id="lin-mo",
        revision_id="vb-2",
        status="confirmed",
        face_shape="窄长脸",
        facial_features=["深眼窝", "左眉旧疤"],
        hair_style="黑色短发",
        body_type="清瘦高挑",
        distinctive_features=["左眉旧疤"],
        outfit_states={"default": "深灰连帽衫与旧牛仔裤"},
        identity_anchors=["左眉旧疤", "清瘦轮廓", "窄长脸"],
        confirmed_by="user-1",
    )
    snapshot = compile_visual_prompt_snapshot(
        bible=bible,
        project_style="二维动漫悬疑",
        reference_paths=["refs/portrait.png", "refs/portrait.png", "refs/side.png"],
    )
    assert snapshot.visual_bible_revision == "vb-2"
    assert snapshot.reference_paths == ["refs/portrait.png", "refs/side.png"]
    assert "二维动漫悬疑" in snapshot.prompt
    assert "左眉旧疤" in snapshot.prompt
    assert "深灰连帽衫" in snapshot.prompt
    assert "撞开门" not in snapshot.prompt
    assert snapshot.source_kinds == [
        "confirmed_visual_bible",
        "project_style",
        "explicit_reference",
    ]


def test_portrait_compiler_excludes_body_and_outfit_context():
    bible = CharacterVisualBible(
        character_id="xie-yan-qiu",
        revision_id="vb-portrait",
        status="confirmed",
        face_shape="窄鹅蛋脸",
        facial_features=["眼尾微垂", "鼻梁偏直"],
        hair_style="黑色长发",
        body_type="高挑纤细",
        distinctive_features=["左眉尾浅痣"],
        outfit_states={"default": "黑色长袍与银色腰带"},
        identity_anchors=["窄鹅蛋脸", "眼尾微垂", "左眉尾浅痣"],
        confirmed_by="user-1",
    )

    snapshot = compile_visual_prompt_snapshot(
        bible=bible,
        project_style="金石证痕",
        reference_paths=[],
        portrait_only=True,
    )

    assert "Face shape: 窄鹅蛋脸" in snapshot.prompt
    assert "Facial features: 眼尾微垂, 鼻梁偏直" in snapshot.prompt
    assert "Hair: 黑色长发" in snapshot.prompt
    assert "Distinctive identity features: 左眉尾浅痣" in snapshot.prompt
    assert "Identity anchors to preserve:" in snapshot.prompt
    assert "高挑纤细" not in snapshot.prompt
    assert "黑色长袍" not in snapshot.prompt
