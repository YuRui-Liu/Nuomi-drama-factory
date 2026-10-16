# 专业 Agent 与节点全链路增量实现计划

> **面向 AI 代理的工作者：** 沿用用户已选择的子代理驱动方式，执行时使用 subagent-driven-development，单批实现、规格审查、质量审查。本文仅规划，不自动启动实现或付费生成。复选框代表尚未执行，历史记录不重新计为待开发。

**目标：** 在现有 Agent、对象、画布和节点执行基础上，实现专业 Agent 直接操作授权节点、语义关联与局部修改，以及完整漫剧真实交付。

**架构：** 继续使用 creation 对象／版本／操作／委派及 freezone scheduler／RunStore。人工与 Agent 共用节点执行入口，专业预设只声明方法和能力需求，不授予权限。画布任务节点投影既有记录，内容节点呈现稳定对象；不新建平行存储或隐藏旧阶段引擎。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、pytest；React、TypeScript、React Flow、TanStack、Vitest；Pencil MCP。

## 0. 依据、执行顺序和历史承接

产品依据：[主 PRD §15](../specs/2026-10-13-agent-canvas-prd.md)、[专业补充规格](../specs/2026-10-16-professional-agents-script-handoff-prd.md)、[专业契约](../specs/2026-10-16-manga-production-agent-contracts.md)。2026-10-16 用户已整体确认书面规格。

本计划是[10 月 14 日恢复计划](2026-10-14-agent-canvas-resume.md)的增量，原计划已交付的 5—8B 不重写。任务 9—13 按下表细化，不能只实现新增目录而遗漏旧计划剩余项：

| 原任务 | 本计划增量 |
| --- | --- |
| 8A／8B 配置和委派 | P1 专业预设；P3 真实节点调用；P5 主创持续编排 |
| 9 授权预算 | P2，包括专家直接调用和并发预留 |
| 10 真实节点执行 | P3；P7 声音与导出；保留原 19 类节点能力清单 |
| 11A 候选与预览 | P4 影响解释；P6 任务投影；P8 局部修改验收 |
| 11B 撤销及跨画布 | 继续原计划的完整范围，由 P6 接入交互，P8 验收 |
| 12 删除旧流程 | P8，保留旧删除清单与能力去向记录 |
| 13 真实验收 | P9，补齐完整成片与专业成果消费证据 |

依赖：P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9。只读探索和无共享状态的测试可并行；共享实现文件串行。沿用当前 feat/canvas 工作区，不另建遗漏本地材料的 worktree。实施前重新记录 HEAD、目标文件差异及测试基线，不将本计划中的源码观察当成运行验收。

当前代码观察：
- builtin_agents.py 仅两个预设，既有固定 v1 必须仍可解析。
- agent_profiles.py 的 editable 与能力枚举是严格白名单；新增五项配置必须贯穿验证、个人发布、采用、覆盖、升级与前端。
- agent_model.py 的 Decision 目前仅四种准备动作，ChildDecision 操作数上限为 4；agent_model 可用性返回最多 3 次模型调用。不能仅扩充提示词就宣称支持全片持续编排。
- freezone/orchestration/capabilities.py 列出 19 类节点及 adapter_not_installed 状态；保留不可用项及原因，不能删除以伪造覆盖率。
- 当前真实生产操作继续遵守 AGENTS.md 与 nuomi-production。P8 完成支持路径迁移并同步规则之前，不绕过该路径运行生产。

## 文件职责

所有路径相对仓库根目录。标为“新增”的文件是本计划创建目标，不声称当前存在。

