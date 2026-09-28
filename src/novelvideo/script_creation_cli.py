"""Project-scoped script creation commands using the production CLI HTTP transport.

This module deliberately has no API or task-runner imports. Command registration
receives the transport and validation helpers so the module entry point works too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import typer


JsonBody = dict[str, Any]
Perform = Callable[..., JsonBody]


def create_script_app(
    *, perform: Perform, body: Callable[[str, Path | None], JsonBody],
    segment: Callable[[str], str], emit: Callable[[Any], None],
) -> typer.Typer:
    """Build script creation commands without importing the calling CLI module."""
    app = typer.Typer(help="剧本创作：文档、候选、检查、资产关联与制作交接。", no_args_is_help=True)
    documents = typer.Typer(help="创作文档和版本", no_args_is_help=True)
    generations = typer.Typer(help="批量创作和候选", no_args_is_help=True)
    rewrites = typer.Typer(help="选段改写", no_args_is_help=True)
    proposals = typer.Typer(help="候选采纳", no_args_is_help=True)
    checks = typer.Typer(help="关联检查", no_args_is_help=True)
    entities = typer.Typer(help="文档实体和文字资产", no_args_is_help=True)
    handoffs = typer.Typer(help="制作交接", no_args_is_help=True)
    for name, group in (
        ("documents", documents), ("generations", generations),
        ("rewrites", rewrites), ("proposals", proposals), ("checks", checks),
        ("entities", entities), ("handoffs", handoffs),
    ):
        app.add_typer(group, name=name)

    def call(ctx: typer.Context, method: str, path: str,
             payload: JsonBody | None = None, params: dict[str, Any] | None = None) -> None:
        emit(perform(ctx.obj, method, "script-creation/" + path, payload, params=params))

    def load(json_body: str | None, body_file: Path | None) -> JsonBody:
        # None distinguishes an omitted --json from an explicit '{}'. The
        # shared parser still owns JSON and UTF-8 validation.
        if body_file is not None and json_body is not None:
            raise typer.BadParameter("Use either --json or --body-file")
        return body(json_body if json_body is not None else "{}", body_file)

    @documents.command("list")
    def documents_list(ctx: typer.Context) -> None:
        """列出文档。"""
        call(ctx, "GET", "documents")

    @documents.command("create")
    def documents_create(ctx: typer.Context,
                         json_body: str | None = typer.Option(None, "--json"),
                         body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """创建简报、大纲、设计文档或单集剧本。"""
        call(ctx, "POST", "documents", load(json_body, body_file))

    @documents.command("get")
    def documents_get(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
        """读取正文及当前修订。"""
        call(ctx, "GET", f"documents/{segment(document_id)}")

    @documents.command("save")
    def documents_save(ctx: typer.Context, document_id: str = typer.Argument(...),
                       json_body: str | None = typer.Option(None, "--json"),
                       body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """带 base_revision_id 保存正文。"""
        call(ctx, "PUT", f"documents/{segment(document_id)}", load(json_body, body_file))

    @documents.command("revisions")
    def documents_revisions(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
        """列出历史修订。"""
        call(ctx, "GET", f"documents/{segment(document_id)}/revisions")

    @documents.command("restore")
    def documents_restore(ctx: typer.Context, document_id: str = typer.Argument(...),
                          json_body: str | None = typer.Option(None, "--json"),
                          body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """明确恢复旧修订，生成一个新修订。"""
        call(ctx, "POST", f"documents/{segment(document_id)}/restore", load(json_body, body_file))

    @documents.command("import")
    def documents_import(ctx: typer.Context,
                         json_body: str | None = typer.Option(None, "--json"),
                         body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """把已有制作来源复制进创作工作台。"""
        call(ctx, "POST", "imports", load(json_body, body_file))

    @generations.command("list")
    def generations_list(ctx: typer.Context) -> None:
        """列出创作运行。"""
        call(ctx, "GET", "generations")

    @generations.command("start")
    def generations_start(ctx: typer.Context,
                          json_body: str | None = typer.Option(None, "--json"),
                          body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """按保存的创作简报生成故事框架或续集。"""
        call(ctx, "POST", "generations", load(json_body, body_file))

    @generations.command("get")
    def generations_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """查看创作运行和候选 ID。"""
        call(ctx, "GET", f"generations/{segment(run_id)}")

    @generations.command("retry")
    def generations_retry(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """显式重试失败或暂停的原运行。"""
        call(ctx, "POST", f"generations/{segment(run_id)}/retry")

    @generations.command("rebase")
    def generations_rebase(ctx: typer.Context, run_id: str = typer.Argument(...),
                           json_body: str | None = typer.Option(None, "--json"),
                           body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """以新 mutation ID 对过期运行重新定基。"""
        call(ctx, "POST", f"generations/{segment(run_id)}/rebase", load(json_body, body_file))

    @generations.command("candidate")
    def generations_candidate(ctx: typer.Context, candidate_id: str = typer.Argument(...)) -> None:
        """读取一个生成候选。"""
        call(ctx, "GET", f"candidates/{segment(candidate_id)}")

    @generations.command("review")
    def generations_review(ctx: typer.Context, candidate_id: str = typer.Argument(...)) -> None:
        """将候选转成待审提案；不会自动采纳。"""
        call(ctx, "POST", f"candidates/{segment(candidate_id)}/review")

    @rewrites.command("create")
    def rewrites_create(ctx: typer.Context,
                        json_body: str | None = typer.Option(None, "--json"),
                        body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """对指定修订与 Unicode 字符范围创建改写任务。"""
        call(ctx, "POST", "rewrites", load(json_body, body_file))

    @rewrites.command("get")
    def rewrites_get(ctx: typer.Context, job_id: str = typer.Argument(...)) -> None:
        """读取改写任务。"""
        call(ctx, "GET", f"rewrites/{segment(job_id)}")

    @rewrites.command("list")
    def rewrites_list(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
        """列出文档改写任务。"""
        call(ctx, "GET", f"documents/{segment(document_id)}/rewrites")

    @proposals.command("list")
    def proposals_list(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
        """列出文档待审提案。"""
        call(ctx, "GET", f"documents/{segment(document_id)}/proposals")

    @proposals.command("accept")
    def proposals_accept(ctx: typer.Context,
                         json_body: str | None = typer.Option(None, "--json"),
                         body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """显式、原子采纳所选提案。"""
        call(ctx, "POST", "proposals/accept", load(json_body, body_file))

    @proposals.command("discard")
    def proposals_discard(ctx: typer.Context, proposal_id: str = typer.Argument(...)) -> None:
        """丢弃单个待审提案。"""
        call(ctx, "POST", f"proposals/{segment(proposal_id)}/discard")

    @checks.command("list")
    def checks_list(ctx: typer.Context,
                    episode_document_id: str | None = typer.Option(None)) -> None:
        """列出关联检查，可按单集文档过滤。"""
        params = {"episode_document_id": segment(episode_document_id)} if episode_document_id else None
        call(ctx, "GET", "consistency-runs", params=params)

    @checks.command("start")
    def checks_start(ctx: typer.Context,
                     json_body: str | None = typer.Option(None, "--json"),
                     body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """对已保存修订启动关联检查。"""
        call(ctx, "POST", "consistency-runs", load(json_body, body_file))

    @checks.command("get")
    def checks_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """读取检查结论及问题 ID。"""
        call(ctx, "GET", f"consistency-runs/{segment(run_id)}")

    @checks.command("intentional")
    def checks_intentional(ctx: typer.Context, issue_id: str = typer.Argument(...),
                           json_body: str | None = typer.Option(None, "--json"),
                           body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """以理由明确标记有意安排。"""
        call(ctx, "POST", f"consistency-issues/{segment(issue_id)}/intentional", load(json_body, body_file))

    @checks.command("targets")
    def checks_targets(ctx: typer.Context, issue_id: str = typer.Argument(...)) -> None:
        """读取此问题已选择过的改写目标文档 ID。"""
        call(ctx, "GET", f"consistency-issues/{segment(issue_id)}/target-rewrites")

    @checks.command("rewrite-targets")
    def checks_rewrite_targets(ctx: typer.Context, issue_id: str = typer.Argument(...),
                               json_body: str | None = typer.Option(None, "--json"),
                               body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """仅为显式选中的目标文档生成改写候选。"""
        call(ctx, "POST", f"consistency-issues/{segment(issue_id)}/target-rewrites", load(json_body, body_file))

    @entities.command("list")
    def entities_list(ctx: typer.Context, document_id: str | None = typer.Option(None)) -> None:
        """列出文档实体关联。"""
        params = {"document_id": segment(document_id)} if document_id else None
        call(ctx, "GET", "entities", params=params)

    @entities.command("put")
    def entities_put(ctx: typer.Context,
                     json_body: str | None = typer.Option(None, "--json"),
                     body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """关联已有资产或创建文字档案。"""
        call(ctx, "POST", "entities", load(json_body, body_file))

    @entities.command("assets")
    def entities_assets(ctx: typer.Context, asset_type: str = typer.Option(...)) -> None:
        """按类型查询可关联的现有资产。"""
        call(ctx, "GET", "assets", params={"asset_type": segment(asset_type)})

    @handoffs.command("list")
    def handoffs_list(ctx: typer.Context, episode_number: int | None = typer.Option(None, min=1)) -> None:
        """列出交接记录，可按集号过滤。"""
        call(ctx, "GET", "handoffs", params={"episode_number": episode_number} if episode_number else None)

    @handoffs.command("get")
    def handoffs_get(ctx: typer.Context, handoff_id: str = typer.Argument(...)) -> None:
        """读取交接快照、来源版本和任务状态。"""
        call(ctx, "GET", f"handoffs/{segment(handoff_id)}")

    @handoffs.command("prepare")
    def handoffs_prepare(ctx: typer.Context,
                         json_body: str | None = typer.Option(None, "--json"),
                         body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """生成交接预览与不可变快照，尚不改写制作来源。"""
        call(ctx, "POST", "handoffs/prepare", load(json_body, body_file))

    @handoffs.command("confirm")
    def handoffs_confirm(ctx: typer.Context, handoff_id: str = typer.Argument(...),
                         json_body: str | None = typer.Option(None, "--json"),
                         body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True)) -> None:
        """用已读来源版本明确确认交接。"""
        call(ctx, "POST", f"handoffs/{segment(handoff_id)}/confirm", load(json_body, body_file))

    @handoffs.command("retry")
    def handoffs_retry(ctx: typer.Context, handoff_id: str = typer.Argument(...)) -> None:
        """在原交接记录重试失败的后续派发。"""
        call(ctx, "POST", f"handoffs/{segment(handoff_id)}/retry")

    return app
