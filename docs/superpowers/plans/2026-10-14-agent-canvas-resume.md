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

**状态：进行中。7A1、A2a-1、A2a-2、A2a-3 已双审提交，分别为 `4317952`、`0784b2c`、`7b8bf35`、`99b111a`。A2b-1 已提交 `4290cf6`；A2b-2 的 production-assets、角色、场景直接入口、画布资产发布、场景 worker 发布组已提交 `3edd058`、`af82152`、`94e55fb`、`d49160b`、`0e0c258`。casting 入口测试上下文夹具独立修复为 `0e1aeca`。7B-1 固定引用 API 与原子节点投影已双审提交 `7b2e8a9`；7B-2a 前端保存协调已双审提交 `3d1d97e`，7B-2b 固定引用界面提交 `71e166a`。共享资产显式保存及 viewer writer 保护由 7B-2c 完成，提交与验证详见下文。当前执行 7B-3 风格／手法固定引用与作用域，尚未完成库界面、Agent 上下文和真实运行接入。**

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

7B-2 先完成保存协调（2a），再接固定引用完整界面（2b），最后补齐共享资产显式保存（2c），每批分别双审。保存协调提供实际工作空间实例隔离、排空已发 PUT 后暂停新 PUT（包括 beforeunload）、操作期间保留本地草稿、baseline/local/remote 三方合并与冲突提示。持久化待决操作请求身份，网络未知不盲目回滚，重新打开后按同一请求恢复；合并保留服务端 receipt、本地独立编辑、bookmarks/shotMetadata 与可用历史。随后资产按钮／拖放共用该能力，固定媒体绕开 latest hydration，资产中心返回显式 workspace；无明确目标不向默认画布写入。这一细分不缩减完整资产界面验收要求。

7B-2b 已双审提交 `71e166a`：固定引用浏览／注册应用／拖放、精确媒体槽升级、资产中心往返、独立副本和冲突恢复。初次合集 137 passed，修复合集 95 passed，末次专项 11 passed、规格独立 9 passed，类型检查通过；真实变体／全景加入、3D 副本、资产中心指定节点替换及两种视口检查见执行报告。

2c 保留现有 CommitDialog，明确新基础资产／变体／新版本的保存目标，已有目标携带用户选择时的版本凭据，持锁后 CAS 校验，冲突不自动采用 latest。补齐新发现的 scene director world/source/clear 及 pano correction 四个真实 writer 的 publication 保护；完整布景媒体与 manifest 改为一次服务端发布，替代客户端循环多次写入。新模式移除生成后 auto:true 隐式共享回填，明确保存与采用。道具、泛媒体不伪装成 character/scene 的 AssetRef；完整地图见执行报告。

2c 串行交付：**2c1 已双审提交 `2e93a34`**，四个现存 viewer writer 的完整读改写纳入可信场景 publication，165 passed、规格独立32 passed；**2c2 已双审提交 `95f4d46`**，实现持久请求 receipt 的共享发布服务，覆盖新基础／变体／已有版本、选择时凭据及媒体与完整 manifest 一次发布。末次服务与引用回归104 passed，规格独立8项无副作用与17项恢复重放通过；隔离真实 API 三模式、历史 receipt 重试与完整全景发布通过，无付费调用。**2c3 已双审提交 `9beccdb`**，接入节点保存和拖卡共享发布，移除新模式自动共享回填；122 tests、tsc-b、规格独立22 tests通过，真实三模式／完整场景／丢响应后刷新恢复通过。名称冲突写前预检修复独立双审提交 `f0b4f9d`，75服务+111关联测试、规格独立27通过。另固定资产按钮自动空位放置小修已双审提交 `ffc45d9`。

**7B-3（当前）**按服务与界面串行交付：3a已双审提交 `8faaa73`，可信固定快照、作用域绑定、只读预览/解析与显式CAS应用API；专项最终28 passed，之前含27专项的关联合集180 passed，规格独立28通过，真实API继承/覆盖/禁用/解除、双节点原子应用、适用性及receipt重放通过。3b正在复用库选择和作用域编辑界面，展示继承/覆盖/禁用来源与冲突。取消本级覆盖和明确禁用不能混同；group须为真实画布组及明确成员关系，不按视觉包含推断。历史快照不得重新解析latest，普通画布保存不能伪造或擦除服务端绑定。Agent上下文/真实执行消费分别在8B/10接入。每批独立双审。