| 文件／目录 | 变更与职责 |
| --- | --- |
| src/novelvideo/creation/builtin_agents.py | 保留旧版本，增加专业预设版本及目录 |
| src/novelvideo/creation/professional_agents.py（新增） | 专业方法、交付要求、示例证据和协作配置 |
| src/novelvideo/creation/agent_profiles.py、personal_library.py | 五项配置校验与固定版本复用 |
| src/novelvideo/creation/approvals.py、budgets.py（新增） | 原任务 9 的授权及严格预算，不替代已有实际费用账本 |
| src/novelvideo/creation/node_execution.py（新增） | 人工／Agent 共用的受控节点运行入口 |
| src/novelvideo/creation/agent_tools.py、delegations.py、agent_model.py、agent_runs.py | 工具白名单、缩权、持续运行与结果交付 |
| src/novelvideo/freezone/orchestration/{capabilities,scheduler,store,projection,planning}.py | 节点适配、真实调度、状态和结果投影 |
| src/novelvideo/creation/semantic_relations.py、impact.py（新增） | 定义内关系、状态依据和固定版本影响分析 |
| src/novelvideo/creation/ontology.py、agent_context.py | 类型／关系确认与可信上下文 |
| src/novelvideo/creation/production_goals.py（新增） | 持久交付要求与未满足项；引用既有 runs，不复制供应商状态 |
| src/novelvideo/script_creation/handoff.py | 原文、目标范围和渐进展开的交接 |
| src/novelvideo/api/routes/creation.py、creation_library.py | 公开领域接口及作用域校验 |
| frontend/src/features/agent-configuration/AgentConfigurationPanel.tsx | 五项配置与版本交互 |
| frontend/src/features/agent-runtime/{AgentRuntimePanel.tsx,useAgentRuntime.ts} | 同一主输入、专家选择、进展与定位 |
| frontend/src/features/agent-runtime/AgentDirectory.tsx（新增） | 按任务／职责选择、真实能力及案例说明 |
| frontend/src/features/canvas/domain/{canvasNodes,nodeRegistry}.ts | 关联任务节点注册及类型 |
| frontend/src/features/creation-workspace/AgentTaskNode.tsx（新增） | 默认折叠的任务视图，只读展示可信记录 |
| frontend/src/features/script-creation/handoff-dialog.tsx | 指定交接目标，保留全文与范围 |
| src/novelvideo/task_backend/runners/video.py、audio/narrative_group_dubbing.py、export/episode_export.py | 真实声音、字幕、合成和文件交付 |
| docs/superpowers/reports/2026-10-16-professional-agent-acceptance.md（新增） | 本轮实现、交互、真实产物证据及限制 |

## P0：现有节点能力地图与统一原型

**文件：** 新增 docs/superpowers/reports/2026-10-16-professional-node-map.md；修改 docs/superpowers/specs/2026-10-16-professional-agents-interaction-design.md；设计源 E:/Proj/nuomi.pen。

- [ ] 逐项读取 capabilities.py、前端 nodeRegistry.ts 和 scheduler 的适配注册，记录“节点类型／操作／当前 handler／可用状态／授权位置／恢复入口／使用专家／测试”。覆盖全部 19 类，不将类名等同真实执行支持。
- [ ] 沿公开执行入口追踪手动与 Agent 调用到提交、计量、RunStore 和投影；记录重复系统或旧阶段依赖，定位到文件和函数。
- [ ] 通过 Pencil MCP 读取现有设计；在同一工作空间重画目录展开、专家委托、折叠任务、影响说明、制作进展、交付预览状态。不得直接读写 .pen 文件。
- [ ] 检查 1440×900 与 1280×720：保留现有主题和节点编辑器；单一主输入；选择与视图不丢失；所有状态能返回原画布。导出截图与 PDF，明确占位与真实跳转。
- [ ] 提交能力地图与原型说明，视觉修订交用户审阅；不将此前九页草稿当作已通过依据。P1—P5 后端工作可按已确认规格推进，P6 布局须使用审阅后的视觉。

验证命令（PowerShell）：

```powershell
rg -n "available|adapter_not_installed|operations" src/novelvideo/freezone/orchestration/capabilities.py
rg -n "register|adapter|execute|submit" src/novelvideo/freezone/orchestration
git diff --check
```

预期：地图覆盖真实操作及不可用原因，不运行付费生成；提交 docs: map professional agents to existing node capabilities。

## P1：专业预设与五项配置

**修改：** builtin_agents.py、agent_profiles.py、personal_library.py、creation_library.py、AgentConfigurationPanel.tsx。
**新增：** professional_agents.py、tests/creation/test_professional_agents.py。
**扩展测试：** tests/creation/test_builtin_agents.py、test_agent_profiles.py；frontend/src/__tests__/features/creation-workspace/agent-configuration.test.tsx。

