from copy import deepcopy

import pytest


def plan_data():
    return dict(schemaVersion=1, revision=0,
                source=dict(assetVersionId='video', sha256='a' * 64, durationMs=90000),
                tracks=[dict(id='t1', name='底乐', clips=[dict(id='c1', assetVersionId='a',
                    startMs=0, sourceInMs=0, lengthMs=90000)])])


def test_plan_accepts_cross_track_overlap():
    from novelvideo.music.models import MusicPlan
    data = plan_data()
    second = deepcopy(data['tracks'][0])
    second.update(id='t2')
    second['clips'][0]['id'] = 'c2'
    data['tracks'].append(second)
    assert len(MusicPlan.model_validate(data).tracks) == 2


@pytest.mark.parametrize('change', ['overlap', 'negative', 'outside', 'fade', 'duplicate', 'nan'])
def test_invalid_plan_rejected(change):
    from novelvideo.music.models import MusicPlan
    data = plan_data()
    clip = data['tracks'][0]['clips'][0]
    if change == 'overlap':
        data['tracks'][0]['clips'].append({**clip, 'id': 'c2'})
    elif change == 'negative': clip['sourceInMs'] = -1
    elif change == 'outside': clip['startMs'] = 1
    elif change == 'fade': clip['fadeInMs'] = 90001
    elif change == 'duplicate': data['tracks'].append(deepcopy(data['tracks'][0]))
    elif change == 'nan': clip['gainDb'] = float('nan')
    with pytest.raises(ValueError): MusicPlan.model_validate(data)


def test_source_length_and_loop_validation():
    from novelvideo.music.models import MusicPlan, validate_sources
    data = plan_data()
    plan = MusicPlan.model_validate(data)
    with pytest.raises(ValueError): validate_sources(plan, {'a': 1000})
    data['tracks'][0]['clips'][0]['loop'] = dict(startMs=0, endMs=1000)
    validate_sources(MusicPlan.model_validate(data), {'a': 1000})

