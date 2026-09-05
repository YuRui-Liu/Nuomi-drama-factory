from datetime import datetime, timezone
import json

import pytest

from novelvideo.screenplay_semantics import ScreenplaySemanticRevision, SemanticValidationReport
from novelvideo.screenplay_semantics.store import (
    ScreenplaySemanticActivationConflict,
    ScreenplaySemanticStore,
)
from tests.screenplay_semantics.test_models import beat, scene


def revision(revision_id: str = "sem-1", source_revision: int = 1):
    return ScreenplaySemanticRevision(
        revision_id=revision_id, episode=1, source_revision=source_revision,
        source_hash=f"source-{source_revision}", status="draft", scenes=(scene(),),
        beats=(beat(),), validation_report=SemanticValidationReport(passed=True),
        created_at=datetime.now(timezone.utc),
    )


def test_store_saves_immutable_revision_and_atomic_active_pointer(tmp_path):
    store = ScreenplaySemanticStore(tmp_path)
    saved = store.save(revision())
    active = store.activate(1, saved.revision_id, expected_source_revision=1)

    assert store.load(1, saved.revision_id) == saved
    assert store.load_active(1) == active
    assert active.status == "active"
    assert (tmp_path / "screenplay_semantics" / "ep001" / "active.json").exists()


def test_activation_rejects_stale_source_revision(tmp_path):
    store = ScreenplaySemanticStore(tmp_path)
    store.save(revision(source_revision=2))

    with pytest.raises(ScreenplaySemanticActivationConflict, match="source revision"):
        store.activate(1, "sem-1", expected_source_revision=1)


def test_list_revisions_skips_invalid_historical_revision(tmp_path):
    store = ScreenplaySemanticStore(tmp_path)
    valid = store.save(revision(revision_id="sem-valid"))
    broken = valid.model_dump(mode="json")
    broken["revision_id"] = "sem-broken"
    broken["beats"][0]["source_ranges"] = [
        {"start_line": 13, "end_line": 14},
        {"start_line": 8, "end_line": 12},
    ]
    path = (
        tmp_path
        / "screenplay_semantics"
        / "ep001"
        / "revisions"
        / "sem-broken.json"
    )
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    assert [item.revision_id for item in store.list_revisions(1)] == ["sem-valid"]
    assert store.load(1, "sem-broken") is None
