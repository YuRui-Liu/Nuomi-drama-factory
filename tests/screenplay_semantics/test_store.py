from datetime import datetime, timezone

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
