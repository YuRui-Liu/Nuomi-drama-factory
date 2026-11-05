"""Acceptance checks for review-gated, managed episode production.

Admission tests use the public creation, canvas, document, progress, and run
HTTP APIs. Their launch spy prevents model/provider workers from starting. The
full checkpoint path separately creates deterministic local media and runs the
real FFmpeg episode composer before recording its output through the progress
API; it does not claim to exercise paid image or video generation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT = "studio-acceptance"
ORCHESTRATION = f"/api/v1/projects/{PROJECT}/freezone/orchestration"


@pytest.fixture
def studio_client(tmp_path, monkeypatch):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import creation, freezone_orchestration

    state_root = tmp_path / "state"
    output_root = tmp_path / "output"
    state_root.mkdir()
    output_root.mkdir()
    (state_root / "project_config.json").write_text(
        json.dumps({"creation_mode": "agent_canvas_v1"}), encoding="utf-8",
    )

    ctx = SimpleNamespace(
        project_id=PROJECT,
        project_name="acceptance",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        effective_role="owner",
        home_node_id="local",
        state_dir=state_root,
        output_dir=output_root,
        runtime_dir=state_root / "runtime",
        is_home_node=True,
    )

    async def resolve_creation_context(*, user, project_id=None, project_name=None,
                                        required_role="viewer"):
        assert project_id == PROJECT
        return ctx

    async def resolve_media_project(project, user, *, required_role="editor"):
        assert project == PROJECT
        return ctx, "alice", "acceptance", output_root, output_root

    monkeypatch.setattr(creation, "resolve_project_context", resolve_creation_context)
    monkeypatch.setattr(freezone_orchestration, "_resolve_media_project", resolve_media_project)

    launches = []
    monkeypatch.setattr(
        freezone_orchestration,
        "launch",
        lambda root, run: launches.append((root, run["id"])),
    )

    app = FastAPI()
    app.include_router(freezone_orchestration.router, prefix="/api/v1")
    app.include_router(creation.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "user-1", "username": "alice"}
    with TestClient(app) as client:
        yield client, state_root, output_root, launches


def _create_episode(client: TestClient) -> dict:
    response = client.post(
        f"/api/v1/projects/{PROJECT}/creation/episodes",
        json={"request_id": "acceptance-episode-create", "title": "旧站台"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _add_managed_nodes(client: TestClient, canvas_id: str, *, requires_audio: bool = False) -> None:
    nodes = [
        {
            "id": "idea",
            "type": "ideationNode",
            "position": {"x": 0, "y": 0},
            "data": {
                "operation": "generate",
                "episodeNumber": 1,
                "briefId": "accepted-brief",
                "briefRevisionId": "brief-r1",
            },
        },
        {
            "id": "screenplay",
            "type": "screenplayNode",
            "position": {"x": 300, "y": 0},
            "data": {
                "operation": "plan",
                "episodeNumber": 1,
                "documentId": "screenplay-document",
                "documentRevisionId": "screenplay-r1",
            },
        },
        {
            "id": "assets",
            "type": "imageGenNode",
            "position": {"x": 600, "y": 0},
            "data": {
                "operation": "generate",
                "episodeNumber": 1,
                "artifactKind": "asset",
                "prompt": "A rain-dark station sign, restrained period detail.",
                "model": "gpt-image-2",
                "size": "2K",
                "aspectRatio": "1:1",
            },
        },
        {
            "id": "storyboard",
            "type": "narrativeGroupStoryboardNode",
            "position": {"x": 900, "y": 0},
            "data": {
                "operation": "render",
                "episodeNumber": 1,
                "groupId": "group-1",
                "directorRevisionId": "director-r1",
            },
        },
        {
            "id": "video",
            "type": "narrativeGroupVideoNode",
            "position": {"x": 1200, "y": 0},
            "data": {
                "operation": "video",
                "episodeNumber": 1,
                "groupId": "group-1",
                "directorRevisionId": "director-r1",
            },
        },
        {
            "id": "dialogue-audio",
            "type": "audioNode",
            "position": {"x": 1200, "y": 240},
            "data": {"operation": "group_dubbing", "episodeNumber": 1},
        },
        {
            "id": "dialogue-audio-retry",
            "type": "audioNode",
            "position": {"x": 1200, "y": 480},
            "data": {"operation": "group_dubbing", "episodeNumber": 1},
        },
        {
            "id": "dialogue-audio-tamper-check",
            "type": "audioNode",
            "position": {"x": 1200, "y": 720},
            "data": {"operation": "group_dubbing", "episodeNumber": 1},
        },
        {
            "id": "composition",
            "type": "episodeComposeNode",
            "position": {"x": 1500, "y": 0},
            "data": {
                "operation": "compose",
                "episodeNumber": 1,
                "directorRevisionId": "director-r1",
                "productionGroupIds": ["group-1"],
                "addBgm": False,
                "requiresAudio": requires_audio,
            },
        },
    ]
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/patch",
        json={
            "base_revision": 1,
            "request_id": "acceptance-production-graph",
            "operations": [
                {"op": "add_node", "node": node}
                for node in nodes
            ],
        },
    )
    assert response.status_code == 200, response.text


def _configure(client: TestClient, canvas_id: str, **body) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/configure",
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _start(client: TestClient, canvas_id: str, target: str, request_id: str):
    return client.post(
        f"{ORCHESTRATION}/runs",
        json={"canvas_id": canvas_id, "target": target, "request_id": request_id},
    )


def _run_count(client: TestClient, canvas_id: str) -> int:
    response = client.get(f"{ORCHESTRATION}/canvases/{canvas_id}/runs")
    assert response.status_code == 200, response.text
    return len(response.json()["data"])


def _write_document(
    client: TestClient, canvas_id: str, path: str, text: str, expected_hash=None,
) -> dict:
    response = client.put(
        f"{ORCHESTRATION}/canvases/{canvas_id}/documents",
        json={
            "path": path,
            "text": text,
            "expected_hash": expected_hash,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _write_screenplay(client: TestClient, canvas_id: str, text: str, expected_hash=None) -> dict:
    return _write_document(
        client, canvas_id, "episodes/001/script.md", text, expected_hash,
    )


def _complete_stage(
    client: TestClient, canvas_id: str, stage: str, input_hash: str, outputs: dict,
) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/complete",
        json={"stage": stage, "input_hash": input_hash, "outputs": outputs},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _approve_stage(client: TestClient, canvas_id: str, stage: str, checkpoint: dict) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/approve",
        json={"stage": stage, "input_hash": checkpoint["approval_hash"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["stages"][stage]


def _complete_screenplay(client: TestClient, canvas_id: str, document: dict) -> dict:
    return _complete_stage(
        client, canvas_id, "screenplay", document["hash"],
        {"script": document["path"], "screenplay": document["path"]},
    )


def _write_test_media(output_root: Path) -> dict[str, str]:
    """Create tiny, valid local fixtures for real composition and receipt checks."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for the local composition acceptance check")

    video_rel = "videos/beats/ep001/beat_01.mp4"
    audio_rel = "audio/ep001/beat_01.mp3"
    video_path = output_root / video_rel
    audio_path = output_root / audio_rel
    video_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=180x320:r=24:d=1",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=1",
            "-c:a", "libmp3lame", "-q:a", "9", str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    table_rel = "episodes/001/storyboard/table.json"
    grid_rel = "episodes/001/storyboard/grid.png"
    table_path = output_root / table_rel
    grid_path = output_root / grid_rel
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(
        json.dumps({"episode": 1, "shots": [{"id": "shot-1", "beat": 1}]}, indent=2),
        encoding="utf-8",
    )
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5",
            "-i", str(video_path), "-frames:v", "1", "-update", "1", str(grid_path),
        ],
        check=True,
        capture_output=True,
    )
    return {"video": video_rel, "audio": audio_rel, "table": table_rel, "grid": grid_rel}