7B-3b 已双审提交 `72f444a`：完整风格页、扩展风格抽屉三个入口、手法库及画布作用域配置已接入固定引用。8 文件 93 tests、末次专项 25 tests、规格独立 25 tests 和 tsc-b 通过。真实 1440/1280 分组、多节点一次应用、禁用/解除、丢响应刷新恢复、并发冲突保留选择通过。用户已允许独立测试并行执行；共享文件实现仍串行。

**7B-3c 已双审提交 `14ed193`**：保留另一套 `freezone/image/style-templates` 的 30 个特色模板和分类浏览，可信模板纳入同一固定目录及历史快照，新模式 ImageGen 入口不再写旧 styleTemplateId。后端33、独立旧图像入口148、前端最终70 tests及tsc-b通过；逆序响应修复经红绿回归与双审。实际UI模板应用、两入口一致显示、旧字段未写通过；实际编译消费仍在任务10接入。

**7B-3d 已双审提交 `8e7843f`**：单一真实 group 默认 group，混合组/节点必须明确选择范围；通用工具栏顶部空间不足时翻到底部，不改画布节点。前端 40 tests、后端继承 1 test、规格独立 7 tests、tsc-b 和质量审查通过。父级 1440/1280 普通点击保存按钮成功；实际分组保存后成员继承正确，节点已有禁用保留。截图 `/private/tmp/canvas-7b3d-toolbar-after-1280.png`。当前进入 8Aa 项目 Ontology 与约定后端，随后 8Ab 个人 Agent/模板及 8Ac 界面；实际对话生成/试用仍在 8B 闭环。

## 任务 8A：Agent 与模板、可扩展对象定义

文件：新增 `creation/{agent_profiles,creative_templates,ontology,creative_constraints}.py`；API `api/routes/creation_library.py`；前端 `creation-workspace/{AgentProfileEditor,TemplateLibrary,ProjectConstraints}.tsx`；各模块对应 tests/creation 测试。

当前按 8Aa 项目对象定义／约定后端、8Ab 个人 Agent／模板与项目采用服务、8Ac 界面串行实现。Product Manager 复核的既有需求验收路径为：创建或复制 → 编辑 → 保存到个人库 → 项目采用固定版本 → 项目局部调整 → 发布新版 → 主动升级。就近编辑保留原画布上下文，集中管理沿用现有视觉；发布预览、依赖和示例选择必须对应真实保存内容。两个项目验证采用版本与覆盖互不污染；升级前可比较配置，升级不静默清除覆盖、不触发生成。模板采用展示来源，未确认扩展不能暗中启用。8Ab/8Ac 完成仅代表配置管理闭环，对话生成、试用及实际消费须待 8B 接入，不能以模拟结果替代。

**8Aa 已双审提交 `1c0117b`**：项目定义草案／明确确认、固定历史、自定义对象与有类型关系、来源明确的约定和最小声明规则已落地。58 项专项及此前 106 项关联回归通过；审查发现的 Agent 修改用户确认约定 P1，以及直接／间接删除 scope 阻断其他配置 P2 均经红绿回归修复并复审通过。真实 10 次 HTTP 请求验证确认／幂等／硬规则冲突且原对象不变，最终服务重启后 7 个原请求结果一致。例外提案记录精确版本和输入，审批执行仍在 8B/9；自然语言语义判断与专业生成消费未包含。当前执行 8Ab 个人 Agent／模板及项目采用服务。

**8Ab 已双审提交 `7e6c151`**：个人不可变版本、项目固定副本、完整 Agent 字段覆盖、显式差异升级，以及模板固定依赖／显式示例／原子定义确认与实例化已实现。最终32项专项、60项既有API回归通过；资源来源项目和升级来源版本两处P2已红绿修复，规格独立3项、质量独立5项通过。真实Agent 15次HTTP、模板12次HTTP及最终来源升级验证通过。默认模板约定仍为draft，实际执行权限／主创指派／试用在8B接入。当前8Ac按完整UI链路分两批：8Ac-1 Agent个人库／项目配置／采用与升级；8Ac-2模板、对象定义与项目约定。两批沿用画布视觉、原地受控面板和侧栏就近入口，不引入阶段切页。

**8Ac-1 已双审提交 `1ad3769`**：画布和侧栏就近 Agent 配置入口、个人发布／复制／固定采用、项目字段覆盖和显式升级已接入，草稿与原请求可恢复。前端15项、最终后端37项、独立后端35项（最终窄修前）、tsc-b通过。升级生效预览、旧版本草稿恢复及Studio可移植字段不一致三项P2经红绿回归与双审修复。实际1280/1440 UI发布、采用、主动升级、保留项目覆盖及旧草稿比较通过。当前继续8Ac-2对象定义／约定与模板界面；实际主创指派、对话生成／试用仍属于8B。

