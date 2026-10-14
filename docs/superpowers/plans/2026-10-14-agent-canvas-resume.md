# Agent、节点、画布创作：PRD V1.1 恢复开发计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务执行，一名实现者，规格审查后质量审查。用户已选该方式并明确恢复开发，不重复询问执行方式。

**目标：** 兑现 PRD V1.1 的项目构思、分集和自由画布、可定制 Agent、版本化创作对象与局部委托闭环，并物理删除旧切页流水线。

**架构：** 扩展已实现的 creation 对象／操作／投影层，以项目内注册画布为作用域，episode_id 可空。复用现有画布与专业节点，Agent 和人工共用操作服务。运行、提案、候选、预算及创作配置分别持久化，画布保存视图而不另存一套可写领域内容。

**技术栈：** FastAPI、Pydantic、SQLite、pytest；React、TypeScript、React Flow、TanStack、Vitest。

## 依据、现状与约束

- 产品基线：`docs/superpowers/specs/2026-10-13-agent-canvas-prd.md`，全部产品与交互决策经用户确认。
- 视觉基线：原有产品及 V4；`.superpowers/brainstorm/27119-1791899331/content/canvas-prd-v4.html`、`.js` 为独立原型，13 项浏览器行为验证，不作生产代码移植。
- 前四任务提交：262f9df、0e35ae4、dfbb61d、0379052。任务 5A、5B、6 已双审提交：7cdcc25、505ea23、6c55105；任务 7A1 已双审提交：4317952。详细验证记录见执行报告。
- 保留现有大量脏改动；实施前记录目标文件差异，只暂存本任务增量。当前分支 feat/canvas，不切分支、不整体还原、不另建遗漏未提交工作的 worktree。
- 旧 13 任务计划继续提供节点能力和删除路径明细。本计划调整顺序及新增范围；旧计划的 episode 强制字段和阶段式创作确认规则不再适用。

## 新增边界与契约

```python
class Scope(StrictModel):
    project_id: NonEmptyString
    canvas_id: NonEmptyString
    episode_id: NonEmptyString | None = None
    node_ids: list[str] = Field(default_factory=list)

class CanvasWorkspaceCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal['concept', 'free']
    title: str = Field(min_length=1, max_length=200)
```

服务端验证 canvas 注册记录属于项目并与 episode_id（包括 null）一致，不能靠可空字段放宽任意画布访问。每集唯一主画布、每项目唯一构思画布，自由画布可多张。canvas 作为工作空间索引，领域对象身份仍与节点分离。

```sql
CREATE TABLE canvas_workspaces(
 canvas_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
 episode_id TEXT UNIQUE, title TEXT NOT NULL,
 operation_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX one_concept_canvas ON canvas_workspaces(kind) WHERE kind='concept';
```

保留原 episode_canvases 映射作为分集索引；新代码同事务写两者，不迁移旧格式项目。新模式既有集记录可在显式写操作的 schema 初始化中补充注册，不在 GET 中写入。

`GET /creation/canvases` 返回 `{canvas_id,kind,episode_id,title,status}` 列表；`POST` 接受 CanvasWorkspaceCreate，返回相同记录及 operation_id。创建仍走 durable outbox，不能直接绕过 projection 存文件。

前端主路由 `/projects/$project/freezone?workspace=<canvas_id>`，必须经已授权服务端列表解析。兼容已存在的 `?episode=<id>`，解析后规范为 workspace。不得接受旧个人画布 `?canvas=` 来绕过注册校验。

## 任务 5A：收尾已完成的新项目与集画布实现

**状态：已双审完成，提交 `7cdcc25`。下列清单保留原验收要求。**

文件：`creation/{workspace,project_mode,projection}.py`、`api/routes/{creation,projects}.py`、`project_config.py`、`api/schemas.py` 的 creation_mode 字段、`frontend/src/api/projects.ts`、`tests/creation/test_episode_workspace.py`。