- [ ] 在既有 fixture 上添加以下失败测试：18 个岗位职责可查；旧两个 v1 的内容及 proof 不变；每个专业配置有五项内容；未知操作拒绝；目录查询不 seed 库；项目升级不扩大任务权限。
- [ ] 运行 uv run pytest tests/creation/test_professional_agents.py tests/creation/test_builtin_agents.py -q，先观察新增断言失败，不能以导入无关依赖失败作为红灯证据。
- [ ] 新配置扩展字段为 delivery_requirements、collaboration_rules、examples；methods、references、capabilities 复用已有字段。旧版本缺新字段按读视图兼容，不改变旧固定快照；新发布版本写全字段。examples 只存来源和公开程度，不复制受限素材。
- [ ] 专业预设声明 required_node_operations；它用于匹配和可用性说明，实际权限仍由委派与授权校验。按 source/ref/revision 升级，不用同名覆盖旧用户配置。
- [ ] 同步前端五项编辑和发布预览。重跑上述测试、test_agent_profiles.py 和前端配置测试，检查项目覆盖与个人配置隔离；提交 feat(agents): add versioned professional presets。

配置示例约束（字段用于新增契约，不直接粘贴替换旧快照）：

```json
{
  "delivery_requirements": ["明确光源、光向、色温与戏剧作用", "关联分镜版本"],
  "collaboration_rules": ["空间冲突提交导演协调", "授权范围内可调用预览"],
  "required_node_operations": [
    {"node_type": "imageGenNode", "operation": "generate"}
  ],
  "examples": [{"source_url": "https://example.org/source", "prompt_public": false, "inputs_public": false, "workflow_public": false}]
}
```

示例域名仅说明字段，不写入正式预设；正式示例只使用研究记录中已核实的来源。

## P2：授权与预算先于媒体执行

**文件：** 原任务 9 的 approvals.py、budgets.py、tests/creation/test_approvals.py、test_budgets.py；修改 delegations.py、agent_tools.py。
**前端：** 新增 frontend/src/features/agent-runtime/ApprovalCard.tsx，接入 AgentRuntimePanel.tsx。

- [ ] 先测试专家直接生成但无授权被拒绝；模型配置、角色名和方法文字均不能授予执行权。子委派的节点、操作、预算均只能收窄。
- [ ] 定义授权绑定 task/scope/input fingerprint/allowed operations/替换与共享写权限。金额以最小货币整数保存，币种不可混算；提交前原子预留，未知结算保留占用。
- [ ] 写并发测试：余额 100、两个并发预留各 70，只允许一个成功；同 request_id 重放不重复占用；提交状态不明不得释放额度并重发。
- [ ] 运行 uv run pytest tests/creation/test_approvals.py tests/creation/test_budgets.py tests/creation/test_delegations.py -q，先红后绿；费用无可靠上界时严格预算提交拒绝，展示独立授权路径。
- [ ] 集中授权卡只询问缺少权限，不逐角色重复确认。覆盖采用即授权、范围漂移、晚到结算、停止及重试次数耗尽。
- [ ] 提交 feat(creation): enforce scoped node grants and budget reservations。

关键计算：

```python
remaining = limit_minor - settled_minor - reserved_minor
admit = currency_matches and upper_bound_minor is not None and upper_bound_minor <= remaining
# 计算、幂等请求登记与预留写入必须处于同一事务。
```

## P3：专家直接调用真实节点

**文件：** 新增 node_execution.py、tests/creation/test_node_execution.py；修改 agent_tools.py、delegations.py、agent_model.py、agent_runs.py，以及 freezone/orchestration 的适配注册；前端 useCanvasRun.ts 接入同一领域入口。
**原任务继续：** 19 类节点逐项接入、非执行关系不触发调度、任意合法子图和无集画布可执行。

