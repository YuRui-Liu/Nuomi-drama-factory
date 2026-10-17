from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.models import RunningHubWorkflowSettings
from novelvideo.media_capabilities.workflows.migration import migrate_existing


def test_missing_sources_preserve_existing_settings_and_routes(tmp_path):
    store = MediaCapabilityStore(tmp_path / "settings.db")
    settings = RunningHubWorkflowSettings(music_acestep="12345")
    store.save_runninghub_workflows(settings)
    first = migrate_existing(store, {}, tmp_path / "packages")
    second = migrate_existing(store, {}, tmp_path / "packages")
    assert first == second
    assert store.get_runninghub_workflows() == settings
    assert store.list_policies() == []
    assert all(item["status"] == "legacy_unverified" for item in first)