**8Ac-2a 已完成双审**：创作对象／关系定义的可视化草稿与固定版本启用、项目约定的范围／来源／级别／历史与冲突恢复已接入。中文对象与属性名称默认自动生成稳定标识，字段可选title兼容旧定义和模板。最终前端35项、后端75项、tsc-b通过，规格独立20项、质量定向6项通过。实际1280/1440中文定义启用、约定草稿→硬约束、冲突不覆盖原文及真实组选区通过；范围键序假冲突、冲突双方展示均已修复。当前进入8Ac-2b模板发布／采用／示例启用界面。

- [ ] 分开持久化个人 Agent 版本、个人创作模板版本、项目采用版本及临时覆盖。定义对象／关系扩展必须显式确认才能启用，验证关系端类型。
- [ ] Agent 配置包含职责、方法、参考、技能权限及默认风格。对话生成配置草案，可视化查看、编辑、试用后保存，不能凭模型自报扩大工具权限。
- [ ] 模板发布预览包含对象定义、关系、规则和依赖；默认排除剧本、媒体、运行历史，只带用户勾选示例。个人库隔离鉴权，不引入市场。
- [ ] 约定支持 hard/preference/draft、source、scope；局部偏好可覆盖，硬约束冲突提出例外方案，推测不自动升级为事实。
- [ ] 测试项目临时覆盖不污染复用 Agent、另一用户不能读个人配置、发布版本不可变、主动升级、未确认类型禁止写入。运行各对应后端与前端测试后双审提交。

**8Ac-2b 已双审提交 `b501ee6`**：个人模板固定发布／复制、跨项目映射采用、显式原子启用示例、项目说明覆盖及主动升级均已接入。最终前端57项、独立后端75项、tsc-b通过。同一弹窗版本冲突后目录不刷新、跨标签恢复记录竞争两项P2已红绿修复并复审通过。真实A→B浏览器验收确认采用保持草稿，明确启用创建2个独立对象与1条关系；个人v2后B保留固定v1，显式升级保留项目覆盖与既有实例，默认硬约定始终draft。真实Web Lock竞争时零POST，释放后原预览重试成功。当前8Ac-recovery窄批修复Agent/settings经独立复现的同类跨标签问题（两个controller、两个Panel、两个测试）；随后8B首先接入每画布固定对话归属，再接入运行与范围委托。

## 任务 8B：真实 Agent 协作与固定范围

前置恢复修复已双审提交 `1d09845`：Agent/settings跨标签run与finish互斥、原请求编号与身份校验，65项关联测试、定向fixture修正2项、tsc通过；真实Agent保存清理与约定冲突后草稿恢复通过。模板提交为`b501ee6`，均保留原脏改，未推送。

**8B1a 已双审提交 `29117c9`，8B1b 接入前端**。按用户／规范项目ID／已应用的真实画布／索引会话联合授权，覆盖HTTP及WS所有会话入口；列表缺库只读返回空，不初始化或迁移。旧default不自动归属新画布，未知会话不能隐式创建。显式创建request_id幂等，绑定不可由scope.set改写。后台运行保留原会话身份。最终实际暂存快照7个测试文件46 passed，449个产品模块均来自快照，无工作区混入；真实API路由注册回归经红绿验证。提交包含必要既有会话基础，排除references/metadata等无关脏改。用户已允许并行测试，独立测试与规格审查并行、质量审查随后完成。前端及真实主创运行尚未完成；后者在8B2接入。

文件：原任务 8 的 agent_context/tools、chat/canvas_context、superchat；新增 `creation/delegations.py`，复用现有 chat conversation 持久化。

**8B1b 已双审提交 `e4b0fad`**：真实画布入口传递固定身份，当前画布可信列表确认后才挂载会话；创建请求持久恢复、跨标签互斥、用户／项目／画布／会话缓存隔离与旧实例回调拦截已接入。实际 index 快照8文件41 passed，最后恢复提示窄修独立8 passed，强制tsc通过。修复旧abort回调、旧clear响应删除新实例缓存、空态侧栏不能关闭及成功回执误提示恢复。真实同项目分集／自由画布分别创建和恢复历史，清理自由画布仅其generation变为2，分集保持1且历史完整；空构思画布列表仍为空，关闭正常。1280／1440截图已检查，未调用模型。提交纳入必要会话与Dock基础及一行既有测试fixture修复，保留其他脏改。当前进入8B2固定项目主创配置、持久委托和受限领域工具；运行接线图`/tmp/task08b2-runtime-map.md`，不能把现有通用HTTP工具直接当作新Agent授权边界。

