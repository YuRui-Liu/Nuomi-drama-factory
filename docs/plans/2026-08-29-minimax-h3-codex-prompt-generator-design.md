# MiniMax H3 Codex 提示词生成器设计

> 状态：已确认  
> 日期：2026-08-29  
> 范围：`dramaclaw` 叙事组 MiniMax 导演台  
> 依据：[MiniMax H3 官方 h3-prompt-writing skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)

## 1. 目标

以 Codex 运行时智能体取代导演台现有提示词生成器，同时满足两层契约：

1. 保持叙事组 MiniMax 导演台现有输入、页面字段和 manifest 向后兼容；
2. 最终提示词严格符合 MiniMax H3 官方 `h3-prompt-writing` skill，覆盖 T2VA、I2VA、FL2VA、L2VA 和 Ref2VA。

系统同时支持：

- 单 Beat 调试、重生成、多轮修改和人工确认；
- 整集 Beat 批量生产和通过校验后自动可用；
- 可选分镜图的多模态理解；
- Codex 默认运行时与未来 WorkBuddy 等适配器；
- 可复现的上下文快照、版本、校验和失败恢复。

## 2. 架构决策

采用“内置任务编排层＋可插拔运行时适配器”：

```text
导演台现有输入
  → 输入规范化
  → 上下文打包
  → Prompt Generation Job
  → Codex Runtime Adapter
  → 导演台契约校验
  → MiniMax H3 官方规范校验
  → 最多两次自动修订
  → 兼容旧格式的结果与 manifest
```

不在首版拆分独立 MCP 服务，但内部边界按可封装为 MCP 工具的方式设计。

## 3. 输入契约与上下文包

导演台现有请求格式保持不变。新系统内部增加 `InputNormalizer`，将旧 Beat 数据映射为稳定的内部上下文。正式实现时必须先对照现有源码和 manifest schema，不凭页面文案猜测字段名。

```json
{
  "task": {
    "episode_id": "E01",
    "beat_id": "B019",
    "duration_seconds": 5.17,
    "requested_mode": "fl2va",
    "aspect_ratio": "9:16"
  },
  "narrative": {
    "current_beat": {},
    "previous_beat_summary": {},
    "next_beat_summary": {},
    "episode_summary": "",
    "dramatic_intent": ""
  },
  "continuity": {
    "characters": [],
    "character_states": [],
    "scene": {},
    "props": [],
    "spatial_constraints": [],
    "continuity_constraints": []
  },
  "references": [],
  "locked_facts": {
    "dialogue": [],
    "plot_facts": [],
    "duration_seconds": 5.17,
    "requested_mode": "fl2va",
    "reference_bindings": []
  },
  "runtime": {
    "provider": "codex",
    "model": null,
    "reasoning_effort": null,
    "strict_validation": true
  }
}
```

### 3.1 默认上下文

每个 Beat 任务默认包含：

- 当前 Beat 的完整信息；
- 前后 Beat 的压缩摘要；
- 本集摘要与叙事目标；
- 相关角色的当前状态；
- 场景、道具和空间连续性；
- 当前模式所需的分镜附件；
- 必要时补读的白名单资料及其版本记录。

对白、剧情事实、时长、导演台指定模式和媒体绑定进入 `locked_facts`，Codex 不得改写。

### 3.2 白名单补读

Codex 默认只消费导演台打包数据。生成所需信息缺失时，只能按设置面板白名单补读指定项目资料。

每次补读记录路径别名、读取原因、修改时间、内容哈希和实际纳入的章节。不得自行扩大白名单；缺失必要资料时返回 `CONTEXT_INSUFFICIENT`。

### 3.3 分镜参考语义

每张图必须明确用途：

- `context_only`：只供 Codex 理解，不进入 H3 引用；
- `first_frame`：H3 首帧 `<Picture N>`；
- `last_frame`：H3 尾帧 `<Picture N>`；
- `full_reference`：人物、场景、风格或其他完整参考。

默认为 `context_only`，避免上传草图后意外改变生成模式。

### 3.4 模式决策

导演台指定模式优先；未指定时才根据参考资产推断。指定模式与素材冲突时必须停止并提示，不静默降级。

```text
明确 t2va  → 不使用首帧或尾帧生成绑定
明确 i2va  → 必须有效首帧
明确 fl2va → 必须有效首帧和尾帧
明确 l2va  → 必须有效尾帧
明确 ref2va → 至少一个完整参考资产

未指定：
首帧＋尾帧       → fl2va
仅首帧           → i2va
仅尾帧           → l2va
仅完整参考     → ref2va
无生成参考资产 → t2va
```

