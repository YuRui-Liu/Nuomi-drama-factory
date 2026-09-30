"""Project bindings contain copies: runtime never consults a user's library."""
from copy import deepcopy
from uuid import uuid4
from typing import Callable

from pydantic import Field

from .catalog import ROLE_CATALOG
from .models import Contract, MethodConfig, TeamVersion, ResourceVersion, ExecutionSnapshot, ProjectDraft
from .resolver import resolve_fields
from .resources import verify_resource, content_hash
from .store import AgentTeamStore, RevisionConflict


class TeamError(ValueError):
    def __init__(self, code, field='', message=''):
        self.code, self.field = code, field
        super().__init__(message or code)


class DraftData(Contract):
    template_id: str = 'builtin'
    template_revision: int = Field(default=1, ge=1, strict=True)
    overrides: dict = Field(default_factory=dict)


class StoredData(DraftData):
    template: TeamVersion
    resources: tuple[ResourceVersion, ...] = ()


def builtin_template():
    return TeamVersion(id='builtin', revision=1, name='默认团队', owner='builtin',
                       roles={r.id: {s: MethodConfig() for s in r.subtasks} for r in ROLE_CATALOG})


class AgentTeamService:
    def __init__(self, store: AgentTeamStore, library: AgentTeamStore | None, username: str,
                 connected: Callable = lambda: set()):
        self.store, self.library, self.username, self.connected = store, library, username, connected

    def _template(self, id, revision):
        value = builtin_template() if id == 'builtin' and revision == 1 else self.library.get_template(id, revision) if self.library else None
        if value is None:
            raise TeamError('TEMPLATE_NOT_FOUND', 'template_id')
        if value.owner not in ('builtin', self.username):
            raise TeamError('ACCESS_DENIED', 'template_id')
        return value

    def publish_template(self, data, expected_revision):
        template = TeamVersion.model_validate({**data, 'owner': self.username})
        if template.id == 'builtin':
            raise TeamError('RESERVED_ID', 'id')
        stored = StoredData(template_id=template.id, template_revision=template.revision, template=template)
        resources = self._resources(self._effective(stored))
        result = self.library.publish_template(template, expected_revision)
        for resource in resources:
            self.library.record_resource_usage(resource.id, resource.revision, 'template', template.id, template.revision)
        return result

    def publish_resource(self, data, expected_revision):
        resource = ResourceVersion.model_validate({**data, 'owner': self.username,
                                                   'content_hash': content_hash(data.get('content', ''))})
        return self.library.publish_resource(resource, expected_revision)

    def copy_template(self, id, revision, new_id, name, project=None):
        source = self._current(project).template if project else self._template(id, revision)
        if project:
            current = self._current(project)
            source = source.model_copy(update={'roles': {r: {t: MethodConfig.model_validate(e['config']) for t, e in tasks.items()}
                                                       for r, tasks in self._effective(current).items()}})
            remapped = {}
            for resource in current.resources:
                new_resource = self.publish_resource({**resource.model_dump(mode='json'),
                    'id': 'copy-' + uuid4().hex, 'revision': 1, 'archived': False}, 0)
                remapped[(resource.id, resource.revision)] = {'id': new_resource.id, 'revision': 1}
            roles = source.model_dump(mode='json')['roles']
            for tasks in roles.values():
                for method in tasks.values():
                    for field in ('skills', 'references'):
                        method[field] = [remapped[(ref['id'], ref['revision'])] for ref in method[field]]
            source = TeamVersion.model_validate({**source.model_dump(mode='json'), 'roles': roles})
        return self.publish_template({**source.model_dump(mode='json'), 'id': new_id, 'name': name, 'revision': 1}, 0)

    def _effective(self, data):
        template = data.template
        known = {r.id: set(r.subtasks) for r in ROLE_CATALOG}
        for source in (template.roles, data.overrides):
            for role, tasks in source.items():
                if role not in known or not isinstance(tasks, dict) or set(tasks) - known[role]:
                    raise TeamError('UNKNOWN_ROLE_OR_SUBTASK', role)
        ProjectDraft(project_id='validation', template_id=template.id, template_revision=template.revision, overrides=data.overrides)
        result = {}
        for role in ROLE_CATALOG:
            result[role.id] = {}
            for task in role.subtasks:
                base = template.roles.get(role.id, {}).get(task, MethodConfig()).model_dump(mode='json')
                overrides = data.overrides.get(role.id, {}).get(task, {})
                result[role.id][task] = {'config': resolve_fields(base, overrides),
                    'origins': {key: 'project' if key in overrides else 'template' for key in base}}
        return result

    def _resources(self, effective, existing=()):
        found = {(r.id, r.revision): r for r in existing}
        result = {}
        for tasks in effective.values():
            for entry in tasks.values():
                method = MethodConfig.model_validate(entry['config'])
                for kind, refs in (('skill', method.skills), ('reference', method.references)):
                    for ref in refs:
                        key = (ref.id, ref.revision)
                        resource = found.get(key)
                        if resource is None and self.library:
                            resource = self.library.get_resource(*key)
                            latest = self.library.get_resource(ref.id)
                            if latest and latest.archived:
                                raise TeamError('RESOURCE_ARCHIVED', ref.id)
                        if resource is None or resource.kind != kind:
                            raise TeamError('RESOURCE_NOT_FOUND_OR_KIND', ref.id)
                        if resource.archived:
                            raise TeamError('RESOURCE_ARCHIVED', ref.id)
                        verify_resource(resource)
                        result[key] = resource
        return tuple(result.values())

    def _current(self, project):
        draft = self.store.get_draft(project)
        return StoredData.model_validate(draft['data']) if draft else StoredData(template=builtin_template())

    def read(self, project):
        draft = self.store.get_draft(project)
        data = StoredData.model_validate(draft['data']) if draft else StoredData(template=builtin_template())
        connected = self.connected()
        return {'catalog': [{**r.model_dump(mode='json'), 'connected': any((r.id, t) in connected for t in r.subtasks)} for r in ROLE_CATALOG],
                'template': data.template.model_dump(mode='json'), 'draft': draft,
                'active': self.store.get_binding(project), 'effective': self._effective(data),
                'connectivity': {r.id: {t: (r.id, t) in connected for t in r.subtasks} for r in ROLE_CATALOG}}

    def save_draft(self, project, data, expected_revision):
        request = DraftData.model_validate(data)
        old = self._current(project)
        same = (request.template_id, request.template_revision) == (old.template_id, old.template_revision)
        template = old.template if same else self._template(request.template_id, request.template_revision)
        stored = StoredData(**request.model_dump(), template=template)
        if same:
            resources = self._resources(self._effective(stored), old.resources)
        else:
            # Resolve template pins independently: a collaborator's library may
            # use the same identity for different content than a copied override.
            inherited = self._resources(self._effective(StoredData(template=template,
                template_id=template.id, template_revision=template.revision)))
            override_pins = {(ref['id'], ref['revision']) for tasks in request.overrides.values()
                for method in tasks.values() for field in ('skills', 'references')
                for ref in method.get(field, [])}
            copied = tuple(r for r in old.resources if (r.id, r.revision) in override_pins)
            for resource in inherited:
                if any((r.id, r.revision) == (resource.id, resource.revision) and r != resource for r in copied):
                    raise TeamError('RESOURCE_IDENTITY_COLLISION', resource.id)
            resources = self._resources(self._effective(stored), (*inherited, *copied))
        stored = stored.model_copy(update={'resources': resources})
        return self.store.save_draft(project, stored.model_dump(mode='json'), expected_revision)

    def _validate_activation(self, data):
        effective = self._effective(data)
        default = MethodConfig().model_dump(mode='json')
        for role, tasks in effective.items():
            for task, entry in tasks.items():
                if (entry['config'] != default or data.overrides.get(role, {}).get(task)) and (role, task) not in self.connected():
                    raise TeamError('ROLE_NOT_CONNECTED', f'{role}.{task}')
        self._resources(effective, data.resources)
        return effective

    def activate(self, project, draft_revision, expected_active_revision):
        draft = self.store.get_draft(project)
        if draft is None or draft['draft_revision'] != draft_revision:
            raise RevisionConflict('draft revision changed before activation')
        data = StoredData.model_validate(draft['data'])
        self._validate_activation(data)
        binding = self.store.activate(project, data.model_dump(mode='json'), expected_active_revision, draft_revision)
        for resource in data.resources:
            self.store.record_resource_usage(resource.id, resource.revision, 'project', project, binding['active_revision'])
        return binding

    def restore_field(self, project, role_id, subtask_id, field, expected_revision):
        data = self._current(project)
        if field not in MethodConfig.model_fields or role_id not in self._effective(data) or subtask_id not in self._effective(data)[role_id]:
            raise TeamError('UNKNOWN_FIELD', field)
        overrides = deepcopy(data.overrides)
        overrides.get(role_id, {}).get(subtask_id, {}).pop(field, None)
        return self.save_draft(project, {'template_id': data.template_id, 'template_revision': data.template_revision, 'overrides': overrides}, expected_revision)

    def upgrade(self, project, template_id, template_revision, expected_revision):
        data = self._current(project)
        return self.save_draft(project, {'template_id': template_id, 'template_revision': template_revision, 'overrides': data.overrides}, expected_revision)

    def diff(self, project):
        active = self.store.get_binding(project)
        before = self._effective(StoredData.model_validate(active['snapshot'])) if active else self._effective(StoredData(template=builtin_template()))
        after = self._effective(self._current(project))
        return [{'role_id': r, 'subtask_id': t, 'field': f, 'before': before[r][t]['config'][f], 'after': e['config'][f]}
                for r, tasks in after.items() for t, e in tasks.items() for f in e['config'] if before[r][t]['config'][f] != e['config'][f]]

    def rollback(self, project, active_revision, expected_active_revision, draft_revision):
        previous = next((b for b in self.store.list_versions(project) if b['active_revision'] == active_revision), None)
        if previous is None:
            raise TeamError('VERSION_NOT_FOUND', 'active_revision')
        data = StoredData.model_validate(previous['snapshot'])
        self._validate_activation(data)
        binding = self.store.activate(project, data.model_dump(mode='json'), expected_active_revision, draft_revision)
        for resource in data.resources:
            self.store.record_resource_usage(resource.id, resource.revision, 'project', project, binding['active_revision'])
        return binding

    def freeze(self, project_id, role_id, subtask_id, input_revision, input_hash, resolved_route):
        active = self.store.get_binding(project_id)
        if active is None:
            return None
        data = StoredData.model_validate(active['snapshot'])
        effective = self._effective(data)
        if role_id not in effective or subtask_id not in effective[role_id]:
            raise TeamError('UNKNOWN_ROLE_OR_SUBTASK', role_id)
        method = MethodConfig.model_validate(effective[role_id][subtask_id]['config'])
        pins = {(r.id, r.revision) for r in (*method.skills, *method.references)}
        snapshot = ExecutionSnapshot(id=uuid4().hex, project_id=project_id, template_id=data.template_id,
            template_revision=data.template_revision, active_revision=active['active_revision'], role_id=role_id,
            subtask_id=subtask_id, input_revision=str(input_revision), input_hash=input_hash, resolved_method=method,
            resolved_model=resolved_route if method.model == 'project' else method.model,
            resource_snapshots=tuple(r for r in data.resources if (r.id, r.revision) in pins))
        return self.store.save_snapshot(snapshot)
