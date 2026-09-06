import json
import os
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from novelvideo.api.routes import narrative_groups
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoReferencePolicy,
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
)
from novelvideo.media_capabilities.models import RunningHubWorkflowSettingsKey
from novelvideo.narrative_groups.models import VideoReferenceItem
from novelvideo.narrative_groups.video_references import (
    MAX_VIDEO_REFERENCE_BYTES,
    ResolvedVideoReference,
    VideoReferenceCandidate,
    VideoReferencePreview,
    opaque_video_reference_id,
)
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterOption,
)
from novelvideo.narrative_groups.references import (
    GroupImageReference,
    GroupReferencePreview,
    GroupStyleReference,
)
from novelvideo.narrative_groups import service as narrative_group_service
from novelvideo.narrative_groups.service import advance_revision, record_stage_result, sidecar_path


class FakeStore:
    def __init__(self, beat_count=6):
        self.beat_count = beat_count

    async def get_beats_as_dicts(self, episode):
        return [
            {"id": f"beat-{index}", "beat_number": index}
            for index in range(1, self.beat_count + 1)
        ]


class FakeBackend:
    def __init__(self):
        self.calls = []

    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append((ctx, kwargs))
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id=f"task-{len(self.calls)}"),
            backend="inline",
            queue=kwargs["queue_kind"],
        )


class FailingBackend(FakeBackend):
    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append((ctx, kwargs))
        raise RuntimeError("queue unavailable")


def make_client(monkeypatch, tmp_path: Path, *, beat_count=6):
    ctx = SimpleNamespace(project_id="demo", output_dir=str(tmp_path), state_dir=str(tmp_path))
    resolved = SimpleNamespace(ctx=ctx, project_dir=tmp_path, output_dir=str(tmp_path))

    async def resolve(*args, **kwargs):
        return resolved

    async def store(*args, **kwargs):
        return FakeStore(beat_count)

    backend = FakeBackend()
    capability_store = SimpleNamespace(
        get_provider=lambda provider_id: SimpleNamespace(
            id=provider_id, provider_type="grsai", enabled=True
        ),
        get_runninghub_workflows=lambda: SimpleNamespace(
            video_minimax_h3_ref_max_images=2
        ),
    )
    credential_resolver = object()
    media_store_dependency = narrative_groups.get_media_capability_store
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        narrative_groups,
        "get_media_capability_store",
        lambda: capability_store,
    )
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id="runninghub:minimax-h3",
                    label="RunningHub MiniMax H3",
                    provider="runninghub",
                    adapter_key="minimax-h3",
                    workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3,
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("auto", "i2va", "fl2va"),
                    parameters=(
                        VideoWorkflowParameterDefinition(
                            key="resolution",
                            label="清晰度",
                            default="720p",
                            options=(
                                VideoWorkflowParameterOption(value="720p", label="720p"),
                                VideoWorkflowParameterOption(value="1080p", label="1080p", relative_cost="higher"),
                            ),
                        ),
                    ),
                ),
            )
        ),
    )
    app = FastAPI()
    app.include_router(narrative_groups.router, prefix="/api/v1")
    app.dependency_overrides[narrative_groups.get_api_user] = lambda: {
        "id": "user-1",
        "username": "tester",
    }
    app.dependency_overrides[media_store_dependency] = lambda: capability_store
    app.dependency_overrides[
        narrative_groups.get_media_credential_resolver
    ] = lambda: credential_resolver
    return TestClient(app), backend


def image_bytes(image_format="PNG", *, size=(8, 8)):
    output = BytesIO()
    Image.new("RGB", size, "#4477aa").save(output, format=image_format)
    return output.getvalue()


def install_h3_reference_registry(monkeypatch, *, max_images=2):
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id="runninghub:minimax-h3-ref",
                    label="RunningHub MiniMax H3 Ref",
                    provider="runninghub",
                    adapter_key="minimax-h3-ref",
                    workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3_REF,
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("auto", "i2va", "fl2va"),
                    reference_policy=VideoReferencePolicy(
                        required=True, min_images=1, max_images=max_images
                    ),
                ),
            )
        ),
    )


def prepare_render_frames(tmp_path: Path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    for index in range(1, 7):
        frame = frame_dir / f"beat-{index}.png"
        frame.write_bytes(image_bytes())
        cells.append({"beat_id": f"beat-{index}", "path": str(frame)})
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "render",
        expected_revision=1,
        status="completed",
        cell_assets=cells,
    )


def activate_director_plan(tmp_path: Path) -> None:
    group = NarrativeGroupPlan(
        id="director-group",
        ordinal=1,
        source_span_ids=("span-1", "span-2"),
        scene_anchor="hallway",
        time_anchor="night",
        objective="reach the door",
        visible_turn="the door opens",
        relation_to_previous="single",
        shots=(
            ShotPlan(
                id="shot-1",
                source_span_ids=("span-1",),
                subject="hero",
                action="opens the door",
                visible_start_state="closed",
                visible_end_state="open",
                duration_seconds=3,
            ),
        ),
    )
    revision = DirectorPlanRevision(
        revision_id="rev-api-active",
        episode=1,
        status="review_required",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)


def make_reference_preview(tmp_path: Path):
    character = tmp_path / "assets" / "characters" / "hero.png"
    scene = tmp_path / "assets" / "scenes" / "room.png"
    character.parent.mkdir(parents=True)
    scene.parent.mkdir(parents=True)
    character.write_bytes(b"character")
    scene.write_bytes(b"scene")
    return GroupReferencePreview(
        style=GroupStyleReference(id="style-opaque", name="cinematic", prompt="moody"),
        image_references=(
            GroupImageReference(
                id="char-opaque", kind="character", source_kind="identity",
                label="Hero", path=str(character), beat_numbers=(1,), first_appearance=1,
                character_name="Hero", identity_id="hero_casual",
            ),
            GroupImageReference(
                id="scene-opaque", kind="scene", source_kind="scene_master",
                label="Room", path=str(scene), beat_numbers=(2,), first_appearance=2,
                scene_id="scene_room",
            ),
        ),
        warnings=("preview warning",),
        asset_root=str(tmp_path / "assets"),
    )


