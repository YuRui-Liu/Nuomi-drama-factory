"""scriptNode `project`: adopted director revision -> storyboard table rows."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.projection import project_plan_rows
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.freezone.orchestration.adapters import NodeAdapter, available_operations
from novelvideo.freezone.orchestration.capabilities import capabilities
from novelvideo.freezone.orchestration.graph import execution_order
from novelvideo.freezone.orchestration.models import OrchestrationError, fingerprint

REVISION_ID = "rev-1"


@pytest.fixture
def project(monkeypatch, tmp_path):
    from novelvideo.api.routes import director_workspace

    ctx = SimpleNamespace(project_id="p", output_dir=tmp_path / "project", state_dir=tmp_path / "state")
    ctx.output_dir.mkdir()
    ctx.state_dir.mkdir()

    async def resolve(project, user, *, role):
        return ctx

    monkeypatch.setattr(director_workspace, "_resolve", resolve)
    return ctx


def _revision(revision_id: str = REVISION_ID) -> DirectorPlanRevision:
    shot = ShotPlan(
        id="s1",
        source_span_ids=("line-1",),
        subject="林渡",
        action="抬手",
        shot_size="近景",
        camera_angle="平视",
        composition="左三分",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        duration_seconds=6,
        technique_intent="克制的惊讶",
    )
    group = NarrativeGroupPlan(
        id="g1",
        ordinal=1,
        source_span_ids=("line-1",),
        scene_anchor="门口",
        time_anchor="夜",
        geo_layout="门口平台；机位始终在东侧。",
        objective="确认门外",
        visible_turn="开门",
        relation_to_previous="single",
        shots=(shot,),
    )
    return DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="review_required",
        source_script_hash="sha256:test",
        director_model="test",
        prompt_version="v2",
        project_style_snapshot_id="test-style",
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime.now(timezone.utc),
    )


def _node(**data):
    payload = {"operation": "project", "episodeNumber": 1, "directorRevisionId": REVISION_ID}
    payload.update(data)
    return {"id": "script", "type": "scriptNode", "data": payload}


def _run(node):
    return {"project_id": "p", "actor": {"id": "u"}, "canvas_id": "canvas",
            "snapshot": {"nodes": [node], "edges": []}}


async def test_project_projects_rows_director_block_and_panel_spec(project):
    revision = _revision()
    DirectorPlanStore(project.output_dir).save(revision)

    node = _node()
    adapter = NodeAdapter(project.state_dir)
    assert adapter.operation(node) == "project"
    assert adapter.requires_external_submission(_run(node), {"node_id": "script"}) is False

    submitted = await adapter.submit(_run(node), {"id": "attempt-1", "node_id": "script"}, [])
    assert submitted["local"] is True
    payload = submitted["result"]["workflowResult"]
    rows = project_plan_rows(revision)
    assert payload["episode"] == 1
    assert payload["directorRevisionId"] == REVISION_ID
    assert payload["rows"] == rows
    assert payload["projectionHash"] == fingerprint(rows)

    row = payload["rows"][0]
    assert row["shot_no"] == 1
    assert row["group_id"] == "g1"
    assert row["segment_index"] == 1
    assert row["duration"] == 6
    assert row["visual_description"] == "手停在近前 → 抬手 → 手未接触"
    director = row["director"]
    assert director["group_id"] == row["group_id"]
    assert director["segment_index"] == row["segment_index"]
    assert director["technique_intent"] == "克制的惊讶"
    assert director["framing"] == {"shot_size": "近景", "angle": "平视", "composition": "左三分"}
    assert director["continuity"] == {
        "start_state": "手停在近前", "end_state": "手未接触", "handoff": "",
    }
    assert director["panel_spec"]["prompt_text"]
    assert "手停在近前" in director["panel_spec"]["prompt_text"]
    assert "门口平台" in director["panel_spec"]["prompt_text"]


async def test_project_requires_bound_episode_and_revision(project):
    adapter = NodeAdapter(project.state_dir)

    with pytest.raises(OrchestrationError) as missing_both:
        await adapter.submit(_run(_node(episodeNumber=None, directorRevisionId="")),
                             {"id": "attempt-1", "node_id": "script"}, [])
    assert missing_both.value.code == "input_required"
    assert set(missing_both.value.details["fields"]) == {"episodeNumber", "directorRevisionId"}

    with pytest.raises(OrchestrationError) as missing_revision:
        await adapter.submit(_run(_node(directorRevisionId="  ")),
                             {"id": "attempt-1", "node_id": "script"}, [])
    assert missing_revision.value.code == "input_required"
    assert missing_revision.value.details["fields"] == ["directorRevisionId"]

    with pytest.raises(OrchestrationError) as unsafe_revision:
        await adapter.submit(_run(_node(directorRevisionId="../../etc/passwd")),
                             {"id": "attempt-1", "node_id": "script"}, [])
    assert unsafe_revision.value.code == "input_required"


async def test_project_rejects_unknown_revision(project):
    adapter = NodeAdapter(project.state_dir)
    with pytest.raises(OrchestrationError) as unknown:
        await adapter.submit(_run(_node(directorRevisionId="absent")),
                             {"id": "attempt-1", "node_id": "script"}, [])
    assert unknown.value.code == "director_revision_not_found"


def test_project_operation_is_available_and_fixed_input():
    assert ("scriptNode", "project") in available_operations()
    entry = next(item for item in capabilities(available_operations())
                 if item["node_type"] == "scriptNode")
    assert {"name": "project", "available": True, "reason": None} in entry["operations"]
    assert entry["default_operation"] == "generate"

    nodes = [
        {"id": "text", "type": "textAnnotationNode", "data": {"text": "剧本"}},
        _node(),
    ]
    edges = [{"id": "text--script", "source": "text", "target": "script",
              "data": {"sourcePort": "text", "targetPort": "text"}}]
    assert execution_order(nodes, edges, "script", operation="project") == ["script"]
    assert execution_order(nodes, edges, "script", operation="generate") == ["text", "script"]

async def test_scheduler_projects_workflow_result_onto_the_script_node(project):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    revision = _revision()
    DirectorPlanStore(project.output_dir).save(revision)
    state = project.state_dir
    create_canvas(state, "canvas", project_id="p", name="projection", actor_id="u")
    patch_graph(state, "canvas", base_revision=1, request_id="seed",
                operations=[{"op": "add_node", "node": _node()}])

    store = RunStore(state)
    run = store.create(read_canvas(state, "canvas"), target="script", request_id="project-run",
                       project_id="p", settings={"concurrency": 1}, actor={"id": "u"},
                       operation="project")
    scheduler = Scheduler(store, NodeAdapter(state))
    finished = run
    for _ in range(4):
        finished = await scheduler.tick(run["id"])
        if finished["status"] in {"succeeded", "failed", "blocked"}:
            break
    assert finished["status"] == "succeeded", finished
    attempt = finished["attempts"]["script"][-1]
    assert attempt["artifacts"][0]["kind"] == "json"

    node = next(item for item in read_canvas(state, "canvas")["nodes"] if item["id"] == "script")
    result = node["data"]["workflowResult"]
    assert result["rows"] == project_plan_rows(revision)
    assert result["projectionHash"] == fingerprint(result["rows"])
    assert result["directorRevisionId"] == REVISION_ID
    assert node["data"]["directorRevisionId"] == REVISION_ID
    assert node["data"]["orchestration"]["attempt_id"] == attempt["id"]
