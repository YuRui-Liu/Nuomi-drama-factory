import asyncio
import pytest

from novelvideo.models import NovelCharacter
from novelvideo.media_capabilities.tts.voice_recommendation import (
    recommend_character_voice, verified_dialogue,
)


def test_dialogue_requires_explicit_speaker_and_exact_source():
    assert verified_dialogue('小狐', '老王说：“你好。”') is None
    assert verified_dialogue('小狐', '小狐说：“你好。”') == ('你好。', '小狐说：“你好。”')
    assert verified_dialogue('小狐', '小狐：我们走吧。') is None
    assert verified_dialogue('小狐', '老王问小狐：“你好。”') is None
    assert verified_dialogue('小狐', '小狐：你好。老王：再见。') is None


@pytest.mark.parametrize('text', [
    '角色介绍\n小狐：一只不会说话的狐狸。',
    '角色属性\n小狐：年龄幼年，发声方式为非语言。',
    '角色简介\n小狐：“一只不会说话的狐狸。”',
    '小狐："沉默的小狐狸"',
    '小狐说：这段没有引号。',
    '小狐说：“不配对的引号。"',
])
def test_character_description_cannot_establish_dialogue(text):
    assert verified_dialogue('小狐', text) is None


@pytest.mark.asyncio
async def test_description_never_persists_speaking_fact(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    store = SQLiteStore('description-voice', output_dir=str(tmp_path))
    try:
        await store.add_character(NovelCharacter(name='小狐'))
        (tmp_path / 'novel.txt').write_text('角色介绍\n小狐：一只不会说话的狐狸。')
        result = await recommend_character_voice(store, '小狐', tmp_path, runtime=Runtime())
        await store.load_graph_state()
        assert result['audition_source'] == 'example'
        assert result['voice_facts']['vocalization_mode'] == 'unknown'
        assert store.get_character('小狐').voice_facts.vocalization_mode == 'unknown'
        assert not store.get_character('小狐').voice_facts.evidence
    finally:
        await store.close()


class Store:
    def __init__(self, path):
        self.db_path = str(path / 'missing.db')
        self.character = NovelCharacter(name='小狐')

    def get_character(self, name):
        return self.character if self.character.name == name else None

    async def load_graph_state(self):
        pass

    async def apply_character_voice_recommendation(self, name, *, expected_digest, voice_facts):
        self.character.voice_facts = voice_facts
        return True


class Runtime:
    def __init__(self):
        self.calls = 0
        self.on_run = None

    async def run_structured(self, **kwargs):
        self.calls += 1
        if self.on_run:
            self.on_run()
        return kwargs['output_type'](voice_description='清晰、轻柔，语速适中。', audition_text='我们一起出发吧。', rationale='表演建议：使用平稳语速。')


@pytest.mark.asyncio
async def test_cache_refresh_sources_and_no_fake_dialogue(tmp_path):
    store, runtime = Store(tmp_path), Runtime()
    async def run(refresh=False):
        return await recommend_character_voice(store, '小狐', tmp_path, runtime=runtime, refresh=refresh)
    first = await run()
    assert first['audition_source'] == 'example'
    assert first['voice_facts']['vocalization_mode'] == 'unknown'
    assert await run() == first
    assert runtime.calls == 1
    await run(True)
    assert runtime.calls == 2
    (tmp_path / 'novel.txt').write_text('小狐说：“别担心，我们会找到出口。”')
    result = await run()
    assert result['audition_source'] == 'dialogue'
    assert result['audition_text'] == '别担心，我们会找到出口。'
    assert result['source_digest'] != first['source_digest']


@pytest.mark.asyncio
async def test_human_override_is_retained(tmp_path):
    store = Store(tmp_path)
    store.character.voice_facts = {'vocalization_mode': 'nonverbal', 'provenance': 'human'}
    (tmp_path / 'novel.txt').write_text('小狐说：“你好。”')
    result = await recommend_character_voice(store, '小狐', tmp_path, runtime=Runtime())
    assert result['voice_facts']['vocalization_mode'] == 'nonverbal'
    assert result['voice_facts']['provenance'] == 'human'


@pytest.mark.asyncio
async def test_stale_profile_and_runtime_failure_are_not_cached(tmp_path):
    store, runtime = Store(tmp_path), Runtime()
    runtime.on_run = lambda: setattr(store.character, 'description', '变化后的设定')
    with pytest.raises(ValueError, match='stale'):
        await recommend_character_voice(store, '小狐', tmp_path, runtime=runtime)
    runtime.on_run = lambda: (_ for _ in ()).throw(RuntimeError('text unavailable'))
    with pytest.raises(RuntimeError, match='text unavailable'):
        await recommend_character_voice(store, '小狐', tmp_path, runtime=runtime)


@pytest.mark.asyncio
async def test_concurrent_requests_share_generation(tmp_path):
    store, runtime = Store(tmp_path), Runtime()
    await asyncio.gather(*(recommend_character_voice(store, '小狐', tmp_path, runtime=runtime) for _ in range(3)))
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_real_store_persists_verified_dialogue_and_unlocks_generation(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request
    store = SQLiteStore('voice-test', output_dir=str(tmp_path))
    try:
        await store.add_character(NovelCharacter(name='小狐'))
        (tmp_path / 'novel.txt').write_text('小狐说：“我们一起去看看。”')
        result = await recommend_character_voice(store, '小狐', tmp_path, runtime=Runtime())
        await store.load_graph_state()
        facts = store.get_character('小狐').voice_facts
        assert facts.vocalization_mode == 'dialogue'
        assert facts.provenance == 'source'
        assert facts.evidence[0].startswith('novel.txt@')
        assert '小狐说：“我们一起去看看。”' in facts.evidence[0]
        assert result['voice_facts'] == facts.model_dump(mode='json')
        assert prepare_character_voice_request(store.get_character('小狐'), slot='default')['audition_text']
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cas_rejects_stale_human_fact_and_changed_original(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.character_voice_facts import VoiceFacts
    from novelvideo.media_capabilities.tts.voice_recommendation import recommendation_snapshot
    from novelvideo.media_capabilities.tts.character_voice import voice_snapshot_digest
    store = SQLiteStore('voice-cas', output_dir=str(tmp_path))
    try:
        await store.add_character(NovelCharacter(name='小狐'))
        novel = tmp_path / 'novel.txt'
        novel.write_text('小狐说：“你好。”')
        snapshot, _ = recommendation_snapshot(store.get_character('小狐'), db_path=store.db_path, project_dir=tmp_path)
        digest = voice_snapshot_digest(snapshot)
        incoming = VoiceFacts(vocalization_mode='dialogue', provenance='source', evidence=[f"novel.txt@{snapshot['novel_sha256']}: 小狐说：“你好。”"])
        forged = incoming.model_copy(update={'evidence': ['novel.txt: 老王说：“你好。”']})
        assert not await store.apply_character_voice_recommendation('小狐', expected_digest=digest, voice_facts=forged)
        novel.write_text('老王说：“你好。”')
        assert not await store.apply_character_voice_recommendation('小狐', expected_digest=digest, voice_facts=incoming)
        novel.write_text('小狐说：“你好。”')
        await store.update_character('小狐', voice_facts={'vocalization_mode': 'nonverbal', 'provenance': 'human'})
        assert not await store.apply_character_voice_recommendation('小狐', expected_digest=digest, voice_facts=incoming)
        assert store.get_character('小狐').voice_facts.provenance == 'human'
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_authoring_context_never_establishes_dialogue(tmp_path, monkeypatch):
    from novelvideo.script_creation import asset_context
    monkeypatch.setattr(asset_context, 'load_asset_authoring_context', lambda *args: [dict(source_document='design', text='小狐说：“你好。”')])
    result = await recommend_character_voice(Store(tmp_path), '小狐', tmp_path, runtime=Runtime())
    assert result['voice_facts']['vocalization_mode'] == 'unknown'
    assert result['audition_source'] == 'example'


@pytest.mark.asyncio
async def test_conflicted_source_facts_are_not_overridden(tmp_path):
    store = Store(tmp_path)
    store.character.voice_facts = {'vocalization_mode': 'unknown', 'conflicts': ['vocalization_mode: dialogue <> nonverbal']}
    (tmp_path / 'novel.txt').write_text('小狐说：“你好。”')
    result = await recommend_character_voice(store, '小狐', tmp_path, runtime=Runtime())
    assert result['voice_facts']['vocalization_mode'] == 'unknown'
    assert result['voice_facts']['conflicts']


@pytest.mark.asyncio
async def test_endpoint_uses_configured_runtime_and_reports_failure(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters
    from novelvideo.text_task_runtime import runtime as runtime_module, settings
    store, runtime = Store(tmp_path), Runtime()
    async def resolve(*args, **kwargs):
        return None, 'user', 'project', tmp_path, str(tmp_path), store
    seen = {}
    def route(**kwargs):
        seen.update(kwargs)
        return 'configured-route'
    def build(route):
        assert route == 'configured-route'
        return runtime
    monkeypatch.setattr(characters, '_resolve_character_project', resolve)
    monkeypatch.setattr(settings, 'resolve_configured_agent_task_route', route)
    monkeypatch.setattr(runtime_module, 'build_text_task_runtime', build)
    result = await characters.character_voice_recommendation('project', '小狐', user={})
    assert result['ok'] is True
    assert set(result['data']) == {'voice_description', 'audition_text', 'audition_source', 'rationale', 'voice_facts', 'source_digest'}
    assert seen['task_role'] == 'script_creation'
    runtime.on_run = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
    result = await characters.character_voice_recommendation('project', '小狐', body=characters.VoiceRecommendationRequest(refresh=True), user={})
    assert result['ok'] is False
    assert 'data' not in result
