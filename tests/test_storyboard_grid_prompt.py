"""Lightweight grid prompts carry style, references and explicit empty cells."""

from novelvideo.freezone.orchestration.adapters.requests import build_request


def test_grid_prompt_injects_style_references_and_empty_cells():
    node = {
        "id": "grid",
        "type": "storyboardGenNode",
        "data": {
            "gridRows": 2,
            "gridCols": 3,
            "aspectRatio": "16:9",
            "model": "gpt-image-2",
            "stylePrompt": "3D 国漫动画电影风格，柔和全局光",
            "referenceNote": "参考图1 林渡：只取面部与服装",
            "referenceUrls": ["/static/a.png", "/static/b.png"],
            "frames": [{"description": f"分镜{i}"} for i in range(1, 6)],
        },
    }
    _, body = build_request(node, "generate", [], canvas_id="c", attempt_id="a")
    prompt = body["prompt"]
    assert prompt.startswith("Subject:")
    assert "3D 国漫动画电影风格" in prompt
    assert "参考图1 林渡" in prompt
    assert "第 6 至 6 格留空为纯黑" in prompt
    assert "Use case:" in prompt and "Constraints:" in prompt
    assert body["reference_urls"] == ["/static/a.png", "/static/b.png"]
