import json

from typer.testing import CliRunner


def invoke(*args):
    from novelvideo.production_cli import app
    return CliRunner().invoke(app, ["--project", "p", "--dry-run", "canvas", *args])


def test_canvas_cli_capabilities_uses_project_transport():
    result = invoke("capabilities")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["path"] == "/projects/p/freezone/orchestration/capabilities"


def test_canvas_preview_is_a_read_only_public_request():
    result = invoke('preview', 'canvas', '--node', 'board')
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload['method'] == 'GET'
    assert payload['path'] == '/projects/p/freezone/orchestration/canvases/canvas/nodes/board/preview'


def test_run_cli_freezes_explicit_target_and_idempotency():
    result = invoke("run", "canvas", "--target", "final", "--request-id", "unique")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"] == {"canvas_id": "canvas", "target": "final", "request_id": "unique", "operation": None}


def test_run_cli_can_require_the_reviewed_canvas_revision():
    result = invoke('run', 'canvas', '--node', 'board', '--expected-revision', '70')
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['body']['expected_revision'] == 70


def test_cli_patch_uses_file_and_explicit_revision(tmp_path):
    file = tmp_path / "node.json"
    file.write_text(json.dumps({"id": "text", "type": "textAnnotationNode", "data": {"text": "script"}}))
    result = invoke("nodes", "add", "canvas", "--body-file", str(file), "--base-revision", "2", "--request-id", "n1")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)["body"]
    assert body["base_revision"] == 2 and body["operations"][0]["node"]["id"] == "text"


def test_cli_rejects_conflicting_run_selection():
    assert invoke("run", "canvas", "--target", "a", "--node", "b").exit_code == 2


def test_cli_refuses_path_traversal():
    assert invoke("status", "../other").exit_code == 2


def test_node_configure_exposes_visible_image_parameters():
    result = invoke("nodes", "configure", "c", "n", "--base-revision", "3", "--aspect-ratio", "16:9",
                    "--size", "2K", "--quality", "high", "--style", "cinematic-photorealism", "--model", "gpt-image-2")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["body"]["operations"][0]["data"]
    assert data["aspectRatio"] == data["requestAspectRatio"] == "16:9"
    assert data["extensionStyleId"] == "cinematic-photorealism"
    assert data["quality"] == "high" and data["size"] == "2K"


def test_plan_carries_user_director_brief(tmp_path):
    source = tmp_path / 'script.md'
    source.write_text('第一场 房间\n【本集完】')
    result = invoke('plan', 'c', '--script', str(source), '--director-brief', '固定机位，母亲说完再切')
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['body']['director_brief'] == '固定机位，母亲说完再切'


def test_batch_patch_dry_run_describes_read_then_atomic_patch(tmp_path):
    operations = tmp_path / "operations.json"
    operations.write_text(json.dumps([
        {"op": "add_node", "node": {"id": "idea", "type": "ideationNode", "data": {}}},
        {"op": "add_node", "node": {"id": "script", "type": "screenplayNode", "data": {}}},
    ]), encoding="utf-8")

    result = invoke("patch", "canvas", "--body-file", str(operations), "--request-id", "batch-1")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["steps"][0] == {
        "method": "GET", "path": "/projects/p/freezone/canvases/canvas",
    }
    assert payload["steps"][1]["method"] == "POST"
    assert payload["steps"][1]["path"] == "/projects/p/freezone/orchestration/canvases/canvas/patch"
    assert payload["steps"][1]["body"]["base_revision"] == "read-current-revision"
    assert payload["steps"][1]["body"]["request_id"] == "batch-1"
    assert len(payload["steps"][1]["body"]["operations"]) == 2


def test_batch_patch_reads_current_revision_and_does_not_retry_conflicts(tmp_path, monkeypatch):
    from novelvideo import production_cli

    operations = tmp_path / "operations.json"
    operations.write_text(json.dumps([{"op": "add_node", "node": {
        "id": "idea", "type": "ideationNode", "data": {}}}]), encoding="utf-8")
    requests = []

    class Response:
        status_code = 200
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs.get("json")))
            if method == "GET":
                return Response({"ok": True, "data": {"project_id": "p", "revision": 9}})
            response = Response({"detail": {"code": "canvas_conflict"}})
            response.status_code = 409
            response.is_success = False
            return response

    monkeypatch.setattr(production_cli, "_http_client", lambda **kwargs: Client())
    result = CliRunner().invoke(production_cli.app, [
        "--project", "p", "canvas", "patch", "canvas", "--body-file", str(operations), "--request-id", "batch-1",
    ])

    assert result.exit_code == 1
    assert requests == [
        ("GET", "projects/p/freezone/canvases/canvas", None),
        ("POST", "projects/p/freezone/orchestration/canvases/canvas/patch", {
            "base_revision": 9, "request_id": "batch-1", "operations": [
                {"op": "add_node", "node": {"id": "idea", "type": "ideationNode", "data": {}}},
            ],
        }),
    ]


