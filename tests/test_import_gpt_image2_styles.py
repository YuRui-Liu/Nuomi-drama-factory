from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_gpt_image2_styles.py"
REVISION = "a" * 40


def write_source(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Photography & Realism",
                        "styles": [
                            {
                                "id": "photo-real",
                                "name": "Photo Real",
                                "prompt": " realistic light ",
                                "negative_prompt": " blur ",
                            }
                        ],
                    },
                    {
                        "name": "Unapproved Category",
                        "styles": [
                            {"id": "skip-me", "name": "Skip", "prompt": "x"}
                        ],
                    },
                    {
                        "name": "Illustration & Art",
                        "styles": [{"id": "ink", "name": "Ink", "prompt": "ink wash"}],
                    },
                    {
                        "name": "Characters & People",
                        "styles": [{"id": "portrait", "name": "Portrait", "prompt": "person"}],
                    },
                    {
                        "name": "Scenes & Storytelling",
                        "styles": [{"id": "story", "name": "Story", "prompt": "scene"}],
                    },
                    {
                        "name": "History & Classical Chinese Themes",
                        "styles": [
                            {"id": "han", "name": "Han Court", "prompt": "palace"}
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_filters_allowlist_and_normalizes_unapproved_candidates(tmp_path: Path) -> None:
    source = tmp_path / "style-library.json"
    output = tmp_path / "out"
    write_source(source)

    result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(output))

    assert result.returncode == 0, result.stderr
    payload = json.loads((output / "candidates.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["candidates"]] == [
        "han",
        "ink",
        "photo-real",
        "portrait",
        "story",
    ]
    assert {item["category"] for item in payload["candidates"]} == {
        "Photography & Realism",
        "Illustration & Art",
        "Characters & People",
        "Scenes & Storytelling",
        "History & Classical Chinese Themes",
    }
    photo = payload["candidates"][2]
    assert photo == {
        "category": "Photography & Realism",
        "id": "photo-real",
        "name": "Photo Real",
        "negative_prompt": "blur",
        "prompt": "realistic light",
        "revision": REVISION,
        "source_ids": ["photo-real"],
    }
    assert all("approved" not in item for item in payload["candidates"])


@pytest.mark.parametrize("revision", ["main", "v1.0.0", "abc1234", "A" * 40, "a" * 39])
def test_rejects_moving_or_noncanonical_revision(tmp_path: Path, revision: str) -> None:
    source = tmp_path / "style-library.json"
    write_source(source)
    result = run_cli("--source", str(source), "--revision", revision, "--output", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "40 lowercase hexadecimal" in result.stderr


def test_writes_deterministic_utf8_json_and_does_not_touch_catalog(tmp_path: Path) -> None:
    source = tmp_path / "style-library.json"
    write_source(source)
    catalog = ROOT / "src" / "novelvideo" / "extension_styles" / "catalog.json"
    before = catalog.read_bytes() if catalog.exists() else None

    hashes = []
    for name in ("first", "second"):
        output = tmp_path / name / "nested"
        result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(output))
        assert result.returncode == 0, result.stderr
        assert (output / "candidates.json").exists()
        assert (output / "diff.json").exists()
        hashes.append(
            tuple(hashlib.sha256((output / filename).read_bytes()).hexdigest() for filename in ("candidates.json", "diff.json"))
        )

    assert hashes[0] == hashes[1]
    assert (catalog.read_bytes() if catalog.exists() else None) == before
    assert "Photo Real" in (tmp_path / "first" / "nested" / "candidates.json").read_text(encoding="utf-8")


def test_diff_reports_added_changed_and_removed(tmp_path: Path) -> None:
    source = tmp_path / "style-library.json"
    write_source(source)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "styles": [
                    {"id": "photo-real", "name": "Old Name", "category": "Photography & Realism", "prompt": "old"},
                    {"id": "removed", "name": "Removed", "category": "Illustration & Art", "prompt": "gone"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out"

    result = run_cli(
        "--source", str(source), "--revision", REVISION, "--output", str(output), "--catalog", str(catalog)
    )

    assert result.returncode == 0, result.stderr
    diff = json.loads((output / "diff.json").read_text(encoding="utf-8"))
    assert diff == {
        "added": ["han", "ink", "portrait", "story"],
        "changed": ["photo-real"],
        "removed": ["removed"],
    }


def test_missing_catalog_marks_every_candidate_added(tmp_path: Path) -> None:
    source = tmp_path / "style-library.json"
    write_source(source)
    output = tmp_path / "out"
    result = run_cli(
        "--source", str(source), "--revision", REVISION, "--output", str(output), "--catalog", str(tmp_path / "missing.json")
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((output / "diff.json").read_text(encoding="utf-8")) == {
        "added": ["han", "ink", "photo-real", "portrait", "story"], "changed": [], "removed": []
    }


def test_cli_requires_source_revision_and_output() -> None:
    result = run_cli()
    assert result.returncode == 2
    assert "--source" in result.stderr
    assert "--revision" in result.stderr
    assert "--output" in result.stderr


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"unexpected": []}, "unsupported style library schema"),
        ({"categories": "wrong"}, "categories must be a list or object"),
        ({"categories": [None, "bad", {"name": "Photography & Realism", "styles": [None]}]}, "no valid style records"),
    ],
)
def test_rejects_unknown_or_fully_malformed_source_schema(
    tmp_path: Path, payload: object, message: str
) -> None:
    source = tmp_path / "style-library.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(tmp_path / "out"))
    assert result.returncode != 0
    assert message in result.stderr
    assert str(source) in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out" / "diff.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"categories": []},
        {"categories": [{"name": "Not Allowed", "styles": [{"id": "x", "name": "X", "prompt": "x"}]}]},
    ],
)
def test_valid_empty_or_entirely_non_allowlisted_source_produces_empty_candidates(
    tmp_path: Path, payload: object
) -> None:
    source = tmp_path / "style-library.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "out"
    result = run_cli(
        "--source", str(source), "--revision", REVISION, "--output", str(output),
        "--catalog", str(tmp_path / "missing-catalog.json"),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((output / "candidates.json").read_text(encoding="utf-8"))["candidates"] == []
    assert json.loads((output / "diff.json").read_text(encoding="utf-8")) == {
        "added": [], "changed": [], "removed": []
    }


def test_rejects_duplicate_normalized_style_ids(tmp_path: Path) -> None:
    source = tmp_path / "style-library.json"
    source.write_text(
        json.dumps({"styles": [
            {"id": "same", "category": "Illustration & Art", "prompt": "one"},
            {"id": "same", "category": "Characters & People", "prompt": "two"},
        ]}),
        encoding="utf-8",
    )
    result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "duplicate style id: same" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("target", ["source", "catalog"])
def test_invalid_json_reports_path_line_and_column_without_traceback(tmp_path: Path, target: str) -> None:
    source = tmp_path / "source.json"
    source.write_text("{\n bad", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{\n bad", encoding="utf-8")
    if target == "catalog":
        write_source(source)
    result = run_cli(
        "--source", str(source), "--revision", REVISION, "--output", str(tmp_path / "out"),
        "--catalog", str(catalog),
    )
    expected_path = source if target == "source" else catalog
    assert result.returncode != 0
    assert str(expected_path) in result.stderr
    assert "line 2 column" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_source_reports_path_without_traceback(tmp_path: Path) -> None:
    source = tmp_path / "missing.json"
    result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(tmp_path / "out"))
    assert result.returncode != 0
    assert str(source) in result.stderr
    assert "Traceback" not in result.stderr


def test_output_write_failure_reports_path_without_traceback(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    write_source(source)
    output = tmp_path / "already-a-file"
    output.write_text("block mkdir", encoding="utf-8")
    result = run_cli("--source", str(source), "--revision", REVISION, "--output", str(output))
    assert result.returncode != 0
    assert str(output) in result.stderr
    assert "Traceback" not in result.stderr
