import pytest

from novelvideo.character_visual.character_sheet import pixel_box, sheet_prompt


def test_crop_uses_source_dimensions():
    assert pixel_box((.1, .2, .3, .4), (1000, 500)) == (100, 100, 400, 300)


@pytest.mark.parametrize('box', [(0, 0, 0, .5), (.9, 0, .2, .4), (float('nan'), 0, .2, .2),
                                (0, 0, float('inf'), .2), (-.1, 0, .2, .2), (0, 0, .0001, .0001)])
def test_invalid_crop_rejected(box):
    with pytest.raises(ValueError):
        pixel_box(box, (1000, 500))


def test_sheet_prompt_preserves_design_and_four_regions():
    prompt = sheet_prompt({'face_shape': '长脸', 'body_type': '修长', 'outfit_states': {'default': '红色长衣'}},
                          '水墨', ['年龄：七十岁'])
    for text in ['头像大图', '无头正面', '无头侧面', '完整背面', '红色长衣', '七十岁', '水墨']:
        assert text in prompt


def test_old_candidate_defaults_to_portrait():
    from tests.character_visual.test_casting_store import pending
    candidate = pending()
    assert candidate.sheet_contract is None
    assert candidate.avatar_image is None
    assert candidate.crop_box is None
