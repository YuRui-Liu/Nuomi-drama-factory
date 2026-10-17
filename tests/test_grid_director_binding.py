"""Canvas grid requests must retain the selected director revision."""

import pytest
from types import SimpleNamespace

from tests.test_api_narrative_groups import (
    activate_director_plan,
    install_empty_planned_snapshot,
    make_client,
)
from novelvideo.narrative_groups import service


def test_grid_rejects_stale_director_before_enqueue(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    planned = install_empty_planned_snapshot(monkeypatch, tmp_path)
    activate_director_plan(tmp_path, group_id="ng-01", shot_id="beat-1")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={**planned, "director_revision_id": "old-director"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DIRECTOR_REVISION_CHANGED"
    assert not backend.calls


def test_grid_freezes_director_revision_in_task(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    planned = install_empty_planned_snapshot(monkeypatch, tmp_path)
    activate_director_plan(tmp_path, group_id="ng-01", shot_id="beat-1")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={**planned, "director_revision_id": "rev-api-active"},
    )
    assert response.status_code == 202, response.text
    assert backend.calls[0][1]["payload"]["director_revision_id"] == "rev-api-active"


def test_reservation_and_completion_reject_different_director(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path, group_id="ng-01", shot_id="beat-1")
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    with pytest.raises(RuntimeError, match="director revision"):
        service.advance_revision(tmp_path, 1, "ng-01", "render",
                                 expected_director_revision_id="old-director")
    group, revision = service.advance_revision(tmp_path, 1, "ng-01", "render")
    updated = service.record_stage_result(
        tmp_path, 1, "ng-01", "render", expected_revision=revision,
        expected_director_revision_id="old-director", status="completed",
        grid_asset="wrong-image.png",
    )
    assert updated.stages["render"].grid_asset == group.stages["render"].grid_asset
    assert updated.stages["render"].status == "queued"


@pytest.mark.asyncio
async def test_stale_queued_grid_never_calls_generator(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import narrative_group

    activate_director_plan(tmp_path, group_id="ng-01", shot_id="beat-1")
    monkeypatch.setattr(narrative_group, "stage_payload", lambda *args: {
        "layout": {"rows": 1, "columns": 1}, "beat_ids": ["beat-1"],
        "cell_to_beat": [{"cell": 0, "beat_id": "beat-1"}],
    })
    async def forbidden(*args, **kwargs):
        pytest.fail("stale grid reached paid generation")
    monkeypatch.setattr(narrative_group, "_generate_grid", forbidden)
    with pytest.raises(RuntimeError, match="director revision"):
        await narrative_group._execute_single({"episode": 1, "payload": {
            "project_dir": str(tmp_path), "group_id": "ng-01", "stage": "render",
            "revision": 1, "director_revision_id": "old-director",
        }}, SimpleNamespace(output_dir=tmp_path), split_only=False)


def test_grid_task_results_expose_safe_batch_urls(tmp_path):
    from novelvideo.api.routes.tasks import _sanitize_task_result_for_client

    image = tmp_path / 'renders' / 'grid.png'
    image.parent.mkdir()
    image.write_bytes(b'fixture')
    result = _sanitize_task_result_for_client({
        'grid_asset': str(image),
        'batches': [{'batch_id': 'pack-1', 'revision': 2, 'grid_asset': str(image)}],
        'invalid': {'grid_asset': str(tmp_path.parent / 'outside.png')},
    }, ctx=SimpleNamespace(output_dir=tmp_path, project_id='project'))
    assert result['grid_url'].startswith('/static/projects/project/renders/grid.png')
    assert result['batches'][0]['grid_url'] == result['grid_url']
    assert 'grid_asset' not in result
    assert 'grid_asset' not in result['invalid']
