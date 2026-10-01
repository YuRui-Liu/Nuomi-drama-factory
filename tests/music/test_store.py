import pytest


def test_private_library_version_and_project_grant(tmp_path):
    from novelvideo.music.store import MusicStore
    s = MusicStore(tmp_path / 'music.db')
    a = s.add_asset('alice', path='/fixture', sha256='abc', duration_ms=120000, name='Rain')
    assert len(s.list_assets('alice')) == 1
    assert s.list_assets('bob') == []
    with pytest.raises(PermissionError): s.version(a['versionId'], owner='bob')
    s.reference('alice', 'p1', a['versionId'])
    assert s.version(a['versionId'], project='p1')['name'] == 'Rain'
    s.update_asset('alice', a['id'], 1, {'name': 'Changed', 'archived': True})
    assert s.version(a['versionId'], project='p1')['name'] == 'Rain'
    with pytest.raises(ValueError): s.reference('alice', 'p2', a['versionId'])
    with pytest.raises(PermissionError): s.version(a['versionId'], project='p2')


def test_compare_and_swap_and_job_idempotency(tmp_path):
    from novelvideo.music.store import MusicStore, Conflict
    s = MusicStore(tmp_path / 'music.db')
    assert s.save_plan('p', 'canvas', 'node', {'revision': 0}, 0)['revision'] == 1
    with pytest.raises(Conflict): s.save_plan('p', 'canvas', 'node', {}, 0)
    first, created = s.create_job('p', 'id', 'render', {'revision': 1})
    assert created
    second, created = s.create_job('p', 'id', 'render', {'revision': 1})
    assert first == second and not created
    with pytest.raises(Conflict): s.create_job('p', 'id', 'render', {'revision': 2})
