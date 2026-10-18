# 真实 Agent 协作与画布接通实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务执行；各任务完成后依次进行规格与代码质量审查。按复选框记录实际完成，不能以接口存在代替真实验收。

**目标：** 贯通按需多 Agent 群聊、构思资产出图和对应集导演空间，并由产品入口交付真实整集。

**架构：** 复用已有 AgentRuns 子委托、领域操作与持久化投影，补齐用户入口、成果关系和作用域。旧整批导演生产不得作为新 Agent 工具；直接操作和 Agent 使用相同受控节点能力。

**技术栈：** Python / FastAPI / SQLite，React / TypeScript / ReactFlow，pytest / Vitest，Codex 文本、Grsai 图像、MiniMax 导演台视频。

**批准规格：** `docs/superpowers/specs/2026-10-18-agent-collaboration-canvas-integration-design.md`，提交 `0c85e8d`。用户已批准子代理协作方式，不再询问执行模式。

## 工作区与所有权

当前 `feat/canvas` 含前轮合并、用户和其他任务未提交修改；不整体暂存、不重置，不把未提交前置实现遗漏到空 worktree。继续当前已建立的工作区，每个任务明确文件所有权；只提交可精确隔离的变更。API 无自动重载，有真实生成任务时禁止重启。

新增专用模块承载作用域、群聊呈现和资产绑定合同，避免继续把所有逻辑堆入大组件。保留真实调用记录及失败现场。Weaver 按实际 Git 根执行；使用 apply_patch 的内容修改由既有 hook 记录。

## 任务 1：权威画布作用域（先执行）

**修改：** `src/novelvideo/creation/canvas_workspaces.py`、`src/novelvideo/api/routes/freezone_orchestration.py`、`frontend/src/features/creation-workspace/contracts.ts`、`CreationWorkspace.tsx`、`frontend/src/features/freezone/FreezoneShell.tsx`、`frontend/src/features/canvas/nodes/DirectorWorkspaceNode.tsx`。

**新增：** `src/novelvideo/creation/canvas_scope.py`、`frontend/src/features/creation-workspace/WorkspaceScope.tsx`。

**测试：** `tests/creation/test_canvas_workspaces.py`、`tests/freezone/orchestration/test_production_graph.py`、`frontend/src/__tests__/features/creation-workspace/workspace.test.tsx`、新增 `frontend/src/__tests__/features/canvas/director-workspace-scope.test.tsx`。

- [ ] 在现有 workspace fixture 中创建 E01、E02 与 free，断言 registry 返回权威 `episode_number`，非集画布为 null；节点数据不能覆盖该值。
- [ ] 运行 `.venv/bin/pytest -q tests/creation/test_canvas_workspaces.py tests/freezone/orchestration/test_production_graph.py`，确认新增断言失败。
- [ ] 从 creation objects 当前 episode payload 解析 number，关联 episode_id 与 canvas_id；不可从标题、node.data 或 metadata 猜测。读接口不初始化或修复数据库。
- [ ] 增加服务端作用域校验函数，供生产图及分集节点执行准入使用；校验项目、注册状态、kind、episode_id、number。错误画布拒绝且无投影、无生成。
- [ ] 在 `freezone/orchestration/store.py:RunStore.create` 的可信 canvas snapshot 上校验所有将执行的分集节点（screenplay/director/narrativeGroup/episodeCompose），不能只保护 production-graph 路由。完整剧本草案在构思画布仍可作为文本创作，不默认调用单集脚本操作。

```ts
export interface WorkspaceScope {
  projectId: string;
  canvasId: string;
  kind: 'concept' | 'episode' | 'free';
  episodeId: string | null;
  episodeNumber: number | null;
}
```

- [ ] 将上述不可变 scope 从 CreationWorkspace 传入 FreezoneShell 并提供给节点；切换画布重新建立 scope，不能用旧请求结果覆盖新 scope。
- [ ] 导演节点只在合法分集 scope 内提供编辑；移除 `episodeNumber ?? 1`。构思／自由画布中的旧导演节点保留内容并显示明确定位／待关联提示；不禁用普通图像、视频或自由创作节点。
- [ ] 对 scope 不匹配的 production-graph POST 添加回归：409 且画布节点数不变；合法 E02 只使用 E02。
- [ ] 运行上述 pytest，以及 `cd frontend && pnpm exec vitest run src/__tests__/features/creation-workspace/workspace.test.tsx src/__tests__/features/canvas/director-workspace-scope.test.tsx` 和 `pnpm exec tsc --noEmit`；审查通过后记录变更。

## 任务 2：真实子委托与群聊记录

