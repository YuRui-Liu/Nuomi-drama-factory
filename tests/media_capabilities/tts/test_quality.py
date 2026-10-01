import io
import math
import shutil
import struct
import wave

import pytest


def wav_content(silent=False):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", 0 if silent else int(6000 * math.sin(i * .08))) for i in range(16000)))
    return output.getvalue()


def assess(content):
    from novelvideo.media_capabilities.tts import quality
    return quality.check_voice_audio(content, kind="dialogue")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_decode_success_does_not_mean_semantic_acceptance():
    result = assess(wav_content())
    assert result["status"] == "qc_unavailable"
    assert result["technical"]["passed"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("content", [b"not audio", wav_content(silent=True)], ids=["invalid", "silent"])
def test_invalid_or_silent_audio_rejected(content):
    assert assess(content)["status"] == "rejected"
