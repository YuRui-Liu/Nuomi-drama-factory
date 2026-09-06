from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    load_reference_upload,
    save_reference_upload,
)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_is_temporary_by_default_and_uses_opaque_id(tmp_path):
    result = save_reference_upload(tmp_path, _png(), "image/png", "portrait.png")

    assert result.temporary is True
    assert result.persisted is False
    assert result.upload_id not in result.image_path
    assert result.image_path.startswith(str(tmp_path / ".runtime" / "reference_uploads"))
    assert (tmp_path / "assets").exists() is False


@pytest.mark.parametrize(
    ("content_type", "filename", "data", "message"),
    [
        ("text/plain", "image.png", b"not image", "MIME"),
        ("image/png", "image.exe", b"not image", "extension"),
        ("image/png", "image.png", b"not image", "valid image"),
    ],
)
def test_upload_rejects_invalid_mime_extension_and_image(
    tmp_path, content_type, filename, data, message
):
    with pytest.raises(InvalidReferenceUpload, match=message):
        save_reference_upload(tmp_path, data, content_type, filename)


def test_upload_rejects_size_before_writing(tmp_path):
    with pytest.raises(InvalidReferenceUpload, match="size"):
        save_reference_upload(
            tmp_path, _png(), "image/png", "portrait.png", max_bytes=4
        )
    assert not (tmp_path / ".runtime").exists()


def test_filename_may_be_omitted_and_upload_can_be_loaded_by_opaque_id(tmp_path):
    result = save_reference_upload(tmp_path, _png(), "image/png")

    assert load_reference_upload(tmp_path, result.upload_id) == result
    assert load_reference_upload(tmp_path / "another-project", result.upload_id) is None


def test_persist_requires_resolver_and_keeps_temporary_binding_on_failure(tmp_path):
    result = save_reference_upload(
        tmp_path,
        _png(),
        "image/png",
        "portrait.png",
        persist=True,
        requirement_id="prop:letter",
        asset_kind="prop",
        target_entity_id="letter",
    )

    assert result.temporary is True
    assert result.persisted is False
    assert result.persistence_warning == "Reference image could not be persisted; using the temporary upload."
    assert result.image_path and io.open(result.image_path, "rb").read()


def test_persist_copies_to_resolved_project_asset(tmp_path):
    target = tmp_path / "assets" / "props" / "letter" / "reference.png"

    def resolver(project_dir, request):
        assert project_dir == tmp_path
        assert request.requirement_id == "prop:letter"
        assert request.asset_kind == "prop"
        assert request.target_entity_id == "letter"
        assert request.upload_id
        assert request.extension == ".png"
        return target

    result = save_reference_upload(
        tmp_path,
        _png(),
        "image/png",
        "portrait.png",
        persist=True,
        requirement_id="prop:letter",
        asset_kind="prop",
        target_entity_id="letter",
        persist_resolver=resolver,
    )

    assert result.persisted is True
    assert result.temporary is False
    assert result.image_path == str(target.resolve())
    assert target.is_file()


def test_persist_rejects_resolver_path_outside_assets_but_keeps_upload(tmp_path):
    result = save_reference_upload(
        tmp_path,
        _png(),
        "image/png",
        "portrait.png",
        persist=True,
        requirement_id="prop:letter",
        asset_kind="prop",
        target_entity_id="letter",
        persist_resolver=lambda *_: tmp_path.parent / "escape.png",
    )

    assert result.temporary is True
    assert result.persisted is False
    assert result.persistence_warning == "Reference image could not be persisted; using the temporary upload."
    assert io.open(result.image_path, "rb").read() == _png()


def test_persist_requires_complete_typed_target_context(tmp_path):
    with pytest.raises(InvalidReferenceUpload, match="target_entity_id"):
        save_reference_upload(
            tmp_path, _png(), "image/png", persist=True,
            requirement_id="prop:letter", asset_kind="prop",
        )
    with pytest.raises(InvalidReferenceUpload, match="base_entity_id"):
        save_reference_upload(
            tmp_path, _png(), "image/png", persist=True,
            requirement_id="scene_variant:hall:rain", asset_kind="scene_variant",
            target_entity_id="hall_rain", variant_id="rain",
        )


