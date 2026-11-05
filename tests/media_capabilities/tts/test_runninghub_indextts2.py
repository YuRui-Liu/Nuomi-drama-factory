import pytest
from pathlib import Path

from novelvideo.media_capabilities.tts.runninghub_indextts2 import (
    EmotionMode,
    compile_indextts2_request,
    RunningHubIndexTTS2Generator,
)


@pytest.mark.asyncio
async def test_generator_retains_safe_rejection_code_and_node_details(tmp_path, monkeypatch):
    from novelvideo.media_capabilities.tts import runninghub_indextts2 as module
    from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubError

    async def rejected(**kwargs):
        raise RunningHubError("RunningHub rejected the request", code="804", http_status=200,
                              operation="/task/openapi/create", details="Node 103 field value invalid")
    monkeypatch.setattr(module, "generate_indextts2_audio", rejected)
    result = await RunningHubIndexTTS2Generator(None).generate(
        prompt="别开。", reference_audio_path=tmp_path / "voice.flac", output_path=tmp_path / "out.wav",
    )
    assert not result.success
    assert "code=804" in result.error
    assert "http=200" in result.error
    assert "operation=/task/openapi/create" in result.error
    assert "Node 103" in result.error


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
        results = (type("Result", (), {"node_id": "5", "url": "https://rh-images.xiaoyaoyou.com/audio.wav"})(),)

    class FakeClient:
        def __init__(self):
            self.uploaded = []
            self.submitted = None

        async def upload(self, path):
            self.uploaded.append(Path(path).name)
            return f"api/{Path(path).name}"

        async def workflow_json(self, workflow_id):
            return {"103": {"class_type": "Switch", "inputs": {}}, "5": {"class_type": "SaveAudio", "inputs": {}}}

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


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_output", [False, True])
@pytest.mark.parametrize("mode,runner,output,extra", [
    ("neutral", "1", "5", {}),
    ("text", "14", "15", {"emotion_text": "紧张"}),
    ("audio", "17", "18", {"emotion_path": "emotion.wav"}),
    ("vector", "20", "22", {"emotion_vector": [0,0,0,0,0,0,0,0.5]}),
])
async def test_four_branch_workflow_is_pruned_and_correct_output_selected(mode, runner, output, extra, missing_output):
    from types import SimpleNamespace
    from novelvideo.media_capabilities.tts.runninghub_indextts2 import generate_indextts2_audio
    graph = {"4": {"class_type": "CR Prompt Text", "inputs": {"prompt": ""}},
             "10": {"class_type": "LoadAudio", "inputs": {"audio": ""}}}
    for r, o, n, field, cls in [("1","5",None,None,None), ("14","15","16","emo_text","CR Prompt Text"), ("17","18","19","emo_audio_prompt","LoadAudio"), ("20","22","21","emo_vector","CR Prompt Text")]:
        inputs = {"text": ["4",0], "audio": ["10",0], "emo_alpha": 1, "use_random": True}
        if n:
            graph[n] = {"class_type": cls, "inputs": {"audio" if n == "19" else "prompt": ""}}
            inputs[field] = [n,0]
        graph[r] = {"class_type": "IndexTTS2Run", "inputs": inputs}
        graph[o] = {"class_type": "SaveAudio", "inputs": {"audio": [r,0]}}
    class Client:
        async def workflow_json(self, _): return graph
        async def upload(self, _): return "uploaded.wav"
        async def submit(self, _, nodes, *, workflow):
            assert "103" not in workflow
            assert {n for n,v in workflow.items() if v["class_type"] == "SaveAudio"} == {output}
            assert {n for n,v in workflow.items() if v["class_type"] == "IndexTTS2Run"} == {runner}
            assert all(n["nodeId"] in workflow for n in nodes)
            assert any(n["nodeId"] == runner and n["fieldName"] == "use_random" for n in nodes)
            return "task"
        async def query(self, _): return SimpleNamespace(status="succeeded", results=[SimpleNamespace(node_id="wrong",url="wrong")] + ([] if missing_output else [SimpleNamespace(node_id=output,url="correct")]))
        async def download(self, url): assert url == "correct"; return b"audio"
    runtime = SimpleNamespace(create_client=lambda: Client(), workflow_id=lambda _: "deployed")
    if missing_output:
        with pytest.raises(RuntimeError, match="missing unique expected audio output"):
            await generate_indextts2_audio(runtime=runtime,text="台词",reference_path="voice.wav",mode=mode,**extra)
    else:
        assert await generate_indextts2_audio(runtime=runtime,text="台词",reference_path="voice.wav",mode=mode,**extra) == b"audio"
