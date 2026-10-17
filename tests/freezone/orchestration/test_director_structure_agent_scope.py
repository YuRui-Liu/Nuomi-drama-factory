from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.adapters import professional_edits as edits
from novelvideo.freezone.orchestration.models import OrchestrationError


def test_structural_command_cannot_use_a_different_revision():
    with pytest.raises(OrchestrationError, match='bound director revision'):
        edits._normalize_inputs('edit_structure', 'rev', {
            'operation': 'edit_structure', 'revisionId': 'other', 'groupIds': ['g'],
            'command': {'kind': 'delete_shot', 'shot_id': 's'},
        })


def test_structural_scope_rejects_undeclared_group_and_extra_group(monkeypatch):
    revision = SimpleNamespace(episode=1, groups=[
        SimpleNamespace(id='g1', shots=[SimpleNamespace(id='s1')]),
        SimpleNamespace(id='g2', shots=[SimpleNamespace(id='s2')]),
    ])
    monkeypatch.setattr(edits, 'DirectorPlanStore', lambda *args: object())
    monkeypatch.setattr(edits, '_load_revision', lambda *args: revision)
    context = {'episode': 1, 'operation': 'edit_structure', 'revision_id': 'rev',
               'scope': {'group_ids': ['g1']}, 'inputs': {'command': {'kind': 'delete_shot', 'shot_id': 's2'}}}
    with pytest.raises(OrchestrationError, match='exact selected group'):
        edits._validate_scope(SimpleNamespace(output_dir='unused'), context)
    context['scope']['group_ids'] = ['g1', 'g2']
    with pytest.raises(OrchestrationError, match='exact selected group'):
        edits._validate_scope(SimpleNamespace(output_dir='unused'), context)
    context['scope']['group_ids'] = ['g2']
    assert edits._validate_scope(SimpleNamespace(output_dir='unused'), context) is revision


def test_structural_command_rejects_unregistered_edit_kind():
    with pytest.raises(OrchestrationError, match='Unsupported structural'):
        edits._normalize_inputs('edit_structure', 'rev', {
            'operation': 'edit_structure', 'revisionId': 'rev', 'groupIds': ['g'],
            'command': {'kind': 'update_shots', 'updates': []},
        })
