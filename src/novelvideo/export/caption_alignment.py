"""Explicit, local-only speech alignment. Recognition never replaces script text."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from difflib import SequenceMatcher

from novelvideo.export.captions import CaptionTrack
from novelvideo.export.caption_editor import _path, _revision, _publish
from novelvideo.export.episode_export import build_caption_track


def _text(value):
    return "".join(c.lower() for c in value if c.isalnum())


def _model_cache_dir() -> str | None:
    explicit = os.getenv("NUOMI_CAPTION_MODEL_CACHE")
    if explicit:
        return explicit
    data_root = os.getenv("NOVELVIDEO_DATA_ROOT")
    return str(Path(data_root) / "cache" / "huggingface" / "hub") if data_root else None


def _validated_words(words, duration):
    clean, issues, previous_end = [], [], 0.
    for word in words:
        try:
            start, end = float(word["start"]), float(word["end"])
            probability = float(word.get("probability", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("CAPTION_ALIGNMENT_INVALID: 识别时间或置信度格式无效") from exc
        if not all(math.isfinite(value) for value in (start, end, probability)) or not 0 <= start <= end <= duration or not 0 <= probability <= 1:
            raise ValueError("CAPTION_ALIGNMENT_INVALID: 识别时间超出原声片段或数值无效")
        if start == end:
            issues.append({"kind": "zero_duration_token", "message": "识别返回零时长词，已排除该词的时间证据，请试听复核", "recognized_text": str(word.get("word", ""))})
            continue
        if start < previous_end:
            raise ValueError("CAPTION_ALIGNMENT_INVALID: 识别词时间倒序或重叠，无法可靠对齐")
        previous_end = end
        clean.append({**word, "start": start, "end": end, "probability": probability})
    return clean, issues


def align_words(track: CaptionTrack, words: list[dict]):
    """Use exact monotonic character matches; ambiguous/unmatched events stay drafts."""
    words, issues = _validated_words(words, track.duration)
    recognized, stamps = "", []
    for word in words:
        value = _text(word.get("word", ""))
        recognized += value
        stamps.extend([word] * len(value))
    scripts = [_text(re.sub(r"^[^：:]{1,12}[：:]", "", e.text.replace("\n", ""))) for e in track.events]
    script = "".join(scripts)
    mapping = {}
    for block in SequenceMatcher(None, script, recognized, autojunk=False).get_matching_blocks():
        mapping.update({block.a + i: block.b + i for i in range(block.size)})
    events, cursor, used = [], 0, set()
    for event, value in zip(track.events, scripts):
        indices = [mapping.get(i) for i in range(cursor, cursor + len(value))]
        cursor += len(value)
        if value and all(i is not None for i in indices) and indices == list(range(indices[0], indices[0] + len(value))):
            matched = [stamps[i] for i in indices]
            start, end = float(matched[0]["start"]), float(matched[-1]["end"])
            confidence = min(float(w.get("probability", 0)) for w in matched)
            if confidence >= .65 and 0 <= start < end <= track.duration:
                events.append(event.model_copy(update={"start": start, "end": end, "source": "aligned", "review_status": "needs_review"}))
                used.update(indices)
                continue
            kind = "low_confidence"
        else:
            kind = "unmatched_script"
        issues.append({"kind": kind, "dialogue_id": event.dialogue_id, "message": "未可靠匹配原声，保留估算时间，请试听复核"})
        events.append(event)
    # Restore only candidates participating in an overlap. Restoring an estimate
    # can reach another candidate, so repeat until this connected region settles.
    # The original track is valid; every iteration restores at least one event.
    while True:
        revert = set()
        for i in range(1, len(events)):
            if events[i].start < events[i - 1].end:
                revert.update(j for j in (i - 1, i) if events[j] != track.events[j])
        if not revert:
            break
        for i in sorted(revert):
            issues.append({"kind": "timing_conflict", "dialogue_id": events[i].dialogue_id,
                           "message": "匹配时间与相邻字幕冲突，已恢复该条估算时间，请试听复核"})
            events[i] = track.events[i]
    extra = "".join(c for i, c in enumerate(recognized) if i not in used)
    if extra:
        issues.append({"kind": "extra_speech", "message": "原声中有未匹配剧本的识别内容", "recognized_text": extra})
    return CaptionTrack.model_validate({**track.model_dump(), "events": [e.model_dump() for e in events]}), issues


async def _run(*args, timeout=600, env=None):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("CAPTION_ALIGNMENT_UNAVAILABLE: " + stderr.decode(errors="replace")[-1500:])
    return stdout


async def transcribe_local(audio: Path, model: str, cache_dir: str | None):
    """Run optional MLX in an isolated bounded process; never download a model."""
    code = """import json, sys
from pathlib import Path
import mlx_whisper
from huggingface_hub import snapshot_download
model=sys.argv[2]
if not Path(model).is_dir():
    model=snapshot_download(model, cache_dir=sys.argv[3] or None, local_files_only=True)
