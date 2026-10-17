# DramaClaw 与 OpenMontage：Agent、剧本与画布代码证据

**观察日期：2026-10-16。** 本次读取 GitHub 仓库页、固定 commit 下的源码、schema、skills、文档和测试文件；未安装依赖、未运行测试、未启动 demo，也未触发模型或媒体生成。文中分别标注源码核实、文档声明和未验证项。

DramaClaw main commit 为 a627206b3919f78769bd5efe4e6b3cd3946eeb93，时间 2026-10-16 09:51:52Z；OpenMontage main commit 为 e45f5b12bd46440b04f3675af9a77d0718e6a254，时间 2026-10-12 17:48:52 +0100。[DramaClaw commit](https://github.com/dramaclaw/dramaclaw/commit/a627206b3919f78769bd5efe4e6b3cd3946eeb93) · [OpenMontage commit](https://github.com/calesthio/OpenMontage/commit/e45f5b12bd46440b04f3675af9a77d0718e6a254)

## DramaClaw

### 仓库状态

GitHub 页面在观察日显示 DramaClaw 6,315 stars、858 forks；README 声明 Elastic License 2.0。页面最新 release 为 v2.0.6（2026-09-29），而本次检查的 main 已到 2026-10-16；Agent Workflow 证据属于 main 源码，不能据此保证 v2.0.6 安装包包含相同实现。[仓库](https://github.com/dramaclaw/dramaclaw) · [license](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/LICENSES/Elastic-2.0.txt) · [releases](https://github.com/dramaclaw/dramaclaw/releases)

### Agent 到可编辑画布

**源码核实：** Freezone 画布是按 project 和 canvas 划分的 JSON 图，载荷包含 schema_version、canvas_id、project_id、revision、nodes、edges、viewport 和 metadata。稳定工作流契约给 Agent 使用的节点包括 scriptNode、beatContextNode、imageGenNode、videoNode、audioNode、videoComposeNode、textAnnotationNode、skillNode 等；边分为 context_for、prompt_for、dependency_for、media_input_for、derived_from 和 composition_input_for。[默认画布载荷](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/canvas_store.py#L184) · [工作流稳定契约](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/schemas/workflow/v1/stable-contract.json#L1)

Agent 的可追踪交接不是单条聊天消息：它生成 workflow intent、compiled plan 和 preview，存成 SQLite workflow_drafts 记录。draft_id 形如 workflow_draft_<uuid>，记录 project_id、canvas_id、revision、skill_id、plan_digest、status、operation_id、task_id 和 root_task_id。草稿修改必须带 expected_revision，成功后递增 revision 并重置确认状态；确认操作原子地将状态从 ready 改为 confirming，随后绑定 task_id/root_task_id，防止重复创建节点。[草稿结构与创建](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/workflow_drafts.py#L238) · [修改与 revision](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/workflow_drafts.py#L410) · [确认和 task 绑定](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/workflow_drafts.py#L519)

执行层另存 workflow_runs：run_id、project_id、canvas_id、幂等键和请求 fingerprint；actions 按 node_id/action 跟踪，并关联 task、recipe 版本、generation attempt、product operation 与 retry_count。runner lease 防止同一 canvas 并发执行；相同幂等键和相同请求复用已有 run，不同请求则报冲突。[run 创建和幂等](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/workflow_runs.py#L998) · [前端 action 状态与重试字段](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/frontend/src/api/canvas.ts#L113)

**Agent Skill 声明：** 一般工作流路径为 catalog 搜索、准备或修订 plan 草稿、确认、一次性画布写入、执行或恢复。Skill 规定编译器分配节点 ID 和批内引用，阻止多节点工作流退化为重复单节点写入；新图片/视频生成前先收集当次参数，且相同 payload 连续失败后修正一次仍失败就停止，避免无意义重试。[Workflow Skill](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/agent-kit/skills/dramaclaw-workflows/SKILL.md#L8) · [工作流写入规则](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/agent-kit/skills/dramaclaw-workflows/SKILL.md#L54) · [参数与确认规约](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/agent-kit/skills/dramaclaw-workflows/SKILL.md#L94)

### Agent 目录、工具与作用域

**源码核实：** agent catalog 实际是 skills 和 recipes 配置库，而不是多名具有人格、职责和独立工具授权的自治 Agent 花名册。Skill schema 提供名称、分类、触发词和 node scopes、输入参数、allowed_recipe_ids、planning notes、conduct rules、评估标准和质量阈值；Recipe schema 提供 output_kind、action_keys、提示词、必备内容、冲突项和媒体前置条件。[Skill/Recipe schema](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/agent_catalog_schema.py#L67)

目录组合内置 JSON、项目目录和用户账户级 JSON；用户可以保存、隐藏或删除配置。面向用户的主 Agent 是 Xia Director。Workflow MCP 负责 catalog 查询与确定性编译，DramaClaw MCP 负责草稿持久化、确认、画布写入和执行；调用受 project/canvas 上下文约束。没有发现 schema 为每个 Agent 单独配置模型、工具白名单或能力沙箱的字段，因此不应称为完整的专业角色 Agent 平台。[配置存储](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/agent_config_store.py#L17) · [Workflow MCP](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/chat/workflow_mcp.py#L340) · [聊天工具上下文策略](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/chat/tool_policy.py#L8)

### 结果晋升、保存与编辑

**源码核实：** 单项 Commit 可以先预览目标影响，再将选中画布结果推到主线的明确槽位，例如 beat frame/sketch、角色身份/肖像、场景资产或音视频。批量 Commit 根据节点 provenance 回到来源槽位；没有来源信息的项跳过。逐项失败可单独重试。[Commit 边界](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/frontend/src/features/freezone/commit/promoteToAsset.ts#L15) · [单项目标选择](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/frontend/src/features/freezone/commit/CommitDialog.tsx#L51) · [批量 provenance 与重试](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/frontend/src/features/freezone/commit/BatchCommitDialog.tsx#L23)

画布保存要求 base_revision；旧 revision 冲突。client_save_id 让同次保存重试幂等；保存会快照历史，默认最多保留 100 版，可恢复旧版；危险空画布覆盖需显式允许。前端为每张画布创建独立保存 session，串行发送、合并快速编辑，切画布后隔离旧请求响应，并保留尚未保存的本地草稿。[存储并发与保护](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/canvas_store.py#L613) · [保存与恢复](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/canvas_store.py#L697) · [前端保存 session](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/frontend/src/features/freezone/canvasSaveCoordinator.ts#L14)

**屏幕剧本直接交接边界：** 可确认工作流包含 scriptNode、beatContextNode，也有主线 preset/projection 画布入口；本次没有确认“导入既有系列剧本，自动建立保留 episode/scene/beat 身份的可编辑画布图”这条完整直连。通用 scriptNode 或预设投影不能等同于已验证的剧本语义迁移。[节点契约](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/schemas/workflow/v1/stable-contract.json#L5) · [投影 contract tests](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/tests/test_api_freezone_projection.py)

### 与当前 Nuomi 仓库的关系

本地仓库包名为 supertale-ce，origin 是 YuRui-Liu/Nuomi-drama-factory；本地有 src/novelvideo/freezone/canvas_store.py，当前 HEAD 为 b59cc055dfa9a7d729bf162c11906b7b0c65e9dc（2026-10-15），提交主题为“chore(canvas): 保存画布创作与制作功能当前改动”。本地同路径文件与 DramaClaw main 文件在行末归一后不完全相同。共同的 novelvideo/freezone 命名和模块结构说明有明显代码/架构重叠，但不足以据此断言 fork 或提交谱系。后续对比应以 Nuomi 当前 HEAD 为基线；若 revision、history、autosave 等已实现，应列为本地能力而非外部新功能。

## OpenMontage

### 仓库状态

GitHub 页面在观察日显示 68,805 stars、8,826 forks；license 为 AGPL-3.0。观察时 main 指向 2026-10-12 的 commit，仍处于活跃维护期。[仓库](https://github.com/calesthio/OpenMontage) · [commit](https://github.com/calesthio/OpenMontage/commit/e45f5b12bd46440b04f3675af9a77d0718e6a254) · [license](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/LICENSE)

### 流程与 Backlot 读模型

**源码核实：** OpenMontage 项目使用 projects/<project_id> 目录，project.json 记录项目身份和 pipeline 类型；YAML manifest 定义 stages、skills、输出 artifacts。checkpoint JSON 保存阶段状态、artifacts、review 和成本等，写入逻辑会读取 manifest 的 approval 规则并检查阶段前置条件。[Pipeline manifest](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/pipeline_defs/animated-explainer.yaml#L1) · [checkpoint 初始化和 gate 规则](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/lib/checkpoint.py#L198)

Backlot 是只读投影：state.py 读取 checkpoint、history、artifacts 和 events.jsonl，将 scene_plan.scenes、script.sections、asset_manifest.assets 按 scene ID join 成电影分镜卡；FastAPI 以 GET 与 SSE 提供 board state 和变更通知。项目文件是事实源，Backlot 没有 screenplay 或 scene graph 写入接口。[场景卡 join](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/state.py#L403) · [状态组装](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/state.py#L588) · [只读 API/SSE](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/server.py#L165)

**文档声明：** Backlot 显示阶段轨、screenplay 卡片、scene filmstrip、生成活动、决策、成本和渲染回放；watchfiles 监听项目文件变化。另有 simulate-run demo 脚本展示动态 board，但本次未执行，不能把 demo 表现当成生产链路验证。[Backlot README](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/README.md) · [Backlot tests](https://github.com/calesthio/OpenMontage/tree/e45f5b12bd46440b04f3675af9a77d0718e6a254/tests/backlot)

Backlot 的 storyboard 是从规范 artifacts 投影出的只读场景卡，不是可编辑节点/边画布；没有验证到从选定 scene/take 晋升回剧本或自由画布的路径。因此它适合作为 Agent 执行态势、媒体、成本和回放的读模型参考，不是 Nuomi 的 Agent-to-editable-canvas 交接方案。

### Agent、skills、工具与 gate

**文档声明：** PROJECT_CONTEXT 将 OpenMontage 定义为 agent-first/instruction-driven：外部 AI Agent 负责 orchestration、创意判断、stage transition 和 review；Python 提供工具及持久化。知识分为工具能力注册、OpenMontage 工作流程 skills、外部技术 skills；pipeline manifest 选择阶段 skill、tool、review focus 和 approval gate。[PROJECT_CONTEXT.md](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/PROJECT_CONTEXT.md) · [Architecture](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/docs/ARCHITECTURE.md)

**源码核实范围：** 已检查 Backlot 与 checkpoint；没有逐一检查工具注册器、BaseTool 的所有作用域、fallback 和 retry 实现，也没有验证各宿主的实际运行体验。OpenMontage 可配置单元是 pipeline/stage/skill/tool，不是面向画布的专业 Agent 角色目录。animated-explainer manifest 和 checkpoint 代码确有分阶段 gate、approval policy、revision/send-back 限额等机制。它们与 Nuomi 要求的统一自由画布不匹配，不应照搬为产品阶段门禁。

## 对 Nuomi 的参考结论

- DramaClaw 对 Nuomi 最值得逐项 diff 的是图事务和运行追踪：skill/recipe schema、可修订 draft、plan digest、确认 claim、task/run/action/node ID 串联、幂等键、runner lease、失败恢复，以及 provenance 驱动的选中结果晋升。它们可作为统一自由画布内 Agent 的执行机制。
- Nuomi 已有同名 Freezone 模块且代码不同；不能把上游共有概念直接作为外部创新。没有 Git 共同祖先/复制历史证据，本报告不判定 fork 关系。
- DramaClaw 的媒体参数审批规约和 OpenMontage 的 stage gate / sequential checkpoint UX 属于各自产品政策，不符合 Nuomi 已确定的统一自由画布边界。
- OpenMontage 可借鉴基于规范 artifacts 聚合运行看板、媒体状态、成本和回放；不能提供可编辑图 schema、Agent 画布事务或选择晋升实现。
- 是否支持“屏幕剧本→保留 episode/scene 身份的画布节点”需追踪 Nuomi 本地 screenplay 输出、episode/scene 数据模型与画布投影。对本次查阅范围，两仓库都没有足够证据证明该完整路径已解决。

## 尚未验证

1. DramaClaw main 中的 Agent Workflow 是否全部进入 v2.0.6 之后的正式发行包。
2. DramaClaw 从系列结构化剧本到 Freezone 的精确保真映射，以及 episode/scene/beat ID 稳定规则。
3. DramaClaw 各 provider 失败后的自动重试次数、错误分类和成本结算边界；虽存在 retry/reconcile 路径，本报告未逐 provider 追完。
4. OpenMontage 各工具的执行级权限、fallback 顺序和 retry policy 源码；文档描述了 contract，本报告未逐工具检验。
5. Nuomi 与 DramaClaw 的 Git 共同祖先或复制历史；本报告只确认仓库 origin、HEAD 和对应文件差异，不推定 fork。
