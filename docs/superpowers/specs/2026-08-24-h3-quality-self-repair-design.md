# H3 导演计划质量自修复设计

## 目标

消除 DeepSeek 使用中文描述或产生轻微帧边界偏差时的误拒绝，同时保持 RunningHub 调用前的严格质量门，避免把不可用提示词送入付费视频工作流。

## 已确认根因

1. `incomplete_action_detail` 的多步骤判定只统计英文单词，中文动作描述被计为零。
2. `action_timeline_gap` 属于机械帧边界错误，可以确定性修正，但当前直接失败。
3. 其他语义质量错误只被拒绝，没有反馈给 DeepSeek 修稿，导致整个叙事组在第一次导演输出不合格时终止。

## 方案

采用混合自修复：

- 质量门使用语言无关的内容长度和中英文分句信号识别详细动作，短而空泛的动作仍拒绝。
- 对每个 shot 的 action 时间轴做确定性归一化：保持动作顺序与各动作结束帧，将首动作起点对齐 shot 起点，后续动作起点对齐前一动作终点，末动作终点对齐 shot 终点。若归一化会产生零长度或逆序区间，则不修改并交由质量门拒绝。
- 归一化后仍有语义错误时，把结构化质量报告附加到原始导演任务，要求 DeepSeek 只修正报告中的问题并返回完整 `H3DirectorPlan`。
- 最多进行 2 次质量修稿（首次生成之外），次数通过 `DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS` 配置。
- 每次候选计划都必须重新执行完整质量门；只有通过后才编译提示词、写缓存并调用 RunningHub。
- 最终失败继续抛出 `H3PromptQualityError`，并保持 `transport_called=false`。

## 数据流

`原始导演任务 → DeepSeek 候选计划 → 时间轴归一化 → 质量门 → 通过并编译`。

若质量门失败且还有额度：`质量报告 → DeepSeek 修稿 → 再归一化 → 再质检`。耗尽额度后失败，不进入 RunningHub。

## 可观测性

- manifest 保留最终通过的 `director_plan`、`quality_report` 和实际 `segment.prompt`。
- 最终拒绝继续记录最后一次质量报告，任务中心显示具体 issue。
- DeepSeek 修稿次数不等于 RunningHub 调用次数；RunningHub 始终只在质量通过后调用一次。

## 测试与验收

- 单元测试覆盖中文详细动作通过、中文短动作拒绝。
- 单元测试覆盖机械时间轴归一化。
- 优化器测试覆盖首次失败、质量反馈修稿后通过，以及修稿耗尽后仍拒绝且不缓存。
- 回归运行 H3 quality、optimizer、narrative-group runner 测试。
- 重启真实后端后重试一个此前失败的叙事组，确认 DeepSeek 与 RunningHub 均真实调用，并用 `ffprobe` 验证输出 MP4。

