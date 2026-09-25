# H3 视频提示词规划韧性 设计

## 背景

「生成组合视频」（`narrative_group_video`）是分集生产的关键路径，也是这条链路里最贵、最慢、最容易失败的一环。最近连续几次生产任务都以整组失败告终，而且**每次失败的原因都不一样**：

| # | 任务错误 | 根因 | 整组报废 |
|---|---|---|---|
| 1 | `optimizer failure: TimeoutError` | dsh 以「编码 agent」启动，96 步 / 101 次 bash+grep+read，598s 撞 `DSH_EXEC_TIMEOUT_SECONDS=600` | 是 |
| 2 | `optimizer failure: KnowledgeRuntimeError:DSH_OUTPUT_INVALID` | 模型 JSON 合法但违反 `ref2va requires reference_summary`；pydantic 详情被丢弃，重试拿不到可操作信息 | 是 |
| 3 | `optimizer failure: H3PromptQualityError: first_frame_character_mismatch, character_acting_missing` | 15 段 rigid prompt 里三处角色名单不一致（漏了只在画外说话的「收简人」） | 是 |
| 4 | 进度永远 1%「任务已开始」 | runner 一次都没有上报进度 | 否（体感极差） |
| 5 | 每次 6–10 分钟，失败后要人肉读栈 | 失败信息只剩一层 `optimizer failure: ...` | — |

这五个不是五个 bug，而是**同一个结构性模式**的五个切面：

> 一次长约 10 分钟、要花钱的模型调用，输出被一个由 ~37 条**精确相等**启发式规则组成的门禁全量否决；只要命中任意一条，**整组**报废，且失败痕迹只有一层。

用户原话：「不要太严格的 QC，因为 AI 会习惯挑错的」「QC 本身就不一定可靠，严格 QC 对流程影响很大」。

## 目标

1. **出片优先**：任何单点质量瑕疵都不再让整组报废；能出多少出多少，缺的部分可见、可重试。
2. **QC 降级为顾问**：QC 只做「确定性修正」和「提示」，**不再拥有否决权**；能否出片由**编译器与供应商**这个更可靠的信号决定。
3. **失败变快、变准**：不合格在花钱之前判定；错误信息带 `code @ field`；失败只影响单个片段。
4. **提高首次通过率**：把「一次调用产出整集 pack」拆成逐片段调用，让模型每次只守一个片段的规则。
5. **可测量**：记录每条规则的真实触发频次，用数据决定哪条该松、哪条该紧，而不是靠猜。

## 非目标

- 不改变视频供应商、workflow、分辨率、参考图策略或成片编码。
- 不重写 `H3_DIRECTOR_SYSTEM_PROMPT` 的结构（十五段 rigid prompt 协议保持）。
- 不为「参考图锁定」做静默降级：非参考兜底渲染**默认关闭**。
- 不新增工作台之外的 UI；不重做任务中心。
- 不放松「不虚构原文内容」这条底线（幻觉类仍是硬拦）。

## 已确认的现状（代码事实）

### 一次调用产出整集 pack

`src/novelvideo/task_backend/runners/narrative_group_video.py:679` 的 `_optimize_missing_prompts()` 会把「本组片段 + 已完成渲染的兄弟组片段」合并成一个 `H3EpisodeInput`，交给 `create_h3_episode_pack_optimizer().optimize()`，**一次调用**要求模型产出覆盖全部片段的 `H3EpisodePromptPack`。单次输入 ~46KB，输出含每片段十五段 rigid prompt。

后果：**一个片段的疏漏会毒掉整个 pack**。这正是报错写「produced no plan for segment(s) shot-03-01, shot-03-02」的机制——一个片段失败，两个片段的证据都不落盘。

### 门禁是精确相等，且任一命中即整组否决

`src/novelvideo/media_capabilities/video/h3_prompt_quality.py` 里有 **37 个错误码**（`_add(issues, "<code>", ...)`）。`H3PromptQualityReport.raise_for_failure()` 在 `passed=False` 时抛 `H3PromptQualityError`，由 `compile_and_gate_h3_plan()` 直接抛出，一路冒泡成组级失败。

`H3PromptQualityIssue` **已经**带 `code / message / field / severity / location` 五个字段，但 `severity: Literal["error"] = "error"`（`:123`）被钉死——**分档的载体已经存在，只是没用**。

