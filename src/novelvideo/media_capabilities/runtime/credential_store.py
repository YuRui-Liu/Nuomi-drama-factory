"""Local OS-backed credential storage for CE provider API keys."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


_FALLBACK_LOCK = threading.RLock()


class CredentialStoreError(RuntimeError):
    """The operating-system credential store could not be used."""


class _MacOSSecurityBackend:
    """Minimal ctypes binding for generic-password Keychain operations."""

    _SUCCESS = 0
    _ITEM_NOT_FOUND = -25300
    _SECURITY_FRAMEWORK = (
        "/System/Library/Frameworks/Security.framework/Security"
    )
    _CORE_FOUNDATION_FRAMEWORK = (
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )

    def __init__(self) -> None:
        self._security = ctypes.CDLL(self._SECURITY_FRAMEWORK)
        self._core_foundation = ctypes.CDLL(self._CORE_FOUNDATION_FRAMEWORK)
        self._configure_functions()

    def _configure_functions(self) -> None:
        void_pointer = ctypes.c_void_p
        uint32 = ctypes.c_uint32
        pointer_to_uint32 = ctypes.POINTER(uint32)
        pointer_to_void_pointer = ctypes.POINTER(void_pointer)

        self._security.SecKeychainFindGenericPassword.argtypes = [
            void_pointer,
            uint32,
            ctypes.c_char_p,
            uint32,
            ctypes.c_char_p,
            pointer_to_uint32,
            pointer_to_void_pointer,
            pointer_to_void_pointer,
        ]
        self._security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainAddGenericPassword.argtypes = [
            void_pointer,
            uint32,
            ctypes.c_char_p,
            uint32,
            ctypes.c_char_p,
            uint32,
            void_pointer,
            pointer_to_void_pointer,
        ]
        self._security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainItemModifyAttributesAndData.argtypes = [
            void_pointer,
            void_pointer,
            uint32,
            void_pointer,
        ]
        self._security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self._security.SecKeychainItemDelete.argtypes = [void_pointer]
        self._security.SecKeychainItemDelete.restype = ctypes.c_int32
        self._security.SecKeychainItemFreeContent.argtypes = [
            void_pointer,
            void_pointer,
        ]
        self._security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self._core_foundation.CFRelease.argtypes = [void_pointer]
        self._core_foundation.CFRelease.restype = None

    @staticmethod
    def _bytes(value: str) -> bytes:
        return value.encode("utf-8")

    @staticmethod
    def _password_pointer(value: bytes) -> ctypes.c_void_p:
        return ctypes.cast(ctypes.c_char_p(value), ctypes.c_void_p)

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status != _MacOSSecurityBackend._SUCCESS:
            raise OSError(f"macOS Keychain operation failed with status {status}")

    def set(self, service: str, account: str, value: str) -> None:
        service_bytes = self._bytes(service)
        account_bytes = self._bytes(account)
        value_bytes = self._bytes(value)
        item = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            None,
            None,
            ctypes.byref(item),
        )
        if status == self._ITEM_NOT_FOUND:
            status = self._security.SecKeychainAddGenericPassword(
                None,
                len(service_bytes),
                service_bytes,
                len(account_bytes),
                account_bytes,
                len(value_bytes),
                self._password_pointer(value_bytes),
                None,
            )
            self._raise_for_status(status)
            return
        self._raise_for_status(status)
        try:
            status = self._security.SecKeychainItemModifyAttributesAndData(
                item,
                None,
                len(value_bytes),
                self._password_pointer(value_bytes),
            )
            self._raise_for_status(status)
        finally:
            if item.value:
                self._core_foundation.CFRelease(item)

    def get(self, service: str, account: str) -> str | None:
        service_bytes = self._bytes(service)
        account_bytes = self._bytes(account)
        value_length = ctypes.c_uint32()
        value_pointer = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            ctypes.byref(value_length),
            ctypes.byref(value_pointer),
            ctypes.byref(item),
        )
        if status == self._ITEM_NOT_FOUND:
            return None
        self._raise_for_status(status)
        try:
            return ctypes.string_at(value_pointer, value_length.value).decode("utf-8")
        finally:
            if value_pointer.value:
                self._security.SecKeychainItemFreeContent(None, value_pointer)
            if item.value:
                self._core_foundation.CFRelease(item)

    def delete(self, service: str, account: str) -> None:
        service_bytes = self._bytes(service)
        account_bytes = self._bytes(account)
        item = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            None,
            None,
            ctypes.byref(item),
        )
        if status == self._ITEM_NOT_FOUND:
            return
        self._raise_for_status(status)
        try:
            self._raise_for_status(self._security.SecKeychainItemDelete(item))
        finally:
            if item.value:
                self._core_foundation.CFRelease(item)


class MacOSCredentialStore:
    """Store secrets in the current macOS user's login Keychain."""

    _ACCOUNT = "DramaClaw"
    _PREFIX = "DramaClaw/"

    def __init__(self, backend: Any | None = None) -> None:
        try:
            self._backend = backend or _MacOSSecurityBackend()
        except Exception as exc:
            self._backend = None
            self._backend_error = exc

    @classmethod
    def _service(cls, reference: str) -> str:
        normalized = reference.strip().lstrip("/")
        if not normalized:
            raise CredentialStoreError("credential target is invalid")
        return f"{cls._PREFIX}{normalized}"

    def set(self, reference: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise CredentialStoreError("credential value must not be empty")
        if self._backend is None:
            raise CredentialStoreError("credential store unavailable") from getattr(
                self, "_backend_error", None
            )
        try:
            self._backend.set(self._service(reference), self._ACCOUNT, value.strip())
        except Exception as exc:
            raise CredentialStoreError("credential store unavailable") from exc

    def get(self, reference: str) -> str | None:
        if self._backend is None:
            return None
        try:
            value = self._backend.get(self._service(reference), self._ACCOUNT)
        except Exception:
            return None
        return value if isinstance(value, str) and value.strip() else None

    def delete(self, reference: str) -> None:
        if self._backend is None:
            return
        try:
            self._backend.delete(self._service(reference), self._ACCOUNT)
        except Exception:
            pass


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


def create_credential_store(
    path: str | Path | None = None,
    *,
    platform: str | None = None,
    macos_backend: Any | None = None,
) -> MacOSCredentialStore | WindowsCredentialStore:
    """Create the credential store supported by the current operating system."""
    if (platform or sys.platform) == "darwin":
        return MacOSCredentialStore(backend=macos_backend)
    return WindowsCredentialStore(path)


__all__ = [
    "CredentialStoreError",
    "MacOSCredentialStore",
    "WindowsCredentialStore",
    "create_credential_store",
]
