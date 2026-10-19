"""`canvas assets` 子命令：brief 读写、校验、编译、提案/准入与 360 切片。

输出统一是 JSON 信封：成功 `{"ok": true, "data": ...}`，失败
`{"ok": false, "error": {"code", "message", ...}}`；退出码按规格 0/2/3/4：
0 通过（可含 warning）、2 存在 error、3 冲突（hash/revision）、4 输入非法。
挂载到根 app 由集成阶段统一处理，本模块只导出 `assets_app`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import typer
from pydantic import ValidationError

from . import admission as admission_gate
from .compiler import compile_brief
from .facts import resolve_fact_sources
from .models import Admission, AssetDesignBrief, Issue
from .pano import slice_pano
from .store import AssetBriefConflict, AssetBriefNotFound, AssetBriefStore
from .validator import validate_brief

ASSET_KINDS = ("character", "scene", "prop")

EXIT_ISSUE = 2
EXIT_CONFLICT = 3
EXIT_INPUT = 4

assets_app = typer.Typer(help="资产 brief、编译与准入。", no_args_is_help=True)
brief_app = typer.Typer(help="资产 brief 读写。", no_args_is_help=True)
pano_app = typer.Typer(help="360 场景切片。", no_args_is_help=True)


@assets_app.callback()
def _group() -> None:
    """资产组：所有子命令都按 `canvas assets <command>` 调用。

    typer>=0.26 会把"只有一个子命令"的 Typer 降级成单命令；保留群组回调可以让
    `assets validate ...`、`assets brief read ...`、`assets pano slice ...` 的
    写法始终有效。
    """


@brief_app.callback()
def _brief_group() -> None:
    """brief 组：`canvas assets brief read|write`。"""


@pano_app.callback()
def _pano_group() -> None:
    """pano 组：`canvas assets pano slice`。"""


def _emit(data: Any) -> None:
    typer.echo(json.dumps({"ok": True, "data": data}, ensure_ascii=False))


def _fail(code: str, message: str, exit_code: int = EXIT_ISSUE, **extra: Any) -> NoReturn:
    error: dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    typer.echo(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
    raise typer.Exit(exit_code)


def _split_ref(ref: str) -> tuple[str, str, str | None]:
    """解析 `<kind>/<id>[/variants/<variant>]`；不合法直接 4 退出。"""

    try:
        kind, asset_id, variant = AssetBriefStore(Path()).resolve(ref)
    except ValueError as exc:
        _fail("brief_ref_invalid", str(exc), EXIT_INPUT)
    if kind not in ASSET_KINDS:
        _fail("brief_ref_invalid", f"unknown asset kind: {kind}", EXIT_INPUT)
    return kind, asset_id, variant


def _store_and_ref(root: str, ref: str) -> tuple[AssetBriefStore, str, str, str | None]:
    store = AssetBriefStore(root)
    kind, asset_id, variant = _split_ref(ref)
    return store, kind, asset_id, variant


def _relative(store: AssetBriefStore, path: Path) -> str:
    return str(path.relative_to(store.root))


def _proposal_path(
    store: AssetBriefStore, kind: str, asset_id: str, variant: str | None
) -> Path:
    return (store.root / store.relative_path(kind, asset_id, variant)).with_name("proposal.v1.json")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_brief(store: AssetBriefStore, ref: str) -> AssetDesignBrief:
    try:
        return store.read(ref)
    except AssetBriefNotFound:
        _fail("brief_not_found", f"brief not found: {ref}", EXIT_INPUT)


def _default_documents(root: Path, brief: AssetDesignBrief) -> dict[int, str]:
    """没给 --document 时按项目约定找 `<root>/episodes/{episode:03d}/script.md`。"""

    documents: dict[int, str] = {}
    for fact in brief.facts:
        episode = fact.source.episode
        if episode in documents:
            continue
        path = root / "episodes" / f"{episode:03d}" / "script.md"
        if path.exists():
            documents[episode] = path.read_text(encoding="utf-8")
    return documents


def _load_documents(root: Path, specs: list[str]) -> dict[int, str]:
    """`--document 1=script.md` -> {1: 正文}；相对路径按项目根解析。"""

    documents: dict[int, str] = {}
    for spec in specs:
        episode_part, sep, path_part = spec.partition("=")
        if not sep or not path_part.strip():
            raise ValueError(f"--document must be <episode>=<path>, got: {spec}")
        try:
            episode = int(episode_part.strip())
        except ValueError as exc:
            raise ValueError(f"--document episode must be an integer, got: {episode_part}") from exc
        if episode < 1:
            raise ValueError(f"--document episode must be >= 1, got: {episode}")
        path = Path(path_part.strip())
        if not path.is_absolute():
            path = root / path
        documents[episode] = path.read_text(encoding="utf-8")
    return documents


@assets_app.command("validate")
def validate(ref: str, root: str = typer.Option(..., "--root")) -> None:
    store = AssetBriefStore(root)
    _split_ref(ref)
    brief = _read_brief(store, ref)
    _emit(brief.model_dump(mode="json"))


@brief_app.command("read")
def brief_read(ref: str, root: str = typer.Option(..., "--root")) -> None:
    store, kind, asset_id, variant = _store_and_ref(root, ref)
    brief = _read_brief(store, ref)
    _emit(
        {
            "brief": brief.model_dump(mode="json"),
            "hash": store.hash(brief),
            "path": _relative(store, store.root / store.relative_path(kind, asset_id, variant)),
        }
    )


@brief_app.command("write")
def brief_write(
    ref: str,
    root: str = typer.Option(..., "--root"),
    file: str = typer.Option("", "--file", help="待写入的 brief JSON 文件。"),
    expected_hash: str = typer.Option("", "--expected-hash", help="读取时拿到的 hash；不匹配即冲突。"),
) -> None:
    store, kind, asset_id, variant = _store_and_ref(root, ref)
    if not file.strip():
        _fail("brief_file_missing", "--file is required", EXIT_INPUT)
    try:
        raw = Path(file).read_text(encoding="utf-8")
    except OSError as exc:
        _fail("brief_file_missing", f"cannot read brief file: {exc}", EXIT_INPUT)
    try:
        brief = AssetDesignBrief.model_validate_json(raw)
    except ValidationError as exc:
        _fail(
            "brief_schema_invalid",
            "brief does not match schema v1",
            EXIT_INPUT,
            detail=str(exc)[:1000],
        )
    if (brief.asset_kind, brief.asset_id, brief.variant_id) != (kind, asset_id, variant):
        _fail("brief_ref_mismatch", "brief identity does not match ref", EXIT_INPUT)
    try:
        saved = store.write(brief, expected_hash=expected_hash.strip() or None)
    except AssetBriefConflict as exc:
        _fail("brief_conflict", str(exc), EXIT_CONFLICT)
    except ValueError as exc:
        _fail("brief_variant_without_base", str(exc), EXIT_INPUT)
    _emit(saved)


@assets_app.command("compile")
def compile_asset(
    ref: str,
    root: str = typer.Option(..., "--root"),
    style_fragment: str = typer.Option("", "--style-fragment", help="软件风格片段（只注入一次）。"),
    style_id: str = typer.Option("", "--style-id", help="风格 ID；需同时给 --style-fragment。"),
    aspect: str = typer.Option("", "--aspect", help="画幅，如 3:2。"),
    resolution: str = typer.Option("", "--resolution", help="分辨率，如 1K。"),
    document: list[str] = typer.Option(
        [], "--document", help="剧本正文，形如 1=script.md，可重复；相对项目根。"
    ),
    asset_exists: bool = typer.Option(
        False, "--asset-exists", help="资产已存在时跳过准入门禁；新资产必须已确认。"
    ),
) -> None:
    store, _kind, _asset_id, _variant = _store_and_ref(root, ref)
    brief = _read_brief(store, ref)
    fragment = style_fragment.strip()
    if not fragment:
        if style_id.strip():
            _fail(
                "style_fragment_required",
                "--style-id also requires --style-fragment (catalog loading is not wired yet)",
                EXIT_ISSUE,
            )
        _fail("style_missing", "one of --style-fragment or --style-id is required", EXIT_ISSUE)
    payload = brief.model_dump(mode="json")
    if document:
        try:
            documents = _load_documents(store.root, document)
        except (OSError, ValueError) as exc:
            _fail("document_invalid", str(exc), EXIT_INPUT)
    else:
        documents = _default_documents(store.root, brief)

    issues: list[Issue] = (
        [] if asset_exists else admission_gate.check_admission(payload, asset_exists=False)
    )
    issues.extend(resolve_fact_sources(payload, documents))
    issues.extend(validate_brief(brief, documents=documents, style_fragment=fragment))
    params: dict[str, object] = {}
    if aspect.strip():
        params["aspect_ratio"] = aspect.strip()
    if resolution.strip():
        params["resolution"] = resolution.strip()
    result = compile_brief(
        brief, style_fragment=fragment, params=params, style_id=style_id.strip()
    )
    issues.extend(result.issues)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        _fail(
            "brief_validation_failed",
            f"{len(errors)} error(s): " + ", ".join(sorted({issue.code for issue in errors})),
            EXIT_ISSUE,
            issues=[issue.model_dump(mode="json") for issue in issues],
        )
    _emit(result.model_copy(update={"issues": issues}).model_dump(mode="json"))


@pano_app.command("slice")
def pano_slice(
    ref: str,
    source: str = typer.Option("", "--source", help="2:1 等距柱状全景图路径。"),
    yaw: float | None = typer.Option(None, "--yaw"),
    pitch: float | None = typer.Option(None, "--pitch"),
    fov: float | None = typer.Option(None, "--fov"),
    out: str = typer.Option("", "--out", help="切片输出路径。"),
    width: int = typer.Option(1024, "--width"),
    height: int = typer.Option(768, "--height"),
) -> None:
    kind, asset_id, variant = _split_ref(ref)
    if not source.strip():
        _fail("pano_source_missing", "--source is required", EXIT_INPUT)
    if yaw is None or pitch is None or fov is None or not out.strip():
        _fail("pano_argument_missing", "--yaw, --pitch, --fov and --out are required", EXIT_INPUT)
    try:
        target = slice_pano(
            source.strip(), out.strip(), yaw=yaw, pitch=pitch, fov=fov, width=width, height=height
        )
    except FileNotFoundError as exc:
        _fail("pano_source_missing", str(exc), EXIT_INPUT)
    except (OSError, ValueError) as exc:
        _fail("pano_range_invalid", str(exc), EXIT_INPUT)
    asset_ref = f"{kind}/{asset_id}" + (f"/variants/{variant}" if variant else "")
    _emit({"ref": asset_ref, "output": str(target), "width": width, "height": height})


@assets_app.command("propose")
def propose(
    ref: str,
    root: str = typer.Option(..., "--root"),
    name: str = typer.Option("", "--name", help="资产显示名。"),
    reason: str = typer.Option("", "--reason", help="为什么需要这个资产。"),
    evidence: list[str] = typer.Option([], "--evidence", help="剧本/用户依据，可重复。"),
    reuse_check: str = typer.Option("", "--reuse-check", help="复用检查结论：已有资产是否可复用。"),
) -> None:
    store, kind, asset_id, variant = _store_and_ref(root, ref)
    proposal = {
        "schema_version": 1,
        "asset_kind": kind,
        "asset_id": asset_id,
        "variant_id": variant,
        "name": name.strip(),
        "reason": reason.strip(),
        "evidence": [item.strip() for item in evidence if item.strip()],
        "reuse_check": reuse_check.strip(),
    }
    missing = [key for key in ("name", "reason", "reuse_check") if not proposal[key]]
    if missing:
        _fail("proposal_field_missing", "proposal requires " + ", ".join(missing), EXIT_ISSUE)
    path = _proposal_path(store, kind, asset_id, variant)
    _write_json(path, proposal)
    _emit({"path": _relative(store, path), "proposal": proposal})


@assets_app.command("admit")
def admit(
    ref: str,
    root: str = typer.Option(..., "--root"),
    confirmed_by: str = typer.Option("", "--confirmed-by", help="确认人（user 等）。"),
    basis: str = typer.Option("", "--basis", help="确认依据：剧本明示 / 用户指定 / 美术补充。"),
    decision: str = typer.Option("accept", "--decision", help="准入结论：accept | reject。"),
) -> None:
    if decision not in ("accept", "reject"):
        _fail("decision_invalid", "--decision must be accept or reject", EXIT_ISSUE)
    store, kind, asset_id, variant = _store_and_ref(root, ref)
    brief = _read_brief(store, ref)
    path = _proposal_path(store, kind, asset_id, variant)
    if not path.exists():
        _fail("proposal_missing", f"proposal not found: {_relative(store, path)}; run propose first", EXIT_ISSUE)
    try:
        proposal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("proposal_invalid", f"cannot read proposal: {exc}", EXIT_ISSUE)
    missing = [
        key for key in ("name", "reason", "reuse_check") if not str(proposal.get(key) or "").strip()
    ]
    if missing:
        _fail("proposal_invalid", "proposal missing " + ", ".join(missing), EXIT_ISSUE)
    identity = (proposal.get("asset_kind"), proposal.get("asset_id"), proposal.get("variant_id"))
    if identity != (kind, asset_id, variant):
        _fail("proposal_invalid", "proposal identity does not match ref", EXIT_ISSUE)

    if decision == "reject":
        proposal["decision"] = "reject"
        if confirmed_by.strip():
            proposal["decided_by"] = confirmed_by.strip()
        if basis.strip():
            proposal["decision_basis"] = basis.strip()
        _write_json(path, proposal)
        _emit({"path": _relative(store, path), "decision": "reject", "proposal": proposal})
        return

    if not confirmed_by.strip() or not basis.strip():
        _fail(
            "brief_admission_missing",
            "admission requires --confirmed-by and --basis",
            EXIT_ISSUE,
        )
    brief.admission = Admission(
        confirmed=True, confirmed_by=confirmed_by.strip(), basis=basis.strip()
    )
    issues = admission_gate.check_admission(brief.model_dump(mode="json"), asset_exists=False)
    if issues:
        _fail(issues[0].code, issues[0].message, EXIT_ISSUE)
    saved = store.write(brief, expected_hash=None)
    proposal["decision"] = "accept"
    proposal["decided_by"] = confirmed_by.strip()
    proposal["decision_basis"] = basis.strip()
    _write_json(path, proposal)
    _emit(
        {
            "path": saved["path"],
            "hash": saved["hash"],
            "admission": brief.admission.model_dump(mode="json"),
            "decision": "accept",
        }
    )


assets_app.add_typer(brief_app, name="brief")
assets_app.add_typer(pano_app, name="pano")
