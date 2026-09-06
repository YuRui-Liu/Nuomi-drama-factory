from __future__ import annotations

import io
from dataclasses import asdict

import pytest
from PIL import Image

from novelvideo.narrative_groups.reference_decisions import (
    InvalidReferenceDecisions,
    ResolvedProjectAsset,
    TooManyReferenceImages,
    UnresolvedReferenceRequirements,
    build_reference_snapshot,
)
from novelvideo.narrative_groups.reference_matching import (
    MatchedReferenceRequirement,
    ReferenceMatchPreview,
)
from novelvideo.narrative_groups.reference_uploads import save_reference_upload


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1), "blue").save(path)
    return str(path)


def _requirement(tmp_path, *, id="prop:letter", status="missing_image", required=True):
    return MatchedReferenceRequirement(
        id=id,
        kind="prop",
        entity_id=id.split(":")[-1],
        base_entity_id="",
        variant_id="",
        shot_ids=("shot-1",),
        required=required,
        label=id,
        status=status,
        candidate_asset_ids=(),
        available_actions=("keep", "choose_prop", "upload", "ignore"),
        bindings=(),
    )


def _preview(*requirements):
    return ReferenceMatchPreview(tuple(requirements), (), ())


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(buffer, "PNG")
    return buffer.getvalue()


def test_draft_variant_must_be_explicitly_resolved(tmp_path):
    draft = _requirement(tmp_path, id="scene_variant:hall:rain", status="draft_variant")
    with pytest.raises(UnresolvedReferenceRequirements, match="scene_variant:hall:rain"):
        build_reference_snapshot(_preview(draft), [], project_dir=tmp_path)


def test_missing_requirement_must_be_bound_or_explicitly_ignored(tmp_path):
    missing = _requirement(tmp_path)
    with pytest.raises(UnresolvedReferenceRequirements, match="prop:letter"):
        build_reference_snapshot(_preview(missing), [], project_dir=tmp_path)

    snapshot = build_reference_snapshot(
        _preview(missing),
        [{"requirement_id": "prop:letter", "action": "ignore"}],
        project_dir=tmp_path,
    )
    assert snapshot.ignored_requirement_ids == ("prop:letter",)
    assert snapshot.ignored == ("prop:letter",)
    assert snapshot.schema == "narrative-reference-decision/v1"


def test_decisions_reject_unknown_duplicate_and_client_path(tmp_path):
    preview = _preview(_requirement(tmp_path))
    with pytest.raises(InvalidReferenceDecisions, match="unknown requirement"):
        build_reference_snapshot(
            preview,
            [{"requirement_id": "prop:other", "action": "ignore"}],
            project_dir=tmp_path,
        )
    with pytest.raises(InvalidReferenceDecisions, match="duplicate"):
        build_reference_snapshot(
            preview,
            [
                {"requirement_id": "prop:letter", "action": "ignore"},
                {"requirement_id": "prop:letter", "action": "upload", "upload_id": "x"},
            ],
            project_dir=tmp_path,
        )
    with pytest.raises(InvalidReferenceDecisions, match="path"):
        build_reference_snapshot(
            preview,
            [{"requirement_id": "prop:letter", "action": "ignore", "image_path": "/tmp/x"}],
            project_dir=tmp_path,
        )


def test_project_asset_is_server_resolved_and_project_scoped(tmp_path):
    preview = _preview(_requirement(tmp_path))
    inside = _image(tmp_path / "assets" / "props" / "letter.png")
    snapshot = build_reference_snapshot(
        preview,
        [{"requirement_id": "prop:letter", "action": "choose_prop", "asset_id": "asset-1"}],
        project_dir=tmp_path,
        project_assets={"asset-1": ResolvedProjectAsset("asset-1", inside, "prop", "letter")},
    )
    assert snapshot.images[0].image_path == str((tmp_path / "assets" / "props" / "letter.png").resolve())
    assert snapshot.images[0].resolution == "project_asset"

    outside = _image(tmp_path / "outside.png")
    with pytest.raises(InvalidReferenceDecisions, match="project assets"):
        build_reference_snapshot(
            preview,
            [{"requirement_id": "prop:letter", "action": "choose_prop", "asset_id": "bad"}],
            project_dir=tmp_path,
            project_assets={"bad": ResolvedProjectAsset("bad", outside, "prop", "letter")},
        )


def test_upload_id_is_server_resolved(tmp_path):
    upload = save_reference_upload(tmp_path, _png(), "image/png", "letter.png")
    snapshot = build_reference_snapshot(
        _preview(_requirement(tmp_path)),
        [{"requirement_id": "prop:letter", "action": "upload", "upload_id": upload.upload_id}],
        project_dir=tmp_path,
        uploads={upload.upload_id: upload},
    )
    assert snapshot.images[0].source == "upload"
    assert snapshot.images[0].source_id == upload.upload_id
    assert snapshot.images[0].resolution == "temporary"
    assert asdict(snapshot.images[0])["resolution"] == "temporary"


