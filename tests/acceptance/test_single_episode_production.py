"""Controlled single-episode acceptance using only deterministic fake providers."""

from datetime import datetime, timezone

import pytest


def _candidate_revision():
    from novelvideo.director_plan.models import (
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotPlan,
        StyleProjections,
        StyleSnapshot,
        ValidationReport,
    )

    style = StyleSnapshot(
        snapshot_id="style-acceptance",
        style_id="acceptance",
        style_version="v1",
        catalog_hash="catalog-acceptance",
        style_hash="style-hash-acceptance",
        projections=StyleProjections(director="grounded drama", image="film still",
                                     video="natural motion", panel_tag="PANEL"),
    )
    return DirectorPlanRevision(
        revision_id="candidate-single-episode-v1",
        episode=1,
        status="review_required",
        source_script_hash="script-candidate-hash",
        director_model="acceptance-fake-director",
        prompt_version="acceptance-v1",
        project_style_snapshot_id=style.snapshot_id,
        project_style_snapshot=style,
        groups=(NarrativeGroupPlan(
            id="ng-single-01",
            ordinal=1,
            source_span_ids=("span-1",),
            scene_anchor="旧车站",
            time_anchor="夜",
            objective="找到失联的同伴",
            visible_turn="站台灯亮起",
            relation_to_previous="single",
            shots=(ShotPlan(
                id="shot-1",
                source_span_ids=("span-1",),
                subject="林岚",
                action="走向亮起的站台灯",
                visible_start_state="站台昏暗",
                visible_end_state="站台灯亮起",
                duration_seconds=4,
            ),),
            style_snapshot_id=style.snapshot_id,
        ),),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_single_episode_graph_runs_to_assembled_result_with_fake_providers(tmp_path):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.models import fingerprint, RESULT_FIELDS
    from novelvideo.freezone.orchestration.production_graph import materialize_production_graph
    from novelvideo.freezone.orchestration.projection import create_canvas, project_result
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.store import RunStore

    state_dir = tmp_path / "state"
    project_dir = tmp_path / "project"
    state_dir.mkdir()
    project_dir.mkdir()
    director = DirectorPlanStore(project_dir)
    candidate = _candidate_revision()
    director.save(candidate)
    active = director.activate(1, candidate.revision_id)
    assert active.revision_id == candidate.revision_id

    create_canvas(state_dir, "single-episode", project_id="p", name="单集受控验收", actor_id="alice")
    materialized = materialize_production_graph(
        state_dir, "single-episode", base_revision=1, request_id="accepted-production-v1",
        episode=1, revision_id=active.revision_id,
        group_ids=[group.id for group in active.groups],
    )
    canvas = materialized["canvas"]
    target = materialized["target"]

    class FakeProviderAdapter:
        """Deterministic provider boundary; never calls a real media provider."""

        def __init__(self):
            self.submissions = []

        async def submit(self, run, attempt, upstream):
            node = next(item for item in run["snapshot"]["nodes"] if item["id"] == attempt["node_id"])
            data = node["data"]
            assert DirectorPlanStore(project_dir).load_active(1).revision_id == data["directorRevisionId"]
            if node["type"] == "narrativeGroupStoryboardNode":
                assert data["groupId"] in {item.id for item in active.groups}
                result = {"narrativeGroupResult": {"status": "completed", "sourceRevisionId": active.revision_id,
                                                   "groupId": data["groupId"], "stage": "render"}}
            elif node["type"] == "narrativeGroupVideoNode":
                assert upstream and upstream[0]["status"] == "succeeded"
                result = {"narrativeGroupResult": {"status": "completed", "sourceRevisionId": active.revision_id,
                                                   "groupId": data["groupId"], "stage": "video"},
                          "videoUrl": f"fake://episode-1/{data['groupId']}.mp4"}
            else:
                assert node["type"] == "episodeComposeNode"
                assert data["productionGroupIds"] == [group.id for group in active.groups]
                videos = [item["result"].get("videoUrl") for item in upstream]
                assert videos == [f"fake://episode-1/{group.id}.mp4" for group in active.groups]
                result = {"narrativeGroupResult": {"status": "completed", "sourceRevisionId": active.revision_id,
                                                   "groupIds": list(data["productionGroupIds"]),
                                                   "assembledVideoCount": len(videos)},
                          "resultVideoUrl": "fake://episode-1/final.mp4"}
            self.submissions.append((node["type"], data["directorRevisionId"], data.get("groupId")))
            return {"fake_provider": True, "result": result}

        async def reconcile(self, _run, attempt):
            return {"status": "succeeded", "result": attempt["submission"]["result"]}

        async def verify(self, _run, attempt):
            result = attempt["result"]
            assert result
            return [{"kind": "json", "sha256": fingerprint(result)}]

        async def project(self, run, attempt, **_kwargs):
            node = next(item for item in run["snapshot"]["nodes"] if item["id"] == attempt["node_id"])
            allowed = RESULT_FIELDS[node["type"]]
            result = {key: value for key, value in attempt["result"].items() if key in allowed}
            project_result(state_dir, run["canvas_id"], node_id=node["id"],
                           expected_input=run["input_fingerprints"][node["id"]],
                           attempt_id=attempt["id"], result=result)

    store = RunStore(tmp_path / "runs")
    run = store.create(canvas, target=target, request_id="single-episode-run", project_id="p",
                       settings={"concurrency": 1}, actor={"id": "alice"}, operation="compose")
    adapter = FakeProviderAdapter()
    scheduler = Scheduler(store, adapter)

    for _ in range(12):
        run = await scheduler.tick(run["id"])
        if run["status"] in {"succeeded", "failed", "blocked", "submission_unknown"}:
            break

    final_canvas = read_canvas(state_dir, "single-episode")
    final_node = next(item for item in final_canvas["nodes"] if item["id"] == target)
    assert run["status"] == "succeeded", run["attempts"][target][-1].get("error")
    assert [entry[0] for entry in adapter.submissions] == [
        "narrativeGroupStoryboardNode", "narrativeGroupVideoNode", "episodeComposeNode",
    ]
    assert {entry[1] for entry in adapter.submissions} == {active.revision_id}
    assert final_node["data"]["resultVideoUrl"] == "fake://episode-1/final.mp4"
    assert final_node["data"]["narrativeGroupResult"] == {
        "status": "completed", "sourceRevisionId": active.revision_id,
        "groupIds": ["ng-single-01"], "assembledVideoCount": 1,
    }
