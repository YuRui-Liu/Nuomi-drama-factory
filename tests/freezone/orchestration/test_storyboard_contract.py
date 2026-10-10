from novelvideo.freezone.orchestration.planning import compile_plan
from novelvideo.freezone.orchestration.adapters.requests import build_request
from tests.freezone.orchestration.test_planning import SCRIPT, blueprint


def test_planned_storyboard_uses_browser_grid_contract():
    plan = compile_plan(SCRIPT, blueprint(), duration=20, aspect_ratio="16:9")
    board = next(n for n in plan["nodes"] if n["type"] == "storyboardGenNode")
    assert board["data"].get("gridRows") == 2
    assert board["data"].get("gridCols") == 2
    assert board["data"].get("storyboardMode") == "narrative"
    assert "林澈：这道题，我会。" in board["data"]["prompt"]


def test_executor_consumes_visible_grid_and_frame_edits():
    node = {"id": "board", "type": "storyboardGenNode", "data": {
        "group_id": "g1", "prompt": "林澈递过试卷", "rows": 2, "cols": 2,
        "gridRows": 1, "gridCols": 3,
        "frames": [{"description": "手部特写"}],
    }}
    _, body = build_request(node, "generate", [], canvas_id="c", attempt_id="a")
    assert "1 行 3 列" in body["prompt"]
    assert "林澈递过试卷" in body["prompt"]
    assert "手部特写" in body["prompt"]


def test_saved_request_cannot_hide_edited_storyboard_prompt():
    node = {"id": "b", "type": "storyboardGenNode", "data": {
        "group_id": "g", "prompt": "新的剧情", "gridRows": 1, "gridCols": 2,
        "request": {"prompt": "过期剧情"},
    }}
    _, body = build_request(node, "generate", [], canvas_id="c", attempt_id="a")
    assert "新的剧情" in body["prompt"]
    assert "过期剧情" not in body["prompt"]
