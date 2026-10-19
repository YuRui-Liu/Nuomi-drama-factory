from typer.testing import CliRunner

from novelvideo.creation.asset_design.cli import assets_app


def test_validate_emits_json_envelope(tmp_path):
    runner = CliRunner()
    result = runner.invoke(assets_app, ["validate", "character/lin-du", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "brief_not_found" in result.stdout
