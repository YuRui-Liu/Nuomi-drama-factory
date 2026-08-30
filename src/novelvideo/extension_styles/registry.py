"""Thread-safe, last-known-good extension style catalog registry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .schema import ExtensionStyle, load_catalog

_UNREADABLE_ATTEMPT = object()


@dataclass(frozen=True)
class CatalogFingerprint:
    mtime_ns: int
    size: int
    sha256: str


@dataclass(frozen=True)
class CatalogError:
    code: str
    file: str


@dataclass(frozen=True)
class CatalogDiagnostics:
    discovered: int = 0
    loaded: int = 0
    failed: int = 0
    degraded: bool = False
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    attempted_status: str = "never"
    attempted_fingerprint: CatalogFingerprint | None = None
    errors: tuple[CatalogError, ...] = ()


@dataclass(frozen=True)
class CatalogSnapshot:
    styles: tuple[ExtensionStyle, ...]
    generation: int
    catalog_hash: str
    fingerprint: CatalogFingerprint | None
    diagnostics: CatalogDiagnostics


class ExtensionStyleRegistry:
    """Load catalog changes atomically while retaining the last valid snapshot."""

    def __init__(self, catalog_path: str | Path) -> None:
        self._catalog_path = Path(catalog_path)
        self._lock = RLock()
        self._attempted_fingerprint: CatalogFingerprint | object | None = None
        self._snapshot = CatalogSnapshot(
            styles=(),
            generation=0,
            catalog_hash="",
            fingerprint=None,
            diagnostics=CatalogDiagnostics(),
        )

    def snapshot(self) -> CatalogSnapshot:
        return self.reload(force=False)

    def reload(self, *, force: bool = True) -> CatalogSnapshot:
        with self._lock:
            attempted_at = _utc_now()
            try:
                fingerprint = _fingerprint(self._catalog_path)
            except OSError:
                return self._record_failure(
                    attempted_at,
                    code="catalog_unreadable",
                    fingerprint=None,
                    discovered=0,
                )

            if not force and fingerprint == self._attempted_fingerprint:
                return self._snapshot

            self._attempted_fingerprint = fingerprint
            for attempt in range(3):
                try:
                    styles = load_catalog(self._catalog_path)
                except (OSError, UnicodeError, ValueError):
                    current = _safe_fingerprint(self._catalog_path)
                    if current is not None and current != fingerprint and attempt < 2:
                        fingerprint = current
                        self._attempted_fingerprint = current
                        continue
                    return self._record_failure(
                        attempted_at,
                        code="invalid_catalog",
                        fingerprint=fingerprint,
                        discovered=_discovered_count(self._catalog_path),
                    )

                current = _safe_fingerprint(self._catalog_path)
                if current is None:
                    return self._record_failure(
                        attempted_at,
                        code="catalog_unreadable",
                        fingerprint=None,
                        discovered=0,
                    )
                if current != fingerprint:
                    fingerprint = current
                    self._attempted_fingerprint = current
                    if attempt < 2:
                        continue
                    return self._record_failure(
                        attempted_at,
                        code="catalog_changing",
                        fingerprint=current,
                        discovered=len(styles),
                    )
                break

            generation = self._snapshot.generation + 1
            diagnostics = CatalogDiagnostics(
                discovered=len(styles),
                loaded=len(styles),
                failed=0,
                degraded=False,
                last_attempt_at=attempted_at,
                last_success_at=attempted_at,
                attempted_status="success",
                attempted_fingerprint=fingerprint,
                errors=(),
            )
            self._snapshot = CatalogSnapshot(
                styles=styles,
                generation=generation,
                catalog_hash=fingerprint.sha256,
                fingerprint=fingerprint,
                diagnostics=diagnostics,
            )
            return self._snapshot

    def _record_failure(
        self,
        attempted_at: str,
        *,
        code: str,
        fingerprint: CatalogFingerprint | None,
        discovered: int,
    ) -> CatalogSnapshot:
        self._attempted_fingerprint = (
            fingerprint if fingerprint is not None else _UNREADABLE_ATTEMPT
        )
        previous = self._snapshot
        attempted_status = {
            "catalog_unreadable": "unreadable",
            "invalid_catalog": "invalid",
            "catalog_changing": "changing",
        }.get(code, "failed")
        diagnostics = CatalogDiagnostics(
            discovered=discovered,
            loaded=len(previous.styles),
            failed=1,
            degraded=True,
            last_attempt_at=attempted_at,
            last_success_at=previous.diagnostics.last_success_at,
            attempted_status=attempted_status,
            attempted_fingerprint=fingerprint,
            errors=(CatalogError(code=code, file=self._catalog_path.name),),
        )
        self._snapshot = CatalogSnapshot(
            styles=previous.styles,
            generation=previous.generation,
            catalog_hash=previous.catalog_hash,
            fingerprint=previous.fingerprint,
            diagnostics=diagnostics,
        )
        return self._snapshot


def _fingerprint(path: Path) -> CatalogFingerprint:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return CatalogFingerprint(
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        sha256=digest,
    )


def _safe_fingerprint(path: Path) -> CatalogFingerprint | None:
    try:
        return _fingerprint(path)
    except OSError:
        return None


def _discovered_count(path: Path) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return 0
    return len(value) if isinstance(value, list) else 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
