# 选角原文引用自愈与事实级降级设计

日期：2026-09-25

状态：分节设计已获用户确认；本书面规格等待审查，尚未进入实现。

## 1. 背景与问题

用户在"角色重新选角 · 女娃"任务上得到失败结果，任务在 1% 处终止，错误为：

```
source quote, offset, value or character attribution could not be verified;
reason=quote_mismatch; field=behavior; offsets=21101:21118
```

任务消耗了一次 `knowledge_extraction` 抽取调用后整体失败，当前形象未改变，用户必须重新发起选角。用户明确要求：不要因为过分的 QC 让任务根本无法完成并反复消耗 token。

这条报错来自事实级核验，而不是任务级输入问题。`behavior` 属于软叙事字段，即使无法核验也不应终止整单选角。

## 2. 根因分析

### 2.1 根因一：把模型自报的绝对字符偏移当成可信输入

`character_visual/casting_source.py` 的抽取提示要求模型输出 `source_start`/`source_end`（文档绝对字符位置），但 LLM 数不准字符数。`verified_fact`（同文件 116-148 行）只在 `doc.text[start:end] != fact.evidence` 时才判定失败，也就是说：**偏移漂移会直接被当成伪造证据**。

复现（`offsets` 与实际位置不符、evidence 文本确实存在于原文）会走到 `reason=quote_mismatch`，与线上报错一致。

### 2.2 根因二：单条事实失败即整单失败

`ground_profile`（同文件 261-269 行）对抽取结果使用 `strict=True`，只有三个原因可降级为警告：

```python
if fact.field not in _NARRATIVE_CONTEXT_FIELDS or exc.reason not in {
        'value_support', 'attribution', 'full_clause_attribution'}:
    raise
```

`quote_mismatch` 不在集合内，于是 `behavior` 这一条软字段的偏移漂移直接抛出 `SourceFactVerificationError`，冒泡到 `task_backend/runners/character_casting_proposals.py`，整单标记 `CASTING_RECAST_FAILED`。

同文件 255-258 行还有两道同样致命的门禁：`wrong identity in extracted source facts`、`fact outside supplied source windows`。它们同样是"单条事实的模型输出瑕疵"，却终止整单。

### 2.3 根因三：排除事实的警告会被下游当成分歧，白烧一次设计调用

即使前两处修好，只要"排除事实"被记进 `profile.source_warnings`，它仍会被放大成致命失败：

1. `casting_brief.build_casting_dossier`（98 行）执行 `dossier.issues.extend(profile.source_warnings)`；
2. `casting_proposals.validate_casting_proposals`（164 行）只豁免两个前缀：
   `issues.extend(x for x in dossier.issues if not x.startswith(("missing:", "excluded_narrative:")))`；
3. 未被豁免的警告进入 `issues`，`casting_service._design_issues_are_fixable` 判定它"可修复"（它不在 `_SOURCE_LEVEL_ISSUE_CODES` 里），于是**再调用一次设计模型**；
4. 第二次调用当然修不好源问题，最终 `raise ValueError('casting proposals rejected: ...')`。

结果是：先白烧一次设计调用，任务仍然失败。这正是用户抱怨的"过分消耗 token 但任务根本无法完成"。

### 2.4 已经正确的部分

`value_support`（`casting_brief.evidence_supports`）、`attribution` 与 `full_clause_attribution`（`attributed_clause`）三道门禁是真正的反幻觉检查：值必须出现在证据中且未被否定，必须是角色本人的直接谓述，且不能被截断的引用藏住别人的补语。它们**保持不变**。

## 3. 目标与非目标

目标：

1. 模型偏移漂移不再导致事实被误判为伪造：证据文本能在项目原文中逐字定位时，用原文真实片段纠正引用与偏移后采纳。
2. 任何**单条事实**级核验失败都不再终止整单选角：该条排除、其余保留、任务继续。
3. 被排除的事实以警告形式暴露给用户，但不再被下游放大成设计重试或整单拒绝。
4. 如实无法核验的内容永不进入 profile、硬约束或生成提示词。

非目标：

