from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.narrative_groups.models import (
    CellMapping,
    GridLayout,
    GroupStageState,
    NarrativeGroup,
)
from novelvideo.narrative_groups.service import (
    load_group_video_prompt_manifest,
    save_groups,
)
from tests.test_api_narrative_groups import (
    activate_director_plan,
    install_empty_planned_snapshot,
    make_client,
)


def test_manifest_read_materializes_active_projection_before_using_stage_state(
    tmp_path: Path,
) -> None:
    stale_manifest = tmp_path / "stale-manifest.json"
    stale_manifest.write_text('{"entries": []}', encoding="utf-8")
    save_groups(
        tmp_path,
        1,
        [
            NarrativeGroup(
                id="director-group",
                ordinal=1,
                beat_ids=("old-span",),
                layout=GridLayout(rows=1, columns=1, capacity=1),
                cell_to_beat=(CellMapping(cell=0, beat_id="old-shot"),),
                stages={
                    "sketch": GroupStageState(),
                    "render": GroupStageState(),
                    "video": GroupStageState(
                        status="completed",
                        revision=2,
                        manifest_asset=str(stale_manifest),
                    ),
                },
                source_span_ids=("old-span",),
                shot_ids=("old-shot",),
                director_revision_id="rev-old",
            )
        ],
    )
    activate_director_plan(tmp_path)

    with pytest.raises(FileNotFoundError, match="manifest is unavailable"):
        load_group_video_prompt_manifest(tmp_path, 1, "director-group")


def test_active_group_generation_payload_contains_director_shots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, backend = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)
    payload = install_empty_planned_snapshot(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/"
        "director-group/sketch/generate",
        json=payload,
    )

    assert response.status_code == 202
    [shot] = backend.calls[0][1]["payload"]["beats"]
    assert shot["id"] == "shot-1"
    assert shot["source_span_ids"] == ["span-1"]
    assert shot["visual_description"] == "hero opens the door Scene: hallway; time: night."
    assert shot["duration_seconds"] == 3
