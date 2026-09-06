from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_public_brand.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("check_public_brand", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_text_reports_public_brand_display_text(tmp_path: Path) -> None:
    scanner = _load_scanner()
    public_file = tmp_path / "README.md"
    public_file.write_text(
        "Welcome\nBuild stories with DramaClaw today.\n",
        encoding="utf-8",
    )

    findings = scanner.scan_text(
        Path("README.md"),
        public_file.read_text(encoding="utf-8"),
    )

    assert findings == ["README.md:2"]


def test_scan_text_allows_compatibility_identifiers_and_asset_urls() -> None:
    scanner = _load_scanner()
    text = "\n".join(
        [
            "DRAMACLAW_API_URL=https://example.invalid",
            "tool_name = dramaclaw_get",
            "https://nfg-web-assets.cdnfg.com/dramaclaw/posters/hero.webp",
            "DramaClaw is retained as an internal compatibility name.",
        ]
    )

    assert scanner.scan_text(Path("frontend/src/example.ts"), text) == []


def test_scan_text_rejects_identifiers_without_exact_case_and_boundaries() -> None:
    scanner = _load_scanner()
    text = "\n".join(
        [
            "DRAMACLAW_Api",
            "DRAMACLAW_API_URLx",
            "dramaclaw_getX",
            "xDRAMACLAW_API_URL",
        ]
    )

    assert scanner.scan_text(Path("frontend/src/example.ts"), text) == [
        "frontend/src/example.ts:1",
        "frontend/src/example.ts:2",
        "frontend/src/example.ts:3",
        "frontend/src/example.ts:4",
    ]


def test_public_paths_include_docs_and_frontend_but_exclude_internal_content() -> None:
    scanner = _load_scanner()

    assert scanner.is_public_path(Path("docs/en/guide.md"))
    assert scanner.is_public_path(Path("docs/cookbook/example.md"))
    assert scanner.is_public_path(Path(".github/ISSUE_TEMPLATE/bug.yml"))
    assert scanner.is_public_path(Path("frontend/src/components/header.tsx"))
    assert not scanner.is_public_path(Path("docs/plans/migration.md"))
    assert not scanner.is_public_path(Path("docs/superpowers/spec.md"))
    assert not scanner.is_public_path(Path("package-lock.json"))
    assert not scanner.is_public_path(Path("LICENSES/vendor.txt"))
    assert not scanner.is_public_path(Path("src/novelvideo/hermes/dramaclaw.py"))
