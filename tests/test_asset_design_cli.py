import json

from PIL import Image
from typer.testing import CliRunner

from novelvideo.creation.asset_design.cli import assets_app
from novelvideo.creation.asset_design.models import AssetDesignBrief, Fact, FactSource
from novelvideo.creation.asset_design.store import AssetBriefStore

runner = CliRunner()


def _brief(**overrides):
    payload = {
        "asset_kind": "character",
        "asset_id": "lin-du",
        "design": {"silhouette": "普通偏瘦", "face": "方脸"},
        "consistency": {"anchors": ["褪色赭红运动夹克", "左眉旧疤"]},
        "render": {"material": "柔和动画材质"},
    }
    payload.update(overrides)
    return AssetDesignBrief(**payload)


def _seed(tmp_path, brief):
    return AssetBriefStore(tmp_path).write(brief, expected_hash=None)


def test_validate_emits_json_envelope(tmp_path):
    result = runner.invoke(assets_app, ["validate", "character/lin-du", "--root", str(tmp_path)])
    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "brief_not_found"


def test_brief_read_roundtrip_and_missing(tmp_path):
    saved = _seed(tmp_path, _brief())

    result = runner.invoke(assets_app, ["brief", "read", "character/lin-du", "--root", str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["hash"] == saved["hash"]
    assert payload["data"]["path"] == "assets/character/lin-du/brief.v1.json"
    assert payload["data"]["brief"]["asset_id"] == "lin-du"

    missing = runner.invoke(assets_app, ["brief", "read", "character/ghost", "--root", str(tmp_path)])
    assert missing.exit_code == 4
    assert json.loads(missing.stdout)["error"]["code"] == "brief_not_found"


def test_brief_write_roundtrip_and_conflict(tmp_path):
    store = AssetBriefStore(tmp_path)
    incoming = tmp_path / "incoming.json"
    incoming.write_text(_brief().model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        assets_app,
        ["brief", "write", "character/lin-du", "--file", str(incoming), "--root", str(tmp_path)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["hash"] == store.hash(store.read("character/lin-du"))
    assert store.read("character/lin-du").asset_id == "lin-du"

    conflict = runner.invoke(
        assets_app,
        [
            "brief",
            "write",
            "character/lin-du",
            "--file",
            str(incoming),
            "--expected-hash",
            "0" * 64,
            "--root",
            str(tmp_path),
        ],
    )
    assert conflict.exit_code == 3
    assert json.loads(conflict.stdout)["error"]["code"] == "brief_conflict"


def test_compile_success_and_style_duplicate_blocks(tmp_path):
    _seed(tmp_path, _brief())

    result = runner.invoke(
        assets_app,
        [
            "compile",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--style-fragment",
            "3D 国漫，柔和全局光",
            "--aspect",
            "3:2",
            "--asset-exists",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["compiled"]["params"] == {"aspect_ratio": "3:2"}
    assert payload["data"]["prompt_text"].count("3D 国漫，柔和全局光") == 1
    assert payload["data"]["issues"] == []

    _seed(tmp_path, _brief(design={"face": "3D 国漫风格"}))
    blocked = runner.invoke(
        assets_app,
        [
            "compile",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--style-fragment",
            "3D 国漫",
            "--asset-exists",
        ],
    )
    assert blocked.exit_code == 2
    error = json.loads(blocked.stdout)["error"]
    assert error["code"] == "brief_validation_failed"
    assert "brief_style_duplicate" in [issue["code"] for issue in error["issues"]]


def test_compile_resolves_fact_sources(tmp_path):
    brief = _brief(
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))]
    )
    _seed(tmp_path, brief)
    (tmp_path / "script.md").write_text("门外站着一个四十来岁的男人。", encoding="utf-8")
    args = [
        "compile",
        "character/lin-du",
        "--root",
        str(tmp_path),
        "--style-fragment",
        "3D 国漫",
        "--document",
        "1=script.md",
        "--asset-exists",
    ]

    result = runner.invoke(assets_app, args)
    assert result.exit_code == 0
    assert [issue["field"] for issue in json.loads(result.stdout)["data"]["issues"]] == []

    (tmp_path / "script.md").write_text("门外站着一个人。", encoding="utf-8")
    blocked = runner.invoke(assets_app, args)
    assert blocked.exit_code == 2
    error = json.loads(blocked.stdout)["error"]
    assert [issue["code"] for issue in error["issues"]] == ["brief_source_missing"]


def test_compile_blocks_unconfirmed_asset(tmp_path):
    _seed(tmp_path, _brief())

    blocked = runner.invoke(
        assets_app,
        ["compile", "character/lin-du", "--root", str(tmp_path), "--style-fragment", "3D 国漫"],
    )
    assert blocked.exit_code == 2
    assert [issue["code"] for issue in json.loads(blocked.stdout)["error"]["issues"]] == [
        "brief_admission_missing"
    ]

    existing = runner.invoke(
        assets_app,
        [
            "compile",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--style-fragment",
            "3D 国漫",
            "--asset-exists",
        ],
    )
    assert existing.exit_code == 0

    _seed(
        tmp_path,
        _brief(admission={"confirmed": True, "confirmed_by": "user", "basis": "剧本明示"}),
    )
    confirmed = runner.invoke(
        assets_app,
        ["compile", "character/lin-du", "--root", str(tmp_path), "--style-fragment", "3D 国漫"],
    )
    assert confirmed.exit_code == 0


def test_compile_loads_project_script_by_default(tmp_path):
    brief = _brief(
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))]
    )
    _seed(tmp_path, brief)
    (tmp_path / "episodes/001").mkdir(parents=True)
    (tmp_path / "episodes/001/script.md").write_text("门外站着一个四十来岁的男人。", encoding="utf-8")

    result = runner.invoke(
        assets_app,
        [
            "compile",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--style-fragment",
            "3D 国漫",
            "--asset-exists",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["issues"] == []


def test_pano_slice_success_and_invalid_ref(tmp_path):
    Image.new("RGB", (2048, 1024), (30, 60, 90)).save(tmp_path / "pano.png")
    out = tmp_path / "slice.png"

    result = runner.invoke(
        assets_app,
        [
            "pano",
            "slice",
            "scene/courtyard",
            "--source",
            str(tmp_path / "pano.png"),
            "--yaw",
            "120",
            "--pitch",
            "0",
            "--fov",
            "60",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["output"] == str(out)
    assert out.exists()
    assert Image.open(out).size == (1024, 768)

    invalid = runner.invoke(
        assets_app,
        [
            "pano",
            "slice",
            "portrait/lin-du",
            "--source",
            str(tmp_path / "pano.png"),
            "--yaw",
            "120",
            "--pitch",
            "0",
            "--fov",
            "60",
            "--out",
            str(out),
        ],
    )
    assert invalid.exit_code == 4
    assert json.loads(invalid.stdout)["error"]["code"] == "brief_ref_invalid"


def test_propose_writes_proposal_and_rejects_missing_field(tmp_path):
    result = runner.invoke(
        assets_app,
        [
            "propose",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--name",
            "林渡",
            "--reason",
            "第一集出场，需要定版设计",
            "--evidence",
            "第一集：门外站着一个四十来岁的男人。",
            "--reuse-check",
            "库内无同角色设计，画布无同角色节点",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["path"] == "assets/character/lin-du/proposal.v1.json"
    proposal = json.loads((tmp_path / "assets/character/lin-du/proposal.v1.json").read_text(encoding="utf-8"))
    assert proposal["name"] == "林渡"
    assert proposal["reason"]
    assert proposal["evidence"] == ["第一集：门外站着一个四十来岁的男人。"]
    assert proposal["reuse_check"]

    missing = runner.invoke(
        assets_app, ["propose", "character/lin-du", "--root", str(tmp_path), "--name", "林渡"]
    )
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)["error"]["code"] == "proposal_field_missing"


def test_admit_requires_proposal_and_writes_admission(tmp_path):
    store = AssetBriefStore(tmp_path)
    _seed(tmp_path, _brief())

    no_proposal = runner.invoke(
        assets_app,
        [
            "admit",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--confirmed-by",
            "user",
            "--basis",
            "剧本明示",
        ],
    )
    assert no_proposal.exit_code == 2
    assert json.loads(no_proposal.stdout)["error"]["code"] == "proposal_missing"

    runner.invoke(
        assets_app,
        [
            "propose",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--name",
            "林渡",
            "--reason",
            "第一集出场",
            "--reuse-check",
            "库内无同角色设计",
        ],
    )

    no_basis = runner.invoke(
        assets_app,
        ["admit", "character/lin-du", "--root", str(tmp_path), "--confirmed-by", "user"],
    )
    assert no_basis.exit_code == 2
    assert json.loads(no_basis.stdout)["error"]["code"] == "brief_admission_missing"

    admitted = runner.invoke(
        assets_app,
        [
            "admit",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--confirmed-by",
            "user",
            "--basis",
            "剧本明示",
        ],
    )
    assert admitted.exit_code == 0
    payload = json.loads(admitted.stdout)
    stored = store.read("character/lin-du")
    assert stored.admission.confirmed is True
    assert stored.admission.confirmed_by == "user"
    assert stored.admission.basis == "剧本明示"
    assert payload["data"]["hash"] == store.hash(stored)
    assert payload["data"]["decision"] == "accept"
    proposal = json.loads(
        (tmp_path / "assets/character/lin-du/proposal.v1.json").read_text(encoding="utf-8")
    )
    assert proposal["decision"] == "accept"
    assert proposal["decided_by"] == "user"


def test_admit_reject_records_decision_without_granting(tmp_path):
    store = AssetBriefStore(tmp_path)
    _seed(tmp_path, _brief())
    runner.invoke(
        assets_app,
        [
            "propose",
            "character/lin-du",
            "--root",
            str(tmp_path),
            "--name",
            "林渡",
            "--reason",
            "第一集出场",
            "--reuse-check",
            "库内无同角色设计",
        ],
    )

    rejected = runner.invoke(
        assets_app,
        ["admit", "character/lin-du", "--root", str(tmp_path), "--decision", "reject"],
    )
    assert rejected.exit_code == 0
    payload = json.loads(rejected.stdout)
    assert payload["data"]["decision"] == "reject"
    proposal = json.loads(
        (tmp_path / "assets/character/lin-du/proposal.v1.json").read_text(encoding="utf-8")
    )
    assert proposal["decision"] == "reject"
    assert store.read("character/lin-du").admission.confirmed is False

    invalid = runner.invoke(
        assets_app,
        ["admit", "character/lin-du", "--root", str(tmp_path), "--decision", "maybe"],
    )
    assert invalid.exit_code == 2
    assert json.loads(invalid.stdout)["error"]["code"] == "decision_invalid"
