"""Local OS-backed credential storage for CE provider API keys."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


_FALLBACK_LOCK = threading.RLock()


class CredentialStoreError(RuntimeError):
    """The operating-system credential store could not be used."""


class WindowsCredentialStore:
    """Store secrets in the current Windows user's Credential Manager."""

    _PREFIX = "DramaClaw/"

    def __init__(
        self,
        path: str | Path | None = None,
        backend: Any | None = None,
        dpapi: Any | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        if backend is None:
            if sys.platform != "win32":
                self._backend = None
                self._dpapi = None
                return
            try:
                import win32cred as backend
            except ImportError:
                self._backend = None
            try:
                import win32crypt as dpapi
            except ImportError:
                dpapi = None
        self._backend = backend
        self._dpapi = dpapi

    @classmethod
    def _target(cls, reference: str) -> str:
        normalized = reference.strip().lstrip("/")
        if not normalized:
            raise CredentialStoreError("credential target is invalid")
        return f"{cls._PREFIX}{normalized}"

    def set(self, reference: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise CredentialStoreError("credential value must not be empty")
        if self._backend is not None:
            try:
                self._backend.CredWrite(
                    {
                        "Type": self._backend.CRED_TYPE_GENERIC,
                        "TargetName": self._target(reference),
                        "UserName": "DramaClaw",
                        "CredentialBlob": value.strip(),
                        "Persist": self._backend.CRED_PERSIST_LOCAL_MACHINE,
                    },
                    0,
                )
                self._delete_fallback(reference)
                return
            except Exception:
                pass
        self._set_fallback(reference, value.strip())

    def get(self, reference: str) -> str | None:
        if self._backend is not None:
            try:
                credential = self._backend.CredRead(
                    self._target(reference),
                    self._backend.CRED_TYPE_GENERIC,
                    0,
                )
                value = credential.get("CredentialBlob")
                if isinstance(value, bytes):
                    value = value.decode("utf-16-le").rstrip("\x00")
                if isinstance(value, str) and value.strip():
                    return value
            except Exception:
                pass
        return self._get_fallback(reference)

    def delete(self, reference: str) -> None:
        if self._backend is not None:
            try:
                self._backend.CredDelete(
                    self._target(reference),
                    self._backend.CRED_TYPE_GENERIC,
                    0,
                )
            except Exception:
                pass
        self._delete_fallback(reference)

    def _read_fallbacks(self) -> dict[str, str]:
        if self._path is None or not self._path.is_file():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_fallbacks(self, values: dict[str, str]) -> None:
        if self._path is None:
            raise CredentialStoreError("encrypted credential storage is unavailable")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(
            json.dumps(values, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)

    def _set_fallback(self, reference: str, value: str) -> None:
        if self._dpapi is None:
            raise CredentialStoreError("credential could not be saved")
        try:
            encrypted = self._dpapi.CryptProtectData(
                value.encode("utf-8"),
                "DramaClaw media credential",
                None,
                None,
                None,
                0,
            )
            with _FALLBACK_LOCK:
                values = self._read_fallbacks()
                values[self._target(reference)] = base64.b64encode(encrypted).decode(
                    "ascii"
                )
                self._write_fallbacks(values)
        except Exception as exc:
            raise CredentialStoreError("credential could not be saved") from exc

    def _get_fallback(self, reference: str) -> str | None:
        if self._dpapi is None:
            return None
        with _FALLBACK_LOCK:
            encoded = self._read_fallbacks().get(self._target(reference))
        if not encoded:
            return None
        try:
            encrypted = base64.b64decode(encoded, validate=True)
            plain = self._dpapi.CryptUnprotectData(
                encrypted,
                None,
                None,
                None,
                0,
            )[1]
            value = plain.decode("utf-8")
        except Exception:
            return None
        return value if value.strip() else None

    def _delete_fallback(self, reference: str) -> None:
        if self._path is None or not self._path.is_file():
            return
        with _FALLBACK_LOCK:
            values = self._read_fallbacks()
            if values.pop(self._target(reference), None) is not None:
                self._write_fallbacks(values)


__all__ = ["CredentialStoreError", "WindowsCredentialStore"]
