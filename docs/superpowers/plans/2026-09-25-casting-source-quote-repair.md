# 选角原文引用自愈与事实级降级 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让"角色重新选角"不再因为单条事实的引用偏移漂移而整单失败，同时保证未被原文逐字支持的内容永不进入选角依据。

**架构：** 在 `casting_source.verified_fact` 中把"引用位置类"失败改为先用严格逐字重定位自愈；把 `ground_profile` 里所有事实级失败从抛错改为"排除该条 + 记录可解析警告"；再让下游提案校验豁免新的警告前缀，避免它被当成"可修复分歧"而白烧一次设计调用；最后给前端补一行中文文案。

**技术栈：** Python 3.11 / pydantic v2 / pytest（pytest-asyncio auto 模式）、React + Vitest + MSW。

**规格：** `docs/superpowers/specs/2026-09-25-casting-source-quote-repair-design.md`

---

## 文件结构

| 文件 | 职责 | 变更 |
| --- | --- | --- |
| `src/novelvideo/character_visual/casting_source.py` | 把模型抽取结果对齐到项目原文，并决定每条事实采纳或排除 | 新增 `_POSITION_REASONS`、`_relocate_quote`；改造 `verified_fact`、`ground_profile` |
| `src/novelvideo/character_visual/casting_proposals.py` | 提案校验与拒绝原因归集 | `validate_casting_proposals` 豁免前缀增加 `excluded_source:` |
| `frontend/src/components/assets/character-casting-panel.tsx` | 选角面板文案映射 | 新增 `excluded_source:` 分支 |
| `tests/character_visual/test_casting_source.py` | 引用核验与降级行为 | 新增 8 个用例，改造 6 个用例 |
| `tests/character_visual/test_casting_recast_quality.py` | 重选角质量门禁回归 | 新增 1 个用例 |
| `frontend/src/__tests__/components/assets/character-casting-panel.test.tsx` | 面板文案回归 | 新增 1 个用例 |

不改动：抽取提示词、窗口预算、`casting_adoption.py` 的严格核验、`conflicting:` / `identity_required:` 门禁。

---

### 任务 1：严格逐字重定位让偏移漂移自愈

**文件：**
- 修改：`src/novelvideo/character_visual/casting_source.py:54-56`（常量区）、`:116-148`（`verified_fact`）
- 测试：`tests/character_visual/test_casting_source.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/character_visual/test_casting_source.py` 末尾追加（`SourceSpan` 是必填字段，不能省略）：

```python
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
    verified = m.verified_fact(_fact(), docs, ['甲'], 'h2', strict=True)
    assert verified.source_document == 'episode:0001'
    assert (verified.source_start, verified.source_end) == (0, 5)


def test_relocation_never_accepts_text_absent_from_sources():
    m = source_module()
    docs = {'novel.txt': m.SourceDocument('novel.txt', '甲看见乙。', 'hash')}
    fact = _fact(evidence='甲是老人。', source_start=0, source_end=5)
    assert m.verified_fact(fact, docs, ['甲'], 'hash') is None
    with pytest.raises(ValueError, match='reason=quote_mismatch'):
        m.verified_fact(fact, docs, ['甲'], 'hash', strict=True)
```

同时改造原有的 `test_strict_verification_reports_precise_reason_without_source_text`（`:203-227`）的参数表。它现在的 `document` 与 `offset_range` 两个用例带着**能逐字命中原文的 evidence**（`甲七十岁。`），重定位会合法地把它们救回来，因此必须让这两条的 evidence 在原文中不存在，才能继续验证 reason 上报：

```python
@pytest.mark.parametrize('changes,source,reason', [
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_source.py -q -k "repaired or nearest or relocated or absent"`

预期：4 个用例全部 FAIL。前三个报 `ValueError: source quote, offset, value or character attribution could not be verified; reason=quote_mismatch ...` 或 `reason=document`；第四个已经通过（它是负向保护用例，先确认它当前也通过）。

- [ ] **步骤 3：编写最少实现代码**

在 `src/novelvideo/character_visual/casting_source.py` 的 `_NARRATIVE_CONTEXT_FIELDS` 定义之后插入：