1. 不自动重试抽取（会额外消耗 token）。零事实时按现有行为继续，档案标记 `missing:visual_evidence`（非阻断）。
2. 不修改 `conflicting:` 与 `identity_required:` 这两个仍然阻断的硬门禁：前者表示原文自相矛盾，后者表示事实属于另一个身份阶段，都需要人工澄清。
3. 不引入模糊匹配、归一化匹配或语义蕴含判定：本次只做严格逐字重定位。
4. 不改造抽取提示词与窗口预算，不改动采纳（adoption）路径的严格性。

## 4. 设计

### 4.1 新增严格逐字重定位 `_relocate_quote`

```python
def _relocate_quote(evidence, documents, claimed_doc, claimed_start): ...
```

输入：模型给出的 `evidence` 文本、全部 `SourceDocument`、声称的文档对象（可能为 `None`）与声称的起始偏移（可能为 `None`）。

行为：

1. `evidence` 为空则返回 `None`，不做任何重定位。
2. 候选文档顺序：先声称的文档，再其余文档按 `document_id` 升序（确定性）。声称的文档为 `None`（文档 ID 无法识别）时，全部文档都按 `document_id` 升序处理。
3. 在每个候选文档中逐字搜索 `evidence`（`re.escape` 精确匹配，不做大小写、空白、全半角或标点归一化）。
4. 命中位置的选择：在声称文档内取距 `claimed_start` 最近的一处（距离相同时取更靠前者）；在其他文档内取最早一处。
5. 返回 `(doc, start, start + len(evidence))`；任何文档都没有命中则返回 `None`。

重定位成功的语义：`source_document`、`source_start`、`source_end`、`evidence` 全部改为**原文真实片段**，模型自报的偏移被丢弃。随后 `value_support`、`attribution`、`full_clause_attribution` 照旧在该真实片段上执行——修正偏移后，"截断引用藏住他人补语"这类检查反而比原来更准确。

### 4.2 `verified_fact` 的失败分类与自愈

三类位置类失败（`document`、`offset_range`、`quote_mismatch`）先尝试 `_relocate_quote`：

- 命中：用修正后的位置与文本继续后续核验（含此前跳过的 `source_revision` 检查）。
- 未命中：保留原 `reason`。

`value_support`、`attribution`、`full_clause_attribution`、`source_revision` 的判定语义不变。函数签名、`SourceFactVerificationError` 与 `strict=True` 的抛错行为不变；strict 模式继续用于诊断测试和采纳路径。

### 4.3 `ground_profile` 的失败面收敛

抽取结果逐条处理，**任何事实级问题都只产生警告**。警告码格式固定为 `<prefix>:<field>:<reason>`，其中 `<prefix>` 按字段性质取 `excluded_narrative`（软叙事字段）或 `excluded_source`（其余硬视觉字段）：

| 情形 | 处理 | `<reason>` |
| --- | --- | --- |
| `identity_id` 与本次选角阶段不符 | 丢弃 | `wrong_identity` |
| 位置类失败（文档不存在 / 区间越界 / 文本不符）且重定位不到，且声称区间不在任何已提供窗口内 | 丢弃 | `<reason>/outside_window` |
| 位置类失败且重定位不到，声称区间在窗口内 | 丢弃 | `quote_mismatch` / `offset_range` / `document` |
| 事实的 `source_revision` 与当前原文版本不符 | 丢弃 | `source_revision` |
| 值/归属/全子句核验不通过 | 丢弃 | `value_support` / `attribution` / `full_clause_attribution` |
| 核验通过（含重定位后通过） | 采纳 | 无 |

例：软字段偏移漂移且重定位失败 → `excluded_narrative:behavior:quote_mismatch`；硬字段文本在原文中根本不存在且声称区间越出窗口 → `excluded_source:hair_style:quote_mismatch/outside_window`。

"声称区间在窗口外，但重定位后核验通过"的条目**予以采纳**，不产生警告：窗口是成本与上下文的约束，逐字出现在项目原文中且归属正确才是正确性约束。

`ground_profile` 仍然失败的情形（全部属于任务级输入问题，不是 QC 洁癖）：