- [ ] 对原任务 5 完成规格与质量审查；自由画布属于 5B，不将新增范围误判成 5A 失败。
- [ ] 验证原子创建、幂等、三个恢复窗口、canvas collision、legacy GET/POST 文件不变与权限。命令：`uv run pytest tests/creation tests/test_project_dependencies.py tests/test_api_project_resolution.py tests/test_project_config_media_pipelines.py tests/test_project_config_narration.py -q`。
- [ ] 仅暂存本任务文件和脏文件中归属明确的 hunk，提交创建集画布能力。

## 任务 5B：构思与自由画布的领域作用域

**状态：已双审完成，提交 `505ea23`。**

文件：新增 `creation/canvas_workspaces.py`、`tests/creation/test_canvas_workspaces.py`；修改 `creation/{models,store,workspace,operations}.py`、`api/routes/creation.py`、`freezone/canvas_store.py` 的语义恢复保护。

- [ ] 写失败测试：无集创建自由画布、唯一构思、同请求重试、不同 actor 冲突、陌生画布／错集拒绝、旧项目无写入。
- [ ] 实现上述索引／请求类型／GET POST 契约，scope 校验以已注册画布为准。get 路径只读。
- [ ] object.create 在 payload 记录 canvas_id 和可空 episode_id，update 验证真实归属，禁止 null episode 使所有自由画布互通写权限。
- [ ] asset.bind 的关系 source 对集画布保留 episode，对无集画布使用稳定项目对象；payload 必含 canvas_id，关系键包含 canvas 和 node，跨画布隔离。
- [ ] 历史恢复保护覆盖所有注册的语义画布，不只 episode_canvases。
- [ ] 验证下述断言并运行 `uv run pytest tests/creation -q`，完成双审后提交。

```python
assert free_canvas['episode_id'] is None
assert first_concept['canvas_id'] == repeated_concept['canvas_id']
assert task_scope.canvas_id == captured_canvas_id
assert other_canvas_object['revision'] == original_revision
```

## 任务 6：统一前端工作空间

**状态：已双审完成，提交 `6c55105`；隔离环境真实交互及两种视口验证见执行报告。**

文件：新增 `frontend/src/features/creation-workspace/{contracts,CreationWorkspace,CanvasWorkspaceSwitcher}.tsx`（contracts 使用 .ts）、`frontend/src/api/creation.ts`、`frontend/src/lib/queries/creation.ts`；修改 freezone lazy route、FreezoneShell、project-navigation-routes、project-header-navigation、project summary 类型与映射。

- [ ] MSW 测试构思／两集／自由画布、新建、未知 workspace、legacy unsupported、加载失败及保存失败不能离开。
- [ ] 复用 ReactFlowProvider、FreezoneShell、flushFreezoneCanvasRuntime，切换前 flush；浏览器后退也保护。每画布恢复 viewport、选择与草稿。禁用内部旧 canvas URL 写入及保存冲突后跳任意个人副本的行为。
- [ ] 项目无画布时提供创建构思或自由画布，不要求创建集。支持新建集与主画布；导航可搜索／收起，三类画布使用相同节点能力。
- [ ] 主创作导航只保留画布与保留库，管理入口保留任务费用设置；旧路径在任务 12 物理删除。
- [ ] `pnpm exec vitest run src/__tests__/features/creation-workspace`、`pnpm exec tsc --noEmit --project tsconfig.app.json`，截图 1440×900／1280×720，双审提交。

## 任务 7：资产及风格／手法引用

**状态：进行中。7A1、A2a-1、A2a-2、A2a-3 已双审提交，分别为 `4317952`、`0784b2c`、`7b8bf35`、`99b111a`。A2b-1 已提交 `4290cf6`；A2b-2 的 production-assets、角色、场景直接入口、画布资产发布、场景 worker 发布组已提交 `3edd058`、`af82152`、`94e55fb`、`d49160b`、`0e0c258`。casting 入口测试上下文夹具独立修复为 `0e1aeca`。7B-1 固定引用 API 与原子节点投影正在实现，7B 界面尚未完成。**

