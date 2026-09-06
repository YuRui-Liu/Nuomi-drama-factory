#!/usr/bin/env python3
"""Prepare and atomically apply Nuomi extension-style releases."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tempfile
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from novelvideo.extension_styles.schema import ExtensionStyle  # noqa: E402


SCHEMA_VERSION = 1
CATALOG_PATH = Path("src/novelvideo/extension_styles/catalog.json")
AUDIT_PATH = Path("src/novelvideo/extension_styles/source_audit.json")
SNAPSHOT_PATH = Path(
    "frontend/src/features/canvas/extension-styles/catalog.generated.json"
)
PREVIEW_DIR = Path("frontend/public/images/extension-styles")


class PublishError(Exception):
    """Expected, user-actionable publishing failure."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublishError(f"{label} is unreadable or invalid JSON: {error}") from error


def _catalog_entries(catalog_bytes: bytes) -> list[dict[str, Any]]:
    try:
        raw = json.loads(catalog_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PublishError(f"extension style catalog is invalid: {error}") from error
    if not isinstance(raw, list):
        raise PublishError("extension style catalog must be a JSON array")
    if not all(isinstance(entry, dict) for entry in raw):
        raise PublishError("extension style catalog entries must be objects")
    _validate_catalog(raw)
    return raw


def _validate_catalog(entries: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in entries:
        try:
            style = ExtensionStyle.from_dict(entry)
        except ValueError as error:
            raise PublishError(f"invalid extension style: {error}") from error
        if style.id in seen:
            raise PublishError(f"duplicate extension style id: {style.id}")
        seen.add(style.id)


def _validate_manifest(raw: Any) -> tuple[ExtensionStyle, str]:
    if not isinstance(raw, Mapping) or set(raw) != {"style", "preview_prompt"}:
        raise PublishError("manifest must contain exactly style and preview_prompt")
    try:
        style = ExtensionStyle.from_dict(raw["style"])
    except ValueError as error:
        raise PublishError(f"invalid style: {error}") from error
    preview_prompt = raw["preview_prompt"]
    if not isinstance(preview_prompt, str) or not preview_prompt.strip():
        raise PublishError("preview_prompt must be a non-empty string")
    return style, preview_prompt.strip()


def _validate_source(style: ExtensionStyle, audit: Any) -> None:
    if not isinstance(audit, Mapping):
        raise PublishError("source audit must be an object")
    if audit.get("license", {}).get("review") != "approved":
        raise PublishError("source audit license review is not approved")
    source = style.source
    if source.get("repository") != audit.get("repository"):
        raise PublishError("style source repository does not match source audit")
    if source.get("imported_revision") != audit.get("revision"):
        raise PublishError("style imported revision does not match source audit")
    source_ids = source.get("source_ids")
    if not isinstance(source_ids, tuple) or not source_ids:
        raise PublishError("style source_ids must be a non-empty array")
    templates = audit.get("templates")
    if not isinstance(templates, list):
        raise PublishError("source audit templates must be an array")
    audited_ids = {
        item.get("id") for item in templates if isinstance(item, Mapping)
    }
    unknown = sorted(set(source_ids) - audited_ids)
    if unknown:
        raise PublishError(
            "style source_ids are not present in source audit: " + ", ".join(unknown)
        )


def _preview_target(style: ExtensionStyle) -> tuple[str, Path]:
    slug = style.id.removeprefix("drama_ext.").replace("_", "-")
    preview_asset = f"/images/extension-styles/{slug}.webp"
    if style.preview_asset != preview_asset:
        raise PublishError(
            f"preview_asset is not canonical; expected {preview_asset}"
        )
    return preview_asset, PREVIEW_DIR / f"{slug}.webp"


def _validate_candidate(
    project_root: Path,
    style: ExtensionStyle,
    entries: list[dict[str, Any]],
) -> Path:
    _, preview_target = _preview_target(style)
    if any(entry.get("id") == style.id for entry in entries):
        raise PublishError(f"extension style id already exists: {style.id}")
    if any(entry.get("preview_asset") == style.preview_asset for entry in entries):
        raise PublishError(f"preview asset already exists in catalog: {style.preview_asset}")
    if (project_root / preview_target).exists():
        raise PublishError(f"preview asset file already exists: {preview_target}")
    audit = _read_json(project_root / AUDIT_PATH, "source audit")
    _validate_source(style, audit)
    return preview_target


def prepare_release(project_root: Path, manifest_path: Path) -> dict[str, object]:
    """Validate a manifest and return a side-effect-free release plan."""

    project_root = project_root.resolve()
    style, preview_prompt = _validate_manifest(
        _read_json(manifest_path, "release manifest")
    )
    catalog_path = project_root / CATALOG_PATH
    try:
        catalog_bytes = catalog_path.read_bytes()
    except OSError as error:
        raise PublishError(f"extension style catalog is unreadable: {error}") from error
    entries = _catalog_entries(catalog_bytes)
    preview_target = _validate_candidate(project_root, style, entries)
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "style": style.projection_input(),
        "preview_prompt": preview_prompt,
        "targets": {
            "catalog": CATALOG_PATH.as_posix(),
            "snapshot": SNAPSHOT_PATH.as_posix(),
            "preview": preview_target.as_posix(),
        },
    }


def _append_catalog_entry(original: bytes, style: dict[str, Any]) -> bytes:
    text = original.decode("utf-8")
    stripped = text.rstrip()
    if not stripped.endswith("]"):
        raise PublishError("extension style catalog must end with a JSON array")
    prefix = stripped[:-1].rstrip()
    rendered = textwrap.indent(
        json.dumps(style, ensure_ascii=False, indent=2), "  "
    )
    separator = "\n" if prefix.endswith("[") else ",\n"
    return f"{prefix}{separator}{rendered}\n]\n".encode("utf-8")


def _render_snapshot(entries: list[dict[str, Any]]) -> bytes:
    return (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _render_preview(source: Path) -> bytes:
    output = io.BytesIO()
    try:
        with Image.open(source) as image:
            image.load()
            normalized = ImageOps.fit(
                image.convert("RGB"),
                (640, 360),
                method=Image.Resampling.LANCZOS,
            )
            normalized.save(output, format="WEBP", quality=88, method=6)
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise PublishError(f"preview image is unreadable or invalid: {error}") from error
    return output.getvalue()


def _replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _commit_payloads(payloads: dict[Path, bytes]) -> None:
    originals = {
        path: path.read_bytes() if path.exists() else None for path in payloads
    }
    completed: list[Path] = []
    try:
        for path, payload in payloads.items():
            _replace_bytes(path, payload)
            completed.append(path)
    except OSError as error:
        recovery_errors: list[str] = []
        for path in reversed(completed):
            try:
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _replace_bytes(path, original)
            except OSError as recovery_error:
                recovery_errors.append(f"{path}: {recovery_error}")
        message = f"failed to write release: {error}"
        if recovery_errors:
            message += "; recovery failed: " + "; ".join(recovery_errors)
        raise PublishError(message) from error


def _validate_plan(raw: Any) -> tuple[ExtensionStyle, dict[str, str]]:
    if not isinstance(raw, Mapping):
        raise PublishError("release plan must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise PublishError(f"unsupported release plan schema: {raw.get('schema_version')}")
    try:
        style = ExtensionStyle.from_dict(raw["style"])
    except (KeyError, ValueError) as error:
        raise PublishError(f"release plan style is invalid: {error}") from error
    _, preview_target = _preview_target(style)
    expected_targets = {
        "catalog": CATALOG_PATH.as_posix(),
        "snapshot": SNAPSHOT_PATH.as_posix(),
        "preview": preview_target.as_posix(),
    }
    if raw.get("targets") != expected_targets:
        raise PublishError("release plan targets are not the fixed project targets")
    if not isinstance(raw.get("catalog_sha256"), str):
        raise PublishError("release plan catalog_sha256 is invalid")
    return style, expected_targets


def apply_release(
    project_root: Path,
    plan_path: Path,
    preview_path: Path,
) -> dict[str, object]:
    """Apply a validated plan to the three fixed product targets."""

    project_root = project_root.resolve()
    plan = _read_json(plan_path, "release plan")
    style, targets = _validate_plan(plan)
    catalog_path = project_root / CATALOG_PATH
    try:
        catalog_bytes = catalog_path.read_bytes()
    except OSError as error:
        raise PublishError(f"extension style catalog is unreadable: {error}") from error
    if hashlib.sha256(catalog_bytes).hexdigest() != plan["catalog_sha256"]:
        raise PublishError(
            "extension style catalog changed after prepare; run prepare again",
            exit_code=3,
        )
    entries = _catalog_entries(catalog_bytes)
    _validate_candidate(project_root, style, entries)
    style_data = style.projection_input()
    new_catalog = _append_catalog_entry(catalog_bytes, style_data)
    new_entries = _catalog_entries(new_catalog)
    preview_bytes = _render_preview(preview_path)
    payloads = {
        project_root / targets["catalog"]: new_catalog,
        project_root / targets["snapshot"]: _render_snapshot(new_entries),
        project_root / targets["preview"]: preview_bytes,
    }
    _commit_payloads(payloads)
    return {"style_id": style.id, "targets": targets}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--project-root", type=Path, default=REPOSITORY_ROOT)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--preview", type=Path, required=True)
    apply.add_argument("--project-root", type=Path, default=REPOSITORY_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            plan = prepare_release(args.project_root, args.manifest)
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(json.dumps(plan, ensure_ascii=False))
        else:
            summary = apply_release(args.project_root, args.plan, args.preview)
            print(json.dumps(summary, ensure_ascii=False))
    except PublishError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
