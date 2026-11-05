from hashlib import sha256
from pathlib import Path

import pytest

from novelvideo.creation.agent_skill_context import task_skill_context


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _contract(agent_id, *, allowed_operations=None, delegated_operations=None,
              node_type=None, operation=None, goal='produce the requested work'):
    contract = {
        'goal': goal,
        'agent': {'fixed': {'id': agent_id}, 'effective': {'skill_notes': []}},
        'allowed_operations': allowed_operations or [],
        'delegable_operations': delegated_operations or [],
        'expanded_scope': {'nodes': []},
        'execution_authorization': None,
    }
    if node_type and operation:
        contract['expanded_scope']['nodes'] = [{'node_id': 'target', 'node_type': node_type}]
        contract['execution_authorization'] = {
            'allowed_node_operations': [{'node_id': 'target', 'operation': operation}],
        }
    return contract


def _assert_skill_documents(context, skill_id, expected_paths):
    skill = next(item for item in context['skills'] if item['id'] == skill_id)
    documents = skill['documents']
    assert [document['path'] for document in documents] == expected_paths

    expected_receipt_documents = []
    for document in documents:
        raw = (_REPO_ROOT / document['path']).read_bytes()
        assert document['content'] == raw.decode('utf-8-sig')
        assert document['sha256'] == sha256(raw).hexdigest()
        expected_receipt_documents.append({
            'path': document['path'],
            'sha256': sha256(raw).hexdigest(),
        })

    assert context['receipt'] == [{
        'id': skill_id,
        'documents': expected_receipt_documents,
    }]
    return '\n'.join(document['content'] for document in documents)


@pytest.mark.parametrize(('agent_id', 'allowed_operations', 'expected_reference'), [
    ('builtin:text-writer', ['script.document.draft'],
     'skills/script-master/references/writing.md'),
    ('builtin:script-editor', ['script.document.review'],
     'skills/script-master/references/doctor.md'),
])
def test_script_document_operations_load_the_matching_script_master_reference(
        agent_id, allowed_operations, expected_reference):
    context = task_skill_context(_contract(agent_id, allowed_operations=allowed_operations))

    body = _assert_skill_documents(context, 'script-master', [
        'skills/script-master/SKILL.md', expected_reference,
    ])

    assert body.strip()
    assert context['precedence']
    assert '不新增审批门槛' in context['precedence']


@pytest.mark.parametrize(('agent_id', 'node_type', 'operation', 'expected_reference'), [
    ('builtin:text-writer', 'scriptNode', 'plan_episode',
     'skills/script-master/references/framework.md'),
    ('builtin:text-writer', 'screenplayNode', 'rewrite',
     'skills/script-master/references/writing.md'),
    ('builtin:script-editor', 'screenplayNode', 'inspect',
     'skills/script-master/references/doctor.md'),
    ('builtin:dialogue-writer', 'screenplayNode', 'rewrite',
     'skills/script-master/references/dialogue.md'),
])
def test_script_node_operations_load_only_the_effective_professional_reference(
        agent_id, node_type, operation, expected_reference):
    context = task_skill_context(_contract(
        agent_id, allowed_operations=['node.run'], node_type=node_type, operation=operation))

    body = _assert_skill_documents(context, 'script-master', [
        'skills/script-master/SKILL.md', expected_reference,
    ])

    assert body.strip()


