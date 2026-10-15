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
