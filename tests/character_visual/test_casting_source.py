import importlib.util
import pytest
from types import SimpleNamespace
from novelvideo.character_visual.models import CharacterNarrativeProfile


def source_module():
    assert importlib.util.find_spec('novelvideo.character_visual.casting_source'), 'source grounding service missing'
    from novelvideo.character_visual import casting_source
    return casting_source


@pytest.mark.asyncio
async def test_legacy_profile_uses_only_exact_target_windows():
    m = source_module()
    docs = {'novel.txt': m.SourceDocument('novel.txt', '乙是青年。\n甲七十岁。', 'hash')}
    calls = []
    async def extract(**kw):
        calls.append(kw)
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='七十岁',
            evidence='甲七十岁。', source_document='novel.txt', source_start=6, source_end=11)])
    p = CharacterNarrativeProfile(character_id='甲', name='甲', biography='青年', occupation='反派')
    result = await m.ground_profile(p, docs, 'hash', runtime=SimpleNamespace(run_structured=extract))
    assert len(calls) == 1
    assert [(f.field, f.value) for f in result.facts] == [('age_range', '七十岁')]
    assert '青年' not in result.biography
    assert result.facts[0].source_start == 6


@pytest.mark.asyncio
async def test_invalid_quote_or_attribution_is_rejected():
    m = source_module()
    docs = {'novel.txt': m.SourceDocument('novel.txt', '乙是青年。\n甲七十岁。', 'hash')}
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='青年',
            evidence='乙是青年。', source_document='novel.txt', source_start=0, source_end=5)])
    with pytest.raises(ValueError, match='attribution'):
        await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'), docs, 'hash',
            runtime=SimpleNamespace(run_structured=extract))


@pytest.mark.asyncio
async def test_missing_source_fails_without_model():
    m = source_module()
    with pytest.raises(ValueError, match='原文'):
        await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'), {}, '', runtime=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('hash_format', ['legacy', 'episode_store'])
async def test_real_episode_offsets_hash_and_final_source_guard(tmp_path, hash_format):
    m = source_module()
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter
    from novelvideo.story_analysis import source_sha256
    from novelvideo.episode_sources import content_sha256
    store = SQLiteStore('u/p', output_dir=str(tmp_path / 'out'), state_dir=str(tmp_path / 'state'))
    await store.initialize()
    try:
        await store.add_character(NovelCharacter(name='甲'))
        text = '甲七十岁。'
        db = await store._ensure_db()
        await db.execute('INSERT INTO episode_sources(episode_number,title,raw_content,content_hash,source_filename,source_revision,imported_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
            (1, '一', text, content_sha256(text) if hash_format == 'episode_store' else source_sha256(text), 'episode-one.txt', 1, 'now', 'now'))
        await db.commit()
        docs, revision = await m.load_sources(store.project_dir, store)
        assert docs['episode:0001'].text == text
        assert docs['episode:0001'].revision == '1'
        ctx = SimpleNamespace(output_dir=store.project_dir)
        m.assert_live_sources(ctx, store, docs, '甲', None)
        await db.execute('UPDATE episode_sources SET raw_content=? WHERE episode_number=1', ('甲二十岁。',))
        await db.commit()
        with pytest.raises(ValueError, match='原文内容校验失败'):
            await m.load_sources(store.project_dir, store)
        with pytest.raises(ValueError, match='source changed'):
            m.assert_live_sources(ctx, store, docs, '甲', None)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_verified_profile_reuse_needs_no_model():
    m = source_module()
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    fact = CharacterNarrativeFact(fact_id='f1', field='age_range', value='七十岁', evidence='甲七十岁。',
        source_document='novel.txt', source_start=0, source_end=5, source_revision='hash',
        source_span=SourceSpan(start_line=1, end_line=1), confidence=1)
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲', facts=[fact]),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash', runtime=None)
    assert result.facts[0].fact_id == 'f1'


@pytest.mark.asyncio
async def test_other_identity_facts_do_not_suppress_targeted_extraction():
    m = source_module()
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    fact = CharacterNarrativeFact(fact_id='old', field='age_range', value='七十岁', evidence='甲七十岁。',
        source_document='novel.txt', source_start=0, source_end=5, source_revision='hash', identity_id='old',
        source_span=SourceSpan(start_line=1, end_line=1), confidence=1)
    calls = []
    async def extract(**kwargs):
        calls.append(kwargs)
        return m.FactExtraction()
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲', facts=[fact]),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash', identity_id='young',
        runtime=SimpleNamespace(run_structured=extract))
    assert len(calls) == 1
    assert not result.facts


@pytest.mark.asyncio
@pytest.mark.parametrize('quote,accepted', [('甲看着乙。乙七十岁。', False), ('甲看着乙，乙七十岁。', False),
    ('甲看着七十岁的乙。', False), ('甲是七十岁的乙的儿子。', False),
    ('甲已经七十岁。', True), ('甲是一位七十岁的老人。', True)])
async def test_fact_value_must_be_attributed_to_target_clause(quote, accepted):
    m = source_module()
    async def extract(**kwargs):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='七十岁', evidence=quote,
            source_document='novel.txt', source_start=0, source_end=len(quote))])
    call = m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', quote, 'hash')}, 'hash', runtime=SimpleNamespace(run_structured=extract))
    if accepted:
        assert (await call).facts[0].value == '七十岁'
    else:
        with pytest.raises(ValueError, match='attribution'):
            await call


@pytest.mark.asyncio
async def test_normalized_age_alias_rejects_other_person_kinship_complement():
    m = source_module()
    quote = '甲是七十岁的乙的儿子。'
    async def extract(**kwargs):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_group', value='elder', evidence=quote,
            source_document='novel.txt', source_start=0, source_end=len(quote))])
    with pytest.raises(ValueError, match='attribution'):
        await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
            {'novel.txt': m.SourceDocument('novel.txt', quote, 'hash')}, 'hash', runtime=SimpleNamespace(run_structured=extract))


@pytest.mark.asyncio
async def test_truncated_quote_cannot_hide_other_person_kinship():
    m = source_module()
    source = '甲是七十岁的乙的儿子。'
    quote = '甲是七十岁'
    async def extract(**kwargs):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='七十岁', evidence=quote,
            source_document='novel.txt', source_start=0, source_end=len(quote))])
    with pytest.raises(ValueError, match='attribution'):
        await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
            {'novel.txt': m.SourceDocument('novel.txt', source, 'hash')}, 'hash', runtime=SimpleNamespace(run_structured=extract))