**现有边界：** `creation/agent_model.py:Decision.delegations`、`agent_runs.py:drive` 已有子委托与 pending 队列，禁止再造替代执行器。

**文件：** `src/novelvideo/creation/discussions.py`、`agent_runs.py`、`delegations.py`、`api/routes/creation_library.py`；`frontend/src/api/agentDiscussions.ts`、`api/agentRuns.ts`、`features/agent-runtime/useAgentRuntime.ts`、`ManagedChatPanel.tsx`。新增专用群聊记录模块与展示组件，具体字段以现有持久化身份关联。

新增 `src/novelvideo/creation/collaboration.py`、`collaboration_store.py`、`src/novelvideo/api/routes/creation_collaboration.py`、`frontend/src/api/collaboration.ts`、`frontend/src/features/agent-runtime/CollaborationTaskCard.tsx`；测试为 `tests/creation/test_collaboration_service.py`、`test_collaboration_api.py`。由明确的会话请求触发服务，不建立全员模型轮询。

持久化合同：`conversation_members` 记录 project/canvas/conversation、adoption 与 pinned revision；`collaboration_events` 用 request/source_event/target 身份去重；`collaboration_tasks` 记录 parent/dependencies、冻结输入、delegation/run ID、状态及 output refs。既有 outputs 和 receipts 仍是成果真相，不复制媒体或另存正文。普通讨论解析主创角色但保持 tool-free；明确创作委托走 preview/授权/执行桥接，@ 专家严格校验当前成员。

既有 pending child 队列是串行聚合，不冒充并行 DAG。独立专业工作建立有父授权链的独立 delegation/run，依赖满足后准入；每个独立任务保留自己的租约和未知提交状态，父任务汇总真实子结果。用共享授权上界约束独立子 run，不能通过拆任务扩大预算。

- [ ] 用现有 agent_runs fixture 构造主创与两个不同专业 adoption，Decision 返回两个有界子委托；断言真实分别调用其配置，输出可追溯到子委托，第三个无关 adoption 无调用。
- [ ] 运行 `.venv/bin/pytest -q tests/creation/test_agent_runs.py tests/creation/test_discussions.py`，检查新增行为失败点，不修改已有停机与未知提交预期。
- [ ] 群聊消息绑定 speaker adoption/config revision、trigger identity、parent task/delegation、canvas/conversation；读取真实运行和工具回执生成任务视图，不合成专家发言。
- [ ] 用户点名请求显式携带目标 adoption 并检查当前项目成员；普通消息交主创，任务相关唤起使用明确交接事件及幂等身份。成员常驻不创建轮询模型任务。
- [ ] 保留子任务已有 grant/budget 上界与来源版本；专家邀请通过主创已有决策产生，重复事件复用同一记录。主创完成摘要不能覆盖子任务未满足项。
- [ ] 前端展示成员、真实负责人及交付定位；选中节点／内容组冻结为委托范围。群聊、草稿和任务按 canvas/conversation 隔离。
- [ ] 验证点名仅调用目标、普通消息不广播、重复交接不重跑、切换画布不串消息；运行相关 pytest 与 agent-runtime Vitest，再做两阶段审查。

## 任务 3：构思资产设计到出图节点

**文件：** `src/novelvideo/creation/agent_tools.py`、`delegations.py`、`node_execution.py`、`projection.py`、`asset_reference.py`；`frontend/src/features/creation-workspace/contracts.ts`、`FixedAssetLibrary.tsx`、现有图像生成节点及注册表。新增 `creation/asset_generation.py` 承载设计对象／基础资产版本与出图节点的绑定操作。

- [ ] 新增 `tests/creation/test_asset_generation.py`：新角色尚无图片也能由设计对象建立出图节点；已有变体固定基础资产版本；重复操作只创建一个节点且不提交模型。
- [ ] 执行 `.venv/bin/pytest -q tests/creation/test_asset_generation.py` 确认失败，再接入已有 Operation / outbox / projection 合同，禁止直接写 canvas JSON。
- [ ] 新建资产生成绑定保存设计对象及版本、可选基础／变体引用、目标画布、输入节点、风格引用和操作身份；服务端生成 node ID，模型不得传任意图写入。
- [ ] 资产设计专家可提交该准备操作；直接节点菜单和资产入口复用它。允许新对象的 draft 来源，不能强制要求已有 asset_ref 或按同名匹配身份。
- [ ] 出图执行复用正式图像／角色／场景能力；使用真实保存配置、固定输入版本和幂等请求，结果投影到原节点。普通节点生成不自动发布共享资产。
- [ ] UI 展示设定、模型、有效风格、参考、候选和生成动作；已有基础资产的“生成变体”建立绑定节点。发布沿用明确保存合同。
- [ ] 用真实模型替身只验证合同，用真实 Grsai 验证出图；分别测试生成失败、未知请求、投影失败恢复和人工输入冲突，不能通过重跑模型修复落图。