```python
# Failures about where a quote lives, not about what it claims: a verbatim
# re-location in the project sources settles them without weakening the
# value and attribution gates.
_POSITION_REASONS = frozenset({'document', 'offset_range', 'quote_mismatch'})
```

在 `verified_fact` 之前插入：

```python
def _relocate_quote(evidence, documents, claimed_doc, claimed_start):
    """Find a verbatim quote in project sources; model offsets are never trusted."""
    if not evidence:
        return None
    others = sorted((doc for doc in documents.values() if doc is not claimed_doc),
                    key=lambda doc: doc.document_id)
    for doc in ([claimed_doc] if claimed_doc is not None else []) + others:
        positions = [match.start() for match in re.finditer(re.escape(evidence), doc.text)]
        if not positions:
            continue
        anchor = claimed_start if doc is claimed_doc and isinstance(claimed_start, int) else 0
        start = min(positions, key=lambda position: (abs(position - anchor), position))
        return doc, start, start + len(evidence)
    return None
```

把 `verified_fact` 的前半段（原 117-133 行）替换为：

```python
    doc = documents.get(fact.source_document or 'novel.txt')
    start, end = fact.source_start, fact.source_end
    reason = None
    if doc is None:
        reason = 'document'
    elif start is None or end is None or not 0 <= start < end <= len(doc.text):
        reason = 'offset_range'
    elif doc.text[start:end] != fact.evidence:
        reason = 'quote_mismatch'
    if reason in _POSITION_REASONS:
        relocated = _relocate_quote(fact.evidence, documents, doc, start)
        if relocated is not None:
            doc, start, end = relocated
            fact = fact.model_copy(update={'source_document': doc.document_id, 'source_start': start,
                'source_end': end, 'evidence': doc.text[start:end]})
            reason = None
    if reason is None and fact.source_revision not in (None, source_revision, doc.content_hash):
        reason = 'source_revision'
    if reason is None and not evidence_supports(fact):
        # Attribution itself uses evidence_supports; distinguish unsupported
        # values first so the diagnostic identifies the failed prerequisite.
        reason = 'value_support'
    if reason is None and not attributed_clause(fact, names):
        reason = 'attribution'
```

`verified_fact` 的后半段（全子句检查、`strict` 抛错、返回值）保持原样不动。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_source.py -q`

预期：全部 PASS，包括改造后的 `test_strict_verification_reports_precise_reason_without_source_text`（7 个参数用例都要求 `strict=True` 在证据确实无法定位时给出精确 reason）。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/casting_source.py tests/character_visual/test_casting_source.py
git commit -m "fix(casting): relocate drifted source quotes instead of failing the fact"
```

---

### 任务 2：事实级核验失败只排除该条

**文件：**
- 修改：`src/novelvideo/character_visual/casting_source.py:253-269`（`ground_profile` 抽取结果循环）
- 测试：`tests/character_visual/test_casting_source.py`

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/character_visual/test_casting_source.py`：

```python
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
```

同时改造已经存在的 4 个"必须抛错"用例，改成断言"排除 + 警告"。原因是它们当前的断言 `pytest.raises(match='attribution')` 匹配的是异常消息的固定前缀 `...value or character attribution could not be verified...`，对 reason 没有约束力，必须换成对警告码的断言：

```python
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
```

```python
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
```

```python
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
```

```python
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
```

```python
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
```

（`test_unverified_dialogue_is_excluded_without_discarding_verified_facts`、`test_missing_source_fails_without_model`、`test_verified_profile_reuse_needs_no_model` 三个用例不改，它们已经覆盖"未核验叙事被排除但已核验事实保留""缺原文仍报错""已核验档案复用不需要模型"。）

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_source.py -q`

预期：新增的 4 个用例 FAIL（抛 `ValueError: ...reason=quote_mismatch...`、`...reason=document...`、`wrong identity in extracted source facts`、`fact outside supplied source windows`），改造的 4 个用例 FAIL（`DID NOT RAISE`），其余 PASS。

- [ ] **步骤 3：编写最少实现代码**

把 `ground_profile` 中"窗口 → 抽取 → 逐条核验"这部分（原 233-269 行，即 `warnings = []` 到循环结束）替换为：

