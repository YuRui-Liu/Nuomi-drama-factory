from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.mark.asyncio
async def test_file_auth_rejects_non_loopback_without_token(monkeypatch) -> None:
    from novelvideo.ports.auth_contract import AuthError, AuthFailureReason
    from novelvideo.ports.local.auth import FileAuthPort

    monkeypatch.setenv("NOVELVIDEO_API_HOST", "0.0.0.0")
    monkeypatch.delenv("ST_LOCAL_API_TOKEN", raising=False)

    with pytest.raises(AuthError) as exc:
        await FileAuthPort().verify_session(None)

    assert exc.value.reason == AuthFailureReason.MISSING


@pytest.mark.asyncio
async def test_file_auth_keeps_loopback_default_compatible(monkeypatch) -> None:
    from novelvideo.ports.local.auth import FileAuthPort

    monkeypatch.setenv("NOVELVIDEO_API_HOST", "127.0.0.1")
    monkeypatch.delenv("ST_LOCAL_API_TOKEN", raising=False)

    assert (await FileAuthPort().verify_session(None))["role"] == "owner"


@pytest.mark.asyncio
async def test_file_auth_requires_configured_token(monkeypatch) -> None:
    from novelvideo.ports.auth_contract import AuthError
    from novelvideo.ports.local.auth import FileAuthPort

    monkeypatch.setenv("ST_LOCAL_API_TOKEN", "correct-horse")
    monkeypatch.setenv("NOVELVIDEO_API_HOST", "127.0.0.1")

    with pytest.raises(AuthError):
        await FileAuthPort().verify_session("wrong")
    assert (await FileAuthPort().verify_session("correct-horse"))["role"] == "owner"


@pytest.mark.parametrize("value", ["", " ", ".", "..", "/tmp/x", "../x", "a/b", r"a\b", "a\0b"])
def test_asset_path_segment_rejects_path_semantics(value: str) -> None:
    from novelvideo.utils.safe_paths import validate_path_segment

    with pytest.raises(ValueError, match="invalid asset name"):
        validate_path_segment(value)


def test_resolve_under_root_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    from novelvideo.utils.safe_paths import resolve_under_root

    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside allowed storage"):
        resolve_under_root(root, "../outside/secret.png")
    with pytest.raises(ValueError, match="outside allowed storage"):
        resolve_under_root(root, "link/secret.png")


def test_canonical_scene_and_prop_paths_reject_unsafe_names(tmp_path: Path) -> None:
    from novelvideo.utils.path_resolver import (
        canonical_prop_reference_path,
        canonical_scene_master_path,
    )

    with pytest.raises(ValueError, match="invalid asset name"):
        canonical_scene_master_path(tmp_path, "/tmp/escape")
    with pytest.raises(ValueError, match="invalid asset name"):
        canonical_prop_reference_path(tmp_path, "../escape")

    assert canonical_scene_master_path(tmp_path, "合法场景") == (
        tmp_path / "assets" / "scenes" / "合法场景" / "master.png"
    ).resolve()


def test_scene_and_prop_schemas_reject_unsafe_names() -> None:
    from novelvideo.api.schemas import PropCreate, PropUpdate, SceneCreate, SceneUpdate

    for model, payload in (
        (SceneCreate, {"name": "../scene"}),
        (SceneUpdate, {"name": "/tmp/scene"}),
        (PropCreate, {"name": r"bad\prop"}),
        (PropUpdate, {"name": ".."}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_seedance2_request_config_rejects_path_injection() -> None:
    from novelvideo.api.routes.generation import _merge_seedance2_request_config

    with pytest.raises(ValueError, match="does not accept local media paths"):
        _merge_seedance2_request_config(
            {},
            seedance2_config_json='{"reference_image_paths":["/etc/passwd"]}',
        )


def test_seedance2_user_reference_must_be_under_project(tmp_path: Path) -> None:
    from novelvideo.seedance2_i2v.assets import append_seedance2_user_reference_assets

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")

    with pytest.raises(ValueError, match="outside allowed storage"):
        append_seedance2_user_reference_assets(
            [],
            reference_image_paths=[str(outside)],
            reference_audio_paths=[],
            allowed_roots=[project],
        )


@pytest.mark.asyncio
async def test_seedance2_crop_rejects_external_source(tmp_path: Path) -> None:
    from novelvideo.seedance2_i2v.panel_service import crop_seedance2_asset_to_reference

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")

    with pytest.raises(ValueError, match="outside allowed storage"):
        await crop_seedance2_asset_to_reference(
            store=object(),
            episode=1,
            beat={"beat_number": 1},
            project_dir=project,
            asset_key="external",
            source_path=outside,
            crop_data={"width": 1, "height": 1},
        )


@pytest.mark.asyncio
async def test_video_relay_allowed_roots_block_external_file(tmp_path: Path) -> None:
    from novelvideo.generators.video_generator import NewApiVideoGenerator

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")

    with pytest.raises(ValueError, match="outside allowed storage"):
        await NewApiVideoGenerator._relay_media_input(
            str(outside),
            allowed_roots=[project],
        )


@pytest.mark.asyncio
async def test_newapi_generation_applies_project_root_to_local_relay(
    tmp_path: Path,
) -> None:
    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator,
        VideoGenStatus,
    )

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"secret")
    generator = NewApiVideoGenerator(
        api_key="test-key",
        endpoint="https://newapi.example",
        model="seedance-1.0-pro-fast",
    )

    result = await generator.generate(
        image_path=str(outside),
        prompt="camera pushes in",
        output_path=str(project / "out.mp4"),
        project_output_dir=str(project),
    )

    assert result.status is VideoGenStatus.FAILED
    assert "outside allowed storage" in (result.error or "")


def test_cli_api_defaults_to_loopback(monkeypatch) -> None:
    from novelvideo import cli

    captured = {}
    monkeypatch.delenv("NOVELVIDEO_API_HOST", raising=False)
    monkeypatch.delenv("NOVELVIDEO_PUBLIC_HOST", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: captured.update(kwargs))

    cli.api(port=8780, host=None, reload=False)

    assert captured["host"] == "127.0.0.1"


def test_cli_public_host_tracks_explicit_public_bind(monkeypatch) -> None:
    from novelvideo import cli

    captured = {}
    monkeypatch.setenv("NOVELVIDEO_PUBLIC_HOST", "127.0.0.1")
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: captured.update(kwargs))

    cli.api(port=8780, host="0.0.0.0", reload=False)

    assert captured["host"] == "0.0.0.0"
    assert __import__("os").environ["NOVELVIDEO_PUBLIC_HOST"] == "0.0.0.0"


def test_seedance_auto_identity_rejects_path_shaped_detected_id(tmp_path: Path) -> None:
    from novelvideo.seedance2_i2v.assets import _identity_asset_path

    with pytest.raises(ValueError, match="invalid character identity"):
        _identity_asset_path(tmp_path, "../../../../tmp/victim_secret")


def test_compose_host_ports_default_to_loopback() -> None:
    repository = Path(__file__).resolve().parents[1]
    for filename in (
        "docker-compose.yml",
        "docker-compose.release.yml",
        "docker-compose.selfhosted.yml",
        "docker-compose.selfhosted.release.yml",
    ):
        text = (repository / filename).read_text(encoding="utf-8")
        published_ports = [line.strip() for line in text.splitlines() if line.strip().startswith('- "')]
        assert published_ports
        assert all(line.startswith('- "${ST_BIND_HOST:-127.0.0.1}:') for line in published_ports)
