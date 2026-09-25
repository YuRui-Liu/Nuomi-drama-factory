import importlib.util
import pytest
from types import SimpleNamespace
from novelvideo.character_visual.models import CharacterNarrativeProfile


def source_module():
    assert importlib.util.find_spec('novelvideo.character_visual.casting_source'), 'source grounding service missing'
    from novelvideo.character_visual import casting_source
    return casting_source


def test_extraction_schema_names_backend_fields_and_requires_literal_values():
    m = source_module()
    from pydantic import ValidationError
    schema = m.ExtractedFact.model_json_schema()['properties']
    assert 'species' in schema['field']['enum']
    assert 'clothing_state' in schema['field']['enum']
    assert '逐字' in schema['value']['description']
    with pytest.raises(ValidationError):
        m.ExtractedFact(field='外貌', value='漂亮', evidence='甲很漂亮。',
            source_document='novel.txt', source_start=0, source_end=5)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['biography', 'behavior'])
async def test_unverified_dialogue_is_excluded_without_discarding_verified_facts(field):
    m = source_module()
    quote = '步知遥：不用。我还记得那天。窗纸破了，我拿抄坏的纸补上。后来一刮风，那张纸就响。'
    text = '步知遥十九岁。\n' + quote
    async def extract(**kw):
        return m.FactExtraction(facts=[
            m.ExtractedFact(field='age_range', value='十九岁', evidence='步知遥十九岁。',
                source_document='novel.txt', source_start=0, source_end=7),
            m.ExtractedFact(field=field, value='我拿抄坏的纸补上', evidence=quote,
                source_document='novel.txt', source_start=8, source_end=len(text)),
        ])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='步知遥', name='步知遥'),
        {'novel.txt': m.SourceDocument('novel.txt', text, 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert [(f.field, f.value) for f in result.facts] == [('age_range', '十九岁')]
    assert result.biography == ''
    assert result.source_warnings == [f'excluded_narrative:{field}:attribution']
    from novelvideo.character_visual.casting_brief import build_casting_dossier
    dossier = build_casting_dossier(result, None, 'hash', 'style')
    assert result.source_warnings[0] in dossier.issues
    assert not dossier.interpretations


@pytest.mark.asyncio
async def test_narrative_exclusion_never_accepts_fabricated_source():
    m = source_module()
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='biography', value='老人',
            evidence='甲是老人。', source_document='novel.txt', source_start=0, source_end=5)])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲看见乙。', 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_narrative:biography:quote_mismatch']


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
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'), docs, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:age_range:attribution']


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
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', quote, 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    if accepted:
        assert result.facts[0].value == '七十岁'
    else:
        assert result.facts == []
        assert result.source_warnings == ['excluded_source:age_range:attribution']


@pytest.mark.asyncio
async def test_normalized_age_alias_rejects_other_person_kinship_complement():
    m = source_module()
    quote = '甲是七十岁的乙的儿子。'
    async def extract(**kwargs):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_group', value='elder', evidence=quote,
            source_document='novel.txt', source_start=0, source_end=len(quote))])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', quote, 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:age_group:attribution']


@pytest.mark.asyncio
async def test_truncated_quote_cannot_hide_other_person_kinship():
    m = source_module()
    quote = '甲是七十岁'
    async def extract(**kwargs):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='七十岁', evidence=quote,
            source_document='novel.txt', source_start=0, source_end=len(quote))])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲是七十岁的乙的儿子。', 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:age_range:full_clause_attribution']


@pytest.mark.parametrize('changes,source,reason', [
    # A repairable claim can no longer be used to observe these reasons: the
    # evidence must be genuinely absent from every source document.
    ({'source_document': 'missing', 'evidence': '甲九十岁。'}, '甲七十岁。', 'document'),
    ({'source_end': 99, 'evidence': '甲九十岁。'}, '甲七十岁。', 'offset_range'),
    ({'evidence': '甲八十岁。'}, '甲七十岁。', 'quote_mismatch'),
    ({'value': '青年'}, '甲七十岁。', 'value_support'),
    ({'source_revision': 'old'}, '甲七十岁。', 'source_revision'),
    ({'evidence': '乙七十岁。'}, '乙七十岁。', 'attribution'),
    ({'evidence': '甲是七十岁'}, '甲是七十岁的乙的儿子。', 'full_clause_attribution'),
])
def test_strict_verification_reports_precise_reason_without_source_text(changes, source, reason):
    m = source_module()
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    fact = CharacterNarrativeFact(fact_id='f', field='age_range', value='七十岁', evidence='甲七十岁。',
        source_document='novel.txt', source_start=0, source_end=5, source_revision='hash',
        source_span=SourceSpan(start_line=1, end_line=1), confidence=1).model_copy(update=changes)
    docs = {'novel.txt': m.SourceDocument('novel.txt', source, 'hash')}
    assert m.verified_fact(fact, docs, ['甲'], 'hash') is None
    with pytest.raises(ValueError) as caught:
        m.verified_fact(fact, docs, ['甲'], 'hash', strict=True)
    message = str(caught.value)
    assert f'reason={reason};' in message
    assert 'field=age_range;' in message
    assert f'offsets={fact.source_start}:{fact.source_end}' in message
    assert fact.evidence not in message
    assert fact.value not in message


