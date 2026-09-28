"""Script creation CLI contract: one project-scoped request per command."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import httpx
import pytest
from typer.testing import CliRunner


def cli():
    return importlib.import_module("novelvideo.production_cli")


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(cli(), "_http_client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


# command words, method, path, JSON body or None, query parameters
ROUTES = [
    (["documents", "list"], "GET", "documents", None, {}),
    (["documents", "create", "--json", '{"kind":"brief","title":"你好","client_mutation_id":"m1"}'], "POST", "documents", {"kind": "brief", "title": "你好", "client_mutation_id": "m1"}, {}),
    (["documents", "get", "doc-1"], "GET", "documents/doc-1", None, {}),
    (["documents", "save", "doc-1", "--json", '{"base_revision_id":"r1","markdown":"你好","client_mutation_id":"m2"}'], "PUT", "documents/doc-1", {"base_revision_id": "r1", "markdown": "你好", "client_mutation_id": "m2"}, {}),
    (["documents", "revisions", "doc-1"], "GET", "documents/doc-1/revisions", None, {}),
    (["documents", "restore", "doc-1", "--json", '{"revision_id":"r1","base_revision_id":"r2","client_mutation_id":"m3"}'], "POST", "documents/doc-1/restore", {"revision_id": "r1", "base_revision_id": "r2", "client_mutation_id": "m3"}, {}),
    (["documents", "import", "--json", '{"episode_number":1}'], "POST", "imports", {"episode_number": 1}, {}),
    (["generations", "list"], "GET", "generations", None, {}),
    (["generations", "start", "--json", '{"mode":"bootstrap","brief_id":"b1","script_mode":"series","episode_count":3,"client_mutation_id":"m4"}'], "POST", "generations", {"mode": "bootstrap", "brief_id": "b1", "script_mode": "series", "episode_count": 3, "client_mutation_id": "m4"}, {}),
    (["generations", "get", "run-1"], "GET", "generations/run-1", None, {}),
    (["generations", "retry", "run-1"], "POST", "generations/run-1/retry", None, {}),
    (["generations", "rebase", "run-1", "--json", '{"client_mutation_id":"m5"}'], "POST", "generations/run-1/rebase", {"client_mutation_id": "m5"}, {}),
    (["generations", "candidate", "candidate-1"], "GET", "candidates/candidate-1", None, {}),
    (["generations", "review", "candidate-1"], "POST", "candidates/candidate-1/review", None, {}),
    (["rewrites", "create", "--json", '{"document_id":"d1","base_revision_id":"r1","start":0,"end":2,"scope":"selection","mode":"revise","client_mutation_id":"m6"}'], "POST", "rewrites", {"document_id": "d1", "base_revision_id": "r1", "start": 0, "end": 2, "scope": "selection", "mode": "revise", "client_mutation_id": "m6"}, {}),
    (["rewrites", "get", "job-1"], "GET", "rewrites/job-1", None, {}),
    (["rewrites", "list", "doc-1"], "GET", "documents/doc-1/rewrites", None, {}),
    (["proposals", "list", "doc-1"], "GET", "documents/doc-1/proposals", None, {}),
    (["proposals", "accept", "--json", '{"proposal_ids":["p1"],"base_revision_id":"r1","client_mutation_id":"m7"}'], "POST", "proposals/accept", {"proposal_ids": ["p1"], "base_revision_id": "r1", "client_mutation_id": "m7"}, {}),
    (["proposals", "discard", "p1"], "POST", "proposals/p1/discard", None, {}),
    (["checks", "list", "--episode-document-id", "doc-1"], "GET", "consistency-runs", None, {"episode_document_id": "doc-1"}),
    (["checks", "start", "--json", '{"episode_document_id":"doc-1","context_revisions":{},"client_mutation_id":"m8"}'], "POST", "consistency-runs", {"episode_document_id": "doc-1", "context_revisions": {}, "client_mutation_id": "m8"}, {}),
    (["checks", "get", "run-1"], "GET", "consistency-runs/run-1", None, {}),
    (["checks", "intentional", "issue-1", "--json", '{"reason":"伏笔"}'], "POST", "consistency-issues/issue-1/intentional", {"reason": "伏笔"}, {}),
    (["checks", "targets", "issue-1"], "GET", "consistency-issues/issue-1/target-rewrites", None, {}),
    (["checks", "rewrite-targets", "issue-1", "--json", '{"target_document_ids":["doc-2"]}'], "POST", "consistency-issues/issue-1/target-rewrites", {"target_document_ids": ["doc-2"]}, {}),
    (["entities", "list", "--document-id", "doc-1"], "GET", "entities", None, {"document_id": "doc-1"}),
    (["entities", "put", "--json", '{"document_id":"d1","base_revision_id":"r1","block_id":"b1","name":"林默","client_mutation_id":"m9"}'], "POST", "entities", {"document_id": "d1", "base_revision_id": "r1", "block_id": "b1", "name": "林默", "client_mutation_id": "m9"}, {}),
    (["entities", "assets", "--asset-type", "character"], "GET", "assets", None, {"asset_type": "character"}),
    (["handoffs", "list", "--episode-number", "1"], "GET", "handoffs", None, {"episode_number": "1"}),
    (["handoffs", "get", "handoff-1"], "GET", "handoffs/handoff-1", None, {}),
    (["handoffs", "prepare", "--json", '{"document_id":"d1","revision_id":"r1","update_scope":{"mode":"all"},"fact_acknowledgement":{"mode":"none"},"client_mutation_id":"m10"}'], "POST", "handoffs/prepare", {"document_id": "d1", "revision_id": "r1", "update_scope": {"mode": "all"}, "fact_acknowledgement": {"mode": "none"}, "client_mutation_id": "m10"}, {}),
    (["handoffs", "confirm", "handoff-1", "--json", '{"expected_source_project_revision":0,"client_mutation_id":"m11"}'], "POST", "handoffs/handoff-1/confirm", {"expected_source_project_revision": 0, "client_mutation_id": "m11"}, {}),
    (["handoffs", "retry", "handoff-1"], "POST", "handoffs/handoff-1/retry", None, {}),
]


@pytest.mark.parametrize("words,method,relative,body,query", ROUTES)
def test_each_script_command_sends_exactly_one_request(monkeypatch, words, method, relative, body, query):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "data": {"id": "value"}})

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(cli().app, ["--project", "项目 1", "script", *words])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"ok": True, "data": {"id": "value"}}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == method
    assert request.url.path == f"/api/v1/projects/项目 1/script-creation/{relative}"
    assert dict(request.url.params) == query
    assert (json.loads(request.content) if request.content else None) == body


def test_body_file_preserves_utf8_and_rejects_mixed_body(monkeypatch, tmp_path):
    body_file = tmp_path / "body.json"
    body_file.write_text('{"kind":"brief","title":"世界观：月亮","client_mutation_id":"中文-1"}', encoding="utf-8")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "data": {}})

    install_transport(monkeypatch, handler)
    base = ["--project", "demo", "script", "documents", "create"]
    result = CliRunner().invoke(cli().app, [*base, "--body-file", str(body_file)])
    assert result.exit_code == 0, result.output
    assert json.loads(seen[0].content)["title"] == "世界观：月亮"
    mixed = CliRunner().invoke(cli().app, [*base, "--body-file", str(body_file), "--json", "{}"])
    assert mixed.exit_code != 0
    assert len(seen) == 1


@pytest.mark.parametrize("bad", ["[1]", "{bad", '"string"'])
def test_malformed_or_non_object_body_stops_before_transport(monkeypatch, bad):
    seen = []
    install_transport(monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json={"ok": True}))
    result = CliRunner().invoke(cli().app, ["--project", "demo", "script", "documents", "create", "--json", bad])
    assert result.exit_code != 0
    assert not seen


@pytest.mark.parametrize("identifier", ["../other", "a/b", "%2e%2e", "x?key=secret", "x#fragment", "x\\other"])
def test_script_identifier_cannot_escape_project(monkeypatch, identifier):
    seen = []
    install_transport(monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json={"ok": True}))
    result = CliRunner().invoke(cli().app, ["--project", "demo", "script", "documents", "get", identifier])
    assert result.exit_code != 0
    assert not seen


def test_api_conflict_detail_is_preserved(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(409, json={
        "detail": {"message": "stale", "current_revision_id": "r2"},
    }))
    result = CliRunner().invoke(cli().app, ["--project", "demo", "script", "documents", "save", "d1", "--json", '{"base_revision_id":"r1","markdown":"x","client_mutation_id":"m1"}'])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["detail"]["current_revision_id"] == "r2"


def test_submission_unknown_has_one_request_and_no_recovery_mutation(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout("lost", request=request)

    install_transport(monkeypatch, handler)
    result = CliRunner().invoke(cli().app, ["--project", "demo", "script", "handoffs", "confirm", "h1", "--json", '{"expected_source_project_revision":0,"client_mutation_id":"m1"}'])
    assert result.exit_code == 1
    assert "submission_unknown" in result.stdout
    assert len(seen) == 1


def test_dry_run_never_contacts_http_and_separates_query_params(monkeypatch):
    seen = []
    install_transport(monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json={"ok": True}))
    result = CliRunner().invoke(cli().app, ["--project", "demo", "--dry-run", "script", "entities", "assets", "--asset-type", "character"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert value["path"].endswith("/script-creation/assets")
    assert value["params"] == {"asset_type": "character"}
    assert not seen


def test_script_module_help_keeps_heavy_server_dependencies_unloaded():
    result = subprocess.run([sys.executable, "-m", "novelvideo.production_cli", "--project", "demo", "script", "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "documents" in result.stdout
    import_result = subprocess.run([sys.executable, "-c", "import sys; import novelvideo.production_cli; assert 'novelvideo.api.routes.script_creation' not in sys.modules; assert 'cognee' not in sys.modules"], capture_output=True, text=True)
    assert import_result.returncode == 0, import_result.stderr


def test_documented_complex_json_bodies_match_api_contract():
    """Keep copyable cookbook examples aligned with the real FastAPI models."""
    from pathlib import Path
    import re

    from novelvideo.api.routes.script_creation import (
        EntityBody, HandoffPrepareBody, RewriteBody, SaveBody,
    )

    cookbook = (Path(__file__).resolve().parents[1] /
                "docs/cookbook/creation/10-script-creation-cli.md").read_text(encoding="utf-8")
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", cookbook, re.S)]
    assert len(examples) == 4
    for model, example in zip((SaveBody, RewriteBody, EntityBody, HandoffPrepareBody), examples):
        model.model_validate(example)
