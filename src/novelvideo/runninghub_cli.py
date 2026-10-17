"""Standalone package tooling; episode production continues through nuomi."""

from __future__ import annotations
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import typer
from novelvideo.media_capabilities.workflows.models import WorkflowError
from novelvideo.media_capabilities.workflows.registry import WorkflowRegistry
from novelvideo.media_capabilities.workflows.importer import (
    import_workflow,
    load_package,
)
from novelvideo.media_capabilities.workflows.graph import (
    inspect_graph,
    validate_deployed_graph,
)
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.task_store import TaskStore

app = typer.Typer(
    help="Import, validate, register and run RunningHub workflow packages.",
    no_args_is_help=True,
)


def _root(ctx) -> Path:
    if ctx.obj:
        return ctx.obj
    from novelvideo import config

    return Path(config.STATE_DIR) / "local"


def _registry(ctx):
    return WorkflowRegistry(MediaCapabilityStore(_root(ctx) / "settings.db"))


def _tasks(ctx):
    return TaskStore(_root(ctx) / "runninghub" / "tasks.db")


def _emit(operation):
    try:
        value = operation()
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        typer.echo(json.dumps(value, ensure_ascii=False))
    except (ValueError, OSError, LookupError, RuntimeError) as exc:
        code = exc.code if isinstance(exc, WorkflowError) else type(exc).__name__
        typer.echo(
            json.dumps(
                {
                    "error": {
                        "code": code,
                        "phase": getattr(exc, "phase", "command"),
                        "recoverable": getattr(exc, "recoverable", False),
                        "run_id": getattr(exc, "run_id", None),
                        "message": str(exc) if isinstance(exc, WorkflowError) else code,
                    }
                },
                ensure_ascii=False,
            )
        )
        raise typer.Exit(1) from None


@app.callback()
def main(
    ctx: typer.Context,
    state_dir: Optional[Path] = typer.Option(
        None, help="Override local application state directory."
    ),
):
    ctx.obj = state_dir


@app.command("inspect")
def inspect_command(source: Path):
    _emit(lambda: inspect_graph(json.loads(source.read_text(encoding="utf-8-sig"))))


@app.command("import")
def import_command(
    source: Path,
    identifier: str = typer.Option(..., "--id"),
    workflow_id: str = typer.Option(...),
    out: Path = typer.Option(...),
):
    _emit(
        lambda: import_workflow(
            source, identifier=identifier, workflow_id=workflow_id, out_dir=out
        )
    )


@asynccontextmanager
async def _service(ctx, account):
    from novelvideo.media_capabilities.workflows.runtime import open_workflow_service

    async with open_workflow_service(
        account_id=account, state_dir=_root(ctx)
    ) as service:
        yield service


@app.command("validate")
def validate_command(
    ctx: typer.Context,
    package: Path,
    remote: bool = False,
    account: str = "runninghub-main",
):
    async def execute():
        manifest, graph = load_package(package)
        if remote:
            async with _service(ctx, account) as service:
                validate_deployed_graph(
                    manifest,
                    graph,
                    await service.client.workflow_json(manifest.workflow_id),
                )
        return {
            "valid": True,
            "remote_checked": remote,
            "id": manifest.id,
            "version": manifest.version,
        }

    _emit(lambda: asyncio.run(execute()))


@app.command("register")
def register_command(ctx: typer.Context, package: Path):
    _emit(lambda: _registry(ctx).register(package))


@app.command("configure")
def configure_command(package: Path, template: str = typer.Option(...)):
    from novelvideo.media_capabilities.workflows.templates import configure_template

    _emit(lambda: configure_template(package, template))


@app.command("list")
def list_command(ctx: typer.Context):
    _emit(lambda: [m.snapshot() for m in _registry(ctx).list()])


@app.command("migrate")
def migrate_command(ctx: typer.Context, source_dir: Path = typer.Option(...)):
    from novelvideo.media_capabilities.workflows.migration import migrate_existing

    def execute():
        sources = {}
        patterns = {
            "music_acestep": "Ace-Step*_api.json",
            "video_minimax_h3_ref": "MiniMax*参考_api.json",
        }
        for key, pattern in patterns.items():
            matches = list(source_dir.glob(pattern))
            if len(matches) > 1:
                raise WorkflowError("ambiguous_source", key)
            if matches:
                sources[key] = matches[0]
        return {
            "workflows": migrate_existing(
                _registry(ctx).store, sources, _root(ctx) / "runninghub" / "packages"
            ),
            "defaults_changed": False,
        }

    _emit(execute)


@app.command("run")
def run_command(
    ctx: typer.Context,
    identifier: str,
    version: int = typer.Option(...),
    inputs: Path = typer.Option(...),
    account: str = "runninghub-main",
    wait: bool = False,
    idempotency_key: Optional[str] = None,
):
    async def execute():
        async with _service(ctx, account) as service:
            return await service.run(
                identifier,
                version,
                json.loads(inputs.read_text(encoding="utf-8-sig")),
                wait=wait,
                idempotency_key=idempotency_key,
            )

    _emit(lambda: asyncio.run(execute()))


@app.command("status")
def status_command(ctx: typer.Context, run_id: str):
    def execute():
        task = _tasks(ctx).get_task(run_id)
        if task is None:
            raise WorkflowError("run_not_found")
        attempts = _tasks(ctx).list_attempts(run_id)
        return {
            "run_id": run_id,
            "status": task.status.value,
            "provider_task_id": attempts[-1].provider_task_id if attempts else None,
            "artifacts": task.output.get("named_artifacts", {}),
            "error_code": attempts[-1].error_code if attempts else None,
        }

    _emit(execute)


def _advance(ctx, run_id, operation):
    async def execute():
        task = _tasks(ctx).get_task(run_id)
        if task is None:
            raise WorkflowError("run_not_found")
        async with _service(ctx, task.implementation_snapshot["account_id"]) as service:
            return await getattr(service, operation)(run_id)

    _emit(lambda: asyncio.run(execute()))


@app.command("resume")
def resume_command(ctx: typer.Context, run_id: str):
    _advance(ctx, run_id, "resume")


@app.command("cancel")
def cancel_command(ctx: typer.Context, run_id: str):
    _advance(ctx, run_id, "cancel")


@app.command("retry")
def retry_command(ctx: typer.Context, run_id: str):
    _advance(ctx, run_id, "retry")


if __name__ == "__main__":
    app()