def _fact(**changes):
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    fields = dict(fact_id='f', field='age_range', value='七十岁', evidence='甲七十岁。',
        source_document='novel.txt', source_start=0, source_end=5, source_revision='hash', confidence=1,
        source_span=SourceSpan(start_line=1, end_line=1))
    fields.update(changes)
    return CharacterNarrativeFact(**fields)


def test_drifted_offsets_are_repaired_from_verbatim_source():
    m = source_module()
    docs = {'novel.txt': m.SourceDocument('novel.txt', '乙是青年。\n甲七十岁。', 'hash')}
    verified = m.verified_fact(_fact(source_start=1, source_end=6), docs, ['甲'], 'hash', strict=True)
    assert (verified.source_start, verified.source_end) == (6, 11)
    assert verified.evidence == '甲七十岁。'


def test_nearest_verbatim_occurrence_is_chosen():
    m = source_module()
    text = '甲七十岁。\n乙是青年。\n甲七十岁。'
    docs = {'novel.txt': m.SourceDocument('novel.txt', text, 'hash')}
    verified = m.verified_fact(_fact(source_start=11, source_end=16), docs, ['甲'], 'hash', strict=True)
    assert (verified.source_start, verified.source_end) == (12, 17)


def test_quote_found_in_another_document_is_relocated_there():
    m = source_module()
    docs = {'episode:0001': m.SourceDocument('episode:0001', '甲七十岁。', 'h1'),
            'novel.txt': m.SourceDocument('novel.txt', '乙是青年。', 'h2')}
    verified = m.verified_fact(_fact(source_revision='h2'), docs, ['甲'], 'h2', strict=True)
    assert verified.source_document == 'episode:0001'
    assert (verified.source_start, verified.source_end) == (0, 5)


def test_relocation_never_accepts_text_absent_from_sources():
    m = source_module()
    docs = {'novel.txt': m.SourceDocument('novel.txt', '甲看见乙。', 'hash')}
    fact = _fact(evidence='甲是老人。', source_start=0, source_end=5)
    assert m.verified_fact(fact, docs, ['甲'], 'hash') is None
    with pytest.raises(ValueError, match='reason=quote_mismatch'):
        m.verified_fact(fact, docs, ['甲'], 'hash', strict=True)


@pytest.mark.asyncio
async def test_unverifiable_soft_fact_is_excluded_without_failing_the_recast():
    m = source_module()
    async def extract(**kw):
        return m.FactExtraction(facts=[
            m.ExtractedFact(field='age_range', value='七十岁', evidence='甲七十岁。',
                source_document='novel.txt', source_start=0, source_end=5),
            m.ExtractedFact(field='behavior', value='爱笑', evidence='甲最爱笑了。',
                source_document='novel.txt', source_start=0, source_end=5),
        ])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert [(f.field, f.value) for f in result.facts] == [('age_range', '七十岁')]
    assert result.source_warnings == ['excluded_narrative:behavior:quote_mismatch']


@pytest.mark.asyncio
async def test_unverifiable_visual_fact_is_excluded_without_failing_the_recast():
    m = source_module()
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='hair_style', value='齐肩黑发',
            evidence='甲留着齐肩黑发。', source_document='novel.txt', source_start=0, source_end=5)])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:hair_style:quote_mismatch']


@pytest.mark.asyncio
async def test_claim_outside_supplied_windows_is_accepted_when_the_quote_is_verbatim():
    m = source_module()
    text = '甲七十岁。' + '乙' * 700
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='age_range', value='七十岁',
            evidence='甲七十岁。', source_document='novel.txt', source_start=650, source_end=655)])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', text, 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert [(f.field, f.value) for f in result.facts] == [('age_range', '七十岁')]
    assert (result.facts[0].source_start, result.facts[0].source_end) == (0, 5)
    assert result.source_warnings == []


@pytest.mark.asyncio
async def test_claim_outside_supplied_windows_is_dropped_with_a_precise_warning():
    m = source_module()
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='hair_style', value='齐肩黑发',
            evidence='甲留着齐肩黑发。', source_document='novel.txt', source_start=0, source_end=7)])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:hair_style:offset_range/outside_window']


@pytest.mark.asyncio
async def test_fact_for_another_identity_is_dropped_without_failing_the_recast():
    m = source_module()
    async def extract(**kw):
        return m.FactExtraction(facts=[m.ExtractedFact(field='hair_style', value='齐肩黑发',
            evidence='甲留着齐肩黑发。', source_document='novel.txt', source_start=0, source_end=5,
            identity_id='other')])
    result = await m.ground_profile(CharacterNarrativeProfile(character_id='甲', name='甲'),
        {'novel.txt': m.SourceDocument('novel.txt', '甲七十岁。', 'hash')}, 'hash', identity_id='young',
        runtime=SimpleNamespace(run_structured=extract))
    assert result.facts == []
    assert result.source_warnings == ['excluded_source:hair_style:wrong_identity']
