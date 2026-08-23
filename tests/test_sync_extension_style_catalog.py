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


def _run_sync() -> None:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)


def _run_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_generated_catalog_is_semantically_identical_to_backend_catalog() -> None:
    _run_sync()
    assert json.loads(GENERATED.read_text(encoding="utf-8")) == json.loads(
        SOURCE.read_text(encoding="utf-8")
    )


def test_sync_is_idempotent() -> None:
    _run_sync()
    first = GENERATED.read_bytes()
    _run_sync()
    assert GENERATED.read_bytes() == first


def test_check_reports_drift_without_writing_and_passes_after_sync() -> None:
    _run_sync()
    synchronized = GENERATED.read_bytes()
    drifted = synchronized + b"\n"
    GENERATED.write_bytes(drifted)

    result = _run_check()

    assert result.returncode != 0
    assert GENERATED.read_bytes() == drifted

    _run_sync()
    assert _run_check().returncode == 0