def install_reference_resolver(monkeypatch, preview, calls):
    def resolve(project_dir, beats, stage="render"):
        calls.append((project_dir, beats, stage))
        return preview

    monkeypatch.setattr(narrative_groups, "resolve_group_reference_preview", resolve)


def test_get_migrates_old_episode_to_stable_groups(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    assert response.status_code == 200
    groups = response.json()["data"]
    assert [group["id"] for group in groups] == ["ng-01"]
    assert groups[0]["layout"] == {"rows": 2, "columns": 3, "capacity": 6}
    assert groups[0]["cell_to_beat"][0] == {"cell": 0, "beat_id": "beat-1"}


def test_get_projects_active_director_plan_fields(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)

    response = client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    assert response.status_code == 200
    [group] = response.json()["data"]
    assert group["id"] == "director-group"
    assert group["beat_ids"] == ["span-1", "span-2"]
    assert group["source_span_ids"] == ["span-1", "span-2"]
    assert group["shot_ids"] == ["shot-1"]
    assert group["objective"] == "reach the door"
    assert group["visible_turn"] == "the door opens"
    assert group["director_revision_id"] == "rev-api-active"


def test_rebuild_rejects_when_director_plan_is_active(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/rebuild"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "DIRECTOR_PLAN_ACTIVE",
            "message": "Active director plan controls narrative groups",
        }
    }


def test_generate_action_uses_stable_group_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch/generate"
    )

    assert response.status_code == 202
    task = response.json()["data"]
    assert task["scope"] == "group_ng-01_sketch_r1"
    assert task["metadata"]["revision"] == 1
    assert backend.calls[0][1]["payload"]["group_id"] == "ng-01"
    assert backend.calls[0][1]["payload"]["cell_to_beat"][0] == {
        "cell": 0,
        "beat_id": "beat-1",
    }
    from novelvideo.task_backend.registry import get_project_task_runner
    from novelvideo.task_backend.runners import narrative_group  # noqa: F401

    queued_task_type = backend.calls[0][1]["task_type"]
    assert get_project_task_runner(queued_task_type) is narrative_group.run_narrative_group_grid
    assert backend.calls[0][1]["payload"]["beats"][0]["id"] == "beat-1"
    assert backend.calls[0][1]["payload"]["provider_id"] == "grsai-main"
    assert backend.calls[0][1]["payload"]["model"] == "nano-banana-2"


def test_render_requires_completed_sketch_unless_explicitly_unconstrained(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    blocked = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate"
    )
    allowed = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    )

    assert blocked.status_code == 409
    assert allowed.status_code == 202
    assert len(backend.calls) == 1
    payload = backend.calls[0][1]["payload"]
    assert payload["constraint_mode"] == "unconstrained"
    assert payload["model"] == "gpt-image-2"
    assert payload["image_size"] == "1K"


def test_render_freezes_completed_sketch_revision_and_temporary_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "sketch")
    sketch = tmp_path / "grids" / "sketch.png"
    sketch.parent.mkdir(parents=True)
    sketch.write_bytes(b"sketch")
    record_stage_result(
        tmp_path, 1, "ng-01", "sketch", expected_revision=1,
        status="completed", grid_asset=str(sketch),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "provider_id": "grsai-alt",
            "model": "gpt-image-2-vip",
            "image_size": "4K",
        },
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert payload["provider_id"] == "grsai-alt"
    assert payload["model"] == "gpt-image-2-vip"
    assert payload["image_size"] == "4K"
    assert "image_size" not in payload["reference_selection"]
    assert payload["constraint_mode"] == "strong_sketch"
    assert payload["source_sketch_revision"] == 1
    assert payload["source_sketch_asset"] == str(sketch)


def test_render_rejects_resolution_unsupported_by_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "allow_unconstrained": True,
            "model": "gpt-image-2",
            "image_size": "2K",
        },
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_repeated_generate_is_idempotent_but_regenerate_advances_revision(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    first = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    ).json()["data"]
    repeated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    ).json()["data"]
    regenerated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"allow_unconstrained": True},
    ).json()["data"]

    assert first["scope"] == repeated["scope"] == "group_ng-01_render_r1"
    assert regenerated["scope"] == "group_ng-01_render_r2"


def test_unknown_group_returns_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/missing/sketch/generate"
    )

    assert response.status_code == 404


def test_put_video_plan_updates_units_and_enforces_cas(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    groups = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups"
    ).json()["data"]
    assert groups[0]["video_plan"]["revision"] == 1
    endpoint = (
        "/api/v1/projects/demo/episodes/1/narrative-groups/"
        "ng-01/video/plan"
    )
    body = {
        "expected_revision": 1,
        "units": [
            {"beat_ids": ["beat-1", "beat-2"]},
            {"beat_ids": ["beat-3"]},
            {"beat_ids": ["beat-4", "beat-5"]},
            {"beat_ids": ["beat-6"]},
        ],
    }

    accepted = client.put(endpoint, json=body)
    stale = client.put(endpoint, json=body)

    assert accepted.status_code == 200
    plan = accepted.json()["data"]["video_plan"]
    assert plan["revision"] == 2
    assert plan["source"] == "manual"
    assert [unit["mode"] for unit in plan["units"]] == [
        "fl2va",
        "i2va",
        "fl2va",
        "i2va",
    ]
    assert stale.status_code == 409