若同时存在首尾帧和完整参考等多类资产，且无法唯一推断，则要求用户明确选择模式。

## 4. 任务编排与会话模型

系统统一使用两层任务：

```text
GenerationRun
  └─ BeatGenerationJob × N
```

`GenerationRun` 表示一次单 Beat 调试或整集批量运行；`BeatGenerationJob` 永远只处理一个 Beat。

### 4.1 单 Beat

- 首次生成创建 Codex 持久会话；
- 结果展示导演计划、最终提示词和质量报告；
- 用户可在同一会话中继续提出修改；
- 每次修改生成不可变版本，支持比较和恢复；
- 人工点击确认后才进入“可用”状态；
- Beat、模式、时长或参考资产发生变化时，创建新上下文分支。

### 4.2 整集批量

- 启动时冻结运行时、模型、推理强度、skill 版本、校验规则和并发上限；
- 每个 Beat 建立独立上下文快照和隔离的 Codex 任务；
- 任务之间不共享会话，叙事连续性由统一打包的上下文保证；
- 单个 Beat 失败不回滚已成功结果；
- 通过自动校验的 Beat 直接标记“可用”；
- 失败 Beat 可单独转入持久调试会话。

### 4.3 状态机

```text
queued
  → validating_input
  → packaging_context
  → waiting_for_runtime
  → generating
  → validating_output
      → auto_revising_1
      → validating_output
      → auto_revising_2
      → validating_output
  → awaiting_approval   # 单 Beat
  → ready               # 批量通过，或单 Beat 确认
  → failed
  → cancelled
```

输入冲突标记为 `input_invalid`，发生在 Codex 调用前，不消耗自动修订次数。

### 4.4 自动修订

输出校验失败后，将机器可读错误反馈给原 Beat 的 Codex 会话，最多自动修订两次。仍失败则转人工处理。

自动修订只能修改不合规部分，不得改变对白、剧情事实、生成模式、时长或参考资产绑定。需要增加资产或修改锁定事实才能修复时，必须拒绝自动修订并转人工。

基础设施错误与提示词合规错误分开计数。运行时暂时失败按连接策略重试，不自动切换到 WorkBuddy，不在重试中更换模型或 skill 版本。

## 5. Codex 运行时契约

Codex 是默认运行时。通过统一的 `AgentRuntimeAdapter` 接口集成，未来 WorkBuddy 只需实现同一接口。

运行时至少需要支持：

- 创建隔离任务；
- 创建或继续持久会话；
- 发送结构化上下文与多模态附件；
- 查询状态；
- 获取结构化结果；
- 取消未完成任务；
- 支持幂等键和恢复。

Codex 返回结果必须符合固定 schema，不直接返回无结构的最终文本：

```json
{
  "schema_version": "h3-director-result/v1",
  "input_mode": "fl2va",
  "director_plan": {
    "duration_seconds": 5.17,
    "shot_count": 1,
    "visual_style": "",
    "opening_composition": "",
    "performance_beats": [],
    "camera_plan": [],
    "reference_alignment": [],
    "dialogue_plan": [],
    "soundscape_plan": [],
    "music_plan": []
  },
  "final_prompt": "",
  "used_context": {
    "packaged_fields": [],
    "whitelist_files": [],
    "reference_assets": []
  },
  "agent_warnings": []
}
```

Codex 系统指令固定包含：

- 官方 `h3-prompt-writing` skill；
- 当前模式对应的完整参考规范；
- 导演台结果 schema；
- 当前任务的锁定事实；
- 禁止修改和禁止推断的边界；
- 只输出 JSON，不使用 Markdown 代码围栏。

官方 skill 固定到具体版本或 Git commit。设置面板可升级，但批量任务进行中不得切换；manifest 记录实际使用的仓库版本。

## 6. 双重校验

### 6.1 导演台契约校验

检查结果是否忠于叙事组输入：

- Beat、模式、时长和参考资产一致；
- 对白文字及标点逐字保留；
- 说话人正确；
- 不改变剧情事实、角色状态、道具状态或空间关系；
- 不遗漏当前 Beat 的核心动作和戏剧意图；
- 不错用相邻 Beat 中尚未发生或已结束的事件；
- 导演计划与最终提示词一致；
- `context_only` 图片不被写成 `<Picture N>`；
- 所有 H3 引用均存在真实资产绑定；
- Codex 未读取白名单外资料；
- 动作、对白和镜头数量在有效时长内可执行。

### 6.2 MiniMax H3 官方规范校验

#### T2VA / I2VA / FL2VA / L2VA

