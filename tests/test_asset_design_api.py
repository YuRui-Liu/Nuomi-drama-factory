"""Project-scoped asset design routes: brief, validate, compile, admit, propose, pano."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

PREFIX = "/api/v1/projects/p/freezone/orchestration"
CANVAS = PREFIX + "/canvases/canvas"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone_orchestration as routes

    state_dir, output_dir = tmp_path / "state", tmp_path / "output"
    state_dir.mkdir()
    output_dir.mkdir()

    async def resolve(project, user, required_role="editor"):
        if project != "p":
            raise HTTPException(403, "project denied")
        return SimpleNamespace(project_id="p", state_dir=state_dir), "u", "test", state_dir, str(output_dir)

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    app = FastAPI()
    app.state.state_dir = state_dir
    app.state.output_dir = output_dir
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u", "username": "u"}
    return TestClient(app)


@pytest.fixture
def canvas(client):
    created = client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "assets"})
    assert created.status_code == 200, created.text
    return created


def _brief(**overrides):
    payload = {
        "asset_kind": "character",
        "asset_id": "lin-du",
        "design": {"silhouette": "普通偏瘦", "face": "方脸"},
        "consistency": {"anchors": ["褪色赭红运动夹克", "左眉旧疤"]},
        "render": {"material": "柔和动画材质"},
    }
    payload.update(overrides)
    return payload


def _put_brief(client, **overrides):
    return client.put(CANVAS + "/assets/brief", json={"ref": "character/lin-du", "brief": _brief(**overrides)})


def _compile(client, **body):
    payload = {"ref": "character/lin-du", "style_fragment": "3D 国漫", "asset_exists": True}
    payload.update(body)
    return client.post(CANVAS + "/assets/compile", json=payload)


def test_asset_brief_roundtrip_uses_project_output_dir(client, canvas):
    saved = _put_brief(client)
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["path"] == "assets/character/lin-du/brief.v1.json"
    assert (client.app.state.output_dir / data["path"]).is_file()

    read = client.get(CANVAS + "/assets/brief", params={"ref": "character/lin-du"})
    assert read.status_code == 200, read.text
    assert read.json()["data"]["hash"] == data["hash"]
    assert read.json()["data"]["brief"]["asset_id"] == "lin-du"
    assert read.json()["data"]["path"] == data["path"]

    stale = client.put(CANVAS + "/assets/brief", json={
        "ref": "character/lin-du", "brief": _brief(), "expected_hash": "0" * 64,
    })
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "brief_conflict"

    missing = client.get(CANVAS + "/assets/brief", params={"ref": "character/ghost"})
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["code"] == "brief_not_found"


def test_asset_brief_rejects_bad_identity_schema_and_ref(client, canvas):
    mismatched = client.put(CANVAS + "/assets/brief", json={
        "ref": "character/lin-du", "brief": _brief(asset_id="other"),
    })
    assert mismatched.status_code == 422, mismatched.text
    assert mismatched.json()["detail"]["code"] == "brief_ref_mismatch"

    invalid = client.put(CANVAS + "/assets/brief", json={
        "ref": "character/lin-du", "brief": {"asset_kind": "character"},
    })
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["detail"]["code"] == "brief_schema_invalid"

    bad_ref = client.get(CANVAS + "/assets/brief", params={"ref": "character/lin-du/variants"})
    assert bad_ref.status_code == 422, bad_ref.text
    assert bad_ref.json()["detail"]["code"] == "brief_ref_invalid"

    unknown_kind = client.get(CANVAS + "/assets/brief", params={"ref": "portrait/lin-du"})
    assert unknown_kind.status_code == 422, unknown_kind.text
    assert unknown_kind.json()["detail"]["code"] == "brief_ref_invalid"


def test_asset_validate_reports_issues_without_writing(client, canvas):
    saved = _put_brief(client, design={"face": "方脸", "silhouette": "水印横条"},
                       admission={"confirmed": True, "confirmed_by": "user", "basis": "剧本明示"})
    assert saved.status_code == 200, saved.text

    response = client.post(CANVAS + "/assets/validate", json={"ref": "character/lin-du"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["valid"] is False
    assert "brief_noise_term" in [issue["code"] for issue in data["issues"]]
    reread = client.get(CANVAS + "/assets/brief", params={"ref": "character/lin-du"})
    assert reread.json()["data"]["hash"] == saved.json()["data"]["hash"]


def test_asset_validate_flags_missing_admission(client, canvas):
    _put_brief(client)
    response = client.post(CANVAS + "/assets/validate", json={"ref": "character/lin-du"})
    assert response.status_code == 200, response.text
    assert "brief_admission_missing" in [issue["code"] for issue in response.json()["data"]["issues"]]


def test_asset_validate_accepts_explicit_handoff_text_and_rejects_missing_quote(client, canvas):
    quote = "临街修简铺门口挂着旧木牌"
    receipt_text = "第一集已确认剧本：临街修简铺门口挂着旧木牌，雨水顺檐落下。"
    ref = "scene/临街修简铺"
    saved = client.put(CANVAS + "/assets/brief", json={"ref": ref, "brief": {
        "asset_kind": "scene", "asset_id": "临街修简铺",
        "facts": [{
            "field": "landmark", "value": "门口挂着旧木牌",
            "source": {"episode": 1, "quote": quote},
        }],
        "consistency": {"anchors": ["旧木牌", "临街店铺"]},
        "admission": {"confirmed": True, "confirmed_by": "user",
                      "basis": "用户确认的正式剧本交接"},
    }})
    assert saved.status_code == 200, saved.text

    response = client.post(CANVAS + "/assets/validate", json={
        "ref": ref, "style_fragment": "3D 国漫",
        "documents": {"1": receipt_text},
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["valid"] is True
    assert response.json()["data"]["issues"] == []

    missing_quote = client.post(CANVAS + "/assets/validate", json={
        "ref": ref, "style_fragment": "3D 国漫",
        "documents": {"1": receipt_text.replace(quote, "铺门口有旧木牌")},
    })
    assert missing_quote.status_code == 200, missing_quote.text
    data = missing_quote.json()["data"]
    assert data["valid"] is False
    assert [issue["code"] for issue in data["issues"]] == ["brief_source_missing"]


def test_asset_compile_reads_project_episode_scripts(client, canvas):
    episodes = client.app.state.output_dir / "episodes" / "001"
    episodes.mkdir(parents=True)
    (episodes / "script.md").write_text("门外站着一个四十来岁的男人。", encoding="utf-8")
    saved = _put_brief(client, facts=[{
        "field": "age", "value": "约40岁",
        "source": {"episode": 1, "quote": "四十来岁的男人"},
    }])
    assert saved.status_code == 200, saved.text

    compiled = _compile(client, aspect="3:2", resolution="1K")
    assert compiled.status_code == 200, compiled.text
    data = compiled.json()["data"]
    assert data["issues"] == []
    assert data["compiled"]["params"] == {"aspect_ratio": "3:2", "resolution": "1K"}
    assert data["prompt_text"].count("3D 国漫") == 1

    (episodes / "script.md").write_text("门外站着一个人。", encoding="utf-8")
    blocked = _compile(client, aspect="3:2")
    assert blocked.status_code == 422, blocked.text
    error = blocked.json()["detail"]
    assert error["code"] == "brief_validation_failed"
    assert [issue["code"] for issue in error["issues"]] == ["brief_source_missing"]


def test_asset_compile_accepts_explicit_documents(client, canvas):
    _put_brief(client, facts=[{
        "field": "age", "value": "约40岁",
        "source": {"episode": 2, "quote": "四十来岁的男人"},
    }])
    compiled = _compile(client, documents={"2": "第二集：门外站着一个四十来岁的男人。"})
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["data"]["issues"] == []

    invalid = _compile(client, documents={"two": "正文"})
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["detail"]["code"] == "document_invalid"


def test_asset_compile_requires_style_fragment(client, canvas):
    _put_brief(client)
    missing = client.post(CANVAS + "/assets/compile", json={
        "ref": "character/lin-du", "style_fragment": "  ", "asset_exists": True,
    })
    assert missing.status_code == 422, missing.text
    assert missing.json()["detail"]["code"] == "style_missing"


def test_asset_admission_flow_gates_compile(client, canvas):
    _put_brief(client)

    blocked = _compile(client, asset_exists=False)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["detail"]["code"] == "brief_validation_failed"
    assert [issue["code"] for issue in blocked.json()["detail"]["issues"]] == [
        "brief_admission_missing"]

    no_proposal = client.post(CANVAS + "/assets/admit", json={
        "ref": "character/lin-du", "confirmed_by": "user", "basis": "剧本明示",
    })
    assert no_proposal.status_code == 422, no_proposal.text
    assert no_proposal.json()["detail"]["code"] == "proposal_missing"

    incomplete = client.post(CANVAS + "/assets/propose", json={
        "ref": "character/lin-du", "name": "林渡", "reason": "第一集出场",
    })
    assert incomplete.status_code == 422, incomplete.text
    assert incomplete.json()["detail"]["code"] == "proposal_field_missing"

    proposed = client.post(CANVAS + "/assets/propose", json={
        "ref": "character/lin-du", "name": "林渡", "reason": "第一集出场",
        "evidence": ["第一集：门外站着一个四十来岁的男人。"],
        "reuse_check": "库内无同角色设计",
    })
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["data"]["path"] == "assets/character/lin-du/proposal.v1.json"

    no_basis = client.post(CANVAS + "/assets/admit", json={
        "ref": "character/lin-du", "confirmed_by": "user",
    })
    assert no_basis.status_code == 422, no_basis.text
    assert no_basis.json()["detail"]["code"] == "brief_admission_missing"

    admitted = client.post(CANVAS + "/assets/admit", json={
        "ref": "character/lin-du", "confirmed_by": "user", "basis": "剧本明示",
    })
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["data"]["decision"] == "accept"
    stored = client.get(CANVAS + "/assets/brief", params={"ref": "character/lin-du"}).json()["data"]
    assert stored["brief"]["admission"] == {
        "confirmed": True, "confirmed_by": "user", "basis": "剧本明示",
    }
    assert admitted.json()["data"]["hash"] == stored["hash"]

    compiled = _compile(client, asset_exists=False)
    assert compiled.status_code == 200, compiled.text


def test_asset_admit_reject_records_decision_without_granting(client, canvas):
    saved = _put_brief(client)
    client.post(CANVAS + "/assets/propose", json={
        "ref": "character/lin-du", "name": "林渡", "reason": "第一集出场",
        "reuse_check": "库内无同角色设计",
    })
    rejected = client.post(CANVAS + "/assets/admit", json={
        "ref": "character/lin-du", "decision": "reject", "confirmed_by": "user",
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["decision"] == "reject"
    reread = client.get(CANVAS + "/assets/brief", params={"ref": "character/lin-du"})
    assert reread.json()["data"]["hash"] == saved.json()["data"]["hash"]
    assert reread.json()["data"]["brief"]["admission"]["confirmed"] is False
    proposal = client.app.state.output_dir / "assets/character/lin-du/proposal.v1.json"
    assert '"decision": "reject"' in proposal.read_text(encoding="utf-8")


def test_asset_pano_slice_is_project_confined(client, canvas):
    source = client.app.state.output_dir / "assets/scene/courtyard/pano.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (2048, 1024), (30, 60, 90)).save(source)

    sliced = client.post(CANVAS + "/assets/pano-slice", json={
        "ref": "scene/courtyard", "source": "assets/scene/courtyard/pano.png",
        "yaw": 120, "pitch": 0, "fov": 60, "out": "assets/scene/courtyard/front.png",
    })
    assert sliced.status_code == 200, sliced.text
    data = sliced.json()["data"]
    assert data["output"] == "assets/scene/courtyard/front.png"
    assert data["ref"] == "scene/courtyard"
    output = client.app.state.output_dir / data["output"]
    assert output.is_file()
    assert Image.open(output).size == (1024, 768)

    escaped = client.post(CANVAS + "/assets/pano-slice", json={
        "ref": "scene/courtyard", "source": "assets/scene/courtyard/pano.png",
        "yaw": 120, "pitch": 0, "fov": 60, "out": "../escape.png",
    })
    assert escaped.status_code == 422, escaped.text
    assert escaped.json()["detail"]["code"] == "pano_path_invalid"

    absent = client.post(CANVAS + "/assets/pano-slice", json={
        "ref": "scene/courtyard", "source": "assets/scene/courtyard/missing.png",
        "yaw": 120, "pitch": 0, "fov": 60, "out": "assets/scene/courtyard/front.png",
    })
    assert absent.status_code == 422, absent.text
    assert absent.json()["detail"]["code"] == "pano_source_missing"


def test_assets_routes_require_project_canvas(client, monkeypatch, canvas):
    from novelvideo.api.routes import freezone_orchestration as routes

    missing = client.get(PREFIX + "/canvases/absent/assets/brief",
                         params={"ref": "character/lin-du"})
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["code"] == "canvas_not_found"

    async def resolve_any(_project, _user, required_role="editor"):
        return (SimpleNamespace(project_id=_project, state_dir=client.app.state.state_dir), "u", "test",
                client.app.state.state_dir, str(client.app.state.output_dir))

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve_any)
    foreign = client.get(PREFIX.replace("/p/", "/other/") + "/canvases/canvas/assets/brief",
                         params={"ref": "character/lin-du"})
    assert foreign.status_code == 403, foreign.text
    assert foreign.json()["detail"]["code"] == "scope_violation"