```python
        warnings = []
        windows = []
        for doc in documents.values():
            for match in re.finditer('|'.join(map(re.escape, names)), doc.text):
                start, end = max(0, match.start() - 300), min(len(doc.text), match.end() + 600)
                if any(w['source_document'] == doc.document_id and w['start'] <= match.start() < w['end'] for w in windows):
                    continue
                windows.append(dict(source_document=doc.document_id, start=start, end=end, text=doc.text[start:end],
                                    content_hash=doc.content_hash, source_revision=doc.revision, filename=doc.filename))
                if len(windows) >= 12:
                    break
            if len(windows) >= 12:
                break
        if not windows:
            raise ValueError('原文中未找到该角色或有证据支持的别名；请检查角色名称')
        if runtime is None:
            raise ValueError('请先配置 knowledge_extraction 文本任务运行时')
        output = await runtime.run_structured(output_type=FactExtraction,
            system_prompt='仅提取指定角色在原文直接陈述的事实，不设计形象，不推断年龄、职业或性格。field 必须使用 schema 中的英文枚举。value 必须逐字截取 evidence 中直接描述该角色的连续词语，不得总结、改写、补全或把动作概括成性格/职业。每条 evidence 必须是逐字原文且含角色名或已核验别名，offset 是文档绝对字符位置。只选角色本人的直接陈述，例如“甲是老人”的 value 是“老人”；“甲看见老人”不能证明甲是老人。不得引用其他人物。不要从身份阶段名称推断事实。无满足条件的证据返回空列表，不必凑数。',
            prompt=json.dumps(dict(character=profile.name, attested_aliases=names[1:], identity_id=identity_id, windows=windows), ensure_ascii=False))
        output = FactExtraction.model_validate(output.model_dump() if isinstance(output, BaseModel) else output)
        for row in output.facts:
            fact = CharacterNarrativeFact(**row.model_dump(), fact_id='source-' + snapshot_digest(row.model_dump())[:24],
                source_revision=source_revision, confidence=1, source_span=SourceSpan(start_line=1, end_line=1))
            outside_window = not any(w['source_document'] == row.source_document
                and w['start'] <= row.source_start < row.source_end <= w['end'] for w in windows)
            reason = 'wrong_identity' if row.identity_id not in (None, identity_id) else None
            if reason is None:
                try:
                    facts.append(verified_fact(fact, documents, names, source_revision, strict=True))
                    continue
                except SourceFactVerificationError as exc:
                    reason = exc.reason
                    if outside_window and reason in _POSITION_REASONS:
                        reason += '/outside_window'
            # A single unverifiable fact only removes that fact: the recast
            # continues with the verified remainder and reports what was dropped.
            warnings.append(('excluded_narrative:' if fact.field in _NARRATIVE_CONTEXT_FIELDS
                else 'excluded_source:') + f'{fact.field}:{reason}')
```

保持循环之前的部分（`if not facts:`、`names = attested_names(...)`）与循环之后的部分（`fields = {...}`、`return CharacterNarrativeProfile(...)`）不动。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_source.py -q`

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/casting_source.py tests/character_visual/test_casting_source.py
git commit -m "fix(casting): exclude unverifiable facts instead of failing the whole recast"
```

---

### 任务 3：下游豁免 excluded_source 警告，不再白烧一次设计调用

**文件：**
- 修改：`src/novelvideo/character_visual/casting_proposals.py:164`
- 测试：`tests/character_visual/test_casting_recast_quality.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/character_visual/test_casting_recast_quality.py` 的 `test_unfixable_source_issue_does_not_waste_a_revision` 之后追加：

```python
@pytest.mark.asyncio
async def test_source_warning_does_not_waste_a_design_revision(tmp_path):
    from novelvideo.character_design_stage import CharacterDesignOutput

    m = _service()
    character = CharacterNarrativeProfile(character_id="甲", name="甲",
        source_warnings=["excluded_source:hair_style:quote_mismatch"])
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id="甲", profile=character))
    prompts: list[str] = []

    async def design(**kwargs):
        prompts.append(kwargs["prompt"])
        return CharacterDesignOutput(design_proposals=valid_proposal_payloads())

    await m.design_and_publish(
        store=store,
        character_id="甲",
        identity_id=None,
        expected_revision=None,
        grounded_profile=character,
        source_revision="source1",
        style="水墨",
        runtime=type("R", (), {"run_structured": staticmethod(design), "snapshot": type("S", (), {"task_role": "knowledge_extraction"})()})(),
        assert_live=lambda: None,
    )

    assert len(prompts) == 1, "a display-only source warning must not trigger a revision"
    assert len(store.get("甲").design_proposals) == 3
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_recast_quality.py -q -k source_warning`