result=mlx_whisper.transcribe(sys.argv[1], path_or_hf_repo=model, language='zh', word_timestamps=True, condition_on_previous_text=False, verbose=False)
Path(sys.argv[4]).write_text(json.dumps([w for s in result['segments'] for w in s.get('words', [])]))
"""
    with tempfile.TemporaryDirectory(prefix="caption-asr-") as folder:
        output = Path(folder) / "words.json"
        try:
            await _run(os.getenv("NUOMI_CAPTION_PYTHON", sys.executable), "-c", code, str(audio), model, cache_dir or "", str(output),
                       env={**os.environ, "HF_HUB_OFFLINE": "1"})
        except (RuntimeError, OSError) as exc:
            types = re.findall(r"\b([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)):", str(exc))
            error_type = types[-1] if types else type(exc).__name__
            if error_type == "LocalEntryNotFoundError":
                message = "本机未找到已缓存的语音模型。请先下载模型，并将 NUOMI_CAPTION_MODEL 指向本地模型目录，或设置 NUOMI_CAPTION_MODEL_CACHE 为 Hugging Face hub 缓存目录；本操作不会自动下载。"
            elif error_type in {"ModuleNotFoundError", "ImportError", "FileNotFoundError"}:
                message = "本地语音识别环境不完整。请在运行环境安装 mlx-whisper，或通过 NUOMI_CAPTION_PYTHON 指向已安装该依赖的 Python。"
            else:
                message = "本地语音识别失败，请检查模型文件与运行环境后重试。"
            raise RuntimeError(f"CAPTION_ALIGNMENT_UNAVAILABLE: {message}（{error_type}）") from exc
        return json.loads(output.read_text())


async def align_track(root: Path, episode: int, beats: list[dict], revision: str, *, adapter=None, reload_beats=None):
    from novelvideo.task_backend.runners.video import resolve_episode_composition_sources
    if _revision(_path(root, episode)) != revision:
        raise ValueError("CAPTION_REVISION_CHANGED: 字幕已改变，请重新载入")
    sources = resolve_episode_composition_sources(root, episode, beats)
    from novelvideo.media_capabilities.video.h3_timeline import DialogueSource
    if any(not source.is_director or any(entry.dialogue_source is not DialogueSource.H3_NATIVE for entry in source.entries) for source in sources):
        raise ValueError("CAPTION_ALIGNMENT_NATIVE_ONLY: 仅支持全部采用导演组原声的片段；外部配音或旧版片段不能使用原视频声音对齐")
    track = await build_caption_track(root, episode, beats, sources=sources, ignore_saved=True, allow_estimated_remap=True)
    if not sources or track.timing_basis != "actual":
        raise ValueError("CAPTION_ALIGNMENT_UNAVAILABLE: 缺少实际原声片段")
    model = os.getenv("NUOMI_CAPTION_MODEL", "mlx-community/whisper-small-mlx")
    cache_dir = _model_cache_dir()
    key = hashlib.sha256(json.dumps([track.audio_fingerprint, track.source_durations, model, cache_dir, "mlx-whisper:zh:words:v2"]).encode()).hexdigest()
    cache = root / "videos" / "episodes" / "caption-alignment" / f"{key}.json"
    if cache.is_file():
        cached = json.loads(cache.read_text())
        words, word_issues = cached["words"], cached["issues"]
    else:
        words, word_issues, offset = [], [], 0.
        with tempfile.TemporaryDirectory(prefix="caption-audio-") as folder:
            for index, (source, duration) in enumerate(zip(sources, track.source_durations)):
                audio = Path(folder) / f"{index}.wav"
                # Adopted video is authoritative for native speech, never a stale final.
                await _run("ffmpeg", "-v", "error", "-y", "-i", str(source.video_path), "-vn", "-af", "apad", "-t", str(duration), "-ar", "16000", "-ac", "1", str(audio), timeout=120)
                result = await (adapter or transcribe_local)(audio, model, cache_dir)
                result, source_issues = _validated_words(result, duration)
                word_issues.extend({**issue, "source_index": index} for issue in source_issues)
                for word in result:
                    start, end = float(word["start"]), float(word["end"])
                    words.append({**word, "start": start + offset, "end": end + offset})
                offset += duration
        if not words:
            raise ValueError("CAPTION_ALIGNMENT_EMPTY: 原声未识别到文字，请人工复核")
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=cache.parent, prefix=".alignment-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"words": words, "issues": word_issues}, handle, ensure_ascii=False)
            os.replace(temporary, cache)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    aligned, issues = align_words(track, words)
    issues = word_issues + issues
    aligned.alignment_issues = issues
    latest_beats = await reload_beats() if reload_beats is not None else beats
    fresh = await build_caption_track(root, episode, latest_beats, ignore_saved=True, allow_estimated_remap=True)
    if fresh.audio_fingerprint != track.audio_fingerprint:
        raise ValueError("CAPTION_SOURCE_CHANGED: 对齐期间原声已改变，请重试")
    return {**_publish(_path(root, episode), revision, aligned), "alignment_issues": issues}
