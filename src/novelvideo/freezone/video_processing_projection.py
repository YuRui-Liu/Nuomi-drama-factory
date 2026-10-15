"""Durable request identity and canvas-owned processing candidates."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from novelvideo.freezone.canvas_store import read_canvas, save_canvas, utc_now_iso
from novelvideo.freezone.orchestration.models import fingerprint
from novelvideo.chat.canvas_context import node_fingerprint


class ProcessingRequests:
    def __init__(self, root):
        self.path = Path(root) / 'freezone' / 'video_processing_requests.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (owner TEXT, id TEXT, signature TEXT, job_id TEXT, response TEXT, PRIMARY KEY(owner,id))')

    def lookup(self, owner, request_id, signature):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT signature,job_id,response FROM requests WHERE owner=? AND id=?', (owner, request_id)).fetchone()
        if row and row[0] != signature:
            raise ValueError('同一 request_id 不能用于不同处理参数')
        return {'job_id': row[1], 'response': json.loads(row[2]) if row[2] else None} if row else None

    def claim(self, owner, request_id, signature):
        job_id = 'vp-' + fingerprint([owner, request_id])[:28]
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT signature FROM requests WHERE owner=? AND id=?', (owner, request_id)).fetchone()
            if row:
                if row[0] != signature:
                    raise ValueError('同一 request_id 不能用于不同处理参数')
                return None
            db.execute('INSERT INTO requests VALUES (?,?,?,?,NULL)', (owner, request_id, signature, job_id))
        return job_id

    def complete(self, owner, request_id, response):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE requests SET response=? WHERE owner=? AND id=?', (json.dumps(response), owner, request_id))


def prepare_processing_candidate(root, job_id, payload, *, media_root=None):
    from novelvideo.freezone.paths import resolve_static_url_to_path
    result_id = 'processing-' + job_id
    media_root = Path(media_root) if media_root is not None else Path(root)
    signature = payload.get('request_signature') or fingerprint(payload)
    # A crash between candidate creation and request claim must keep the first
    # frozen model selection even if project settings changed before retry.
    saved = read_canvas(Path(root), payload['canvas_id'])
    prior = next((n for n in (saved or {}).get('nodes', []) if n['id'] == result_id), None)
    if prior is not None:
        origin = prior.get('data', {}).get('processingOrigin') or {}
        if origin.get('request_signature') != signature:
            raise ValueError('候选请求身份冲突')
        if origin.get('model_config_snapshot') is not None:
            payload['model_config_snapshot'] = deepcopy(origin['model_config_snapshot'])
    def build(existing):
        if existing is None:
            raise ValueError('请先保存来源画布')
        canvas = deepcopy(existing)
        prior = next((n for n in canvas['nodes'] if n['id'] == result_id), None)
        if prior is not None:
            if (prior.get('data', {}).get('processingOrigin') or {}).get('request_signature') != signature:
                raise ValueError('候选请求身份冲突')
            if prior['data']['processingOrigin'].get('model_config_snapshot') is not None:
                payload['model_config_snapshot'] = deepcopy(prior['data']['processingOrigin']['model_config_snapshot'])
            return canvas
        source = next((n for n in canvas['nodes'] if n['id'] == payload['node_id']), None)
        if source is None:
            raise ValueError('来源节点不存在于所选画布')
        urls = [source.get('data', {}).get(field) for field in ('videoUrl', 'resultVideoUrl')]
        expected = resolve_static_url_to_path(payload['source_url'], media_root)
        if not any(url and resolve_static_url_to_path(url, media_root) == expected for url in urls):
            raise ValueError('来源节点的视频已变化，请重新选择')
        source_version = node_fingerprint(source)
        canvas['nodes'].append({'id': result_id, 'type': 'videoNode',
            'position': {'x': source.get('position', {}).get('x', 0) + 480, 'y': source.get('position', {}).get('y', 0)},
            'data': {'displayName': '视频处理候选', 'isGenerating': True,
                     'generationTaskKey': payload.get('task_key'), 'generationTaskType': 'freezone_video_upscale',
                     'generationTaskJobId': job_id,
                     'processingOrigin': {'node_id': payload['node_id'], 'canvas_id': payload['canvas_id'],
                                          'fingerprint': source_version, 'job_id': job_id, 'request_signature': signature,
                                          'model_config_snapshot': deepcopy(payload.get('model_config_snapshot'))},
                     'orchestration': {'status': 'pending', 'attempt_id': job_id}}})
        canvas['edges'].append({'id': 'origin-' + result_id, 'source': payload['node_id'], 'target': result_id,
            'sourceHandle': 'source', 'targetHandle': 'target', 'data': {'contextLink': True}})
        canvas['revision'] += 1
        canvas['updated_at'] = utc_now_iso()
        return canvas
    save_canvas(Path(root), payload['canvas_id'], base_revision=None, enforce_revision=False, build_payload=build,
                client_save_id='processing-create-' + job_id, request_hash=signature, save_source='video-processing')
    # Idempotent saves can bypass build after a concurrent submit won the lock.
    stored = read_canvas(Path(root), payload['canvas_id'])
    candidate = next((n for n in (stored or {}).get('nodes', []) if n['id'] == result_id), None)
    if candidate is not None and candidate['data']['processingOrigin'].get('model_config_snapshot') is not None:
        payload['model_config_snapshot'] = deepcopy(candidate['data']['processingOrigin'].get('model_config_snapshot'))
    return result_id


def project_processing_result(project_dir, job_id, *, payload, output_url, meta):
    # This field is derived from the authorized ProjectContext at submission;
    # it is deliberately absent from the public request schema.
    root = Path(payload.get('canvas_state_root') or project_dir)
    result_id = 'processing-' + job_id
    def build(existing):
        if existing is None:
            raise ValueError('结果保留在任务中心，原画布已删除')
        canvas = deepcopy(existing)
        node = next((n for n in canvas['nodes'] if n['id'] == result_id), None)
        if node is None:
            raise ValueError('结果保留在任务中心，候选节点已删除')
        origin = node.get('data', {}).get('processingOrigin') or {}
        if origin.get('job_id') != job_id:
            raise ValueError('候选身份已变化，结果保留在任务中心')
        source = next((n for n in canvas['nodes'] if n['id'] == payload['node_id']), None)
        changed = source is None or node_fingerprint(source) != origin.get('fingerprint')
        # Never overwrite a candidate the user has already replaced with another video.
        if node['data'].get('videoUrl') and node['data']['videoUrl'] != output_url:
            raise ValueError('候选已被替换，结果保留在任务中心')
        node['data'].update(videoUrl=output_url, isGenerating=False, processingMeta=deepcopy(meta),
            orchestration={'status': 'source_changed' if changed else 'succeeded', 'attempt_id': job_id})
        if changed:
            node['data']['displayName'] = '视频处理候选 · 来源已变化'
        ids = {n['id'] for n in canvas['nodes']}
        canvas['edges'] = [e for e in canvas['edges'] if not ((e.get('data') or {}).get('contextLink') and (e['source'] not in ids or e['target'] not in ids))]
        canvas['revision'] += 1
        canvas['updated_at'] = utc_now_iso()
        return canvas
    try:
        save_canvas(root, payload['canvas_id'], base_revision=None, enforce_revision=False, build_payload=build,
                    client_save_id='processing-result-' + job_id,
                    request_hash=fingerprint([job_id, output_url]), save_source='video-processing')
        return {'canvas_id': payload['canvas_id'], 'node_id': result_id, 'status': 'placed'}
    except ValueError as exc:
        # The paid artifact remains a successful task result even if its canvas was removed.
        return {'canvas_id': payload['canvas_id'], 'node_id': result_id, 'status': 'source_changed', 'message': str(exc)}
