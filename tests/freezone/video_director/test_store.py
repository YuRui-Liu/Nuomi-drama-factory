from pathlib import Path

import pytest

from novelvideo.freezone.video_director.models import DirectorDraft, DirectorImage, DirectorSegment
from novelvideo.freezone.video_director.store import DirectorAttemptStore


def _draft(url: str) -> DirectorDraft:
    return DirectorDraft(revision=3, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="s1", prompt="Walk", duration_seconds=5,
                        first_frame=DirectorImage(image_id="first", url=url)),
    ))


def test_request_key_is_durable_and_preserves_first_snapshot(tmp_path: Path):
    store = DirectorAttemptStore(tmp_path)
    first, created = store.create("project", "canvas", "node", "click", _draft("freezone/a.png"))
    assert created
    again, created = DirectorAttemptStore(tmp_path).create(
        "project", "canvas", "node", "click", _draft("freezone/b.png"))
    assert not created
    assert again["id"] == first["id"]
    assert again["snapshot"]["segments"][0]["first_frame"]["url"] == "freezone/a.png"
    assert [row["id"] for row in store.list("project", "canvas", "node")] == [first["id"]]


def test_claim_and_retry_link_are_atomic(tmp_path: Path):
    store = DirectorAttemptStore(tmp_path)
    attempt, _ = store.create("p", "c", "n", "r", _draft("freezone/a.png"))
    assert store.claim(attempt["id"], "created", "optimizing")
    assert not DirectorAttemptStore(tmp_path).claim(attempt["id"], "created", "optimizing")
    store.update(attempt["id"], stage="failed", failed_stage="generating")
    retry, created = store.retry(attempt["id"])
    assert created and retry["parent_attempt_id"] == attempt["id"]
    assert store.retry(attempt["id"])[0]["id"] == retry["id"]


def test_detail_cannot_shadow_immutable_columns(tmp_path: Path):
    store = DirectorAttemptStore(tmp_path)
    original, _ = store.create("p", "c", "n", "r", _draft("freezone/a.png"))
    for field, value in (
        ("snapshot", {"revision": 999}), ("id", "forged"),
        ("project_id", "other"), ("created_at", "yesterday"),
        ("request_id", "other"), ("parent_attempt_id", "forged"),
    ):
        with pytest.raises(ValueError, match="immutable"):
            store.update(original["id"], **{field: value})
    with pytest.raises(ValueError, match="immutable"):
        store.create("p", "c", "n", "different", _draft("freezone/a.png"),
                     detail={"snapshot": {"revision": 999}})
    assert store.get(original["id"])["snapshot"] == original["snapshot"]
    assert store.list("p", "c", "n")[0]["id"] == original["id"]
    detached = store.get(original["id"])
    detached["snapshot"]["revision"] = 999
    detached["id"] = "forged"
    assert store.get(original["id"])["snapshot"]["revision"] == 3
    assert store.get(original["id"])["id"] == original["id"]


def test_retry_clones_frozen_techniques_and_cannot_mutate_them(tmp_path: Path):
    store = DirectorAttemptStore(tmp_path)
    frozen = {"s1": {"card": {"id": "fixed-reaction", "sources": [{"url": "https://example.test"}]},
                     "projection": {"content_hash": "old"}}}
    parent, _ = store.create("p", "c", "n", "r", _draft("freezone/a.png"),
                             detail={"frozen_techniques": frozen})
    with pytest.raises(ValueError, match="immutable"):
        store.update(parent["id"], frozen_techniques={})
    child, _ = store.retry(parent["id"])
    assert child["frozen_techniques"] == frozen
