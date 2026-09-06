#!/usr/bin/env python3
"""Reject legacy DramaClaw branding in public documentation and UI text."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

_PUBLIC_FILES = {
    "README.md",
    "readme/README_zh.md",
    "docs/README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "NOTICE",
    "frontend/index.html",
}
_PUBLIC_TREES = (
    "docs/en/",
    "docs/zh/",
    "docs/cookbook/",
    ".github/ISSUE_TEMPLATE/",
    "frontend/src/",
)
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mdx",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_LOCK_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
_BRAND = re.compile(r"dramaclaw", re.IGNORECASE)
_ALLOWED_OCCURRENCES = re.compile(
    r"(?<![A-Za-z0-9_])DRAMACLAW_[A-Z0-9_]+(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])dramaclaw_[a-z0-9_]+(?![A-Za-z0-9_])"
    r"|https://nfg-web-assets\.cdnfg\.com/dramaclaw(?:/[^\s\"'<>)]*)?"
)


def is_public_path(path: Path) -> bool:
    """Return whether a repository-relative path belongs to the public surface."""
    normalized = path.as_posix().removeprefix("./")
    lowered_parts = {part.lower() for part in Path(normalized).parts}

    if normalized.startswith(("docs/plans/", "docs/superpowers/")):
        return False
    if Path(normalized).name in _LOCK_NAMES or normalized.endswith(".lock"):
        return False
    if "licenses" in lowered_parts or "third_party" in lowered_parts:
        return False
    if "hermes" in lowered_parts or "hermes-plugin" in normalized.lower():
        return False

    in_scope = normalized in _PUBLIC_FILES or normalized.startswith(_PUBLIC_TREES)
    if not in_scope:
        return False
    return normalized == "NOTICE" or Path(normalized).suffix.lower() in _TEXT_SUFFIXES


def _is_compatibility_explanation(line: str) -> bool:
    lowered = line.lower()
    return "internal compatibility name" in lowered or "内部兼容名称" in line


def scan_text(path: Path, text: str) -> list[str]:
    """Return ``path:line`` findings for disallowed legacy-brand occurrences."""
    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        matches = list(_BRAND.finditer(line))
        if not matches or _is_compatibility_explanation(line):
            continue

        allowed_spans = [match.span() for match in _ALLOWED_OCCURRENCES.finditer(line)]
        if any(
            not any(start <= match.start() and match.end() <= end for start, end in allowed_spans)
            for match in matches
        ):
            findings.append(f"{path.as_posix()}:{line_number}")
    return findings


def _public_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and is_public_path(path.relative_to(root)):
            yield path


def main() -> int:
    findings: list[str] = []
    for path in _public_files(REPO_ROOT):
        relative_path = path.relative_to(REPO_ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(relative_path, text))

    if findings:
        print("Legacy DramaClaw public branding found:", file=sys.stderr)
        print("\n".join(sorted(findings)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
