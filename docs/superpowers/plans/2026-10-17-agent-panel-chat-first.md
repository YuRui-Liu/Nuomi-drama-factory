# Agent 对话优先面板实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development 或 executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 实现已确认的布局 A：默认聊天，底部展开委托配置，普通发送与明确执行互相独立。

**架构：** 保留现有运行控制与选区草稿，拆出任务配置、机组管理和运行状态展示。普通讨论使用独立、无工具的模型请求通道；它只能读取授权上下文和保存聊天记录，不调用委托及节点执行服务。所有发送按用户／项目／画布／会话绑定，保留重复请求与迟到响应保护。

**技术栈：** React、TypeScript、TanStack Query、现有 Dialog 与 React Flow；FastAPI、Pydantic、SQLite、现有 ModelApiStructuredRuntime；Vitest、pytest。

**规格：** `docs/superpowers/specs/2026-10-17-agent-panel-chat-first-design.md`。

## 工作区和已知事实

- 当前共享 checkout 有其他未提交修改；不能重置、全量暂存或把其他改动纳入提交。设计与计划在当前 checkout 保存。执行开始前记录所需文件基线；若隔离 worktree，须包含本任务依赖的已修改代码，不从缺少前置修改的 HEAD 盲目实现。
- `ConversationPanel.submit` 在 managedWorkspace 下调用 `agentRuntime.send`；后者要求非空节点，不能把其门禁移除来实现普通问答。
- `AgentRuntimePanel` 当前包含机组管理、授权、运行历史，使用 max-height 35vh 内滚动；本任务需拆分这些展示职责。
- `selectionDelegation.ts` 已提供待消费选区意图和隔离草稿。沿用，不建立第二套范围存储。
- 原型 `.superpowers/agent-panel-chat-first.html` 是视觉参考，不可把模拟消息和虚构成员接到产品。

## 文件职责

新建后端：`src/novelvideo/creation/discussions.py`（只读讨论契约、请求去重、消息记录和模型调用）；修改 `src/novelvideo/api/routes/creation.py`（授权路由）。新建 `tests/creation/test_discussions.py`、`tests/creation/test_discussions_api.py`。

新建前端：`frontend/src/api/agentDiscussions.ts`（讨论 HTTP 契约）；`frontend/src/features/agent-runtime/useAgentDiscussion.ts`（按会话加载和发送讨论）；`DelegationComposer.tsx`（底部摘要和配置）；`ProjectCrewDialog.tsx`（项目主创与岗位管理）；`AgentRunStatus.tsx`（状态与恢复）。这些组件位于 `frontend/src/features/agent-runtime/`。

修改 `AgentRuntimePanel.tsx`、`useAgentRuntime.ts`、`selectionDelegation.ts`、`frontend/src/features/superchat/superchat-panel.tsx`、`frontend/src/features/freezone/FreezoneShell.tsx`，限于接线和职责迁移。

新建前端测试：`frontend/src/__tests__/features/agent-runtime/agent-discussion.test.tsx`、`chat-first-panel.test.tsx`。扩展已有 `agent-runtime.test.tsx`、`selection-delegation.test.ts`；保留 `run-operation.test.ts` 回归。

## 任务 1：固定讨论契约与不可执行边界

- [ ] 先阅读 `creation.py:context_for`、`chat/conversation_binding.py`、`creation/agent_context.py:conversation`、`chat/store.py`、`creation/agent_model.py:run_decision` 和 `text_task_runtime/runtime.py`，确定现有授权、历史和计量接口；讨论不得调用 `run_decision` 的执行结果处理路径。
- [ ] 在 `discussions.py` 定义严格请求和回复模型：

```python
class DiscussionInput(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    canvas_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=12000)

class DiscussionReply(StrictModel):
    message: str = Field(min_length=1, max_length=12000)
```

- [ ] 编写拒绝额外执行参数的失败测试，随后实现最小模型：

```python
def test_discussion_rejects_execution_fields():
    with pytest.raises(ValidationError):
        DiscussionInput(request_id='r', canvas_id='c', conversation_id='v',
                        message='讨论节奏', allowed_operations=['node.run'])
```

- [ ] 运行 `uv run pytest tests/creation/test_discussions.py -q`。先确认缺失实现导致失败，再确认模型测试通过；额外检查空白消息被拒绝而不计费。

