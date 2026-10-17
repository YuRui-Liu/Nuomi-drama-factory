import json
from typer.testing import CliRunner
from novelvideo.runninghub_cli import app


def test_help_and_offline_inspect(tmp_path):
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "resume" in help_result.output
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps({"1": {"class_type": "Text", "inputs": {"text": "hello"}}})
    )
    result = runner.invoke(app, ["inspect", str(path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["nodes"][0]["node_id"] == "1"


def test_invalid_graph_json_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"nodes": []}')
    result = CliRunner().invoke(app, ["inspect", str(path)])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "invalid_api_graph"
