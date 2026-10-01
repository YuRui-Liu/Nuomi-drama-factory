"""Prepare cloned dialogue and separated ambience for an existing group video."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from novelvideo.seedance2_i2v.voice_clone import (
    resolve_dialogue_reference_audio, _default_indextts2_generator,
    _generate_with_reference_audio,
)


def dubbing_directory(manifest: Path, span_index: int) -> Path:
    return manifest.parent / "dubbing" / manifest.stem / f"span-{span_index}"


async def resolve_lines(segment, store, *, resolver=resolve_dialogue_reference_audio):
    lines = list(segment.dialogue_lines)
    if not lines and segment.dialogue.strip():
        from novelvideo.media_capabilities.video.h3_timeline import H3SourceDialogueLine
        lines = [H3SourceDialogueLine(speaker=segment.speaker, text=segment.dialogue)]
    resolved = []
    missing = []
    for line in lines:
        voice = await resolver({"speaker": line.speaker}, store)
        if voice is None:
            missing.append(line.speaker)
        else:
            resolved.append((line, *voice))
    if missing:
        raise RuntimeError(f"角色「{'、'.join(dict.fromkeys(missing))}」缺少声音克隆参考声线，请先在角色工作区配置声线。")
    if not resolved:
        raise RuntimeError("本片段没有可克隆的角色对白")
    return resolved


async def _run(*command: str) -> str:
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-1000:])
    return stdout.decode()


async def prepare_external_audio(ctx, episode: int, group_id: str, revision: int, span_index: int):
    from novelvideo.api.deps import make_sqlite_store_for_context
    from novelvideo.media_capabilities.audio.stem_separator import DemucsStemSeparator
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource, load_h3_director_manifest, save_h3_director_manifest,
    )
    from novelvideo.narrative_groups.service import stage_payload, _sidecar_guard

    root = Path(ctx.output_dir)
    state = stage_payload(root, episode, group_id, "video")
    if int(state["revision"]) != revision:
        raise RuntimeError("视频版本已改变，请刷新后重试配音")
    path = Path(state["manifest_asset"])
    original = path.read_bytes()
    manifest = load_h3_director_manifest(path)
    entry = manifest.entries[span_index]
    store = await make_sqlite_store_for_context(ctx)
    try:
        lines = await resolve_lines(entry.segment, store)
    finally:
        await store.close()
    directory = dubbing_directory(path, span_index)
    # Resolve every speaker and separate audio before making paid TTS calls.
    stems = {}
    if manifest.ambience_stem_status != "succeeded" or not Path(manifest.ambience_stem_path or "").is_file():
        separator = DemucsStemSeparator()
        if not separator.available:
            raise RuntimeError("声音克隆任务需要环境音分离，但 Demucs 未安装；请配置音轨分离后重试，无需重生成视频。")
        result = await separator.separate(manifest.physical_video, directory / "stems")
        stems = {"ambience_stem_path": str(result.no_vocals), "ambience_stem_status": "succeeded",
                 "dialogue_stem_path": str(result.vocals), "dialogue_stem_status": "succeeded"}
    generator = _default_indextts2_generator()
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for line, reference, voice_hash in lines:
        key = hashlib.sha256(json.dumps([line.model_dump(), voice_hash], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        output = directory / f"{key}.wav"
        if not output.is_file() or not output.stat().st_size:
            result = await _generate_with_reference_audio(generator=generator, prompt=line.text,
                reference_path=reference, output_path=output, emotion_prompt="", audio_url_builder=None)
            if not result.success or not output.is_file():
                raise RuntimeError(f"{line.speaker} 声音克隆失败：{result.error}")
        files.append(output)
    command = ["ffmpeg", "-v", "error", "-y"]
    for file in files:
        command.extend(["-i", str(file)])
    joined = directory / "dialogue.wav"
    inputs = "".join(f"[{i}:a]" for i in range(len(files)))
    await _run(*command, "-filter_complex", f"{inputs}concat=n={len(files)}:v=0:a=1[a]",
               "-map", "[a]", str(joined))
    duration = float(await _run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=noprint_wrappers=1:nokey=1", str(joined)))
    target = float(entry.actual_duration_seconds)
    # Fit the whole dialogue, never silently truncate the last speaker.
    ratio = max(1.0, duration / target)
    if ratio > 1.35:
        raise RuntimeError(f"克隆对白长 {duration:.1f} 秒，超过片段 {target:.1f} 秒；请调整对白或镜头时长。已保存克隆结果。")
    fitted = directory / "dialogue-fitted.wav"
    await _run("ffmpeg", "-v", "error", "-y", "-i", str(joined), "-af",
               f"atempo={ratio},apad,atrim=duration={target}", str(fitted))
    with _sidecar_guard(root, episode):
        current = stage_payload(root, episode, group_id, "video")
        if int(current["revision"]) != revision or path.read_bytes() != original:
            raise RuntimeError("配音准备期间视频或声音选择已改变，已保留音频，请刷新后重试")
        entries = list(manifest.entries)
        entries[span_index] = entry.model_copy(update={
            "segment": entry.segment.model_copy(update={"dialogue_source": DialogueSource.EXTERNAL_TTS}),
            "dialogue_source": DialogueSource.EXTERNAL_TTS, "external_audio_path": str(fitted),
        })
        save_h3_director_manifest(path, manifest.model_copy(update={"entries": tuple(entries), **stems}))
    return str(fitted)
