"""CLI contract tests: real commands and HTTP transport, no model calls."""

import importlib
import json

import httpx
import pytest
from typer.testing import CliRunner


def module():
    return importlib.import_module("novelvideo.production_cli")


def test_render_dry_run_defaults_to_no_sketch_and_needs_no_auth():
    result = CliRunner().invoke(module().app, [
        "--project", "demo", "--dry-run", "generate", "--episode", "1",
        "--group", "ng-01", "--json", '{"reference_resolution":{"reference_revision":"r1","selected_binding_ids":[],"upload_ids":[]}}',
    ])
    assert result.exit_code == 0, result.output
    request = json.loads(result.stdout)
    assert request["path"].endswith("/ng-01/render/generate")
    assert request["body"]["allow_unconstrained"] is True
    assert "Authorization" not in result.stdout


def test_explicit_sketch_and_required_sketch_render():
    for stage in ("sketch", "render"):
        result = CliRunner().invoke(module().app, [
            "--project", "demo", "--dry-run", "generate", "--episode", "1",
            "--group", "ng-01", "--stage", stage, "--require-sketch", "--json", "{}",
        ])
        assert result.exit_code == 0, result.output
        request = json.loads(result.stdout)
        assert request["path"].endswith(f"/{stage}/generate")
        assert request["body"]["allow_unconstrained"] is False


@pytest.mark.parametrize("path", ["../other", "https://evil.test", "/projects/other", "x/../../other", "%2e%2e/other", "x?token=secret", "x#fragment", "x\\other"])
def test_request_cannot_escape_project(path):
    result = CliRunner().invoke(module().app, [
        "--project", "demo", "--dry-run", "request", path,
    ])
    assert result.exit_code != 0


def install_transport(monkeypatch, handler):
    cli = module()
    original = httpx.Client
    monkeypatch.setenv("NUOMI_TOKEN", "private-session-token")
    monkeypatch.setattr(cli, "_http_client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.parametrize("status_code", [200, 401, 403])
def test_no_credentials_delegates_authentication_to_server(monkeypatch, status_code):
    requests = []

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(status_code, json=(
            {"ok": True, "data": []} if status_code == 200
            else {"detail": "Authentication required"}
        ))

    install_transport(monkeypatch, handler)
    monkeypatch.delenv("NUOMI_TOKEN", raising=False)
    monkeypatch.delenv("NUOMI_SESSION", raising=False)
    result = CliRunner().invoke(module().app, ["--project", "demo", "status"])
    assert len(requests) == 1
    assert result.exit_code == (0 if status_code == 200 else 1), result.output
    if status_code != 200:
        assert json.loads(result.stdout)["error"]["status"] == status_code


def test_explicit_session_is_preserved(monkeypatch):
    def handler(request):
        assert request.headers["cookie"] == "st_session=local-test-session"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"ok": True, "data": []})

    install_transport(monkeypatch, handler)
    monkeypatch.delenv("NUOMI_TOKEN", raising=False)
    monkeypatch.setenv("NUOMI_SESSION", "local-test-session")
    result = CliRunner().invoke(module().app, ["--project", "demo", "status"])
    assert result.exit_code == 0, result.output


def test_wait_tracks_exact_task_id_and_failure_is_nonzero(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-session-token"
        return httpx.Response(200, json={"ok": True, "data": [
            {"task_id": "older", "status": "completed"},
            {"task_id": "wanted", "status": "failed"},
        ]})

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(module().app, [
        "--project", "demo", "wait", "wanted", "--timeout", "1",
    ])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["data"]["task_id"] == "wanted"
    assert len(requests) == 1
    assert "private-session-token" not in result.output


def test_submission_timeout_never_retries_or_exposes_credentials(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("private-session-token", request=request)

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(module().app, ["--project", "demo", "plan", "--episode", "1"])
    assert result.exit_code == 1
    assert len(requests) == 1
    assert "private-session-token" not in result.output
    assert "submission_unknown" in result.output


def test_api_application_error_is_nonzero_and_redacts_token(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        "ok": False, "error": "private-session-token",
    }))
    result = CliRunner().invoke(module().app, ["--project", "demo", "status"])
    assert result.exit_code == 1
    assert "private-session-token" not in result.output


def test_wait_timeout_does_not_report_completion(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"ok": True, "data": []}))
    result = CliRunner().invoke(module().app, [
        "--project", "demo", "wait", "absent", "--timeout", "0.01", "--interval", "0.01",
    ])
    assert result.exit_code == 1
    assert "wait_timeout" in result.output


def test_cli_help_has_no_cognee_import():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "-c", "import sys; import novelvideo.production_cli; assert 'cognee' not in sys.modules"], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_wait_completed_image_with_failed_qc_exits_nonzero(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"ok": True, "data": [
        {"task_id": "image", "status": "completed", "result": {"qc_passed": False}},
    ]}))
    result = CliRunner().invoke(module().app, ["--project", "demo", "wait", "image"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["data"]["result"]["qc_passed"] is False


def test_request_preserves_api_prefix_and_encodes_names(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"ok": True, "task_id": "portrait"})

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(module().app, [
        "--base-url", "http://test/prefix/api/v1", "--project", "demo",
        "request", "characters/林默/portrait-async", "--method", "POST", "--json", '{"model":"gpt-image-2"}',
    ])
    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/prefix/api/v1/projects/demo/characters/林默/portrait-async"
    assert requests[0].read() == b'{"model":"gpt-image-2"}'


def test_import_preview_uploads_existing_script(monkeypatch, tmp_path):
    source = tmp_path / "episode.txt"
    source.write_text("第1集\n室内 日\n林默：你好", encoding="utf-8")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "data": {"preview_id": "p1"}})

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(module().app, ["--project", "demo", "import-preview", str(source)])
    assert result.exit_code == 0, result.output
    assert requests[0].url.path.endswith("/episode-imports/preview")
    assert b"existing_script" in requests[0].read()
    assert '林默：你好'.encode() in requests[0].read()


def test_http_error_explains_validation_failure_without_token(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(409, json={"detail": {
        "code": "planned_references_stale", "message": "private-session-token",
    }}))
    result = CliRunner().invoke(module().app, ["--project", "demo", "status"])
    assert result.exit_code == 1
    assert "planned_references_stale" in result.output
    assert "private-session-token" not in result.output


def test_batch_dry_run_exposes_default_multi_episode_workflow_without_requests():
    result = CliRunner().invoke(module().app, [
        "--project", "demo", "--dry-run", "batch", "--episodes", "1-3,5",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["episodes"] == [1, 2, 3, 5]
    assert payload["stages"] == ["render", "video", "compose"]