- 三个字段名称和顺序严格正确；
- 无额外 `constraints`、`negative_prompt` 等非官方段落；
- T2VA 不含图片对齐首行；
- I2VA 使用官方 0.00 秒首帧句式；
- FL2VA 同时对齐首帧和尾帧；
- L2VA 将尾帧对齐到有效结束时间；
- 结束时间格式化为两位小数；
- 首行和三个字段之间保留一个空行；
- `[Shot 1]` 不带时间戳；
- `[Shot 2]` 起包含严格递增的切镜时间和明确切换动作；
- 最后一个切镜时间小于总时长；
- 引用标签统一且全部有资产绑定；
- I2VA 从首帧构图、人物、服装和空间锚点继续发展；
- FL2VA 描述首帧到尾帧的连续路径；
- L2VA 逐步收敛到尾帧；
- 说话人 ID 稳定；
- `<d>` 内只有语言标签和原始对白；
- 对白、演唱和有源音乐不重复放入 `overall_soundscape`；
- `overall_soundscape` 为 1–4 句英文；
- `non_diegetic_music` 为 1–3 句英文或 `N/A`；
- 不输出实际反斜杠转义；
- 描述总量与 4–15 秒有效时长匹配。

#### Ref2VA

使用官方六段式顺序：

```text
subject_definitions
summary
retention_analysis
detailed_description
overall_soundscape
non_diegetic_music
```

不得将 Ref2VA 错套为三字段结构。所有人物、场景、图片、视频和音频参考标签在各段中保持一致。

### 6.3 错误分级

确定性检查覆盖 schema、字段顺序、标签、时间、对白、模式和资产绑定；语义检查覆盖剧情忠实度、动作密度、连续性、分镜图理解和导演计划一致性。

- `error`：改变剧情、违背连续性、动作无法在时长内完成、参考图用错；
- `warning`：描述略密、运镜可能不稳定、音乐控制过多；
- `info`：风格性建议。

批量自动可用条件：确定性错误为 0、语义 `error` 为 0，且 `warning` 不超过设置阈值。

## 7. 导演台页面

### 7.1 单 Beat 页面

输入区增加：

- 生成模式：自动、T2VA、I2VA、FL2VA、L2VA、Ref2VA；
- 有效时长；
- 分镜及参考资产用途；
- 上下文预览；
- 输入校验状态和缺失素材说明。

结果区保留现有主要区域：

1. 原始 Beat 信息／输入摘要；
2. 导演计划；
3. 最终提交提示词；
4. 质量报告。

新增 Codex 状态、会话 ID、模型、skill 版本、上下文快照、确认采用、自然语言修改、版本历史、版本对比、恢复和白名单读取记录。

允许人工编辑最终提示词，但编辑必须生成新版本并重新校验，不得绕过质量闸门。

### 7.2 整集批量页面

启动前展示 Beat 数量、模式分布、缺失素材、Codex 运行时、模型、skill 版本、并发数、修订次数、warning 阈值和白名单范围。

运行中支持：

- 显示排队、生成、修订、可用和失败数量；
- 暂停继续派发；
- 取消未开始任务；
- 查看单 Beat 状态；
- 单独重试、选中重生成或转调试会话；
- 按错误代码聚合问题；
- 下载当前批次 manifest。

提示词通过只标记“可用”，不因替换生成器而隐式自动请求 MiniMax 生成视频。

## 8. 设置面板

新增“提示词智能体”设置分组。

### 8.1 运行时

- 默认运行时 Codex；
- 预留 WorkBuddy 适配器；
- Codex 连接方式与可执行环境；
- 默认模型和推理强度；
- 请求超时、基础设施重试次数和批量并发上限；
- 测试连接。

### 8.2 H3 skill

- 官方 MiniMax-H3 仓库来源；
- 当前固定 commit／版本；
- 检查更新；
- 升级并重新验证；
- 批量任务中禁止切换版本。

### 8.3 上下文与权限

- 项目资料白名单；
- 允许的文件类型；
- 单文件和总上下文大小上限；
- 角色库、场景库和道具库的读取权限；
- 最近白名单访问记录。

### 8.4 校验与保留

- 生产批量不可关闭严格 H3 校验；
- 语义 warning 阈值；
- 自动修订默认和上限均为 2；
- 动作密度标准；
- 单 Beat 会话保留期限；
- 生成版本和上下文快照保留策略；
- 日志级别和脱敏策略。

访问令牌和本地认证数据不写入 manifest，不显示在质量报告中。

## 9. Manifest 向后兼容

不删除、不改名现有字段。原始 Beat、`director_plan`、`final_prompt` 和 `quality_report` 继续保留在旧读取路径，新信息追加到带版本的 `agent_generation` 扩展区。

