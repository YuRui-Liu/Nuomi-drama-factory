# “规划道具”仅链接现有资产设计

## 目标

“规划道具”只把剧本中明确出现的现有道具资产链接到本集、叙事组和镜头，不提取或创建任何新道具。

## 行为边界

- 唯一候选集是项目资产库 `list_props()` 返回的现有道具。
- 正式名称或别名在场景块文本中明确出现时视为命中；匹配沿用现有标点、空格和大小写归一化。
- 同一现有道具在一集内只进入一次 `prop_menu`，但 planned bindings 仍按导演方案投影到对应范围。
- 未命中的普通物件直接忽略；零命中是成功结果，不报错。
- `draft.props` 恒为空，不调用 `add_prop()`，不运行自动晋升服务。
- `episode_prop_planner` 不注册文本 Agent 角色、不调用 Codex 或普通文本模型；场景规划保持现有 Agent 路由。

## 兼容性

- 保留 `PropPlanDraft`、`prop_menu`、原子发布和结果字段形状。
- `auto_promoted_props` 暂时保留为空数组，避免前端或调用方解析失败。
- 已存在但未在当前剧本明确出现的资产不会被自动关联。

## 验证

- 覆盖正式名称命中、别名命中、跨场景去重和零命中。
- 测试明确禁止 `_analyze_block_props()`、`add_prop()` 和 `promote_episode_props_to_global()` 被调用。
- 验证道具任务不再包含 `agent_route_snapshot`，场景任务仍使用现有路由。
