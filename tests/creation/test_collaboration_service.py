from types import SimpleNamespace

import pytest

from novelvideo.creation.collaboration import Collaboration
from novelvideo.creation.collaboration_store import CollaborationStore
from novelvideo.creation.operations import OperationError
from .test_delegations import ctx, ready, service


def _service(ctx, ready, *, conversation_id=None, canvas_id=None):
    return Collaboration(
        ctx, username='alice', canvas_id=canvas_id or ready[0],
        conversation_id=conversation_id or ready[1],
    )


def test_empty_collaboration_read_does_not_create_database(ctx):
    store = CollaborationStore(ctx.state_dir / 'creation' / 'objects.sqlite3')
    assert store.members('alice', 'p', 'canvas', 'conversation') == []
    assert not store.db_path.exists()

    service = Collaboration(ctx, username='alice', canvas_id='canvas', conversation_id='conversation')

    with pytest.raises(OperationError):
        service.group()
    assert not service.store.db_path.exists()


async def test_member_revision_and_profile_are_frozen_per_conversation(ready):
    ctx = ready[2].ctx
    agents, adopted = ready[2:]
    service = _service(ctx, ready)

    joined = service.join(adopted['adoption_id'], 1)
    assert joined['adoption_revision'] == 1
    assert joined['profile']['methods'] == '先核对事实'
    assert service.join(adopted['adoption_id'], 1) == joined

    changes = {'methods': '后续版本的新方法'}
    preview = agents.override_preview(adopted['adoption_id'], changes)
    await agents.apply({
        'request_id': 'override-after-member-join',
        'action': 'override',
        'payload': {
            'adoption_id': adopted['adoption_id'], 'expected_revision': 1,
            'changes': changes, 'proof': preview['proof'],
        },
    })

    assert service.group()['members'][0]['profile']['methods'] == '先核对事实'
    assert service.group()['members'][0]['adoption_revision'] == 1

    from novelvideo.chat.conversations import ConversationStore
    other_conversation = ConversationStore('alice', 'project', 'p').create(
        '另一个讨论', canvas_id=ready[0], request_id='another-conversation')
    other = _service(ctx, ready, conversation_id=other_conversation['id'])
    assert other.group()['members'] == []


def test_only_current_members_can_be_resolved_as_named_speakers(ready):
    ctx = ready[2].ctx
    adopted = ready[3]
    service = _service(ctx, ready)

    with pytest.raises(OperationError) as failure:
        service.resolve_speaker(adopted['adoption_id'])
    assert failure.value.code == 'not_group_member'

    service.join(adopted['adoption_id'], 1)
    speaker = service.resolve_speaker(adopted['adoption_id'])
    assert speaker['kind'] == 'member'
    assert speaker['adoption_id'] == adopted['adoption_id']
    assert speaker['adoption_revision'] == 1

    left = service.leave(adopted['adoption_id'])
    assert service.leave(adopted['adoption_id']) == left
    with pytest.raises(OperationError) as removed:
        service.resolve_speaker(adopted['adoption_id'])
    assert removed.value.code == 'not_group_member'


def test_foreign_project_context_cannot_read_another_projects_group(ready):
    ctx = ready[2].ctx
    foreign = SimpleNamespace(**vars(ctx))
    foreign.project_id = 'another-project'
    service = Collaboration(foreign, username='alice', canvas_id=ready[0],
                            conversation_id=ready[1])

    with pytest.raises(OperationError) as failure:
        service.group()
    assert failure.value.code == 'scope_violation'
