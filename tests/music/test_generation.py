from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
async def test_unknown_submission_never_submits_twice(tmp_path):
    from novelvideo.music.generation import advance_generation
    from novelvideo.music.store import MusicStore
    store = MusicStore(tmp_path/'store.db')
    store.create_job('p','j','generate',{'workflow':'123','request':{'tags':'piano','seed':'1'},'owner':'alice'})
    class Client:
        calls = 0
        async def submit(self,*args):
            self.calls += 1
            raise TimeoutError('lost response')
    client = Client()
    await advance_generation(store, 'p', 'j', client, tmp_path)
    await advance_generation(store, 'p', 'j', client, tmp_path)
    assert client.calls == 1
    assert store.job('p','j')['status'] == 'submission_unknown'


@pytest.mark.asyncio
async def test_known_remote_id_is_queried_on_resume(tmp_path):
    from novelvideo.music.generation import advance_generation
    from novelvideo.music.store import MusicStore
    store = MusicStore(tmp_path/'store.db')
    store.create_job('p','j','generate',{'workflow':'123','request':{'tags':'piano','seed':'1'},'owner':'alice'})
    store.update_job('p','j',status='running',remoteTaskId='remote')
    class Client:
        async def submit(self,*args): raise AssertionError('must not resubmit')
        async def query(self,task):
            assert task == 'remote'
            return SimpleNamespace(status='running', results=(), usage={})
    await advance_generation(store,'p','j',Client(),tmp_path)
    assert store.job('p','j')['status'] == 'running'


@pytest.mark.asyncio
async def test_completed_candidate_is_not_auto_added_to_library_or_plan(tmp_path):
    from novelvideo.music.generation import advance_generation
    from novelvideo.music.store import MusicStore
    from test_api import wav_bytes
    store = MusicStore(tmp_path/'store.db')
    store.create_job('p','j','generate',{'workflow':'123','request':{'tags':'piano','seed':'1'},'owner':'alice'})
    store.update_job('p','j',status='running',remoteTaskId='remote')
    class Client:
        async def submit(self,*args): raise AssertionError('must not resubmit')
        async def query(self,task):
            return SimpleNamespace(status='completed',results=[SimpleNamespace(node_id='107',output_type='audio',url='https://example.test/music')],usage={})
        async def download(self,url): return wav_bytes()
    result = await advance_generation(store,'p','j',Client(),tmp_path/'media')
    assert result['status'] == 'completed'
    assert store.list_assets('alice') == []
    assert store.plan('p','c','n') is None
    assert store.version(result['candidate']['versionId'],project='p')