文件：原任务 7 文件，新增 `creation/library_references.py` 和测试。AssetAdapter 源写锁、重命名映射、interrupted publication 的显式对账并入本任务。

执行细分（产品范围不变，按 7A → 7B 顺序，各自双审）：

- **7A 资产源一致性：** 统一 Adapter 与保留资产编辑／采用入口的异步写入保护；角色及 identity、场景改名时同步 source locator 而保持语义 ID；明确恢复中断 publication；将真实已采用 workflow slot/version 与模型记录一同快照，保留历史引用。重点修改 Adapter、SQLiteStore 精确元数据方法、casting adoption、production adoption 和场景目录改名边界。现有线程锁不可直接跨 await 使用。
- **7B 引用 API 与界面：** 在 7A 契约上接入基础／变体选择器、引用详情和风格／手法的项目／画布／组／节点作用域；复用现有完整资产中心，选择与应用分开，失败保留原值，不自动重生成。

7A 内部再分两次完整交付：**A1** 共享写锁、Adapter 接入与中断 publication 对账；**A2** 源元数据／重命名、采用事务与可信媒体快照接入。每次双审提交，A1 不能宣称尚未接入的所有源写入口已受保护。

A2 按实际写入边界依次交付：**A2a-1** 源元数据普通／批量写锁、可信 SQL 重载、错误传播及取消清理；**A2a-2** durable mutation journal、语义版本同步、显式 refresh、Adapter 嵌套去重，未完成同步阻止读取当前版本但保留固定历史引用；**A2a-3** 改名时稳定 source locator、目录迁移与 episode identity cascade 的恢复；**A2b** 真实媒体采用事务与可信 workflow 快照。各批独立双审，不能以其中一个基础批次代替完整资产接入。

A2b 分两批串行交付：**A2b-1** 可信媒体内容快照、可恢复发布边界及 casting／production-assets 显式采用；**A2b-2** 画布采用、首个 provisional current、上传、历史恢复等其余保留入口。入口地图见 `reports/2026-10-14-asset-media-entrypoints.md`。固定版本引用必须保留原媒体字节，不能仅记录会被覆盖的 canonical 路径；读取不触发迁移。

- [ ] 先测试基础／变体选择不写入、明确应用只改目标、创建变体不采用、引用失败不改原值、跨集及自由画布隔离。
- [ ] 复用完整现有资产中心编辑器，用户明确选择新基础／已有变体／新版本的发布目标。资产中心写入与适配层共享源状态检查，真实媒体采用刷新可信语义快照，不允许客户端任意媒体路径。
- [ ] 库引用记录对象、版本、scope（project/canvas/group/node）、覆盖／解除；显示生效来源，冲突显式提示，变更不自动重生成。
- [ ] 原任务 7 测试加 `uv run pytest tests/creation/test_asset_adapter.py tests/creation/test_library_references.py -q`，双审提交。

7B 按依赖继续串行交付，产品范围保持上述要求：

1. **固定引用 API 与原子节点投影。** 新增只读浏览／固定版本解析及显式注册接口，沿用项目鉴权；GET 不创建或迁移源库。扩展 `asset.bind` 支持受限节点的新建与已有节点替换，客户端只能提交 ref、媒体槽／角色与布局，媒体 URL 由服务端从固定档案生成。一次 outbox 包含节点、引用和全部相关媒体字段，恢复沿用保存的 patch。保留 `graph.patch` 禁止伪造媒体字段的限制；逐字段 CAS 保护并发编辑，固定历史版本不要求仍为 current。验证幂等、崩溃恢复、3D 多媒体整体写入、非法角色及归档缺失拒绝。
2. **完整资产中心与画布衔接。** 复用基础／变体编辑器及现有节点，拖放与按钮采用同一领域操作；固定引用绕开读取最新 manifest 的旧 hydration。显示名称／变体，详情展示版本、来源、更新与受影响范围；显式选择更新目标。操作到 applied 才显示成功，失败保留原节点，跨画布迟到响应不得改当前画布。保存共享资产明确基础／变体／新版本。验证两集与自由画布隔离、失败无半节点、旧引用在真实预览及生成输入中保持固定。
3. **风格／手法作用域。** 新增 `library_references.py` 与 API／界面，复用可信 style snapshot 与 technique catalog 投影，保存固定版本和内容摘要。实现 project → canvas → group → node 的覆盖／解除和生效来源，校验目标存在及归属；冲突与不适用项可见，不静默叠加。配置更新不启动生成，接入 Agent 上下文和真实执行分别由任务 8B／10完成。针对性后端／前端测试并对照现有样式检查 1440×900、1280×720。