### 失败信息只剩一层

`H3PromptQualityError.__init__`（`:162`）只做 `", ".join(report.codes)`，把 `field` 和 `message` 全丢了。所以运维看到的只有两个光秃秃的码，不知道是哪个字段、哪个 shot。

### 预算

- `create_h3_episode_pack_optimizer(..., output_retries=1)`（`h3_episode_pack.py:413`）
- `DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS` 默认 `2`（`h3_episode_pack.py:429`）
- `max_parallel` 形参被 `del max_parallel`（`narrative_group_video.py:698`）——**并发能力存在但被丢弃**

### 片段级兜底机制其实大半已存在

- 片段是**逐个提交**的：`adapter.generate_narrative_group(..., segments=(segment,))`
- 提交失败会 `record_video_segment_result(status="failed", error=...)` 并**继续下一个片段**
- `narrative_group_video.py:3350`：`terminal_status = "partial_failure" if segment_errors else "completed"`
- 合片用的就是成功的那批 `generated_segments`
- 前端 `group-video-segment-list.tsx` **已经在渲染** `segment.status` + `segment.error`，并对 `failed` 片段提供「重试片段」按钮
- 重试会**重新走 planner**：`generate_video_segment` → `_enqueue_group_video(..., segment_id=...)` → `narrative_group_video.py:2286` 把工作收窄到该片段；失败片段无缓存，必然重新调用模型

真正堵死这条路线的只有两处：

1. `_optimize_missing_prompts` 一个片段规划失败就**整体抛错** → `evidence_by_segment` 全空。
2. `_ensure_reference_plan_evidence`（`:1176`）**任一**片段缺 `director_plan` 就抛错，把整个循环拦在供应商调用之前。

### 本设计之前已落地的相关修复（未提交）

这五项是本轮排查的产物，是本次设计的前置条件，不再重复设计：

1. `text_task_runtime/deepseek_harness.py`：给 dsh 加 `--patch` 覆盖层禁用全部模型可见工具（原失败 #1）；`DSH_OUTPUT_INVALID` 带上结构化校验详情（不泄露模型原文）；超时消息带上执行预算。
2. `text_task_runtime/runtime.py`：`StructuredRuntimeAgent` 的重试提示词改为携带运行时给出的真实校验错误，而不是笼统断言「不是合法 JSON」。
3. `media_capabilities/video/h3_prompt_profile.py`：profile 16→17，补上从未写过的 `active_character_ids` 不变式（含画外/仅出声角色）。
4. `media_capabilities/video/h3_prompt_quality.py` + `h3_prompt_optimizer.py`：`normalize_h3_active_characters()` 在质检前用权威名单回填 `scene_context`（原失败 #3）。
5. `task_backend/runners/narrative_group_video.py`：按阶段上报 `progress` / `current_task`（原失败 #4）。

## 关键决策

### 决策 1：QC 不是把关人，编译器和供应商是唯一仲裁者

**理由**：37 条规则是启发式（正则 + 精确相等），误判成本极不对称——它错一次，代价是一次 10 分钟、要花钱的整组报废。而 `compile_h3_director_plan()` / `compile_h3_wire()` 是这个项目里**真正决定能不能渲染**的信号，它失败才是事实。

**后果**：`passed` 不再等于「一串规则全过」，而是「没有 error 级 issue 残留」。

### 决策 2：三级分档（T0 / T1 / T2），载体是 `severity`

`severity` 放宽为 `Literal["error", "warning", "repaired"]`：

- **T0 `repaired`**：权威输入已给正确值，我们直接写回，不报错、不花调用。
- **T1 `warning`**：不完美但能出片，记录后放行。
- **T2 `error`**：默认拦（但仍受决策 4、5 约束）。

**T0 — 可确定性修正**