def test_put_video_plan_rejects_non_partition(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.put(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/plan",
        json={
            "expected_revision": 1,
            "units": [
                {"beat_ids": ["beat-1", "beat-3"]},
                {"beat_ids": ["beat-2"]},
                {"beat_ids": ["beat-4", "beat-5"]},
                {"beat_ids": ["beat-6"]},
            ],
        },
    )

    assert response.status_code == 422


def test_video_reference_preview_returns_safe_dto_and_canonical_thumbnail(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    identity = (
        tmp_path
        / "assets"
        / "characters"
        / "RealOwner"
        / "identities"
        / "identity-007.png"
    )
    identity.parent.mkdir(parents=True)
    identity.write_bytes(image_bytes())
    reference_id = opaque_video_reference_id(
        "character_identity", "identity-007"
    )

    async def preview(**kwargs):
        return VideoReferencePreview(
            revision=3,
            candidates=(
                VideoReferenceCandidate(
                    reference_id=reference_id,
                    source_kind="character_identity",
                    label="Age Variant",
                    subject_description="The real owner identity",
                    asset_id="identity-007",
                    character_name="RealOwner",
                ),
            ),
            references=(
                VideoReferenceItem(
                    reference_id=reference_id,
                    source_kind="character_identity",
                    label="Age Variant",
                    subject_description="The selected identity",
                    asset_id="identity-007",
                ),
            ),
            warnings=("one warning",),
            max_images=2,
        )

    monkeypatch.setattr(
        narrative_groups, "resolve_group_video_reference_preview", preview
    )
    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-preview"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "revision": 3,
        "max_images": 2,
        "candidates": [{
            "reference_id": reference_id,
            "source_kind": "character_identity",
            "label": "Age Variant",
            "subject_description": "The real owner identity",
            "thumbnail_url": (
                "/api/v1/projects/demo/media/assets/characters/RealOwner/"
                "identities/identity-007.png"
            ),
        }],
        "selected": [{
            "reference_id": reference_id,
            "subject_description": "The selected identity",
        }],
        "warnings": ["one warning"],
    }
    serialized = json.dumps(data)
    assert str(tmp_path) not in serialized
    assert "asset_id" not in serialized
    assert "temporary_upload_id" not in serialized

    missing = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/missing/"
        "video/reference-preview"
    )
    assert missing.status_code == 404


def test_video_reference_upload_normalizes_png_and_ignores_filename(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads",
        files={"file": ("../../outside.jpg", image_bytes("JPEG"), "image/jpeg")},
    )

    assert response.status_code == 200
    candidate = response.json()["data"]
    assert set(candidate) == {
        "reference_id", "source_kind", "label", "subject_description",
        "thumbnail_url",
    }
    assert candidate["source_kind"] == "temporary_upload"
    assert candidate["thumbnail_url"].startswith("/api/v1/projects/demo/media/")
    uploads = list(
        (tmp_path / "videos" / "ep001" / "narrative_groups" / "references" / "ng-01").glob("*.png")
    )
    assert len(uploads) == 1
    assert Image.open(uploads[0]).format == "PNG"
    assert not (tmp_path.parent / "outside.jpg").exists()


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"not-an-image", "image/png"),
        (b"", "image/png"),
        (b"x" * (MAX_VIDEO_REFERENCE_BYTES + 1), "image/png"),
        (image_bytes(), "image/gif"),
    ],
    ids=("bad-image", "empty", "too-large", "bad-mime"),
)
def test_video_reference_upload_rejects_invalid_files_without_partial_output(
    monkeypatch, tmp_path, content, content_type
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads",
        files={"file": ("input.png", content, content_type)},
    )

    assert response.status_code == 422
    upload_root = (
        tmp_path / "videos" / "ep001" / "narrative_groups" / "references" / "ng-01"
    )
    assert not upload_root.exists() or list(upload_root.iterdir()) == []


def test_video_reference_upload_rejects_images_over_pixel_limit(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(narrative_groups, "MAX_VIDEO_REFERENCE_PIXELS", 63)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads",
        files={"file": ("large.png", image_bytes(), "image/png")},
    )

    assert response.status_code == 422


