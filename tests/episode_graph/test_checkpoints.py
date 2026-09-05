import json

from novelvideo.episode_graph.checkpoints import EpisodeGraphCheckpointStore


def test_checkpoint_is_reused_only_for_matching_revision_and_hash(tmp_path):
    store = EpisodeGraphCheckpointStore(tmp_path)
    store.save_success(
        target_revision=2,
        group_key="e2-e6",
        content_hash="abc",
        result={"entities": []},
    )
    assert store.load_success(2, "e2-e6", "abc") == {"entities": []}
    assert store.load_success(3, "e2-e6", "abc") is None
    assert store.load_success(2, "e2-e6", "changed") is None


def test_invalid_checkpoint_is_ignored(tmp_path):
    path = tmp_path / "state" / "episode_graph" / "checkpoints" / "rev_2" / "e2-e6.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    assert EpisodeGraphCheckpointStore(tmp_path).load_success(2, "e2-e6", "abc") is None

    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    assert EpisodeGraphCheckpointStore(tmp_path).load_success(2, "e2-e6", "abc") is None
