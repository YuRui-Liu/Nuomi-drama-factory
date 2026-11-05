"""Explicit, conversation-scoped professional identities for discussions."""

from pathlib import Path

from . import agent_context
from .collaboration_store import CollaborationStore
from .delegations import Delegations
from .ontology import require, text, integer
from .operations import OperationError


class Collaboration:
    def __init__(self, ctx, *, username, canvas_id, conversation_id):
        self.ctx = ctx
        self.username = username
        self.canvas_id = text(canvas_id, 128)
        self.conversation_id = text(conversation_id, 64)
        self.owner_id = str(ctx.requester_user_id)
        self.project_id = str(ctx.project_id)
        self.store = CollaborationStore(Path(ctx.state_dir) / 'creation' / 'objects.sqlite3')
        self.delegations = Delegations(ctx, username=username, authority='user')

    def _validate_scope(self):
        agent_context.conversation(self.ctx, self.username, self.canvas_id, self.conversation_id)

    def main(self):
        self._validate_scope()
        with self.delegations.read() as conn:
            binding = self.delegations._main(conn)
            if not binding.get('adoption_id'):
                return None
            adopted = self.delegations._agent(conn, binding['adoption_id'])
        return {
            'kind': 'main',
            'binding_revision': binding['binding_revision'],
            'adoption_id': adopted['adoption_id'],
            'adoption_revision': adopted['revision'],
            'profile': adopted['effective'],
        }

    def group(self):
        self._validate_scope()
        return {
            'main': self.main(),
            'members': self.store.members(self.owner_id, self.project_id, self.canvas_id, self.conversation_id),
        }

    def join(self, adoption_id, adoption_revision):
        self._validate_scope()
        adoption_id = text(adoption_id, 128)
        adoption_revision = integer(adoption_revision, 1)
        with self.delegations.read() as conn:
            adopted = self.delegations._agent(conn, adoption_id, adoption_revision)
        return self.store.join(self.owner_id, self.project_id, self.canvas_id, self.conversation_id,
                               adoption_id, adopted['revision'], adopted['effective'])

    def leave(self, adoption_id):
        self._validate_scope()
        adoption_id = text(adoption_id, 128)
        member = self.store.leave(self.owner_id, self.project_id, self.canvas_id,
                                  self.conversation_id, adoption_id)
        require(member is not None, '此会话中没有该常驻成员', 'not_group_member')
        return member

    def resolve_speaker(self, target_adoption_id=None):
        self._validate_scope()
        if target_adoption_id is not None:
            target_adoption_id = text(target_adoption_id, 128)
            member = self.store.member(self.owner_id, self.project_id, self.canvas_id,
                                       self.conversation_id, target_adoption_id)
            require(member is not None, '点名对象不是当前会话常驻成员', 'not_group_member')
            return {key: member[key] for key in (
                'kind', 'binding_revision', 'adoption_id', 'adoption_revision', 'profile')}

        main = self.main()
        if main is None:
            raise OperationError('missing_main', '请先明确选择项目主创')
        return main

    def resolve_discussion_speaker(self, target_adoption_id=None):
        """Resolve an adopted speaker, with a non-executable built-in fallback for discussion."""
        if target_adoption_id is not None:
            return self.resolve_speaker(target_adoption_id)
        main = self.main()
        if main is not None:
            return main

        from .builtin_agents import catalog

        default = next((item for item in catalog() if item['id'] == 'builtin:creative-lead'), None)
        require(default is not None, '默认创作助手暂不可用', 'source_conflict')
        return {
            'kind': 'default_discussion',
            'binding_revision': 0,
            'adoption_id': None,
            'adoption_revision': None,
            'profile': default['configuration'],
        }
