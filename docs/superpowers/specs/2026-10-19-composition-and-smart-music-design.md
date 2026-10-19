# 合成与智能配乐设计（D 切片）

日期：2026-10-19。分支：`feat/canvas-codex-production`。
状态：设计已由用户确认（A3 按组选择音频来源、X3→X2→X4 对齐处理、Y3 先匹配后生成、配乐按组且默认关闭）；本文待书面审阅，尚未实施。

## 1. 背景与目标

C 切片产出的是按叙事组的视频批次与对白音轨；到成片还差三步：把批次拼回时间线、决定每一组的对白来源（配音或 MiniMax 原声）、按需配乐并混音。现有零件齐全：`MusicPlan`（多轨、淡入淡出、ducking）、`render_mix` / `mux_video`、`music/matching.py`、`audioNode.music`、`musicDeskNode`、`episodeComposeNode.compose`。

缺的是编排与判定：谁决定音频来源、对白放不下时怎么办、配乐什么时候该出现、合成前要校验什么。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 音频来源 | A3：默认配音优先，原声作为环境轨；可按叙事组切换为原声优先 |
| 对齐处理顺序 | X3 先挤气口 → X2 ≤5% 轻微变速 → X4 阻断请用户决定 |
| 智能配乐 | Y3：先素材库匹配，无合适再生成；生成结果回库复用 |
| 配乐粒度 | 叙事组；组间淡入淡出衔接 |
| 配乐开关 | 默认关闭，显式开启 |
| 对白优先 | 人声区间音乐自动 ducking，不依赖人工调音量 |

## 2.1 外部依据

Higgsfield Hell Grind 的生产线把音乐明确留给后期（“SFX only. No music.”），理由与我们的 D 切片一致：生成时带音乐只会在剪辑时碍事。因此：

- H3 段默认只要环境/动作声；
- 音乐只在 D 阶段作为独立轨进入，默认关闭；
- 迭代遵守“外科手术”原则：一次只改一处，同一镜超限就改镜头结构，不无限重抽。

## 3. 范围与非目标

**纳入**
- 批次拼接为集时间线（跨批、跨组）。
- 每组音频来源选择与对白对齐。
- 可选智能配乐（匹配 → 生成回退）与 ducking。
- 合成前校验与合成后播放检查。

**非目标**
- 重新生成视频（对齐兜底时不自动重跑；由用户决定）。
- 语音克隆模型训练、音色设计（属 C）。
- 影视级混音（多段压缩、母带处理）。
- 新增画布节点：沿用 `episodeComposeNode` / `musicDeskNode`。

## 4. 数据流

```
C 的视频批次（每批一个 attempt，段顺序固定）
  │ 拼接：按组顺序、段顺序、批间末帧续接
  ▼
集时间线（组 → 段 → 起止帧/秒）
  │ 每组音频模式：dub | original | hybrid（默认 dub）
  ▼
对白轨（C 产出的配音或 H3 原声）
  │ 对齐：X3 挤气口 → X2 ≤5% 变速 → X4 阻断
  ▼
音乐轨（可选）：匹配 → 生成回退 → 按组放置
  │ ducking：人声区间压低音乐
  ▼
混音（MusicPlan）→ 合成（episodeComposeNode.compose）
  │ 校验：每轨一次、顺序、缺失、时长、播放
  ▼
成片
```

## 5. 音频来源策略（A3）

每个叙事组带 `audio_mode`：

| 模式 | 对白 | 环境/动作声 | 适用 |
| --- | --- | --- | --- |
| `dub`（默认） | 采用后的 IndexTTS2 配音 | H3 原声压低 | 常规对白、跨段音色需统一 |
| `original` | MiniMax 原声 | MiniMax 原声 | 强情绪、口型要求高、原声已满意 |
| `hybrid` | 配音替换指定行 | 其余保留原声 | 只有部分台词需要重配 |

规则：
- 默认 `dub`；切换需显式，且切换后重新生成该组混音。
- `original` 模式下不得再叠一份配音对白（避免双声）。
- `hybrid` 必须列出被替换的对白行 ID，未列出的保持原声。

