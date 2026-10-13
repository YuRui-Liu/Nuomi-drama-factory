# Agent、节点与画布创作工作空间实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 在 `feat/canvas` 将新项目创作统一到每集画布与主 Agent，保留现有视觉、19 类节点能力、资产中心、手法库和风格库，删除旧分页规则流水线。

**架构：** 引入薄层创作对象、基础资产／变体引用、版本与操作契约；人工节点操作和 Agent 工具共用契约。复用现有画布、运行存储、调度器、模型供应商及资产编辑器。通过持久化操作日志连接语义更新与画布结果应用，避免两套可写内容。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、pytest；React 19、TypeScript、TanStack Router/Query、Zustand、React Flow、Vitest。

---

## 已批准依据与执行约束

- 规格：`docs/superpowers/specs/2026-10-13-agent-canvas-creation-design.md`，提交 `776e7e8`。
- V3 原型：`.superpowers/brainstorm/27119-1791899331/content/canvas-existing-v3.html`。用户已确认基础／变体交互；V1 草图作废。原型中的重复示意图、演示聊天和内存状态不得作为生产实现。
- 工作目录：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。用户已指定当前分支且存在大量未提交工作，计划阶段不创建丢失这些工作的 worktree，不切分支、不整体还原。
- 每项任务开始前记录其目标文件已有差异；只暂存本任务拥有的修改，禁止 `git add .`。删除已有未提交文件前保留可恢复快照及归属记录；快照不包含密钥或生成媒体。
- 本计划只服务一个新项目创作闭环，任务 1–5、6–9、10–13 是顺序交付阶段，不能以隐藏旧导航替代删除旧执行路径。
- 文中新增模块、接口和测试是拟实现内容。已检查现有源码，不声称下列接口已经存在。

## 文件职责

新增后端包 `src/novelvideo/creation/`：

| 文件 | 唯一职责 |
| --- | --- |
| `__init__.py` | 包入口，不执行初始化副作用 |
| `models.py` | 对象、版本、引用、作用域、操作请求与结果类型 |
| `store.py` | SQLite 事务、对象版本、关系、集画布索引和幂等日志 |
| `asset_adapter.py` | 现有角色 identity／场景 variant 到语义引用的适配 |
| `operations.py` | 人工与 Agent 共用的业务操作、授权范围与版本校验 |
| `approvals.py` | 持久化提案、版本指纹与确认判定 |
| `projection.py` | 操作日志到现有画布的可恢复应用 |
| `agent_context.py` | 当前集、选中节点、资产和库的结构化上下文 |
| `agent_tools.py` | 可调用操作目录、主 Agent 委托范围与专业工具输出校验 |
| `project_mode.py` | 新项目模式与旧格式只读识别 |

新增薄 API `src/novelvideo/api/routes/creation.py`，从现有鉴权依赖获得项目目录与权限。新增前端目录 `frontend/src/features/creation-workspace/`，包含 `EpisodeCanvasWorkspace.tsx`、`EpisodeCanvasSwitcher.tsx`、`AssetReferencePicker.tsx`、`NodeReferenceDetails.tsx`、`ApprovalCard.tsx` 和 `contracts.ts`；API 与 Query 分别置于 `frontend/src/api/creation.ts`、`frontend/src/lib/queries/creation.ts`。

修改复用点：`features/freezone/FreezoneShell.tsx`、`features/canvas/Canvas.tsx`、`features/canvas/domain/canvasNodes.ts`、`features/canvas/domain/nodeRegistry.ts`、`features/canvas/application/useCanvasRun.ts`、`features/canvas/infrastructure/orchestrationGateway.ts`、`features/superchat/superchat-panel.tsx`、`src/novelvideo/chat/agent_tools.py`、`src/novelvideo/chat/canvas_context.py`、`src/novelvideo/freezone/orchestration/{graph,store,scheduler,projection,planning}.py`。

不复制现有大型节点组件、不复制资产中心编辑器。工作空间仅负责组织与引用，将现有 `FreezoneShell` 中旧主线提交／投影职责移除，不顺带重构无关工具。

