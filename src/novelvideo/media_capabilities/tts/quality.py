"""Local technical audio checks; never equate decode success with voice quality."""

from __future__ import annotations

import array
import math
import shutil
import subprocess
import sys


def check_voice_audio(content: bytes, *, kind: str = "dialogue") -> dict:
    policy = {"version": "voice-technical-v1", "sample_rate": 16000,
              "min_duration": 0.5 if kind == "dialogue" else 0.1, "max_duration": 30.0,
              "max_silence_fraction": 0.95, "max_clipping_fraction": 0.01}
    report = {"status": "qc_unavailable", "policy": policy, "technical": {"passed": False},
              "reason": "audio_semantic_reviewer_not_configured", "auto_publish": False}
    if not shutil.which("ffmpeg"):
        report["reason"] = "ffmpeg_unavailable"
        return report
    if not content or len(content) > 32 * 1024 * 1024:
        return {**report, "status": "rejected", "reason": "invalid_audio_size"}
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0",
             "-t", "31", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
            input=content, capture_output=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {**report, "status": "rejected", "reason": "audio_decode_failed"}
    samples = array.array("f")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples or any(not math.isfinite(value) for value in samples):
        return {**report, "status": "rejected", "reason": "invalid_audio_samples"}
    duration = len(samples) / policy["sample_rate"]
    silence = sum(abs(value) < 0.001 for value in samples) / len(samples)
    clipping = sum(abs(value) >= 0.999 for value in samples) / len(samples)
    technical = {"duration": duration, "silence_fraction": silence, "clipping_fraction": clipping}
    passed = (policy["min_duration"] <= duration <= policy["max_duration"]
              and silence < policy["max_silence_fraction"] and clipping < policy["max_clipping_fraction"])
    report["technical"] = {**technical, "passed": passed}
    if not passed:
        report.update(status="rejected", reason="technical_audio_check_failed")
    return report