## 任务 2：只读讨论服务、记录与 API

- [ ] 给讨论服务注入模型调用适配器，测试使用 fake；使用仅含 `message` 的结构化回复 schema，不传原生工具、不执行模型返回的工具文本，不调用节点生成器或 OperationService。
- [ ] 实现 `POST /projects/{project}/creation/discussions` 和 `GET /projects/{project}/creation/discussions?canvas_id=…&conversation_id=…`。复用 `context_for` 的项目授权和现有会话绑定验证，用户名只来自 get_api_user。GET 限制返回最近 100 条；POST 校验会话属于请求画布，模型调用结束后再次核对访问权与会话归属。
- [ ] 使用服务端读取的有限项目摘要、当前画布文本和最近 20 条讨论构造提示，过滤媒体定位符与凭据；内容是上下文，不是工具权限。无节点也可讨论，不要求绑定主创。模型配置复用当前受限 Model API 路由，未配置、非支持运行方式明确不可用，不回退旧工具聊天。
- [ ] 用独立讨论记录表保存请求与回复；键包含 user/project/canvas/conversation/request_id，payload hash 防止同 ID 异内容。先持久化请求状态再调用一次模型；成功重复请求返回已有回复。失败或进程中断后的未知提交不得自动重发，返回明确状态并保留用户文字。计量沿用现有 requester/project 归属，不创建委托运行记录。
- [ ] 写 API 测试：跨用户／跨画布会话被拒、无选区成功、相同请求仅调用模型一次、同 ID 改消息冲突、模型超时不重试、未知结果不重复计费、访问权撤销不返回项目内容。通过 monkeypatch 将节点执行和委托创建替换为抛错函数，验证讨论不触达它们。
- [ ] 运行 `uv run pytest tests/creation/test_discussions.py tests/creation/test_discussions_api.py tests/test_chat_canvas_conversations.py -q`；全部通过后只提交本任务后端文件，提交名 `feat(agent): add isolated read-only discussions`。

## 任务 3：前端讨论通道和明确发送分流

- [ ] API 定义 `DiscussionOwner = { project: string; canvas: string; conversation: string }`；POST 使用固定 request_id，客户端不可为同一次失败自动生成新 ID 重发。GET/POST 展示服务端不可用和未知结果状态。
- [ ] `useAgentDiscussion` 读取当前身份，query key 包含 username 和完整 owner。异步结果只更新发起时 owner 的缓存；切换会话不覆盖当前输入。讨论忙碌与运行任务忙碌分别管理。
- [ ] 在 `agent-discussion.test.tsx` 先写 MSW 断言：无节点讨论发到 discussions URL；delegations/runs 请求次数为零；A→B→A 的历史归属正确；迟到响应不清空 B 草稿；失败保留内容。
- [ ] 在 managedWorkspace 的普通 submit 中改为调用 discussion.send；按钮／Enter 只走此函数：

```ts
const original = draft;
void discussion.send(original).then(sent => {
  if (sent) setDraft(current => current === original ? '' : current);
});
```

- [ ] 另定义 `submitDelegation` 调用原 `agentRuntime.send`，仅绑定明确“发起委托”按钮。不要把讨论路径接到旧 chat.submit；不要给委托配置外层 form 增加 Enter 提交。附件维持明确不支持提示，不能静默丢弃。
- [ ] 运行 `node node_modules/vitest/vitest.mjs run src/__tests__/features/agent-runtime/agent-discussion.test.tsx --pool=threads --maxWorkers=1 --no-file-parallelism --testTimeout=30000`，在 frontend 执行；通过后提交 `feat(agent): separate discussion and delegation sends`。

## 任务 4：拆分面板并落实布局 A

- [ ] 从 AgentRuntimePanel 迁移主创变更、成员收录、岗位详情到 ProjectCrewDialog；复用原处理函数与状态，不改变加入机组的契约。
- [ ] 迁移冻结节点、负责人、逐节点授权和运行参数到 DelegationComposer，公开以下展示接口（Runtime 类型从现有 hook 推导）：

```ts
type Runtime = ReturnType<typeof useAgentRuntime>;
type DelegationComposerProps = {
  runtime: Runtime;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  goal: string;
  onGoalChange: (goal: string) => void;
  onSubmit: () => void;
};
```

