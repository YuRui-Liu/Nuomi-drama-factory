import pytest

from novelvideo.media_capabilities.runtime.credential_store import (
    CredentialStoreError,
    MacOSCredentialStore,
    WindowsCredentialStore,
    create_credential_store,
)


class FakeMacOSKeychainBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.calls: list[tuple[str, str, str, str | None]] = []

    def set(self, service: str, account: str, value: str) -> None:
        self.calls.append(("set", service, account, value))
        self.values[service] = value

    def get(self, service: str, account: str) -> str | None:
        self.calls.append(("get", service, account, None))
        return self.values.get(service)

    def delete(self, service: str, account: str) -> None:
        self.calls.append(("delete", service, account, None))
        self.values.pop(service, None)


class FakeWin32CredentialBackend:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        self.saved: dict[str, object] | None = None

    def CredWrite(self, credential: dict[str, object], flags: int) -> None:
        assert flags == 0
        self.saved = credential

    def CredRead(self, target: str, credential_type: int, flags: int):
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        if self.saved is None or self.saved["TargetName"] != target:
            raise FileNotFoundError(target)
        return self.saved

    def CredDelete(self, target: str, credential_type: int, flags: int) -> None:
        assert target == "DramaClaw/dramaclaw/media/runninghub-main"
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        self.saved = None


class UnavailableWin32CredentialBackend(FakeWin32CredentialBackend):
    def CredWrite(self, credential: dict[str, object], flags: int) -> None:
        raise OSError(1312, "no logon session")

    def CredRead(self, target: str, credential_type: int, flags: int):
        raise OSError(1312, "no logon session")


class FakeDpapi:
    @staticmethod
    def CryptProtectData(value: bytes, *_args):
        return b"protected:" + value

    @staticmethod
    def CryptUnprotectData(value: bytes, *_args):
        assert value.startswith(b"protected:")
        return "description", value.removeprefix(b"protected:")


def test_windows_credential_manager_round_trip_and_delete() -> None:
    backend = FakeWin32CredentialBackend()
    store = WindowsCredentialStore(backend=backend)
    reference = "dramaclaw/media/runninghub-main"

    store.set(reference, "密钥-rh-key")

    assert backend.saved == {
        "Type": backend.CRED_TYPE_GENERIC,
        "TargetName": "DramaClaw/dramaclaw/media/runninghub-main",
        "UserName": "DramaClaw",
        "CredentialBlob": "密钥-rh-key",
        "Persist": backend.CRED_PERSIST_LOCAL_MACHINE,
    }
    assert store.get(reference) == "密钥-rh-key"
    store.delete(reference)
    assert store.get(reference) is None


def test_dpapi_file_fallback_when_background_session_cannot_use_credwrite(
    tmp_path,
) -> None:
    path = tmp_path / "credentials.json"
    store = WindowsCredentialStore(
        path,
        backend=UnavailableWin32CredentialBackend(),
        dpapi=FakeDpapi(),
    )

    store.set("dramaclaw/media/runninghub-main", "rh-secret")

    assert "rh-secret" not in path.read_text(encoding="utf-8")
    assert store.get("dramaclaw/media/runninghub-main") == "rh-secret"
    store.delete("dramaclaw/media/runninghub-main")
    assert store.get("dramaclaw/media/runninghub-main") is None


def test_macos_keychain_round_trip_uses_native_backend() -> None:
    backend = FakeMacOSKeychainBackend()
    store = MacOSCredentialStore(backend=backend)
    reference = "dramaclaw/media/runninghub-main"

    store.set(reference, "rh-secret")

    assert store.get(reference) == "rh-secret"
    assert backend.calls[0] == (
        "set",
        "DramaClaw/dramaclaw/media/runninghub-main",
        "DramaClaw",
        "rh-secret",
    )
    store.delete(reference)
    assert store.get(reference) is None


def test_credential_store_factory_selects_macos_keychain() -> None:
    backend = FakeMacOSKeychainBackend()
    store = create_credential_store(platform="darwin", macos_backend=backend)

    assert isinstance(store, MacOSCredentialStore)


def test_macos_keychain_translates_native_backend_failures() -> None:
    class FailingBackend:
        def set(self, *_args) -> None:
            raise OSError("keychain is unavailable")

        def get(self, *_args) -> str | None:
            raise OSError("keychain is unavailable")

        def delete(self, *_args) -> None:
            raise OSError("keychain is unavailable")

    store = MacOSCredentialStore(backend=FailingBackend())

    with pytest.raises(CredentialStoreError, match="credential store unavailable"):
        store.set("dramaclaw/media/runninghub-main", "rh-secret")

    assert store.get("dramaclaw/media/runninghub-main") is None
    store.delete("dramaclaw/media/runninghub-main")


def test_credential_store_factory_preserves_windows_fallback_path(tmp_path) -> None:
    path = tmp_path / "credentials.json"

    store = create_credential_store(path, platform="win32")

    assert isinstance(store, WindowsCredentialStore)
    assert store._path == path