def test_put_video_references_preserves_order_and_enforces_validation_and_cas(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = (
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/references"
    )
    upload_endpoint = (
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads"
    )
    first = client.post(
        upload_endpoint,
        files={"file": ("one.png", image_bytes(), "image/png")},
    ).json()["data"]
    second = client.post(
        upload_endpoint,
        files={"file": ("two.webp", image_bytes("WEBP"), "image/webp")},
    ).json()["data"]
    third = client.post(
        upload_endpoint,
        files={"file": ("three.png", image_bytes(), "image/png")},
    ).json()["data"]

    response = client.put(endpoint, json={
        "expected_revision": 0,
        "references": [
            {"reference_id": second["reference_id"], "subject_description": " Second "},
            {"reference_id": first["reference_id"], "subject_description": "First"},
        ],
    })
    assert response.status_code == 200
    assert response.json()["data"]["selected"] == [
        {"reference_id": second["reference_id"], "subject_description": "Second"},
        {"reference_id": first["reference_id"], "subject_description": "First"},
    ]
    assert client.put(endpoint, json={
        "expected_revision": 0,
        "references": [{"reference_id": first["reference_id"], "subject_description": "First"}],
    }).status_code == 409
    for references in (
        [],
        [
            {"reference_id": item["reference_id"], "subject_description": "Image"}
            for item in (first, second, third)
        ],
        [{"reference_id": "unknown", "subject_description": "Unknown"}],
        [{"reference_id": first["reference_id"], "subject_description": "  "}],
        [{"reference_id": first["reference_id"], "subject_description": "two\nlines"}],
    ):
        assert client.put(endpoint, json={
            "expected_revision": 1, "references": references,
        }).status_code == 422
    assert client.put(endpoint, json={
        "expected_revision": 1,
        "references": [{
            "reference_id": first["reference_id"],
            "subject_description": "First",
            "path": "/tmp/client-controlled.png",
        }],
    }).status_code == 422

    advance_revision(tmp_path, 1, "ng-01", "video")
    assert client.put(endpoint, json={
        "expected_revision": 1,
        "references": [{"reference_id": first["reference_id"], "subject_description": "First"}],
    }).status_code == 409


def test_put_video_references_validates_trimmed_description_and_top_level_extra(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    prefix = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video"
    candidate = client.post(
        f"{prefix}/reference-uploads",
        files={"file": ("one.png", image_bytes(), "image/png")},
    ).json()["data"]
    reference = {
        "reference_id": candidate["reference_id"],
        "subject_description": f"  {'x' * 500}  ",
    }

    accepted = client.put(
        f"{prefix}/references",
        json={"expected_revision": 0, "references": [reference]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["selected"][0]["subject_description"] == "x" * 500

    too_long = client.put(
        f"{prefix}/references",
        json={
            "expected_revision": 1,
            "references": [{**reference, "subject_description": "x" * 501}],
        },
    )
    top_level_extra = client.put(
        f"{prefix}/references",
        json={
            "expected_revision": 1,
            "references": [reference],
            "path": "/tmp/client-controlled.png",
        },
    )
    assert too_long.status_code == top_level_extra.status_code == 422


@pytest.mark.parametrize("failed_operation", ["write", "rename"])
@pytest.mark.skipif(os.name == "nt", reason="exercises POSIX writer failures")
def test_video_reference_upload_surfaces_safe_writer_failure_without_temp_files(
    monkeypatch, tmp_path, failed_operation
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group_root = (
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "references"
        / "ng-01"
    )

    if failed_operation == "write":
        monkeypatch.setattr(
            os,
            "write",
            lambda descriptor, content: (_ for _ in ()).throw(OSError("write failed")),
        )
    else:
        monkeypatch.setattr(
            os,
            "rename",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("rename failed")),
        )
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads",
        files={"file": ("one.png", image_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert failed_operation in response.json()["detail"]
    assert list(group_root.iterdir()) == []


@pytest.mark.parametrize("failure_mode", ["resolver-error", "candidate-missing"])
def test_video_reference_upload_cleans_published_file_when_preview_fails(
    monkeypatch, tmp_path, failure_mode
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group_root = (
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "references"
        / "ng-01"
    )

    async def failed_preview(**kwargs):
        if failure_mode == "resolver-error":
            raise OSError("simulated preview failure")
        return VideoReferencePreview(
            revision=0,
            candidates=(),
            references=(),
            warnings=(),
            max_images=2,
        )

    monkeypatch.setattr(
        narrative_groups,
        "resolve_group_video_reference_preview",
        failed_preview,
    )
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/"
        "video/reference-uploads",
        files={"file": ("one.png", image_bytes(), "image/png")},
    )

    assert response.status_code == 422
    assert group_root.is_dir()
    assert list(group_root.iterdir()) == []


def test_h3_reference_generate_requires_current_reference_revision_before_reserve(
    monkeypatch, tmp_path
):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    install_h3_reference_registry(monkeypatch)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate"
    base = {
        "model": "runninghub:minimax-h3-ref",
        "mode": "auto",
        "revision": 0,
        "plan_revision": 1,
    }

    missing = client.post(endpoint, json=base)
    stale = client.post(endpoint, json={**base, "reference_revision": 1})

    assert missing.status_code == stale.status_code == 409
    assert backend.calls == []
    group = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]
    assert group["stages"]["video"]["revision"] == 0


def test_h3_reference_generate_validates_references_and_frames_before_enqueue(
    monkeypatch, tmp_path
):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    install_h3_reference_registry(monkeypatch)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate"
    request = {
        "model": "runninghub:minimax-h3-ref",
        "mode": "auto",
        "revision": 0,
        "plan_revision": 1,
        "reference_revision": 0,
    }
    no_references = client.post(endpoint, json=request)
    assert no_references.status_code == 422
    assert backend.calls == []

    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(image_bytes())

    async def resolved_references(**kwargs):
        return (ResolvedVideoReference(
            reference_id="opaque",
            source_kind="temporary_upload",
            label="reference",
            subject_description="Hero",
            path=reference_path,
            content=reference_path.read_bytes(),
            sha256="a" * 64,
            temporary_upload_id="upload",
        ),)

    monkeypatch.setattr(
        narrative_groups, "resolve_saved_video_references", resolved_references
    )
    no_frames = client.post(endpoint, json=request)
    assert no_frames.status_code == 422
    assert backend.calls == []
    prepare_render_frames(tmp_path)

    missing_tail = client.post(endpoint, json={**request, "mode": "fl2va"})
    assert missing_tail.status_code == 422
    assert backend.calls == []
    group = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups"
    ).json()["data"][0]
    assert group["stages"]["video"]["revision"] == 0

    accepted = client.post(endpoint, json=request)
    assert accepted.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_revision"] == 0


def test_legacy_h3_payload_does_not_gain_reference_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
            "reference_revision": 0,
        },
    )

    assert response.status_code == 202
    assert "reference_revision" not in backend.calls[0][1]["payload"]


def test_video_generate_rejects_stale_plan_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 999,
        },
    )

    assert response.status_code == 409
    assert backend.calls == []