## 任务 8A：Agent 与模板、可扩展对象定义

文件：新增 `creation/{agent_profiles,creative_templates,ontology,creative_constraints}.py`；API `api/routes/creation_library.py`；前端 `creation-workspace/{AgentProfileEditor,TemplateLibrary,ProjectConstraints}.tsx`；各模块对应 tests/creation 测试。

- [ ] 分开持久化个人 Agent 版本、个人创作模板版本、项目采用版本及临时覆盖。定义对象／关系扩展必须显式确认才能启用，验证关系端类型。
- [ ] Agent 配置包含职责、方法、参考、技能权限及默认风格。对话生成配置草案，可视化查看、编辑、试用后保存，不能凭模型自报扩大工具权限。
- [ ] 模板发布预览包含对象定义、关系、规则和依赖；默认排除剧本、媒体、运行历史，只带用户勾选示例。个人库隔离鉴权，不引入市场。
- [ ] 约定支持 hard/preference/draft、source、scope；局部偏好可覆盖，硬约束冲突提出例外方案，推测不自动升级为事实。
- [ ] 测试项目临时覆盖不污染复用 Agent、另一用户不能读个人配置、发布版本不可变、主动升级、未确认类型禁止写入。运行各对应后端与前端测试后双审提交。

## 任务 8B：真实 Agent 协作与固定范围

文件：原任务 8 的 agent_context/tools、chat/canvas_context、superchat；新增 `creation/delegations.py`，复用现有 chat conversation 持久化。

- [ ] 保存项目主创配置，每画布独立 conversation 与任务；切换恢复对应讨论，共享已确认项目事实。
- [ ] 发送时持久化 scope、input_refs、allowed_operations、acceptance；后续选中内容不改变已发委托。子代理只能缩小范围，读相关依赖不等于获得写权限。
- [ ] 真实 Agent 运行时调用公开领域操作和能力目录，写入结果检查对象存在，禁止旧 batch 引擎或直接文件写入。
- [ ] 任务展示实际满足／未满足项，不以 supplier success 或用户未采用决定无限运行；达到目标交付并停止。
- [ ] 测试切画布、选择变化、跨画布过期请求、专业代理扩权与伪造结果拒绝，运行原任务 8 测试和 conversation 隔离测试，双审提交。

## 任务 9：操作授权与严格预算

文件：原 approvals.py 和 ApprovalCard；新增 `creation/budgets.py`、`tests/creation/test_budgets.py`。

- [ ] 确认针对付费、替换采用内容、修改共享资产及有歧义的目标，不按剧本／分镜阶段设置门禁。
- [ ] 提案绑定 scope、对象输入版本及操作；集中卡展示目标、保持不变内容、范围、费用和授权项。用户明确采用即授权该操作，无冲突不重复询问。
- [ ] 金额用最小货币单位整数并记录币种。提交前同事务预留已知费用上界，余额扣除已发生与待结算，不允许并发超用。
- [ ] 无可靠上界的供应商暂停严格预算项，展示区间，独立显式授权；重试需次数和余额，两者不足不提交。任务状态未知先对账。
- [ ] 测试并发预算预留、重复请求、迟到结算、未知费用阻止提交、版本变化使旧授权失效。运行 approvals/budgets 测试，双审提交。