- [ ] 先添加“同输入的人工／专家执行得到同一计划语义”、权限外操作拒绝、未安装 adapter 明确受阻的失败测试。
- [ ] 新增 node.execute 和 node.read_result 两种受限领域动作。执行 payload 固定 node_id、node_type、operation、expected_revision、input_refs、authorization_id；request_id 沿用已有命令身份。服务端校验实际节点类型与归属，不信客户端自报。
- [ ] node.execute 只创建并关联一次真实 RunStore run；node.read_result 读取该委派拥有的真实任务。输入变化、撤销、预算及当前权限在实际提交前复核。不能在数据库事务内等待供应商。
- [ ] ToolDecision 和 ChildDecision 的白名单／数量校验同步变更；能力目录显示适配状态，配置声明不能标记为已安装。专家可以直接调用，不能按身份要求先经过主创或 MiniMax。
- [ ] 接入现有 scheduler 和 projection。保存提交意图与外部任务关联；未知提交先查询；提交后投影崩溃可重放，不新买一次媒体。真实外部动作通过受支持服务，不开放通用 HTTP、shell 或任意文件操作。
- [ ] 运行 uv run pytest tests/creation/test_node_execution.py tests/creation/test_agent_tools.py tests/creation/test_agent_runs.py tests/freezone/orchestration -q；覆盖停止发送间隙、迟到结果、服务重启、成功分支复用。提交 feat(creation): share node execution between agents and users。

拟新增请求示例：

```json
{
  "request_id": "task-42-video-1",
  "operation": "node.execute",
  "payload": {
    "node_id": "video-1",
    "node_type": "videoNode",
    "operation": "generate",
    "expected_revision": 3,
    "input_refs": [{"object_id": "storyboard-1", "revision": 4}],
    "authorization_id": "grant-42"
  }
}
```

返回 prepared/submitted/run_id 不得等同交付成功；以已验证并投影的实际媒体引用为交付依据。

## P4：本体关系、状态与修改影响

**文件：** 新增 semantic_relations.py、impact.py、tests/creation/test_semantic_relations.py、test_impact.py；修改 ontology.py、agent_context.py、operations.py。
**复用：** ObjectStore 的对象、版本、关系，不新增平行知识库。

- [ ] 用已存在的对象构造人物持物状态→分镜→视频关系，写失败测试：修改信封为手机，影响结果包含有关分镜和视频，不包含无关人物；纯布局变动不产生内容过期。
- [ ] 关系记录 source/target 及使用版本、定义版本、依据和 confirmed/candidate 状态。已启用定义内可自动建立；未知类型或改变定义要求草案确认，Agent 不可把推测升为硬事实。
- [ ] 影响查询返回直接依赖、传递依赖、推测关系和各产物采用版本，遍历去重；引用或来源线不当作执行线，不因查询启动生成。
- [ ] 主动解释由可信影响集合生成，不能由模型编造“已检查”对象。修复计划绑定固定目标，修改授权按 P2 处理。
- [ ] 运行 uv run pytest tests/creation/test_semantic_relations.py tests/creation/test_impact.py tests/creation/test_ontology.py tests/creation/test_creative_constraints.py -q。覆盖跨画布只读引用、未知关系、候选未确认、陈旧版本和执行依赖循环。
- [ ] 提交 feat(creation): explain versioned semantic change impact。

结果结构：

```json
{
  "changed_ref": {"object_id": "prop-state-1", "revision": 2},
  "affected": [{"object_id": "storyboard-1", "revision": 4, "certainty": "direct", "reason": "uses_changed_state"}],
  "suggested_operations": [],
  "requires_generation": false
}
```

suggested_operations 由实际节点能力与任务目标填充，不能仅因有 affected 就自动购买新生成。

## P5：渐进交接、持续目标与专业协作

**文件：** 新增 production_goals.py、tests/creation/test_production_goals.py；修改 script_creation/handoff.py、creation/agent_runs.py、agent_context.py、agent_model.py、delegations.py。
**测试：** tests/script_creation/test_handoff.py；新增 tests/creation/test_goal_recovery.py。

