from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.character_visual.identity_sheet import IdentitySheetQualityReport
from novelvideo.production_workflow import ProductionWorkflowStore


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "output"
    state = tmp_path / "state"
    image = root / "assets/characters/林舟/identities/青年/versions/v1.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (90, 60), "gray").save(image)
    store = ProductionWorkflowStore(state / "production_workflow.json")
    store.register_candidate_version(
        slot_id="character:林舟:state:young", asset_kind="character_state",
        version_id="v1", asset_path=image.relative_to(root).as_posix(),
        source_attempt_id=None, qc_passed=False,
        generation_metadata={"quality_report": {"passed": False, "issues": ["qc_unavailable"]},
                             "canonical_path": "assets/characters/林舟/identities/青年.png"},
        actor="test", at=datetime.now(timezone.utc),
    )
    return SimpleNamespace(output_dir=root, state_dir=state), image


def prepare(ctx):
    from novelvideo.character_visual.recheck import prepare_recheck
    return prepare_recheck(ctx, character_name="林舟", identity_id="young", version_id="v1")


def report(passed=True):
    return IdentitySheetQualityReport(
        passed=passed, checks={"body_cropped": not passed},
        issues=[] if passed else ["body_cropped"],
        blocking_issues=[] if passed else ["body_cropped"],
        style_family="3d_realistic",
    )


def publish(ctx, target, passed=True):
    from novelvideo.character_visual.recheck import publish_recheck
    return publish_recheck(ctx, target=target, report=report(passed),
                           fingerprint="policy", route={"runtime": "codex"}, actor="test")


def test_recheck_preserves_image_and_promotes_first_pass(sample):
    ctx, image = sample
    before = image.read_bytes()
    result = publish(ctx, prepare(ctx))
    assert result["qc_passed"] is True
    assert result["adoption_status"] == "provisional"
    assert image.read_bytes() == before
    assert (ctx.output_dir / "assets/characters/林舟/identities/青年.png").read_bytes() == before
    store = ProductionWorkflowStore(ctx.state_dir / "production_workflow.json")
    slot, versions = store.get_slot("character:林舟:state:young")
    assert slot.current_version_id == "v1"
    metadata = versions["v1"].generation_metadata
    assert metadata["qc_history"][0]["report"]["passed"] is False
    assert metadata["qc_history"][-1]["fingerprint"] == "policy"


def test_failed_recheck_stays_candidate(sample):
    ctx, _ = sample
    result = publish(ctx, prepare(ctx), False)
    assert result["adoption_status"] == "candidate"
    assert not (ctx.output_dir / "assets/characters/林舟/identities/青年.png").exists()


def test_partial_staging_write_is_cleaned(sample, monkeypatch):
    from pathlib import Path
    ctx, image = sample
    original = Path.write_bytes
    def partial_write(path, data):
        if ".qc-" in path.name:
            original(path, data[:4])
            raise OSError("disk full")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", partial_write)
    with pytest.raises(OSError, match="disk full"):
        publish(ctx, prepare(ctx))
    assert not list(image.parent.parent.parent.glob(".*.qc-*.tmp"))


def test_changed_image_rejects_publication(sample):
    ctx, image = sample
    target = prepare(ctx)
    Image.new("RGB", (90, 60), "blue").save(image)
    with pytest.raises(ValueError, match="changed"):
        publish(ctx, target)


def test_changed_version_rejects_publication(sample):
    ctx, _ = sample
    target = prepare(ctx)
    publish(ctx, target)
    with pytest.raises(ValueError, match="changed"):
        publish(ctx, target)


def test_wrong_identity_cannot_recheck(sample):
    from novelvideo.character_visual.recheck import prepare_recheck
    ctx, _ = sample
    with pytest.raises((KeyError, ValueError)):
        prepare_recheck(ctx, character_name="林舟", identity_id="other", version_id="v1")


def test_symlink_image_rejected(sample):
    ctx, image = sample
    source = image.with_name("source.png")
    image.rename(source)
    image.symlink_to(source)
    with pytest.raises(ValueError):
        prepare(ctx)


def test_other_current_not_replaced(sample):
    ctx, _ = sample
    store = ProductionWorkflowStore(ctx.state_dir / "production_workflow.json")
    store.register_candidate_version(
        slot_id="character:林舟:state:young", asset_kind="character_state", version_id="other",
        asset_path="other.png", source_attempt_id=None, qc_passed=True,
        generation_metadata={}, actor="test", at=datetime.now(timezone.utc),
    )
    result = publish(ctx, prepare(ctx))
    assert result["adoption_status"] == "candidate"
    slot, _ = ProductionWorkflowStore(ctx.state_dir / "production_workflow.json").get_slot("character:林舟:state:young")
    assert slot.current_version_id == "other"


def test_canonical_escape_rejected(sample):
    ctx, _ = sample
    import json
    path = ctx.state_dir / "production_workflow.json"
    data = json.loads(path.read_text())
    data["versions"][0]["generation_metadata"]["canonical_path"] = "../../escape.png"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        prepare(ctx)


def test_canonical_replace_failure_rolls_back_report(sample, monkeypatch):
    from novelvideo.character_visual import recheck
    ctx, _ = sample
    path = ctx.state_dir / "production_workflow.json"
    before = path.read_bytes()
    target = prepare(ctx)
    replace = recheck.os.replace
    def fail_canonical(source, destination):
        if str(destination).endswith("青年.png"):
            raise OSError("disk error")
        return replace(source, destination)
    monkeypatch.setattr(recheck.os, "replace", fail_canonical)
    with pytest.raises(OSError):
        publish(ctx, target)
    assert path.read_bytes() == before


def test_cache_reuses_only_same_image_and_policy(sample):
    from novelvideo.character_visual.recheck import recheck_cache
    ctx, image = sample
    target = prepare(ctx)
    publish(ctx, target)
    assert recheck_cache(ctx, prepare(ctx), "policy")["qc_passed"] is True
    assert recheck_cache(ctx, prepare(ctx), "different") is None
    Image.new("RGB", (90, 60), "red").save(image)
    assert recheck_cache(ctx, prepare(ctx), "policy") is None
