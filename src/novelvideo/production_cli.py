"""Lightweight, project-scoped HTTP CLI for human and agent production workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import typer


app = typer.Typer(help="漫剧制作 API：JSON 输出、可选草图、任务跟踪。", no_args_is_help=True)
_http_client = httpx.Client


class Stage(str, Enum):
    sketch = "sketch"
    render = "render"
    video = "video"


class Through(str, Enum):
    render = "render"
    video = "video"
    compose = "compose"


class Method(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass
class Connection:
    base_url: str
    project: str
    dry_run: bool
    timeout: float


class CliFailure(typer.Exit):
    def __init__(self, result: dict[str, Any]):
        super().__init__(1)
        self.result = result


def _segment(value: str) -> str:
    if (not value or value in {".", ".."}
            or any(c in value for c in "/\\%?#")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise typer.BadParameter("IDs and path segments must be nonempty, unencoded names without separators")
    return value


def _path(connection: Connection, relative: str) -> str:
    if relative == "":
        return "/projects/" + quote(connection.project, safe="")
    if "://" in relative:
        raise typer.BadParameter("Use a path relative to the selected project")
    parts = [_segment(part) for part in relative.split("/")]
    return "/projects/" + quote(connection.project, safe="") + "/" + "/".join(
        quote(part, safe="") for part in parts
    )


def _emit(value: Any) -> None:
    output = json.dumps(value, ensure_ascii=False)
    for key in ("NUOMI_TOKEN", "NUOMI_SESSION"):
        secret = os.environ.get(key, "").strip()
        if secret:
            output = output.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
    typer.echo(output)


def _fail(code: str, message: str, **extra: Any) -> None:
    result = {"ok": False, "error": {"code": code, "message": message, **extra}}
    _emit(result)
    raise CliFailure(result)


def _body(value: str, file: Path | None) -> dict[str, Any]:
    if file is not None and value != "{}":
        raise typer.BadParameter("Use either --json or --body-file")
    try:
        data = json.loads(file.read_text(encoding="utf-8") if file else value)
    except (ValueError, OSError):
        raise typer.BadParameter("Request body must contain valid UTF-8 JSON") from None
    if not isinstance(data, dict):
        raise typer.BadParameter("Request body must be a JSON object")
    return data


def _perform(
    connection: Connection, method: str, relative: str,
    body: dict[str, Any] | None = None, *, files: Any = None,
) -> dict[str, Any]:
    path = _path(connection, relative)
    if connection.dry_run:
        return {"ok": True, "dry_run": True, "method": method, "path": path, "body": body}
    token = os.environ.get("NUOMI_TOKEN", "").strip()
    session = os.environ.get("NUOMI_SESSION", "").strip()
    # Local CE permits unauthenticated loopback access. The API, not the CLI,
    # decides whether credentials are required for this deployment.
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    cookies = {"st_session": session} if session and not token else {}
    try:
        with _http_client(
            base_url=connection.base_url + "/", headers=headers, cookies=cookies,
            timeout=connection.timeout, follow_redirects=False,
        ) as client:
            kwargs = {"files": files, "data": body} if files else {"json": body} if body is not None else {}
            response = client.request(method, path.lstrip("/"), **kwargs)
    except httpx.TransportError:
        code = "transport_error" if method == "GET" else "submission_unknown"
        _fail(code, "Request failed; inspect project tasks before resubmitting. No automatic retry was made.")
    if not response.is_success:
        # Preserve structured API validation details; never dump a proxy HTML error page.
        try:
            error_body = response.json()
            detail = error_body.get("detail") if isinstance(error_body, dict) else None
        except ValueError:
            detail = None
        _fail("http_error", "API rejected the request", status=response.status_code, detail=detail)
    try:
        result = response.json()
    except ValueError:
        _fail("invalid_response", "Expected a JSON API response")
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        _fail("invalid_response", "Expected an API envelope with boolean ok")
    if not result["ok"]:
        _emit(result)
        raise CliFailure(result)
    return result


@app.callback()
def configure(
    ctx: typer.Context,
    project: str = typer.Option(..., envvar="NUOMI_PROJECT", help="Project ID, not its display name"),
    base_url: str = typer.Option("http://127.0.0.1:8780/api/v1", envvar="NUOMI_API_URL"),
    dry_run: bool = typer.Option(False, help="Print the request without contacting the API"),
    request_timeout: float = typer.Option(30.0, min=0.01),
) -> None:
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise typer.BadParameter("API URL must be HTTP(S), without credentials, query, or fragment")
    ctx.obj = Connection(base_url.rstrip("/"), _segment(project), dry_run, request_timeout)


@app.command()
def status(ctx: typer.Context) -> None:
    """List project tasks, including task IDs and terminal results."""
    _emit(_perform(ctx.obj, "GET", "tasks"))


@app.command()
def groups(ctx: typer.Context, episode: int = typer.Option(..., min=1)) -> None:
    """Read narrative groups and their current revisions."""
    _emit(_perform(ctx.obj, "GET", f"episodes/{episode}/narrative-groups"))


@app.command()
def plan(ctx: typer.Context, episode: int = typer.Option(..., min=1)) -> None:
    """Submit director planning from an already imported episode script."""
    _emit(_perform(ctx.obj, "POST", f"episodes/{episode}/director-plans"))


@app.command()
def generate(
    ctx: typer.Context,
    episode: int = typer.Option(..., min=1),
    group: str = typer.Option(...),
    stage: Stage = typer.Option(Stage.render),
    json_body: str = typer.Option("{}", "--json"),
    body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True),
    require_sketch: bool = typer.Option(False),
) -> None:
    """Generate one group. Pass planned references (images) or revisions (video) in JSON."""
    body = _body(json_body, body_file)
    if stage != Stage.video:
        body.setdefault("allow_unconstrained", not require_sketch)
        if require_sketch:
            body["allow_unconstrained"] = False
    elif require_sketch:
        raise typer.BadParameter("--require-sketch applies only to image generation")
    _emit(_perform(ctx.obj, "POST", f"episodes/{episode}/narrative-groups/{_segment(group)}/{stage.value}/generate", body))


@app.command()
def compose(
    ctx: typer.Context, episode: int = typer.Option(..., min=1),
    json_body: str = typer.Option("{}", "--json"),
    body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True),
) -> None:
    """Submit episode composition using existing generated clips."""
    _emit(_perform(ctx.obj, "POST", f"episodes/{episode}/videos/compose", _body(json_body, body_file)))


@app.command("import-preview")
def import_preview(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Preview an existing script import; use request episode-imports/commit to commit it."""
    if ctx.obj.dry_run:
        result = _perform(ctx.obj, "POST", "episode-imports/preview", {"input_intent": "existing_script"})
        result["file"] = str(file)
        _emit(result)
        return
    with file.open("rb") as stream:
        _emit(_perform(ctx.obj, "POST", "episode-imports/preview",
                       {"input_intent": "existing_script"}, files=[("files", (file.name, stream))]))


