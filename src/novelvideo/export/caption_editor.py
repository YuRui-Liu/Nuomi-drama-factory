"""Caption editing with optimistic revisions and recoverable atomic publication."""
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from novelvideo.export.captions import CaptionTrack
from novelvideo.export.episode_export import build_caption_track


def _path(root: Path, episode: int) -> Path:
    return root / "videos" / "episodes" / f"ep{episode:03d}.caption-events.json"


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "unsaved"


@contextmanager
def _lock(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _publish(path: Path, revision: str, track: CaptionTrack):
    with _lock(path):
        if _revision(path) != revision:
            raise ValueError("CAPTION_REVISION_CHANGED: 字幕已被另一处编辑，请重新载入")
        if path.exists():
            history = path.parent / "caption-history"
            history.mkdir(exist_ok=True)
            (history / f"{path.stem}.{revision}.json").write_bytes(path.read_bytes())
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".caption-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(track.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"revision": _revision(path), "track": track.model_dump()}


async def get_track(root: Path, episode: int, beats: list[dict]):
    revision = _revision(_path(root, episode))
    track = await build_caption_track(root, episode, beats, allow_estimated_remap=True)
    if revision != _revision(_path(root, episode)):
        raise ValueError("CAPTION_REVISION_CHANGED: 字幕已被另一处编辑，请重新载入")
    return {"revision": revision, "track": track.model_dump()}


async def save_track(root: Path, episode: int, beats: list[dict], revision: str, data: dict):
    path = _path(root, episode)
    if _revision(path) != revision:
        raise ValueError("CAPTION_REVISION_CHANGED: 字幕已被另一处编辑，请重新载入")
    current = await build_caption_track(root, episode, beats, allow_estimated_remap=True)
    fresh = await build_caption_track(root, episode, beats, ignore_saved=True, allow_estimated_remap=True)
    track = CaptionTrack.model_validate(data)
    if track.audio_fingerprint != fresh.audio_fingerprint:
        raise ValueError("CAPTION_SOURCE_CHANGED: 音频或脚本已改变，请重建草稿")
    if (track.duration != fresh.duration or track.source_durations != fresh.source_durations
            or track.timing_basis != fresh.timing_basis):
        raise ValueError("CAPTION_TIMELINE_CHANGED: 字幕时间轴与实际片段时长不一致，请重建草稿")
    old_ids = [event.dialogue_id for event in current.events]
    if [event.dialogue_id for event in track.events] != old_ids:
        raise ValueError("CAPTION_CONTENT_REMOVED: 不可删除或替换对白；请在剧本中修订内容")
    if any(not event.text.strip() for event in track.events):
        raise ValueError("CAPTION_CONTENT_REMOVED: 对白不可为空")
    if track.timing_basis != "actual" and any(e.review_status == "approved" for e in track.events):
        raise ValueError("CAPTION_ACTUAL_TIMING_REQUIRED: 缺少实际片段时长，不能确认字幕")
    for old, new in zip(current.events, track.events):
        if new != old and new.source != "manual":
            raise ValueError("CAPTION_MANUAL_REQUIRED: 人工修改必须标记为 manual")
    return _publish(path, revision, track)


async def rebuild_track(root: Path, episode: int, beats: list[dict], revision: str):
    track = await build_caption_track(root, episode, beats, ignore_saved=True, allow_estimated_remap=True)
    return _publish(_path(root, episode), revision, track)
