"""Explicit review of retained media; never dispatches video generation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from novelvideo.director_plan.models import ShotPlan
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorOutputManifest, save_h3_director_manifest
from novelvideo.narrative_groups.service import load_group_video_prompt_manifest, narrative_group_sidecar_guard


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def issue_id(issue: dict) -> str:
    return _issue_digest(issue)


def _issue_digest(issue: dict) -> str:
    return digest({key: value for key, value in issue.items() if key != 'issue_id'})


def media_paths(manifest, project_dir):
    root = Path(project_dir).resolve()
    result = []
    for entry in manifest.entries:
        stored = entry.physical_video
        if not stored and len(manifest.entries) == 1:
            stored = manifest.physical_video
        if not stored:
            raise ValueError('缺少原始分段视频，待人工复核')
        path = Path(stored)
        path = (path if path.is_absolute() else root / path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('视频来源不在当前项目内')
        result.append((entry.segment, path))
    return result


def media_fingerprint(manifest, project_dir) -> str:
    rows = []
    for segment, path in media_paths(manifest, project_dir):
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        rows.append((segment.segment_id, checksum))
    return digest(rows)


def load_snapshot(project_dir, episode, group_id, revision):
    raw, stage = load_group_video_prompt_manifest(project_dir, episode, group_id)
    if stage.revision != revision:
        raise RuntimeError('视频版本已变化，请刷新后重新检查')
    if stage.status in {'queued', 'running'}:
        raise RuntimeError('视频生成尚未结束')
    return raw, stage, H3DirectorOutputManifest.model_validate(raw)


async def review_existing_manifest(manifest, *, project_dir, episode, ctx):
    from novelvideo.shot_continuity.production_review import review_generated_segments
    from novelvideo.text_task_runtime.runtime import current_text_task_runtime
    unavailable = lambda reason: [{'phase': 'generated', 'status': 'unavailable',
        'technical_error': reason, 'evidence': [], 'issues': []}]
    # A live director plan is not evidence of the facts used for this video.
    # Legacy H3 is separate partial evidence; never invent adopted cinematography.
    legacy = not manifest.cinematography_shots
    if legacy and (not manifest.entries or any(not entry.director_plan for entry in manifest.entries)):
        return unavailable('缺少原始摄影快照，待人工复核')
    try:
        shots = {row['id']: ShotPlan.model_validate(row) for row in manifest.cinematography_shots}
        expected = {value for entry in manifest.entries for value in entry.segment.source_shot_ids}
        if not legacy and (not expected or any(value not in shots or shots[value].cinematography is None for value in expected)):
            return unavailable('原始摄影快照与视频镜头不匹配，待人工复核')
        reports = await review_generated_segments(media_paths(manifest, project_dir), shots,
            current_text_task_runtime(), Path(ctx.state_dir) / 'cinematography_review_cache',
            Path(project_dir) / 'videos' / f'ep{episode:03d}' / 'cinematography_evidence',
            **({'frozen_h3_plans': {entry.segment.segment_id: entry.director_plan for entry in manifest.entries}}
               if legacy else {}))
        return [{'phase': 'generated', **report.model_dump(mode='json')} for report in reports]
    except (ValueError, OSError, KeyError):
        return unavailable('原始视频或摄影证据不可用，待人工复核')


def disposition_record(manifest, *, current_fingerprint, review_id, issue_id, media_fingerprint,
                       reason, decision, actor):
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise ValueError('请填写人工处理理由（1–2000 字）')
    if decision not in {'acknowledged', 'needs_repair', 'dismissed'}:
        raise ValueError('不支持的人工处理结论')
    if current_fingerprint != media_fingerprint:
        raise ValueError('视频来源已变化，请重新检查')
    review = next((row for row in reversed(manifest.cinematography_reviews)
                   if row.get('review_id') == review_id), None)
    if (not review or review.get('status') != 'failed'
        or review.get('media_fingerprint') != current_fingerprint
        or not review.get('evidence') or not review.get('frame_assets')):
        raise ValueError('缺少可核验的问题证据，请重新检查')
    issue = next((row for row in review.get('issues', []) if _issue_digest(row) == issue_id), None)
    if not issue:
        raise ValueError('检查问题已变化，请刷新')
    return {'disposition_id': uuid4().hex, 'review_id': review_id, 'issue_id': issue_id,
        'media_fingerprint': media_fingerprint, 'reason': reason, 'decision': decision,
        'actor': str(actor), 'created_at': datetime.now(timezone.utc).isoformat()}


def record_disposition(project_dir, episode, group_id, revision, **kwargs):
    with narrative_group_sidecar_guard(project_dir, episode):
        _, stage, manifest = load_snapshot(project_dir, episode, group_id, revision)
        row = disposition_record(manifest, current_fingerprint=media_fingerprint(manifest, project_dir), **kwargs)
        updated = manifest.model_copy(update={'quality_dispositions': (*manifest.quality_dispositions, row)})
        path = Path(stage.manifest_asset)
        save_h3_director_manifest(path if path.is_absolute() else Path(project_dir) / path, updated)
        return row


async def execute_quality_check(envelope, ctx):
    payload = envelope['payload']
    project_dir = Path(ctx.output_dir)
    episode, group_id, revision = int(envelope.get('episode') or payload['episode']), payload['group_id'], int(payload['revision'])
    with narrative_group_sidecar_guard(project_dir, episode):
        raw, _, manifest = load_snapshot(project_dir, episode, group_id, revision)
        if digest(raw) != payload['manifest_digest']:
            raise RuntimeError('视频清单已变化，请重新检查')
        fingerprint = media_fingerprint(manifest, project_dir)
        if fingerprint != payload['media_fingerprint']:
            raise RuntimeError('视频来源已变化，请重新检查')
    reports = await review_existing_manifest(manifest, project_dir=project_dir, episode=episode, ctx=ctx)
    attempt_id = uuid4().hex
    reports = [{**row, 'media_fingerprint': fingerprint, 'attempt_id': attempt_id,
                'checked_at': datetime.now(timezone.utc).isoformat()} for row in reports]
    with narrative_group_sidecar_guard(project_dir, episode):
        current, stage, latest = load_snapshot(project_dir, episode, group_id, revision)
        if digest(current) != payload['manifest_digest'] or media_fingerprint(latest, project_dir) != fingerprint:
            raise RuntimeError('检查期间视频版本或清单发生变化；结果未覆盖，请重新检查')
        updated = latest.model_copy(update={'cinematography_reviews': (*latest.cinematography_reviews, *reports)})
        path = Path(stage.manifest_asset)
        save_h3_director_manifest(path if path.is_absolute() else project_dir / path, updated)
    from novelvideo.shot_continuity.production_review import quality_status
    return {'group_id': group_id, 'revision': revision, 'attempt_id': attempt_id,
        'quality_status': quality_status(updated.cinematography_reviews, [entry.segment.segment_id for entry in updated.entries])}