def test_video_generate_rejects_newapi_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "newapi_seedance-1.0-pro-fast",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Video workflow is unavailable for narrative groups"
    )
    assert backend.calls == []


def test_video_generate_accepts_future_registered_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    future_model = "future:director-v2"
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id=future_model,
                    label="Future Director V2",
                    provider="future",
                    adapter_key="director-v2",
                    workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3,
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("auto",),
                ),
            )
        ),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": future_model,
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["model"] == future_model


def test_video_generate_rejects_unsupported_registered_mode(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id="runninghub:minimax-h3",
                    label="RunningHub MiniMax H3",
                    provider="runninghub",
                    adapter_key="minimax-h3",
                    workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3,
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("i2va",),
                    default_mode="i2va",
                ),
            )
        ),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_video_generate_does_not_mask_registry_builder_errors(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    def fail_registry(store, resolver):
        raise RuntimeError("registry configuration is broken")

    monkeypatch.setattr(
        narrative_groups, "build_video_workflow_registry", fail_registry
    )

    with pytest.raises(RuntimeError, match="registry configuration is broken"):
        client.post(
            "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
            json={
                "model": "runninghub:minimax-h3",
                "mode": "auto",
                "revision": 0,
                "plan_revision": 1,
            },
        )

    assert backend.calls == []


def test_video_generate_enqueues_only_stable_director_identifiers(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3", "mode": "auto", "revision": 0,
            "plan_revision": 1,
            "aspect_ratio": "16:9", "resolution": "720p",
        },
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert backend.calls[0][1]["task_type"] == "narrative_group_video"
    assert backend.calls[0][1]["queue_kind"] == "video"
    assert payload == {
        "episode": 1,
        "group_id": "ng-01",
        "revision": 1,
        "plan_revision": 1,
        "model": "runninghub:minimax-h3",
        "mode": "auto",
        "aspect_ratio": "16:9",
        "workflow_parameters": {"resolution": "720p"},
        "settings_revision": 0,
    }


def test_video_generate_rejects_stale_revision_without_changing_sidecar(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate"

    request = {
        "model": "runninghub:minimax-h3",
        "mode": "auto",
        "revision": 0,
        "plan_revision": 1,
    }
    accepted = client.post(endpoint, json=request)
    assert accepted.status_code == 202

    stale = client.post(endpoint, json=request)

    assert stale.status_code == 409
    assert len(backend.calls) == 1
    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["video"]
    assert stage["revision"] == 1
    assert stage["status"] == "queued"


def test_video_enqueue_failure_restores_complete_prior_sidecar(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    video = tmp_path / "videos" / "prior.mp4"
    manifest = tmp_path / "videos" / "prior.manifest.json"
    stems = [tmp_path / "videos" / name for name in ("original.wav", "dialogue.wav", "ambience.wav")]
    video.parent.mkdir(parents=True, exist_ok=True)
    for path in (video, manifest, *stems):
        path.write_bytes(b"old")
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path, 1, "ng-01", "video", expected_revision=1, status="completed",
        video_asset=str(video), manifest_asset=str(manifest), original_audio_path=str(stems[0]),
        dialogue_stem_path=str(stems[1]), ambience_stem_path=str(stems[2]),
        dialogue_stem_status="succeeded", ambience_stem_status="succeeded",
    )
    before = sidecar_path(tmp_path, 1).read_bytes()
    failing = FailingBackend()
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: failing)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3", "mode": "auto",
            "revision": 1, "plan_revision": 1,
        },
    )

    assert response.status_code == 503
    assert sidecar_path(tmp_path, 1).read_bytes() == before
    assert len(failing.calls) == 1


def test_video_generate_requires_current_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={"model": "runninghub:minimax-h3", "mode": "auto"},
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_list_urlizes_only_project_scoped_assets(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "render")
    grid = tmp_path / "grids" / "grid.png"
    cell = tmp_path / "frames" / "cell.png"
    grid.parent.mkdir(parents=True)
    cell.parent.mkdir(parents=True)
    grid.write_bytes(b"grid")
    cell.write_bytes(b"cell")
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=1,
                        status="completed", grid_asset=str(grid),
                        cell_assets=[{"cell": 0, "path": str(cell)}])

    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["render"]
    assert stage["grid_asset"] == "/api/v1/projects/demo/media/grids/grid.png"
    assert stage["cell_assets"][0]["url"] == "/api/v1/projects/demo/media/frames/cell.png"
    assert stage["cell_assets"][0]["path"] == "/api/v1/projects/demo/media/frames/cell.png"


def test_stage_history_and_rollback_routes(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=1,
                        status="completed", grid_asset="one.png")
    advance_revision(tmp_path, 1, "ng-01", "render", regenerate=True)
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=2,
                        status="completed", grid_asset="two.png")
    history_url = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/revisions"
    history = client.get(history_url).json()["data"]
    assert history["items"][0]["revision"] == 1
    assert history["current_revision"] == 2
    rolled = client.post(history_url + "/1/rollback").json()["data"]
    assert rolled["stages"]["render"]["revision"] == 3


def test_reference_preview_is_safe_project_scoped_and_group_bounded(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path, beat_count=10)
    preview = make_reference_preview(tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, preview, calls)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-02/render/references"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["style"] == {
        "id": "style-opaque", "label": "cinematic", "prompt": "moody",
        "enabled_by_default": True, "warning": "",
    }
    assert data["character_references"][0]["thumbnail_url"] == (
        "/api/v1/projects/demo/media/assets/characters/hero.png"
    )
    assert data["scene_references"][0]["thumbnail_url"] == (
        "/api/v1/projects/demo/media/assets/scenes/room.png"
    )
    assert "path" not in str(data).lower()
    assert [beat["id"] for beat in calls[0][1]] == ["beat-10"]
    assert calls[0][2] == "render"
    assert data["limits"] == {
        "max_images": 9, "selected_images": 2, "omitted_reference_ids": [],
    }