## 任务 10：统一真实执行与可靠恢复

沿用原任务 10 的明确文件与 19 节点能力表、执行线／引用线／来源线规则。新增验证节点任意子图、无集画布、供应商取消限制、授权和预算在真实提交处生效。调用现有 scheduler 和 RunStore，不复制模拟原型定时器。已完成分支不重复生成；停止只阻止后续提交并对账外部任务。后端 creation/freezone 针对性合集通过后双审提交。

## 任务 11A：候选、组级分支与预览定位

文件：新增 `creation/candidates.py`、`creation/content_groups.py`、`creation/provenance.py`；前端 `creation-workspace/{CandidateCompare,GroupVariants,PreviewReferences}.tsx`，复用原节点和预览工具。

- [ ] 候选记录源版本和生成运行，默认节点内收纳，不抢焦点；新内容以可编辑草案落图。
- [ ] 内容组是可拆并的组织方式，组级方案保存成员引用快照；整体或局部采用均明确范围，混用提示事实／素材冲突。
- [ ] 采用作为操作写新版本保留历史，下游过期提示不自动生成；候选可展开独立分支。
- [ ] 合成产物保存时间区间到镜头／素材／声音的来源映射，真实播放器暂停处定位相关节点，带入修改上下文；不能用固定时间点假数据代替。
- [ ] 测试冲突候选、切换浏览不采用、局部采用、组级采用、迟到结果、跨节点来源查询及预览时间定位，双审提交。

## 任务 11B：委托级撤销与跨画布组织

文件：原任务11 operations/store/projection；新增 `creation/content_organization.py`、`tests/creation/test_content_organization.py`。

- [ ] 一个 delegation 聚合 operation，默认按委托撤销，可局部选择；新版本恢复、字段CAS保护后续独立修改，保留媒体费用日志。
- [ ] move 与 reference 分开：move 改主要归属、展示原引用和运行影响；reference 新建固定版本采用记录、原画布不变。运行中范围不静默迁移。
- [ ] 分集提案展示边界、顺序和归属，用户编辑确认后通过领域操作创建集及关联，不将分集变为制作门槛。
- [ ] 验证重复撤销、局部冲突、运行中移动拒绝静默变更、跨集引用版本独立。针对性测试通过后双审提交。

## 任务 12–13：删除与验收

严格继承原任务12文件清单和共享能力提取规则，删除阶段导航、门禁、旧批量引擎及回填，保留 production_assets 共享服务。删除前保护既有未提交工作，不删用户数据。

最终验收按 PRD 第12节三入口和规则表执行，增加自由画布、自定义 Agent、模板、预算、组级分支、任务交付未满足项及预览定位；对照 V4 和现有产品视觉。真实生成、合成、播放与局部修改需供应商证据，费用授权不可从“继续开发”推定为无限预算。现有配置能运行的授权范围内验收；缺少明确付费授权时先完成不付费验证并给出具体任务报价和范围。

## 覆盖与执行记录

| PRD | 任务 |
| --- | --- |
| 三入口、构思／分集／自由画布 | 5B、6、11B、13 |
| Ontology、对象扩展、约定 | 5B、8A、8B |
| 基础／变体、手法、风格 | 7 |
| 自定义Agent、个人发布、独立对话 | 8A、8B |
| 目标、授权、预算、重试 | 8B、9、10 |
| 候选、组级方案、预览定位 | 11A |
| 撤销、跨画布移动／引用 | 11B |
| 现有样式与专业节点 | 6、7、10、13 |
| 物理删除旧流程 | 12 |

各项按失败测试 → 最小完整实现 → 针对性检查 → 规格审查 → 质量审查 → 仅本任务提交推进。状态同步 `docs/superpowers/reports/2026-10-13-agent-canvas-execution.md`。
