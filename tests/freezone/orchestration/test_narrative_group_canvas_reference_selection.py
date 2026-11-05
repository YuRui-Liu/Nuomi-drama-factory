from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError, input_fingerprint


def _node(**data_overrides):
    data = {
        "episodeNumber": 1,
        "groupId": "group-a",
        "directorRevisionId": "revision-a",
    }
    data.update(data_overrides)
    return {"id": "storyboard-a", "type": "narrativeGroupStoryboardNode", "data": data}


def _run_context(tmp_path: Path, *, render_status="pending"):
    reference = {
        "episodeNumber": 1,
        "groupId": "group-a",
        "directorRevisionId": "revision-a",
    }
    resolved = SimpleNamespace(project_dir=tmp_path)
    group = SimpleNamespace(stages={"render": SimpleNamespace(status=render_status)})
    return reference, resolved, group


def _reference_preview(
    revision="ref-r1", *, binding_a_version="asset-a-v1",
    binding_a_sha256="a" * 64, include_private_reference_metadata=False,
):
    binding_a = {
        "binding_id": "binding-a", "status": "ready", "version_id": binding_a_version,
        "asset_slot_id": "character:hero:portrait", "selected_by_default": False,
    }
    if include_private_reference_metadata:
        binding_a.update({
            "sha256": binding_a_sha256,
            "relative_path": f"assets/{binding_a_version}.png",
        })
    return {"data": {
        "source_plan_revision_id": "revision-a",
        "reference_revision": revision,
        "max_images": 4,
        "bindings": [
            binding_a,
            {"binding_id": "binding-b", "status": "ready", "version_id": "asset-b-v1", "selected_by_default": True},
            {"binding_id": "binding-missing", "status": "missing_image", "version_id": None, "selected_by_default": False},
        ],
    }}


def _completed_render_reuse(selected_binding_ids, reference_revision, preview):
    bindings = {item["binding_id"]: item for item in preview["data"]["bindings"]}
    selection = [
        {
            "binding_id": binding_id,
            "version_id": bindings[binding_id]["version_id"],
            "sha256": bindings[binding_id].get("sha256", "a" * 64),
            "asset_slot_id": bindings[binding_id].get("asset_slot_id", ""),
            "relative_path": bindings[binding_id].get(
                "relative_path", f"assets/{bindings[binding_id]['version_id']}.png"
            ),
        }
        for binding_id in selected_binding_ids
    ]
    group = SimpleNamespace(stages={"render": SimpleNamespace(provider_parameters={
        "reference_audit": {
            "reference_selection": {
                "selected_binding_ids": list(selected_binding_ids),
                "upload_ids": [],
                "reference_revision": reference_revision,
                "bindings": selection,
            },
        },
    })})
    return {"signature": "saved-render", "stage": "render", "group": group}


def test_completed_render_reuse_requires_audited_saved_inputs(tmp_path, monkeypatch):
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter

    asset = tmp_path / "storyboard.png"
    asset.write_bytes(b"storyboard")
    preview = _reference_preview()
    audit = _completed_render_reuse(["binding-a"], "ref-r1", preview)["group"].stages[
        "render"
    ].provider_parameters["reference_audit"]
    stage = SimpleNamespace(
        status="completed", needs_regeneration=False, grid_asset=str(asset), revision=1,
        selected_storyboard_id="storyboard-a", actual_provider="provider-a",
        actual_model="model-a", actual_pixel_size="1K", requested_pixel_size="1K",
        provider_parameters={"reference_audit": audit},
    )
    group = SimpleNamespace(director_revision_id="revision-a", stages={"render": stage})
    monkeypatch.setattr(adapter, "_resolve_group_from_disk", lambda *_args: group)

    assert adapter.reuse_snapshot(
        tmp_path, _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1"), "render",
    ) is not None
    assert adapter.reuse_snapshot(
        tmp_path, _node(selectedBindingIds=["binding-b"], referenceRevision="ref-r1"), "render",
    ) is None
    assert adapter.reuse_snapshot(tmp_path, _node(), "render") is None


def test_render_reuse_compares_binding_hash_when_preview_exposes_it():
    from novelvideo.freezone.orchestration.adapters.narrative_groups import (
        _render_reuse_matches_preview,
    )

    preview = _reference_preview(include_private_reference_metadata=True)
    audit = _completed_render_reuse(["binding-a"], "ref-r1", preview)["group"].stages[
        "render"
    ].provider_parameters["reference_audit"]
    assert _render_reuse_matches_preview(
        audit, ["binding-a"], "ref-r1", preview["data"],
    )

    changed_hash_preview = _reference_preview(
        include_private_reference_metadata=True, binding_a_sha256="b" * 64,
    )
    assert not _render_reuse_matches_preview(
        audit, ["binding-a"], "ref-r1", changed_hash_preview["data"],
    )


