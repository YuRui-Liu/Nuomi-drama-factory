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
from novelvideo.freezone.video_director.models import (
    DirectorDraft, DirectorImage, DirectorSegment, TechniqueSelection,
)
from novelvideo.freezone.video_director.service import DirectorService
from novelvideo.freezone.video_director.techniques import resolve_technique
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubError


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
                    "detailed_description": "; ".join(subjects) + " moves through a room."
                    + (" two-person gaze intent" if "technique" in data else ""),
                    "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
        else:
            wire = {"mode": data["mode"], "duration_seconds": data["duration_seconds"],
                    "final_shot_number": 1,
                    "integrated_multimodal_description": "[Shot 1] Directed scene: " + data["source_prompt"]
                    + (" two-person gaze intent" if "technique" in data else ""),
                    "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
        result = {"segment_id": data["segment_id"], "wire": wire}
        if "technique" in data:
            result["technique_conflict"] = None
        return output_type.model_validate(result)


class CompilingProvider:
    def __init__(self, video: bytes, *, reject_once: bool = False):
        self.video = video
        self.reject_once = reject_once
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
        if self.reject_once:
            self.reject_once = False
            raise RunningHubError("fixture rejection", code="BAD_INPUT", http_status=200)
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
@pytest.mark.parametrize("mode", ["i2v", "fl2v", "ref_only"])
@pytest.mark.parametrize("selected", [False, True], ids=["baseline", "selected-card"])
async def test_asset_selection_to_durable_two_segment_result(tmp_path, mode, selected):
    reference_mode = mode == "ref_only"
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
    technique = TechniqueSelection(id="two-person-gaze", version="1.0.0") if selected else None
    segments = ((DirectorSegment(id="opening", prompt="Original opening", duration_seconds=15,
                                 technique=technique),
                 DirectorSegment(id="closing", prompt="Original closing", duration_seconds=5))
                if reference_mode else
                (DirectorSegment(id="opening", prompt="Original opening", duration_seconds=15,
                                 first_frame=base,
                                 last_frame=image("last") if mode == "fl2v" else None,
                                 technique=technique),
                 DirectorSegment(id="closing", prompt="Original closing", duration_seconds=5,
                                 first_frame=costume)))
    draft = DirectorDraft(revision=3, aspect_ratio="9:16", resolution="720p",
                          references=(base, costume) if reference_mode else (), segments=segments)
    runtime = StructuredRuntime()
    provider = CompilingProvider(b"", reject_once=selected)
    service = DirectorService(ctx, runtime=runtime, provider=provider)
    created, is_new = service.create("canvas", "director", "request", draft)
    assert is_new
    assert created["snapshot"]["segments"][0]["prompt"] == "Original opening"
    assert [item["character_id"] for item in created["snapshot"]["references"]] == (
        ["hero", "hero"] if reference_mode else [])
    for image_id in originals:
        (output / f"{image_id}.png").write_bytes(png("white"))
    attempt_id = created["id"]
    if selected:
        rejected = await service.resume(attempt_id)
        assert rejected["stage"] == "failed"
        assert rejected["failed_stage"] == "submitting"
        assert rejected["optimized"]["segments"][0]["prompt"] != "Original opening"
        restarted = DirectorService(ctx, runtime=runtime, provider=provider)
        retry, is_new_retry = restarted.retry(attempt_id)
        assert is_new_retry
        assert retry["parent_attempt_id"] == attempt_id
        assert retry["frozen_techniques"] == rejected["frozen_techniques"]
        assert retry["optimized"] == rejected["optimized"]
        attempt_id = retry["id"]
        service = restarted
    queued = await service.resume(attempt_id)
    assert queued["stage"] == "queued"
    assert provider.submissions == (2 if selected else 1)
    expected_uploads = (originals if mode == "fl2v" else
                        {key: originals[key] for key in ("base", "costume")})
    assert provider.uploaded == expected_uploads
    assert [call[0]["segment_id"] for call in runtime.calls] == ["opening", "closing"]
    assert runtime.calls[0][0]["mode"] == {
        "i2v": "i2va", "fl2v": "fl2va", "ref_only": "ref2va",
    }[mode]
    assert ["technique" in call[0] for call in runtime.calls] == [selected, False]
    assert all("technique" not in neighbor for call, _ in runtime.calls
               for neighbor in call["neighbor_segments"])
    if selected:
        card = resolve_technique("two-person-gaze", "1.0.0")
        frozen = queued["frozen_techniques"]
        assert list(frozen) == ["opening"]
        assert frozen["opening"]["card"]["id"] == card.id
        assert frozen["opening"]["card"]["version"] == card.version
        assert frozen["opening"]["card"]["content_hash"] == card.content_hash
        assert frozen["opening"]["card"]["sources"] == [
            source.model_dump(mode="json") for source in card.sources]
        assert frozen["opening"]["projection"] == runtime.calls[0][0]["technique"]
    else:
        assert not queued.get("frozen_techniques")
    assert [call[1] for call in runtime.calls] == (
        [(originals["base"], originals["costume"])] * 2 if reference_mode else
        [(originals["base"], originals["last"]) if mode == "fl2v"
         else (originals["base"],), (originals["costume"],)])
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
    assert ("two-person gaze intent" in timeline["shots"][0]["prompt"]) == selected
    assert "two-person gaze intent" not in timeline["shots"][1]["prompt"]
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
        assert timeline["shots"][0]["endImage"] == (
            {"imageFile": "frozen-last"} if mode == "fl2v" else None)
        assert timeline["shots"][1]["endImage"] is None
    restarted = DirectorService(ctx, runtime=runtime, provider=provider)
    assert len(restarted.list("canvas", "director")) == (2 if selected else 1)
    assert restarted.get(attempt_id)["snapshot"]["segments"][1]["prompt"] == "Original closing"
    assert restarted.get(attempt_id).get("frozen_techniques") == queued.get("frozen_techniques")
    assert provider.submissions == (2 if selected else 1)
    assert len(runtime.calls) == 2  # Cached retry needs no second optimization.


@pytest.mark.asyncio
async def test_real_video_completion_probe_is_optional(tmp_path, video_bytes):
    output = tmp_path / "output"
    output.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (output / "start.png").write_bytes(png("red"))
    ctx = SimpleNamespace(project_id="project-1", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=runtime_dir)
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="one", prompt="Original scene", duration_seconds=5,
                        first_frame=DirectorImage(image_id="start", url="start.png")),
    ))
    runtime = StructuredRuntime()
    provider = CompilingProvider(video_bytes)
    service = DirectorService(ctx, runtime=runtime, provider=provider)
    created, _ = service.create("canvas", "director", "request", draft)
    assert (await service.resume(created["id"]))["stage"] == "queued"
    restarted = DirectorService(ctx, runtime=runtime, provider=provider)
    completed = await restarted.resume(created["id"])
    assert completed["stage"] == "completed"
    assert urlsplit(completed["result_url"]).path.endswith(".mp4")
    assert provider.submissions == 1
