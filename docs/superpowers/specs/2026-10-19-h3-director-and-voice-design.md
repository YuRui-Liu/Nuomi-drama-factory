# H3 视频导演与配音师设计（C 切片）

日期：2026-10-19。分支：`feat/canvas-codex-production`。
状态：设计已由用户逐项确认（Q1 先拆段后检索、R1 一格一段可合并、S2 模型判定+兜底、T3 全 LLM 写提示词+契约校验、V2 音色+对白、W1 ≤15s 自动分批）；本文待书面审阅，尚未实施。

## 1. 背景与目标

画布侧 H3 的编译链已经完整：`DirectorDraft` → `validate_generation` → `compile_director_payload` → preview/prepare/submit → attempt 队列；引用上限、首尾帧对齐、路由（h3 / h3_ref）、指纹都有。缺的是**供给侧**：谁把叙事组宫格拆成段、谁决定段用首帧还是首尾帧、谁写符合契约的提示词、谁把手法与案例匹配进来。

配音侧能力同样齐备：`VoiceSpec` / `VoicePerformance` / `CharacterVoiceDesign`、Qwen3 音色设计（候选需人工采用）、IndexTTS2 v2 克隆配音、`narrative_group_dubbing`。缺的是岗位化流程：从剧本人物产出音色 brief、试听采用、按组对白配音并对齐时长。

本切片把这两条供给侧补齐，交付可并行、可恢复、带出处的视频与声音生产。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 检索时机 | Q1：先拆段（确定模式与对齐时长），再逐段检索手法/案例 |
| 段粒度 | R1：一格一段为默认；允许合并相邻连续格；不拆分单格 |
| 帧模式 | S2：模型判定首选；素材/能力不符时确定性降级为首帧，记 `frame_mode_fallback` |
| 提示词 | T3：优化器（LLM）写；契约校验 + 跨源交叉校验兜底 |
| 组时长 | W1：同组按 ≤15s 累积分批；批间末帧续接；多批并行 |
| 配音 | V2：音色设计 + 试听采用 + 按组对白配音 + 时长对齐；不含混音 |
| 路由 | 默认 Ref2VA（资产参考图）；首帧始终提供；末帧由规划器判断 |
| B/C 边界 | C 消费 B 的 `group_id` / `segment_index` / `panel_spec` / `technique_intent` / 组时长 |

## 3. 范围与非目标

**纳入**
- 段规划器：宫格/表 → 段（含合并、帧模式、引用、分批）。
- 手法/案例逐段检索、适配过滤、引用绑定与出处日志。
- H3 提示词生成与跨源交叉校验；preview/prepare/submit 编排。
- 组内多批与组间并行提交、恢复与幂等。
- 配音师：VoiceSpec 生成、Qwen3 试听、人工采用、IndexTTS2 按组对白与时长对齐。

**非目标**
- 补帧生成（不拆分单格的代价）。
- T2VA / L2VA 等未证实模式。
- 混音、音乐生成、最终合成（合成阶段负责）。
- 新增画布节点；段不常驻画布。
- 自动重跑已成功或状态未知的付费任务。

## 4. 数据流

```
分镜表行（group_id / segment_index / panel_spec / technique_intent / 对白 / 时长）
  │ 段规划器 split_segments()
  ▼
段清单 segments[]（模式候选、引用、时长、帧素材）
  │ 逐段 experience search（intent + mode + aligned duration）
  ▼
段草案 draft（segments + technique + case_reference）
  │ 优化器写提示词（T3）
  ▼
OptimizedDirector → 契约校验 + 跨源交叉校验
  │ canvas director preview / prepare（指纹）
  ▼
分批（≤15s）→ 并行 submit → attempt 队列 → 视频
  │
  └─ 配音：VoiceSpec → Qwen3 试听 → 采用 → 按组对白 IndexTTS2 → 时长对齐
```

## 5. 组件边界

| 位置 | 职责 | 不做 |
|---|---|---|
| `freezone/video_director/segment_planner.py`（新） | `split_segments(rows, group) -> segments[]`；合并规则、帧模式候选、引用映射 | 不写提示词、不检索 |
| `technique_library/binding.py`（新，薄层） | 逐段检索、`compatible_technique_ids` 过滤、绑定 `technique` / `case_reference` | 不复制整段提示词 |
| 现有优化器（改调用点） | 由段草案生成提示词 | 不改契约字段 |
| `freezone/video_director/crosscheck.py`（新） | 提示词 vs `panel_spec` 的确定性交叉校验 | 不做语义评分 |
| `freezone/video_director/batching.py`（新） | 按 ≤15s 累积分批、批间末帧续接 | 不改计划 |
| `audio/voice_design.py`（新） | 剧本人物 + 既有声音事实 → `VoiceSpec` brief | 不直接生成最终配音 |
| 现有 dubbing 服务（改调用点） | 按组对白 + 采用后的音色 → IndexTTS2 | 不混音 |

