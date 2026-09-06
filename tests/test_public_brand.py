from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


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


def test_scan_text_rejects_negated_compatibility_explanation() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("docs/compatibility.md"),
        "DramaClaw is not retained as an internal compatibility name.",
    ) == ["docs/compatibility.md:1"]


def test_scan_text_only_allows_the_occurrence_named_by_compatibility_explanation() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("docs/compatibility.md"),
        "DramaClaw is retained as an internal compatibility name; use DramaClaw publicly.",
    ) == ["docs/compatibility.md:1"]


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


def test_scan_text_rejects_cdn_paths_without_an_exact_brand_segment() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("README.md"),
        "https://nfg-web-assets.cdnfg.com/dramaclawX/poster.webp",
    ) == ["README.md:1"]


def test_public_paths_include_docs_and_frontend_but_exclude_internal_content() -> None:
    scanner = _load_scanner()

    assert scanner.is_public_path(Path("docs/en/guide.md"))
    assert scanner.is_public_path(Path("docs/cookbook/example.md"))
    assert scanner.is_public_path(Path("docs/releasing.md"))
    assert scanner.is_public_path(Path("docs/operations/runbook.md"))
    assert scanner.is_public_path(Path("readme/another-language.md"))
    assert scanner.is_public_path(Path(".github/ISSUE_TEMPLATE/bug.yml"))
    assert scanner.is_public_path(Path(".github/PULL_REQUEST_TEMPLATE.md"))
    assert scanner.is_public_path(Path(".github/PULL_REQUEST_TEMPLATE/release.md"))
    assert scanner.is_public_path(Path("frontend/src/components/header.tsx"))
    assert scanner.is_public_path(Path("frontend/src/plans/editor.tsx"))
    assert scanner.is_public_path(Path("frontend/src/superpowers/panel.tsx"))
    assert scanner.is_public_path(Path("readme/plans/guide.md"))
    assert not scanner.is_public_path(Path("docs/plans/migration.md"))
    assert not scanner.is_public_path(Path("docs/superpowers/spec.md"))
    assert not scanner.is_public_path(Path(".github/workflows/ci.yml"))
    assert not scanner.is_public_path(Path("package-lock.json"))
    assert not scanner.is_public_path(Path("LICENSES/vendor.txt"))
    assert not scanner.is_public_path(Path("docs/third-party/vendor.md"))
    assert not scanner.is_public_path(Path("docs/third_party/vendor.md"))
    assert not scanner.is_public_path(Path("docs/THIRD-PARTY-LICENSES.txt"))
    assert not scanner.is_public_path(Path("docs/hermes-plugin.md"))
    assert not scanner.is_public_path(Path("docs/HermesPlugin/README.md"))
    assert not scanner.is_public_path(Path("src/novelvideo/hermes/dramaclaw.py"))


def test_repository_scan_and_cli_report_only_public_findings(
    tmp_path: Path, capsys
) -> None:
    scanner = _load_scanner()
    files = {
        "README.md": "DramaClaw heading\n",
        "readme/README_fr.md": "About DramaClaw\n",
        "docs/operations/runbook.md": "Run DramaClaw\n",
        "frontend/src/header.tsx": 'const title = "DramaClaw";\n',
        ".github/ISSUE_TEMPLATE/bug.yml": "description: DramaClaw bug\n",
        ".github/PULL_REQUEST_TEMPLATE.md": "DramaClaw checklist\n",
        "docs/plans/rename.md": "DramaClaw ignored\n",
        "docs/third_party/LICENSE.md": "DramaClaw ignored\n",
        "docs/hermes-plugin.md": "DramaClaw ignored\n",
        ".github/workflows/ci.yml": "name: DramaClaw ignored\n",
        "src/internal.py": "DramaClaw ignored\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    findings, errors = scanner.scan_repository(tmp_path)

    assert errors == []
    assert findings == [
        ".github/ISSUE_TEMPLATE/bug.yml:1",
        ".github/PULL_REQUEST_TEMPLATE.md:1",
        "README.md:1",
        "docs/operations/runbook.md:1",
        "frontend/src/header.tsx:1",
        "readme/README_fr.md:1",
    ]
    assert scanner.main(tmp_path) == 1
    captured = capsys.readouterr()
    assert "Legacy DramaClaw public branding found:" in captured.err
    assert "docs/operations/runbook.md:1" in captured.err
    assert "docs/plans/rename.md" not in captured.err


def test_cli_returns_zero_for_clean_public_tree(tmp_path: Path, capsys) -> None:
    scanner = _load_scanner()
    readme = tmp_path / "README.md"
    readme.write_text("# Nuomi\n", encoding="utf-8")

    assert scanner.main(tmp_path) == 0
    assert capsys.readouterr().err == ""


def test_cli_reports_non_utf8_public_files(tmp_path: Path, capsys) -> None:
    scanner = _load_scanner()
    document = tmp_path / "docs" / "broken.md"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"\xff\xfe")

    assert scanner.main(tmp_path) == 1
    captured = capsys.readouterr()
    assert "docs/broken.md: unable to read as UTF-8" in captured.err