- [ ] 先写两种目标测试：只做指定场景不展开其他集；完整第一集在中间任务成功后仍保留声音、导出等未满足项，不提前结束。
- [ ] 交接保存全文源版本和目标 scope，幂等创建结构；按需求展开内容节点，标记原文／推断／采用。不重新解析重写全剧，不覆盖人工节点。
- [ ] ProductionGoal 保存 requirements、dependencies、run refs、授权与阻塞原因。专业任务引用现有 delegation，供应商状态引用 RunStore。导演合并专业方案时检查 expected_revision，专业冲突回到具体字段。
- [ ] 将当前“最多三次模型调用”的一次运行与长期制作目标分开：单次 Decision 仍有界；目标通过持久就绪任务继续调度，不能用无限循环或提高默认调用数冒充持续执行。每次新调用计费、授权和停止 fence 生效。
- [ ] 专家检查局部、导演检查专业冲突、主创检查交付项；检查结果记录 evidence_ref／满足项／未满足项。模型 finished=true 不能清除未完成依赖或伪造媒体证据。
- [ ] 运行 uv run pytest tests/creation/test_production_goals.py tests/creation/test_goal_recovery.py tests/script_creation/test_handoff.py tests/creation/test_agent_runs.py -q。模拟进程退出、重复唤醒、人工并发编辑、缺参考和停任务；有效子任务不重复提交。
- [ ] 提交 feat(creation): persist scoped production goals and specialist handoffs。

完成判定：

```python
def goal_complete(required_ids: set[str], verified_ids: set[str], blockers: list[str]) -> bool:
    return required_ids <= verified_ids and not blockers
```

verified_ids 只能由实际产物验证及任务回执产生，不能直接采用模型输出中的列表。

## P6：目录、任务节点和影响交互

**文件：** 新增 AgentDirectory.tsx、AgentTaskNode.tsx；修改 AgentRuntimePanel.tsx、useAgentRuntime.ts、canvasNodes.ts、nodeRegistry.ts、handoff-dialog.tsx。
**测试：** 新增 frontend/src/__tests__/features/agent-runtime/agent-directory.test.tsx、agent-task-node.test.tsx；扩展 agent-runtime.test.tsx、handoff-dialog.test.tsx。

- [ ] 先写交互失败测试：选三节点委托后改变选择不改变任务范围；返回目录保留原画布；折叠任务不丢状态；迟到响应不写入另一画布。
- [ ] 目录按职责／任务搜索，显示五项配置、证据来源及真实可用性；使用现有项目固定采用流程，避免新建第二个个人库。
- [ ] 关联任务节点只保存 delegation/run 引用及视图状态，状态读服务端；内容和专业贡献通过对象关系展开。若注册新节点类型，同步能力目录和原节点计数验收，不能硬编码“始终19”。
- [ ] 在原侧栏保留唯一主输入；实现任务定位、结果查看、停止、恢复、影响说明及按授权的操作。节点编辑器与播放工具复用现有能力。
- [ ] 接入原 11A／11B 的候选比较、组级分支、撤销与跨画布移动／引用；不把新增任务节点显示替代这些功能。保留后续独立人工修改。
- [ ] 在 frontend 运行 pnpm exec vitest run src/__tests__/features/agent-runtime src/__tests__/features/creation-workspace src/features/script-creation/handoff-dialog.test.tsx，然后 pnpm exec tsc --noEmit --project tsconfig.app.json。使用 P0 审阅后的原型对照真实浏览器两种视口。
- [ ] 提交 feat(canvas): integrate specialist tasks into the existing workspace。

## P7：声音、字幕与成片导出闭环

**文件：** task_backend/runners/video.py、audio/narrative_group_dubbing.py、export/episode_export.py、api/routes/generation.py；按 P0 地图接入音频／合成节点适配。
**测试：** tests/test_compose_episode_audio_source.py、test_compose_episode_completeness.py、test_api_compose_export_contract.py；新增 tests/creation/test_delivery_requirements.py。

- [ ] 写失败回归：配置配乐时实际混音输入包含选定 BGM；缺少对白或必需视频不能标完整成功；导出路径失效时返回未满足项。
- [ ] 修复 add_bgm 从 API 到实际 compose 的消费，明确音源、时间范围、增益和与对白的混合；不靠 boolean 回执宣称配乐已入片。使用本地短音频夹具验证，不为测试额外购买音频。
- [ ] 字幕估时和实际逐字对齐分别记录能力；生成／导出 SRT、烧录字幕及视频均校验实际文件。采用任务要求决定音效、音乐、字幕交付，不对无声局部任务强制追加。
- [ ] 真实导出返回媒体身份和可访问引用，由 P5 检查目标，不能只判断 FFmpeg exit code。预览时间位置可定位相关镜头与声音对象。
- [ ] 运行 uv run pytest tests/test_compose_episode_audio_source.py tests/test_compose_episode_completeness.py tests/test_api_compose_export_contract.py tests/creation/test_delivery_requirements.py -q。
- [ ] 提交 fix(production): consume requested audio and verify final deliverables。

