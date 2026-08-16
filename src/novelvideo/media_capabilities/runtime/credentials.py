"""Resolve credential references without persisting credential values."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TypeAlias


CredentialReader: TypeAlias = Callable[[str], str | None]


class CredentialResolutionError(RuntimeError):
    """A credential reference could not be resolved safely."""


class CredentialResolver:
    """Resolve supported credential references from injected sources."""

    __slots__ = ("_env", "_keyring_reader", "_secret_reader")

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        keyring_reader: CredentialReader | None = None,
        secret_reader: CredentialReader | None = None,
    ) -> None:
        self._env = os.environ if env is None else env
        self._keyring_reader = keyring_reader
        self._secret_reader = secret_reader

    def __repr__(self) -> str:
        return "CredentialResolver()"

    def resolve(self, reference: str) -> str:
        scheme, separator, target = reference.partition("://")
        if (
            not separator
            or scheme not in {"env", "keyring", "secret"}
            or not target
            or target.isspace()
        ):
            raise CredentialResolutionError("invalid credential reference")

        if scheme == "env":
            try:
                value = self._env.get(target)
            except Exception:
                raise CredentialResolutionError("credential reader failed") from None
        else:
            reader = (
                self._keyring_reader if scheme == "keyring" else self._secret_reader
            )
            if reader is None:
                raise CredentialResolutionError("credential reader is not configured")
            try:
                value = reader(target)
            except Exception:
                raise CredentialResolutionError("credential reader failed") from None

        if not isinstance(value, str) or not value.strip():
            raise CredentialResolutionError("credential is unavailable")
        return value


__all__ = ["CredentialResolutionError", "CredentialResolver"]
