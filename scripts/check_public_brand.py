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
    ".github/PULL_REQUEST_TEMPLATE.md",
}
_PUBLIC_TREES = (
    "docs/",
    "readme/",
    ".github/ISSUE_TEMPLATE/",
    ".github/PULL_REQUEST_TEMPLATE/",
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
_COMPATIBILITY_EXPLANATIONS = (
    re.compile(
        r"(?P<brand>dramaclaw)\s+is\s+(?:retained|kept)\s+as\s+an\s+"
        r"internal compatibility name",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<brand>dramaclaw)\s*(?:仅|仍)?作为内部兼容名称保留",
        re.IGNORECASE,
    ),
)
_ALLOWED_OCCURRENCES = re.compile(
    r"(?<![A-Za-z0-9_])DRAMACLAW_[A-Z0-9_]+(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])dramaclaw_[a-z0-9_]+(?![A-Za-z0-9_])"
    r"|https://nfg-web-assets\.cdnfg\.com/dramaclaw/[^\s\"'<>)]*"
)


def _is_excluded_path(path: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in path.parts)
    if len(lowered_parts) >= 2 and lowered_parts[:2] in {
        ("docs", "plans"),
        ("docs", "superpowers"),
    }:
        return True

    for part in path.parts:
        lowered = part.lower()
        normalized = re.sub(r"[^a-z0-9]", "", lowered)
        if lowered == "licenses":
            return True
        if normalized == "thirdparty" or normalized.startswith("thirdpartylicenses"):
            return True
        if normalized.startswith("hermes"):
            return True
    return False


def is_public_path(path: Path) -> bool:
    """Return whether a repository-relative path belongs to the public surface."""
    normalized = path.as_posix().removeprefix("./")

    if _is_excluded_path(Path(normalized)):
        return False
    if Path(normalized).name in _LOCK_NAMES or normalized.endswith(".lock"):
        return False

    in_scope = normalized in _PUBLIC_FILES or normalized.startswith(_PUBLIC_TREES)
    if not in_scope:
        return False
    return normalized == "NOTICE" or Path(normalized).suffix.lower() in _TEXT_SUFFIXES


def _compatibility_spans(line: str) -> list[tuple[int, int]]:
    return [
        match.span("brand")
        for pattern in _COMPATIBILITY_EXPLANATIONS
        for match in pattern.finditer(line)
    ]


def scan_text(path: Path, text: str) -> list[str]:
    """Return ``path:line`` findings for disallowed legacy-brand occurrences."""
    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        matches = list(_BRAND.finditer(line))
        if not matches:
            continue

        allowed_spans = [match.span() for match in _ALLOWED_OCCURRENCES.finditer(line)]
        allowed_spans.extend(_compatibility_spans(line))
        if any(
            not any(start <= match.start() and match.end() <= end for start, end in allowed_spans)
            for match in matches
        ):
            findings.append(f"{path.as_posix()}:{line_number}")
    return findings


def _public_files(root: Path) -> list[Path]:
    candidates = {root / relative for relative in _PUBLIC_FILES}
    for relative_tree in _PUBLIC_TREES:
        tree = root / relative_tree
        if tree.is_dir():
            candidates.update(path for path in tree.rglob("*") if path.is_file())
    return sorted(
        (
            path
            for path in candidates
            if path.is_file() and is_public_path(path.relative_to(root))
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def scan_repository(root: Path) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    errors: list[str] = []
    for path in _public_files(root):
        relative_path = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative_path.as_posix()}: unable to read as UTF-8")
            continue
        except OSError as exc:
            errors.append(f"{relative_path.as_posix()}: unable to read: {exc}")
            continue
        findings.extend(scan_text(relative_path, text))
    return sorted(findings), sorted(errors)


def main(root: Path = REPO_ROOT) -> int:
    findings, errors = scan_repository(root)

    if findings:
        print("Legacy DramaClaw public branding found:", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
    if errors:
        print("Public branding scan errors:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
    if findings or errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
