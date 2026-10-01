"""Voice CLI contracts with a real HTTP client and isolated transport."""

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from novelvideo import production_cli as cli


def invoke(*args, dry_run=False):
    return CliRunner().invoke(cli.app, [
        "--project", "demo", *(["--dry-run"] if dry_run else []), "voice", *args,
    ])


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(cli, "_http_client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.parametrize("args,method,path,body", [
    (["preflight", "林默"], "GET", "characters/林默/voice-preflight", None),
    (["candidates", "林默"], "GET", "characters/林默/voice-candidates", None),
    (["preflight-episode", "2"], "GET", "episodes/2/voice-preflight", None),
    (["recheck", "林默", "c1"], "POST", "characters/林默/voice-candidates/c1/recheck", None),
    (["approve", "林默", "c1", "--reason", "听音通过", "--confirm-reviewed"],
     "POST", "characters/林默/voice-candidates/c1/approve", {"confirm": True, "reason": "听音通过"}),
    (["design", "林默", "--slot", "default", "--description", "低沉", "--text", "你好"],
     "POST", "characters/林默/voice-samples/default/design",
     {"voice_description": "低沉", "audition_text": "你好"}),
])
def test_voice_commands_send_one_request(monkeypatch, args, method, path, body):
    requests = []
    envelope = {"ok": True, "data": {"status": "qc_unavailable", "production_ready": False}}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=envelope)

    transport(monkeypatch, handler)
    result = invoke(*args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    assert len(requests) == 1
    assert requests[0].method == method
    assert requests[0].url.path == "/api/v1/projects/demo/" + path
    assert (json.loads(requests[0].read()) if requests[0].read() else None) == body


@pytest.mark.parametrize("extra", [[], ["--reason", "reviewed"], ["--confirm-reviewed"],
                                       ["--reason", " ", "--confirm-reviewed"]])
def test_approval_requires_review_confirmation_and_nonempty_reason(monkeypatch, extra):
    transport(monkeypatch, lambda request: pytest.fail("must not send unconfirmed approval"))
    assert invoke("approve", "林默", "c1", *extra).exit_code != 0


def test_import_uses_multipart_and_preserves_candidate_status(monkeypatch, tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"sample-audio")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "data": {"status": "qc_unavailable"}})

    transport(monkeypatch, handler)
    result = invoke("import", "巨兽", str(source), "--kind", "nonverbal", "--slot", "alarm",
                    "--source", "self-recorded", "--rights", "owned")
    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path.endswith("/characters/巨兽/voice-candidates/import")
    assert request.headers["content-type"].startswith("multipart/form-data;")
    for value in (b'name="file"', b"sample-audio", b"nonverbal", b"alarm", b"self-recorded", b"owned"):
        assert value in request.read()
    assert json.loads(result.stdout)["data"]["status"] == "qc_unavailable"


def test_import_dry_run_never_reads_audio_or_contacts_api(monkeypatch, tmp_path):
    source = tmp_path / "not-created.wav"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("dry run must not read audio"))
    transport(monkeypatch, lambda request: pytest.fail("dry run must not contact API"))
    result = invoke("import", "林默", str(source), "--kind", "dialogue", "--slot", "default",
                    "--source", "recording", "--rights", "owned", dry_run=True)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["file"] == str(source)
    assert payload["body"] == {"kind": "dialogue", "slot": "default", "source": "recording", "rights": "owned"}


@pytest.mark.parametrize("args", [["preflight", "../other"], ["recheck", "林默", "%2e"],
                                  ["design", "林默", "--slot", "../bad", "--description", "声线", "--text", "你好"]])
def test_voice_paths_cannot_escape_project(args):
    assert invoke(*args, dry_run=True).exit_code != 0


def test_design_timeout_never_retries(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    transport(monkeypatch, handler)
    result = invoke("design", "林默", "--slot", "default", "--description", "低沉", "--text", "你好")
    assert result.exit_code == 1
    assert len(requests) == 1
    assert json.loads(result.stdout)["error"]["code"] == "submission_unknown"


def test_approval_preserves_server_refusal(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(409, json={
        "ok": False, "error": "stale voice profile; review a new candidate",
    }))
    result = invoke("approve", "林默", "c1", "--reason", "reviewed", "--confirm-reviewed")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["detail"] == "stale voice profile; review a new candidate"