- [ ] 保存项目主创配置，每画布独立 conversation 与任务；切换恢复对应讨论，共享已确认项目事实。
- [ ] 发送时持久化 scope、input_refs、allowed_operations、acceptance；后续选中内容不改变已发委托。子代理只能缩小范围，读相关依赖不等于获得写权限。
- [ ] 真实 Agent 运行时调用公开领域操作和能力目录，写入结果检查对象存在，禁止旧 batch 引擎或直接文件写入。
- [ ] 任务展示实际满足／未满足项，不以 supplier success 或用户未采用决定无限运行；达到目标交付并停止。
- [ ] 测试切画布、选择变化、跨画布过期请求、专业代理扩权与伪造结果拒绝，运行原任务 8 测试和 conversation 隔离测试，双审提交。

8B2按三批完成纵向链：a 项目主创指派与固定委托服务/API；b 受限领域工具及内置Agent首用；c 实际支持的运行适配器与现有侧栏/节点委托UI。a当前实施，契约`/tmp/task08b2a-proposed-contract.md`，相关独立HEAD后端基线129passed。主创只引用项目adoption身份，新任务冻结当时revision/effective；用户明确改项目覆盖/主动升级后新任务直接消费，旧任务与恢复不变，不加第二次主创升级。scope明确canvas/nodes/group，冻结真实成员和内容指纹，纯布局变化不使输入失效；单root写域一张host画布，跨画布输入显式只读，child只能缩小固定输入/写集合/操作能力。a包含revoke元数据阻断后续授权，不虚报provider取消；读取/预览不初始化库，客户端不能自报authority或完成状态。固定事实、草案与未评估来源分开，调用既有受控领域服务，禁止通用HTTP/shell冒充用户确认。内置或已有个人Agent必须可直接选用，空个人库不阻塞首次创作。a仅保存prepared契约，不声称模型/节点运行已完成；8B2最终必须有真实可用草案或提案、任务状态及恢复，后续预算/19节点执行/完整候选仍属9/10/11。

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

### 8B2a 固定委派交付记录（2026-10-15）

已双审提交 `24a2748`，覆盖项目主创指派、只读预览、固定委派创建／历史／回执、子委派缩权及撤销后续权限。此前“a 当前实施”状态由本记录更新为完成；8B2 整体仍未完成。仅 5 个本批文件，保留其他原始脏改。

任务固定采用版本、节点内容及成员链、会话代次、逐对象约定和逐节点方向；基础／变体资产由可信映射分别解析约定，草案与已确认约定分开。预览使用既有画布共享锁及只读 SQL 事务；纯布局变化不使输入失效。非法标识在 SQL 前拒绝，空容器不能绕过父委派校验。

最终 HEAD 加本批文件的独立快照 7 个测试文件 **213 passed**，10 个弃用警告，451 个产品模块全部来自快照；实际 index 与快照 5/5 文件一致。规格审查修复资产对象约定遗漏后 PASS，质量审查修复非法 ID 返回 500 后 APPROVE。真实 API 验证主创选择→两节点预览→创建→同请求回放→读取→撤销；委派 `b19237a55e094357a6989d3a41236373` 已撤销，无模型调用。该真实 API 验证先于最后两项窄修，最终修复由上述独立快照测试覆盖。

材料：`/tmp/task08b2a-contract.md`、`/tmp/task08b2a-independent-release.log`、`/tmp/task08b2a-spec-review.md`、`/tmp/task08b2a-quality-review.md`、`/tmp/task08b2a-live-qa.json`。下一批 8B2b 接受限领域 broker 与内置 Agent 首用，定位图 `/tmp/task08b2b-builtin-map.md`；之后 8B2c 接真实支持的 adapter 与现有侧栏／节点委托 UI，适配器边界图 `/tmp/task08b2-adapter-boundary.md`。prepared 仍仅表示委派已保存，不能宣称实际模型执行、结果交付或供应商取消已完成。

### 8B2b1 内置 Agent 交付记录（2026-10-15）

双审提交 `a98048a`，8 个 owned 文件。可信内置目录含通用主创、文本编剧；只读目录不 seed 个人库，显式采用保存项目固定副本和 builtin 来源。同来源显式升级沿用 CAS、keep/drop 与 receipt，跨个人／内置来源升级拒绝。个人 source 形状与模板行为保持兼容。已有配置面板准确区分内置／个人版本，内置条目不会按相同 ID 进入个人升级入口。新主创选择 UI 尚未接入，留 8B2c。

