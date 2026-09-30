from concurrent.futures import ThreadPoolExecutor

import pytest

from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig, ResourceRef, ResourceVersion, TeamVersion
from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict
from novelvideo.agent_teams.resources import content_hash
from novelvideo.text_task_runtime.models import AgentTaskRoute


@pytest.fixture
def store(tmp_path):
    return AgentTeamStore(tmp_path / 'teams.sqlite')


def test_template_immutable_owner_and_copy(store):
    team = TeamVersion(id='t', revision=1, owner='alice', name='Team')
    store.publish_template(team, 0)
    with pytest.raises(RevisionConflict):
        store.publish_template(team, 0)
    with pytest.raises(ValueError, match='owner'):
        store.publish_template(team.model_copy(update={'revision': 2, 'owner': 'bob'}), 1)
    store.publish_template(team.model_copy(update={'revision': 2, 'name': 'New'}), 1)
    assert store.get_template('t', 1).name == 'Team'
    assert store.list_templates('alice')[0].revision == 2
    assert store.list_templates('bob') == []
    store.get_template('t').roles['bad'] = {}
    assert store.get_template('t').roles == {}


def test_resource_hash_history_usage(store):
    resource = ResourceVersion(id='s', revision=1, owner='alice', kind='skill', content='hello', content_hash=content_hash('hello'))
    store.publish_resource(resource, 0)
    with pytest.raises(ValueError, match='owner'):
        store.publish_resource(resource.model_copy(update={'revision': 2, 'owner': 'bob'}), 1)
    with pytest.raises(ValueError, match='hash'):
        store.publish_resource(resource.model_copy(update={'revision': 2, 'content': 'changed'}), 1)
    store.publish_resource(resource.model_copy(update={'revision': 2, 'archived': True}), 1)
    assert not store.get_resource('s', 1).archived
    assert store.list_resources('alice')[0].archived
    store.record_resource_usage('s', 1, 'template', 't', 1)
    store.record_resource_usage('s', 1, 'project', 'p', 2)
    assert len(store.list_resource_usage('s', 1)) == 2


def test_draft_active_cas_and_rollback(store):
    data = {'template_id': 't', 'overrides': {'a': 'b'}}
    draft = store.save_draft('p', data, 0)
    data['overrides']['a'] = 'changed'
    assert store.get_draft('p')['data']['overrides']['a'] == 'b'
    assert store.get_binding('p') is None
    first = store.activate('p', {'content': 'one'}, 0, draft['draft_revision'])
    store.save_draft('p', {}, 1)
    with pytest.raises(RevisionConflict):
        store.activate('p', {}, 1, 1)
    with pytest.raises(RevisionConflict):
        store.activate('p', {}, 0, 2)
    store.activate('p', {'content': 'two'}, 1, 2)
    restored = store.activate('p', first['snapshot'], 2, 2)
    assert restored['active_revision'] == 3
    assert len(store.list_versions('p')) == 3


def test_concurrent_draft_writers(store):
    def write(_):
        try:
            store.save_draft('p', {}, 0)
            return True
        except RevisionConflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(write, range(4))) == 1


def test_concurrent_activation_writers(store):
    store.save_draft('p', {}, 0)
    def activate(_):
        try:
            store.activate('p', {'team': 'copied'}, 0, 1)
            return True
        except RevisionConflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(activate, range(4))) == 1
    binding = store.get_binding('p')
    binding['snapshot']['team'] = 'mutated'
    assert store.get_binding('p')['snapshot']['team'] == 'copied'


def test_payload_bounds_and_json(store):
    for value in [float('nan'), 'x' * (2 * 1024 * 1024)]:
        with pytest.raises(ValueError):
            store.save_draft('p', {'bad': value}, 0)


def test_execution_snapshot_immutable_and_secret_free(store):
    resource = ResourceVersion(id='skill', revision=1, kind='skill', owner='alice', content='exact skill', content_hash=content_hash('exact skill'))
    snapshot = ExecutionSnapshot(id='run', project_id='p', template_id='t', template_revision=1,
        active_revision=1, role_id='r', subtask_id='s', input_revision='1', input_hash='hash',
        resolved_method=MethodConfig(prompt='exact', skills=(ResourceRef(id='skill', revision=1),)),
        resolved_model=AgentTaskRoute(), resource_snapshots=(resource,))
    store.save_snapshot(snapshot)
    assert store.load_snapshot('run') == snapshot
    with pytest.raises(RevisionConflict):
        store.save_snapshot(snapshot)
    assert 'api_key' not in store.load_snapshot('run').model_dump_json()
    reopened = AgentTeamStore(store.path)
    assert reopened.load_snapshot('run').resource_snapshots[0].content == 'exact skill'
    bad = snapshot.model_copy(update={'id': 'bad', 'resource_snapshots': (resource.model_copy(update={'content_hash': 'wrong'}),)})
    with pytest.raises(ValueError, match='hash'):
        store.save_snapshot(bad)
    with pytest.warns(UserWarning, match='serializer'), pytest.raises(ValueError):
        store.save_snapshot(snapshot.model_copy(update={'id': 'secret', 'resolved_model': {'api_key': 'secret'}}))
