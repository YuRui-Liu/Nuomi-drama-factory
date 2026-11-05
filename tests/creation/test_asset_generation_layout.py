import json
from types import SimpleNamespace

from novelvideo.creation.asset_generation import AssetGeneration
from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
from novelvideo.creation.projection import ProjectionService
from novelvideo.creation.store import ObjectStore
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.paths import canvas_path


def _workspace(tmp_path, nodes=None):
    state_dir = tmp_path / "state"
    output_dir = tmp_path / "media"
    state_dir.mkdir()
    output_dir.mkdir()
    ctx = SimpleNamespace(
        project_id="p", requester_user_id="alice", state_dir=state_dir, output_dir=output_dir
    )
    objects = ObjectStore(state_dir)
    created = CanvasWorkspace(objects, project_id="p").create(
        "alice", CanvasCreate(request_id="asset-layout-canvas", kind="concept", title="资产布局")
    )
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]["canvas_id"]
    canvas = read_canvas(state_dir, canvas_id)
    canvas["nodes"] = nodes or []
    canvas["edges"] = []
    canvas["revision"] += 1
    canvas_path(state_dir, canvas_id).write_text(json.dumps(canvas), encoding="utf-8")
    return ctx, canvas_id


def _body(canvas_id, revision, request_id):
    return {
        "request_id": request_id,
        "canvas_id": canvas_id,
        "expected_canvas_revision": revision,
        "kind": "character",
        "name": f"角色-{request_id}",
        "description": "夜班记者",
        "prompt": "雨夜站台中的记者肖像",
        "source_proposal": {"title": "人物设定", "text": "林岚是夜班记者。"},
    }


def _node_rect(node):
    x, y = node["position"]["x"], node["position"]["y"]
    width = node.get("width", (node.get("style") or {}).get("width"))
    height = node.get("height", (node.get("style") or {}).get("height"))
    return x, y, x + width, y + height


def _overlap(left, right):
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]


def test_three_asset_design_groups_get_distinct_top_level_space_without_moving_existing_nodes(tmp_path):
    existing = {
        "id": "existing-upload",
        "type": "uploadNode",
        "position": {"x": 0, "y": 0},
        "width": 320,
        "height": 350,
        "data": {"imageUrl": "/static/projects/p/assets/reference.png"},
    }
    ctx, canvas_id = _workspace(tmp_path, [existing])
    generation = AssetGeneration(ctx)
    original_positions = {existing["id"]: dict(existing["position"])}
    group_ids = []

    for index in range(3):
        canvas = read_canvas(ctx.state_dir, canvas_id)
        original_positions.update({node["id"]: dict(node["position"])
                                   for node in canvas["nodes"] if node["id"] not in original_positions})
        result = generation.prepare("alice", _body(canvas_id, canvas["revision"], f"layout-{index}"))
        assert result["projection_status"] == "applied"
        group_ids.append(result["group_id"])
        projected = read_canvas(ctx.state_dir, canvas_id)
        projected_nodes = {node["id"]: node for node in projected["nodes"]}
        assert all(projected_nodes[node_id]["position"] == position
                   for node_id, position in original_positions.items())

    canvas = read_canvas(ctx.state_dir, canvas_id)
    nodes = {node["id"]: node for node in canvas["nodes"]}
    groups = [nodes[group_id] for group_id in group_ids]

    assert len({(group["position"]["x"], group["position"]["y"]) for group in groups}) == 3
    assert all(not _overlap(_node_rect(left), _node_rect(right))
               for index, left in enumerate(groups) for right in groups[index + 1:])
    assert all(not _overlap(_node_rect(group), _node_rect(nodes["existing-upload"])) for group in groups)

    for group in groups:
        text_node = next(node for node in canvas["nodes"]
                         if node.get("parentId") == group["id"] and node["type"] == "textAnnotationNode")
        image_node = next(node for node in canvas["nodes"]
                          if node.get("parentId") == group["id"] and node["type"] == "imageGenNode")
        group_width, group_height = group["width"], group["height"]
        assert group["style"] == {"width": group_width, "height": group_height}
        assert text_node["data"]["content"] == "林岚是夜班记者。"
        assert text_node["width"] == 440 and text_node["height"] == 320
        assert image_node["width"] == 580 and image_node["height"] == 360
        assert text_node["position"] == {"x": 20, "y": 34}
        assert image_node["position"] == {"x": 500, "y": 34}
        assert group_width >= image_node["position"]["x"] + image_node["width"] + 20
        assert group_height >= image_node["position"]["y"] + image_node["height"] + 20
        assert not _overlap(_node_rect(text_node), _node_rect(image_node))