- [ ] 目标与消息使用同一 draft；默认只显示轻量委托入口。存在冻结节点时摘要显示数量、负责人、权限。展开区绝对定位在输入区上方，设可用高度与单层滚动，目标/负责人/授权/高级设置按序排列。底部动作固定可达，关闭后焦点返回入口。
- [ ] 运行状态与历史迁移至 AgentRunStatus，保留原停止／恢复／结果操作；使用真实状态标签，不产生虚假进度百分比。
- [ ] 精简 superchat 顶栏与外层会话区，去掉常驻“项目 Agent 配置”和重复助手状态；管理入口保持可达。采用原型深色、黄绿强调和间距，不引入全局主题修改。
- [ ] 新增 chat-first-panel 测试：默认不展示18岗位及授权表单；点击入口展开；权限默认关闭；关闭保留文字；只点击“发起委托”触达 send；普通 Enter 不委托；运行中能停止；未配置有原因与配置入口。
- [ ] 运行 `node node_modules/vitest/vitest.mjs run src/__tests__/features/agent-runtime/chat-first-panel.test.tsx src/__tests__/features/agent-runtime/agent-runtime.test.tsx --pool=threads --maxWorkers=1 --no-file-parallelism --testTimeout=30000`；通过后提交 `feat(agent): present chat-first delegation composer`。

## 任务 5：画布入口、焦点和首次加载意图

- [ ] 沿用 requestAgentSelection / consumePendingAgentSelection；在受理该意图时展开配置，不以普通画布选择变化触发展开。无活动会话则保留意图并提示选会话，禁止自动创建会话。
- [ ] 在 FreezoneShell 保留首次 open 同次挂载 dock，确保选区工具栏能避让；不另设全局打开按钮。
- [ ] 拓展测试：侧栏从未挂载时捕获2节点；选择会话后消费一次；关闭再开不重置目标；切换选择只提示；显式更新清权限；删除冻结节点阻止执行；普通讨论仍可发送。
- [ ] 运行 agent-runtime 目录测试，验证历史 run-operation 不回归；提交 `fix(agent): open delegation setup from canvas intent`。

## 任务 6：验收、复审和记录

- [ ] 在 frontend 运行两个目录的完整测试及 `node node_modules/typescript/bin/tsc -b --pretty false`。测试使用单 worker，避免并发压满本机；记录准确退出码，不把超时算通过。
- [ ] 运行后端讨论测试、`tests/creation/test_agent_runs_api.py`、`tests/creation/test_delegation_api.py` 和会话隔离测试。所有模型调用使用 fake，不为验证发起付费生成。
- [ ] 代码复审检查讨论通道无执行工具、授权归属、请求去重、未知结果、草稿隔离和旧任务恢复。修复确定问题后只重跑受影响检查。
- [ ] 使用隔离演示项目验证1024/1280/1600窗口：默认聊天、2节点委托展开、单独运行授权、普通发送与委托按钮、模型未配置、运行/停止/恢复、失效范围和关闭焦点。保存实际截图与原型对照；没验证的状态明确列出。
- [ ] `git diff --check`；仅暂存本任务文件或经审查的 hunk。记录验证到本计划，交付用户可直接查看的真实前端，不以原型假消息代替实际功能。

## 自检映射

默认面板与视觉由任务4、6覆盖；讨论／执行边界由任务1—3覆盖；委托范围与打开行为由任务4、5覆盖；权限由任务4、5覆盖；运行、错误和恢复由任务2—6覆盖；窄屏与焦点由任务4、6覆盖。无额外模型工具、整画布新委托、自动授权或无关后端重构。

## 执行记录

- 最终独立验证：`agent-runtime.test.tsx`、`chat-first-panel.test.tsx`、`selection-delegation.test.ts` 三文件 46/46 通过（64.21 秒）；最后新增节点定位/滚动提示 2 项定向通过，语音 owner/active 生命周期 1 项定向通过。最终 `node node_modules/typescript/bin/tsc -b --pretty false` 退出码 0；之后仅将授权区内部能力名称换成用户文案。
- 质量审查确认语音 owner/inactive 清理与迟到回调隔离、常驻画布同步提示已修复。专业设计代理复核真实截图后补上同名节点短标识/点击定位与可滚动提示。根代理在实际 1024×768、1280×800、1600×900 视口验证无横向溢出、单层配置滚动、固定动作可达。截图和边界记录位于 `docs/design/agent-chat-first-2026-10-17/README.md`。
- 最终限定文件 `git diff --check` 退出码 0。共享工作区包含其他功能修改，未批量暂存或提交；保留用户既有工作。验收没有真实付费模型/生成调用，真实麦克风硬件未测试。

