"""Validated immutable media files; no caller-selected filesystem paths."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def probe_media(path: Path, kind: str) -> dict:
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
        info = json.loads(result.stdout)
        streams = info.get('streams', [])
        if not any(s.get('codec_type') == kind for s in streams):
            raise ValueError('媒体类型不符')
        duration = float(info['format']['duration'])
        if not math.isfinite(duration) or duration <= 0 or duration > 86400:
            raise ValueError('媒体时长无效')
        return dict(durationMs=round(duration * 1000), hasAudio=any(s.get('codec_type') == 'audio' for s in streams))
    except (subprocess.SubprocessError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('无法解码媒体，请检查文件及 ffprobe') from exc


def persist_media(path: Path, root: Path, owner: str, kind: str) -> dict:
    info = probe_media(path, kind)
    # Decode the entire audio stream, not merely the container header.
    if kind == 'audio':
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(path), '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, check=True, timeout=180)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError('音频不完整或不可解码') from exc
    sha = digest(path)
    owner_key = hashlib.sha256(owner.encode()).hexdigest()
    suffix = path.suffix.lower()
    if suffix not in {'.mp4', '.webm', '.mov', '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}:
        suffix = '.bin'
    dest = root / owner_key / (sha + suffix)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        import tempfile
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as f:
            temporary = Path(f.name)
        try:
            shutil.copyfile(path, temporary)
            if digest(temporary) != sha:
                raise ValueError('源文件在读取时发生变化，请重试')
            temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
    return {**info, 'sha256': sha, 'path': str(dest)}
