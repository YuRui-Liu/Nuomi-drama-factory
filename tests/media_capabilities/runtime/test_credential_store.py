from novelvideo.media_capabilities.runtime.credential_store import (
    WindowsCredentialStore,
)


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
