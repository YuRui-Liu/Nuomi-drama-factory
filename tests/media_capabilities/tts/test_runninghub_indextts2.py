import pytest
from pathlib import Path

from novelvideo.media_capabilities.tts.runninghub_indextts2 import (
    EmotionMode,
    compile_indextts2_request,
    RunningHubIndexTTS2Generator,
)


def test_text_emotion_variant_uses_clean_text_and_natural_emotion_prompt():
    compiled = compile_indextts2_request(
        text="你别过来。",
        reference_file="api/voice.wav",
        mode=EmotionMode.TEXT,
        emotion_text="求你了，别再靠近我。",
        emotion_alpha=0.6,
    )

    assert compiled.node_info("4", "prompt") == "你别过来。"
    assert compiled.node_info("16", "prompt") == "求你了，别再靠近我。"
    assert compiled.node_info("103", "value") == "2"
    assert compiled.node_info("1", "use_random") == "false"
    assert compiled.node_info("1", "emo_alpha") == "0.6"


def test_emotion_sources_are_mutually_exclusive():
    with pytest.raises(ValueError, match="emotion sources are mutually exclusive"):
        compile_indextts2_request(
            text="别过来。",
            reference_file="api/voice.wav",
            mode=EmotionMode.TEXT,
            emotion_text="求你了。",
            emotion_file="api/emotion.wav",
        )


@pytest.mark.asyncio
async def test_generator_submits_runninghub_workflow_and_writes_audio(tmp_path):
    class FakeSnapshot:
        status = "succeeded"
        provider_message = None
        results = (type("Result", (), {"url": "https://rh-images.xiaoyaoyou.com/audio.wav"})(),)

    class FakeClient:
        def __init__(self):
            self.uploaded = []
            self.submitted = None

        async def upload(self, path):
            self.uploaded.append(Path(path).name)
            return f"api/{Path(path).name}"

        async def submit(self, workflow_id, node_info):
            self.submitted = (workflow_id, node_info)
            return "task-1"

        async def query(self, task_id):
            return FakeSnapshot()

        async def download(self, url):
            return b"runninghub-audio"

        async def close(self):
            return None

    class FakeRuntime:
        def __init__(self):
            self.client = FakeClient()

        def create_client(self):
            return self.client

        def workflow_id(self, _capability):
            return "workflow-tts2"

    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"voice")
    output = tmp_path / "beat.mp3"
    generator = RunningHubIndexTTS2Generator(FakeRuntime())

    result = await generator.generate(
        prompt="你别过来。",
        reference_audio_path=reference,
        output_path=output,
        emotion_prompt="求你了，别再靠近我。",
    )

    assert result.success is True
    assert output.read_bytes() == b"runninghub-audio"
    assert generator.runtime.client.submitted[0] == "workflow-tts2"
