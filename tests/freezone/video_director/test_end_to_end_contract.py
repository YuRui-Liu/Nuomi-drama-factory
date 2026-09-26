"""Local Director contract through freezing, optimization, compilation and recovery."""

from __future__ import annotations

import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from PIL import Image

from novelvideo.freezone.video_director.h3_adapter import compile_director_payload
from novelvideo.freezone.video_director.models import DirectorDraft, DirectorImage, DirectorSegment
from novelvideo.freezone.video_director.service import DirectorService


def png(color: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, "PNG")
    return stream.getvalue()


class StructuredRuntime:
    def __init__(self):
        self.calls = []

    async def run_structured(self, *, prompt, output_type, system_prompt, images):
        data = json.loads(prompt.partition("INPUT_JSON:\n")[2])
        self.calls.append((data, tuple(image.data for image in images)))
        if data["mode"] == "ref2va":
            facts = data["references"]
            subjects = list(dict.fromkeys(fact["subject_tag"] for fact in facts))
            definitions = [subject + ": " + "; ".join(
                f"{fact['variant_label'] or 'base'} from {fact['picture_tag']}"
                for fact in facts if fact["subject_tag"] == subject) for subject in subjects]
            wire = {"mode": "ref2va", "duration_seconds": data["duration_seconds"],
                    "subject_definitions": "\n".join(definitions),
                    "summary": "; ".join(subjects) + " moves",
                    "retention_analysis": [{"subject": subject, "retain": "fully_preserved - identity"}
                                           for subject in subjects],
                    "detailed_description": "; ".join(subjects) + " moves through a room.",
                    "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
        else:
            wire = {"mode": data["mode"], "duration_seconds": data["duration_seconds"],
                    "final_shot_number": 1,
                    "integrated_multimodal_description": "[Shot 1] Directed scene: " + data["source_prompt"],
                    "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
        return output_type.model_validate({"segment_id": data["segment_id"], "wire": wire})


class CompilingProvider:
    def __init__(self, video: bytes):
        self.video = video
        self.prepared = None
        self.uploaded = {}
        self.submissions = 0
        self.queries = 0

    async def prepare(self, draft, optimized, paths, reference_limit):
        self.uploaded = {image_id: path.read_bytes() for image_id, path in paths.items()}
        uploads = {image_id: {"imageFile": "frozen-" + image_id} for image_id in paths}
        self.prepared = compile_director_payload(draft, optimized, uploads,
                                                 reference_limit=reference_limit)
        return {"workflow_id": "fake-workflow", "profile_id": "fake-profile",
                "profile_version": 1, "node_info": {"timeline_data": self.prepared.timeline_data,
                                                      **self.prepared.semantic_values}}

    async def submit(self, prepared):
        self.submissions += 1
        assert self.prepared is not None
        assert prepared["node_info"]["timeline_data"] == self.prepared.timeline_data
        return "fake-paid-task"

    async def query(self, task_id):
        assert task_id == "fake-paid-task"
        self.queries += 1
        return SimpleNamespace(status="queued" if self.queries == 1 else "succeeded",
                               results=(SimpleNamespace(node_id="7", url="https://fake.test/video.mp4"),))

    async def download(self, url):
        assert url == "https://fake.test/video.mp4"
        return self.video


@pytest.fixture
def video_bytes(tmp_path):
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not Path(ffmpeg).is_file():
        pytest.skip("ffmpeg is required for real output probe")
    output = tmp_path / "fixture.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=32x32:r=24", "-t", "0.5", "-c:v", "mpeg4",
                    str(output)], check=True)
    return output.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_mode", [False, True], ids=["first-last", "pure-reference"])
