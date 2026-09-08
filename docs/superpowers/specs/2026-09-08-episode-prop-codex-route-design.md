# “规划道具”使用 Codex Agent 路由设计

## 目标

“规划道具”必须通过后台任务入队时冻结的 Codex Agent 路由执行结构化道具分析，不再读取、校验或回退到普通文本模型 API Key。

## 根因

`episode_prop_planner` 当前没有注册 `text_task_role`，因此任务执行期间不存在 `current_text_task_runtime()`。与此同时，`AssetCompiler._analyze_block_props()` 直接调用 `get_newapi_text_pydantic_model()`，使道具规划错误地依赖普通文本模型配置。

## 设计

- 新增专用任务角色 `episode_asset_planning`，设置页显示为“场景/道具规划”。其默认路由为 `codex / gpt-5.6-sol / low / stop`。
- `episode_scene_planner` 与 `episode_prop_planner` 注册到该角色。任务入队时沿用现有机制解析并冻结路由快照，执行时由 `run_core` 建立并发安全的 runtime scope。
- `AssetCompiler` 在场景和道具的结构化 Agent 调用处优先使用 scope 中的 `StructuredRuntimeAgent`。对于已注册的后台规划任务，缺失 runtime 视为配置/执行错误，不允许静默切回普通文本模型。
- `StructuredRuntimeAgent` 支持传递 Pydantic 校验上下文，使现有 `BlockPropRequirements` 的文本证据校验保持不变。

## 错误处理

- Codex CLI 不可用、路由无效或结构化输出失败时，任务直接失败并保留可诊断错误。
- 普通文本 API Key 是否配置不再影响“规划道具”。
- 不增加自动 fallback，避免在用户不知情时切换计费渠道或模型语义。

## 验证

- 路由配置接口返回专用角色及 Codex 默认值。
- 入队的 `episode_prop_planner` 包含冻结的 `episode_asset_planning` Codex 快照。
- 在普通文本 Key 为空的测试环境中，道具块分析仍通过注入的 Codex runtime 返回并完成校验。
- 运行相关后端测试及静态检查。