@app.command()
def request(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Unencoded path relative to projects/{project}, no leading slash"),
    method: Method = typer.Option(Method.GET),
    json_body: str = typer.Option("{}", "--json"),
    body_file: Path | None = typer.Option(None, exists=True, dir_okay=False, readable=True),
) -> None:
    """Call project APIs for imports, assets, references, plan confirmation, or settings."""
    body = _body(json_body, body_file)
    if method == Method.GET and body:
        raise typer.BadParameter("GET requests do not accept a JSON body")
    _emit(_perform(ctx.obj, method.value, path, None if method == Method.GET else body))


@app.command()
def wait(
    ctx: typer.Context,
    task_id: str = typer.Argument(...),
    timeout: float = typer.Option(600.0, min=0.01),
    interval: float = typer.Option(2.0, min=0.01, max=60.0),
) -> None:
    """Wait for exactly one task ID. Failure/cancellation/timeout exits with code 1."""
    if ctx.obj.dry_run:
        _emit(_perform(ctx.obj, "GET", "tasks"))
        return
    _emit({"ok": True, "data": _wait_task(ctx.obj, task_id, timeout, interval)})


def _wait_task(connection: Connection, task_id: str, timeout: float, interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("wait_timeout", "Task has not completed; no task was cancelled or resubmitted", task_id=task_id)
        poll_connection = Connection(connection.base_url, connection.project, False, min(connection.timeout, remaining))
        result = _perform(poll_connection, "GET", "tasks")
        tasks = result.get("data")
        if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
            _fail("invalid_response", "Expected a list of project tasks")
        task = next((task for task in tasks if task.get("task_id") == task_id), None)
        if task and task.get("status") in {"completed", "failed", "cancelled", "canceled", "partial_failure"}:
            task_result = task.get("result") or {}
            success = task["status"] == "completed" and not (
                isinstance(task_result, dict) and task_result.get("qc_passed") is False
            )
            if not success:
                failure = {"ok": False, "data": task}
                _emit(failure)
                raise CliFailure(failure)
            return task
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


@app.command()
def batch(
    ctx: typer.Context,
    episodes: str = typer.Option(..., help="Episode numbers/ranges, e.g. 1-10,12"),
    through: Through = typer.Option(Through.compose),
    with_sketch: bool = typer.Option(False, help="Opt into additional sketch generation"),
    max_submissions: int = typer.Option(100, min=1, help="Maximum mutation requests in this batch, not a currency budget"),
    retry_failed: bool = typer.Option(False, help="Allow one retry of previously failed stages"),
    aspect_ratio: str | None = typer.Option(None, help="9:16 or 16:9; default follows project orientation"),
    task_timeout: float = typer.Option(900.0, min=0.01),
    poll_interval: float = typer.Option(2.0, min=0.01, max=60.0),
) -> None:
    """Produce multiple imported episodes, reuse media, and report only exceptions (JSONL)."""
    from novelvideo.production_batch import BatchFailure, parse_episodes, run_batch

    try:
        numbers = parse_episodes(episodes)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if aspect_ratio is not None and aspect_ratio not in {"9:16", "16:9"}:
        raise typer.BadParameter("--aspect-ratio must be 9:16 or 16:9")
    if ctx.obj.dry_run:
        stages = (["sketch"] if with_sketch else []) + ["render"]
        if through != Through.render:
            stages.append("video")
        if through == Through.compose:
            stages.append("compose")
        _emit({"ok": True, "dry_run": True, "episodes": numbers, "stages": stages,
               "aspect_ratio": aspect_ratio or "project", "max_submissions": max_submissions})
        return
    if aspect_ratio is None:
        project = _perform(ctx.obj, "GET", "").get("data")
        try:
            width, height = map(int, project["aspect_ratio"].split(":"))
            if min(width, height) <= 0 or width == height:
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            _fail("aspect_ratio_required", "Project orientation is missing or unsupported; set --aspect-ratio")
        aspect_ratio = "9:16" if width < height else "16:9"

    def call(method, path, body=None):
        try:
            return _perform(ctx.obj, method, path, body)
        except CliFailure as exc:
            error = exc.result.get("error")
            if isinstance(error, dict) and error.get("status") in {401, 403}:
                raise
            raise BatchFailure(json.dumps(exc.result, ensure_ascii=False)) from None

    def await_task(task_id):
        try:
            return _wait_task(ctx.obj, task_id, task_timeout, poll_interval)
        except CliFailure as exc:
            raise BatchFailure(json.dumps(exc.result, ensure_ascii=False)) from None

    summary = run_batch(request=call, wait_task=await_task, episodes=numbers,
                        through=through.value, with_sketch=with_sketch,
                        max_submissions=max_submissions, aspect_ratio=aspect_ratio,
                        retry_failed=retry_failed, emit=_emit)
    _emit(summary)
    if not summary["ok"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
