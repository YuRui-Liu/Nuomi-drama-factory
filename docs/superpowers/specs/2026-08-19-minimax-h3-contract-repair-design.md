# MiniMax H3 导演台契约修复设计

## 背景

叙事组视频任务在提示词优化阶段报错：`dialogue is required for a segment with dialogue intent`。真实项目 Beat 使用 `narration_segment + audio_type + speaker` 表达对白，现有叙事组与单镜头 H3 入口却只读取测试数据里的 `dialogue/line`，导致对白意图存在但对白正文为空。

进一步对照仓库外已提供的 `MiniMax H3 导演台全能工作流_api.json` 与 MiniMax H3 官方提示词规范，发现两个下游契约也不完整：提示词使用自定义格式而非 H3 官方 I2VA/FL2VA 格式；RunningHub profile 只覆盖节点 12 的 `timeline_data`，没有同步 `task_type`、画幅尺寸、总帧数、帧率等顶层输入。

## 目标

一次性修复从标准 Beat 到 RunningHub 提交的完整数据链：

1. 标准 Beat 对白可被叙事组与单镜头 H3 入口一致识别。
2. H3 提示词遵循官方帧对齐、结构字段和对白标签格式。
3. RunningHub 节点 12 的动态参数与 timeline 始终一致。
4. 画幅比例保持请求级可配置，`9:16` 只是默认值。
5. 用真实契约形状的 fixture 和回归测试锁定行为。

## 设计

### 1. 标准 Beat 适配层

新增共享的 H3 Beat 适配函数，按以下优先级读取：

- `audio_type == dialogue` 时正文取 `narration_segment`，并兼容旧数据 `dialogue/line`。
- 非对白类型不把 `narration_segment` 误当对白。
- 说话人取 `speaker`，兼容旧字段。
- 对白意图由规范化后的 `audio_type` 决定。
- `tone` 为可选元数据，不再作为阻断生成的必填项。

叙事组与单镜头 runner 共用该适配层，避免两条路径再次分叉。

### 2. 官方 H3 提示词

渲染器输出：

- I2VA 使用官方首帧引用句。
- FL2VA 使用官方首尾帧对齐句。
- 主体字段固定为 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。
- 对白由程序在优化结果后注入，使用稳定说话人 ID 与 `<d>[Chinese]原文</d>`，确保模型不能改写或遗漏原台词。
- `tone` 缺失时允许生成，由上下文描述自然推断表演方式。

### 3. RunningHub 导演台参数同步

以用户提供的真实 API JSON 中节点 12 `MiniMaxH3Director` 为契约来源。profile 增加动态绑定：

- `task_type`
- `global_prompt`
- `frame_rate`
- `width`
- `height`
- `ref_max_size`
- `total_frames`
- `timeline_data`

timeline 的段落时长保留用户请求的名义秒数，帧数按 H3/工作流规则对齐；节点顶层总帧数、尺寸和 timeline output 必须相同。参考图对象补充真实宽高信息。

### 4. 可配置画幅

API 请求显式接收并透传 `aspect_ratio` 与 `resolution`。默认仍为 `9:16`，但 `3:4`、`16:9` 等受支持比例必须根据请求动态计算 `width/height/ref_max_size`，不得在 runner 或 profile 中硬编码为竖屏尺寸。

### 5. 失败策略

- 对白意图存在但正文确实为空：在调用优化器前给出包含 Beat/segment 上下文的明确错误。
- 对白存在但说话人缺失：保持明确校验，避免生成不可控多角色口型。
- 单段时长不满足 H3 的 4–15 秒：在序列化边界规范化或拒绝，并通过测试固定选择。
- RunningHub profile 缺少必需绑定：本地契约测试失败，不把不一致请求发到远端。

## 验证

- 标准生产 Beat 形状的 runner 回归测试。
- 官方 I2VA/FL2VA 与 `<d>` 对白格式测试。
- 真实节点 12 fixture 的多段 timeline 与顶层输入一致性测试。
- 至少覆盖 `9:16` 和一个非默认比例，证明画幅可配置。
- 聚焦测试、相关媒体能力测试、ruff 和 diff 检查。

