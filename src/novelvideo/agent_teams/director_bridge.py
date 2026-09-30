"""One director preference draft shared by the team and director editors."""
from copy import deepcopy

from .store import RevisionConflict

PREFERENCE_FIELDS = ('pace', 'camera_motion', 'composition', 'performance', 'method')


def import_director_method(service, project, document, expected_document_revision, expected_draft_revision):
    if document['revision'] != expected_document_revision:
        raise RevisionConflict('导演配置已有新版本，请重新加载')
    from novelvideo.creative_studios.director import director_snapshot
    preferences = director_snapshot(document['data'], document['id'], document['revision'])['preferences']
    current = service.store.get_draft(project)
    data = deepcopy(current['data']) if current else {}
    data = {key: data[key] for key in ('template_id', 'template_revision', 'overrides') if key in data}
    method = data.setdefault('overrides', {}).setdefault('director', {}).setdefault('director_plan', {})
    method['director_preferences'] = {key: preferences[key] for key in PREFERENCE_FIELDS}
    return service.save_draft(project, data, expected_draft_revision)
