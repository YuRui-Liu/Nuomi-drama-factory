"""Thread-safe, last-known-good extension style catalog registry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .schema import ExtensionStyle, load_catalog


@dataclass(frozen=True)
class CatalogFingerprint:
    mtime_ns: int
    size: int
    sha256: str


@dataclass(frozen=True)
class CatalogDiagnostics:
    discovered: int = 0
    loaded: int = 0
    failed: int = 0
    degraded: bool = False
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    errors: tuple[dict[str, str], ...] = ()


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
        self._attempted_fingerprint: CatalogFingerprint | None = None
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
                )

            if not force and fingerprint == self._attempted_fingerprint:
                return self._snapshot

            self._attempted_fingerprint = fingerprint
            try:
                styles = load_catalog(self._catalog_path)
            except (OSError, UnicodeError, ValueError):
                return self._record_failure(
                    attempted_at,
                    code="invalid_catalog",
                    fingerprint=fingerprint,
                )

            generation = self._snapshot.generation + 1
            diagnostics = CatalogDiagnostics(
                discovered=len(styles),
                loaded=len(styles),
                failed=0,
                degraded=False,
                last_attempt_at=attempted_at,
                last_success_at=attempted_at,
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
    ) -> CatalogSnapshot:
        if fingerprint is not None:
            self._attempted_fingerprint = fingerprint
        previous = self._snapshot
        diagnostics = CatalogDiagnostics(
            discovered=0,
            loaded=len(previous.styles),
            failed=1,
            degraded=True,
            last_attempt_at=attempted_at,
            last_success_at=previous.diagnostics.last_success_at,
            errors=({"code": code, "file": self._catalog_path.name},),
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