## 6. 对白对齐

按顺序尝试：
1. **X3 挤气口**：压缩句间静音与停顿，不改台词、不动视频。
2. **X2 轻微变速**：累计不超过 5%；超过即进入下一步。
3. **X4 阻断**：报 `dubbing_overrun`，由用户选择延长镜头（回 C 重跑）、改写台词或接受留白。

约束：
- 绝不自动重跑视频，绝不自动删台词。
- 每次调整记录在案的策略与幅度，可回滚。
- 对齐只作用于对白轨，不影响环境轨与音乐轨。

## 7. 智能配乐（Y3）

开启后按叙事组执行：
1. **匹配**：用组情绪/节奏/时长查询素材库（`music/matching.py`），要求纯音乐、时长可覆盖。
2. **回退生成**：无合适结果时调用 `audioNode.music` 生成；生成结果入素材库供后续复用。
3. **放置**：按组起止放置，组间淡入淡出；总时长不足则循环补足，超出则淡出。
4. **Ducking**：人声区间自动压低音乐（现有 `MusicPlan` ducking），不做人工音量包络。

规则：
- 默认关闭；开启是一次显式动作。
- 同一集内同一曲目最多出现一次（避免重复听感）；素材库匹配失败且生成失败时留空，不阻断合成。
- 配乐不覆盖对白与环境声；音量基线由 `MusicPlan` 负责。

## 8. 合成与校验

- 每集只合成一次：视频轨、对白轨、环境/原声轨、音乐轨各一次。
- 合成前校验：段顺序、组顺序、缺失片段、静音对白、重复音轨、总时长、每轨是否被引用两次。
- 合成后校验：可播放、总时长与时间线一致、无意外空白、关键对白可闻。
- 校验失败不写“完成”，保留上一次可用成片。

## 9. 错误码

| 码 | 级别 | 含义 |
|---|---|---|
| `timeline_gap` | error | 时间线缺段或顺序错误 |
| `audio_mode_conflict` | error | 组内原声与配音同时作对白 |
| `dubbing_overrun` | error | 对齐三步后仍超出，需用户决定 |
| `music_duration_short` | warning | 配乐不足，已循环或留空 |
| `duplicate_audio_track` | error | 同一轨被叠加两次 |
| `composition_incomplete` | error | 合成缺片或时长不符 |

## 10. 测试与验收

| 文件 | 覆盖 |
|---|---|
| `tests/test_episode_audio_mode.py` | 三种模式互斥、hybrid 行级替换、默认 dub |
| `tests/test_dubbing_alignment_chain.py` | X3 → X2 → X4 顺序、5% 上限、不重跑视频 |
| `tests/test_smart_music.py` | 匹配优先、生成回退、同曲不重复、失败留空 |
| `tests/test_music_ducking.py` | 人声区间压低、无人工包络 |
| `tests/test_composition_checks.py` | 缺段、重复轨、时长、播放检查 |

验收（一次付费，用户授权）：
1. 两组视频 + 配音 → 合成 → 播放，对白可闻、顺序正确。
2. 一组切 `original` → 无双声、无配音残留。
3. 一句配音超长 3% → 仅变速；超 8% → 阻断并给出选项。
4. 开启配乐 → 匹配命中，无对白段可闻；关闭则无音乐轨。
5. 缺一段视频 → `timeline_gap`，不产出残缺成片。

## 11. 本地依据

- `src/novelvideo/music/`（models、mix、matching、generation、service）
- `src/novelvideo/freezone/orchestration/capabilities.py`（episodeComposeNode / musicDeskNode）
- `src/novelvideo/freezone/audio_node.py`（music 生成）
- `src/novelvideo/audio/narrative_group_dubbing.py`、`dubbing_service.py`
- `skills/nuomi-canvas-production/references/h3-experience.md`（音频去重与合成检查）
- `docs/superpowers/specs/2026-10-19-h3-director-and-voice-design.md`（C）

自检：音频来源互斥；对齐不自动重跑视频；配乐默认关闭；合成前校验可定位；未新增节点。
