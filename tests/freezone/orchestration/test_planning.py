import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError

SCRIPT = "# 这次，别留白\n\n## 第一场 教室 日\n林澈：这道题，我会。\n周野推来草稿。\n\n## 第二场 走廊 日\n周野：一起报名。\n两人并肩走远。\n【本集完】"


def blueprint():
    return {"title": "这次，别留白", "assets": [{"id": "pair", "name": "林澈与周野", "prompt": "两个学生", "kind": "character"}],
            "groups": [{"id": "g1", "scene_id": "scene-1", "start_line": 3, "end_line": 6, "duration": 10,
                        "prompt": "教室互相拆穿", "asset_ids": ["pair"]},
                       {"id": "g2", "scene_id": "scene-2", "start_line": 7, "end_line": 10, "duration": 10,
                        "prompt": "走廊共同报名", "asset_ids": ["pair"]}]}


def test_plan_preserves_source_and_builds_shared_asset_group_graph():
    from novelvideo.freezone.orchestration.planning import compile_plan
    plan = compile_plan(SCRIPT, blueprint(), duration=20, aspect_ratio="16:9")
    assert plan["duration"] == 20
    assert len(plan["coverage"]) == 2
    assert len([n for n in plan["nodes"] if n["type"] == "imageGenNode"]) == 1
    assert len([n for n in plan["nodes"] if n["type"] == "storyboardGenNode"]) == 2
    directors = [n for n in plan["nodes"] if n["type"] == "videoDirectorNode"]
    assert len(directors) == 2
    assert "林澈：这道题，我会。" in directors[0]["data"]["draft"]["segments"][0]["prompt"]
    assert plan["nodes"][-1]["data"]["timeline"][0]["node_id"] == directors[0]["id"]
    assert all(e["sourceHandle"] == "source" and e["targetHandle"] == "target" for e in plan["edges"])
    assert all(e["data"]["sourcePort"] == e["data"]["targetPort"] for e in plan["edges"])
    director_edges = [e for e in plan["edges"] if e["target"].startswith("video-")]
    assert director_edges and all(e["data"].get("edgeKind") == "videoDirectorImage" and
                                  e["data"].get("slot") == {"kind": "reference"} for e in director_edges)


@pytest.mark.parametrize("mutate,code", [
    (lambda b: b["groups"].pop(), "coverage_gap"),
    (lambda b: b["groups"][1].update(start_line=5), "coverage_overlap"),
    (lambda b: b["groups"][0].update(asset_ids=["unknown"]), "unknown_asset"),
    (lambda b: b["groups"][0].update(duration=0), "invalid_blueprint"),
])
def test_invalid_plan_blocks_before_paid_generation(mutate, code):
    from novelvideo.freezone.orchestration.planning import compile_plan
    b = blueprint()
    mutate(b)
    with pytest.raises(OrchestrationError) as raised:
        compile_plan(SCRIPT, b, duration=20, aspect_ratio="16:9")
    assert raised.value.code == code


def test_truncated_source_cannot_be_reported_complete():
    from novelvideo.freezone.orchestration.planning import source_scenes
    with pytest.raises(OrchestrationError, match="完整"):
        source_scenes("## 第一场 教室\n**周野：**")


def test_plan_preserves_explicit_scene_timings():
    from novelvideo.freezone.orchestration.planning import compile_plan
    script = SCRIPT.replace("第一场 教室 日", "第一场 教室 日（8 秒）").replace("第二场 走廊 日", "第二场 走廊 日（12 秒）")
    with pytest.raises(OrchestrationError) as raised:
        compile_plan(script, blueprint(), duration=20, aspect_ratio="16:9")
    assert raised.value.code == "scene_duration_mismatch"


def test_plan_freezes_configured_image_route_on_every_image_generator():
    from novelvideo.freezone.orchestration.planning import compile_plan
    route = {"provider": "grsai", "model": "gpt-image-2"}
    plan = compile_plan(SCRIPT, blueprint(), duration=20, aspect_ratio="16:9", image_route=route)
    for node in plan["nodes"]:
        if node["type"] in {"imageGenNode", "storyboardGenNode"}:
            assert node["data"]["request"] == route
            assert node["data"]["model"] == route["model"]