def test_persist_rejects_symlink_target_without_touching_external_file(tmp_path):
    external = tmp_path / "external.png"
    external.write_bytes(b"original")
    target = tmp_path / "assets" / "props" / "letter.png"
    target.parent.mkdir(parents=True)
    target.symlink_to(external)

    result = save_reference_upload(
        tmp_path, _png(), "image/png", persist=True,
        requirement_id="prop:letter", asset_kind="prop", target_entity_id="letter",
        persist_resolver=lambda *_: target,
    )

    assert result.temporary is True
    assert external.read_bytes() == b"original"


def test_persist_rejects_intermediate_symlink_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "props").symlink_to(outside, target_is_directory=True)
    target = assets / "props" / "letter.png"

    result = save_reference_upload(
        tmp_path, _png(), "image/png", persist=True,
        requirement_id="prop:letter", asset_kind="prop", target_entity_id="letter",
        persist_resolver=lambda *_: target,
    )

    assert result.temporary is True
    assert not (outside / "letter.png").exists()


def test_atomic_write_rejects_changed_parent_directory_identity(tmp_path, monkeypatch):
    from novelvideo.narrative_groups import reference_uploads

    target = tmp_path / "assets" / "props" / "letter.png"
    monkeypatch.setattr(
        reference_uploads,
        "_same_directory_identity",
        lambda _anchor, parts, _fd: "assets" not in parts,
    )
    result = save_reference_upload(
        tmp_path, _png(), "image/png", persist=True,
        requirement_id="prop:letter", asset_kind="prop", target_entity_id="letter",
        persist_resolver=lambda *_: target,
    )

    assert result.temporary is True
    assert not target.exists()


def test_save_creates_controlled_directories_only_through_dirfd(tmp_path, monkeypatch):
    def reject_path_mkdir(*_args, **_kwargs):
        raise AssertionError("path-based mkdir must not be used")

    monkeypatch.setattr(Path, "mkdir", reject_path_mkdir)

    result = save_reference_upload(tmp_path, _png(), "image/png")

    assert result.temporary is True
    assert io.open(result.image_path, "rb").read() == _png()


def test_persistence_failure_after_replace_restores_existing_target(
    tmp_path, monkeypatch
):
    from novelvideo.narrative_groups import reference_uploads

    target = tmp_path / "assets" / "props" / "letter.png"
    target.parent.mkdir(parents=True)
    original = b"original target"
    target.write_bytes(original)
    monkeypatch.setattr(
        reference_uploads,
        "validate_reference_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fail")),
    )

    result = save_reference_upload(
        tmp_path, _png(), "image/png", persist=True,
        requirement_id="prop:letter", asset_kind="prop", target_entity_id="letter",
        persist_resolver=lambda *_: target,
    )

    assert result.temporary is True
    assert target.read_bytes() == original


def test_load_rejects_tampered_metadata_scope_id_and_persisted_path(tmp_path):
    result = save_reference_upload(tmp_path, _png(), "image/png")
    metadata = tmp_path / ".runtime" / "reference_uploads" / f"{result.upload_id}.json"
    raw = __import__("json").loads(metadata.read_text())
    raw["upload_id"] = "upl_" + "f" * 32
    metadata.write_text(__import__("json").dumps(raw))
    assert load_reference_upload(tmp_path, result.upload_id) is None

    result = save_reference_upload(tmp_path, _png(), "image/png")
    metadata = tmp_path / ".runtime" / "reference_uploads" / f"{result.upload_id}.json"
    raw = __import__("json").loads(metadata.read_text())
    raw["storage_kind"] = "persisted"
    raw["relative_path"] = "../outside.png"
    metadata.write_text(__import__("json").dumps(raw))
    assert load_reference_upload(tmp_path, result.upload_id) is None


def test_persistence_failure_warning_does_not_expose_exception_or_path(tmp_path):
    secret = "/private/customer/secret.png"

    def resolver(*_):
        raise RuntimeError(secret)

    result = save_reference_upload(
        tmp_path, _png(), "image/png", persist=True,
        requirement_id="prop:letter", asset_kind="prop", target_entity_id="letter",
        persist_resolver=resolver,
    )

    assert result.temporary is True
    assert secret not in result.persistence_warning
    assert "RuntimeError" not in result.persistence_warning


def test_upload_root_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / f"outside-{os.getpid()}"
    outside.mkdir(exist_ok=True)
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "reference_uploads").symlink_to(outside)
    with pytest.raises(InvalidReferenceUpload, match="symlink"):
        save_reference_upload(tmp_path, _png(), "image/png")
