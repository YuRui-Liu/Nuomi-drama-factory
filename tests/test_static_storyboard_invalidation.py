from copy import deepcopy


def test_static_edit_invalidates_only_its_downstream_and_keeps_media():
    from novelvideo.freezone.storyboard_invalidation import invalidate_static_outputs
    before = {'nodes': [
        {'id': 'board', 'type': 'storyboardGenNode', 'data': {'frames': [{'static_panel': {'state': 'before'}}], 'imageUrl': '/old.png'}},
        {'id': 'video', 'type': 'videoDirectorNode', 'data': {'videoUrl': '/old.mp4'}},
        {'id': 'other', 'type': 'storyboardGenNode', 'data': {'imageUrl': '/other.png'}},
    ], 'edges': [{'source': 'board', 'target': 'video'}]}
    after = deepcopy(before)
    after['nodes'][0]['data']['frames'][0]['static_panel']['state'] = 'after'
    invalidate_static_outputs(before, after)
    assert after['nodes'][0]['data']['orchestration']['status'] == 'stale'
    assert after['nodes'][1]['data']['orchestration']['status'] == 'stale'
    assert after['nodes'][1]['data']['videoUrl'] == '/old.mp4'
    assert 'orchestration' not in after['nodes'][2]['data']


def test_viewport_and_result_projection_do_not_invalidate_board():
    from novelvideo.freezone.storyboard_invalidation import invalidate_static_outputs
    before = {'nodes': [{'id': 'b', 'type': 'storyboardGenNode', 'data': {'frames': [{'static_panel': {'state': 's'}}], 'imageUrl': '/old.png'}}], 'edges': []}
    after = deepcopy(before)
    after['nodes'][0]['position'] = {'x': 99, 'y': 20}
    after['nodes'][0]['data']['imageUrl'] = '/new.png'
    invalidate_static_outputs(before, after)
    assert 'orchestration' not in after['nodes'][0]['data']