预期：FAIL，报 `ValueError: casting proposals rejected: excluded_source:hair_style:quote_mismatch`。

- [ ] **步骤 3：编写最少实现代码**

把 `src/novelvideo/character_visual/casting_proposals.py:164` 改为：

```python
    issues.extend(x for x in dossier.issues if not x.startswith(("missing:", "excluded_narrative:", "excluded_source:")))
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/character_visual/test_casting_recast_quality.py -q`

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/casting_proposals.py tests/character_visual/test_casting_recast_quality.py
git commit -m "fix(casting): keep excluded-fact warnings out of proposal rejection"
```

---

### 任务 4：前端把新警告码转成中文说明

**文件：**
- 修改：`frontend/src/components/assets/character-casting-panel.tsx:81-87`
- 测试：`frontend/src/__tests__/components/assets/character-casting-panel.test.tsx`

- [ ] **步骤 1：编写失败的测试**

在 `character-casting-panel.test.tsx` 的 `describe("story-grounded casting")` 中，紧跟 `it("explains excluded narrative without disabling a verified draft", ...)` 之后追加：

```tsx
  it("explains excluded appearance evidence without disabling a verified draft", async () => {
    const data = workspace();
    server.use(http.get(base, () => HttpResponse.json({ ok: true, data: {
      ...data, dossier: { ...data.dossier, issues: ["excluded_source:hair_style:quote_mismatch"] },
    } })));
    mount();
    expect(await screen.findByText("部分外观描述未能在原文中逐字核实，已排除出选角依据；其余已核实事实仍保留。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成候选" })).toBeEnabled();
    expect(writes).toEqual([]);
  });
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-casting-panel.test.tsx`

预期：新用例 FAIL，`Unable to find an element with the text: 部分外观描述未能在原文中逐字核实，已排除出选角依据；其余已核实事实仍保留。`

- [ ] **步骤 3：编写最少实现代码**

在 `frontend/src/components/assets/character-casting-panel.tsx` 的 `if (value.startsWith("excluded_narrative:"))` 分支之后插入：

```tsx
  if (value.startsWith("excluded_source:"))
    return "部分外观描述未能在原文中逐字核实，已排除出选角依据；其余已核实事实仍保留。";
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-casting-panel.test.tsx`

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/components/assets/character-casting-panel.tsx frontend/src/__tests__/components/assets/character-casting-panel.test.tsx
git commit -m "fix(web): explain excluded appearance evidence in the casting panel"
```

---

### 任务 5：整体验证

**文件：** 无新增改动

- [ ] **步骤 1：运行选角相关全部后端测试**

运行：`.venv/bin/python -m pytest tests/character_visual -q`

预期：全部 PASS，无 error。

- [ ] **步骤 2：运行后端全量测试**

运行：`.venv/bin/python -m pytest -q`

预期：与改动前同样通过。若出现与本改动无关的既有失败，记录下来并说明，不要顺手修。

- [ ] **步骤 3：运行受影响的前端用例**

运行：`pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-casting-panel.test.tsx`

预期：全部 PASS。

- [ ] **步骤 4：核对规格覆盖**

对照 `docs/superpowers/specs/2026-09-25-casting-source-quote-repair-design.md` 的 6 节测试计划逐条确认：任务 1 覆盖第 1、2、3、7 条；任务 2 覆盖第 4、5、6 条；任务 3 覆盖第 9 条；任务 4 覆盖第 10 条；第 8 条（`test_missing_source_fails_without_model`）由任务 2 的全量运行覆盖。任何一条没有对应用例就地补齐。

- [ ] **步骤 5：报告**

向用户报告：改动文件清单、上述命令的真实输出摘要、以及"这次报错不会再出现"的证据（任务 2 的软字段用例与任务 3 的单次设计调用断言）。
