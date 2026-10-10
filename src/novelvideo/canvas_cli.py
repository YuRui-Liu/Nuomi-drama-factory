"""Thin canvas CLI. Execution and persistence belong to the shared service."""

from __future__ import annotations

from pathlib import Path
import time
from uuid import uuid4

import typer


def create_canvas_app(*, perform, body, segment, emit):
    app = typer.Typer(help="创作画布编排：建图、执行、状态和断点恢复。", no_args_is_help=True)
    nodes = typer.Typer(no_args_is_help=True)
    edges = typer.Typer(no_args_is_help=True)
    app.add_typer(nodes, name="nodes")
    app.add_typer(edges, name="edges")
    prefix = "freezone/orchestration"

    def call(ctx, method, path, payload=None):
        result = perform(ctx.obj, method, f"{prefix}/{path}", payload)
        emit(result)
        return result

    @app.command("capabilities")
    def capabilities(ctx: typer.Context):
        call(ctx, "GET", "capabilities")

    @app.command("create")
    def create(ctx: typer.Context, name: str = typer.Option(...), canvas_id: str | None = typer.Option(None)):
        payload = {"name": name}
        if canvas_id:
            payload["canvas_id"] = segment(canvas_id)
        call(ctx, "POST", "canvases", payload)

    @app.command("get")
    def get(ctx: typer.Context, canvas_id: str):
        emit(perform(ctx.obj, "GET", f"freezone/canvases/{segment(canvas_id)}"))

    @app.command("validate")
    def validate(ctx: typer.Context, canvas_id: str, plan: str | None = typer.Option(None)):
        result = call(ctx, "GET", f"plans/{segment(plan)}" if plan else f"canvases/{segment(canvas_id)}/validate")
        if not plan and not result.get("dry_run") and not result.get("data", {}).get("valid"):
            raise typer.Exit(2)

    def patch(ctx, canvas_id, base_revision, request_id, operation):
        call(ctx, "POST", f"canvases/{segment(canvas_id)}/patch", {
            "base_revision": base_revision, "request_id": request_id or uuid4().hex, "operations": [operation]})

    @nodes.command("add")
    def add_node(ctx: typer.Context, canvas_id: str, body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                 base_revision: int = typer.Option(..., min=1), request_id: str | None = typer.Option(None)):
        patch(ctx, canvas_id, base_revision, request_id, {"op": "add_node", "node": body("{}", body_file)})

    @nodes.command("update")
    def update_node(ctx: typer.Context, canvas_id: str, node_id: str, body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                    base_revision: int = typer.Option(..., min=1), request_id: str | None = typer.Option(None)):
        data = body("{}", body_file)
        patch(ctx, canvas_id, base_revision, request_id, {**data, "op": "update_node", "node_id": segment(node_id)})

    @edges.command("add")
    def add_edge(ctx: typer.Context, canvas_id: str, body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                 base_revision: int = typer.Option(..., min=1), request_id: str | None = typer.Option(None)):
        patch(ctx, canvas_id, base_revision, request_id, {"op": "add_edge", "edge": body("{}", body_file)})

    @nodes.command("configure")
    def configure_node(ctx: typer.Context, canvas_id: str, node_id: str,
                       base_revision: int = typer.Option(..., min=1), request_id: str | None = typer.Option(None),
                       aspect_ratio: str | None = typer.Option(None), size: str | None = typer.Option(None),
                       quality: str | None = typer.Option(None), model: str | None = typer.Option(None),
                       style: str | None = typer.Option(None, help="Extension style ID; empty string clears selection"),
                       style_template: str | None = typer.Option(None),
                       reference_image: str | None = typer.Option(None),
                       camera_body: str | None = typer.Option(None), lens: str | None = typer.Option(None),
                       focal_length: int | None = typer.Option(None, min=1), aperture: str | None = typer.Option(None)):
        """Set persisted image-node controls, shared with the webpage."""
        data = {key: value for key, value in {"size": size, "quality": quality, "model": model,
                "extensionStyleId": style, "styleTemplateId": style_template,
                "referenceImageUrl": reference_image}.items() if value is not None}
        if aspect_ratio is not None:
            data.update(aspectRatio=aspect_ratio, requestAspectRatio=aspect_ratio)
        camera = {key: value for key, value in {"cameraBodyId": camera_body, "lensId": lens,
                  "focalLengthMm": focal_length, "aperture": aperture}.items() if value is not None}
        if camera:
            data["cameraSelection"] = camera
        if not data:
            raise typer.BadParameter("Provide at least one node parameter")
        patch(ctx, canvas_id, base_revision, request_id, {"op": "update_node", "node_id": segment(node_id), "data": data})

    @app.command("run")
    def run(ctx: typer.Context, canvas_id: str, target: str | None = typer.Option(None), node: str | None = typer.Option(None),
            operation: str | None = typer.Option(None), request_id: str | None = typer.Option(None)):
        if bool(target) == bool(node):
            raise typer.BadParameter("Choose exactly one of --target or --node")
        call(ctx, "POST", "runs", {"canvas_id": segment(canvas_id), "target": segment(target or node),
                                   "request_id": request_id or uuid4().hex, "operation": operation})

    @app.command("status")
    def status(ctx: typer.Context, run_id: str):
        call(ctx, "GET", f"runs/{segment(run_id)}")

    @app.command("plan")
    def plan(ctx: typer.Context, canvas_id: str, script: Path = typer.Option(..., exists=True, dir_okay=False),
             duration: float = typer.Option(90, min=1), aspect_ratio: str = typer.Option("16:9"),
             request_id: str | None = typer.Option(None)):
        try:
            text = script.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise typer.BadParameter("Script must be readable UTF-8") from exc
        call(ctx, "POST", "plans", {"canvas_id": segment(canvas_id), "script": text, "duration": duration,
                                   "aspect_ratio": aspect_ratio, "request_id": request_id or uuid4().hex})

    @app.command("apply")
    def apply(ctx: typer.Context, canvas_id: str, plan: str = typer.Option(...), base_revision: int = typer.Option(..., min=1)):
        if not ctx.obj.dry_run:
            details = perform(ctx.obj, "GET", f"{prefix}/plans/{segment(plan)}")
            if details["data"]["canvas_id"] != segment(canvas_id):
                raise typer.BadParameter("Plan belongs to a different canvas")
        call(ctx, "POST", f"plans/{segment(plan)}/apply", {"base_revision": base_revision})

    @app.command("export")
    def export(ctx: typer.Context, run_id: str, output: Path = typer.Option(...)):
        import os
        import httpx
        relative = f"projects/{segment(ctx.obj.project)}/{prefix}/runs/{segment(run_id)}/export"
        if ctx.obj.dry_run:
            emit({"ok": True, "dry_run": True, "method": "GET", "path": "/"+relative, "output": str(output)})
            return
        if output.exists():
            raise typer.BadParameter("Output already exists; choose a new filename")
        token, session = os.environ.get("NUOMI_TOKEN", ""), os.environ.get("NUOMI_SESSION", "")
        try:
            with httpx.Client(headers={"Authorization": f"Bearer {token}"} if token else {},
                              cookies={"st_session": session} if session and not token else {}, timeout=ctx.obj.timeout,
                              follow_redirects=False) as client:
                with client.stream("GET", ctx.obj.base_url+"/"+relative) as response:
                    response.raise_for_status()
                    with output.open("xb") as dest:
                        for chunk in response.iter_bytes():
                            dest.write(chunk)
        except (httpx.HTTPError, OSError):
            emit({"ok": False, "code": "export_failed", "message": "Export failed; any partial local file was retained"})
            raise typer.Exit(1)
        emit({"ok": True, "data": {"output": str(output.resolve()), "bytes": output.stat().st_size}})

    @app.command("wait")
    def wait(ctx: typer.Context, run_id: str, timeout: float = typer.Option(3600, min=0), poll_interval: float = typer.Option(3, min=1)):
        path = f"{prefix}/runs/{segment(run_id)}"
        deadline = time.monotonic() + timeout
        while True:
            result = perform(ctx.obj, "GET", path)
            if result.get("dry_run"):
                emit(result)
                return
            status = result["data"]["status"]
            if status in {"succeeded", "failed", "blocked", "submission_unknown", "cancelled"}:
                emit(result)
                raise typer.Exit(0 if status == "succeeded" else 1)
            if time.monotonic() >= deadline:
                emit({"ok": False, "code": "wait_timeout", "data": result["data"]})
                raise typer.Exit(1)
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

    @app.command("resume")
    def resume(ctx: typer.Context, run_id: str):
        call(ctx, "POST", f"runs/{segment(run_id)}/resume")

    @app.command("retry")
    def retry(ctx: typer.Context, run_id: str, failed: bool = typer.Option(False), request_id: str | None = typer.Option(None)):
        if not failed:
            raise typer.BadParameter("Explicit --failed is required")
        call(ctx, "POST", f"runs/{segment(run_id)}/retry", {"request_id": request_id or uuid4().hex})

    @app.command("cancel")
    def cancel(ctx: typer.Context, run_id: str):
        call(ctx, "POST", f"runs/{segment(run_id)}/cancel")

    return app