## 统一契约

在任务 1 定义下面的字段名，后续任务不得另起一套同义结构。

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class AssetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_asset_id: str = Field(min_length=1)
    variant_id: str | None = None
    revision: int = Field(ge=1)

class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    episode_id: str
    canvas_id: str
    node_ids: list[str] = Field(default_factory=list)

class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal["object.create", "object.update", "asset.variant.create",
                  "asset.bind", "graph.patch", "node.run", "result.adopt",
                  "proposal.confirm", "operation.undo"]
    scope: Scope
    expected_revisions: dict[str, int] = Field(default_factory=dict)
    payload: dict
    proposal_id: str | None = None

class OperationResult(BaseModel):
    operation_id: str
    status: Literal["applied", "awaiting_confirmation", "running", "conflict"]
    object_refs: list[dict] = Field(default_factory=list)
    canvas_revision: int | None = None
    run_id: str | None = None
    candidate_id: str | None = None
```

HTTP 约定：`GET /api/v1/projects/{project}/creation/episodes` 返回集与画布对应；`POST .../creation/episodes` 创建一集及主画布；`GET .../creation/context?episode_id=...&canvas_id=...` 查询上下文；`POST .../creation/operations` 接受 `OperationRequest`；`GET .../creation/operations/{operation_id}` 查询结果；`GET .../creation/assets/{base_asset_id}/uses` 查询跨集引用。所有写入以服务端解析的权限和目录为准，不信任请求中的项目字段。

错误约定：422 `invalid_contract`，403 `scope_violation`，409 `revision_conflict`／`idempotency_conflict`／`approval_stale`，409 `legacy_project_readonly`，503 `capability_unavailable`。错误响应保留可显示的中文说明及定位对象 ID；不返回供应商凭证。

## 任务 1：建立语义契约与项目内持久化

**文件：** 创建 `creation/__init__.py`、`creation/models.py`、`creation/store.py`；创建 `tests/creation/test_store.py`、`tests/creation/test_models.py`。

- [ ] 添加 `AssetRef` 非法 revision、未知字段、空 ID 测试；执行 `uv run pytest tests/creation/test_models.py -q`，确认首先因目标模块不存在而失败。
- [ ] 写入上述模型。对象类型固定为 project、episode、story_fragment、character、scene、storyboard、media、style、technique；节点操作实例独立保存，不给布局分组创造业务对象。
- [ ] 在项目 `creation/objects.sqlite3` 创建下列表结构；外键开启，事务使用 `BEGIN IMMEDIATE`，不持有事务等待网络。

```sql
CREATE TABLE objects(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
  base_asset_id TEXT, variant_id TEXT, current_revision INTEGER NOT NULL);
CREATE TABLE object_versions(object_id TEXT NOT NULL, revision INTEGER NOT NULL,
  payload TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(object_id, revision), FOREIGN KEY(object_id) REFERENCES objects(id));
