import pytest


def test_clear_is_idempotent_and_fences_old_generation(tmp_path):
    from novelvideo.chat.session_store import SessionStore, SessionConflict
    store = SessionStore(tmp_path / 'chat.db')
    old = store.get_or_create()
    assert SessionStore(tmp_path / 'chat.db').get_or_create() == old
    new = store.clear(expected_generation=old.generation, request_id='clear-1')
    assert store.clear(expected_generation=old.generation, request_id='clear-1') == new
    assert old.session_id != new.session_id
    with pytest.raises(SessionConflict):
        store.require_current(old.session_id, old.generation)


def test_backend_change_requires_clear(tmp_path):
    from novelvideo.chat.session_store import SessionStore, SessionConflict
    store = SessionStore(tmp_path / 'chat.db')
    state = store.get_or_create()
    store.bind_provider(state.session_id, state.generation, 'codex', 'provider-1', 'native')
    with pytest.raises(SessionConflict):
        store.bind_provider(state.session_id, state.generation, 'hermes', None, 'native')
    assert store.get_or_create().provider_session_id == 'provider-1'


def test_distinct_databases_isolate_scopes(tmp_path):
    from novelvideo.chat.session_store import SessionStore
    assert SessionStore(tmp_path/'a.db').get_or_create().session_id != SessionStore(tmp_path/'b.db').get_or_create().session_id
