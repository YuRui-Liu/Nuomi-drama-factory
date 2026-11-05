"""`canvas assets` commands are thin project-scoped transports.

Each command must hit one project route with its business parameters; brief
paths, scripts and pano paths are resolved by the API, never by the CLI.
"""

import json

from typer.testing import CliRunner

PREFIX = "/projects/p/freezone/orchestration/canvases/canvas/assets"


def invoke(*args):
    from novelvideo.production_cli import app
    return CliRunner().invoke(app, ["--project", "p", "--dry-run", "canvas", "assets", *args])


def payload(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_assets_validate_posts_ref_and_style_fragment():
    data = payload(invoke("validate", "character/lin-du", "canvas", "--style-fragment", "3D 国漫"))
    assert data["method"] == "POST"
    assert data["path"] == PREFIX + "/validate"
    assert data["body"] == {"ref": "character/lin-du", "style_fragment": "3D 国漫"}


def test_assets_validate_forwards_explicit_handoff_document(tmp_path):
    receipt = tmp_path / "0bcba77785e6431499c14877c0059315.md"
    receipt.write_text("第一集已确认剧本：临街修简铺门口挂着旧木牌。", encoding="utf-8")

    data = payload(invoke("validate", "scene/临街修简铺", "canvas",
                          "--style-fragment", "国漫三维动画",
                          "--document", "1=" + str(receipt)))
    assert data["method"] == "POST"
    assert data["path"] == PREFIX + "/validate"
    assert data["body"] == {
        "ref": "scene/临街修简铺",
        "style_fragment": "国漫三维动画",
        "documents": {"1": "第一集已确认剧本：临街修简铺门口挂着旧木牌。"},
    }


def test_assets_brief_read_uses_query_and_accepts_canvas_option():
    data = payload(invoke("brief", "read", "character/lin-du", "--canvas", "canvas"))
    assert data["method"] == "GET"
    assert data["path"] == PREFIX + "/brief"
    assert data["params"] == {"ref": "character/lin-du"}


def test_assets_brief_write_puts_brief_and_expected_hash(tmp_path):
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps({"asset_kind": "character", "asset_id": "lin-du"}), encoding="utf-8")

    data = payload(invoke("brief", "write", "character/lin-du", "canvas",
                          "--file", str(brief), "--expected-hash", "abc123"))
    assert data["method"] == "PUT"
    assert data["path"] == PREFIX + "/brief"
    assert data["body"] == {
        "ref": "character/lin-du",
        "brief": {"asset_kind": "character", "asset_id": "lin-du"},
        "expected_hash": "abc123",
    }


def test_assets_compile_forwards_business_parameters_and_reads_documents(tmp_path):
    script = tmp_path / "script.md"
    script.write_text("门外站着一个四十来岁的男人。", encoding="utf-8")

    data = payload(invoke("compile", "character/lin-du", "canvas",
                          "--style-fragment", "3D 国漫", "--aspect", "3:2",
                          "--resolution", "1K", "--asset-exists",
                          "--document", "1=" + str(script)))
    assert data["method"] == "POST"
    assert data["path"] == PREFIX + "/compile"
    assert data["body"] == {
        "ref": "character/lin-du",
        "style_fragment": "3D 国漫",
        "aspect": "3:2",
        "resolution": "1K",
        "asset_exists": True,
        "documents": {"1": "门外站着一个四十来岁的男人。"},
    }


def test_assets_compile_rejects_malformed_document_spec():
    assert invoke("compile", "character/lin-du", "canvas", "--document", "script.md").exit_code == 2
    assert invoke("validate", "character/lin-du", "canvas", "--document", "receipt.md").exit_code == 2


def test_assets_admit_and_propose_carry_review_fields():
    admitted = payload(invoke("admit", "character/lin-du", "canvas",
                              "--confirmed-by", "user", "--basis", "剧本明示"))
    assert admitted["path"] == PREFIX + "/admit"
    assert admitted["body"] == {
        "ref": "character/lin-du", "confirmed_by": "user", "basis": "剧本明示",
        "decision": "accept",
    }

    proposed = payload(invoke("propose", "character/lin-du", "canvas", "--name", "林渡",
                              "--reason", "第一集出场", "--evidence", "第一集正文",
                              "--reuse-check", "库内无同角色设计"))
    assert proposed["path"] == PREFIX + "/propose"
    assert proposed["body"] == {
        "ref": "character/lin-du", "name": "林渡", "reason": "第一集出场",
        "evidence": ["第一集正文"], "reuse_check": "库内无同角色设计",
    }


def test_assets_pano_slice_posts_viewpoint_and_output():
    data = payload(invoke("pano", "slice", "scene/courtyard", "canvas",
                          "--source", "assets/scene/courtyard/pano.png",
                          "--yaw", "120", "--pitch", "0", "--fov", "60",
                          "--out", "assets/scene/courtyard/front.png",
                          "--width", "2048", "--height", "1536"))
    assert data["path"] == PREFIX + "/pano-slice"
    assert data["body"] == {
        "ref": "scene/courtyard", "source": "assets/scene/courtyard/pano.png",
        "yaw": 120.0, "pitch": 0.0, "fov": 60.0,
        "out": "assets/scene/courtyard/front.png", "width": 2048, "height": 1536,
    }


def test_assets_pano_slice_requires_viewpoint():
    assert invoke("pano", "slice", "scene/courtyard", "canvas",
                  "--source", "pano.png", "--yaw", "120", "--out", "front.png").exit_code == 2


def test_assets_canvas_is_supplied_exactly_once():
    assert invoke("validate", "character/lin-du").exit_code == 2
    assert invoke("validate", "character/lin-du", "canvas", "--canvas", "canvas").exit_code == 2
    assert invoke("validate", "character/lin-du", "../other").exit_code == 2


def test_assets_no_longer_accepts_a_local_root():
    """Project output directories come from the API, not from --root."""
    result = invoke("validate", "character/lin-du", "canvas", "--root", "/tmp/project")
    assert result.exit_code == 2
    assert "--root" in result.output
