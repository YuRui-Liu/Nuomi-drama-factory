"""`canvas assets` 子命令：brief 校验与编译输出。

输出统一是 JSON 信封：成功 `{"ok": true, "data": ...}`，失败
`{"ok": false, "error": {"code", "message"}}` 并以非 0 退出，方便上层直接解析。
挂载到根 app 由集成阶段统一处理，本模块只导出 `assets_app`。
"""

from __future__ import annotations

import json
from typing import NoReturn

import typer

from .store import AssetBriefNotFound, AssetBriefStore

assets_app = typer.Typer(help="资产 brief、编译与准入。", no_args_is_help=True)


@assets_app.callback()
def _group() -> None:
    """资产组：所有子命令都按 `canvas assets <command>` 调用。

    typer>=0.26 会把"只有一个子命令"的 Typer 降级成单命令；保留这个群组回调
    可以确保 `assets validate ...` 的写法始终有效。
    """


def _fail(code: str, message: str, exit_code: int = 2) -> NoReturn:
    typer.echo(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
    raise typer.Exit(exit_code)


@assets_app.command("validate")
def validate(ref: str, root: str = typer.Option(..., "--root")) -> None:
    try:
        brief = AssetBriefStore(root).read(ref)
    except AssetBriefNotFound:
        _fail("brief_not_found", f"brief not found: {ref}", 4)
    except ValueError as exc:
        _fail("brief_ref_invalid", str(exc), 2)
    typer.echo(json.dumps({"ok": True, "data": brief.model_dump(mode="json")}, ensure_ascii=False))