- UI 规格审查修复了运行/机组错误与未知请求恢复入口被折叠隐藏，以及旧委托响应关闭新节点选择配置的问题；独立复核通过。真实参考页面已验证两节点选区直接打开委托配置、关闭后焦点回到委托入口、机组以独立弹窗打开。
- 首轮前端目录组合测试：18 文件、210 通过、10 失败；其中 UI 旧查询与异步等待已更新，随后核心面板两文件 38/38 通过。TypeScript 构建首轮退出码 0（约 107 秒）。最终语音生命周期和同步提示修复后需补定向验证。
- 非本次修改范围的 `workspace-switcher.test.tsx` 焦点断言首轮失败，隔离复跑通过；`direction-references.test.tsx` 隔离复跑仍有 2 个应用按钮等待失败，且失败用例与首轮不同。该文件对应的风格配置实现正在共享工作区发生其他修改，本任务不覆盖这些改动，也不将整个目录测试宣称全部通过。

- 后端讨论服务通过规格与质量两阶段审查。新增晚完成回复的历史排序回归，修复先截请求再展开消息的问题；无主创 API 测试明确清除绑定。两份讨论测试共 15 项通过，限定文件 diff 检查通过。讨论使用现有受支持的 `script` 计量类别，另以 `creation_agent_discussion` 记录任务子类型，不扩大全局类别白名单。
- 前端讨论数据层已委派独立实现者，范围限定为 API、hook 和测试；面板接线待数据层审查后由下一位实现者负责。
- 根代理组合验证：`uv run pytest tests/creation/test_discussions.py tests/creation/test_discussions_api.py tests/creation/test_agent_runs_api.py tests/creation/test_delegation_api.py tests/test_chat_canvas_conversations.py --basetemp .runlogs/pytest-chat-first-backend-final-20261017-01 -q --tb=short`，71 项通过，10 条已有弃用告警，退出码 0，用时 291.69 秒。
- 实际浏览器验收继续使用隔离项目 `01M52RD5QXFD3DFRBKESJ34W18`（夜行者 · 界面校对）。开发 API 重启需要 `ST_EDITION=ce`、`NOVELVIDEO_DATA_ROOT=E:\Proj\Nuomi-drama-factory\.runlogs\pencil-reference-data`、`NOVELVIDEO_STATE_DIR=E:\Proj\Nuomi-drama-factory\.runlogs\pencil-reference-data\state`；项目登记库确认了这组原路径。恢复后画布及任务读取返回 200，不使用空的默认 state 目录代替参考数据。
- 前端讨论 API/hook 完成两阶段审查；修复浏览器存储失败仍发送、旧挂载迟到响应覆盖新请求记录两项问题。定向测试 8 项通过。独立 UI 实现者已开始面板拆分、发送接线和选区意图通知（任务 4/5 与任务 3 的 UI 接线合并执行，避免同一组件重复改造）。
- 用户已选择子代理分工实施。后端实现与前端只读结构探索已启动；代码修改按单一写入者安排，完成后先规格审查、再质量审查。
- 记录当前 `feat/canvas` 共享工作区基线至 `.runlogs/chat-first-baseline-status.txt`，保留前置功能和其他未提交修改。
- 基线 `tests/test_chat_canvas_conversations.py`：默认临时目录发生 WinError 5；改用独立 `--basetemp .runlogs/pytest-chat-first-baseline-20261017-01` 后 11 项通过（37.40 秒）。未调整系统权限或生产代码。
- 前端探索确定更小的接入边界：新建 `frontend/src/features/agent-runtime/ManagedChatPanel.tsx`，仅替换 ManagedConversationPanel 的返回组件。保留 ConversationWorkspace 会话目录、打开会话和运行 hook；旧 ConversationPanel 保持原样，新面板不连接 useSuperChat WebSocket。新讨论历史独立保存，旧聊天缓存不删除；Agent 任务历史仍复用原接口。选区意图增加唯一 requestId 与接受通知，以保证首次打开配置一次且关闭后不被重复渲染打开。