| 码 | 修正方式 |
|---|---|
| `style_prefix_mismatch` | 用输入 `style_prefix` 覆盖 |
| `lighting_source_conflict` | 用锁定的 lighting facts 覆盖对应字段 |
| `dialogue_not_verbatim` / `dialogue_speaker_mismatch` / `dialogue_tone_mismatch` | 用输入 `dialogue_lines` **逐字重建** AUDIO |
| `dialogue_in_action_timing` | 按输入行重算时间 |
| `format_duration_mismatch` / `duration_frame_mismatch` | `total_duration_seconds = total_frames / fps` |
| `positive_constraint_count_mismatch` | 按列表重算 count |
| `character_count_mismatch` / `first_frame_character_mismatch` | 用 `active_character_ids` 回填（已实现） |
| `duplicate_active_reference` | 去重 |
| `unresolved_active_reference` | 丢弃没有 supplied tag 的引用 |
| `unsupported_active_reference_kind` / `reference_kind_mismatch` | 用 supplied fact 的 `kind` 覆盖 |
| `first_frame_anchor` / `last_frame_anchor` | 按 mode 与 supplied sha 填/清 |
| `action_timeline_gap` | 已由 `normalize_h3_action_timeline` 修 |
| `optics_shot_mismatch` | 丢弃不匹配 shot 的 optics 行 |
| `unknown_moving_entity` / `physics_entity_mismatch` | 与 `active cast ∪ held_props` 归并，去掉名单外实体 |

**T1 — 记警告放行**

`location_landmarks_required`、`quality_requirements_required`、`positive_constraints_required`、`physics_required`、`physics_incomplete`、`physics_entity_description_missing`、`incomplete_action_detail`、`vague_action`、`action_phase_regression`、`action_moving_entities_required`、`global_state_scope`、`format_mode_mismatch`、`format_cut_points_mismatch`

**T2 — 候选拦阻集（缩到最小）**

`rigid_prompt_required`、`character_acting_missing`（回填不成立时）、`first_frame_character_missing`（回填不成立时）、幻觉类（引用了输入中不存在的实体/台词）、以及**编译器本身抛错**（`compile_h3_director_plan` / `compile_h3_wire` / `inspect_h3_prompt` 失败——这不是 QC 意见，是事实）。

注意：T1 只是"不因 QC 而拦"，**仍然要过编译器**。如果某条 T1 警告实际上让编译器失败（例如 `physics_required` 导致 wire 缺段），那照样会拦——拦它的是编译器，不是规则表。这条把「T1 放松」与「编译器是仲裁者」统一起来，是全设计的一致性前提。

### 决策 3：默认档位是 `loose`

新增任务/项目级档位（默认 `loose`）。档位决定**T2 候选集里哪些真正拦**，以及 T1 是否拦：

| 档位 | T0 | T1 | T2 中真正拦的 | 编译器失败 |
|---|---|---|---|---|
| `strict` | 修 | 拦（等价于今天的行为） | 全部 T2 | 拦 |
| `balanced` | 修 | 放行 | 全部 T2 | 拦 |
| `loose`（默认） | 修 | 放行 | 仅幻觉类 | 拦 |

**关键区别**：`loose` 下 `rigid_prompt_required` / `character_acting_missing` / `first_frame_character_missing` **不拦**，交给编译器裁决（编译不过自然拦）与人工强制送渲染兜底。这是"QC 不可靠"最直接的体现。

**严重级别由 (码, 档位) 共同决定**，这样 `passed` 的定义在所有档位下都自洽（`passed == 无 error 级 issue 残留`）：

| 来源 | severity |
|---|---|
| T0 | `repaired` |
| T1 | `strict` 时 `error`，否则 `warning` |
| T2 | 该档位下拦则为 `error`，否则 `warning` |

**理由**：用户明确要求"QC 本身不一定可靠"。想收紧时改配置，不改代码。

### 决策 4：T2 触发时仍尝试本地编译一次

编译不花钱（纯本地字符串处理）。因此对**结构性 / 一致性类 T2**：

- 编译失败 → 真的出不了片 → 拦，记 `blocked_uncompilable`
- 编译成功 → QC 只是"意见" → **默认放行**，记 `qc_overridden_by_compiler`

**幻觉类 T2 不适用编译探测**：编译器无法判断"这个名字原文里有没有"，编译成功不构成放行依据，因此幻觉类始终拦（并可被决策 5 人工越过）。

**收益**：长期下来，哪些结构性 T2 规则是假警报会被数据直接暴露，而不是继续靠人写规则。

### 决策 5：T2 拦截必须可人工强制送渲染

