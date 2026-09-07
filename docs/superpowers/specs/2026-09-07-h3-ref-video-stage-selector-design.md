# H3 Ref 视频生成卡片选择器设计

## 背景

叙事组页面已经支持 `runninghub:minimax-h3` 和 `runninghub:minimax-h3-ref` 两个工作流，但模型选择器位于页面顶部。用户滚动到「视频生成」卡片后，看不到当前区域的切换入口。

前端当前还会通过 `availableVideoModels` 删除不可用模型。H3 Ref 在真实 RunningHub 混合输入完成验证前被后端标记为 `hybrid_input_unverified`，因此它不会出现在现有选择器中。

## 目标

- 在「视频生成」卡片标题栏直接展示视频模型选择器。
- 同时展示 Base H3 和 H3 Ref，使用户能看到系统具备的两种工作流。
- Base H3 继续作为项目默认模型。
- H3 Ref 未完成真实混合输入验证时保持不可选择，并显示不可用原因。
- 后端将 H3 Ref 标记为可用后，同一控件自动允许切换，并沿用现有项目默认、叙事组设置和参考图管理流程。

## 方案

`NarrativeGroupWorkbench` 继续负责模型目录、当前选择和保存逻辑。它把完整模型目录、保存状态和 `changeVideoModel` 回调传给 `GroupVideoStage`。

`GroupVideoStage` 在标题栏展示紧凑选择器：

- 可用模型正常展示并可选择。
- 不可用模型保留在列表中，但禁用。
- 不可用条目显示「未验证」或后端提供的明确原因。
- 只有一个目录项时也保留控件，避免用户误以为系统只有一种能力。
- 切换期间禁用控件并显示保存状态。

页面顶部不再重复展示同一个视频模型选择器，避免两个入口造成层级和状态歧义。

## 数据与安全边界

UI 不修改模型可用性，也不绕过 `available` 检查。H3 Ref 的 `hybrid_input_unverified` 门禁仍由后端 workflow registry 控制。选择动作继续调用现有的项目默认设置和叙事组视频设置更新接口；更新失败时恢复原选择并显示错误。

## 测试

- 卡片在 Base H3 为默认时同时展示 Base 和禁用的 H3 Ref。
- 不可用 H3 Ref 无法触发 `onModelChange`，且展示原因。
- 后端返回可用 H3 Ref 时，用户能在卡片内切换并触发现有保存流程。
- 保存中和视频任务运行中禁用选择器。
- 原 Base H3 生成请求和 H3 Ref 参考图生成守卫测试继续通过。

## 非目标

- 本次不执行付费 RunningHub 烟测。
- 本次不解除 `hybrid_input_unverified`。
- 本次不改变 RunningHub payload、参考图快照或任务恢复逻辑。