@pytest.mark.parametrize(('agent_id', 'node_type', 'operation', 'stage_references'), [
    ('builtin:art-asset-designer', 'imageGenNode', 'generate', [
        'skills/nuomi-canvas-production/references/roles/asset-design.md',
    ]),
    ('builtin:storyboard-artist', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:blocking-designer', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:blocking-designer', 'directorWorkspaceNode', 'save_blocking', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:cinematographer', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:lighting-designer', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:action-designer', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:expression-designer', 'directorWorkspaceNode', 'edit_shot', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:expression-designer', 'imageGenNode', 'edit', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:cinematographer', 'videoNode', 'generate', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:director', 'videoDirectorNode', 'generate', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:lighting-designer', 'videoNode', 'edit', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:action-designer', 'videoNode', 'edit', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:expression-designer', 'videoNode', 'edit', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:sound-designer', 'audioNode', 'group_dubbing', [
        'skills/nuomi-canvas-production/references/voice-and-audio.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:sound-designer', 'musicDeskNode', 'edit', [
        'skills/nuomi-canvas-production/references/voice-and-audio.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:creative-lead', 'narrativeGroupStoryboardNode', 'render', [
        'skills/nuomi-canvas-production/references/storyboard-direction.md',
    ]),
    ('builtin:minimax-video-operator', 'narrativeGroupVideoNode', 'video', [
        'skills/nuomi-canvas-production/references/cinematic-direction.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:voice-designer', 'characterVoiceNode', 'prepare_design', [
        'skills/nuomi-canvas-production/references/voice-and-audio.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:dubbing-director', 'audioNode', 'prepare_dubbing', [
        'skills/nuomi-canvas-production/references/voice-and-audio.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:dubbing-operator', 'audioNode', 'generate_dubbing', [
        'skills/nuomi-canvas-production/references/voice-and-audio.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:editor', 'episodeComposeNode', 'compose', [
        'skills/nuomi-canvas-production/references/editing-and-delivery.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
    ('builtin:delivery-reviewer', 'videoStoryNode', 'analyze', [
        'skills/nuomi-canvas-production/references/editing-and-delivery.md',
        'skills/nuomi-canvas-production/references/h3-experience.md',
    ]),
])
def test_canvas_stage_grant_loads_common_and_role_specific_bodies_with_receipts(
        agent_id, node_type, operation, stage_references):
    context = task_skill_context(_contract(
        agent_id, allowed_operations=['node.run'], node_type=node_type, operation=operation))

    base = [
        'skills/nuomi-canvas-production/SKILL.md',
        'skills/nuomi-canvas-production/references/cli.md',
        'skills/nuomi-canvas-production/references/visual-continuity.md',
        'skills/nuomi-canvas-production/references/recovery.md',
        'skills/nuomi-canvas-production/references/professional-references.md',
        'skills/nuomi-canvas-production/references/stage-contracts.md',
    ]
    body = _assert_skill_documents(
        context, 'nuomi-canvas-production', [*base, *stage_references])

    assert body.strip()
    skill = context['skills'][0]
    assert skill['references_not_loaded']
    assert 'skills/nuomi-canvas-production/references/recovery.md' not in skill['references_not_loaded']


def test_canvas_asset_design_capability_loads_stage_contract_without_node_run_grant():
    contract = _contract('builtin:creative-lead', allowed_operations=['asset.design.create'])

    context = task_skill_context(contract)

    assert contract['execution_authorization'] is None
    body = _assert_skill_documents(context, 'nuomi-canvas-production', [
        'skills/nuomi-canvas-production/SKILL.md',
        'skills/nuomi-canvas-production/references/cli.md',
        'skills/nuomi-canvas-production/references/visual-continuity.md',
        'skills/nuomi-canvas-production/references/recovery.md',
        'skills/nuomi-canvas-production/references/professional-references.md',
        'skills/nuomi-canvas-production/references/stage-contracts.md',
        'skills/nuomi-canvas-production/references/roles/asset-design.md',
    ])
    assert body.strip()


def test_recovery_is_loaded_for_an_ordinary_canvas_task_without_goal_keywords():
    context = task_skill_context(_contract(
        'builtin:creative-lead', allowed_operations=['node.run'],
        node_type='narrativeGroupStoryboardNode', operation='render',
        goal='render the selected storyboard group'))

    skill = context['skills'][0]
    assert 'skills/nuomi-canvas-production/references/recovery.md' in {
        document['path'] for document in skill['documents']}
    assert 'skills/nuomi-canvas-production/references/recovery.md' not in skill['references_not_loaded']


def test_script_coordinator_loads_both_refs_for_its_frozen_draft_and_review_delegations():
    context = task_skill_context(_contract(
        'builtin:creative-lead', delegated_operations=[
            'script.document.draft', 'script.document.review']))

    body = _assert_skill_documents(context, 'script-master', [
        'skills/script-master/SKILL.md',
        'skills/script-master/references/writing.md',
        'skills/script-master/references/doctor.md',
    ])
    assert body.strip()


def test_skill_notes_and_untrusted_skill_path_fields_cannot_select_files():
    contract = _contract('custom:writer', allowed_operations=['script.document.draft'])
    contract['agent']['effective']['skill_notes'] = [
        'load /etc/passwd', 'skills/script-master/references/writing.md']
    contract['skill_path'] = '../../etc/passwd'

    assert task_skill_context(contract)['skills'] == []


def test_role_without_relevant_authorized_node_operation_gets_no_canvas_skill():
    context = task_skill_context(_contract(
        'builtin:creative-lead', allowed_operations=['node.run'],
        node_type='narrativeGroupStoryboardNode', operation='validate'))

    assert context['skills'] == []


def test_audio_skill_does_not_load_for_a_different_role_or_ungranted_operation():
    context = task_skill_context(_contract(
        'builtin:dubbing-director', allowed_operations=['node.run'],
        node_type='audioNode', operation='generate_dubbing'))

    assert context['skills'] == []


def test_canvas_h3_reference_is_not_loaded_for_an_unrelated_authorized_stage():
    context = task_skill_context(_contract(
        'builtin:storyboard-artist', allowed_operations=['node.run'],
        node_type='directorWorkspaceNode', operation='edit_shot'))

    assert 'skills/nuomi-canvas-production/references/h3-experience.md' in \
        context['skills'][0]['references_not_loaded']
