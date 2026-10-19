"""Thin canvas CLI. Execution and persistence belong to the shared service."""

from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4
from urllib.parse import quote

import typer


def create_canvas_app(*, perform, body, segment, emit):
    app = typer.Typer(help="创作画布编排：建图、执行、状态和断点恢复。", no_args_is_help=True)
    nodes = typer.Typer(no_args_is_help=True)
    edges = typer.Typer(no_args_is_help=True)
    documents = typer.Typer(help="项目内正文读写。", no_args_is_help=True)
    progress = typer.Typer(help="按集配置和读取制作审阅状态。", no_args_is_help=True)
    experience = typer.Typer(help="检索本地创作经验目录。", no_args_is_help=True)
    director = typer.Typer(help="结构化 H3 导演输入。", no_args_is_help=True)
    app.add_typer(nodes, name="nodes")
    app.add_typer(edges, name="edges")
    app.add_typer(documents, name="documents")
    app.add_typer(progress, name="progress")
    app.add_typer(experience, name="experience")
    app.add_typer(director, name="director")
    from novelvideo.creation.asset_design.cli import assets_app
    app.add_typer(assets_app, name="assets")
    prefix = "freezone/orchestration"

    def call(ctx, method, path, payload=None, *, params=None, project_route=True):
        relative = f"{prefix}/{path}" if project_route else path
        result = perform(ctx.obj, method, relative, payload, params=params)
        emit(result)
        return result

    def read_json_object(path: Path, description: str) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise typer.BadParameter(f"{description} must be readable UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise typer.BadParameter(f"{description} must be a JSON object")
        return value

    def read_operations(path: Path) -> list[dict]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise typer.BadParameter("Operations must be readable UTF-8 JSON") from exc
        if (not isinstance(value, list) or not value or len(value) > 2000
                or any(not isinstance(item, dict) for item in value)):
            raise typer.BadParameter("Operations must be a nonempty JSON array of at most 2000 objects")
        return value

    def document_path(value: str) -> str:
        if (not value or value != value.strip() or value.startswith(("/", "\\"))
                or "\\" in value or ":" in value or "\x00" in value):
            raise typer.BadParameter("Document paths must be project-relative POSIX paths")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise typer.BadParameter("Document paths must not contain empty, dot, or parent segments")
        return value

    def progress_stage(value: str) -> str:
        if value not in {"screenplay", "assets", "storyboard", "video", "audio", "composition"}:
            raise typer.BadParameter("Unknown production stage")
        return value

    def read_prepared_pair(draft_file: Path, prepared_file: Path) -> tuple[dict, dict]:
        draft = read_json_object(draft_file, "Draft")
        prepared = read_json_object(prepared_file, "Prepared director input")
        return draft, prepared

    def validate_and_preview_prepared(ctx, draft: dict, prepared: dict) -> tuple[dict, dict]:
        initial = perform(ctx.obj, "POST", "freezone/video-director/preview", {"draft": draft})
        initial_data = initial.get("data") if isinstance(initial, dict) else None
        draft_fingerprint = initial_data.get("draft_fingerprint") if isinstance(initial_data, dict) else None
        if not isinstance(draft_fingerprint, str) or not draft_fingerprint:
            raise typer.BadParameter("Director preview did not return a frozen draft fingerprint")
        prepared = dict(prepared)
        supplied_fingerprint = prepared.get("draft_fingerprint")
        if supplied_fingerprint is not None and supplied_fingerprint != draft_fingerprint:
            raise typer.BadParameter("Prepared director input belongs to a different draft; prepare it again")
        prepared["draft_fingerprint"] = draft_fingerprint
        previewed = perform(ctx.obj, "POST", "freezone/video-director/preview",
                            {"draft": draft, "prepared": prepared})
        return prepared, previewed

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

    @app.command('preview')
    def preview(ctx: typer.Context, canvas_id: str, node: str = typer.Option(...)):
        """Inspect the saved node's actual request without generating media."""
        call(ctx, 'GET', f'canvases/{segment(canvas_id)}/nodes/{segment(node)}/preview')

    @app.command("patch")
    def patch_batch(ctx: typer.Context, canvas_id: str,
                    body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                    request_id: str = typer.Option(...)):
        """Apply one atomic batch against the latest canvas revision."""
        canvas_id = segment(canvas_id)
        if not request_id.strip() or len(request_id) > 128:
            raise typer.BadParameter("Request ID must contain 1 to 128 characters")
        operations = read_operations(body_file)
        canvas_path = f"projects/{quote(ctx.obj.project, safe='')}/freezone/canvases/{quote(canvas_id, safe='')}"
        patch_path = f"/projects/{quote(ctx.obj.project, safe='')}/{prefix}/canvases/{quote(canvas_id, safe='')}/patch"
        if ctx.obj.dry_run:
            emit({"ok": True, "dry_run": True, "steps": [
                {"method": "GET", "path": "/" + canvas_path},
                {"method": "POST", "path": patch_path, "body": {
                    "base_revision": "read-current-revision", "request_id": request_id,
                    "operations": operations,
                }},
            ]})
            return
        current = perform(ctx.obj, "GET", f"freezone/canvases/{canvas_id}")
        canvas = current.get("data") if isinstance(current, dict) else None
        revision = canvas.get("revision") if isinstance(canvas, dict) else None
        if type(revision) is not int or revision < 1:
            raise typer.BadParameter("Canvas has no readable current revision; inspect its project scope")
        if canvas.get("project_id") != ctx.obj.project:
            raise typer.BadParameter("Canvas does not belong to the selected project")
        call(ctx, "POST", f"canvases/{canvas_id}/patch", {
            "base_revision": revision, "request_id": request_id, "operations": operations,
        })

    @documents.command("read")
    def read_document(ctx: typer.Context, canvas_id: str, path: str):
        """Read project-local Markdown or other UTF-8 production text."""
        call(ctx, "GET", f"canvases/{segment(canvas_id)}/documents",
             params={"path": document_path(path)})

    @documents.command("write")
    def write_document(ctx: typer.Context, canvas_id: str, path: str,
                       text_file: Path = typer.Option(..., exists=True, dir_okay=False),
                       expected_hash: str | None = typer.Option(None, help="Current document hash; omit only to create")):
        """Write text only when the current document fingerprint matches."""
        try:
            text = text_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise typer.BadParameter("Document text must be readable UTF-8") from exc
        call(ctx, "PUT", f"canvases/{segment(canvas_id)}/documents", {
            "path": document_path(path), "text": text, "expected_hash": expected_hash,
        })

    @progress.command("get")
    def get_progress(ctx: typer.Context, canvas_id: str, episode_id: str):
        call(ctx, "GET", f"canvases/{segment(canvas_id)}/progress/{segment(episode_id)}")

    @progress.command("configure")
    def configure_progress(ctx: typer.Context, canvas_id: str, episode_id: str,
                           mode: str | None = typer.Option(None, help="review or auto"),
                           stop_after: str | None = typer.Option(None, help="Stop after a production stage"),
                           clear_stop_after: bool = typer.Option(False, "--clear-stop-after")):
        if mode not in {None, "review", "auto"}:
            raise typer.BadParameter("Mode must be review or auto")
        if stop_after is not None:
            stop_after = progress_stage(stop_after)
        if clear_stop_after and stop_after is not None:
            raise typer.BadParameter("Use either --stop-after or --clear-stop-after")
        if mode is None and stop_after is None and not clear_stop_after:
            raise typer.BadParameter("Provide --mode, --stop-after, or --clear-stop-after")
        payload = {"mode": mode} if mode is not None else {}
        if clear_stop_after:
            payload["stop_after"] = None
        elif stop_after is not None:
            payload["stop_after"] = stop_after
        call(ctx, "POST", f"canvases/{segment(canvas_id)}/progress/{segment(episode_id)}/configure", payload)

    @progress.command("complete")
    def complete_progress(ctx: typer.Context, canvas_id: str, episode_id: str,
                          stage: str = typer.Option(...), input_hash: str = typer.Option(...),
                          outputs_file: Path = typer.Option(..., exists=True, dir_okay=False)):
        outputs = read_json_object(outputs_file, "Stage outputs")
        if not outputs or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value
                              for key, value in outputs.items()):
            raise typer.BadParameter("Stage outputs must map labels to nonempty project-relative paths")
        call(ctx, "POST", f"canvases/{segment(canvas_id)}/progress/{segment(episode_id)}/complete", {
            "stage": progress_stage(stage), "input_hash": input_hash, "outputs": outputs,
        })

    @progress.command("approve")
    def approve_progress(ctx: typer.Context, canvas_id: str, episode_id: str, stage: str,
                         input_hash: str = typer.Option(..., help="approval_hash returned by progress get/complete")):
        call(ctx, "POST", f"canvases/{segment(canvas_id)}/progress/{segment(episode_id)}/approve", {
            "stage": progress_stage(stage), "input_hash": input_hash,
        })

    @experience.command("search")
    def search_experience(ctx: typer.Context, query: str = typer.Option(...),
                          mode: str = typer.Option(...), duration: float = typer.Option(..., min=0.01),
                          limit: int = typer.Option(5, min=1, max=20)):
        """Search the local experience catalog without creating or executing work."""
        if not query.strip():
            raise typer.BadParameter("Query must not be blank")
        call(ctx, "GET", "techniques/experience", project_route=False, params={
            "query": query, "mode": mode, "duration_seconds": duration, "limit": limit,
        })

    @director.command("capabilities")
    def director_capabilities(ctx: typer.Context):
        call(ctx, "GET", "freezone/video-director/capabilities", project_route=False)

    @director.command("preview")
    def director_preview(ctx: typer.Context,
                         body_file: Path = typer.Option(..., exists=True, dir_okay=False)):
        """Inspect draft profile, segment timing, and reference identity."""
        draft = read_json_object(body_file, "Director draft")
        call(ctx, "POST", "freezone/video-director/preview", {"draft": draft}, project_route=False)

    @director.command("prepare")
    def director_prepare(ctx: typer.Context,
                         body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                         prepared_file: Path = typer.Option(..., exists=True, dir_okay=False)):
        """Compile a structured director input against the current frozen draft."""
        draft, prepared = read_prepared_pair(body_file, prepared_file)
        if ctx.obj.dry_run:
            fingerprint_value = prepared.get("draft_fingerprint") or "from-first-preview"
            prepared["draft_fingerprint"] = fingerprint_value
            endpoint = f"/projects/{quote(ctx.obj.project, safe='')}/freezone/video-director/preview"
            emit({"ok": True, "dry_run": True, "steps": [
                {"method": "POST", "path": endpoint, "body": {"draft": draft}},
                {"method": "POST", "path": endpoint, "body": {"draft": draft, "prepared": prepared}},
            ]})
            return
        _prepared, previewed = validate_and_preview_prepared(ctx, draft, prepared)
        emit(previewed)

    @director.command("submit")
    def director_submit(ctx: typer.Context, canvas_id: str, node_id: str,
                        body_file: Path = typer.Option(..., exists=True, dir_okay=False),
                        prepared_file: Path = typer.Option(..., exists=True, dir_okay=False),
                        request_id: str = typer.Option(...)):
        """Submit the exact prepared H3 input through the existing attempt queue."""
        draft, prepared = read_prepared_pair(body_file, prepared_file)
        canvas_id, node_id = segment(canvas_id), segment(node_id)
        if not request_id.strip() or len(request_id) > 128:
            raise typer.BadParameter("Request ID must contain 1 to 128 characters")
        attempts_path = f"/projects/{quote(ctx.obj.project, safe='')}/freezone/video-director/attempts"
        if ctx.obj.dry_run:
            fingerprint_value = prepared.get("draft_fingerprint") or "from-first-preview"
            prepared["draft_fingerprint"] = fingerprint_value
            emit({"ok": True, "dry_run": True, "steps": [
                {"method": "POST", "path": "/projects/" + quote(ctx.obj.project, safe='')
                 + "/freezone/video-director/preview", "body": {"draft": draft}},
                {"method": "POST", "path": "/projects/" + quote(ctx.obj.project, safe='')
                 + "/freezone/video-director/preview", "body": {"draft": draft, "prepared": prepared}},
                {"method": "POST", "path": attempts_path, "body": {
                    "canvas_id": canvas_id, "node_id": node_id, "request_id": request_id,
                    "draft": draft, "prepared": prepared,
                }},
            ]})
            return
        prepared, _previewed = validate_and_preview_prepared(ctx, draft, prepared)
        call(ctx, "POST", "freezone/video-director/attempts", {
            "canvas_id": canvas_id, "node_id": node_id, "request_id": request_id,
            "draft": draft, "prepared": prepared,
        }, project_route=False)

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

    @edges.command("remove")
    def remove_edge(ctx: typer.Context, canvas_id: str, edge_id: str,
                    base_revision: int = typer.Option(..., min=1), request_id: str | None = typer.Option(None)):
        patch(ctx, canvas_id, base_revision, request_id, {"op": "remove_edge", "edge_id": segment(edge_id)})

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
            operation: str | None = typer.Option(None), request_id: str | None = typer.Option(None),
            expected_revision: int | None = typer.Option(None, min=1)):
        if bool(target) == bool(node):
            raise typer.BadParameter("Choose exactly one of --target or --node")
        payload = {"canvas_id": segment(canvas_id), "target": segment(target or node),
                   "request_id": request_id or uuid4().hex, "operation": operation}
        if expected_revision is not None:
            payload['expected_revision'] = expected_revision
        call(ctx, "POST", "runs", payload)

    @app.command("status")
    def status(ctx: typer.Context, run_id: str):
        call(ctx, "GET", f"runs/{segment(run_id)}")

    @app.command("plan")
    def plan(ctx: typer.Context, canvas_id: str, script: Path = typer.Option(..., exists=True, dir_okay=False),
             duration: float = typer.Option(90, min=1), aspect_ratio: str = typer.Option("16:9"),
             director_brief: str = typer.Option('', help='导演要求：视点、节奏、镜头与声音偏好'),
             request_id: str | None = typer.Option(None)):
        try:
            text = script.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise typer.BadParameter("Script must be readable UTF-8") from exc
        call(ctx, "POST", "plans", {"canvas_id": segment(canvas_id), "script": text, "duration": duration,
                                   "aspect_ratio": aspect_ratio, "director_brief": director_brief,
                                   "request_id": request_id or uuid4().hex})

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
