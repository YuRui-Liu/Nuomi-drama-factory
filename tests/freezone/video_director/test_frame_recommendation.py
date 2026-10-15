import pytest
from novelvideo.freezone.video_director.frame_recommendation import (
    FrameRecommendations, validate_recommendations, recommend_frames,
)

FRAMES = [{'id': 'a', 'static_panel': {'anchor': 'start'}},
          {'id': 'b', 'static_panel': {'anchor': 'selected'}},
          {'id': 'c', 'static_panel': {'anchor': 'end'}}]
SEGMENTS = [{'id': 'g1', 'description': '抬手', 'duration_seconds': 5}]


def result(mode='first', first='a', last=None):
    return FrameRecommendations.model_validate({'segments': [{'id': 'g1', 'mode': mode,
        'first_frame_id': first, 'last_frame_id': last, 'reason': '动作自由发展'}]})


def test_first_only_and_explicit_pair_preserve_source():
    pair = validate_recommendations(result(), FRAMES, SEGMENTS)[0]
    assert pair['last_frame_id'] == ''
    assert pair['description'] == '抬手'
    assert pair['duration_seconds'] == 5
    assert validate_recommendations(result('first_last', last='c'), FRAMES, SEGMENTS)[0]['last_frame_id'] == 'c'


@pytest.mark.parametrize('value', [result(first='missing'), result(last='c'),
    result('first_last', last='b'), result('first_last', last='a')])
def test_rejects_invalid_or_invented_endpoints(value):
    with pytest.raises(ValueError):
        validate_recommendations(value, FRAMES, SEGMENTS)


@pytest.mark.asyncio
async def test_ai_recommendation_does_not_mutate_manual_inputs():
    class Runtime:
        async def run_structured(self, **kwargs):
            assert '不能将相邻镜头' in kwargs['system_prompt']
            return result()
    manual = [{**SEGMENTS[0], 'selection_source': 'manual', 'last_frame_id': 'c'}]
    recommended = await recommend_frames(Runtime(), FRAMES, manual)
    assert manual[0]['last_frame_id'] == 'c'
    assert recommended[0]['last_frame_id'] == ''


def test_rejects_tail_from_another_director_shot():
    frames = [dict(FRAMES[0], static_panel={'anchor': 'start', 'shot_id': 'g1'}),
              dict(FRAMES[2], static_panel={'anchor': 'end', 'shot_id': 'g2'})]
    with pytest.raises(ValueError, match='same director shot'):
        validate_recommendations(result('first_last', last='c'), frames, SEGMENTS)