def test_document_and_progress_cli_dry_runs_are_project_scoped(tmp_path):
    text = tmp_path / "script.md"
    text.write_text("正文", encoding="utf-8")
    result = invoke("documents", "write", "canvas", "episodes/003/script.md",
                    "--text-file", str(text), "--expected-hash", "old-hash")

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["method"] == "PUT"
    assert document["path"] == "/projects/p/freezone/orchestration/canvases/canvas/documents"
    assert document["body"] == {
        "path": "episodes/003/script.md", "text": "正文", "expected_hash": "old-hash",
    }

    progress = invoke("progress", "approve", "canvas", "003", "storyboard", "--input-hash", "approval-1")
    assert progress.exit_code == 0, progress.output
    approval = json.loads(progress.output)
    assert approval["method"] == "POST"
    assert approval["path"] == "/projects/p/freezone/orchestration/canvases/canvas/progress/003/approve"
    assert approval["body"] == {"stage": "storyboard", "input_hash": "approval-1"}


def test_progress_cli_can_clear_a_configured_stop_after():
    result = invoke("progress", "configure", "canvas", "003", "--mode", "auto", "--clear-stop-after")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["path"] == "/projects/p/freezone/orchestration/canvases/canvas/progress/003/configure"
    assert payload["body"] == {"mode": "auto", "stop_after": None}

    conflict = invoke("progress", "configure", "canvas", "003", "--stop-after", "storyboard",
                      "--clear-stop-after")
    assert conflict.exit_code == 2


def test_experience_search_is_read_only_and_uses_existing_technique_route():
    result = invoke("experience", "search", "--query", "固定长镜头", "--mode", "FL2V",
                    "--duration", "10", "--limit", "3")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "GET"
    assert payload["path"] == "/projects/p/techniques/experience"
    assert payload["params"] == {
        "query": "固定长镜头", "mode": "FL2V", "duration_seconds": 10.0, "limit": 3,
    }


def test_director_prepare_uses_preview_fingerprint_and_returns_compiled_preview(tmp_path, monkeypatch):
    from novelvideo import production_cli

    draft_file = tmp_path / "draft.json"
    prepared_file = tmp_path / "prepared.json"
    draft_file.write_text(json.dumps({"segments": [{"segment_id": "s1"}]}), encoding="utf-8")
    prepared_file.write_text(json.dumps({"segments": [{"segment_id": "s1", "wire": {"mode": "FL2V"}}]}),
                             encoding="utf-8")
    requests = []

    class Response:
        status_code = 200
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs.get("json")))
            if len(requests) == 1:
                return Response({"ok": True, "data": {"draft_fingerprint": "a" * 64, "segments": []}})
            return Response({"ok": True, "data": {
                "draft_fingerprint": "a" * 64, "preview": {"route": {"model": "h3"}, "timeline_data": []},
            }})

    monkeypatch.setattr(production_cli, "_http_client", lambda **kwargs: Client())
    result = CliRunner().invoke(production_cli.app, [
        "--project", "p", "canvas", "director", "prepare", "--body-file", str(draft_file),
        "--prepared-file", str(prepared_file),
    ])

    assert result.exit_code == 0, result.output
    assert len(requests) == 2
    assert requests[0][1] == "projects/p/freezone/video-director/preview"
    assert requests[0][2] == {"draft": {"segments": [{"segment_id": "s1"}]}}
    assert requests[1][2]["prepared"]["draft_fingerprint"] == "a" * 64
    assert json.loads(result.output)["data"]["preview"]["route"]["model"] == "h3"


def test_director_submit_revalidates_prepared_payload_before_attempt_enqueue(tmp_path, monkeypatch):
    from novelvideo import production_cli

    draft_file = tmp_path / "draft.json"
    prepared_file = tmp_path / "prepared.json"
    draft_file.write_text(json.dumps({"segments": [{"segment_id": "s1"}]}), encoding="utf-8")
    prepared_file.write_text(json.dumps({"segments": [{"segment_id": "s1", "wire": {"mode": "FL2V"}}]}),
                             encoding="utf-8")
    requests = []

    class Response:
        status_code = 200
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs.get("json")))
            if path.endswith("/preview") and "prepared" not in kwargs["json"]:
                return Response({"ok": True, "data": {"draft_fingerprint": "b" * 64}})
            if path.endswith("/preview"):
                return Response({"ok": True, "data": {"draft_fingerprint": "b" * 64,
                                                            "preview": {"timeline_data": []}}})
            return Response({"ok": True, "data": {"attempt_id": "attempt-1"}})

    monkeypatch.setattr(production_cli, "_http_client", lambda **kwargs: Client())
    result = CliRunner().invoke(production_cli.app, [
        "--project", "p", "canvas", "director", "submit", "canvas", "director-node",
        "--body-file", str(draft_file), "--prepared-file", str(prepared_file), "--request-id", "h3-1",
    ])

    assert result.exit_code == 0, result.output
    assert [request[1].rsplit("/", 1)[-1] for request in requests] == ["preview", "preview", "attempts"]
    attempt_body = requests[-1][2]
    assert attempt_body["canvas_id"] == "canvas" and attempt_body["node_id"] == "director-node"
    assert attempt_body["request_id"] == "h3-1"
    assert attempt_body["prepared"]["draft_fingerprint"] == "b" * 64