def test_repository_scan_reports_a_public_file_that_disappears(
    tmp_path: Path, monkeypatch
) -> None:
    scanner = _load_scanner()
    readme = tmp_path / "README.md"
    readme.write_text("DramaClaw\n", encoding="utf-8")
    original_read_text = Path.read_text

    def disappearing_read(path: Path, *args, **kwargs):
        if path == readme:
            raise FileNotFoundError("removed during scan")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", disappearing_read)

    findings, errors = scanner.scan_repository(tmp_path)

    assert findings == []
    assert len(errors) == 1
    assert errors[0].startswith("README.md: unable to read:")


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("docs/zh/config.md", "默认名称为 `dramaclaw-ce-runtime`。"),
        ("docs/zh/config.md", "示例目录是 `dramaclaw-relay`。"),
        ("docs/en/self-hosting.md", "-v dramaclaw-ce_ce-data:/data"),
        ("frontend/src/main.tsx", 'import "dramaclaw-spec-render/style.css";'),
        ("frontend/src/index.css", '@source "../node_modules/dramaclaw-spec-render/dist";'),
        (
            "frontend/src/hooks/use-github-stars.ts",
            'const KEY = "dramaclaw.login.githubStars";',
        ),
        (
            "frontend/src/lib/release-notification-state.ts",
            'const KEY = "dramaclaw:release-notifications:muted";',
        ),
        (
            "frontend/src/lib/release-notification-state.ts",
            'return `dramaclaw:release-seen:${tag}`;',
        ),
        (
            "frontend/src/lib/queries/model-gateway.ts",
            'type Provider = "deepseek" | "dramaclaw";',
        ),
        (
            "frontend/src/components/settings/text-runtime-panel.tsx",
            '<SelectItem value="dramaclaw">Nuomi Drama Factory API</SelectItem>',
        ),
        (
            "frontend/src/components/login/cinematic/media.ts",
            'const CDN_BASE = "https://nfg-web-assets.cdnfg.com/dramaclaw";',
        ),
        (
            "frontend/src/features/canvas/nodes/Pano360ViewerNode.tsx",
            "// 历史兼容协议接口 /__dramaclaw 已由 JSON 导出替代。",
        ),
        (
            "frontend/src/lib/desktop-download.test.ts",
            'const oldInstaller = "DramaClaw-Setup-1.1.0.exe";',
        ),
        (
            "frontend/src/__tests__/lib/gateway-error-classify.test.ts",
            "const oldError = 'DramaClawAPI image generation failed';",
        ),
        (
            "frontend/src/__tests__/components/brand/brand-mark.test.tsx",
            "expect(html).not.toMatch(/DramaClaw|SuperTale/);",
        ),
        (
            "frontend/src/__tests__/i18n/locales-json.test.ts",
            "const legacyProductLanguage = /DramaClaw|SuperTale/;",
        ),
        (
            "frontend/src/__tests__/i18n/locales-json.test.ts",
            "/DramaClaw|SuperTale|Xia Director/;",
        ),
        (
            "frontend/src/__tests__/features/brand/runtime-brand-contract.test.ts",
            r"expect(source).not.toMatch(/SuperTale|DramaClaw\/SuperTale/);",
        ),
        (
            "frontend/src/components/settings/text-runtime-panel.tsx",
            "dramaclaw: {",
        ),
        (
            "frontend/src/features/superchat/message.ts",
            r"/\n?\[(DRAMACLAW_[A-Z0-9_]+)\]/g;",
        ),
        (
            "frontend/src/lib/desktop-download.ts",
            "&& !/(?:DramaClaw|SuperTale)/i.test(candidate.name)",
        ),
    ],
)
def test_scan_text_allows_explicit_legacy_machine_contracts(path: str, line: str) -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(Path(path), line) == []


def test_explicit_allowlist_does_not_hide_public_brand_on_the_same_line() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("frontend/src/__tests__/components/brand/brand-mark.test.tsx"),
        'expect(html).not.toMatch(/DramaClaw/); const label = "DramaClaw";',
    ) == ["frontend/src/__tests__/components/brand/brand-mark.test.tsx:1"]


def test_negative_assertion_does_not_hide_brand_in_expect_argument() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("frontend/src/__tests__/components/brand/brand-mark.test.tsx"),
        'expect("public DramaClaw label").not.toMatch(/DramaClaw/);',
    ) == ["frontend/src/__tests__/components/brand/brand-mark.test.tsx:1"]


def test_commented_negative_assertion_is_not_allowlisted() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("frontend/src/__tests__/components/brand/brand-mark.test.tsx"),
        "  // expect(label).not.toMatch(/DramaClaw/);",
    ) == ["frontend/src/__tests__/components/brand/brand-mark.test.tsx:1"]


def test_explicit_allowlist_rejects_unknown_machine_brand() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(Path("docs/guide.md"), "`dramaclaw-new-brand`") == [
        "docs/guide.md:1"
    ]


def test_explicit_allowlist_does_not_allow_arbitrary_test_fixtures() -> None:
    scanner = _load_scanner()

    assert scanner.scan_text(
        Path("frontend/src/__tests__/new-brand.test.ts"),
        'const fixture = "DramaClaw-Setup-1.1.0.exe";',
    ) == ["frontend/src/__tests__/new-brand.test.ts:1"]