```json
{
  "director_plan": {},
  "final_prompt": "",
  "quality_report": {},
  "agent_generation": {
    "schema_version": "1.0",
    "runtime": {
      "provider": "codex",
      "model": "",
      "reasoning_effort": "",
      "session_id": null
    },
    "skill": {
      "name": "h3-prompt-writing",
      "source": "MiniMax-AI/MiniMax-H3",
      "commit": ""
    },
    "input": {
      "requested_mode": "fl2va",
      "resolved_mode": "fl2va",
      "context_snapshot_id": "",
      "reference_bindings": []
    },
    "result": {
      "selected_version": 2,
      "validation_status": "passed",
      "revision_count": 1
    },
    "versions": []
  }
}
```

未使用 Codex 的旧 manifest 仍可读取；新 manifest 被旧版导演台打开时，至少可继续显示旧字段。本地绝对路径尽量转换为项目相对资产 ID。

## 10. 失败恢复

每个 Beat 任务持久化当前状态、输入快照、Codex 会话或任务标识、当前版本、自动修订次数、最近错误、幂等键和配置快照。

服务重启后：

- 恢复排队任务；
- 不盲目重复创建未确认是否已提交的运行时请求；
- 无法确认的运行中任务转为“需要确认”；
- 已通过校验的结果保持可用；
- 单 Beat 持久会话可继续对话；
- 批量失败项可单独恢复。

取消不删除已完成结果。正在运行且无法中止的迟到结果标记为已取消任务的孤立结果，不自动采用。

## 11. 安全边界

提交给 Codex 的 Beat、剧本、项目资料、图片文字和元数据全部视为数据，不视为运行指令。其中即使出现“读取文件”“忽略规则”或“访问 URL”等文本，也只能作为剧情素材处理。

运行时必须：

- 只访问导演台打包数据和白名单文件；
- 不自行遍历项目或用户目录；
- 不执行项目资料中包含的命令；
- 不根据素材 URL 自动联网；
- 将图片 OCR 结果同样视为不可信数据；
- 默认不允许写项目文件；
- 不获得视频提交、费用消耗或发布权限；
- 对日志中的令牌、认证头和敏感路径脱敏；
- 先校验返回 JSON schema，再进入页面渲染；
- 页面按纯文本显示提示词和错误。

## 12. 上线策略

1. **影子对比**：新旧生成器同时运行，但不自动采用新结果，对比模式判定、对白保真、时长和 H3 合规率。
2. **单 Beat 主用**：Codex 接管单 Beat，所有结果人工确认，验证持久会话、版本和自动修订。
3. **整集批量主用**：开放批量自动采用，旧生成器仅保留管理员回滚开关，稳定后移除。

旧生成器不得在 Codex 失败时静默接管，避免同一批次混入两套逻辑。

## 13. 验收标准

1. 无图片且未指定模式时推断 T2VA。
2. 一张首帧推断 I2VA。
3. 首帧和尾帧推断 FL2VA。
4. 指定 FL2VA 但仅有首帧时，在调用 Codex 前停止。
5. `5.166666...` 秒在 FL2VA 尾帧对齐中格式化为 `5.17` 秒。
6. `context_only` 图片可供理解，但不生成 `<Picture N>`。
7. 中文对白及标点与输入逐字一致。
8. 一个连续运镜不被错误拆成无时间戳的后续 Shot。
9. 真正的后续 Shot 包含递增切镜时间。
10. 声音字段不重复对白，音乐字段不混入有源音乐。
11. Ref2VA 使用六段式结构。
12. 校验失败后自动修订最多两次。
13. 自动修订不得改变模式、对白、时长或资产绑定。
14. 单 Beat 修改意见在同一 Codex 会话继续。
15. 批量中每个 Beat 会话隔离，单个失败不影响其他结果。
16. 批量启动后更改设置不影响已冻结任务。
17. 服务重启后任务和版本可恢复。
18. 新 manifest 可被旧读取逻辑打开。
19. 白名单外读取请求被拒绝并记录。
20. 导演台标记 FL2VA，却仅生成 I2VA 首帧格式时，必须校验失败。

## 14. 实现前置检查

在编写实现计划或修改业务代码前，必须先完成：

1. 找到导演台现有提示词生成入口、请求模型和返回模型；
2. 找到单 Beat、批量和 manifest 下载的完整调用链；
3. 固定并本地化官方 H3 skill 版本；
4. 建立旧 manifest 样本和契约测试；
5. 确认 Codex 在目标运行环境中的任务、持久会话、附件和取消能力；
6. 明确资产 ID 与 H3 `<Picture N>` / `<Video N>` / `<Audio N>` 的映射位置；
7. 以现有错配案例建立回归测试：导演台为 FL2VA，实际只有 I2VA 首帧提示词。

