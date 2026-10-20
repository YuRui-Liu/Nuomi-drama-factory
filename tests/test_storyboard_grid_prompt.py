"""Grid prompts must use frames, never the whole upstream storyboard table."""

from novelvideo.freezone.orchestration.adapters.requests import build_request


def test_grid_prompt_ignores_upstream_script_result():
    node = {
        "id": "grid",
        "type": "storyboardGenNode",
        "data": {
            "gridRows": 2,
            "gridCols": 3,
            "aspectRatio": "16:9",
            "model": "gpt-image-2",
            "frames": [{"description": "分镜一：林渡护喉，丧尸贴近。"}],
        },
    }
    upstream = [{"node_id": "table", "result": {"scriptResult": {"rows": [
        {"shot_no": 1, "visual_description": "LEAKED_ROW"},
    ]}}}]
    _, body = build_request(node, "generate", upstream, canvas_id="c", attempt_id="a")
    prompt = body["prompt"]
    assert "LEAKED_ROW" not in prompt
    assert "分镜一" in prompt
    assert prompt.startswith("生成 2 行 3 列分镜图")