被 T2 拦下的片段，在片段行失败提示上提供「仍然送渲染」。QC 不可靠，最终否决权交回人手里。强制送渲染成功后，manifest 记录该片段是人工越过的。

### 决策 6：plan 生成粒度改为逐片段

`H3EpisodeInput` 改为**每片段一个**（单片段 pack）。

- `_validate_pack(..., expected_ids={segment_id})` 已支持单片段。
- 缓存键 `_segment_input_hash()`（`h3_episode_pack.py:459`）**本来就只含该片段的 source/context + style/revision**，不含兄弟片段列表 → 逐片段调用天然复用已有缓存，**不需要新缓存表**。
- profile 已从 16 提到 17，旧缓存自然失效；**不必**再动 `_PACK_FORMAT_VERSION`（`:46`，留作将来单独调粒度的杠杆）。
- 每片段的 context 照旧塞满 `prev/next summary`、`continuity_contracts_json`、`continuity_locks`、`style_prefix`、`resolved_references`、`lighting_facts_json`——这些本来就按片段构造，不依赖 pack 里有没有兄弟。

**代价与缓解**：失去"同一次调用里跨组共同规划"。连续性本来由 contracts / locks / prev-next summary 承载；如果将来发现跨组共同规划确有价值，可以退化为"兄弟片段作为**只读上下文**放进 payload，但 pack 只覆盖本片段"。

### 决策 7：并发上限落实为 3

`max_parallel` 不再被丢弃：默认 3，env `DRAMACLAW_H3_PROMPT_CONCURRENCY`，上限 4。依据是仓库已有先例 `design_merged_characters(concurrency=3)`。不并发的话 8 个片段会把约 1 分钟变成约 8 分钟。

视觉（storyboard）路径仍按 `pack_storyboard_batches` 分批送图（受图片体积约束），批次之间在并发上限内并行。

### 决策 8：片段级隔离；组级三态不变

- `_optimize_missing_prompts` 改为**逐片段收集结果**（`plan ready` / `plan failed + reason`），不再整体抛错；只有 `MemoryError` / `StoryboardPromptBlocked` 这类真·环境错误仍向上抛。
- `_ensure_reference_plan_evidence` 从"抛错"改为**返回缺失集合**；调用处把这些片段直接记为**该片段失败**（`record_video_segment_result(status="failed", error=<原因>)`），然后**继续提交其余片段**。
- 组级终态沿用现有三分支：全成功 `completed`；部分成功 `partial_failure`；全失败仍抛 `all video segments failed`。
- 合片沿用现有逻辑：≥2 个成功片段按 `compose_local_segments` 拼接**成功的那些**；恰好 1 个则直接用它当输出。

### 决策 9：参考降级渲染默认关闭

保留为显式开关 `DRAMACLAW_H3_ALLOW_NONREFERENCE_FALLBACK`，**默认 0**。开启时该片段以空 `reference_wire` 提交、mode 回落 `i2va`/`fl2va`，manifest 写 `reference_fallback: true`，前端标注"该片段未锁定参考图"。

**理由**：静默降级会改变画质承诺（用户以为锁了参考图，实际没有）。

### 决策 10：错误信息带 `code @ field`

`H3PromptQualityError` 的摘要改为每条至少 `code @ field`（**最多 5 条**，超出以 `… (+N more)` 结尾），例如：

```
first_frame_character_mismatch @ rigid_prompt.spatial_blocking.1.subjects;
character_acting_missing @ rigid_prompt.character_acting
```

同时把**该片段的** issue 列表（含 T1 warning、T0 repaired）写进 `record_video_segment_result(error=...)` 与 manifest，而不是只留在组级异常里。

### 决策 11：记录规则触发频次

T0 的每次自动修正、T1 的每条警告、T2 的每次拦截与 override 都落进 manifest：

```json
{ "repairs": [{"code": "...", "field": "..."}],
  "warnings": ["..."],
  "blocked": [{"code": "...", "field": "...", "compilable": true}] }
```

跑几集之后就有真实频次表：天天出现的码 → 改提示词或降为 T0；从不出现的码 → 可以放心保持严格。这是"AI 会习惯挑错"的长期解药——用数据决定规则松紧。

### 决策 12：进度补全