## P8：旧流程清除与局部修改整合

**文件：** 沿用原任务 12 的明确路由删除清单、api/__init__.py、项目导航、AGENTS.md、skills/nuomi-production/SKILL.md；新增 tests/creation/test_no_legacy_execution.py。
**报告：** 更新 docs/superpowers/reports/2026-10-13-canvas-removal-ledger.md。

- [ ] 从 P0 地图给每个删除项登记“旧入口→保留共享能力→新节点入口→测试”。保护历史用户数据，不整目录删除 narrative_groups 或 production_assets。
- [ ] 在新链路测试中替换旧阶段协调入口为抛错桩，先确认当前依赖，再切断旧调用。保留有效的生成器、执行合同和恢复服务。
- [ ] 同步生产指导到实际已支持的新入口，保存并发、任务对账及成片检查要求不丢失。未完成迁移前不提前移除保护规则。
- [ ] 完成原 11A／11B 回归：预览提出表情修改→影响定位→专家局部候选→采用→撤销；另一个片段和其他画布保持原引用。恢复不重复购买成功内容。
- [ ] 运行 uv run pytest tests/creation tests/freezone -q；frontend 运行 pnpm run build。失败按真实引用链修复，不保留空壳阶段入口。
- [ ] 提交 refactor(creation): complete unified node execution cutover。

## P9：真实端到端与最终证据

**文件：** 新增 docs/superpowers/reports/2026-10-16-professional-agent-acceptance.md。生成媒体不提交 Git。

- [ ] 建立隔离验收项目，读取真实模型、并发、片长及画幅。汇总本次生成范围、费用上界和重试次数；已有授权覆盖则继续，未覆盖则只请求具体差额授权，不能从批准 PRD 推导付费授权。
- [ ] 分别完成三入口：想法→片段；完整剧本→指定场景；已有素材→局部修改。再完成一集真实成片，记录输入版本、任务、专业贡献、媒体、声音、字幕及导出。
- [ ] 对站位、灯光、动作、表情至少各记录一次字段进入实际请求的证据，并查看实际片段是否明显违背要求；无需为了覆盖岗位强制每场运行全部专家。
- [ ] 验证关闭重开任务状态、只恢复失败范围、成功产物复用。故意超时／重复提交的故障由自动测试承担，不重复购买媒体制造故障。
- [ ] 实际播放并检查明显剧情遗漏、对白、声音、字幕和导出可用性；缺项写为部分完成。供应商不可用则真实验收未完成，自动测试不能替代。
- [ ] 按变更运行最终检查，记录代码测试、浏览器交互、真实媒体分别的结果与限制；提交 docs: record professional agent end-to-end acceptance。

## 自检与执行交接

| 主 PRD 增量 | 对应任务 |
| --- | --- |
| 15.1 按需专家、内容＋任务节点 | P3、P6 |
| 15.2 专业团队、直接节点能力 | P1、P2、P3、P5 |
| 15.3 五项配置和目录 | P1、P6 |
| 15.4 按目标展开剧本 | P5、P6 |
| 15.5 本体和主动影响解释 | P4、P6 |
| 15.6 分层检查及完整交付 | P5、P7、P9 |
| 15.7 统一界面与原型修订 | P0、P6 |
| 15.8 当前规则与未来架构 | P3、P8 |
| 15.9 历史承接及真实验收 | 原恢复计划 9—13 与 P9 |

每批先新增行为失败用例、确认失败原因，再实现、针对性验证、规格审查、质量审查和窄范围提交。代码块是约束与请求示例，不伪装成已实现 API；新增测试路径必须随相应任务创建。原有生成／恢复测试只在相关改动或失败时重跑。

用户已选择子代理驱动方式，无须重新询问执行模式。此次只更新计划；实施开始于 P0 能力地图与 Pencil 修订，再按依赖推进。P0 是局部可审阅交付，不能用它替代全链路目标。
