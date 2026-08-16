from __future__ import annotations

import traceback
from collections.abc import Callable

import pytest

from novelvideo.media_capabilities.runtime.credentials import (
    CredentialResolutionError,
    CredentialResolver,
)


def test_env_reference_uses_injected_mapping() -> None:
    resolver = CredentialResolver(env={"RUNNINGHUB_KEY": "memory-only-key"})

    assert resolver.resolve("env://RUNNINGHUB_KEY") == "memory-only-key"


@pytest.mark.parametrize(
    ("reference", "reader_name", "expected_target"),
    [
        ("keyring://dramaclaw/runninghub", "keyring", "dramaclaw/runninghub"),
        ("secret://media/runninghub", "secret", "media/runninghub"),
    ],
)
def test_reader_reference_uses_injected_reader(
    reference: str,
    reader_name: str,
    expected_target: str,
) -> None:
    calls: list[str] = []

    def reader(target: str) -> str:
        calls.append(target)
        return "reader-secret"

    resolver = CredentialResolver(
        env={},
        keyring_reader=reader if reader_name == "keyring" else None,
        secret_reader=reader if reader_name == "secret" else None,
    )

    assert resolver.resolve(reference) == "reader-secret"
    assert calls == [expected_target]


@pytest.mark.parametrize(
    "reference",
    [
        "inline-value",
        "https://example.invalid/key",
        "unknown://key",
        "env://",
        "keyring://",
        "secret://",
    ],
)
def test_invalid_reference_raises_stable_error_without_echo(reference: str) -> None:
    with pytest.raises(CredentialResolutionError) as exc_info:
        CredentialResolver(env={}).resolve(reference)

    assert reference not in str(exc_info.value)


@pytest.mark.parametrize(
    ("resolver", "reference"),
    [
        (CredentialResolver(env={}), "env://MISSING"),
        (CredentialResolver(env={"EMPTY": ""}), "env://EMPTY"),
        (CredentialResolver(env={"BLANK": "   "}), "env://BLANK"),
        (
            CredentialResolver(env={}, keyring_reader=lambda _: None),
            "keyring://missing/key",
        ),
        (
            CredentialResolver(env={}, secret_reader=lambda _: ""),
            "secret://missing/key",
        ),
    ],
)
def test_missing_or_empty_credentials_raise_resolution_error(
    resolver: CredentialResolver,
    reference: str,
) -> None:
    with pytest.raises(CredentialResolutionError) as exc_info:
        resolver.resolve(reference)

    assert str(exc_info.value) == "credential is unavailable"


def test_env_mapping_resolution_error_is_sanitized_without_exception_chain() -> None:
    leaked = "known-env-mapping-secret"

    class FailingEnvironment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            cause = RuntimeError(f"cause={leaked}")
            raise CredentialResolutionError(f"message={leaked}") from cause

    resolver = CredentialResolver(env=FailingEnvironment())

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolver.resolve("env://PROVIDER_KEY")

    assert str(exc_info.value) == "credential reader failed"
    assert exc_info.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert leaked not in formatted


@pytest.mark.parametrize("scheme", ["keyring", "secret"])
def test_reader_exception_is_wrapped_without_leaking_secret(scheme: str) -> None:
    leaked = "known-reader-secret"

    def failing_reader(_: str) -> str:
        raise RuntimeError(f"token={leaked}")

    kwargs: dict[str, Callable[[str], str]] = {f"{scheme}_reader": failing_reader}
    resolver = CredentialResolver(env={}, **kwargs)

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolver.resolve(f"{scheme}://provider/account")

    assert leaked not in str(exc_info.value)
    assert leaked not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert leaked not in formatted


@pytest.mark.parametrize("scheme", ["keyring", "secret"])
def test_reader_resolution_error_is_sanitized_without_exception_chain(
    scheme: str,
) -> None:
    leaked = "known-reader-resolution-secret"

    def failing_reader(_: str) -> str:
        cause = RuntimeError(f"cause={leaked}")
        raise CredentialResolutionError(f"message={leaked}") from cause

    kwargs: dict[str, Callable[[str], str]] = {f"{scheme}_reader": failing_reader}
    resolver = CredentialResolver(env={}, **kwargs)

    with pytest.raises(CredentialResolutionError) as exc_info:
        resolver.resolve(f"{scheme}://provider/account")

    assert str(exc_info.value) == "credential reader failed"
    assert exc_info.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert leaked not in formatted


def test_resolver_repr_does_not_include_environment_values() -> None:
    secret = "known-env-secret"
    resolver = CredentialResolver(env={"RUNNINGHUB_KEY": secret})

    assert secret not in repr(resolver)
