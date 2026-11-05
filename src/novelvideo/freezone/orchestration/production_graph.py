"""Materialize version-bound group production as ordinary canvas nodes.

This module only changes the graph. Submission, recovery and concurrency belong
to the shared canvas scheduler and the installed node adapters.
"""
from copy import deepcopy
from pathlib import Path
import re

from novelvideo.freezone.canvas_store import read_canvas, save_canvas, utc_now_iso
from .graph import validate_graph
from .models import OrchestrationError, fingerprint

_SAFE_BINDING_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


def build_production_graph(episode: int, revision_id: str, group_ids: list[str]) -> dict:
    if type(episode) is not int or episode < 1:
        raise OrchestrationError('invalid_input', 'A positive episode is required')
    if not isinstance(revision_id, str) or not _SAFE_BINDING_ID.fullmatch(revision_id):
        raise OrchestrationError('invalid_input', 'A fixed director revision is required')
    if (not isinstance(group_ids, list) or not 1 <= len(group_ids) <= 50
            or any(not isinstance(g, str) or not _SAFE_BINDING_ID.fullmatch(g) for g in group_ids)
            or len(set(group_ids)) != len(group_ids)):
        raise OrchestrationError('invalid_input', 'Unique safe narrative groups are required')
    scope = fingerprint([episode, revision_id])[:16]
    target = f'production-{scope}-compose'
    nodes, edges = [], []
    for index, group_id in enumerate(group_ids):
        group_key = fingerprint(group_id)[:12]
        render_id = f'production-{scope}-{group_key}-render'
        video_id = f'production-{scope}-{group_key}-video'
        binding = {'episodeNumber': episode, 'directorRevisionId': revision_id, 'groupId': group_id}
        for node_id, node_type, operation, x, title in (
            (render_id, 'narrativeGroupStoryboardNode', 'render', 0, '整组分镜'),
            (video_id, 'narrativeGroupVideoNode', 'video', 460, '整组视频'),
        ):
            nodes.append({'id': node_id, 'type': node_type,
                'position': {'x': x, 'y': index * 300},
                'data': {**binding, 'operation': operation, 'displayName': f'{title} · {group_id}'}})
        for source, destination in ((render_id, video_id), (video_id, target)):
            edges.append({'id': f'{source}-to-{destination}', 'source': source, 'target': destination,
                          'data': {'sourcePort': 'json', 'targetPort': 'json'}})
    nodes.append({'id': target, 'type': 'episodeComposeNode',
        'position': {'x': 920, 'y': max(0, (len(group_ids) - 1) * 150)},
        'data': {'episodeNumber': episode, 'directorRevisionId': revision_id,
                 'productionGroupIds': list(group_ids), 'operation': 'compose',
                 'displayName': f'E{episode:02d} 整集成片'}})
    return {'nodes': nodes, 'edges': edges, 'target': target}


def materialize_production_graph(project_dir: Path, canvas_id: str, *, base_revision: int,
                                 request_id: str, episode: int, revision_id: str,
                                 group_ids: list[str]) -> dict:
    graph = build_production_graph(episode, revision_id, group_ids)
    if not request_id.strip():
        raise OrchestrationError('request_id_required', 'A stable request ID is required')

    def build(existing):
        if existing is None:
            raise OrchestrationError('canvas_not_found', 'Canvas does not exist')
        payload = deepcopy(existing)
        by_id = {n['id']: n for n in payload['nodes']}
        offset = max((n.get('position', {}).get('x', 0) + 500 for n in payload['nodes']), default=0)
        for node in graph['nodes']:
            prior = by_id.get(node['id'])
            if prior is not None:
                keys = ('episodeNumber', 'directorRevisionId', 'groupId', 'productionGroupIds')
                if prior['type'] != node['type'] or any(prior.get('data', {}).get(k) != node['data'].get(k) for k in keys):
                    raise OrchestrationError('input_conflict', 'Existing production node binding changed', node_id=node['id'])
                continue
            added = deepcopy(node)
            added['position']['x'] += offset
            payload['nodes'].append(added)
        by_edge = {e['id']: e for e in payload['edges']}
        for edge in graph['edges']:
            prior = by_edge.get(edge['id'])
            if prior is not None and (
                any(prior.get(key) != edge[key] for key in ('source', 'target'))
                or any((prior.get('data') or {}).get(key) != edge['data'][key]
                       for key in ('sourcePort', 'targetPort'))
                or (prior.get('data') or {}).get('contextLink')
            ):
                raise OrchestrationError('input_conflict', 'Existing production edge changed', edge_id=edge['id'])
            if prior is None:
                payload['edges'].append(deepcopy(edge))
        report = validate_graph(payload['nodes'], payload['edges'])
        if not report['valid']:
            raise OrchestrationError('invalid_graph', 'Production graph is invalid', errors=report['errors'])
        payload['revision'] += 1
        payload['updated_at'] = utc_now_iso()
        payload['save_source'] = 'orchestration'
        return payload

    save_canvas(project_dir, canvas_id, base_revision=base_revision, build_payload=build,
                client_save_id=request_id, request_hash=fingerprint({'production_graph': graph}),
                save_source='orchestration')
    return {'canvas': read_canvas(project_dir, canvas_id), 'target': graph['target'],
            'node_ids': [n['id'] for n in graph['nodes']], 'submitted': False}
