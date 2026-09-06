from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.task_backend.runners import narrative_group


def _png(path: Path, color: str = "blue") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), color).save(path, "PNG")
    return str(path.resolve())


def _payload(project_dir: Path, snapshot: dict | None = None) -> dict:
    payload = {
        "project_dir": str(project_dir),
        "stage": "sketch",
        "layout": {"rows": 1, "columns": 1},
        "beats": [{"beat_number": 1, "visual_description": "A letter on a desk"}],
    }
    if snapshot is not None:
        payload["reference_resolution"] = snapshot
    return payload


def _snapshot(*images: dict, ignored: tuple[str, ...] = ()) -> dict:
    return {
        "id": "refsnap_test",
        "schema_version": "narrative-reference-decision/v1",
        "images": list(images),
        "ignored_requirement_ids": list(ignored),
        "warnings": ["snapshot warning"],
        "style_reference": "",
    }


def _image(requirement_id: str, image_path: str, *, resolution: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "source": "upload" if resolution == "temporary" else "project_asset",
        "source_id": f"source-{requirement_id}",
        "asset_kind": "prop",
        "image_path": image_path,
        "resolution": resolution,
    }


@pytest.mark.parametrize("damage", ["missing", "content", "escape"])
def test_reference_snapshot_is_revalidated_before_generation(tmp_path, damage):
    original = Path(_png(tmp_path / "assets" / "props" / "letter.png"))
    image_path = str(original)
    if damage == "missing":
        original.unlink()
    elif damage == "content":
        original.write_bytes(b"not an image")
    else:
        image_path = _png(tmp_path / "outside.png")

    with pytest.raises(narrative_group.ReferenceSnapshotInvalid) as exc_info:
        narrative_group._generation_input(
            _payload(tmp_path, _snapshot(_image("prop:letter", image_path, resolution="project_asset")))
        )

    assert exc_info.value.error_code == "REFERENCE_SNAPSHOT_INVALID"
    assert str(exc_info.value) == "REFERENCE_SNAPSHOT_INVALID"
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_snapshot_fails_before_transport_client_is_created(
    tmp_path, monkeypatch
):
    missing = str((tmp_path / "assets" / "missing.png").resolve())
    runtime = SimpleNamespace(
        model="image-model",
        api_key="secret",
        create_client=lambda: (_ for _ in ()).throw(
            AssertionError("transport client must not be created")
        ),
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration."
        "load_grsai_runtime_configuration",
        lambda *args, **kwargs: runtime,
    )
    payload = _payload(
        tmp_path,
        _snapshot(
            _image("prop:letter", missing, resolution="project_asset")
        ),
    )

    with pytest.raises(narrative_group.ReferenceSnapshotInvalid):
        await narrative_group._generate_grid(
            payload, SimpleNamespace(output_dir=tmp_path)
        )


def test_reference_snapshot_wins_over_legacy_selection(tmp_path, monkeypatch):
    formal = _png(tmp_path / "assets" / "props" / "letter.png")
    monkeypatch.setattr(
        narrative_group,
        "resolve_group_reference_preview",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy reference resolver must not run")
        ),
    )
    payload = _payload(
        tmp_path,
        _snapshot(_image("prop:letter", formal, resolution="project_asset"), ignored=("prop:key",)),
    )
    payload["reference_selection"] = {
        "selected_character_reference_ids": ["stale-selection"]
    }

    value = narrative_group._generation_input(payload)

    assert value.references == (formal,)
    assert value.warnings == ("snapshot warning",)
    assert value.reference_audit == {
        "snapshot_id": "refsnap_test",
        "formal": 1,
        "temporary": 0,
        "fallback": 0,
        "ignored": 1,
    }


def test_reference_snapshot_audit_counts_each_resolution(tmp_path):
    images = (
        _image("prop:formal", _png(tmp_path / "assets" / "formal.png"), resolution="project_asset"),
        _image(
            "prop:temporary",
            _png(tmp_path / ".runtime" / "reference_uploads" / "temporary.png"),
            resolution="temporary",
        ),
        _image("prop:fallback", _png(tmp_path / "assets" / "fallback.png"), resolution="fallback"),
    )

    value = narrative_group._generation_input(
        _payload(tmp_path, _snapshot(*images, ignored=("prop:ignored",)))
    )

    assert value.reference_audit == {
        "snapshot_id": "refsnap_test",
        "formal": 1,
        "temporary": 1,
        "fallback": 1,
        "ignored": 1,
    }


def test_generation_without_snapshot_keeps_legacy_behavior(tmp_path, monkeypatch):
    legacy = _png(tmp_path / "assets" / "legacy.png")
    selection = type("Selection", (), {
        "image_paths": (legacy,), "selected": (), "warnings": (), "style_prompt": "legacy style"
    })()
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: object())
    monkeypatch.setattr(narrative_group, "apply_group_reference_selection", lambda *args, **kwargs: selection)

    value = narrative_group._generation_input(_payload(tmp_path))

    assert value.references == (legacy,)
    assert value.reference_audit == {}
    assert "legacy style" in value.prompt
