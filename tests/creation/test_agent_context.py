from novelvideo.creation.agent_context import node_snapshot


def test_node_snapshot_exposes_structured_static_panel_fields_without_arbitrary_data():
    snapshot = node_snapshot({
        'id': 'storyboard', 'type': 'storyboardGenNode', 'data': {
            'frames': [{
                'id': 'frame-1', 'description': '两人对峙', 'referenceIndex': 0,
                'static_panel': {
                    'id': 'panel-1', 'shot_id': 'shot-1', 'anchor': 'start',
                    'state': '两人隔桌相望', 'framing': '中景', 'setting': '雨夜站台',
                    'lighting': '冷色侧光', 'visible_text': [],
                    'private_locator': 'https://example.invalid/secret.png',
                },
                'static_source_description': '参考上游分镜',
                'private_field': 'must not leak',
            }],
            'extraParams': {'api_key': 'must not leak'},
            'imageUrl': '/private/result.png',
        },
    })
    frame = snapshot['editable_fields']['frames'][0]
    assert frame['static_panel']['lighting'] == '冷色侧光'
    assert frame['static_panel']['framing'] == '中景'
    assert 'private_locator' not in frame['static_panel']
    assert 'private_field' not in frame
    assert 'extraParams' not in snapshot['editable_fields']
    assert 'imageUrl' not in snapshot['editable_fields']


def test_editable_frames_ignore_malformed_duration_and_read_only_identifiers():
    snapshot = node_snapshot({
        'id': 'storyboard', 'type': 'storyboardGenNode', 'data': {
            'frames': [
                {'id': 'frame-a', 'referenceIndex': 'corrupt', 'duration_seconds': 'bad',
                 'description': '旧帧'},
                {'id': 'frame-b', 'referenceIndex': -1, 'duration_seconds': 1.25,
                 'description': '合法帧'},
            ],
        },
    })
    frames = snapshot['editable_fields']['frames']
    assert frames == [{'description': '旧帧'}, {'description': '合法帧', 'duration_seconds': 1.25}]
