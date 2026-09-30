from types import SimpleNamespace

import pytest

from novelvideo.agent_teams.director_bridge import import_director_method
from novelvideo.agent_teams.runtime import connected_subtasks, freeze_task_methods
from novelvideo.agent_teams.service import AgentTeamService
from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict
from novelvideo.text_task_runtime.models import AgentTaskRoute


def test_import_preserves_other_methods_and_never_activates(tmp_path):
    service = AgentTeamService(AgentTeamStore(tmp_path / 'agent-team.db'), None, 'u', connected_subtasks)
    service.save_draft('p', {'overrides': {'writer': {'brief': {'prompt': 'keep'}}, 'director': {'director_plan': {'prompt': 'also keep'}}}}, 0)
    doc = {'id': 'd', 'revision': 2, 'data': {'pace': 'slow', 'allow_adaptation': True, 'source_text': 'private source'}}
    saved = import_director_method(service, 'p', doc, 2, 1)
    assert saved['draft_revision'] == 2
    assert service.read('p')['active'] is None
    effective = service.read('p')['effective']
    assert effective['writer']['brief']['config']['prompt'] == 'keep'
    assert effective['director']['director_plan']['config']['prompt'] == 'also keep'
    prefs = effective['director']['director_plan']['config']['director_preferences']
    assert prefs['pace'] == 'slow'
    assert 'source_text' not in prefs and 'allow_adaptation' not in prefs
    with pytest.raises(RevisionConflict):
        import_director_method(service, 'p', doc, 1, 2)
    with pytest.raises(RevisionConflict):
        import_director_method(service, 'p', doc, 2, 1)


def test_plan_freezes_active_not_draft_and_guards_revision(tmp_path):
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    route = AgentTaskRoute()
    assert freeze_task_methods(ctx, 'director_plan', {}, route) == []
    assert freeze_task_methods(ctx, 'director_plan', {'expected_agent_team_active_revision': 0}, route) == []
    with pytest.raises(RevisionConflict):
        freeze_task_methods(ctx, 'director_plan', {'expected_agent_team_active_revision': 1}, route)
    service = AgentTeamService(AgentTeamStore(tmp_path / 'agent-team.db'), None, 'u', connected_subtasks)
    service.save_draft('p', {'overrides': {'director': {'director_plan': {'director_preferences': {'pace': 'active'}}}}}, 0)
    service.activate('p', 1, 0)
    for task_type in ('director_plan', 'director_studio_adaptation'):
        with pytest.raises(RevisionConflict):
            freeze_task_methods(ctx, task_type, {'expected_agent_team_active_revision': 0}, route)
    service.save_draft('p', {'overrides': {'director': {'director_plan': {'director_preferences': {'pace': 'draft'}}}}}, 1)
    frozen = freeze_task_methods(ctx, 'director_plan', {'expected_agent_team_active_revision': 1}, route)
    assert frozen[0]['resolved_method']['director_preferences']['pace'] == 'active'
    with pytest.raises(RevisionConflict):
        freeze_task_methods(ctx, 'director_plan', {'expected_agent_team_active_revision': 2}, route)