## 任务 4：分集交接与导演专业协作

**文件：** `src/novelvideo/creation/script_handoff.py`、`workspace.py`、`ideation_service.py`、`professional_agents.py`、`node_execution.py`、已有导演 handoff 路由；`frontend/src/features/canvas/production/ideation/useIdeationWorkspace.ts`、`nodes/ScreenplayNode.tsx`、`nodes/DirectorWorkspaceNode.tsx`、导演工作区组件。

- [ ] 用真实 adopted screenplay fixture 测试交接创建／复用相同 number 的集对象与主画布，来源保留；重复请求不创建第二集或第二导演编辑器。
- [ ] 复用 ScriptHandoffCanvas 的事务与 outbox，将专业剧本、导演编辑视图投影到返回的集画布；源构思画布仅增加固定来源引用及可导航回执。
- [ ] 剧本交接 UI 导航到服务返回的画布，不用当前画布自行追加 episodeNumber=1 节点；授权运行也必须使用该回执。
- [ ] 主创到导演、导演到所需专家使用任务 2 子委托；每项贡献绑定当前导演输入及专业字段，采用冲突由导演合并候选，不用单次通用模型结果伪造专业调用。
- [ ] 既有错集节点保留并提供显式关联／定位；禁止静默删除和按标题迁移。自由作品保留无分集作用域，不强制造 E01。
- [ ] 测试跨集编辑拒绝、旧版本交接冲突、重复回执与恢复；执行 screenplay/handoff、crew integration 和导演节点的定向测试。

## 任务 5：新链路制作依赖与最终集成

**文件：** `freezone/orchestration/adapters/narrative_groups.py`、`production_graph.py`、`task_backend/runners/narrative_group_video.py`、组视频正式 API 与模型、导演版本编辑服务、前端对应节点。

- [ ] 将真实生成前的风格快照差异和原生／外配音策略纳入固定输入，测试旧已采用版本不被项目配置静默改写。
- [ ] MiniMax 原生音频策略在正式请求、提交指纹与 manifest 中一致；显式外配保留依赖，缺声不宣称可合成。旧请求省略新字段维持既有行为，已完成视频复用必须核对策略。
- [ ] 给导演风格更新提供受审查的新版本操作，保留来源／镜头／时长；所有 group snapshot 与 revision snapshot 一致，不能直接篡改采用文件。
- [ ] 制作由真实专业任务调用组图／组视频节点，导出为独立动作。禁止 `director_production` → `production_batch.run_batch` 从新入口被调用；集成测试将该函数设为失败哨兵。
- [ ] 运行 creation、orchestration、组媒体及前端相关测试；修复实际失败后独立复审，不把已有无关失败算为本任务通过。

## 任务 6：产品入口真实验收与交付

- [ ] 新建验收项目，先确认保存的现代风格、Codex、Grsai、MiniMax 配置及当前并发；不改其他项目，不重新索取密钥。
- [ ] 从构思画布群聊发出包含人物及场景设计的目标，核对至少两类必要专业工作的真实调用和成果节点；不手工 API 补齐本应由产品生成的节点。
- [ ] 从构思节点生成、比较并保存人物／场景素材；通过产品入口交接到 E01，对应集导演真实调度并形成组分镜。
- [ ] 选中局部内容委托专家，验证范围与候选；刷新、切换、重放、停止按已批准合同验证。
- [ ] 正式生成组图、MiniMax 组视频，检查实际图像、对白、时长与引用；正式导出约 90 秒整集，ffprobe 检查音视频流并实际播放。
- [ ] 更新 `docs/acceptance/2026-10-18-agent-canvas.md`，记录真实 ID、调用路径、画布归属、成片绝对路径、时长与质量结论；未完成不标通过。
- [ ] 最终规格与质量审查后交付代码和可播放文件，不以模拟测试代替影片。

## 自检与依赖

规格第 2 节由任务 2 / 4 覆盖；第 3 节由任务 1 / 2 覆盖；第 4 节由任务 3 覆盖；第 5 节由任务 1 / 4 覆盖；第 6–7 节由任务 2–5 覆盖；第 8 节由任务 6 覆盖。先固定作用域再做跨画布写入；先有真实委托再验收专业协作；生成费用只在正式任务中发生。
