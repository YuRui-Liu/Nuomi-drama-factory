import subprocess
import wave
from pathlib import Path

import numpy as np


def fixture_media(tmp_path):
    for name, source, args in [('video.mp4', 'color=blue:s=160x90:r=24:d=2', ['-c:v', 'libx264']),
                               ('a.wav', 'sine=frequency=440:duration=2', []),
                               ('b.wav', 'sine=frequency=880:duration=2', [])]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', source, *args, str(tmp_path/name)], check=True)
    return tmp_path/'video.mp4', {'a': tmp_path/'a.wav', 'b': tmp_path/'b.wav'}


def test_real_multitrack_mix_and_video_preserved(tmp_path):
    from novelvideo.music.models import MusicPlan
    from novelvideo.music.mix import render_mix, mux_video
    from novelvideo.music.media import digest, probe_media
    video, paths = fixture_media(tmp_path)
    sha = digest(video)
    plan = MusicPlan(source=dict(assetVersionId='v', sha256=sha, durationMs=2000), tracks=[
        dict(id='t'+k, name=k, clips=[dict(id='c'+k, assetVersionId=k, startMs=0, sourceInMs=0, lengthMs=2000)]) for k in paths])
    audio = tmp_path/'mix.wav'
    render_mix(plan, video, paths, audio)
    with wave.open(str(audio)) as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').reshape(-1, 2)[:, 0] / 32768
        rate = w.getframerate()
    spectrum = abs(np.fft.rfft(samples))
    for frequency in (440, 880):
        assert spectrum[round(frequency*len(samples)/rate)] > 30
    assert len(samples) == 96000
    assert max(abs(samples)) <= .9
    out = tmp_path/'scored.mp4'
    mux_video(video, audio, out, 2000)
    assert abs(probe_media(out, 'video')['durationMs'] - 2000) < 100
    assert digest(video) == sha


def test_solo_and_muted_precedence():
    from novelvideo.music.models import MusicTrack
    from novelvideo.music.mix import active_tracks
    tracks = [MusicTrack(id='a', name='a', solo=True, muted=True), MusicTrack(id='b', name='b')]
    assert active_tracks(tracks) == []


def test_loop_fade_and_ducking(tmp_path):
    from novelvideo.music.models import MusicPlan
    from novelvideo.music.mix import render_mix
    video, paths = fixture_media(tmp_path)
    plan = MusicPlan(source=dict(assetVersionId='v', sha256='a'*64, durationMs=2000),
        ducking=dict(enabled=True, gainDb=-20, intervals=[dict(startMs=600, endMs=1400)]),
        tracks=[dict(id='t', name='t', clips=[dict(id='c', assetVersionId='a', startMs=0,
            sourceInMs=100, lengthMs=2000, loop=dict(startMs=0, endMs=400), fadeInMs=100, fadeOutMs=100)])])
    out = tmp_path/'duck.wav'
    render_mix(plan, video, paths, out)
    with wave.open(str(out)) as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').reshape(-1, 2)[:, 0].astype(float)
    rms = lambda a,b: np.sqrt(np.mean(samples[int(a*48000):int(b*48000)]**2))
    assert rms(.8,1.2) < rms(.2,.4)*.2
