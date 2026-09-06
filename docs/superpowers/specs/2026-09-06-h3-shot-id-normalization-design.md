# H3 镜头编号规范化设计

## 问题

`narrative_group_video` 的 H3 episode pack 输入包含外层业务镜头 ID（例如 `shot-01`），而 `H3DirectorPlan.shots[].shot_id` 要求每个 segment 内使用从字符串 `"1"` 开始的连续编号。当前提示词没有明确区分两者，Codex 会复制业务 ID，经过三次结构化输出重试后仍被 Pydantic 的连续编号校验拒绝。

## 目标

- 明确外层业务镜头 ID 与 H3 内部镜头序号的不同语义。
- 接受镜头顺序及其他字段合法、但内部 `shot_id` 使用业务 ID 的 Codex 结果。
- 保持外层 `H3EpisodeVideoSegment.shot_ids`、镜头顺序以及所有非 `shot_id` 字段不变。
- 保留 `H3DirectorPlan` 的严格模型校验，避免放宽持久化和下游契约。

## 方案

在 episode pack 的提示词中明确要求每个 `director_plan.shots` 独立使用 `"1"`、`"2"`、`"3"` 连续编号，不得复制外层 `shot_ids`。

在 Codex 输出进入 `H3EpisodePromptPack` 严格校验前，对普通 JSON 对象执行窄范围预处理：仅遍历 `segments[].director_plan.shots`，按现有数组顺序覆盖 `shot_id`。不新增、删除或重排镜头；非映射结构继续交给 Pydantic 拒绝。规范化后的数据仍必须通过全部帧覆盖、动作、对话、模式和质量校验。

该预处理放在 H3 episode pack 边界，而不是通用 Codex runtime 中，避免改变其他结构化任务的语义。

## 测试

- 回归测试模拟 Codex 返回 `shot-01` 等业务 ID，验证 optimizer 能成功生成结果，并且内部 ID 变成连续字符串。
- 测试提示词明确包含禁止复制业务 ID 的规则。
- 测试非 ID 结构错误仍会被严格 Schema 拒绝。
- 运行 H3 episode pack、director plan 和 narrative group video 相关测试。

## 非目标

- 不修改外层业务镜头 ID。
- 不放宽 `H3DirectorPlan` 的连续编号要求。
- 不自动修复帧区间、镜头顺序、缺失字段或其他无效模型输出。