async def _submit_render(monkeypatch, tmp_path, node, preview, *, reuse=None):
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.freezone.orchestration.adapters import narrative_groups as adapter

    monkeypatch.setattr(adapter, "_resolve_bound_group", _resolve_bound_group)
    monkeypatch.setattr(adapter, "reuse_snapshot", lambda *_args: reuse)
    monkeypatch.setattr(adapter, "_stage_media_result", lambda *_args: {"reused": True})
    monkeypatch.setattr(api, "preview_group_references", _preview_references)
    monkeypatch.setattr(api, "generate_render_group", _generate_render)
    _resolve_bound_group.value = _run_context(
        tmp_path, render_status="completed" if reuse is not None else "pending"
    )
    _preview_references.value = preview
    _preview_references.calls = []
    _generate_render.calls = []
    result = await adapter.submit(
        {"project_id": "project-a", "actor": {"id": "user-a"}},
        {"id": "attempt-a", "explicit_retry": False},
        node,
        "render",
    )
    return result, _generate_render.calls


async def _resolve_bound_group(_run, _data):
    return _resolve_bound_group.value


async def _preview_references(*_args):
    _preview_references.calls.append(_args)
    return _preview_references.value


async def _generate_render(*args):
    _generate_render.calls.append(args)
    return {"data": {"task_id": "task-a", "scope": "group-a_render_r1", "metadata": {"revision": 1}}}


def test_canvas_reference_selection_is_part_of_the_execution_input_fingerprint():
    selected = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1")
    changed_selection = _node(selectedBindingIds=["binding-b"], referenceRevision="ref-r1")
    changed_revision = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r2")

    assert input_fingerprint(selected) != input_fingerprint(changed_selection)
    assert input_fingerprint(selected) != input_fingerprint(changed_revision)


@pytest.mark.asyncio
async def test_canvas_render_uses_saved_bindings_instead_of_automatic_defaults(monkeypatch, tmp_path):
    node = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1")

    result, calls = await _submit_render(monkeypatch, tmp_path, node, _reference_preview())

    request = calls[0][3]
    assert request.reference_resolution.selected_binding_ids == ["binding-a"]
    assert request.reference_resolution.reference_revision == "ref-r1"
    assert result["task_id"] == "task-a"


@pytest.mark.asyncio
async def test_canvas_render_rejects_saved_reference_revision_after_preview_changes(monkeypatch, tmp_path):
    node = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1")

    with pytest.raises(OrchestrationError) as conflict:
        await _submit_render(monkeypatch, tmp_path, node, _reference_preview("ref-r2"))

    assert conflict.value.code == "source_conflict"
    assert _generate_render.calls == []


@pytest.mark.asyncio
async def test_canvas_render_validates_current_revision_before_reusing_saved_output(monkeypatch, tmp_path):
    node = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1")
    preview = _reference_preview("ref-r2")
    reuse = _completed_render_reuse(["binding-a"], "ref-r1", _reference_preview())

    with pytest.raises(OrchestrationError) as conflict:
        await _submit_render(monkeypatch, tmp_path, node, preview, reuse=reuse)

    assert conflict.value.code == "source_conflict"
    assert len(_preview_references.calls) == 1
    assert _generate_render.calls == []


@pytest.mark.asyncio
async def test_canvas_render_reuses_only_the_same_saved_selection_and_frozen_versions(
    monkeypatch, tmp_path,
):
    node = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r1")
    preview = _reference_preview()
    reuse = _completed_render_reuse(["binding-a"], "ref-r1", preview)

    result, calls = await _submit_render(monkeypatch, tmp_path, node, preview, reuse=reuse)

    assert result == {"local": True, "result": {"reused": True}}
    assert calls == []
    assert len(_preview_references.calls) == 1


@pytest.mark.asyncio
async def test_canvas_render_generates_when_saved_binding_selection_changes(monkeypatch, tmp_path):
    node = _node(selectedBindingIds=["binding-b"], referenceRevision="ref-r1")
    preview = _reference_preview()
    reuse = _completed_render_reuse(["binding-a"], "ref-r1", preview)

    result, calls = await _submit_render(monkeypatch, tmp_path, node, preview, reuse=reuse)

    assert result["task_id"] == "task-a"
    assert calls[0][3].reference_resolution.selected_binding_ids == ["binding-b"]


@pytest.mark.asyncio
async def test_canvas_render_generates_when_selected_binding_version_or_hash_changes(
    monkeypatch, tmp_path,
):
    node = _node(selectedBindingIds=["binding-a"], referenceRevision="ref-r2")
    old_preview = _reference_preview()
    current_preview = _reference_preview(
        "ref-r2", binding_a_version="asset-a-v2", binding_a_sha256="b" * 64,
    )
    reuse = _completed_render_reuse(["binding-a"], "ref-r1", old_preview)

    result, calls = await _submit_render(monkeypatch, tmp_path, node, current_preview, reuse=reuse)

    assert result["task_id"] == "task-a"
    assert calls[0][3].reference_resolution.reference_revision == "ref-r2"


@pytest.mark.asyncio
async def test_canvas_render_rejects_selected_binding_that_is_no_longer_ready(monkeypatch, tmp_path):
    node = _node(selectedBindingIds=["binding-missing"], referenceRevision="ref-r1")

    with pytest.raises(OrchestrationError) as conflict:
        await _submit_render(monkeypatch, tmp_path, node, _reference_preview())

    assert conflict.value.code == "source_conflict"
    assert _generate_render.calls == []
