from copy import deepcopy

import pytest

from novelvideo.creation.professional_roles import (
    PROFESSIONAL_ROLES,
    SOURCES,
    role_catalog,
    role_configuration,
    validate_roles,
)


def test_roles_are_independent_and_proposal_only():
    roles = role_catalog()
    assert len(roles) == len({role['slug'] for role in roles}) == 34

    lighting = role_configuration('lighting-designer')
    assert set(lighting) == {
        'name', 'responsibilities', 'methods', 'references', 'skill_notes',
        'capabilities', 'defaults', 'studio_refs',
    }
    assert lighting['capabilities'] == ['context.read', 'proposal.create']
    assert any('当前仅提供方案' in note for note in lighting['skill_notes'])
    assert any('不代表采纳' in note for note in lighting['skill_notes'])
    assert any('不证明角色实现或生产效果' in note for note in lighting['skill_notes'])
    assert lighting['references']

    roles[0]['name'] = '外部修改'
    roles[0]['source_ids'].clear()
    lighting['references'].clear()
    lighting['capabilities'].append('media.execute')
    lighting['defaults']['style'] = {'id': 'untrusted'}
    lighting['studio_refs'].append({'id': 'untrusted'})

    fresh_roles = role_catalog()
    fresh_lighting = role_configuration('lighting-designer')
    assert fresh_roles[0]['name'] == '主创'
    assert fresh_roles[0]['source_ids']
    assert fresh_lighting['references']
    assert fresh_lighting['capabilities'] == ['context.read', 'proposal.create']
    assert fresh_lighting['defaults'] == {}
    assert fresh_lighting['studio_refs'] == []


def test_source_status_marks_design_contributions_without_claiming_production():
    for role in role_catalog():
        assert role['source_ids']
        assert set(role['source_ids']) <= SOURCES.keys()
        assert role['evidence_status'] == (
            'design_only' if 'P' in role['source_ids'] else 'documented'
        )
        assert role['evidence_status'] != 'production_verified'


def test_role_source_definitions_are_immutable():
    with pytest.raises(TypeError):
        SOURCES['H'] = 'unreviewed source'
    with pytest.raises(TypeError):
        PROFESSIONAL_ROLES[0]['name'] = '修改全局定义'


def test_unknown_role_rejected():
    with pytest.raises(ValueError, match='unknown professional role'):
        role_configuration('unknown')


def test_validator_rejects_duplicate_role_ids_and_unresolved_sources():
    roles = role_catalog()
    with pytest.raises(ValueError, match='duplicate slug'):
        validate_roles((*roles, roles[0]))

    unresolved = deepcopy(roles)
    unresolved[0]['source_ids'] = ['not-a-reviewed-source']
    with pytest.raises(ValueError, match='unresolved source id'):
        validate_roles(unresolved)


@pytest.mark.parametrize('field', ['name', 'inputs', 'outputs', 'checks', 'handoff'])
def test_validator_rejects_empty_or_placeholder_contract_fields(field):
    roles = role_catalog()
    roles[0][field] = '  TODO  ' if field == 'outputs' else '   '
    with pytest.raises(ValueError, match=f'empty or placeholder {field}'):
        validate_roles(roles)