def test_reference_preview_unknown_group_returns_404_without_resolving(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/missing/sketch/references"
    )

    assert response.status_code == 404
    assert calls == []


def test_generate_preserves_explicit_reference_selection_and_empty_list(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "aspect_ratio": "16:9",
            "use_style": False,
            "selected_character_reference_ids": ["char-opaque"],
            "selected_scene_reference_ids": [],
            "allow_unconstrained": True,
        },
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": False,
        "selected_character_reference_ids": ["char-opaque"],
        "selected_scene_reference_ids": [],
    }
    assert backend.calls[0][1]["payload"]["aspect_ratio"] == "16:9"
    assert len(calls) == 1


def test_generate_without_body_defaults_to_all_references(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch/generate"
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": True,
        "selected_character_reference_ids": None,
        "selected_scene_reference_ids": None,
    }
    assert backend.calls[0][1]["payload"]["aspect_ratio"] == "9:16"


def test_unknown_generate_reference_returns_422_without_enqueue(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"selected_character_reference_ids": ["foreign-id"]},
    )

    assert response.status_code == 422
    assert backend.calls == []
    groups = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"]
    assert groups[0]["stages"]["render"]["revision"] == 0


def test_regenerate_validates_and_forwards_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    valid = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"selected_scene_reference_ids": ["scene-opaque"], "allow_unconstrained": True},
    )
    invalid = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"selected_scene_reference_ids": ["foreign-id"], "allow_unconstrained": True},
    )

    assert valid.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"]["selected_scene_reference_ids"] == ["scene-opaque"]
    assert invalid.status_code == 422
    assert len(backend.calls) == 1


def test_split_keeps_aspect_but_does_not_resolve_or_include_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)

    def fail_resolver(*args, **kwargs):
        raise AssertionError("split must not resolve references")

    monkeypatch.setattr(narrative_groups, "resolve_group_reference_preview", fail_resolver)
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/split",
        json={"aspect_ratio": "16:9", "use_style": False},
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert payload["aspect_ratio"] == "16:9"
    assert "reference_selection" not in payload


def test_legacy_grid_aliases_keep_generation_contract(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), [])

    sketch = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch-grid/generate"
    )
    render = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render-grid/generate",
        json={"allow_unconstrained": True},
    )

    assert sketch.status_code == render.status_code == 202
    assert [call[1]["payload"]["stage"] for call in backend.calls] == ["sketch", "render"]
    assert all("reference_selection" in call[1]["payload"] for call in backend.calls)


def test_unknown_revision_rollback_returns_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/revisions/999/rollback"
    )

    assert response.status_code == 404


def _seed_director_manifest(tmp_path: Path) -> None:
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        H3DirectorOutputManifest,
        H3DirectorSegment,
        build_h3_timeline_data,
        save_h3_director_manifest,
    )

    video = tmp_path / "videos" / "director.mp4"
    ambience = tmp_path / "videos" / "ambience.wav"
    dialogue = tmp_path / "videos" / "dialogue.wav"
    video.parent.mkdir(parents=True, exist_ok=True)
    for path in (video, ambience, dialogue):
        path.write_bytes(b"media")
    entries = build_h3_timeline_data((
        H3DirectorSegment(segment_id="s1", beat_number=1, prompt="p", duration_seconds=1,
                          first_frame="f1.png", dialogue_source=DialogueSource.EXTERNAL_TTS),
        H3DirectorSegment(segment_id="s2", beat_number=2, prompt="p", duration_seconds=1,
                          first_frame="f2.png", dialogue_source=DialogueSource.H3_NATIVE),
    )).entries
    manifest_path = tmp_path / "videos" / "director.manifest.json"
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(video), entries=entries,
        ambience_stem_path=str(ambience), ambience_stem_status="succeeded",
        dialogue_stem_path=str(dialogue), dialogue_stem_status="succeeded",
    ))
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(tmp_path, 1, "ng-01", "video", expected_revision=1,
                        status="completed", video_asset=str(video), manifest_asset=str(manifest_path),
                        ambience_stem_path=str(ambience), dialogue_stem_path=str(dialogue))


def test_list_video_stage_exposes_manifest_video_spans_and_urls(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)

    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["video"]
    assert stage["video_asset"] == "/api/v1/projects/demo/media/videos/director.mp4"
    assert stage["ambience_stem_path"] == "/api/v1/projects/demo/media/videos/ambience.wav"
    assert stage["video_spans"] == [
        {"span_index": 0, "beat_numbers": [1], "start_seconds": 0.0,
         "end_seconds": 39 / 24, "dialogue_source": "external_tts"},
        {"span_index": 1, "beat_numbers": [2], "start_seconds": 39 / 24,
         "end_seconds": 78 / 24, "dialogue_source": "h3_native"},
    ]


def test_change_dialogue_source_updates_manifest_and_enqueues_compose_only(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/dialogue-source",
        json={"span_index": 0, "dialogue_source": "h3_native", "revision": 1},
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["task_type"] == "narrative_group_video_compose"
    assert backend.calls[0][1]["payload"] == {
        "episode": 1, "group_id": "ng-01", "revision": 1,
        "span_index": 0, "dialogue_source": "h3_native",
    }
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    manifest = load_h3_director_manifest(tmp_path / "videos" / "director.manifest.json")
    assert manifest.entries[0].dialogue_source.value == "h3_native"


def test_change_dialogue_source_rejects_invalid_span_and_stale_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/dialogue-source"

    assert client.post(endpoint, json={"span_index": 9, "dialogue_source": "h3_native", "revision": 1}).status_code == 404
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "h3_native", "revision": 2}).status_code == 409
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "h3_native"}).status_code == 422
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "unknown"}).status_code == 422
    assert backend.calls == []
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    manifest = load_h3_director_manifest(tmp_path / "videos" / "director.manifest.json")
    assert manifest.entries[0].dialogue_source.value == "external_tts"


