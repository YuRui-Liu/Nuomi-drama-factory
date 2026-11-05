"""Runner-backed acceptance of managed storyboard, H3 video, and composition.

The storyboard goes through the public run admission, NodeAdapter, Scheduler,
inline task backend, real narrative-group worker, and real grid splitter. The
GRSAI image transport is replaced with a deterministic PNG response. The H3
runner and local episode composer are also real; model prompt optimization and
the paid H3 video transport are deterministic test boundaries. All project
state and media live under pytest's temporary directory.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


PROJECT = "studio-media-acceptance"
REVISION = "rev-studio-media-1"
ORCHESTRATION = f"/api/v1/projects/{PROJECT}/freezone/orchestration"


class _CanonicalStore:
    """Small read-only store fixture for the one source span in this project."""

    async def get_beats_as_dicts(self, _episode):
        return [{
            "id": "span-1",
            "beat_number": 1,
            "scene_name": "old-station",
            "time_of_day": "night",
            "visual_description": "A traveler pauses beneath the station sign.",
            "duration_seconds": 4,
        }]

    def get_all_characters(self):
        return []

    async def close(self):
        return None


@pytest.fixture
def media_project(tmp_path, monkeypatch):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import (
        creation,
        director_workspace,
        freezone,
        freezone_orchestration,
        generation,
        narrative_groups,
    )
    from novelvideo.api import deps
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.ports.local import register_local_ports
    from novelvideo.ports.local.tasks import InlineTaskBackend
    from novelvideo.ports.registry import register_port

    # The current project workspace projection places managed canvas state and
    # production media in one project directory; keeping both roots identical
    # makes this an isolated, self-contained project like the app runtime.
    state_root = tmp_path / "project"
    output_root = state_root
    runtime_root = state_root / "runtime"
    state_root.mkdir()
    (state_root / "project_config.json").write_text(
        json.dumps({"creation_mode": "agent_canvas_v1"}), encoding="utf-8",
    )
    ctx = SimpleNamespace(
        project_id=PROJECT,
        project_name="acceptance",
        owner_project_label="alice/acceptance",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        effective_role="owner",
        home_node_id="local",
        state_dir=state_root,
        output_dir=output_root,
        runtime_dir=runtime_root,
        is_home_node=True,
    )
    resolved = SimpleNamespace(
        ctx=ctx,
        username="alice",
        project_name="acceptance",
        project_dir=output_root,
        output_dir=str(output_root),
        state_dir=str(state_root),
        runtime_dir=str(runtime_root),
    )

    async def resolve_context(*, user, project_id=None, project_name=None,
                              required_role="viewer"):
        assert project_id in (None, PROJECT)
        return ctx

    async def resolve_scope(project, user, *, required_role="viewer"):
        assert project == PROJECT
        return resolved

    async def resolve_freezone(project, user, *, required_role="editor"):
        assert project == PROJECT
        return ctx, "alice", "acceptance", output_root, str(output_root)

    async def make_canonical_store(_ctx):
        return _CanonicalStore()

    monkeypatch.setattr(creation, "resolve_project_context", resolve_context)
    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_freezone)
    monkeypatch.setattr(freezone_orchestration, "_resolve_freezone_project", resolve_freezone)
    monkeypatch.setattr(deps, "resolve_project_context", resolve_context)
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve_scope)
    monkeypatch.setattr(director_workspace, "resolve_project_scope", resolve_scope)
    monkeypatch.setattr(generation, "resolve_project_scope", resolve_scope)
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", make_canonical_store)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", make_canonical_store)

    # Register the production local ports against this test process, then
    # install a fresh real inline worker backend for the temporary project.
    register_local_ports()
    backend = InlineTaskBackend(execution_owner_id="inline:studio-media-acceptance")
    register_port("task_backend", backend)

    scheduler_tasks = []

    def launch(project_dir, run):
        task = asyncio.create_task(
            Scheduler(RunStore(project_dir), NodeAdapter(project_dir)).drive(
                run["id"], poll_seconds=0.02,
            )
        )
        scheduler_tasks.append(task)

    monkeypatch.setattr(freezone_orchestration, "launch", launch)

    media_store = SimpleNamespace(
        get_provider=lambda provider_id: SimpleNamespace(
            id=provider_id,
            provider_type="grsai",
            credential_ref="env://acceptance-grsai-key",
            enabled=True,
            max_concurrency=1,
            capability_limits={},
            queue_limit=1,
            base_url="https://grsai.invalid",
            model="gpt-image-2",
        ),
        get_runninghub_workflows=lambda: SimpleNamespace(
            video_minimax_h3_ref_max_images=2,
            video_minimax_h3_ref="2096502793044582401",
        ),
    )
    credentials = SimpleNamespace(resolve=lambda _reference: "acceptance-key")
    monkeypatch.setattr(narrative_groups, "get_media_capability_store", lambda: media_store)
    monkeypatch.setattr(narrative_groups, "get_media_credential_resolver", lambda: credentials)
    monkeypatch.setattr(deps, "get_media_capability_store", lambda: media_store)
    monkeypatch.setattr(deps, "get_media_credential_resolver", lambda: credentials)

    app = FastAPI()
    app.include_router(freezone_orchestration.router, prefix="/api/v1")
    app.include_router(creation.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "user-1", "username": "alice",
    }
    with TestClient(app) as client:
        yield client, state_root, output_root, ctx, media_store, scheduler_tasks

    # InlineTaskBackend owns executors for its local queues. No work should be
    # left after all run records reached terminal state.
    for lane in backend._lanes.values():
        lane.executor.shutdown(wait=True)


def _save_director_plan(output_root: Path) -> None:
    from novelvideo.director_plan.models import (
        DirectorPlanRevision,
        NarrativeGroupPlan,
        StyleProjections,
        StyleSnapshot,
        ValidationReport,
    )
    from novelvideo.director_plan.store import DirectorPlanStore
    from tests.shot_continuity.test_cinematography import shot

    cinematography = shot().cinematography
    production_shot = shot(
        id="shot-1",
        source_span_ids=("span-1",),
        subject="traveler",
        action="pauses beneath the station sign",
        visible_start_state="walking through rain",
        visible_end_state="looking up at the sign",
        asset_requirements=(
            {"kind": "character_identity", "entity_key": "lin"},
        ),
        cinematography=cinematography,
    )
    group = NarrativeGroupPlan(
        id="group-1",
        ordinal=1,
        source_span_ids=("span-1",),
        scene_anchor="old-station",
        time_anchor="night",
        objective="recognize the station sign",
        visible_turn="the traveler stops and looks up",
        relation_to_previous="single",
        shots=(production_shot,),
    )
    style = StyleSnapshot(
        snapshot_id="style-1",
        style_id="cinematic-realism",
        style_version="1",
        catalog_hash="catalog-hash",
        style_hash="style-hash",
        projections=StyleProjections(
            director="restrained cinematic realism",
            image="rain-dark cinematic realism",
            video="rain-dark cinematic realism",
            panel_tag="grounded camera language",
        ),
    )
    revision = DirectorPlanRevision(
        revision_id=REVISION,
        episode=1,
        status="review_required",
        source_script_hash="sha256:test-screenplay",
        director_model="acceptance-fixture",
        prompt_version="acceptance-v1",
        project_style_snapshot_id="style-1",
        project_style_snapshot=style,
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc),
    )
    plans = DirectorPlanStore(output_root)
    plans.save(revision)
    plans.activate(1, REVISION)


def _add_graph(client: TestClient, canvas_id: str) -> None:
    nodes = [
        ("idea", "ideationNode", {"operation": "generate", "episodeNumber": 1}),
        ("screenplay", "screenplayNode", {"operation": "plan", "episodeNumber": 1}),
        ("assets", "imageGenNode", {
            "operation": "generate", "episodeNumber": 1, "artifactKind": "asset",
        }),
        ("storyboard-table", "scriptNode", {
            "operation": "project", "episodeNumber": 1,
            "directorRevisionId": REVISION,
        }),
        ("storyboard", "narrativeGroupStoryboardNode", {
            "operation": "render", "episodeNumber": 1,
            "groupId": "group-1", "directorRevisionId": REVISION,
            "allowUnconstrained": True,
        }),
        ("video", "narrativeGroupVideoNode", {
            "operation": "video", "episodeNumber": 1,
            "groupId": "group-1", "directorRevisionId": REVISION,
            "dialogueSource": "h3_native",
        }),
        ("composition", "episodeComposeNode", {
            "operation": "compose", "episodeNumber": 1,
            "directorRevisionId": REVISION, "productionGroupIds": ["group-1"],
            "addBgm": False, "addSubtitles": False, "requiresAudio": False,
            "resolution": "180x320",
        }),
    ]
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/patch",
        json={
            "base_revision": 1,
            "request_id": "runner-backed-media-graph",
            "operations": [{
                "op": "add_node",
                "node": {
                    "id": node_id,
                    "type": node_type,
                    "position": {"x": index * 260, "y": 0},
                    "data": data,
                },
            } for index, (node_id, node_type, data) in enumerate(nodes)],
        },
    )
    assert response.status_code == 200, response.text


def _run(client: TestClient, canvas_id: str, target: str, request_id: str) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/runs",
        json={"canvas_id": canvas_id, "target": target, "request_id": request_id},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _wait_run(client: TestClient, run_id: str, *, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{ORCHESTRATION}/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["data"]
        if run["status"] in {"succeeded", "failed", "blocked", "cancelled"}:
            assert run["status"] == "succeeded", json.dumps(run, ensure_ascii=False, indent=2)
            return run
        time.sleep(0.02)
    pytest.fail(f"run {run_id} did not finish; last state: {run}")


def _progress_complete(client, canvas_id: str, stage: str, input_hash: str, outputs: dict) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/complete",
        json={"stage": stage, "input_hash": input_hash, "outputs": outputs},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _approve(client, canvas_id: str, stage: str, checkpoint: dict) -> dict:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/approve",
        json={"stage": stage, "input_hash": checkpoint["approval_hash"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["stages"][stage]


def _configure_review(client, canvas_id: str) -> None:
    response = client.post(
        f"{ORCHESTRATION}/canvases/{canvas_id}/progress/001/configure",
        json={"mode": "review"},
    )
    assert response.status_code == 200, response.text


def _tiny_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (96, 160), "#24384a").save(output, format="PNG")
    return output.getvalue()


def _video_registry():
    from novelvideo.media_capabilities.models import RunningHubWorkflowSettingsKey
    from novelvideo.media_capabilities.video.parameters import (
        VideoWorkflowParameterDefinition,
        VideoWorkflowParameterOption,
    )
    from novelvideo.media_capabilities.video.workflow_registry import (
        VideoWorkflowDefinition,
        VideoWorkflowRegistry,
        VideoWorkflowScene,
    )

    return VideoWorkflowRegistry((VideoWorkflowDefinition(
        id="runninghub:minimax-h3",
        label="Acceptance MiniMax H3",
        provider="runninghub",
        adapter_key="minimax-h3",
        workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3,
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=("i2va", "fl2va"),
        parameters=(VideoWorkflowParameterDefinition(
            key="resolution",
            label="Resolution",
            default="720p",
            options=(VideoWorkflowParameterOption(value="720p", label="720p"),),
        ),),
    ),))


def _install_provider_fakes(monkeypatch, output_root: Path) -> dict[str, list]:
    from novelvideo.api.routes import narrative_groups
    from novelvideo.media_capabilities.runtime import grsai_execution
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.reference_decisions import ReferenceDecisionSnapshot
    from novelvideo.media_capabilities.video.h3_storyboard_context import (
        StoryboardObservation,
        StoryboardPromptDecision,
    )
    from tests.media_capabilities.video.test_h3_episode_pack import _plan
    from tests.test_task_narrative_group_video_runner import _optimizer_result

    observed = {"images": [], "videos": []}

    async def empty_reference_snapshot(*_args, **_kwargs):
        return ReferenceDecisionSnapshot(
            id="refsnap-empty",
            schema_version="narrative-reference-decision/v2",
            images=(),
            ignored_requirement_ids=(),
        )

    monkeypatch.setattr(narrative_groups, "build_planned_reference_snapshot", empty_reference_snapshot)
    def registry_factory(_store, _resolver):
        return _video_registry()
    monkeypatch.setattr(narrative_groups, "build_video_workflow_registry", registry_factory)
    monkeypatch.setattr(narrative_group_video, "build_video_workflow_registry", registry_factory)

    async def fake_image_provider(_runtime, request, **_kwargs):
        observed["images"].append(request)
        return SimpleNamespace(task_id="fixture-grsai-task", content=_tiny_png())

    monkeypatch.setattr(grsai_execution, "execute_grsai_generation", fake_image_provider)

    class OfflinePromptOptimizer:
        async def optimize(self, episode_input, *, storyboard_images=()):
            return SimpleNamespace(segments=tuple(
                SimpleNamespace(
                    segment_id=item.segment_id,
                    storyboard_decision=StoryboardPromptDecision(
                        status="ready",
                        plan=_plan(),
                        observations=tuple(StoryboardObservation(
                            image_label=image.label,
                            framing="medium shot",
                            orientation="subject faces camera",
                            spatial_relations="subject centered in frame",
                        ) for image in storyboard_images),
                        conflicts=(),
                        required_starting_facts_status="verified",
                    ),
                    **vars(_optimizer_result(
                        "The traveler pauses beneath the sign and looks up.",
                        segment=item.source_segment,
                    )),
                )
                for item in episode_input.segments
            ))

    monkeypatch.setattr(
        narrative_group_video,
        "create_h3_episode_pack_optimizer",
        lambda **_kwargs: OfflinePromptOptimizer(),
    )

    async def fake_h3_provider(
        _ctx, *, segments, output_path, aspect_ratio, resolution,
        frozen_frames=None, on_provider_submitted=None,
    ):
        del segments, aspect_ratio, frozen_frames
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg is required for runner-backed media acceptance")
        width, height = 720, 1280
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        observed["videos"].append({"resolution": resolution, "path": target})
        if on_provider_submitted is not None:
            await on_provider_submitted("fixture-h3-task")
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=24:d=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(target),
            ],
            check=True,
            capture_output=True,
        )
        return H3GenerationResult(
            output_path=str(target),
            provider_task_id="fixture-h3-task",
            actual_mode="i2va",
            actual_output={"width": width, "height": height},
        )

    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", fake_h3_provider)
    monkeypatch.setattr(narrative_group_video, "_workflow_definition_for_payload", lambda _payload: (
        _video_registry().resolve("runninghub:minimax-h3", "narrative_group")
    ))
    return observed


def test_reviewed_storyboard_flows_through_worker_h3_and_real_episode_composer(
    media_project, monkeypatch,
):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for runner-backed media acceptance")

    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.narrative_groups.service import load_materialized_groups
    from novelvideo.task_backend.runners import narrative_group_video

    client, state_root, output_root, ctx, _media_store, _scheduler_tasks = media_project
    provider_calls = _install_provider_fakes(monkeypatch, output_root)
    episode_response = client.post(
        f"/api/v1/projects/{PROJECT}/creation/episodes",
        json={"request_id": "studio-media-episode", "title": "旧站台"},
    )
    assert episode_response.status_code == 200, episode_response.text
    canvas_id = episode_response.json()["data"]["canvas_id"]
    _save_director_plan(output_root)
    _add_graph(client, canvas_id)
    _configure_review(client, canvas_id)

    script = client.put(
        f"{ORCHESTRATION}/canvases/{canvas_id}/documents",
        json={"path": "episodes/001/script.md", "text": "雨夜，旅人停在舊站牌下。"},
    )
    assert script.status_code == 200, script.text
    screenplay = _progress_complete(
        client, canvas_id, "screenplay", script.json()["data"]["hash"],
        {"script": script.json()["data"]["path"], "screenplay": script.json()["data"]["path"]},
    )["stages"]["screenplay"]
    assert screenplay["status"] == "awaiting_review"
    _approve(client, canvas_id, "screenplay", screenplay)

    asset_doc = client.put(
        f"{ORCHESTRATION}/canvases/{canvas_id}/documents",
        json={
            "path": "episodes/001/assets/reuse-checklist.md",
            "text": "沿用已确认的旅人形象，雨夜站台保持一致。",
        },
    )
    assert asset_doc.status_code == 200, asset_doc.text
    assets = _progress_complete(
        client, canvas_id, "assets", asset_doc.json()["data"]["hash"],
        {"reuse_checklist": asset_doc.json()["data"]["path"]},
    )["stages"]["assets"]
    assert assets["status"] == "awaiting_review"
    _approve(client, canvas_id, "assets", assets)

    # The local projection adapter creates the table rows from the fixed active
    # DirectorPlan; persist exactly those rows beside the worker's actual grid.
    table_run = _run(client, canvas_id, "storyboard-table", "table-project-run")
    projected = _wait_run(client, table_run["id"])
    projection = projected["attempts"]["storyboard-table"][-1]["result"]["workflowResult"]
    assert projection["directorRevisionId"] == REVISION
    table_rel = "episodes/001/storyboard/table.json"
    table_path = output_root / table_rel
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(
        json.dumps({"episode": 1, "directorRevisionId": REVISION, "rows": projection["rows"]}, indent=2),
        encoding="utf-8",
    )
    assert projection["rows"] and projection["rows"][0]["group_id"] == "group-1"
    assert projection["rows"][0]["director"]["segment_index"] == 1

    storyboard_run = _run(client, canvas_id, "storyboard", "real-storyboard-worker-run")
    storyboard_result = _wait_run(client, storyboard_run["id"])
    assert storyboard_result["attempts"]["storyboard"][-1]["status"] == "succeeded"
    assert len(provider_calls["images"]) == 1

    [group] = load_materialized_groups(output_root, 1)
    render = group.stages["render"]
    grid_path = Path(render.grid_asset)
    assert grid_path.is_file() and grid_path.stat().st_size > 0
    assert len(render.cell_assets) == 1
    cell_path = Path(render.cell_assets[0]["path"])
    assert cell_path.is_file() and Image.open(cell_path).size[0] > 0
    grid_rel = grid_path.relative_to(output_root).as_posix()
    storyboard = _progress_complete(
        client, canvas_id, "storyboard", projection["projectionHash"],
        {"table": table_rel, "grid": grid_rel},
    )["stages"]["storyboard"]
    assert storyboard["status"] == "awaiting_review"

    blocked_video = client.post(
        f"{ORCHESTRATION}/runs",
        json={
            "canvas_id": canvas_id,
            "target": "video",
            "request_id": "blocked-before-storyboard-review",
        },
    )
    assert blocked_video.status_code == 409, blocked_video.text
    blocked_detail = blocked_video.json()["detail"]
    assert blocked_detail["code"] == "production_stage_blocked"
    assert blocked_detail["stage"] == "video"
    assert blocked_detail["required_stage"] == "storyboard"
    assert blocked_detail["status"] == "awaiting_review"
    assert provider_calls["videos"] == []

    _approve(client, canvas_id, "storyboard", storyboard)

    video_run = _run(client, canvas_id, "video", "real-h3-worker-run")
    video_result = _wait_run(client, video_run["id"])
    assert video_result["attempts"]["video"][-1]["status"] == "succeeded"
    assert len(provider_calls["videos"]) == 1
    [group] = load_materialized_groups(output_root, 1)
    video_stage = group.stages["video"]
    group_video = Path(video_stage.video_asset)
    manifest_path = Path(video_stage.manifest_asset)
    assert group_video.is_file() and manifest_path.is_file()
    manifest = narrative_group_video.load_h3_director_manifest(manifest_path)
    assert manifest.status == "completed"
    assert manifest.entries[0].status == "completed"
    assert Path(manifest.entries[0].physical_video).is_file()

    video_rel = group_video.relative_to(output_root).as_posix()
    video_checkpoint = _progress_complete(
        client, canvas_id, "video", storyboard["approval_hash"], {"video": video_rel},
    )["stages"]["video"]
    assert video_checkpoint["status"] == "ready"

    # This episode has no independent dialogue/music input; its H3-native
    # audio is already part of the generated group video.
    compose_run = _run(client, canvas_id, "composition", "real-compose-worker-run")
    compose_result = _wait_run(client, compose_run["id"])
    compose_attempt = compose_result["attempts"]["composition"][-1]
    final_path = Path(output_root) / "videos" / "episodes" / "ep001_final.mp4"
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
    assert {item["codec_type"] for item in json.loads(probe.stdout)["streams"]} == {
        "audio", "video",
    }
    assert compose_attempt["status"] == "succeeded"

    final_rel = final_path.relative_to(output_root).as_posix()
    completed = _progress_complete(
        client, canvas_id, "composition", video_checkpoint["approval_hash"],
        {"final": final_rel},
    )
    assert completed["stages"]["composition"]["status"] == "ready"
    retained = final_path.read_bytes()
    assert retained and final_path.read_bytes() == retained
    assert state_root.joinpath("creation", "progress", "001.json").is_file()
    canvas = read_canvas(output_root, canvas_id)
    compose_projection = next(
        node for node in canvas["nodes"] if node["id"] == "composition"
    )["data"]["orchestration"]
    assert compose_projection["status"] == "succeeded"
