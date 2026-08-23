from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/novelvideo/extension_styles/catalog.json"
GENERATED = (
    ROOT
    / "frontend/src/features/canvas/extension-styles/catalog.generated.json"
)
SCRIPT = ROOT / "scripts/sync_extension_style_catalog.py"


def _run(source: Path, destination: Path, *, check: bool = False):
    command = [
        sys.executable,
        str(SCRIPT),
        "--source",
        str(source),
        "--destination",
        str(destination),
    ]
    if check:
        command.append("--check")
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def test_checked_in_snapshot_matches_backend_without_writing() -> None:
    before = GENERATED.read_bytes()
    result = _run(SOURCE, GENERATED, check=True)
    assert result.returncode == 0
    assert result.stderr == ""
    assert GENERATED.read_bytes() == before


def test_sync_writes_semantically_identical_catalog_and_is_idempotent(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "catalog.json"
    assert _run(SOURCE, destination).returncode == 0
    first = destination.read_bytes()
    assert json.loads(first) == json.loads(SOURCE.read_text(encoding="utf-8"))
    assert _run(SOURCE, destination).returncode == 0
    assert destination.read_bytes() == first


def test_check_reports_drift_without_writing(tmp_path: Path) -> None:
    destination = tmp_path / "catalog.json"
    destination.write_text("[]\n", encoding="utf-8")
    before = destination.read_bytes()
    result = _run(SOURCE, destination, check=True)
    assert result.returncode == 1
    assert result.stderr == "extension style catalog snapshot is stale\n"
    assert destination.read_bytes() == before


def test_check_reports_missing_destination(tmp_path: Path) -> None:
    result = _run(SOURCE, tmp_path / "missing.json", check=True)
    assert result.returncode == 1
    assert result.stderr == "extension style catalog snapshot is missing\n"


def test_invalid_source_json_has_stable_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "invalid.json"
    source.write_text("{", encoding="utf-8")
    result = _run(source, tmp_path / "generated.json")
    assert result.returncode == 2
    assert result.stderr == "extension style catalog source is invalid JSON\n"