def _seed_prompt_review_manifest(tmp_path: Path, payload: dict) -> Path:
    manifest = tmp_path / "videos" / "prompt-review.manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "video",
        expected_revision=1,
        status="completed",
        manifest_asset=str(manifest),
        actual_provider="runninghub",
        actual_model="runninghub:minimax-h3",
        actual_mode="fl2va",
    )
    return manifest


def test_get_video_prompts_exposes_safe_submitted_prompt_evidence(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    first = tmp_path / "frames" / "beat-1.png"
    last = tmp_path / "frames" / "beat-2.png"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    _seed_prompt_review_manifest(tmp_path, {
        "workflow_id": "workflow-136",
        "provider_task_id": "task-42",
        "api_key": "manifest-secret",
        "workflow_json": {"authorization": "Bearer secret"},
        "entries": [{
            "segment": {
                "segment_id": "beat-1--beat-2",
                "beat_number": 1,
                "prompt": "the exact submitted prompt",
                "duration_seconds": 8.5,
                "first_frame": str(first),
                "last_frame": str(last),
            },
            "workflow_id": "workflow-136",
            "provider_task_id": "task-42",
            "director_plan": {
                "mode": "fl2va",
                "shots": [{"action": "camera tracks quickly to the locked end pose"}],
                "credential": "must-not-leak",
                "token": "director-token-secret",
                "headers": {"X-Api-Key": "director-header-secret"},
                "workflow": {"nodes": [{"secret": "director-workflow-secret"}]},
                "source_path": str(tmp_path / "private" / "plan.json"),
            },
            "prompt_profile": {
                "id": "minimax-h3-director", "version": 4, "compiler_version": 1,
                "token": "profile-token-secret",
            },
            "quality_report": {
                "passed": True,
                "issues": [],
                "headers": {"X-Api-Key": "quality-header-secret"},
                "workflow_json": {"token": "quality-workflow-secret"},
            },
            "input_summary": {
                "beat_ids": ["beat-1", "beat-2"],
                "mode": "fl2va",
                "duration_seconds": 8.5,
                "first_frame_sha256": "a" * 64,
                "last_frame_sha256": "b" * 64,
                "authorization": "Bearer secret",
                "token": "summary-token-secret",
                "headers": {"X-Api-Key": "summary-header-secret"},
                "workflow": {"credential": "summary-workflow-secret"},
                "server_path": str(tmp_path),
            },
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    item = response.json()["data"]["units"][0]
    assert item["beat_ids"] == ["beat-1", "beat-2"]
    assert item["label"] == "Beat 1 → Beat 2"
    assert item["mode"] == "fl2va"
    assert item["duration_seconds"] == 8.5
    assert item["first_frame_url"] == "/api/v1/projects/demo/media/frames/beat-1.png"
    assert item["last_frame_url"] == "/api/v1/projects/demo/media/frames/beat-2.png"
    assert item["director_plan"]["mode"] == "fl2va"
    assert item["final_prompt"] == "the exact submitted prompt"
    assert item["prompt_profile"]["version"] == 4
    assert item["quality_report"]["passed"] is True
    assert item["workflow"] == "workflow-136"
    assert item["model"] == "runninghub:minimax-h3"
    assert item["provider_task_id"] == "task-42"
    serialized = response.text.lower()
    for forbidden in (
        "api_key", "authorization", "credential", "workflow_json", "x-api-key",
        '"token"', '"headers"', '"workflow":{"nodes"',
    ):
        assert forbidden not in serialized
    for secret in (
        "manifest-secret", "must-not-leak", "director-token-secret",
        "director-header-secret", "director-workflow-secret", "profile-token-secret",
        "quality-header-secret", "quality-workflow-secret", "summary-token-secret",
        "summary-header-secret", "summary-workflow-secret",
    ):
        assert secret not in serialized
    assert str(tmp_path).lower().replace("\\", "\\\\") not in serialized


def test_get_video_prompts_whitelists_nested_manifest_fields(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{
            "segment": {
                "segment_id": "beat-1", "beat_number": 1,
                "prompt": "safe prompt", "duration_seconds": 5,
            },
            "director_plan": {
                "mode": "i2va", "visual_style": str(tmp_path),
                "api_key": "plan-api-secret", "token": "plan-token-secret",
                "headers": {"X-Api-Key": "plan-header-secret"},
                "workflow": {"nodes": [{"credential": "plan-workflow-secret"}]},
            },
            "prompt_profile": {
                "id": "minimax-h3-director", "version": 4,
                "authorization": "profile-auth-secret",
            },
            "quality_report": {
                "passed": True,
                "issues": [{
                    "code": "safe-code", "message": str(tmp_path),
                    "credential": "quality-credential-secret",
                }],
                "workflow_json": {"token": "quality-workflow-secret"},
            },
            "input_summary": {
                "beat_ids": ["beat-1"], "mode": "i2va", "duration_seconds": 5,
                "first_frame_sha256": "a" * 64,
                "server_path": str(tmp_path), "token": "summary-token-secret",
            },
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    unit = response.json()["data"]["units"][0]
    assert unit["director_plan"] == {"mode": "i2va"}
    assert unit["quality_report"] == {
        "passed": True, "issues": [{"code": "safe-code"}],
    }
    serialized = response.text.lower()
    for forbidden in (
        "api_key", "authorization", "credential", "workflow_json", "x-api-key",
        '"token"', '"headers"', '"nodes"', str(tmp_path).lower(),
        "plan-api-secret", "plan-token-secret", "plan-header-secret",
        "plan-workflow-secret", "profile-auth-secret", "quality-credential-secret",
        "quality-workflow-secret", "summary-token-secret",
    ):
        assert forbidden not in serialized


def test_get_video_prompts_keeps_legacy_final_prompt(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{
            "segment": {
                "segment_id": "beat-1", "beat_number": 1,
                "prompt": "legacy submitted prompt", "duration_seconds": 5,
                "first_frame": None, "last_frame": None,
            }
        }]
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    item = response.json()["data"]["units"][0]
    assert item["final_prompt"] == "legacy submitted prompt"
    assert item["director_plan"] is None
    assert item["prompt_profile"] is None
    assert item["quality_report"] is None


def test_get_video_prompts_rejects_missing_group_and_unsafe_manifest(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"

    assert client.get(endpoint.replace("ng-01", "missing")).status_code == 404
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path, 1, "ng-01", "video", expected_revision=1,
        status="completed", manifest_asset=str(tmp_path.parent / "secret.json"),
    )
    response = client.get(endpoint)
    assert response.status_code == 404
    assert str(tmp_path.parent).lower() not in response.text.lower()


def test_get_video_prompts_reports_corrupt_manifest_without_path_leak(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    manifest = _seed_prompt_review_manifest(tmp_path, {"entries": []})
    manifest.write_text("{broken", encoding="utf-8")

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert str(manifest).lower() not in response.text.lower()


def test_get_video_prompts_rejects_manifest_over_hard_byte_limit(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(
        narrative_group_service, "VIDEO_PROMPT_MANIFEST_MAX_BYTES", 128,
        raising=False,
    )
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{"segment": {
            "segment_id": "beat-1", "beat_number": 1,
            "prompt": "x" * 512, "duration_seconds": 5,
        }}],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert str(tmp_path).lower() not in response.text.lower()


@pytest.mark.parametrize(
    ("limit_name", "limit", "payload"),
    [
        (
            "VIDEO_PROMPT_MANIFEST_MAX_ENTRIES", 1,
            {"entries": [
                {"segment": {"segment_id": "beat-1", "prompt": "p"}},
                {"segment": {"segment_id": "beat-2", "prompt": "p"}},
            ]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_DEPTH", 4,
            {"entries": [{"segment": {"segment_id": "beat-1", "prompt": "p"},
                          "extra": {"a": {"b": {"c": {"d": "deep-secret"}}}}}]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_COLLECTION_ITEMS", 3,
            {"entries": [{"segment": {"segment_id": "beat-1", "prompt": "p"},
                          "extra": [1, 2, 3, 4]}]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_STRING_LENGTH", 32,
            {"entries": [{"segment": {
                "segment_id": "beat-1", "prompt": "long-secret" * 8,
            }}]},
        ),
    ],
)
def test_get_video_prompts_rejects_structural_manifest_limits(
    monkeypatch, tmp_path, limit_name, limit, payload,
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(narrative_group_service, limit_name, limit, raising=False)
    _seed_prompt_review_manifest(tmp_path, payload)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert "secret" not in response.text.lower()
    assert str(tmp_path).lower() not in response.text.lower()


def test_get_video_prompts_rejects_non_scalar_dto_fields_and_unsafe_frame_paths(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "workflow_id": {"secret": "workflow-secret"},
        "provider_task_id": {"secret": "task-secret"},
        "entries": [{
            "segment": {
                "segment_id": {"secret": "segment-secret"},
                "beat_number": {"secret": "beat-secret"},
                "prompt": {"secret": "prompt-secret"},
                "duration_seconds": {"secret": "duration-secret"},
                "first_frame": "file:///C:/private/frame.png",
                "last_frame": r"\\server\share\frame.png",
            },
            "workflow_id": {"secret": "entry-workflow-secret"},
            "provider_task_id": {"secret": "entry-task-secret"},
            "model": {"secret": "model-secret"},
            "actual_duration_seconds": {"secret": "actual-duration-secret"},
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    unit = response.json()["data"]["units"][0]
    assert unit["beat_ids"] == []
    assert unit["label"] == ""
    assert unit["mode"] == "fl2va"
    assert unit["duration_seconds"] == 0
    assert unit["final_prompt"] == ""
    assert unit["first_frame_url"] == ""
    assert unit["last_frame_url"] == ""
    assert unit["workflow"] == ""
    assert unit["provider_task_id"] == ""
    assert all(isinstance(unit[field], str) for field in (
        "label", "mode", "final_prompt", "first_frame_url", "last_frame_url",
        "workflow", "model", "provider", "provider_task_id",
    ))
    serialized = response.text.lower()
    for forbidden in (
        "workflow-secret", "task-secret", "segment-secret", "beat-secret",
        "prompt-secret", "duration-secret", "entry-workflow-secret",
        "entry-task-secret", "model-secret", "actual-duration-secret",
        "file://", "c:/private", "server\\share",
    ):
        assert forbidden not in serialized

def test_put_video_plan_uses_active_director_shot_ids(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)
    [group] = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups"
    ).json()["data"]

    response = client.put(
        "/api/v1/projects/demo/episodes/1/narrative-groups/director-group/video/plan",
        json={
            "expected_revision": group["video_plan"]["revision"],
            "units": [{"beat_ids": ["shot-1"]}],
        },
    )

    assert response.status_code == 200
    plan = response.json()["data"]["video_plan"]
    assert plan["units"][0]["beat_ids"] == ["shot-1"]
    assert plan["units"][0]["duration_seconds"] == 3.0
