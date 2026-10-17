from copy import deepcopy

import pytest

from novelvideo.creation.node_execution import _validate_change
from novelvideo.creation.operations import OperationError


def timeline_node():
    return {'type': 'videoComposeNode', 'data': {'draftTimeline': {'draftRevision': 4, 'tracks': [
        {'id': 'audio', 'kind': 'audio', 'clips': [{
            'id': 'a', 'kind': 'audio', 'sourceUrl': '/fixed.wav', 'timelineStartMs': 0,
            'trimStartMs': 0, 'trimEndMs': 1000, 'durationMs': 3000, 'speed': 1, 'volume': 1,
        }]},
    ]}}}


def test_timeline_agent_can_edit_timing_and_silence_but_not_sources():
    node = timeline_node()
    for field, value in [('timelineStartMs', 1000), ('volume', 0), ('muted', True), ('speed', 2)]:
        path = ['draftTimeline', 'tracks', 0, 'clips', 0, field]
        assert _validate_change(node, path, value) == tuple(path)
    for field, value in [('sourceUrl', '/another.wav'), ('dubbingSource', {'takeId': 'forged'}), ('trimEndMs', 4000)]:
        with pytest.raises(OperationError):
            _validate_change(node, ['draftTimeline', 'tracks', 0, 'clips', 0, field], value)


def test_manual_subtitle_authoring_cannot_forge_dialogue_source():
    node = timeline_node()
    track = {'id': 'subtitles', 'cues': [{'id': 'c', 'text': '别开门。', 'timelineStartMs': 0,
                                       'timelineEndMs': 1000, 'timing': 'manual', 'timingConfirmed': True}]}
    assert _validate_change(node, ['draftTimeline', 'subtitleTrack'], track)
    forged = deepcopy(track)
    forged['cues'][0]['sourceTakeId'] = 'not-adopted'
    with pytest.raises(OperationError, match='forge'):
        _validate_change(node, ['draftTimeline', 'subtitleTrack'], forged)


def test_subtitle_timing_confirmation_requires_matching_current_clip():
    node = timeline_node()
    cue = {'id': 'c', 'text': '别开门。', 'timelineStartMs': 0, 'timelineEndMs': 1000,
           'sourceClipId': 'a', 'sourceClipUrl': '/fixed.wav', 'timing': 'estimated_audio_clip',
           'timingConfirmed': False, 'sourceTiming': {'timelineStartMs': 0, 'timelineEndMs': 1000,
                                                    'sourceStartMs': 0, 'sourceEndMs': 1000, 'speed': 1}}
    node['data']['draftTimeline']['subtitleTrack'] = {'id': 's', 'cues': [cue]}
    path = ['draftTimeline', 'subtitleTrack', 'cues', 0, 'timingConfirmed']
    assert _validate_change(node, path, True)
    node['data']['draftTimeline']['tracks'][0]['clips'][0]['timelineStartMs'] = 1000
    with pytest.raises(OperationError, match='current source'):
        _validate_change(node, path, True)
