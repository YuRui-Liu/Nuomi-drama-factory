from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import pytest

from novelvideo.media_capabilities.tts.audio_merge import (
    AudioSegment,
    merge_segments,
    probe_audio,
)


def write_wav(path: Path, value: int, *, rate: int, frames: int) -> None:
    sample = int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(sample * frames)


def test_merge_orders_resamples_and_inserts_exact_pause(tmp_path: Path) -> None:
    first, second, third = (tmp_path / f"{name}.wav" for name in "abc")
    write_wav(first, 100, rate=8000, frames=800)
    write_wav(second, 200, rate=16000, frames=1600)
    write_wav(third, 300, rate=8000, frames=800)
    segments = [
        AudioSegment(index=2, path=third),
        AudioSegment(index=0, path=first),
        AudioSegment(index=1, path=second),
    ]

    output = merge_segments(segments, tmp_path / "merged.wav", pause_ms=100)
    probe = probe_audio(output)

    assert probe.sample_rate == 16000
    assert probe.frame_count == 3 * 1600 + 2 * 1600
    first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    merge_segments(segments, output, pause_ms=100)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == first_hash


def test_merge_rejects_missing_segment(tmp_path: Path) -> None:
    source = tmp_path / "a.wav"
    write_wav(source, 100, rate=8000, frames=100)
    with pytest.raises(ValueError, match="audio.segment_missing"):
        merge_segments(
            [AudioSegment(index=0, path=source), AudioSegment(index=2, path=source)],
            tmp_path / "out.wav",
        )