1. 没有导入原文；
2. 未配置 `knowledge_extraction` 运行时；
3. 角色名与已核验别名在原文中找不到（`原文中未找到该角色或有证据支持的别名`）；
4. 抽取输出无法通过 `FactExtraction` 结构校验；
5. 流程中原文、角色或草案发生变化（由 `assert_live_sources`、`design_and_publish` 负责）。

### 4.4 下游豁免新警告前缀

`casting_proposals.validate_casting_proposals` 的豁免前缀从 `("missing:", "excluded_narrative:")` 扩展为 `("missing:", "excluded_narrative:", "excluded_source:")`。

语义：这两类警告是"哪些内容被排除"的告知，进入选角档案问题列表供用户查看，但不作为提案拒绝理由，也不触发设计重试。

### 4.5 前端文案

`frontend/src/components/assets/character-casting-panel.tsx` 现有的 `excluded_narrative:` 分支旁增加：

```tsx
if (value.startsWith("excluded_source:"))
  return "部分外观描述未能在原文中逐字核实，已排除出选角依据；其余已核实事实仍保留。";
```

避免把内部警告码直接展示给用户。

## 5. 不变量

1. 未通过核验的事实永不写入 `profile.facts`，永不进入 `hard_constraints` 或生成提示词。
2. 采纳路径 `casting_adoption.py` 对草稿事实继续 `strict=True`：此处失败意味着原文在流程中被改动，属于真实异常。
3. `evidence` 永远是原文真实片段；模型提供的文本只在逐字命中原文后才被采纳，且以原文为准。
4. 警告码是稳定的、可解析的诊断信息，不包含原文或角色隐私内容。

## 6. 测试计划

先写失败测试，再改实现。

`tests/character_visual/test_casting_source.py`：

1. 偏移漂移（文本在别处、声称区间在窗口内）→ 采纳，`source_start`/`source_end` 等于真实位置。
2. 跨文档重定位（声称 `novel.txt`，文本只在 `episode:0001`）→ 采纳，`source_document` 被修正。
3. 声称区间落在窗口外但文本逐字存在 → 采纳。
4. 找不到的 evidence：软字段 → 不抛错，警告 `excluded_narrative:behavior:quote_mismatch`；硬字段 → 不抛错，警告 `excluded_source:hair_style:quote_mismatch`；同一批次里已核验的事实保留。
5. `wrong_identity` 与 `outside_window` 只排除该条。
6. 现有 `pytest.raises(match='attribution')` 用例改为断言"丢弃 + 警告"，并保留"被排除的伪造事实绝不进入 profile"这一不变量。
7. 保留 `test_strict_verification_reports_precise_reason_without_source_text`：`strict=True` 仍要给出精确 reason，且真实不存在的引用仍报 `quote_mismatch`。其中 `document` 与 `offset_range` 两个参数用例必须把 evidence 换成原文中不存在的文本，否则会被新的重定位逻辑合法救回，无法再验证 reason 上报。
8. 保留 `test_missing_source_fails_without_model`。

`tests/character_visual/test_casting_recast_quality.py`：

9. 携带 `excluded_source:hair_style:quote_mismatch` 警告的档案能走完 `design_and_publish`：不抛 `casting proposals rejected`，且设计模型只被调用一次（覆盖根因三）。

`frontend/src/__tests__/components/assets/character-casting-panel.test.tsx`：

10. `excluded_source:` 警告渲染为中文说明文案。

## 7. 影响面

| 文件 | 变更 |
| --- | --- |
| `src/novelvideo/character_visual/casting_source.py` | 新增 `_relocate_quote`；`verified_fact` 位置类失败先自愈；`ground_profile` 事实级失败全部降级为警告 |
| `src/novelvideo/character_visual/casting_proposals.py` | 豁免前缀增加 `excluded_source:` |
| `frontend/src/components/assets/character-casting-panel.tsx` | 新警告码的中文文案 |
| 上述测试文件 | 新增与改造用例 |

兼容性：`verified_fact` 与 `SourceFactVerificationError` 的公开签名不变；`strict=True` 语义不变。低风险。

## 8. 验证方式

```bash
.venv/bin/python -m pytest tests/character_visual -x -q
```

以及前端受影响用例：

```bash
pnpm --dir frontend vitest run src/__tests__/components/assets/character-casting-panel.test.tsx
```