CREATE TABLE relations(id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
  target_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE episode_canvases(episode_id TEXT PRIMARY KEY, canvas_id TEXT UNIQUE NOT NULL);
CREATE TABLE operations(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
  request_hash TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
  result TEXT, actor_id TEXT NOT NULL);
CREATE TABLE projection_outbox(operation_id TEXT PRIMARY KEY,
  canvas_id TEXT NOT NULL, patch TEXT NOT NULL, applied_revision INTEGER);
CREATE TABLE proposals(id TEXT PRIMARY KEY, scope TEXT NOT NULL,
  input_hash TEXT NOT NULL, authorized_operations TEXT NOT NULL,
  decision TEXT, actor_id TEXT);
```

- [ ] `ObjectStore(project_dir)` 提供 `create(kind, payload, actor_id)`、`read(id, revision=None)`、`update(id, expected_revision, payload, actor_id)`；更新成功产生新不可变版本，失败不改 current_revision。所有 ID 作为数据处理，不拼接文件路径。
- [ ] 在 `test_store.py` 验证：重开 store 后仍可读取 r1/r2；两个调用同时更新 r1 仅一个成功；错误写入整笔回滚。运行 `uv run pytest tests/creation/test_models.py tests/creation/test_store.py -q`，全部通过后仅提交本任务文件，消息 `feat(creation): add versioned object contracts and store`。

## 任务 2：基础资产与变体适配

**文件：** 创建 `creation/asset_adapter.py`、`tests/creation/test_asset_adapter.py`；修改 `api/routes/character_casting.py`、`api/routes/scenes.py` 中对应领域调用点，不改旧 API 的通用资产语义。

- [ ] 先测试角色基础形象的 `variant_id=None`，身份变体映射已有 `identity_id`；场景变体映射已有 `base_scene_id` 与 `variant_id`。运行 `uv run pytest tests/creation/test_asset_adapter.py -q` 确认测试未通过。
- [ ] 新增 `AssetAdapter` 的 `resolve(ref)`、`list_variants(base_id)`、`create_variant(base_id, name, delta, request_id)`、`publish_revision(base_id, variant_id, expected_revision, payload)`。复用现有资产创建服务；SQLite 只保存稳定映射、不可变引用快照和操作记录，不能成为第二个独立可写资产库。
- [ ] 基础资产更新与变体更新分别索引；名称仅用于展示，不用名字作为跨集身份。新建变体检查基础类型，场景变体必须属于同一地点；名称相同只作为候选，不在有歧义时静默合并。图片候选未采纳前不能替换已发布引用。
- [ ] 添加下列测试数据并验证映射：

```python
CASES = [
    ("character", "ayao", None, "基础形象"),
    ("character", "ayao", "rain-coat", "雨夜外出"),
    ("scene", "gate", None, "基础场景"),
    ("scene", "gate", "rain-night", "雨夜"),
]
```

- [ ] 覆盖重复幂等请求、基础不存在、变体属于其他基础、独立地点误作变体、角色换装保持年龄阶段五个失败场景。测试通过后提交 `feat(creation): adapt base assets and reusable variants`。

## 任务 3：统一业务操作与集内引用

**文件：** 创建 `creation/operations.py`、`api/routes/creation.py`、`tests/creation/test_operations.py`、`tests/creation/test_api.py`；修改 `api/__init__.py` 注册路由。

- [ ] API 测试先构造项目 A 与 B，并确认 A 用户不能引用 B 资产、操作 B 集或伪造 canvas 所属；运行 `uv run pytest tests/creation/test_operations.py tests/creation/test_api.py -q` 确认失败。
- [ ] `OperationService.apply(actor, request)` 依次验证：真实项目权限 → 集画布对应 → 节点归属 → 对象版本 → 幂等键和内容 hash → 操作专属 payload → 确认范围。actor 从登录依赖取得，不允许客户端提交。
- [ ] `asset.bind` 的明确 payload 如下；只修改当前集目标节点的引用，不更新基础资产本身。

```json
{"node_id":"character-reference-1","asset_ref":
 {"base_asset_id":"ayao","variant_id":"rain-coat","revision":1}}
```

- [ ] `graph.patch` 校验新增／更新／删除节点所有权与字段白名单；客户端不得用对象更新写生成 URL 或成功状态。`node.run` 只传执行意图，实际结果由可信执行器验证后写入。
- [ ] 同一 request_id 相同内容返回同一操作结果，不同内容 409。验证第二集改绑后第一集仍引用基础形象；变体可以再被第三集复用。测试全部通过后提交 `feat(creation): unify scoped human and agent operations`。

## 任务 4：画布投影、恢复与编辑冲突

**文件：** 创建 `creation/projection.py`、`tests/creation/test_projection.py`；修改 `freezone/orchestration/projection.py`、`chat/canvas_context.py`。

- [ ] 添加故障注入用例：语义事务已提交但画布还没保存；画布已保存但 outbox 尚未标记完成。执行 `uv run pytest tests/creation/test_projection.py -q` 确认尚未实现恢复。
- [ ] 语义事务同时写 operation 和 outbox；应用器通过现有 `save_canvas` 的幂等键及锁应用 patch，成功后标记 applied_revision。重启扫描未完成 outbox，不能再次提交付费任务。
- [ ] 节点新增字段只包含引用与展示所需的投影；输入引用版本是内容的权威值，用户拖拽位置和尺寸仍由画布保存。结果只写本次操作拥有的字段；新输入与旧结果冲突时保留 candidate，不覆盖当前输入。
- [ ] 测试布局变动可合并、相同输入冲突保留候选、同操作恢复两次只落一个节点和一条来源关系。确保旧画布历史恢复不绕开语义版本校验。通过后提交 `feat(creation): recover semantic canvas projections safely`。

## 任务 5：新项目格式与每集主画布

**文件：** 创建 `creation/project_mode.py`、`tests/creation/test_episode_workspace.py`；修改 `api/routes/projects.py`、`api/routes/creation.py`、`frontend/src/api/projects.ts`。

- [ ] 首先测试没有新模式标记的旧项目：查询不得初始化数据库、创建画布或修改项目文件；写操作返回 `legacy_project_readonly`。
- [ ] 新建项目记录 `creation_mode="agent_canvas_v1"`；创建集在同一语义事务记录 episode 对象和唯一 canvas 索引，再通过 outbox 创建画布。重复创建请求返回同一集与画布。
- [ ] 不迁移历史文件；旧项目卡片仍可被识别，进入时显示“不支持在新工作空间编辑”并提供创建新项目入口，不自动改模式标记。
- [ ] 添加两集创建、重复请求、创建中断恢复和未知 canvas ID 用例。运行 `uv run pytest tests/creation/test_episode_workspace.py -q`；通过后提交 `feat(creation): create episode canvases for new projects`。

## 任务 6：集画布工作空间与保留的库入口

**文件：** 以下前端路径均相对 `frontend/src/`：创建 `features/creation-workspace/contracts.ts`、`features/creation-workspace/EpisodeCanvasWorkspace.tsx`、`features/creation-workspace/EpisodeCanvasSwitcher.tsx`、`api/creation.ts`、`lib/queries/creation.ts`；修改 `routes/_app/projects.$project/freezone.lazy.tsx`、`features/freezone/FreezoneShell.tsx`、`components/layout/project-navigation-routes.ts`、`components/layout/project-header-navigation.tsx`。测试 `frontend/src/__tests__/features/creation-workspace/episode-canvas.test.tsx`。

- [ ] 用 MSW 提供两集分别映射 canvas-a/canvas-b，测试切集后加载对应画布，未保存状态在离开前提交或明确报告错误，返回第一集能还原其 viewport。
- [ ] `EpisodeCanvasWorkspace` 复用现有 ReactFlowProvider 和 FreezoneShell；URL 固定采用 `/projects/$project/freezone?episode=ID`，canvas_id 由服务端集映射获得，避免 query 参数跨集串写。旧 `?canvas=` 不用于新项目创建主画布。
- [ ] 顶部主创作导航仅保留创作画布、资产中心和视觉风格，手法库延续现有入口；任务、费用及设置以原有管理入口或菜单保留，Agent 作为侧栏。先断开新项目对旧入口的导航，任务 12 再删除路由源码。
- [ ] 加入加载、无集创建、找不到集、旧项目不支持和切集失败状态。现有 dark tokens、NodeHeader、OperationPanelShell、底部工具条和悬浮助手样式不重做。
- [ ] 运行 `cd frontend && pnpm exec vitest run src/__tests__/features/creation-workspace/episode-canvas.test.tsx`。检查键盘切集、窄窗口不遮挡新增节点入口后提交 `feat(canvas): open episode workspaces with existing canvas UI`。

## 任务 7：基础／变体选择器和关系详情

**文件：** 创建 `features/creation-workspace/{AssetReferencePicker,NodeReferenceDetails}.tsx`；修改 `features/freezone/AssetLibraryPanel.tsx`、`components/assets/character-delivery/character-delivery.tsx`、`components/assets/scenes-panel.tsx`、`features/canvas/domain/canvasNodes.ts`；测试 `src/__tests__/features/creation-workspace/asset-reference.test.tsx`。

- [ ] 渲染阿遥基础、雨夜外出、年轻形象三个选项；验证选中变体本身不改画布，点击应用才提交 `asset.bind`。应用失败保持原引用并允许重试。
- [ ] 选择器复用现有基础／identity 切换方式和场景属性／变体方式，创建表单保留年龄阶段、服装／状态描述等现有能力。不复制一个只有图像卡片的简化资产中心。
- [ ] 在 `contracts.ts` 定义对应引用字段：

```ts
export type AssetReference = {
  base_asset_id: string;
  variant_id: string | null;
  revision: number;
};
```

- [ ] 节点显示业务名称与基础／变体；引用详情展示归属、实际 revision 和下游影响。场景选择器不把不同地点当作昼夜变体。三个库的引用回到当前节点或 Agent 上下文，不改变所选集。
- [ ] 执行 `cd frontend && pnpm exec vitest run src/__tests__/features/creation-workspace/asset-reference.test.tsx`，覆盖两集不同引用、创建后未自动采用、已存在变体复用、引用过期错误。提交 `feat(canvas): bind base assets and variants explicitly`。

## 任务 8：主 Agent 与专业委托

**文件：** 创建 `creation/agent_context.py`、`creation/agent_tools.py`、`tests/creation/test_agent_context.py`、`tests/creation/test_agent_scope.py`；修改 `chat/agent_tools.py`、`chat/canvas_context.py`、`features/superchat/{superchat-panel,canvas-references}.tsx`。

- [ ] 首先测试选中两个节点时返回两者明确引用和必要只读依赖，不能把整个项目所有内容默认为可编辑范围。传入不属于当前集节点返回 403。
- [ ] 注册查询上下文、查询基础／变体、查询风格／手法、提交操作、查询操作、创建提案、专业委托工具。写工具均调用任务 3 的服务；禁止直接文件写入和旧主线 batch 工具作为新模式执行路径。
- [ ] 主 Agent 委托结构为 `{task_id, specialty, scope, input_refs, allowed_operations, acceptance}`。specialty 支持编剧、角色设计、分镜、音视频和合成，沿用现有运行时设置；子任务只能缩小 scope，工具执行器再次校验。
- [ ] 专业 Agent 返回 `{task_id, proposals, operation_ids, output_refs, status}`，必须验证输出对象确实存在。主 Agent 汇总真实持久化任务事件，不凭自然语言“完成”标记节点成功。
- [ ] 接入不同题材策略为上下文配置，不新建 Agent 市场。侧栏选择引用、清空引用、多选引用和点击定位复用现有 superchat 组件。
- [ ] 执行 `uv run pytest tests/creation/test_agent_context.py tests/creation/test_agent_scope.py -q`；加入专业 Agent 扩权、伪造 output_ref、重复回调、切集后过期请求四个用例。提交 `feat(agent): coordinate scoped canvas creation tools`。

## 任务 9：关键确认与变更失效

**文件：** 创建 `creation/approvals.py`、`features/creation-workspace/ApprovalCard.tsx`、`tests/creation/test_approvals.py`、`frontend/src/__tests__/features/creation-workspace/approval-card.test.tsx`；修改 `creation/operations.py`、`features/superchat/superchat-panel.tsx`。

- [ ] `proposal.confirm` 必须带用户决定、proposal ID 和输入指纹。直接执行未确认的受控提案返回 awaiting_confirmation，不能默默提交供应商任务。
- [ ] 对当前提案按对象 ID、revision、操作种类及 scope 计算 canonical hash；确认绑定这些值。已授权同一范围不重复询问；范围扩大、内容版本变化或换变体后必须重新确认。
- [ ] 确认卡显示可定位节点、创作决定、输入来源和下一步范围；支持认可、修改意见及跳到节点直接编辑。按钮提交期间禁用，重复点击使用相同 request_id。
- [ ] 运行 `uv run pytest tests/creation/test_approvals.py -q` 与前端 approval-card 用例，覆盖陈旧确认、另一集提案、重复确认、明确授权继续四种情况。通过后提交 `feat(agent): persist scoped creative approvals`。

## 任务 10：将现有节点操作接到统一执行契约

**文件：** 修改 `features/canvas/{domain/nodeRegistry.ts,application/useCanvasRun.ts,infrastructure/orchestrationGateway.ts}`、`freezone/orchestration/{graph,capabilities,planning,store,scheduler,projection}.py`；新增 `tests/creation/test_node_parity.py`、`tests/creation/test_execution_recovery.py`。

- [ ] 先对现有 19 种节点建立能力清单快照：输入、输出、纯编辑／生成／交互操作、调用入口、真实可用性。测试人工和 Agent 调用同一操作构造相同锁定输入。
- [ ] `execute_input` 连线参与依赖；`asset_reference` 解析锁定产物；`provenance` 仅追溯。延续现有 contextLink 的不执行语义，拒绝执行环，不以画布分组或坐标决定顺序。
- [ ] 现有 RunStore 继续负责供应商尝试和运行记录；creation operation 保存 run_id。生成成功先验证媒体，再通过任务 4 投影更新，页面关闭不终止服务端运行。
- [ ] 将规划器固定叙事组假设移到题材策略，允许用户任意单节点、选中子图和合成目标执行。已有 3D、音频、全景、Skill 和专业导演节点继续按现有能力适配，不用空执行器宣称支持。
- [ ] 在新测试中注入超时、重复提交、生成成功后投影崩溃、局部失败、取消后仍收到供应商结果、服务重启、上游变更。断言未确认提交不盲重试，复用成功分支，结果不重复落图。
- [ ] 运行 `uv run pytest tests/creation/test_node_parity.py tests/creation/test_execution_recovery.py tests/freezone/orchestration -q`。提交 `feat(canvas): share reliable node execution across human and agent`。

## 任务 11：局部撤销与产物版本选择

**文件：** 修改 `creation/{operations,store,projection}.py`，创建 `tests/creation/test_undo.py`；修改 `features/canvas/application/useCanvasRun.ts` 与画布历史入口。

- [ ] 撤销命令引用 operation_id 与当前 expected_revisions；测试撤销改绑只恢复该集引用，基础资产和其他集不变。
- [ ] 每个可撤销操作保存旧引用／内容和拥有字段；撤销作为新操作及新版本提交，不删除历史日志、供应商任务或已生成媒体。
- [ ] 输入已再次修改时拒绝自动撤销覆盖，显示冲突。运行中撤销先停止后续调度，已接单任务仍对账，产物保留为候选；对话明确费用不会撤回。
- [ ] 测试重复撤销、陈旧版本撤销、部分字段合并和取消后的迟到结果，执行 `uv run pytest tests/creation/test_undo.py -q`。提交 `feat(canvas): undo scoped edits without losing generated results`。

## 任务 12：清除旧创作流程与专属依赖

**文件：** 删除下列旧路由的页面入口；修改 `api/__init__.py`、`components/layout/project-navigation-routes.ts`、`AGENTS.md` 和相关生产说明；写 `docs/superpowers/reports/2026-10-13-canvas-removal-ledger.md` 记录每项删除及保留能力去向。

明确旧页面清单：

```text
frontend/src/routes/_app/projects.$project/ingest.tsx
frontend/src/routes/_app/projects.$project/creation.tsx
frontend/src/routes/_app/projects.$project/production.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/index.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/overview.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/sketches.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/video.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/audio.lazy.tsx
frontend/src/routes/_app/projects.$project/episodes.$episode/compose.lazy.tsx
```

- [ ] 对每个旧路由和 FreezoneShell 的 commit／projection 依赖用 `rg` 记录调用者。删除记录必须列出“原能力、保留调用者、替代入口、可删模块”；同时核对已有未提交差异，不能把其他工作的改动误删。
- [ ] 节点、资产中心、手法库、风格库仍使用的共享服务先改接领域操作，再移除旧消费者。`production_runs`、`production_assets`、`narrative_groups` 的旧批量／阶段接口按调用图逐项移除；不能整目录删除其中仍服务节点的通用生成器。
- [ ] 删除旧主线素材投影、提交回填、状态同步路径；保留 `freezone/orchestration/projection.py` 的真实画布结果应用，它不是旧主线回填。两个概念不得仅因同名 projection 一起删除。
- [ ] 用构建生成器更新 TanStack 路由，不手改生成路由表。旧深链接显示明确不可用或返回当前项目画布，不能恢复隐藏的旧流程。
- [ ] 移除只验证已删除功能的测试，保留并迁移共享行为回归；新增 `tests/creation/test_no_legacy_execution.py`，通过替身在新制作链路调用旧阶段引擎时立即抛错。
- [ ] 修改仓库生产规则，使 Agent 使用公开画布操作和运行契约，移除“所有创作必须走旧叙事组流水线”的指导；保留不绕过供应商、并发和真实产物检查要求。
- [ ] 执行 `uv run pytest tests/creation tests/freezone -q` 与 `cd frontend && pnpm run build`。编译错误按引用链解决，不靠空壳旧组件或空实现绕过。提交 `refactor(creation): remove legacy staged workflow and backfill paths`。

## 任务 13：真实链路与视觉验收

**文件：** 新建 `docs/superpowers/reports/2026-10-13-agent-canvas-acceptance.md`；图片证据写入项目忽略的验证目录，不提交生成媒体。

- [ ] 新建隔离验收项目，准备两集、一个基础角色、一个服装变体、一个基础场景及昼夜变体。确认已有项目目录未修改；记录项目 ID、模式和运行 ID，不记录凭证。
- [ ] 通过主 Agent 创建第一集创作子图，用户手动添加参考节点并局部改提示词；确认关键分镜，调用已配置供应商完成一个真实片段和成片合成，实际播放并检查明显遗漏。
- [ ] 第二集引用同一基础角色的另一变体，验证第一集的输入引用和产物不变；查看各集引用与来源关系，检查手法／风格库引用可见。
- [ ] 运行中关闭浏览器再打开，检查任务恢复；制造一个无效节点输入并局部修复，确认成功分支不重复生成。模拟超时／提交不明由自动测试覆盖，不为验证故障重复购买媒体。
- [ ] 对比现有界面和批准的 V3，至少在 1440×900 与 1280×720 检查画布、节点菜单、选中操作区、侧栏、资产基础／变体、手法库、风格库。键盘可到达确认与关闭按钮；移动／缩放及多选引用不互相抢焦点。
- [ ] 报告分别记录代码测试、交互证据、真实运行结果和限制。供应商不可用则明确真实验收未完成，不把 mock 成功写成可交付。
- [ ] 最后执行 `git diff --check`，按改动范围完成前端测试、构建、后端集成测试；只在有新变更或失败时重跑。提交验收报告 `docs: record agent canvas acceptance evidence`。

## 自检与覆盖

| 规格要求 | 对应任务 |
| --- | --- |
| 新项目范围、旧项目不改写 | 5、12、13 |
| 现有视觉、画布及节点保留 | 6、7、10、13 |
| 每集自由画布与跨集资产 | 2、3、5、6、7 |
| Ontology 对象／关系／版本 | 1、2、3、4 |
| 基础／变体与修改历史分离 | 2、7、11、13 |
| 主 Agent、专业委托、不同题材 | 8、10 |
| 关键确认与范围控制 | 3、8、9 |
| 冲突、取消、幂等、重试、恢复 | 4、9、10、11 |
| 保留三个库并删除旧流程 | 7、12 |
| 真实成片及视觉证据 | 13 |

字段一致性：基础资产 `base_asset_id`、变体 `variant_id`、修改记录 `revision`；`Scope` 始终包含 project、episode、canvas。无数据库迁移旧项目步骤，无重建现有主题步骤，无替换全部节点步骤。

## 执行交接

当前仅完成计划，以上任务均未执行。采用内联执行时调用 `executing-plans` 并依次完成；采用子代理驱动时调用 `subagent-driven-development`，明确各任务文件所有权，并在任务间审查。任何方式均先完成任务 1–5 的数据与作用域闭环，再推进交互和删除阶段。
