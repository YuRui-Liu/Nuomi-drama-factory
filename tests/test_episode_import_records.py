from __future__ import annotations

import pytest

from novelvideo.episode_import_records import EpisodeImportRecords


@pytest.mark.asyncio
async def test_import_results_and_stale_stages_survive_new_repository(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore("alice/demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    records = EpisodeImportRecords(sqlite)
    await records.record_result(import_id="task-1", target_revision=4, episodes=[{"episode_number": 2, "status": "overwritten"}])
    await records.mark_stale(episode_number=2, source_revision=3)
    await sqlite.close()

    reopened = SQLiteStore("alice/demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await reopened.initialize()
    again = EpisodeImportRecords(reopened)
    assert (await again.list_results())[0]["episodes"][0]["status"] == "overwritten"
    stale = await again.list_stale(2)
    assert [item["stage"] for item in stale] == ["characters", "scenes", "beats", "media"]
    assert {item["consumed_revision"] for item in stale} == {3}
    await reopened.close()


@pytest.mark.asyncio
async def test_clear_stale_stage_does_not_delete_assets(tmp_path):
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    asset = project / "images" / "ep002" / "keep.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"keep")
    sqlite = SQLiteStore("alice/demo", str(project), str(tmp_path / "state"))
    await sqlite.initialize()
    sources = EpisodeSourceStore(sqlite)
    await sources.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n旧")], expected_revision=0
    )
    await sources.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n新")], expected_revision=1
    )
    records = EpisodeImportRecords(sqlite)
    await records.mark_stale(episode_number=2, source_revision=1)

    assert await records.clear_stale(
        episode_number=2, stage="beats", source_revision=2
    ) is True
    assert asset.read_bytes() == b"keep"
    assert [item["stage"] for item in await records.list_stale(2)] == ["characters", "scenes", "media"]
    await sqlite.close()


@pytest.mark.asyncio
async def test_clearing_last_stale_stage_resets_source_flag(tmp_path):
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore("alice/demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    source_store = EpisodeSourceStore(sqlite)
    await source_store.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n旧")], expected_revision=0
    )
    await source_store.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n新")], expected_revision=1
    )
    records = EpisodeImportRecords(sqlite)
    await records.mark_stale(episode_number=2, source_revision=2)
    for stage in ("characters", "scenes", "beats", "media"):
        assert await records.clear_stale(
            episode_number=2, stage=stage, source_revision=2
        )
    assert (await source_store.list_sources())[0].downstream_stale is False
    await sqlite.close()


@pytest.mark.asyncio
async def test_clear_stale_rejects_a_revision_that_was_not_consumed(tmp_path):
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore("alice/demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    source_store = EpisodeSourceStore(sqlite)
    await source_store.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n旧")], expected_revision=0
    )
    await source_store.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n新")], expected_revision=1
    )
    records = EpisodeImportRecords(sqlite)
    await records.mark_stale(episode_number=2, source_revision=1)

    assert await records.clear_stale(
        episode_number=2, stage="beats", source_revision=1
    ) is False
    assert [item["stage"] for item in await records.list_stale(2)] == [
        "characters",
        "scenes",
        "beats",
        "media",
    ]
    await sqlite.close()