def test_managed_episode_runs_wait_for_review_and_current_inputs(studio_client):
    client, state_root, output_root, launches = studio_client
    episode = _create_episode(client)
    canvas_id = episode["canvas_id"]
    _add_managed_nodes(client, canvas_id)

    configured = _configure(client, canvas_id, mode="review")
    assert configured["mode"] == "review"
    assert configured["episode_id"] == "001"

    # Idea and screenplay work can start. The stage gate applies to downstream
    # production stages, and the run endpoint remains the admission point.
    idea = _start(client, canvas_id, "idea", "idea-run")
    assert idea.status_code == 202, idea.text
    screenplay = _start(client, canvas_id, "screenplay", "screenplay-run")
    assert screenplay.status_code == 202, screenplay.text

    run_count = _run_count(client, canvas_id)
    launch_count = len(launches)
    for target, expected_stage in (
        ("assets", "screenplay"),
        ("storyboard", "screenplay"),
        ("video", "screenplay"),
        ("dialogue-audio", "screenplay"),
        ("composition", "screenplay"),
    ):
        response = _start(client, canvas_id, target, f"blocked-{target}")
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "production_stage_blocked"
        assert detail["stage"] == {
            "assets": "assets", "storyboard": "storyboard", "video": "video",
            "dialogue-audio": "audio", "composition": "composition",
        }[target]
        assert detail["required_stage"] == expected_stage
        assert detail["status"] == "pending"
        assert detail["reason"] == "not_ready"
        assert _run_count(client, canvas_id) == run_count
        assert len(launches) == launch_count

    # The public document API writes the authored screenplay; the public
    # checkpoint API then requests its review. A pending review cannot advance
    # assets or dialogue until the displayed approval fingerprint is accepted.
    script = _write_screenplay(client, canvas_id, "夜班列车停在旧站台。林岚看見站牌上的名字。")
    review = _complete_screenplay(client, canvas_id, script)
    checkpoint = review["stages"]["screenplay"]
    assert checkpoint["status"] == "awaiting_review"
    assert checkpoint["approval_hash"]

    for target in ("assets", "dialogue-audio"):
        response = _start(client, canvas_id, target, f"review-blocked-{target}")
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["stage"] == ("assets" if target == "assets" else "audio")
        assert detail["required_stage"] == "screenplay"
        assert detail["status"] == "awaiting_review"
        assert detail["reason"] == "awaiting_review"
    assert len(launches) == launch_count

    approved = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/approve",
        json={"stage": "screenplay", "input_hash": checkpoint["approval_hash"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["stages"]["screenplay"]["status"] == "ready"

    # stop_after takes priority over auto mode, while a requested dialogue
    # track may run as soon as the screenplay is current and approved.
    policy = _configure(client, canvas_id, mode="auto", stop_after="screenplay")
    assert policy["stop_after"] == "screenplay"
    stopped = _start(client, canvas_id, "dialogue-audio", "audio-stop-after")
    assert stopped.status_code == 409, stopped.text
    assert stopped.json()["detail"]["reason"] == "stop_after"
    assert len(launches) == launch_count

    _configure(client, canvas_id, mode="auto", stop_after=None)
    audio = _start(client, canvas_id, "dialogue-audio", "audio-independent")
    assert audio.status_code == 202, audio.text
    assert len(launches) == launch_count + 1

    # A local screenplay edit invalidates its content-addressed receipt before
    # another run can be launched. Re-recording the revised source through the
    # public checkpoint API makes a retry admissible again.
    edited = _write_screenplay(
        client, canvas_id,
        "夜班列车停在旧站台。林岚认出站牌上的名字，决定留下等人。",
        expected_hash=script["hash"],
    )
    stale_audio = _start(client, canvas_id, "dialogue-audio", "audio-stale-script")
    assert stale_audio.status_code == 409, stale_audio.text
    stale_detail = stale_audio.json()["detail"]
    assert stale_detail["code"] == "production_stage_blocked"
    assert stale_detail["stage"] == "audio"
    assert stale_detail["required_stage"] == "screenplay"
    # The public document endpoint invalidates the screenplay receipt as part
    # of the edit, so admission reports pending/not_ready here.
    assert stale_detail["reason"] == "not_ready"
    assert len(launches) == launch_count + 1

    current_progress = client.get(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001",
    )
    assert current_progress.status_code == 200, current_progress.text
    assert current_progress.json()["data"]["stages"]["screenplay"]["status"] == "pending"

    retried = _complete_screenplay(client, canvas_id, edited)
    assert retried["stages"]["screenplay"]["status"] == "ready"
    replay = _complete_screenplay(client, canvas_id, edited)
    assert replay["stages"]["screenplay"]["status"] == "ready"
    retry_audio = _start(client, canvas_id, "dialogue-audio-retry", "audio-retry")
    assert retry_audio.status_code == 202, retry_audio.text
    assert len(launches) == launch_count + 2

    # The storyboard receipt contract is also exposed through the HTTP API:
    # a table-only result cannot be accepted in place of the required table+grid.
    partial_storyboard = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/complete",
        json={
            "stage": "storyboard",
            "input_hash": "table-only",
            "outputs": {"table": "episodes/001/storyboard/table.json"},
        },
    )
    assert partial_storyboard.status_code == 422, partial_storyboard.text
    assert "grid" in partial_storyboard.json()["detail"]["message"]

    assert state_root.joinpath("creation", "progress", "001.json").is_file()

    # An out-of-band edit cannot bypass the content-addressed receipt check.
    # Unlike the document API path above, this leaves the old receipt in place
    # so admission can detect the changed output bytes itself.
    script_path = output_root / edited["path"]
    script_path.write_text("临时外部修改。", encoding="utf-8")
    tampered = _start(
        client, canvas_id, "dialogue-audio-tamper-check", "audio-tampered-script",
    )
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["detail"]["reason"] == "output_changed"
    assert len(launches) == launch_count + 2
    tampered_progress = client.get(f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001")
    assert tampered_progress.status_code == 200, tampered_progress.text
    assert tampered_progress.json()["data"]["stages"]["screenplay"]["status"] == "pending"


def test_unbound_canvas_experiments_stay_outside_managed_episode_admission(studio_client):
    client, _state_root, _output_root, launches = studio_client
    created = client.post(
        f"{ORCHESTRATION}/canvases",
        json={"canvas_id": "unbound-experiment", "name": "Unbound experiment"},
    )
    assert created.status_code == 200, created.text

    patched = client.post(
        f"{ORCHESTRATION}/canvases/unbound-experiment/patch",
        json={
            "base_revision": 1,
            "request_id": "unbound-node",
            "operations": [{
                "op": "add_node",
                "node": {
                    "id": "local-check",
                    "type": "textAnnotationNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"operation": "validate", "content": "scratch"},
                },
            }],
        },
    )
    assert patched.status_code == 200, patched.text

    response = client.post(
        f"{ORCHESTRATION}/runs",
        json={"canvas_id": "unbound-experiment", "target": "local-check", "request_id": "scratch-run"},
    )
    assert response.status_code == 202, response.text
    assert len(launches) == 1


def test_complete_studio_path_reviews_storyboard_then_composes_real_media(
    studio_client, monkeypatch,
):
    """Exercise all public checkpoint transitions and retain actual compose output.

    Stage generation is deliberately not claimed here: ``launch`` is the
    admission spy from the fixture. The final video and independent audio are
    valid local FFmpeg fixtures, and the actual episode composer creates the
    accepted composition output.
    """
    from novelvideo.task_backend.runners import video as video_runner

    client, _state_root, output_root, launches = studio_client
    episode = _create_episode(client)
    canvas_id = episode["canvas_id"]
    _add_managed_nodes(client, canvas_id, requires_audio=True)
    _configure(client, canvas_id, mode="review")
    media = _write_test_media(output_root)

    script = _write_screenplay(client, canvas_id, "雨夜，林岚在旧站台认出那封信。")
    screenplay = _complete_screenplay(client, canvas_id, script)["stages"]["screenplay"]
    assert screenplay["status"] == "awaiting_review"
    _approve_stage(client, canvas_id, "screenplay", screenplay)

    asset_run = _start(client, canvas_id, "assets", "workflow-assets")
    assert asset_run.status_code == 202, asset_run.text
    assets_doc = _write_document(
        client, canvas_id, "episodes/001/assets/reuse-checklist.md",
        "沿用已确认的旧站台资产；雨夜反光保持一致。",
    )
    assets = _complete_stage(
        client, canvas_id, "assets", assets_doc["hash"],
        {"reuse_checklist": assets_doc["path"]},
    )["stages"]["assets"]
    assert assets["status"] == "awaiting_review"
    _approve_stage(client, canvas_id, "assets", assets)

    storyboard_run = _start(client, canvas_id, "storyboard", "workflow-storyboard")
    assert storyboard_run.status_code == 202, storyboard_run.text
    table_only = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/complete",
        json={
            "stage": "storyboard",
            "input_hash": "storyboard-r1",
            "outputs": {"table": media["table"]},
        },
    )
    assert table_only.status_code == 422, table_only.text
    assert "grid" in table_only.json()["detail"]["message"]
    storyboard = _complete_stage(
        client, canvas_id, "storyboard", "storyboard-r1",
        {"table": media["table"], "grid": media["grid"]},
    )["stages"]["storyboard"]
    assert storyboard["status"] == "awaiting_review"
    _approve_stage(client, canvas_id, "storyboard", storyboard)

    video_run = _start(client, canvas_id, "video", "workflow-video")
    assert video_run.status_code == 202, video_run.text
    video = _complete_stage(
        client, canvas_id, "video", storyboard["approval_hash"],
        {"video": media["video"]},
    )["stages"]["video"]
    assert video["status"] == "ready"

    compose_before_audio = _start(client, canvas_id, "composition", "compose-before-audio")
    assert compose_before_audio.status_code == 409, compose_before_audio.text
    blocked_audio = compose_before_audio.json()["detail"]
    assert blocked_audio["required_stage"] == "audio"
    assert blocked_audio["reason"] == "not_ready"

    audio_run = _start(client, canvas_id, "dialogue-audio", "workflow-dialogue-audio")
    assert audio_run.status_code == 202, audio_run.text
    audio = _complete_stage(
        client, canvas_id, "audio", script["hash"], {"audio": media["audio"]},
    )["stages"]["audio"]
    assert audio["status"] == "ready"

    compose_run = _start(client, canvas_id, "composition", "workflow-compose")
    assert compose_run.status_code == 202, compose_run.text

    monkeypatch.setattr(
        video_runner,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_args, **_kwargs: None),
    )
    composed = video_runner.run_compose_episode(
        {
            "project_id": PROJECT,
            "episode": 1,
            "payload": {
                "output_dir": str(output_root),
                "beats": [{"beat_number": 1}],
                "resolution": "180x320",
            },
        },
        SimpleNamespace(output_dir=output_root),
    )
    final_path = Path(composed["video_path"])
    assert final_path.is_file() and final_path.stat().st_size > 0
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type", "-of", "json", str(final_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]} == {
        "audio", "video",
    }
    final_rel = final_path.relative_to(output_root).as_posix()
    ready = _complete_stage(
        client, canvas_id, "composition", "compose-r1", {"final": final_rel},
    )
    assert ready["stages"]["composition"]["status"] == "ready"
    retained_final = final_path.read_bytes()

    edited_script = _write_screenplay(
        client, canvas_id,
        "雨夜，林岚认出旧站台上的字迹，决定等到天亮。",
        expected_hash=script["hash"],
    )
    invalidated = client.get(f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001")
    assert invalidated.status_code == 200, invalidated.text
    statuses = invalidated.json()["data"]["stages"]
    assert {stage: statuses[stage]["status"] for stage in statuses} == {
        "screenplay": "pending", "assets": "pending", "storyboard": "pending",
        "video": "pending", "audio": "pending", "composition": "pending",
    }
    assert final_path.read_bytes() == retained_final
    assert (output_root / media["video"]).is_file()
    assert (output_root / media["audio"]).is_file()
    assert len(launches) == 5

    retry = _complete_screenplay(client, canvas_id, edited_script)["stages"]["screenplay"]
    assert retry["status"] == "awaiting_review"
    _approve_stage(client, canvas_id, "screenplay", retry)
    resumed_audio = _start(
        client, canvas_id, "dialogue-audio-retry", "workflow-dialogue-retry",
    )
    assert resumed_audio.status_code == 202, resumed_audio.text


def test_composition_requires_an_audio_receipt_only_when_audio_is_requested():
    from novelvideo.creation.production_admission import composition_requires_audio

    silent_compose = {
        "id": "silent-compose",
        "type": "episodeComposeNode",
        "data": {"addBgm": False, "requiresAudio": False},
    }
    dialogue_compose = {
        "id": "dialogue-compose",
        "type": "episodeComposeNode",
        "data": {"includeDialogue": True},
    }
    music_compose = {
        "id": "music-compose",
        "type": "episodeComposeNode",
        "data": {"addBgm": True},
    }

    assert composition_requires_audio(silent_compose) is False
    assert composition_requires_audio(dialogue_compose) is True
    assert composition_requires_audio(music_compose) is True