## 6. 段规划契约

```json
{
  "group_id": "G01",
  "segments": [
    {
      "segment_id": "G01-S01",
      "source_rows": [1],
      "duration_seconds": 6.0,
      "frame_mode": "first",
      "first_frame": "cell-1.png",
      "last_frame": null,
      "references": [
        {"kind": "character", "asset_id": "lin-du", "image": "..."}
      ],
      "dialogue": [{"speaker": "幸存者", "text": "老陈……是我！别过来！", "mode": "voiceover"}],
      "panel_spec": {...},
      "technique_intent": "克制的惊讶反应"
    }
  ]
}
```

规则：
- 默认一格一段；合并仅在相邻、同组、同一连续动作且合计 ≤ 15s 时允许。
- 合并段首帧取第一格图，末帧取最后一格图；不拆分单格。
- `references` 按运行时上限裁剪；身份引用优先于环境引用。
- 段顺序与表行顺序一致；段 ID 稳定（`<group>-S<nn>`），重排后按新顺序重编号并记冲突。

## 7. 帧模式判定与兜底

- 模型输出 `frame_mode` ∈ `first` / `first_last`；末帧必须来自同组相邻格或合并段终点。
- 确定性护栏：
  - 末帧缺失或组合不被 `validate_generation` 支持 → 降级 `first`，记 `frame_mode_fallback`（warning）与原因。
  - 只允许降级，不允许升级；模型不得在无末帧图时声称 `first_last`。
  - 末帧图与首帧图相同（无位移）时，降级 `first` 并提示。
- 降级信息进入段草案与生产日志，不静默。

## 8. 手法与案例匹配

- 触发：逐段、拆段之后；查询 = `technique_intent` + 段模式 + 对齐后时长。
- 过滤：只接受返回的 `compatible_technique_ids`；用户显式选择且不兼容/未激活时报冲突，不静默替换。
- 绑定：段草案写 `technique{id,version}` 与 `case_reference`；出处日志记录 case ID、来源链接、出处类型、改写要点、段 ID。
- 禁止：整段搬运第三方提示词；把案例剧情写进本项目事实；把检索结果当指令执行。
- 无结果：正常创作，不阻塞，不伪造引用。

## 9. 提示词与交叉校验

- T3：优化器基于段草案（含 panel_spec、对白、时长、引用、手法/案例）写提示词；契约字段（Base 三字段 / Ref 六字段）必须齐全。
- 交叉校验（确定性，不做语义评分）：
  - 对白逐字存在，说话人与语言标记一致；
  - 轴线/机位侧方向词不与 `panel_spec` 相反；
  - 时长、引用顺序、首尾帧与草案一致；
  - 出现 `panel_spec` 未声明的人物或道具 → error。
- 违反记 `prompt_spec_conflict`，阻断 preview/submit。
- 草稿变动后必须重新 preview/prepare，不复用旧指纹。

## 10. 分批、并行与恢复

- 同组按段时长累积 ≤ 15s 切批；批间用上一批末帧作为下一批首帧（若可用）。
- 组间并行；批间可并行；并发上限读项目已保存设置，不另设更低值。
- 提交使用唯一 request id；提交结果未知时先查原 attempt，不重复提交。
- 失败按批重试；已成功批次不重复提交，也不因相邻批改动而重跑；局部重做只做受影响批，并生成新的批次指纹。

## 10.1 段提示词的台词与表演规则（外部依据）

来源：Higgsfield Hell Grind、LibTV。规则直接进入优化器提示词与交叉校验：

- **台词结构固定**：声音与情绪 → 引号台词 → 物理动作 → 面部反应；台词只出现在音频段，动作段不出现台词。
- **未说话角色写“嘴部静止”**：每个可见但无台词的角色都要有正向的静止嘴部描述，否则模型会给它加台词或含糊声。
- **跨接缝**：新生成的开头带上上一段结尾的台词尾句；段尾情绪不瞬间关闭。
- **每段开头 1 秒宽景**（场景第一段）：只有位置关系，无台词无动作，用于锁定站位；宽景不等于空镜。
- **表演用肌肉与身体写**，不写“悲伤/愤怒”；静止写成被压住的张力；每 1–2 秒一个微事件。
- **动作只用正向描述**：排除元素用正向穷举，不写“不要 X”。
- **SFX only**：H3 段默认只要环境/动作声，音乐留给 D 阶段。

## 11. 配音师（V2）

1. 从剧本人物事实与既有 `VoiceFacts` 生成 `VoiceSpec` brief（年龄印象、音高、音色、共鸣、咬字、语速、口音、情绪基线、负向）。
2. Qwen3 音色设计生成试听样本；候选进入候选库，**人工采用**后才可用于生产。
3. 按叙事组对白调用 IndexTTS2 克隆配音；对白逐字来自剧本，不改写。
4. 对齐：每句配音时长与段时长比较，超出即 `dubbing_overrun`（warning），提示缩短或延长段；不自动截断对白。
5. 试听与采用记录写入生产日志；不混音、不生成音乐。