def test_free_project_asset_and_upload_are_added_to_snapshot(tmp_path):
    asset_path = _image(tmp_path / "assets" / "props" / "letter.png")
    asset = ResolvedProjectAsset("asset-1", asset_path, "prop", "letter")
    upload = save_reference_upload(tmp_path, _png(), "image/png", "mood.png")

    snapshot = build_reference_snapshot(
        _preview(), [], project_dir=tmp_path,
        project_assets={asset.asset_id: asset}, uploads={upload.upload_id: upload},
        additional_asset_ids=[asset.asset_id],
        additional_upload_ids=[upload.upload_id],
    )

    assert [(item.source_id, item.resolution) for item in snapshot.images] == [
        (asset.asset_id, "project_asset"),
        (upload.upload_id, "temporary"),
    ]


def test_snapshot_rejects_more_than_nine_images(tmp_path):
    requirements = tuple(
        _requirement(tmp_path, id=f"prop:{index}") for index in range(10)
    )
    assets = {
        f"asset-{index}": ResolvedProjectAsset(
            f"asset-{index}", _image(tmp_path / "assets" / f"{index}.png"),
            "prop", str(index),
        )
        for index in range(10)
    }
    decisions = [
        {"requirement_id": f"prop:{index}", "action": "choose_prop", "asset_id": f"asset-{index}"}
        for index in range(10)
    ]
    with pytest.raises(TooManyReferenceImages, match="9"):
        build_reference_snapshot(
            _preview(*requirements), decisions, project_dir=tmp_path, project_assets=assets
        )


def test_action_must_be_advertised_and_match_requirement_state(tmp_path):
    missing = _requirement(tmp_path)
    with pytest.raises(InvalidReferenceDecisions, match="available"):
        build_reference_snapshot(
            _preview(missing),
            [{"requirement_id": missing.id, "action": "accept_fallback"}],
            project_dir=tmp_path,
        )


def test_draft_cannot_be_ignored_or_confirmed_without_variant_image(tmp_path):
    base = _image(tmp_path / "assets" / "scenes" / "hall.png")
    binding = __import__(
        "novelvideo.narrative_groups.reference_matching", fromlist=["ReferenceBinding"]
    ).ReferenceBinding("scene_variant:hall:rain", "fallback", "hall", "scene_base", base)
    draft = MatchedReferenceRequirement(
        id="scene_variant:hall:rain", kind="scene_variant", entity_id="hall_rain",
        base_entity_id="hall", variant_id="rain", shot_ids=("s",), required=True,
        label="hall / rain", status="draft_variant", candidate_asset_ids=("hall_rain",),
        available_actions=("confirm_draft", "choose_variant", "use_base", "upload"),
        bindings=(binding,),
    )
    with pytest.raises(InvalidReferenceDecisions, match="ignored"):
        build_reference_snapshot(
            _preview(draft), [{"requirement_id": draft.id, "action": "ignore"}],
            project_dir=tmp_path,
        )
    with pytest.raises(UnresolvedReferenceRequirements):
        build_reference_snapshot(
            _preview(draft), [{"requirement_id": draft.id, "action": "confirm_draft"}],
            project_dir=tmp_path,
        )
    snapshot = build_reference_snapshot(
        _preview(draft), [{"requirement_id": draft.id, "action": "use_base"}],
        project_dir=tmp_path,
    )
    assert snapshot.images[0].source_id == "hall"
    assert snapshot.images[0].resolution == "fallback"


def test_snapshot_revalidates_image_content_after_upload(tmp_path):
    upload = save_reference_upload(tmp_path, _png(), "image/png", "letter.png")
    io.open(upload.image_path, "wb").write(b"replaced executable")
    with pytest.raises(InvalidReferenceDecisions, match="valid image"):
        build_reference_snapshot(
            _preview(_requirement(tmp_path)),
            [{"requirement_id": "prop:letter", "action": "upload", "upload_id": upload.upload_id}],
            project_dir=tmp_path, uploads={upload.upload_id: upload},
        )


def test_resolved_asset_metadata_must_match_requirement(tmp_path):
    inside = _image(tmp_path / "assets" / "characters" / "alice.png")
    with pytest.raises(InvalidReferenceDecisions, match="does not match"):
        build_reference_snapshot(
            _preview(_requirement(tmp_path)),
            [{"requirement_id": "prop:letter", "action": "choose_prop", "asset_id": "alice"}],
            project_dir=tmp_path,
            project_assets={"alice": ResolvedProjectAsset("alice", inside, "character_identity", "alice")},
        )
