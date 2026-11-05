"""`canvas assets` 子命令：通过项目级 assets 路由读写 brief、校验、编译、提案/准入与 360 切片。

命令本身不碰本地目录：brief 位置、剧本正文与切片路径都由服务端按项目输出目录解析，
画布只提供 `canvas_id`（位置参数或 `--canvas`）与业务参数。输出沿用统一的 JSON 信封：
成功 `{"ok": true, "data": ...}`，失败由传输层按服务端错误返回。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import typer


def create_assets_app(*, call: Callable[..., Any], segment: Callable[[str], str]) -> typer.Typer:
    """Build `canvas assets ...` on top of the project-scoped HTTP transport."""

    app = typer.Typer(help="资产 brief、编译与准入。", no_args_is_help=True)
    brief = typer.Typer(help="资产 brief 读写。", no_args_is_help=True)
    pano = typer.Typer(help="360 场景切片。", no_args_is_help=True)
    app.add_typer(brief, name="brief")
    app.add_typer(pano, name="pano")

    def canvas_id(value: str | None, option: str | None) -> str:
        if bool(value) == bool(option):
            raise typer.BadParameter("Provide the canvas once, as an argument or with --canvas")
        return segment(value or option or "")

    def path(canvas: str, suffix: str) -> str:
        return f"canvases/{canvas}/assets/{suffix}"

    def read_json(file: Path) -> dict:
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise typer.BadParameter("Asset brief must be readable UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise typer.BadParameter("Asset brief must be a JSON object")
        return value

    def documents(specs: list[str]) -> dict[str, str] | None:
        """`--document 1=script.md` -> {"1": 正文}；相对路径按当前目录解析。"""
        if not specs:
            return None
        result: dict[str, str] = {}
        for spec in specs:
            episode_part, separator, path_part = spec.partition("=")
            if not separator or not path_part.strip():
                raise typer.BadParameter(f"--document must be <episode>=<path>, got: {spec}")
            try:
                episode = int(episode_part.strip())
            except ValueError as exc:
                raise typer.BadParameter(
                    f"--document episode must be an integer, got: {episode_part}") from exc
            if episode < 1:
                raise typer.BadParameter(f"--document episode must be >= 1, got: {episode}")
            try:
                result[str(episode)] = Path(path_part.strip()).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise typer.BadParameter(f"--document file is not readable UTF-8: {path_part}") from exc
        return result

    @app.command("validate")
    def validate(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                 canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
                 style_fragment: str = typer.Option("", "--style-fragment", help="软件风格片段。"),
                 document: list[str] = typer.Option(
                     [], "--document", help="剧本正文，形如 1=script.md，可重复；相对当前目录。")):
        """Report brief issues; the same checks gate compile."""
        body = {"ref": ref, "style_fragment": style_fragment}
        explicit_documents = documents(document)
        if explicit_documents is not None:
            body["documents"] = explicit_documents
        call(ctx, "POST", path(canvas_id(canvas, canvas_option), "validate"), body)

    @brief.command("read")
    def brief_read(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                   canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。")):
        call(ctx, "GET", path(canvas_id(canvas, canvas_option), "brief"), params={"ref": ref})

    @brief.command("write")
    def brief_write(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                    canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
                    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False,
                                              help="待写入的 brief JSON 文件。"),
                    expected_hash: str | None = typer.Option(
                        None, "--expected-hash", help="读取时拿到的 hash；不匹配即冲突。")):
        call(ctx, "PUT", path(canvas_id(canvas, canvas_option), "brief"), {
            "ref": ref, "brief": read_json(file), "expected_hash": expected_hash,
        })

    @app.command("compile")
    def compile_asset(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                      canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
                      style_fragment: str = typer.Option(
                          "", "--style-fragment", help="软件风格片段（只注入一次）。"),
                      aspect: str = typer.Option("", "--aspect", help="画幅，如 3:2。"),
                      resolution: str = typer.Option("", "--resolution", help="分辨率，如 1K。"),
                      document: list[str] = typer.Option(
                          [], "--document", help="剧本正文，形如 1=script.md，可重复；相对当前目录。"),
                      asset_exists: bool = typer.Option(
                          False, "--asset-exists", help="资产已存在时跳过准入门禁；新资产必须已确认。")):
        call(ctx, "POST", path(canvas_id(canvas, canvas_option), "compile"), {
            "ref": ref, "style_fragment": style_fragment, "aspect": aspect,
            "resolution": resolution, "asset_exists": asset_exists,
            "documents": documents(document),
        })

    @app.command("admit")
    def admit(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
              canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
              confirmed_by: str = typer.Option("", "--confirmed-by", help="确认人（user 等）。"),
              basis: str = typer.Option(
                  "", "--basis", help="确认依据：剧本明示 / 用户指定 / 美术补充。"),
              decision: str = typer.Option("accept", "--decision", help="准入结论：accept | reject。")):
        if decision not in ("accept", "reject"):
            raise typer.BadParameter("--decision must be accept or reject")
        call(ctx, "POST", path(canvas_id(canvas, canvas_option), "admit"), {
            "ref": ref, "confirmed_by": confirmed_by, "basis": basis, "decision": decision,
        })

    @pano.command("slice")
    def pano_slice(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                   canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
                   source: str = typer.Option("", "--source", help="项目内 2:1 等距柱状全景图路径。"),
                   yaw: float | None = typer.Option(None, "--yaw"),
                   pitch: float | None = typer.Option(None, "--pitch"),
                   fov: float | None = typer.Option(None, "--fov"),
                   out: str = typer.Option("", "--out", help="项目内切片输出路径。"),
                   width: int = typer.Option(1024, "--width", min=1),
                   height: int = typer.Option(768, "--height", min=1)):
        if not source.strip():
            raise typer.BadParameter("--source is required")
        if yaw is None or pitch is None or fov is None or not out.strip():
            raise typer.BadParameter("--yaw, --pitch, --fov and --out are required")
        call(ctx, "POST", path(canvas_id(canvas, canvas_option), "pano-slice"), {
            "ref": ref, "source": source, "yaw": yaw, "pitch": pitch, "fov": fov,
            "out": out, "width": width, "height": height,
        })

    @app.command("propose")
    def propose(ctx: typer.Context, ref: str, canvas: str | None = typer.Argument(None, help="画布 ID；与 --canvas 二选一。"),
                canvas_option: str | None = typer.Option(None, "--canvas", help="画布 ID；与位置参数二选一。"),
                name: str = typer.Option("", "--name", help="资产显示名。"),
                reason: str = typer.Option("", "--reason", help="为什么需要这个资产。"),
                evidence: list[str] = typer.Option([], "--evidence", help="剧本/用户依据，可重复。"),
                reuse_check: str = typer.Option(
                    "", "--reuse-check", help="复用检查结论：已有资产是否可复用。")):
        call(ctx, "POST", path(canvas_id(canvas, canvas_option), "propose"), {
            "ref": ref, "name": name, "reason": reason,
            "evidence": [item for item in evidence if item.strip()], "reuse_check": reuse_check,
        })

    return app
