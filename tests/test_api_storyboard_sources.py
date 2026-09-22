from dataclasses import replace

from PIL import Image
import pytest

from tests.test_api_narrative_groups import make_client
from tests.test_storyboard_sources import _source
from novelvideo.narrative_groups.service import load_groups, save_groups


def test_enable_storyboard_contract_is_scoped_cas_and_does_not_enqueue(tmp_path, monkeypatch):
    client, backend = make_client(monkeypatch, tmp_path, beat_count=1)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    original = load_groups(tmp_path, 1)[0]
    url = f"/api/v1/projects/demo/episodes/1/narrative-groups/{original.id}/storyboard-contract"
    body = {"version": 1, "expected_version": 0, "expected_selected_id": ""}
    response = client.put(url, json=body)
    assert response.status_code == 200, response.text
    current = load_groups(tmp_path, 1)[0]
    assert current.storyboard_contract_version == 1
    assert current.stages == original.stages
    assert response.json()["data"]["storyboard_contract_version"] == 1
    assert client.put(url, json=body).status_code == 409
    assert client.put(url, json={**body, "expected_version": 1}).status_code == 200
    assert client.put(url, json={**body, "version": 0}).status_code == 422
    assert backend.calls == []


@pytest.mark.parametrize("reason", ["selection", "render_running", "video_queued", "corrupt"])
def test_enable_storyboard_contract_rejects_stale_busy_or_invalid_source(tmp_path, monkeypatch, reason):
    client, backend = make_client(monkeypatch, tmp_path, beat_count=1)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group = load_groups(tmp_path, 1)[0]
    stages = dict(group.stages)
    expected_id = ""
    if reason == "selection":
        expected_id = "a" * 64
    elif reason == "corrupt":
        stages["render"] = replace(stages["render"], selected_storyboard_id="a" * 64)
        expected_id = "a" * 64
    else:
        stage, status = reason.split("_")
        stages[stage] = replace(stages[stage], status=status)
    group = replace(group, stages=stages)
    save_groups(tmp_path, 1, [group])
    response = client.put(
        f"/api/v1/projects/demo/episodes/1/narrative-groups/{group.id}/storyboard-contract",
        json={"version": 1, "expected_version": 0, "expected_selected_id": expected_id})
    assert response.status_code == (422 if reason == "corrupt" else 409), response.text
    assert load_groups(tmp_path, 1)[0] == group
    assert backend.calls == []


def test_storyboard_sources_query_select_and_stale_selection(tmp_path, monkeypatch):
    client, backend = make_client(monkeypatch, tmp_path, beat_count=1)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group = load_groups(tmp_path, 1)[0]
    first = _source(tmp_path).model_copy(update={"project_id": "demo", "group_id": group.id})
    first = first.model_copy(update={"cells": (first.cells[0].model_copy(update={"shot_id": "beat-1"}),)})
    second = first.model_copy(update={"generation_id": "second-generation"})
    render = replace(group.stages["render"], storyboard_sources=(first.model_dump(mode="json"), second.model_dump(mode="json")),
                     selected_storyboard_id=first.source_id,
                     selected_storyboard_sources={first.batch_id: first.source_id})
    save_groups(tmp_path, 1, [replace(group, stages={**group.stages, "render": render})])
    url = f"/api/v1/projects/demo/episodes/1/narrative-groups/{group.id}/storyboard-sources"
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["selected_storyboard_id"] == first.source_id
    assert all(item["validation"]["valid"] for item in response.json()["data"]["items"])
    selected = client.put(url + "/selection", json={"source_id": second.source_id,
                                                  "expected_selected_id": first.source_id})
    assert selected.status_code == 200, selected.text
    current = load_groups(tmp_path, 1)[0]
    assert current.stages["render"].selected_storyboard_id == second.source_id
    assert current.stages["video"].needs_regeneration
    stale = client.put(url + "/selection", json={"source_id": first.source_id,
                                               "expected_selected_id": first.source_id})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STORYBOARD_SELECTION_CHANGED"
    assert backend.calls == []
    Image.new("RGB", (8, 8), "red").save(tmp_path / "cell.png")
    assert not client.get(url).json()["data"]["items"][0]["validation"]["valid"]
    assert client.get(url).json()["data"]["items"][0]["source_id"] == first.source_id
    invalid = client.put(url + "/selection", json={"source_id": first.source_id,
                                                 "expected_selected_id": second.source_id})
    assert invalid.status_code == 422
    assert load_groups(tmp_path, 1)[0].stages["render"].selected_storyboard_id == second.source_id


@pytest.mark.parametrize("invalid", [None, "missing", "changed"])
def test_split_retry_enqueues_original_identity_or_blocks_without_revision_change(tmp_path, monkeypatch, invalid):
    from novelvideo.narrative_groups.storyboard_sources import capture_storyboard_split_input

    client, backend = make_client(monkeypatch, tmp_path, beat_count=1)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group = load_groups(tmp_path, 1)[0]
    grid = tmp_path / "paid-grid.png"
    Image.new("RGB", (90, 160), "blue").save(grid)
    snapshot = capture_storyboard_split_input(str(grid), {
        "project_id": "demo", "episode": 1, "group_id": group.id, "generation_id": "original-task",
        "layout": {"rows": 1, "columns": 1}, "cell_to_beat": [{"cell": 0, "beat_id": "beat-1"}],
        "aspect_ratio": "16:9", "model": "gpt-image-2", "image_size": "2K",
    }, tmp_path).model_dump(mode="json")
    render = replace(group.stages["render"], status="partial_failure", grid_asset=str(grid),
        provider_parameters={} if invalid == "missing" else {"storyboard_split_input": snapshot})
    save_groups(tmp_path, 1, [replace(group, storyboard_contract_version=1,
                                     stages={**group.stages, "render": render})])
    if invalid == "changed":
        Image.new("RGB", (90, 160), "red").save(grid)
    response = client.post(f"/api/v1/projects/demo/episodes/1/narrative-groups/{group.id}/render/split")
    if invalid:
        assert response.status_code == 422, response.text
        assert backend.calls == []
        assert load_groups(tmp_path, 1)[0].stages["render"].revision == render.revision
    else:
        assert response.status_code == 202, response.text
        payload = backend.calls[0][1]["payload"]
        assert payload["storyboard_split_input"] == snapshot
        assert payload["generation_id"] == "original-task"
        assert payload["storyboard_contract_version"] == 1
        assert payload["aspect_ratio"] == "16:9"
        assert payload["image_size"] == "2K"
