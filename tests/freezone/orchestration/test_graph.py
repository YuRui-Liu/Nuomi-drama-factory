from pathlib import Path
import re

import pytest


def node(key, kind="textAnnotationNode", **data):
    return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": data}


def edge(source, target, **ports):
    return {"id": source + "-" + target, "source": source, "target": target, **ports}


def test_capability_catalog_matches_frontend_and_expands_skills():
    from novelvideo.freezone.orchestration.capabilities import capabilities
    from novelvideo.freezone.skill_registry import list_skills
    catalog = capabilities()
    source = (Path(__file__).resolve().parents[3] / "frontend/src/features/canvas/domain/canvasNodes.ts").read_text()
    declared = set(re.findall(r": '(\w+Node)'", source.split("} as const;")[0]))
    assert {entry["node_type"] for entry in catalog} == declared
    skill = next(entry for entry in catalog if entry["node_type"] == "skillNode")
    assert {item["id"] for item in skill["skills"]} == {s.id for s in list_skills()}
    assert all(entry["operations"] for entry in catalog)


def test_target_subgraph_rejects_cycle():
    from novelvideo.freezone.orchestration.graph import validate_graph
    report = validate_graph([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])
    assert "cycle" in {e["code"] for e in report["errors"]}
    assert not report["valid"]


@pytest.mark.parametrize("nodes,edges,code", [
    ([node("a"), node("a")], [], "duplicate_node"),
    ([node("a")], [edge("a", "missing")], "missing_endpoint"),
    ([node("a", "inventedNode")], [], "unknown_node_type"),
    ([node("a")], [edge("a", "a")], "cycle"),
    ([node("a", "audioNode"), node("b", "pano360ViewerNode")], [edge("a", "b")], "incompatible_port"),
    ([node("a"), node("b")], [edge("a", "b", targetHandle="invented")], "unknown_port"),
    ([node("a", "skillNode", skill_id="invented")], [], "unknown_skill"),
    ([{**node("a"), "parentId": "missing"}], [], "invalid_parent"),
    ([{**node("a", "groupNode"), "parentId": "b"}, {**node("b", "groupNode"), "parentId": "a"}], [], "parent_cycle"),
])
def test_graph_rejects_invalid_structure(nodes, edges, code):
    from novelvideo.freezone.orchestration.graph import validate_graph
    report = validate_graph(nodes, edges)
    assert not report["valid"]
    assert code in {item["code"] for item in report["errors"]}


def test_target_order_excludes_independent_nodes_and_container():
    from novelvideo.freezone.orchestration.graph import execution_order
    nodes = [node("container", "groupNode"), node("unused"), node("source"),
             {**node("image", "imageGenNode"), "parentId": "container"}]
    assert execution_order(nodes, [edge("source", "image")], "image") == ["source", "image"]


def test_input_fingerprint_ignores_layout_and_result_but_not_prompt():
    from novelvideo.freezone.orchestration.models import input_fingerprint
    original = node("image", "imageGenNode", prompt="first", model="saved")
    moved = {**original, "position": {"x": 99, "y": 2},
             "data": {**original["data"], "displayName": "new name", "imageUrl": "/result.png"}}
    assert input_fingerprint(original) == input_fingerprint(moved)
    moved["data"]["prompt"] = "second"
    assert input_fingerprint(original) != input_fingerprint(moved)


def test_input_media_is_part_of_source_fingerprint():
    from novelvideo.freezone.orchestration.models import input_fingerprint
    assert input_fingerprint(node("a", "uploadNode", imageUrl="/one.png")) != input_fingerprint(node("a", "uploadNode", imageUrl="/two.png"))
