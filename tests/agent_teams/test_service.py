import pytest
from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict
from novelvideo.agent_teams.service import AgentTeamService, TeamError


def test_draft_inheritance_activation_and_isolation(tmp_path):
    library = AgentTeamStore(tmp_path / 'library.db')
    service = AgentTeamService(AgentTeamStore(tmp_path / 'project.db'), library, 'alice', lambda: {('writer', 'brief')})
    draft = service.save_draft('one', {'overrides': {'writer': {'brief': {'prompt': 'custom'}}}}, 0)
    assert service.read('one')['active'] is None
    assert service.read('one')['effective']['writer']['brief']['origins']['prompt'] == 'project'
    assert service.read('two')['effective']['writer']['brief']['config']['prompt'] == ''
    with pytest.raises(RevisionConflict):
        service.save_draft('one', {'overrides': {}}, 0)
    service.activate('one', draft['draft_revision'], 0)
    assert service.read('one')['active']['active_revision'] == 1


def test_stale_activation_and_rollback_freeze(tmp_path):
    from novelvideo.text_task_runtime.models import AgentTaskRoute
    service = AgentTeamService(AgentTeamStore(tmp_path / 'project.db'), None, 'alice', lambda: {('writer', 'brief')})
    service.save_draft('one', {'overrides': {'writer': {'brief': {'prompt': 'first'}}}}, 0)
    service.activate('one', 1, 0)
    route = AgentTaskRoute(model='test')
    frozen = service.freeze('one', 'writer', 'brief', '1', 'hash', route)
    service.save_draft('one', {'overrides': {'writer': {'brief': {'prompt': 'second'}}}}, 1)
    with pytest.raises(RevisionConflict):
        service.activate('one', 1, 1)
    service.activate('one', 2, 1)
    service.rollback('one', 1, 2, 2)
    assert service.freeze('one', 'writer', 'brief', '1', 'hash', route).resolved_method.prompt == 'first'
    assert service.store.load_snapshot(frozen.id) == frozen


def test_collaborator_upgrade_keeps_resource_and_copy_imports_it(tmp_path):
    store = AgentTeamStore(tmp_path / 'project.db')
    alice = AgentTeamService(store, AgentTeamStore(tmp_path / 'alice.db'), 'alice')
    resource = alice.publish_resource({'id': 'skill', 'revision': 1, 'kind': 'skill', 'content': 'hello'}, 0)
    alice.save_draft('one', {'overrides': {'writer': {'brief': {'skills': [{'id': 'skill', 'revision': 1}]}}}}, 0)
    bob = AgentTeamService(store, AgentTeamStore(tmp_path / 'bob.db'), 'bob')
    bob.publish_template({'id': 'new', 'revision': 1, 'name': 'new'}, 0)
    bob.upgrade('one', 'new', 1, 1)
    assert bob._current('one').resources == (resource,)
    copied = bob.copy_template('', 1, 'copy', 'Copy', project='one')
    ref = copied.roles['writer']['brief'].skills[0]
    assert ref.id != resource.id
    assert bob.library.get_resource(ref.id).content == 'hello'
    assert bob.library.get_resource(ref.id).owner == 'bob'


def test_disconnected_rejected(tmp_path):
    service = AgentTeamService(AgentTeamStore(tmp_path / 'project.db'), AgentTeamStore(tmp_path / 'library.db'), 'alice')
    service.save_draft('one', {'overrides': {'writer': {'brief': {'prompt': 'custom'}}}}, 0)
    with pytest.raises(TeamError, match='ROLE_NOT_CONNECTED'):
        service.activate('one', 1, 0)


def test_publish_uses_authenticated_owner_and_upgrade_preserves_override(tmp_path):
    service = AgentTeamService(AgentTeamStore(tmp_path / 'project.db'), AgentTeamStore(tmp_path / 'library.db'), 'alice')
    template = service.publish_template({'id': 'mine', 'revision': 1, 'name': 'Mine', 'owner': 'mallory', 'roles': {}}, 0)
    assert template.owner == 'alice'
    service.save_draft('one', {'overrides': {'writer': {'brief': {'prompt': 'hello'}}}}, 0)
    service.upgrade('one', 'mine', 1, 1)
    assert service.read('one')['effective']['writer']['brief']['config']['prompt'] == 'hello'
    assert service.read('one')['active'] is None
