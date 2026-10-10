from types import SimpleNamespace

import pytest


def test_music_workflow_default_and_validation():
    from novelvideo.media_capabilities.models import RunningHubWorkflowSettings
    assert RunningHubWorkflowSettings().music_acestep == '2059090557116440578'
    assert RunningHubWorkflowSettings(music_acestep=' 12345 ').music_acestep == '12345'
    with pytest.raises(ValueError):
        RunningHubWorkflowSettings(music_acestep='not-an-id')


def test_music_workflow_persists(tmp_path):
    from novelvideo.media_capabilities.store import MediaCapabilityStore
    from novelvideo.media_capabilities.models import RunningHubWorkflowSettings
    store = MediaCapabilityStore(tmp_path / 'media.db')
    store.save_runninghub_workflows(RunningHubWorkflowSettings(music_acestep='12345'))
    assert MediaCapabilityStore(tmp_path / 'media.db').get_runninghub_workflows().music_acestep == '12345'


@pytest.mark.asyncio
async def test_music_generation_reads_saved_workflow(monkeypatch):
    from novelvideo.music import generation
    from novelvideo.media_capabilities.models import RunningHubWorkflowSettings
    called = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def workflow_json(self, workflow):
            called.append(workflow)
            return {}
    runtime = SimpleNamespace(workflows=RunningHubWorkflowSettings(music_acestep='12345'), create_client=Client)
    monkeypatch.delenv('NUOMI_MUSIC_WORKFLOW_ID', raising=False)
    monkeypatch.setattr(generation, 'get_media_capability_store', lambda: None)
    monkeypatch.setattr(generation, 'get_media_credential_resolver', lambda: None)
    monkeypatch.setattr(generation, 'load_runninghub_runtime_configuration', lambda *args: runtime)
    monkeypatch.setattr(generation, 'validate_contract', lambda graph: None)
    assert (await generation.generation_configuration())[0] == '12345'
    assert called == ['12345']
