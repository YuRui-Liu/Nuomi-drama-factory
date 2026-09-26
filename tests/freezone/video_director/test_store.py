from pathlib import Path

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
