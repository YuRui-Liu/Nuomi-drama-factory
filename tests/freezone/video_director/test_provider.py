from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone.video_director.models import (
    CanvasBaseWire, CanvasReferenceWire, DirectorDraft, DirectorImage, DirectorSegment,
    OptimizedDirector, OptimizedSegment,
)
from novelvideo.freezone.video_director.provider import RunningHubDirectorProvider
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.media_capabilities.video.h3_prompt_profile import H3_PROMPT_PROFILE_VERSION


class FakeClient:
    def __init__(self):
        self.uploads = []
        self.submissions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def upload(self, path):
        self.uploads.append(Path(path).read_bytes())
        return "remote-image"

    async def submit(self, workflow_id, node_info):
        self.submissions.append((workflow_id, node_info))
        return "paid-id"


class FakeCoordinator:
    def __init__(self):
        self.config = None
        self.leases = []

    def configure(self, *args):
        self.config = args

    @asynccontextmanager
    async def lease(self, account, capability):
        self.leases.append((account, capability))
        yield


@pytest.mark.asyncio
async def test_reference_workflow_uses_its_settings_key_and_no_refine_node(tmp_path):
    image = DirectorImage(image_id="r", url="unused")
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p",
                          references=(image,), segments=(DirectorSegment(
                              id="s", prompt="Walk", duration_seconds=5),))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = CanvasReferenceWire(mode="ref2va", duration_seconds=aligned.duration_seconds,
                               subject_definitions="<Subject 1>: person from <Picture 1>",
                               summary="<Subject 1> walks", retention_analysis=(
                                   {"subject": "<Subject 1>", "retain": "fully_preserved - identity"},),
                               detailed_description="<Subject 1> walks", overall_soundscape="Footsteps",
                               non_diegetic_music="N/A")
    optimized = OptimizedDirector(revision=1, route="h3_ref", profile_id="minimax-h3-director",
                                  profile_version=H3_PROMPT_PROFILE_VERSION, optimized_at=datetime.now(timezone.utc),
                                  segments=(OptimizedSegment(segment_id="s", mode="ref2va",
                                      requested_duration_seconds=5, duration_seconds=aligned.duration_seconds,
                                      frames=aligned.frames, wire=wire, prompt=compile_h3_wire(wire)),))
    client = FakeClient()
    keys = []

    def workflow_key(key):
        keys.append(key)
        return "2096502793044582401"

    runtime = SimpleNamespace(account=SimpleNamespace(id="account", max_concurrency=3,
                              capability_limits={"video.*": 2}, queue_limit=17),
                              workflow_id_for_key=workflow_key,
                              workflow_id=lambda capability: (_ for _ in ()).throw(AssertionError("wrong route")),
                              create_client=lambda: client)
    coordinator = FakeCoordinator()
    provider = RunningHubDirectorProvider(SimpleNamespace(), runtime=runtime, coordinator=coordinator)
    path = tmp_path / "frozen.png"
    path.write_bytes(b"frozen")
    prepared = await provider.prepare(draft, optimized, {"r": path}, 5)
    assert prepared["workflow_id"] == "2096502793044582401"
    assert keys[0].value == "video_minimax_h3_ref"
    assert {node["nodeId"] for node in prepared["node_info"]} == {"12"}
    assert coordinator.config == ("account", 3, {"video.*": 2}, 17)
    assert client.uploads == [b"frozen"]
    assert await provider.submit(prepared) == "paid-id"
    assert client.submissions[0][0] == prepared["workflow_id"]


@pytest.mark.asyncio
async def test_known_ordinary_profile_receives_timeline_and_refine_dimensions(tmp_path):
    image = DirectorImage(image_id="first", url="unused")
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p",
                          segments=(DirectorSegment(id="s", prompt="Walk", duration_seconds=5,
                                                    first_frame=image),))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = CanvasBaseWire(mode="i2va", duration_seconds=aligned.duration_seconds,
                          integrated_multimodal_description="[Shot 1] Walk",
                          overall_soundscape="Footsteps", non_diegetic_music="N/A")
    optimized = OptimizedDirector(revision=1, route="h3", profile_id="minimax-h3-director",
                                  profile_version=H3_PROMPT_PROFILE_VERSION, optimized_at=datetime.now(timezone.utc),
                                  segments=(OptimizedSegment(segment_id="s", mode="i2va",
                                      requested_duration_seconds=5, duration_seconds=aligned.duration_seconds,
                                      frames=aligned.frames, wire=wire, prompt=compile_h3_wire(wire)),))
    client = FakeClient()
    runtime = SimpleNamespace(account=SimpleNamespace(id="account", max_concurrency=3,
                              capability_limits={}, queue_limit=17),
                              workflow_id=lambda capability: "2089723723468328961",
                              create_client=lambda: client)
    provider = RunningHubDirectorProvider(SimpleNamespace(), runtime=runtime,
                                          coordinator=FakeCoordinator())
    path = tmp_path / "frozen.png"
    path.write_bytes(b"frozen")
    prepared = await provider.prepare(draft, optimized, {"first": path}, 5)
    nodes = prepared["node_info"]
    assert {node["nodeId"] for node in nodes} == {"12", "18"}
    timeline = next(node["fieldValue"] for node in nodes if node["nodeId"] == "12")
    import json
    assert json.loads(timeline)["totalFrames"] == aligned.frames
    refine = {node["fieldName"]: node["fieldValue"] for node in nodes if node["nodeId"] == "18"}
    assert refine["width"] == 736 and refine["height"] == 1280