def test_asset_design_inside_selected_group_uses_sibling_space_and_keeps_existing_nodes_fixed(tmp_path):
    parent = {
        "id": "selected-group",
        "type": "groupNode",
        "position": {"x": 800, "y": 160},
        "width": 1120,
        "height": 460,
        "style": {"width": 1120, "height": 460},
        "data": {"displayName": "场景素材"},
    }
    text = {
        "id": "existing-text",
        "type": "textAnnotationNode",
        "parentId": "selected-group",
        "position": {"x": 20, "y": 34},
        "width": 440,
        "height": 320,
        "data": {"content": "已有内容"},
    }
    image = {
        "id": "existing-image",
        "type": "imageGenNode",
        "parentId": "selected-group",
        "position": {"x": 500, "y": 34},
        "width": 580,
        "height": 360,
        "data": {"prompt": "已有图像"},
    }
    ctx, canvas_id = _workspace(tmp_path, [parent, text, image])
    generation = AssetGeneration(ctx)
    initial_positions = {node["id"]: dict(node["position"]) for node in (parent, text, image)}
    canvas = read_canvas(ctx.state_dir, canvas_id)
    body = _body(canvas_id, canvas["revision"], "nested-layout")
    body["group_id"] = "selected-group"

    result = generation.prepare("alice", body, selected_group_id="selected-group")

    assert result["projection_status"] == "applied"
    canvas = read_canvas(ctx.state_dir, canvas_id)
    nodes = {node["id"]: node for node in canvas["nodes"]}
    assert all(nodes[node_id]["position"] == position for node_id, position in initial_positions.items())
    new_group = nodes[result["group_id"]]
    assert new_group["parentId"] == "selected-group"
    assert new_group["position"] != {"x": 0, "y": 0}
    assert not _overlap(_node_rect(new_group), _node_rect(text))
    assert not _overlap(_node_rect(new_group), _node_rect(image))


def test_pending_projection_retry_reuses_persisted_asset_layout(tmp_path, monkeypatch):
    ctx, canvas_id = _workspace(tmp_path)
    generation = AssetGeneration(ctx)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    body = _body(canvas_id, canvas["revision"], "layout-retry")
    real_apply = ProjectionService.apply
    calls = 0

    def pending_once(self, operation_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(status="pending", canvas_revision=None)
        return real_apply(self, operation_id)

    monkeypatch.setattr(ProjectionService, "apply", pending_once)
    first = generation.prepare("alice", body)
    assert first["projection_status"] == "pending"
    design = generation.get_design(first["design_ref"]["id"])["design"]
    saved_layout = design["canvas_layout"]

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("retry must consume the immutable candidate layout")

    monkeypatch.setattr(
        "novelvideo.creation.asset_generation._asset_design_layout", fail_if_recomputed
    )
    retried = generation.prepare("alice", body)

    assert retried["projection_status"] == "applied"
    canvas = read_canvas(ctx.state_dir, canvas_id)
    nodes = {node["id"]: node for node in canvas["nodes"]}
    assert nodes[first["group_id"]]["position"] == saved_layout["group"]["position"]
    assert nodes[first["text_node_id"]]["position"] == saved_layout["text"]["position"]
    assert nodes[first["image_node_id"]]["position"] == saved_layout["image"]["position"]
    assert generation.prepare("alice", body) == retried


def test_legacy_selected_group_accepts_new_asset_without_moving_or_overlapping_children(tmp_path, monkeypatch):
    parent = {
        "id": "legacy-group",
        "type": "groupNode",
        "position": {"x": 800, "y": 160},
        "width": 940,
        "height": 360,
        "style": {"width": 940, "height": 360},
        "data": {"displayName": "旧素材组"},
    }
    text = {
        "id": "legacy-text",
        "type": "textAnnotationNode",
        "parentId": "legacy-group",
        "position": {"x": 0, "y": 0},
        "width": 440,
        "height": 320,
        "data": {"content": "旧角色设定"},
    }
    image = {
        "id": "legacy-image",
        "type": "imageGenNode",
        "parentId": "legacy-group",
        "position": {"x": 360, "y": 0},
        "width": 580,
        "height": 360,
        "data": {"prompt": "旧角色图像"},
    }
    ctx, canvas_id = _workspace(tmp_path, [parent, text, image])
    generation = AssetGeneration(ctx)
    initial_positions = {node["id"]: dict(node["position"]) for node in (parent, text, image)}
    canvas = read_canvas(ctx.state_dir, canvas_id)
    body = _body(canvas_id, canvas["revision"], "legacy-group-layout")
    body["group_id"] = "legacy-group"
    real_apply = ProjectionService.apply
    calls = 0

    def pending_once(self, operation_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(status="pending", canvas_revision=None)
        return real_apply(self, operation_id)

    monkeypatch.setattr(ProjectionService, "apply", pending_once)
    first = generation.prepare("alice", body, selected_group_id="legacy-group")

    assert first["projection_status"] == "pending"
    saved_layout = generation.get_design(first["design_ref"]["id"])["design"]["canvas_layout"]
    candidate = {
        "id": first["group_id"],
        "type": "groupNode",
        "position": saved_layout["group"]["position"],
        "width": saved_layout["group"]["width"],
        "height": saved_layout["group"]["height"],
    }
    assert not _overlap(_node_rect(candidate), _node_rect(text))
    assert not _overlap(_node_rect(candidate), _node_rect(image))

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("retry must reuse the frozen layout for the legacy group")

    monkeypatch.setattr("novelvideo.creation.asset_generation._asset_design_layout", fail_if_recomputed)
    retried = generation.prepare("alice", body, selected_group_id="legacy-group")

    assert retried["projection_status"] == "applied"
    projected = read_canvas(ctx.state_dir, canvas_id)
    nodes = {node["id"]: node for node in projected["nodes"]}
    assert all(nodes[node_id]["position"] == position for node_id, position in initial_positions.items())
    assert nodes[first["group_id"]]["position"] == saved_layout["group"]["position"]
    assert not _overlap(_node_rect(nodes[first["group_id"]]), _node_rect(nodes["legacy-text"]))
    assert not _overlap(_node_rect(nodes[first["group_id"]]), _node_rect(nodes["legacy-image"]))
    assert generation.prepare("alice", body, selected_group_id="legacy-group") == retried
