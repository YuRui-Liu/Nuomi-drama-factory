"""Deterministic, non-destructive multi-track music rendering."""
from pathlib import Path
import subprocess
import tempfile
import time

from .media import probe_media
from .models import MusicPlan, validate_sources


def active_tracks(tracks):
    solo = any(t.solo for t in tracks)
    return [t for t in tracks if not t.muted and (not solo or t.solo)]


def run(args, check_cancel=None):
    process = None
    try:
        process = subprocess.Popen(['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin', '-y', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 900
        while True:
            if check_cancel:
                check_cancel()
            try:
                _, stderr = process.communicate(timeout=.5)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise ValueError('音频处理超时')
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('音频处理超时或 FFmpeg 不可用') from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
    if process.returncode:
        raise ValueError('混音失败：' + stderr.decode(errors='replace')[-700:])


def duck_expression(plan):
    if not plan.ducking.enabled or not plan.ducking.intervals:
        return '1'
    expressions = []
    duck = plan.ducking
    g = 10 ** (duck.gainDb / 20)
    a, r = max(.001, duck.attackMs / 1000), max(.001, duck.releaseMs / 1000)
    for interval in duck.intervals:
        start, end = interval.startMs / 1000, interval.endMs / 1000
        expressions.append(f'if(lt(t,{start-a}),1,if(lt(t,{start}),1-(1-{g})*(t-({start-a}))/{a},if(lt(t,{end}),{g},if(lt(t,{end+r}),{g}+(1-{g})*(t-{end})/{r},1))))')
    expr = expressions[0]
    for other in expressions[1:]:
        expr = f'min({expr},{other})'
    return expr


def render_mix(plan: MusicPlan, video: Path, sources: dict[str, Path], output: Path, check_cancel=None):
    validate_sources(plan, {k: probe_media(v, 'audio')['durationMs'] for k, v in sources.items()})
    seconds = plan.source.durationMs / 1000
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temp = Path(folder)
        inputs = ['-f', 'lavfi', '-i', f'anullsrc=r=48000:cl=stereo:d={seconds}']
        filters, labels = [], ['[0:a]']
        index = 1
        if not plan.original.muted and probe_media(video, 'video')['hasAudio']:
            inputs += ['-i', str(video)]
            filters.append(f'[{index}:a:0]aresample=48000,asetpts=PTS-STARTPTS,volume={10**(plan.original.gainDb/20)}[original]')
            labels.append('[original]')
            index += 1
        for track in active_tracks(plan.tracks):
            for clip in track.clips:
                source = sources[clip.assetVersionId]
                offset = clip.sourceInMs / 1000
                if clip.loop:
                    loopfile = temp / f'loop-{index}.wav'
                    run(['-i', str(source), '-af', f'atrim=start={clip.loop.startMs/1000}:end={clip.loop.endMs/1000},asetpts=PTS-STARTPTS', '-ar', '48000', '-ac', '2', str(loopfile)], check_cancel)
                    source = loopfile
                    offset = (clip.sourceInMs - clip.loop.startMs) / 1000
                    inputs += ['-stream_loop', '-1']
                inputs += ['-i', str(source)]
                length = clip.lengthMs / 1000
                chain = f'[{index}:a:0]atrim=start={offset}:duration={length},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={10**((track.gainDb+clip.gainDb)/20)}'
                if clip.fadeInMs:
                    chain += f',afade=t=in:st=0:d={clip.fadeInMs/1000}'
                if clip.fadeOutMs:
                    chain += f',afade=t=out:st={(clip.lengthMs-clip.fadeOutMs)/1000}:d={clip.fadeOutMs/1000}'
                chain += f',adelay={clip.startMs}:all=1,volume=\'{duck_expression(plan)}\':eval=frame[a{index}]'
                filters.append(chain)
                labels.append(f'[a{index}]')
                index += 1
        filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,alimiter=limit=0.891251:level=false:latency=true,atrim=duration={seconds}[mix]')
        temporary = temp / 'mix.wav'
        run([*inputs, '-filter_complex', ';'.join(filters), '-map', '[mix]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(temporary)], check_cancel)
        temporary.replace(output)


def mux_video(video: Path, audio: Path, output: Path, duration_ms: int, check_cancel=None):
    if video.resolve() == output.resolve():
        raise ValueError('配乐版不能覆盖源视频')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temporary = Path(folder) / 'result.mp4'
        common = ['-i', str(video), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-b:a', '256k', '-t', str(duration_ms/1000), '-movflags', '+faststart']
        try:
            run([*common, '-c:v', 'copy', str(temporary)], check_cancel)
        except ValueError:
            run([*common, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(temporary)], check_cancel)
        temporary.replace(output)
