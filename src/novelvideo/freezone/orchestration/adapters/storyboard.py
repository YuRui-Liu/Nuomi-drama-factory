"""Translate saved narrative panels and actual upstream media, never plot prose."""
from novelvideo.narrative_groups.storyboard_prompt import compile_storyboard
from ..models import OrchestrationError


def compile_canvas_storyboard(data, upstream):
    panels = []
    for frame in data.get('frames', []):
        if not frame.get('static_panel'):
            raise OrchestrationError('static_contract_required',
                'static panel required; complete the selected instant before generation', frame_id=frame.get('id'))
        if frame.get('static_source_description') != frame.get('description', ''):
            raise OrchestrationError('static_edit_conflict',
                'director edit requires updating the static panel before generation', frame_id=frame.get('id'))
        panel = frame['static_panel']
        if panel.get('shot_id') != frame.get('id'):
            raise OrchestrationError('static_shot_mismatch', 'static panel shot_id differs from frame ID')
        panels.append(panel)
    actual = {item['node_id']: item.get('result') or {} for item in upstream}
    bindings = data.get('referenceBindings', [])
    by_id = {item['node_id']: item for item in bindings}
    if len(by_id) != len(bindings):
        raise OrchestrationError('reference_ambiguous', 'duplicate reference binding')
    # upstream has already been sorted by the actual saved reference order.
    refs = []
    for item in upstream:
        identifier = item['node_id']
        if identifier not in by_id:
            continue
        binding, result = by_id[identifier], actual[identifier]
        url = result.get('imageUrl')
        digest = (result.get('asset_sha256') or result.get('assetSha256')
                  or next((a.get('sha256') for a in item.get('artifacts', []) if a.get('kind') == 'image'), None))
        version = ':'.join(str(v) for v in (result.get('assetVersionId'), digest) if v)
        if not url or not version:
            raise OrchestrationError('reference_not_ready',
                'reference requires an actual image and frozen version/hash', node_id=identifier)
        refs.append({key: binding[key] for key in ('entity_id', 'name', 'role', 'panel_ids')}
                    | {'id': identifier, 'url': url, 'version': version,
                       'identity_anchor': binding.get('identity_anchor')})
    if set(by_id) - set(actual):
        raise OrchestrationError('reference_not_connected', 'reference binding has no actual upstream image',
                                 node_ids=sorted(set(by_id) - set(actual)))
    try:
        return compile_storyboard(panels, refs, rows=data.get('gridRows', 2), cols=data.get('gridCols', 2),
            aspect_ratio=data.get('cellAspectRatio') or data.get('aspectRatio'),
            style=data.get('staticStylePrompt', ''))
    except (ValueError, KeyError, TypeError) as exc:
        raise OrchestrationError('static_contract_invalid', str(exc)) from exc
