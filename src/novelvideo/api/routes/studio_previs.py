"""Persist locally rendered previs outputs with their exact scene snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.creative_studios.previs import PrevisScene

router = APIRouter(prefix='/projects/{project}/studios/previs-tools')
logger = logging.getLogger(__name__)


def _folder(scope) -> Path:
    root = Path(scope.project_dir) / 'studios' / 'previs' / 'outputs'
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post('/outputs')
async def upload_output(project: str, file: UploadFile, manifest: str = Form(...), user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='editor')
    if len(manifest) > 2_000_000:
        raise HTTPException(413, '场景清单超过大小限制')
    try:
        meta = json.loads(manifest)
        scene = PrevisScene.model_validate(meta['scene'])
        revision = int(meta.get('revision', 0))
        if revision < 0:
            raise ValueError('无效版本')
        frame_time = meta.get('time')
        if frame_time is not None and (isinstance(frame_time, bool) or not isinstance(frame_time, (int, float)) or not math.isfinite(frame_time) or not 0 <= frame_time <= 3600):
            raise ValueError('参考帧时间必须在 0–3600 秒之间')
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise HTTPException(422, f'场景清单无效：{exc}') from exc
    data = await file.read(100 * 1024 * 1024 + 1)
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, '单个预演输出不能超过 100 MB')
    return await asyncio.to_thread(_verify_and_write, scope, data, scene, revision, frame_time)


def _verify_and_write(scope, data: bytes, scene: PrevisScene, revision: int, frame_time: float | None):
    root = _folder(scope)
    # Same exact media and metadata produce the same ID, so retry is idempotent.
    canonical = json.dumps({'scene': scene.model_dump(mode='json'), 'revision': revision, 'time': frame_time}, sort_keys=True, ensure_ascii=False, allow_nan=False)
    asset_id = hashlib.sha256(data + canonical.encode()).hexdigest()
    extension = '.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else '.webm'
    path = root / f'{asset_id}{extension}'
    temporary = root / f'{uuid.uuid4().hex}.tmp{extension}'
    temporary_record = root / f'{uuid.uuid4().hex}.tmp'
    try:
        if extension == '.png':
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 24_000_000:
                    raise ValueError('参考帧尺寸过大')
                image.verify()
        elif not data.startswith(b'\x1a\x45\xdf\xa3'):
            raise ValueError('仅支持真实 PNG 或 WebM 文件')
        temporary.write_bytes(data)
        if extension == '.webm':
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(temporary)], capture_output=True, text=True, timeout=20, check=True)
            info = json.loads(result.stdout)
            if not any(stream.get('codec_type') == 'video' for stream in info.get('streams', [])):
                raise ValueError('预演文件没有可播放的视频轨道')
        temporary.replace(path)
        record = {'id': asset_id, 'kind': 'frame' if extension == '.png' else 'video', 'filename': path.name, 'scene': scene.model_dump(mode='json'), 'revision': revision, 'time': frame_time, 'source': scene.source.model_dump() if scene.source else None, 'created_at': datetime.now(timezone.utc).isoformat()}
        record_path = root / f'{asset_id}.json'
        temporary_record.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary_record.replace(record_path)
        return {'ok': True, 'data': record}
    except (ValueError, OSError, subprocess.SubprocessError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, f'输出校验失败：{exc}') from exc
    finally:
        temporary.unlink(missing_ok=True)
        temporary_record.unlink(missing_ok=True)


def _list_records(scope):
    records = []
    for path in _folder(scope).glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or not all(k in record for k in ('id', 'kind', 'filename', 'created_at')):
                raise ValueError('Missing output metadata')
            records.append(record)
        except (ValueError, OSError) as exc:
            logger.warning('Skipping damaged previs record %s: %s', path.name, exc)
    return sorted(records, key=lambda r: r['created_at'], reverse=True)


@router.get('/outputs')
async def list_outputs(project: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    return {'ok': True, 'data': await asyncio.to_thread(_list_records, scope)}


@router.get('/outputs/{asset_id}/media')
async def output_media(project: str, asset_id: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role='viewer')
    if len(asset_id) != 64 or any(c not in '0123456789abcdef' for c in asset_id):
        raise HTTPException(404, '素材不存在')
    root = _folder(scope)
    for extension, content_type in (('.png', 'image/png'), ('.webm', 'video/webm')):
        path = root / f'{asset_id}{extension}'
        if path.is_file():
            return FileResponse(path, media_type=content_type)
    raise HTTPException(404, '素材不存在或已删除')