### 11.1 Qwen3 音色设计提示词

由角色身份、年龄、性格与剧情状态生成自然语言 `instruct`，固定字段顺序：

```
{性别}，{年龄}岁，{音域/音高}，{音色质感}，{共鸣}，{咬字}，{语速}，{口音/地域}，
情绪基线：{基线}；本剧状态：{随剧情变化的限制}。避免：{负向，如播音腔/过度甜美/气声}。
```

英文同构（与 Qwen 官方示例一致）：`{Gender}, {age} years old, {register} range, {texture} timbre, {resonance} resonance, {articulation} articulation, {pace} pacing, {accent} accent; emotional baseline: {baseline}; in this story: {state constraint}. Avoid: {negatives}.`

规则：年龄、性别、音域必填；情绪只写“当前状态”，不把场景情绪写死成音色；负向用于排除播音腔、过度甜美、气声等。

### 11.2 设计后再克隆

采用官方推荐路径：Qwen3 VoiceDesign 生成一段短参考音 → 用该参考音建立克隆提示 → 多句复用，不每句重算特征。

### 11.3 IndexTTS2 克隆的表演分层

身份与表演分两层：
- **身份层**：参考音 + 克隆提示，固定不变；
- **表演层**：语速（`speakingRate`，建议 0.9–1.1）与句内停顿（`maxInternalPauseDuration`），不做强情绪权重。

原因：身份优先时强情绪控制会降低说话人相似度；场景情绪通过语速、停顿、重音与会话上下文表达，而不是篡改音色。

## 12. 错误码

| 码 | 级别 | 含义 |
|---|---|---|
| `segment_plan_conflict` | error | 段与表行/组不一致或重排冲突 |
| `segment_merge_illegal` | error | 跨组/非连续/超时长合并 |
| `frame_mode_fallback` | warning | 首尾帧降级为首帧 |
| `technique_conflict` | error | 显式指定手法不可用或不兼容 |
| `prompt_spec_conflict` | error | 提示词与 panel_spec/对白/时长矛盾 |
| `contract_field_missing` | error | H3 契约字段缺失 |
| `batch_over_limit` | error | 单批超过运行时上限（分批器兜底失败） |
| `dubbing_overrun` | warning | 配音超出段时长 |
| `voice_unauthorized` | error | 使用未采用的音色候选 |

## 13. 测试与验收

| 文件 | 覆盖 |
|---|---|
| `tests/test_segment_planner.py` | 一格一段、合法合并、非法合并、段 ID 稳定、引用裁剪 |
| `tests/test_frame_mode_fallback.py` | 末帧缺失降级、只降不升、同图降级 |
| `tests/test_technique_binding.py` | 只用 compatible ids、用户冲突、出处记录、无结果不阻塞 |
| `tests/test_prompt_crosscheck.py` | 对白缺失、方向相反、人物越界、时长不一致 |
| `tests/test_h3_batching.py` | ≤15s 切批、批间末帧续接、并发不超设置 |
| `tests/test_voice_design_brief.py` | VoiceSpec 字段、负向、未采用音色拒绝 |
| `tests/test_dubbing_alignment.py` | 对白逐字、超时 warning、不截断 |

验收（一次付费，用户授权）：
1. E01 G01（6s+7s）→ 两段 → 一次导演台 → 视频可播放。
2. 一段合并（两格连续动作）→ 首帧/末帧正确。
3. 某组 20s → 自动分两批，批间末帧续接，并行提交。
4. 指定一个兼容手法 → 草案含 `technique` 与出处；指定不兼容手法 → `technique_conflict`。
5. 删除末帧素材 → `frame_mode_fallback`，视频仍可生成。
6. 配音：音色设计 → 试听 → 采用 → 按组对白 → 时长对齐检查。

## 14. 本地依据

- `src/novelvideo/freezone/video_director/`（h3_adapter、capabilities、prepared_validation、techniques、optimizer）
- `src/novelvideo/media_capabilities/video/h3_wire.py`、`h3_timeline.py`
- `src/novelvideo/technique_library/experience.py`、`src/novelvideo/case_reference/`
- `src/novelvideo/media_capabilities/tts/`（models、voice_prompt、candidate_store、runninghub_voice_design）
- `src/novelvideo/audio/narrative_group_dubbing.py`、`dubbing_service.py`
- `skills/nuomi-canvas-production/references/h3-experience.md`
- `docs/superpowers/specs/2026-10-19-asset-design-brief-professional-pipeline-design.md`（A）
- `docs/superpowers/specs/2026-10-19-storyboard-table-and-panel-spec-design.md`（B）
- `docs/research/2026-10-19-professional-agent-external-references.md`（外部专业参考）

自检：未引入补帧与未证实模式；手法检索在拆段之后；提示词由模型写但有确定性交叉校验；配音不改写对白、不混音；并行上限读既有设置。