计划阶段把 15%→35% 按"已完成片段数 / N"分摊；片段规划失败也报 `第 i/N 段提示词未通过：<一句话原因>`。复用已落地的 `_progress_reporter`。

## 实施分阶段（按投入产出排序）

三个阶段各自**独立可交付、可单独成计划**；第一个实现计划只覆盖 P0。

**P0 — 让失败不再报废（不改产物语义，自包含）**

1. `severity` 放宽 + `passed` 语义改为「无 error 级残留」。
2. T0 修正器逐码实现（决策 2 表）。
3. T1 全部降为 warning。
4. `_optimize_missing_prompts` 逐片段收集、`_ensure_reference_plan_evidence` 返回缺失集合 → 片段级失败但不拖垮全组（决策 8）。
5. 错误信息带 `code @ field`（决策 10）。

**P1 — 逐片段生成与并发（提高首次通过率）**

6. 粒度改为逐片段（决策 6）。
7. `max_parallel` 落实为 3（决策 7）。
8. 进度按片段分摊（决策 12）。

**P2 — 可观测与可控**

9. 档位开关 `loose`/`balanced`/`strict`（决策 3）。
10. T2 触发时的本地编译探测（决策 4）。
11. 人工强制送渲染（决策 5）。
12. manifest 里的 `repairs`/`warnings`/`blocked`（决策 11）。
13. 前端：组级一行汇总（`3/5 片段成功 · 2 个未通过（展开原因）`）+ T1 条数。

## 验收标准

1. **历史载荷复跑**：本次事故的真实 pack 输出（含 `scene_context` 掉人的两份、违反 ref2va 的两份）在默认档下**不再让整组失败**——要么产出可用 plan，要么只废对应片段、组级为 `partial_failure`。
2. **单片段失败不拖垮全组**：构造"第 2 段 planner 必败"，断言第 1 段照常提交、成片文件存在、组级 `partial_failure`、失败片段 `error` 带 `code @ field`。
3. **QC 不拦可编译的结果**：对每个**结构性 / 一致性类** T2 码各构造一次触发，断言"编译器能过 → 该片段仍被提交"，并记 `qc_overridden_by_compiler`；幻觉类 T2 反向断言"编译器能过也仍然拦"。
4. **不花冤枉钱**：T2 判定发生在供应商调用之前；T0 修正不增加模型调用次数。
5. **预算**：N ≥ 3 且并发 3 时，计划阶段墙钟时间 < 串行所需时间的 60%；每片段模型调用数 ≤ `1 + DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS`（默认 3 次）。
6. **可观测**：manifest 内每片段可见 `repairs` / `warnings` / `blocked`，可直接统计各码频次。
7. **回归**：现有 1805 个测试全绿；新增覆盖 T0 逐码、T1 不阻断、T2 阻断、编译器 override、强制送渲染、partial 成片、逐片段并发。
8. **产品口径**：连续 3 集生产不再出现"因质检而整组失败"。

## 测试组织

- **T0/T1/T2 码表**用参数化测试逐码覆盖，而不是只测代表样本——码表是本设计的核心数据，必须每个码都有断言。
- **片段级隔离、并发、partial 成片**用真实 runner 的集成测试（沿用 `tests/test_task_narrative_group_video_runner.py` 的 `_seed_group` + `_patch_segment_optimizer` 夹具）。
- **强制送渲染**用一条 HTTP 级测试。
- **历史载荷**作为固定夹具放进测试（脱敏后保留结构），防止回归。

## 风险与取舍

| 风险 | 取舍 |
|---|---|
| 放宽 QC 后可能出"不完美但能看"的片子 | 这是有意的：用户明确要求出片优先；不完美以 warning 形式可见 |
| 逐片段生成失去跨组共同规划 | 连续性由 contracts/locks/prev-next summary 承载；确有价值时可退化为"只读上下文" |
| per-segment 并发可能触及模型侧限流 | 默认 3 且可配；`DSH_EXEC_TIMEOUT_SECONDS` 独立生效 |
| T2 只剩极少规则，可能漏掉真正会毁片的错误 | 决策 4 的编译探测 + 决策 5 的人工强制送渲染构成兜底；幻觉类始终保留 |
| 逐片段调用数从 1 变 N，成本上升 | 缓存仍按片段命中，重跑只补缺失片段；并发抵消时间成本 |