async def test_asset_selection_to_durable_two_segment_result(tmp_path, video_bytes, reference_mode):
    output = tmp_path / "output"
    output.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    ctx = SimpleNamespace(project_id="project-1", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=runtime_dir)
    originals = {"base": png("red"), "costume": png("blue"), "last": png("green")}
    for image_id, data in originals.items():
        (output / f"{image_id}.png").write_bytes(data)
    def image(image_id, **metadata):
        return DirectorImage(image_id=image_id, url=f"{image_id}.png", **metadata)
    base = image("base", asset_id="character:hero", character_id="hero", asset_kind="portrait")
    costume = image("costume", asset_id="character:hero", character_id="hero",
                    variant_id="coat", variant_label="红外套", asset_kind="identity_costume")
    segments = ((DirectorSegment(id="opening", prompt="Original opening", duration_seconds=15),
                 DirectorSegment(id="closing", prompt="Original closing", duration_seconds=5))
                if reference_mode else
                (DirectorSegment(id="opening", prompt="Original opening", duration_seconds=15,
                                 first_frame=base),
                 DirectorSegment(id="closing", prompt="Original closing", duration_seconds=5,
                                 first_frame=costume, last_frame=image("last"))))
    draft = DirectorDraft(revision=3, aspect_ratio="9:16", resolution="720p",
                          references=(base, costume) if reference_mode else (), segments=segments)
    runtime = StructuredRuntime()
    provider = CompilingProvider(video_bytes)
    service = DirectorService(ctx, runtime=runtime, provider=provider)
    created, is_new = service.create("canvas", "director", "request", draft)
    assert is_new
    assert created["snapshot"]["segments"][0]["prompt"] == "Original opening"
    assert [item["character_id"] for item in created["snapshot"]["references"]] == (
        ["hero", "hero"] if reference_mode else [])
    for image_id in originals:
        (output / f"{image_id}.png").write_bytes(png("white"))
    queued = await service.resume(created["id"])
    assert queued["stage"] == "queued"
    assert provider.submissions == 1
    expected_uploads = (dict((key, originals[key]) for key in ("base", "costume"))
                        if reference_mode else originals)
    assert provider.uploaded == expected_uploads
    assert [call[0]["segment_id"] for call in runtime.calls] == ["opening", "closing"]
    assert [call[1] for call in runtime.calls] == (
        [(originals["base"], originals["costume"])] * 2 if reference_mode else
        [(originals["base"],), (originals["costume"], originals["last"])])
    assert [s["prompt"] for s in queued["optimized"]["segments"]] != [s.prompt for s in draft.segments]
    assert [s["frames"] for s in queued["optimized"]["segments"]] == [362, 124]
    timeline = json.loads(provider.prepared.timeline_data)
    assert [s["id"] for s in timeline["segments"]] == ["opening", "closing"]
    assert [s["start"] for s in timeline["segments"]] == [0, 362]
    assert [s["length"] for s in timeline["segments"]] == [362, 124]
    assert timeline["totalFrames"] == 486
    assert all((s["length"] - 5) % 17 == 0 for s in timeline["segments"])
    assert queued["actual_parameters"]["duration_seconds"] == pytest.approx(486 / 24)
    assert [shot["prompt"] for shot in timeline["shots"]] == [
        segment["prompt"] for segment in queued["optimized"]["segments"]]
    assert all(shot["prompt"] != source.prompt for shot, source in zip(
        timeline["shots"], draft.segments, strict=True))
    if reference_mode:
        assert provider.prepared.route == "h3_ref"
        assert [r["imageFile"] for r in timeline["global"]["refs"]] == ["frozen-base", "frozen-costume"]
        assert "<Subject 1>" in queued["optimized"]["segments"][0]["prompt"]
        assert "<Subject 2>" not in queued["optimized"]["segments"][0]["prompt"]
        assert all(s["genImage"] is None and s["endImage"] is None for s in timeline["segments"])
    else:
        assert provider.prepared.route == "h3"
        assert timeline["shots"][0]["startImage"]["imageFile"] == "frozen-base"
        assert timeline["shots"][1]["endImage"]["imageFile"] == "frozen-last"
    restarted = DirectorService(ctx, runtime=runtime, provider=provider)
    completed = await restarted.resume(created["id"])
    assert completed["stage"] == "completed" and urlsplit(completed["result_url"]).path.endswith(".mp4")
    assert len(restarted.list("canvas", "director")) == 1
    assert restarted.get(created["id"])["snapshot"]["segments"][1]["prompt"] == "Original closing"
    assert provider.submissions == 1