独立 HEAD 加 owned 快照：后端 5 文件 **119 passed**（10 个弃用警告），446 个模块全部来自快照；前端配置面板 **20 passed**；隔离 `/tmp` 构建缓存后 `tsc -b --force` exit0。8/8 实际 index 与验证快照一致，spec PASS、quality APPROVE。真实 API 采用 builtin:creative-lead v1，得到项目副本 `e7f6cfe6303e46fda4104b6cbc51ae2c`，同请求回放相同；页面“内置 v1”与个人来源并存，截图 `/tmp/canvas-builtin-agent-adoption.png` 已检查。未调用模型。API8781当前 session58468，日志 `/tmp/task08b2b1-api.log`，Vite5173保持。

材料 `/tmp/task08b2b1-contract.md`、`/tmp/task08b2b1-independent.log`、`/tmp/task08b2b1-independent-frontend.log`、`/tmp/task08b2b1-independent-tsc.log`、`/tmp/task08b2b1-spec-review.md`、`/tmp/task08b2b1-quality-review.md`。下一步 8B2b2 受限领域 broker，契约 `/tmp/task08b2b-proposed-contract.md` 第2–6节；采用真实 story_fragment 文本候选加 delegation_outputs 关联，不能将授权 proposals 表混作创作成果。其后 8B2c 接实际 adapter 与现有侧栏／节点委托 UI。整个 PRD 仍未完成。

### 8B2b2 受限领域工具交付记录（2026-10-15）

双审提交 `1f929aa`，5 个 owned 文件。四个真实动作：固定上下文读取、独立 story_fragment 文本候选、新对象定义草案、媒体文字方案；原节点／资产不改，不开放通用 HTTP、shell、用户确认或付费生成。当前 ACL 在项目锁取得后复核，新动作检查会话代次／祖先撤销／输入语义／硬约定变化；产物、outputs 关联、操作和回执同事务。自然语言符合度明确 not_evaluated，不把工具成功当任务完成。

独立 HEAD 加 owned 快照六文件 **154 passed**，10 个弃用警告，447 个模块全部来自快照；5/5 index 一致。spec PASS、quality APPROVE。真实 API：builtin 项目采用→委派 `a0fc3e37406b468da924965990f74082`→人工验收候选对象 `5eaf1be4ea82484e9da545bd078092f5` v1→固定文本读取→撤销→原请求重放同结果；原节点预览 proof 不变，结果仅一个。没有模型调用。此委派已撤销，历史保留。API8781当前 session74020，日志 `/tmp/task08b2b2-api.log`。

材料 `/tmp/task08b2b2-contract.md`、`/tmp/task08b2b2-independent.log`、`/tmp/task08b2b2-spec-review.md`、`/tmp/task08b2b2-quality-review.md`、`/tmp/task08b2b2-live-qa.json`。8B2c UI 接点地图 `/tmp/task08b2c-ui-map.md`：多选取 nodes.selected；使用固定 canvas/conversation，不能用客户端 sessionEpoch 代服务端 generation；委派 outputs 独立读取，不冒充 canvas Run 卡片。Shell／superchat-panel／use-superchat 仍含原 dirty，接线须捕获 baseline、仅提交必要增量。下一步实际零工具 model_api Decision 适配器与现有侧栏／节点委派 UI，仍须完成真实产出、停止与恢复验收；整体 PRD、旧流程删除及任务9–13仍未完成。

### 8B2c 实施决定

用户“下一步”后继续 c1 真实运行服务 → c2 现有侧栏 UI，不以纯后端代替纵向闭环。实施契约 `/tmp/task08b2c-proposed-contract.md`；运行配置图 `/tmp/task08b2c-runtime-config-map.md`。采用持久 run lease 加有界 BackgroundTasks；默认1、最多3次明确模型调用（root/child合计），Decision最多4个领域动作，SDK/输出自动重试为0。GET不驱动任务，未知提交不自动重发；停止与实际效果在同事务核对 fence。现有可信计量保留，金额不明显示unknown，不冒称任务9严格货币预算。可用性只读元数据，不触发配置迁移或凭据解密。c1冻结审查后立即接c2主创首用、多选委派、真实结果、定位、停止与恢复。模型真实验收仍须有可用配置与明确调用范围；mock不得冒称真实模型通过。运行时独立基线50passed／8warnings，网络阻断、86模块来自快照。

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
