from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError


@pytest.mark.asyncio
async def test_agent_node_validate_is_a_local_fixed_profile_reference(tmp_path):
    from novelvideo.creation.builtin_agents import resolve
    from novelvideo.freezone.orchestration.adapters import NodeAdapter, available_operations
    from novelvideo.freezone.orchestration.capabilities import capabilities

    ref = {"id": "builtin:ideation", "revision": 1}
    node = {"id": "agent", "type": "agentNode", "data": {
        "operation": "validate", "agentRef": ref,
        "settings": {"genrePrimary": "悬疑"},
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    assert ("agentNode", "validate") in available_operations()
    capability = next(item for item in capabilities(available_operations())
                      if item["node_type"] == "agentNode")
    assert capability["default_operation"] == "validate"
    assert capability["operations"] == [{"name": "validate", "available": True, "reason": None}]

    result = await NodeAdapter(tmp_path).submit(
        run, {"node_id": "agent", "id": "attempt-1"}, [],
    )

    fixed = resolve(ref)
    assert result == {"local": True, "result": {"agentIdentity": {
        "agentRef": ref,
        "role": {
            "name": fixed["configuration"]["name"],
            "responsibilities": fixed["configuration"]["responsibilities"],
        },
    }}}


@pytest.mark.asyncio
async def test_compose_request_uses_schema_default_when_canvas_does_not_set_resolution(
    monkeypatch, tmp_path,
):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import VideoComposeRequest
    from novelvideo.freezone.orchestration.adapters import NodeAdapter

    captured = {}

    async def compose_video(project, episode, body, actor):
        captured["body"] = body
        return {"task_id": "task-1", "scope": "scope-1", "request_fingerprint": "hash-1"}

    monkeypatch.setattr(generation, "compose_video", compose_video)
    node = {"id": "compose", "type": "episodeComposeNode", "data": {
        "operation": "compose", "episodeNumber": 2,
        "directorRevisionId": "revision-2", "productionGroupIds": ["g1", "g2"],
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    await NodeAdapter(tmp_path).submit(run, {"node_id": "compose", "id": "attempt-1"}, [])

    assert captured["body"].resolution == VideoComposeRequest.model_fields["resolution"].default
    assert "resolution" not in captured["body"].model_fields_set


@pytest.mark.asyncio
async def test_render_uses_ready_versioned_character_and_scene_preview_bindings(
    monkeypatch, tmp_path,
):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter_groups
    from novelvideo.narrative_groups.planned_binding_service import ResolvedPlannedReference

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"render": SimpleNamespace(status="pending")})
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    def binding(binding_id, asset_kind, entity_id, status, selected, version_id):
        return ResolvedPlannedReference(
            binding_id=binding_id,
            asset_kind=asset_kind,
            entity_id=entity_id,
            display_label=entity_id,
            required=True,
            status=status,
            resolution="auto_matched",
            selected_by_default=selected,
            asset_slot_id=f"{asset_kind}:{entity_id}",
            version_id=version_id,
        ).model_dump(mode="json")

    preview_data = {
        "reference_revision": "planned-r3",
        "max_images": 9,
        "warnings": [],
        "bindings": [
            binding("character-binding", "character_identity", "Hero_casual", "ready", True, "hero-v3"),
            binding("scene-binding", "scene_base", "Workshop", "ready", True, "workshop-v2"),
            binding("unselected-binding", "character_identity", "Extra", "ready", False, "extra-v1"),
            binding("pending-binding", "scene_variant", "Workshop_night", "pending_confirmation", False, ""),
            binding("unversioned-binding", "scene_base", "Alley", "ready", True, ""),
        ],
    }
    captured = {}

    async def preview(*args, **kwargs):
        return {"ok": True, "data": preview_data}

    async def generate(project, episode, group_id, request, actor):
        captured["request"] = request
        return {"ok": True, "data": {"task_id": "task-1", "scope": "render-scope",
                                      "metadata": {"revision": 1}}}

    monkeypatch.setattr(adapter_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(adapter_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "preview_group_references", preview)
    monkeypatch.setattr(api, "generate_render_group", generate)
    node = {"id": "render", "type": "narrativeGroupStoryboardNode", "data": {
        "operation": "render", **reference,
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    await NodeAdapter(tmp_path).submit(run, {"node_id": "render", "id": "attempt-1"}, [])

    assert captured["request"].reference_resolution.selected_binding_ids == [
        "character-binding", "scene-binding",
    ]
    assert captured["request"].reference_resolution.reference_revision == "planned-r3"


@pytest.mark.asyncio
async def test_render_uses_saved_temporary_upload_capture_as_provider_reference(monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter_groups
    from novelvideo.narrative_groups.reference_uploads import save_reference_upload

    image = BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    upload = save_reference_upload(tmp_path, image.getvalue(), "image/png", "stage-capture.png")
    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"render": SimpleNamespace(status="pending")})
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    async def preview(*args, **kwargs):
        return {"ok": True, "data": {
            "source_plan_revision_id": "rev-2", "reference_revision": "planned-r3",
            "max_images": 4, "bindings": [],
        }}

    captured = {}

    async def generate(project, episode, group_id, request, actor):
        captured["request"] = request
        return {"ok": True, "data": {"task_id": "task-1", "scope": "render-scope",
                                      "metadata": {"revision": 1}}}

    monkeypatch.setattr(adapter_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(adapter_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "preview_group_references", preview)
    monkeypatch.setattr(api, "generate_render_group", generate)
    node = {"id": "render", "type": "narrativeGroupStoryboardNode", "data": {
        "operation": "render", **reference, "selectedBindingIds": [],
        "selectedUploadIds": [upload.upload_id], "referenceRevision": "planned-r3",
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    await NodeAdapter(tmp_path).submit(run, {"node_id": "render", "id": "attempt-1"}, [])

    assert captured["request"].reference_resolution.selected_binding_ids == []
    assert captured["request"].reference_resolution.upload_ids == [upload.upload_id]


@pytest.mark.asyncio
async def test_render_rejects_missing_saved_temporary_upload_before_provider_submission(
    monkeypatch, tmp_path,
):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter_groups

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"render": SimpleNamespace(status="pending")})
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    async def preview(*args, **kwargs):
        return {"ok": True, "data": {"reference_revision": "planned-r3", "max_images": 4,
                                      "bindings": []}}

    generated = False

    async def generate(*args, **kwargs):
        nonlocal generated
        generated = True
        return {"ok": True, "data": {"task_id": "task-1", "scope": "render-scope"}}

    monkeypatch.setattr(adapter_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(adapter_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "preview_group_references", preview)
    monkeypatch.setattr(api, "generate_render_group", generate)
    node = {"id": "render", "type": "narrativeGroupStoryboardNode", "data": {
        "operation": "render", **reference, "selectedBindingIds": [],
        "selectedUploadIds": ["upl_" + "0" * 32], "referenceRevision": "planned-r3",
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    with pytest.raises(OrchestrationError) as captured:
        await NodeAdapter(tmp_path).submit(run, {"node_id": "render", "id": "attempt-1"}, [])

    assert captured.value.code == "source_conflict"
    assert not generated


@pytest.mark.asyncio
async def test_video_node_inherits_saved_project_h3_mode_and_group_workflow(
    monkeypatch, tmp_path,
):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter_groups

    context = SimpleNamespace(
        state_dir=tmp_path, owner_username="owner", project_name="project",
    )
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(
        video_settings=SimpleNamespace(workflow_id="runninghub:minimax-h3", revision=7),
        video_plan=SimpleNamespace(revision=4),
        video_reference_settings=SimpleNamespace(revision=3),
        stages={"video": SimpleNamespace(revision=2, status="pending")},
    )
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    async def enqueue(*args, **kwargs):
        captured["request"] = args[4]
        return {"ok": True, "data": {"task_id": "task-1", "scope": "video-scope",
                                      "metadata": {"revision": 3}}}

    captured = {}
    monkeypatch.setattr(adapter_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(adapter_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "get_media_capability_store", lambda: object())
    monkeypatch.setattr(api, "get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(api, "_enqueue_group_video", enqueue)
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_from_state_dir",
        lambda *args, **kwargs: {"h3_mode": "fl2va"},
    )
    node = {"id": "video", "type": "narrativeGroupVideoNode", "data": {
        "operation": "video", "explicit_retry": True, **reference,
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    await NodeAdapter(tmp_path).submit(run, {"node_id": "video", "id": "attempt-1"}, [])

    assert captured["request"].mode == "fl2va"
    assert captured["request"].model == "runninghub:minimax-h3"
    assert captured["request"].settings_revision == 7


@pytest.mark.asyncio
async def test_cached_group_admission_cannot_turn_into_unbudgeted_external_submission(
    monkeypatch, tmp_path,
):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    node = {"id": "video", "type": "narrativeGroupVideoNode", "data": {
        "operation": "video", "episodeNumber": 2, "groupId": "g1",
        "directorRevisionId": "rev-2",
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}
    attempt = {"node_id": "video", "id": "attempt-1"}
    adapter = NodeAdapter(tmp_path)
    cached = {"signature": "fixed-success"}
    monkeypatch.setattr(narrative_groups, "reuse_snapshot", lambda *args, **kwargs: cached)

    assert adapter.requires_external_submission(run, attempt) is False
    assert attempt["narrative_group_admission"] == {
        "external": False, "reuse_signature": "fixed-success",
    }

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"video": SimpleNamespace(status="pending")})

    async def resolve_bound_group(run, data):
        return {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}, resolved, group

    monkeypatch.setattr(narrative_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(narrative_groups, "reuse_snapshot", lambda *args, **kwargs: None)

    with pytest.raises(OrchestrationError) as changed:
        await adapter.submit(run, attempt, [])
    assert changed.value.code == "source_conflict"


@pytest.mark.asyncio
async def test_failed_render_requires_explicit_retry_operation(monkeypatch, tmp_path):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter_groups

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"render": SimpleNamespace(status="failed")})
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    monkeypatch.setattr(adapter_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(adapter_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    node = {"id": "render", "type": "narrativeGroupStoryboardNode", "data": {
        "operation": "render", **reference,
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    with pytest.raises(OrchestrationError) as retry_required:
        await NodeAdapter(tmp_path).submit(run, {"node_id": "render", "id": "attempt-1"}, [])
    assert retry_required.value.code == "request_rejected"

    captured = {}
    async def preview(*args, **kwargs):
        return {"data": {"bindings": [], "reference_revision": "refs-1"}}

    async def enqueue(*args, **kwargs):
        captured["explicit_retry"] = kwargs.get("explicit_retry")
        return {"ok": True, "data": {"task_id": "task-1", "scope": "render-scope",
                                      "metadata": {"revision": 2}}}

    monkeypatch.setattr(api, "preview_group_references", preview)
    monkeypatch.setattr(api, "_enqueue_group_action", enqueue)
    node["data"]["operation"] = "retry_render"
    await NodeAdapter(tmp_path).submit(run, {"node_id": "render", "id": "attempt-2"}, [])
    assert captured["explicit_retry"] is True


@pytest.mark.asyncio
async def test_failed_video_cannot_be_retried_by_normal_video_operation(monkeypatch, tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={"video": SimpleNamespace(status="failed")})
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    monkeypatch.setattr(narrative_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(narrative_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    node = {"id": "video", "type": "narrativeGroupVideoNode", "data": {
        "operation": "video", **reference,
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    with pytest.raises(OrchestrationError) as retry_required:
        await NodeAdapter(tmp_path).submit(run, {"node_id": "video", "id": "attempt-1"}, [])
    assert retry_required.value.code == "request_rejected"


@pytest.mark.asyncio
async def test_group_reconcile_rejects_replaced_task_id(monkeypatch, tmp_path):
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(output_dir=tmp_path)

    async def resolve_project(*args, **kwargs):
        return context, None, None, tmp_path, None

    class Manager:
        def get_task_for_project(self, *args, **kwargs):
            return SimpleNamespace(
                task_id="replacement-task", status="completed", result={"video_path": "final.mp4"},
                metadata={}, error="",
            )

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr("novelvideo.task_state.get_task_manager", lambda: Manager())
    run = {"project_id": "project-1", "actor": {"id": "user-1"}}
    submission = {"narrative_group": True, "stage": "compose", "task_type": "compose_episode",
                  "task_id": "original-task", "scope": "compose-scope", "episode": 2,
                  "director_revision_id": "rev-2", "group_ids": ["g1"]}

    result = await narrative_groups.reconcile(run, submission)

    assert result["status"] == "submission_unknown"


@pytest.mark.asyncio
async def test_completed_compose_reconciles_and_publishes_final_media(monkeypatch, tmp_path):
    from novelvideo.api.routes import freezone, narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(output_dir=tmp_path)
    snapshot = {"revision_id": "rev-2", "group_ids": ["g1"],
                "video_sources": [{"group_id": "g1", "revision": 4, "manifest_asset": "m1"}]}
    node_data = {"episodeNumber": 2, "directorRevisionId": "rev-2",
                 "productionGroupIds": ["g1"], "addSubtitles": True, "addBgm": False}
    body = narrative_groups._compose_request(node_data, "attempt-1")
    receipt = narrative_groups._compose_request_fingerprint(2, snapshot, body)

    async def resolve_project(*args, **kwargs):
        return context, None, None, tmp_path, None

    class Manager:
        def get_task_for_project(self, *args, **kwargs):
            return SimpleNamespace(
                task_id="compose-task", status="completed",
                result={"video_path": str(tmp_path / "final.mp4")},
                metadata={"request_fingerprint": receipt}, error="",
            )

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr("novelvideo.task_state.get_task_manager", lambda: Manager())
    monkeypatch.setattr(narrative_groups, "_compose_snapshot", lambda *_: snapshot)
    monkeypatch.setattr(api, "_asset_url", lambda project, root, path: "/media/final.mp4")
    run = {"project_id": "project-1", "actor": {"id": "user-1"}}
    submission = {"narrative_group": True, "stage": "compose", "task_type": "compose_episode",
                  "task_id": "compose-task", "scope": "compose-scope", "episode": 2,
                  "director_revision_id": "rev-2", "group_ids": ["g1"],
                  "request_fingerprint": receipt,
                  "compose_request": body.model_dump(mode="json", exclude={"client_mutation_id"})}

    result = await narrative_groups.reconcile(run, submission)

    assert result["status"] == "succeeded"
    assert result["result"]["resultVideoUrl"] == "/media/final.mp4"
    assert result["result"]["narrativeGroupResult"]["productionGroupIds"] == ["g1"]


@pytest.mark.asyncio
async def test_compose_reconcile_rejects_changed_video_source_with_same_group_set(
    monkeypatch, tmp_path,
):
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(output_dir=tmp_path)
    original = {"revision_id": "rev-2", "group_ids": ["g1"],
                "video_sources": [{"group_id": "g1", "revision": 4, "manifest_asset": "old.m1"}]}
    current = {"revision_id": "rev-2", "group_ids": ["g1"],
               "video_sources": [{"group_id": "g1", "revision": 5, "manifest_asset": "new.m1"}]}
    node_data = {"episodeNumber": 2, "directorRevisionId": "rev-2",
                 "productionGroupIds": ["g1"], "addSubtitles": True, "addBgm": False}
    body = narrative_groups._compose_request(node_data, "attempt-1")
    receipt = narrative_groups._compose_request_fingerprint(2, original, body)

    async def resolve_project(*args, **kwargs):
        return context, None, None, tmp_path, None

    class Manager:
        def get_task_for_project(self, *args, **kwargs):
            return SimpleNamespace(
                task_id="compose-task", status="completed",
                result={"video_path": str(tmp_path / "final.mp4")},
                metadata={"request_fingerprint": receipt}, error="",
            )

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr("novelvideo.task_state.get_task_manager", lambda: Manager())
    monkeypatch.setattr(narrative_groups, "_compose_snapshot", lambda *_: current)
    run = {"project_id": "project-1", "actor": {"id": "user-1"}}
    submission = {"narrative_group": True, "stage": "compose", "task_type": "compose_episode",
                  "task_id": "compose-task", "scope": "compose-scope", "episode": 2,
                  "director_revision_id": "rev-2", "group_ids": ["g1"],
                  "request_fingerprint": receipt,
                  "compose_request": body.model_dump(mode="json", exclude={"client_mutation_id"})}

    result = await narrative_groups.reconcile(run, submission)

    assert result["status"] == "failed"
    assert "composition inputs changed" in result["error"].lower()


@pytest.mark.asyncio
async def test_compose_submission_recovery_rejects_changed_video_sources(monkeypatch, tmp_path):
    from novelvideo.api.routes import freezone
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(output_dir=tmp_path)
    original = {"revision_id": "rev-2", "group_ids": ["g1"],
                "video_sources": [{"group_id": "g1", "revision": 4, "manifest_asset": "old.m1"}]}
    current = {"revision_id": "rev-2", "group_ids": ["g1"],
               "video_sources": [{"group_id": "g1", "revision": 5, "manifest_asset": "new.m1"}]}
    node = {"id": "compose", "type": "episodeComposeNode", "data": {
        "operation": "compose", "episodeNumber": 2, "directorRevisionId": "rev-2",
        "productionGroupIds": ["g1"],
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}
    attempt = {"id": "attempt-1", "node_id": "compose"}
    body = narrative_groups._compose_request(node["data"], attempt["id"])
    receipt = narrative_groups._compose_request_fingerprint(2, original, body)
    calls = []

    async def resolve_project(*args, **kwargs):
        return context, None, None, tmp_path, None

    class Manager:
        def get_task_for_project(self, *args, **kwargs):
            return SimpleNamespace(
                task_id="compose-task", status="queued", metadata={"request_fingerprint": receipt},
            )

    def current_snapshot(*args):
        calls.append(args)
        return current

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr("novelvideo.task_state.get_task_manager", lambda: Manager())
    monkeypatch.setattr(narrative_groups, "_compose_snapshot", current_snapshot)

    recovered = await narrative_groups.reconcile_submission(run, attempt, node, "compose")

    assert calls
    assert recovered == {"status": "submission_unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type,operation,stage", [
    ("narrativeGroupStoryboardNode", "render", "render"),
    ("narrativeGroupVideoNode", "video", "video"),
])
async def test_failed_group_attempt_marker_dispatches_explicit_retry(
    monkeypatch, tmp_path, node_type, operation, stage,
):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={stage: SimpleNamespace(status="failed", revision=2)})
    if stage == "video":
        group.video_settings = SimpleNamespace(workflow_id="runninghub:minimax-h3", revision=1)
        group.video_plan = SimpleNamespace(revision=1)
        group.video_reference_settings = SimpleNamespace(revision=1)
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    captured = {}
    async def enqueue(*args, **kwargs):
        captured["explicit_retry"] = kwargs.get("explicit_retry")
        return {"ok": True, "data": {"task_id": "task-1", "scope": "retry-scope",
                                      "metadata": {"revision": 3}}}

    monkeypatch.setattr(narrative_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(narrative_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    if stage == "render":
        async def preview(*args, **kwargs):
            return {"data": {"bindings": [], "reference_revision": "refs-1"}}
        monkeypatch.setattr(api, "preview_group_references", preview)
        monkeypatch.setattr(api, "_enqueue_group_action", enqueue)
    else:
        monkeypatch.setattr(api, "get_media_capability_store", lambda: object())
        monkeypatch.setattr(api, "get_media_credential_resolver", lambda: object())
        monkeypatch.setattr(api, "_enqueue_group_video", enqueue)
        monkeypatch.setattr(
            "novelvideo.project_config.load_project_config_from_state_dir",
            lambda *args, **kwargs: {"h3_mode": "i2va"},
        )

    node = {"id": "group", "type": node_type, "data": {
        "operation": operation, **reference,
    }}
    original_data = dict(node["data"])
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    await NodeAdapter(tmp_path).submit(
        run, {"node_id": "group", "id": "attempt-2", "explicit_retry": True}, [],
    )

    assert captured["explicit_retry"] is True
    assert node["data"] == original_data
    assert node["data"]["operation"] == operation


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type,operation,stage", [
    ("narrativeGroupStoryboardNode", "render", "render"),
    ("narrativeGroupVideoNode", "video", "video"),
])
async def test_preflight_failed_retry_marker_keeps_normal_submit_for_pending_stage(
    monkeypatch, tmp_path, node_type, operation, stage,
):
    from fastapi import HTTPException
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.adapters import narrative_groups

    context = SimpleNamespace(state_dir=tmp_path, owner_username="owner", project_name="project")
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=context)
    group = SimpleNamespace(stages={stage: SimpleNamespace(status="pending", revision=0)})
    if stage == "video":
        group.video_settings = SimpleNamespace(workflow_id="runninghub:minimax-h3", revision=1)
        group.video_plan = SimpleNamespace(revision=1)
        group.video_reference_settings = SimpleNamespace(revision=1)
    reference = {"episodeNumber": 2, "groupId": "g1", "directorRevisionId": "rev-2"}

    async def resolve_bound_group(run, data):
        return reference, resolved, group

    monkeypatch.setattr(narrative_groups, "_resolve_bound_group", resolve_bound_group)
    monkeypatch.setattr(narrative_groups, "reuse_snapshot", lambda *args, **kwargs: None)
    captured = {"calls": 0, "explicit_retry": None}
    if stage == "render":
        async def preview(*args, **kwargs):
            return {"data": {"bindings": [], "reference_revision": "refs-1"}}

        async def generate(*args, **kwargs):
            captured["calls"] += 1
            if captured["calls"] == 1:
                raise HTTPException(status_code=422, detail={"code": "PREFLIGHT_REJECTED"})
            return {"ok": True, "data": {"task_id": "task-1", "scope": "render-scope",
                                          "metadata": {"revision": 1}}}

        async def retry(*args, **kwargs):
            pytest.fail("pending stage must use normal render preflight, not retry_render")

        monkeypatch.setattr(api, "preview_group_references", preview)
        monkeypatch.setattr(api, "generate_render_group", generate)
        monkeypatch.setattr(api, "_enqueue_group_action", retry)
    else:
        monkeypatch.setattr(api, "get_media_capability_store", lambda: object())
        monkeypatch.setattr(api, "get_media_credential_resolver", lambda: object())
        monkeypatch.setattr(
            "novelvideo.project_config.load_project_config_from_state_dir",
            lambda *args, **kwargs: {"h3_mode": "i2va"},
        )

        async def enqueue(*args, **kwargs):
            captured["calls"] += 1
            captured["explicit_retry"] = kwargs.get("explicit_retry")
            if captured["calls"] == 1:
                raise HTTPException(status_code=422, detail={"code": "PREFLIGHT_REJECTED"})
            return {"ok": True, "data": {"task_id": "task-1", "scope": "video-scope",
                                          "metadata": {"revision": 1}}}

        monkeypatch.setattr(api, "_enqueue_group_video", enqueue)

    node = {"id": "group", "type": node_type, "data": {
        "operation": operation, **reference,
    }}
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}
    adapter = NodeAdapter(tmp_path)

    with pytest.raises(OrchestrationError) as first_rejected:
        await adapter.submit(run, {"node_id": "group", "id": "attempt-1"}, [])
    assert first_rejected.value.code == "request_rejected"
    assert first_rejected.value.details["status"] == 422
    assert group.stages[stage].status == "pending"

    await adapter.submit(
        run, {"node_id": "group", "id": "attempt-2", "explicit_retry": True}, [],
    )

    assert captured["calls"] == 2
    if stage == "video":
        assert captured["explicit_retry"] is False


def test_agent_node_role_description_does_not_grant_execution_operations():
    from novelvideo.freezone.orchestration.capabilities import catalog_by_type
    from novelvideo.freezone.orchestration.models import RESULT_FIELDS

    entry = catalog_by_type()["agentNode"]
    assert [item["name"] for item in entry["operations"]] == ["validate"]
    assert RESULT_FIELDS["agentNode"] == {"agentIdentity"}
