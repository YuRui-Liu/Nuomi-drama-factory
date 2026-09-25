import json

from novelvideo.narrative_groups.models import CellMapping, GridLayout, NarrativeGroup
from novelvideo.narrative_groups.service import load_groups, save_groups, sidecar_path


def _group(group_id: str = "ng-01", **overrides) -> NarrativeGroup:
    base = dict(
        id=group_id,
        ordinal=1,
        beat_ids=("shot-01-01", "shot-01-02"),
        layout=GridLayout(rows=1, columns=2, capacity=2),
        cell_to_beat=(
            CellMapping(cell=0, beat_id="shot-01-01"),
            CellMapping(cell=1, beat_id="shot-01-02"),
        ),
    )
    base.update(overrides)
    return NarrativeGroup(**base)


def test_group_defaults_to_no_image_prompt_overrides():
    assert _group().image_prompt_overrides == {}


def test_sidecar_without_the_field_still_loads(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group()])

    path = sidecar_path(project, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["groups"]:
        item.pop("image_prompt_overrides", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_groups(project, 1)[0].image_prompt_overrides == {}


def test_sidecar_without_cell_to_beat_still_loads(tmp_path):
    """旧 sidecar 可能缺少 cell_to_beat；加载不得因此失败。"""

    project = tmp_path / "proj"
    save_groups(project, 1, [_group()])

    path = sidecar_path(project, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["groups"]:
        item.pop("image_prompt_overrides", None)
        item.pop("cell_to_beat", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_groups(project, 1)[0].image_prompt_overrides == {}


def test_overrides_survive_a_sidecar_round_trip(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group(image_prompt_overrides={"shot-01-01": "改成铜甲"})])

    assert load_groups(project, 1)[0].image_prompt_overrides == {"shot-01-01": "改成铜甲"}
