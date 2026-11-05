# 专业 Agent 入口与立意节点实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务执行；也可在用户选择后使用 subagent-driven-development。使用复选框跟踪完成状态。

**目标：** 在画布添加专业 Agent，跑通立意设定、生成、比较、编辑、采纳和引用，交互与样式沿用 `fix/codex-identity-qc`。

**架构：** 新增 Agent 节点，直接复用 ScriptSetter 与 ScriptSettings。立意作为版本化内置角色，通过现有委托与运行服务生成提案；专用适配校验三个候选，持久化采纳引用，不另建模型调用通道。节点仅保存设定和对象引用。

**技术栈：** React、TypeScript、XYFlow、Zustand、Vitest、Python、FastAPI、SQLite、pytest。

**规格：** `docs/superpowers/specs/2026-10-16-ideation-agent-node-design.md`。

**工作目录：** `/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。本计划只新增文档；执行代码任务前确认工作区归属，不提交他人改动。不要切换至参考分支，使用 `git show` 读取参考。

---

## 文件与职责

以下路径均相对工作目录。

| 文件 | 操作与职责 |
| --- | --- |
| `src/novelvideo/creation/builtin_agents.py` | 新增立意角色，保留现有角色 ID 和版本 |
| `src/novelvideo/creation/ideation.py` | 新建：结构化候选校验、编辑版本和采纳事务 |
| `src/novelvideo/api/routes/creation.py` | 挂接立意查询、预览、编辑、采纳入口，保持薄路由 |
| `tests/creation/test_ideation.py` | 新建：候选、版本、幂等和作用域测试 |
| `tests/creation/test_builtin_agents.py` | 验证新角色与已有目录兼容 |
| `frontend/src/api/ideation.ts` | 新建：立意结果与采纳 API 类型和调用 |
| `frontend/src/features/canvas/domain/canvasNodes.ts` | 增加 Agent 节点类型与数据联合 |
| `frontend/src/features/canvas/domain/nodeRegistry.ts` | 注册节点默认数据与连接能力 |
| `frontend/src/features/canvas/domain/nodeDisplay.ts` | 注册显示名称 |
| `frontend/src/features/canvas/nodes/index.ts` | 注册节点渲染器 |
| `frontend/src/features/canvas/ui/CanvasAddNodePanel.tsx` | 专业 Agent 添加入口 |
| `frontend/src/features/canvas/NodeSelectionMenu.tsx` | 保持另一添加入口一致 |
| `frontend/src/features/canvas/nodes/AgentNode.tsx` | 新建：节点框架、角色与状态，委托专用内容组件 |
| `frontend/src/features/ideation/IdeationSettings.tsx` | 新建：节点常用输入和原 ScriptSetter 适配 |
| `frontend/src/features/ideation/IdeationCandidates.tsx` | 新建：候选比较、编辑和采纳 |
| `frontend/src/features/ideation/useIdeationRun.ts` | 新建：保存、运行、查询恢复与输入过期标记 |
| `frontend/src/features/ideation/ideation-node.css` | 新建：仅节点内容的紧凑样式 |
| `frontend/src/features/ideation/ideation-node.test.tsx` | 新建：添加、设置、取消、运行和采纳集成测试 |
| `frontend/src/features/agent-runtime/useAgentRuntime.ts` | 按需提取复用的委托／运行操作，不改变侧栏行为 |
| `frontend/src/features/agent-runtime/runOperation.ts` | 复用意图、回执与重复提交恢复机制 |
| `frontend/public/locales/zh/translation.json` | 添加 Agent 入口和状态中文文案 |
| `frontend/public/locales/en/translation.json` | 同步对应英文键 |

直接复用、不另写一套：`script-setter.tsx`、`script-setter-data.ts`、`script-setter.css`、`settings.ts` 与 `types.ts`。若节点注册文件后缀发生变化，以代码库中的既有注册文件为准，不建立平行注册表。

## 任务 1：锁定基线与运行适配边界

执行记录：后端基线 50 passed；ScriptSetter 基线 6 passed。节点类型需登记到前端水合白名单；后端画布保存接受通用 JSON。保存屏障为 `flushFreezoneCanvasRuntime(project, canvas)`，仅返回 true 表示成功。节点运行使用独立 adoption，不调用 main.select；会话解析需独立于侧栏。

- [ ] 运行基线并记录结果：

```bash
git status --short
git diff fix/codex-identity-qc HEAD -- frontend/src/features/script-creation/script-setter.tsx frontend/src/features/script-creation/script-setter-data.ts frontend/src/features/script-creation/script-setter.css
uv run pytest tests/creation/test_builtin_agents.py tests/creation/test_agent_runs.py tests/creation/test_agent_tools.py -q
pnpm --dir frontend test src/features/script-creation/script-setter.test.tsx
```

预期：参考组件无差异；测试通过。若基线失败，记录既存失败并排查与本改动的关系，不将其误报为本次回归。

- [ ] 阅读 `creation/delegations.py`、`creation/operations.py`、`creation/agent_runs.py`、`api/routes/creation.py`、`api/agentRuns.ts` 和 `useAgentRuntime.ts`。定位现有创建对象、版本更新、预览证明、回执及运行查询入口，复用其事务和身份校验。
- [ ] 明确立意调用采用已有 `proposal.create`：`text` 存 JSON 文档，`title` 为候选集标题。结构化校验在服务端完成；不得仅靠提示词或浏览器 JSON.parse 声称结果有效。

## 任务 2：内置立意角色与结构化结果

执行记录：已完成，提交 `dcbb6af`、`6f4e61d`；62 项相关测试通过，修订后的内置角色测试 9 项通过。规格与代码质量审查通过；合计长度的额外回归用例纳入任务 3。

- [ ] 在 `test_ideation.py` 添加纯函数测试，先运行确认导入或断言失败：

```python
import pytest
from novelvideo.creation.ideation import validate_candidates

def test_three_distinct_candidates():
    item = dict(title='雨夜来客', premise='失忆者追查自己的证词',
                protagonist_goal='找回记忆', conflict='证人隐瞒同一事实',
                hook='证词来自未来', expansion='逐层揭开证人关系')
    items = [dict(item, id=str(i)) for i in range(3)]
    assert len(validate_candidates({'schema_version': 1, 'candidates': items})) == 3
    with pytest.raises(ValueError):
        validate_candidates({'schema_version': 1, 'candidates': items[:2]})
    with pytest.raises(ValueError):
        validate_candidates({'schema_version': 1, 'candidates': [items[0]] * 3})
```

- [ ] 实现 `validate_candidates(payload)`：要求 schema_version 为 1、严格三项、ID 唯一、六个正文键为非空字符串；每项正文总长上限 30,000 字符。拒绝畸形 JSON、重复 ID、未知版本和缺失字段，返回规范化列表。保留原始提案供查看错误。
- [ ] 增加 `builtin:ideation` revision 1，权限仅 `context.read`、`proposal.create`。职责明确题材、用户设置、三个方向、JSON 键名及不自动采纳。使用现有内置目录签名、来源与版本机制，不更改旧条目。
- [ ] 增补目录测试：旧角色仍可解析，新角色版本可解析，能力集合不含媒体执行权限。
- [ ] 运行 `uv run pytest tests/creation/test_ideation.py tests/creation/test_builtin_agents.py -q`，通过后精确提交上述文件：`feat(agent): add structured ideation role`。

## 任务 3：候选查询、编辑与采纳

执行记录：已完成，提交 `0533377`、`189f0e3`；76 项相关测试通过，规格与代码质量审查通过。服务独立于校验器位于 `ideation_service.py`，路由沿用 `creation_library.py`。对外 origin 与 object_ref 统一为 `{object_id, revision}`，编辑正文放在 candidate 对象中。当前采纳可由显式新操作更新，旧回执固定原版本；proof 包含当前采纳和当前引用证据。读预览保留历史来源，所有写入口拒绝 agent_session。

- [ ] 在 `test_ideation.py` 增加集成用例：重复请求返回相同回执；同 ID 不同载荷冲突；跨画布提案拒绝；旧 expected_revision 拒绝；修改后采纳使用新版本；采纳一轮不改写其他轮次。
- [ ] 在 `ideation.py` 增加服务适配。候选集从现有提案对象版本读取；采纳记录包括候选集对象 ID／版本、候选 ID、来源 run ID、节点 ID、项目与画布、actor 和输入快照签名。编辑使用已有对象版本操作建立新版本，不原地覆盖正文。
- [ ] 采纳事务验证调用者权限、对象归属、节点仍存在、当前版本和预览证明；记录选中版本。请求幂等键走既有 operation receipt，不另建内存幂等字典。下游只读取已采纳版本；未采纳或过期输入返回明确提示。
- [ ] 在 creation 路由增加以下契约，响应对象引用沿用既有版本引用格式：

```text
GET  /projects/{project}/creation/ideation/{object_id}
POST /projects/{project}/creation/ideation/{object_id}/preview
POST /projects/{project}/creation/ideation/{object_id}/edit
POST /projects/{project}/creation/ideation/{object_id}/adopt

写请求：request_id, canvas_id, conversation_id, node_id,
        expected_revision, candidate_id, proof
edit 另带：candidate（六项正文）；adopt 不接受任意新正文。
preview 带相同业务字段及 action=edit|adopt，返回 proof 和变更摘要。
```

- [ ] `frontend/src/api/ideation.ts` 导出候选类型和查询／预览／编辑／采纳函数，使用现有 apiCall 和错误 envelope。候选字段与任务 2 完全一致，不在组件中拼 URL。
- [ ] 运行 `uv run pytest tests/creation/test_ideation.py tests/creation/test_agent_tools_api.py -q`，通过后提交：`feat(ideation): persist candidate edits and adoption`。

## 任务 4：Agent 节点与添加入口

执行记录：节点与原设定器适配已提交 `bcbcb9b`，保存失败同内容重试修复 `aa70b51`。真实画布验证了三角色入口、立意添加、悬疑推理与喜剧融合、故事种子保存及刷新保留。规格复审通过，质量审查正在修复异步保存乱序确认；16 项前端相关测试通过。API 客户端两处 searchParams 类型问题纳入任务 6。

- [ ] 在节点集成测试中验证：新增入口展示立意、通用主创、文本编剧；选择立意创建一枚节点，角色与设置刷新后保持；添加失败显示错误，不创建虚假可运行节点。
- [ ] 在节点数据联合加入 `agentNode`，数据包含 displayName、Agent 来源引用、settings（立意时）、运行引用和已采纳对象引用。settings 使用 `defaultSettings()` 创建独立实例，不共享可变默认对象；候选正文不存第二份。
- [ ] 注册节点渲染、默认名称、端口及菜单，复用现有 nodeFactory、画布保存与撤销。AgentNode 使用现有 NodeHeader、Handle、节点尺寸与选中样式。
- [ ] 添加入口从内置目录取得配置和 proof，复用 adopt 流程；立意自动使用其内置配置，已有角色使用原通用运行界面。创建节点不改变当前主 Agent 绑定。
- [ ] 运行 `pnpm --dir frontend test src/features/ideation/ideation-node.test.tsx`，通过后提交：`feat(canvas): expose professional agent nodes`。

## 任务 5：原设定器交互和样式复用

- [ ] 编写集成断言：节点题材修改同步到展开面板；修改面板后取消节点不变；保存后摘要同步；保存失败保留面板草稿；点击输入不触发节点拖拽。使用已有 ScriptSetter 测试覆盖其内部选项逻辑，不复制测试。
- [ ] `IdeationSettings` 接收 settings 与异步 onSave；展开时直接渲染原组件：

```tsx
<ScriptSetter
  initial={settings}
  onSave={async next => {
    await onSave(next);
    setOpen(false);
  }}
  onClose={() => setOpen(false)}
/>
```

- [ ] 常用输入复用 genres、ScriptSettings 和原字段名；外层使用 XYFlow 的 nodrag、nowheel 类。主题材和融合题材为单选；六项多选和自定义仍在原抽屉完成。摘要从已保存 settings 计算。
- [ ] `ideation-node.css` 仅定义节点内容布局，颜色使用原深色底、灰边、黄绿色按钮和选中态。完整弹窗继续加载原 CSS，不复制融合圆、抽屉、断点。按钮、焦点和保存状态沿用原组件。
- [ ] 运行 `pnpm --dir frontend test src/features/script-creation/script-setter.test.tsx src/features/ideation/ideation-node.test.tsx`，通过后提交：`feat(ideation): reuse original creative settings interaction`。

## 任务 6：运行、恢复与结果承接

集成补充：既有 object 输入仅解析 Ontology 对象，不能直接引用 story_fragment。增加 `ideation_selection` 输入引用，字段为 request_id、canvas_id、conversation_id、node_id、object_id、revision、candidate_id，固定到一次已应用采纳回执。底层只读 resolver 在调用方 SQLite 连接内校验 actor/project/domain/action 和固定对象版本，仅返回选定候选；由 agent_context 使用，避免导入 IdeationService 形成循环。新节点运行状态字段须登记至 orchestration.models.RESULT_FIELDS，防止写入 runRefs 等状态使自身委托的输入指纹失效；用户设定仍作为输入证据。下游只在显式引用时解析已采纳版本，不自动执行。

- [ ] 添加集成测试：保存未完成不启动、重复点击只提交一次、运行时修改设置不被旧结果覆盖、切换画布后迟到结果不落到新画布、刷新查询已有运行、提交状态不明先查回执。
- [ ] `useIdeationRun` 调用现有运行操作。先等待画布保存成功，再捕获 settings 与显式引用；delegation 的目标限定当前节点，goal 包含设定 JSON 和候选输出契约。不要复用会修改主 Agent 绑定的 UI 初始化步骤。
- [ ] 运行前持久化意图；沿用 requestStep、运行查询和回执恢复。停止使用现有 stop 操作。状态 UI 区分运行中、停止请求、失败、结果不完整、完成；失败重试建立新运行，原结果保留。
- [ ] 查询 proposal.create 产物后经任务 3 服务校验，再展示三个卡片。输入签名只基于内容和引用版本，位置／尺寸变化不标记结果过期。设置修改后显示旧输入提示，生成新轮次不清空旧轮次。
- [ ] 候选编辑先保存新对象版本，再明确采纳；调用 preview 后 adopt 并更新节点对象引用。版本冲突提示刷新、保留本地编辑草稿。下游委托输入绑定已采纳版本；连线本身不自动运行。
- [ ] 运行前端节点集成测试和后端 `test_ideation.py`、`test_agent_runs.py`、`test_agent_tools.py`，通过后提交：`feat(ideation): connect runs and versioned candidate selection`。

## 任务 7：视觉与真实闭环验收

阶段验证：2026-10-16 后端候选校验、service、API、Agent runs 与 tools 合计 111 passed（10 条既有弃用警告）。验收项目 `ideation_agent_qa_20261016`、画布 `788c5de6b71340b7a9cd005d1094704f`；当前仅完成设置交互实测，尚未将任何模拟结果标为真实生成。

- [ ] 运行 `pnpm --dir frontend build`。若出现无关基线错误，记录具体文件及错误，不静默跳过本次文件的类型检查。
- [ ] 在实际画布验证：添加立意 → 主题材与融合题材 → 展开原设定器 → 预设／自定义／六项设置 → 保存 → 生成三候选 → 修改一项 → 采纳 → 文本编剧引用该版本。
- [ ] 在桌面和窄屏检查并保存截图：深色背景、黄绿色主按钮、融合圆、六项卡片、抽屉、选中和键盘焦点；节点内表单操作不拖动画布。原型浅色／紫色不得成为验收基准。
- [ ] 使用当前项目已配置且获准的模型完成一次真实运行。模型不可用时明确记录阻塞，保留通过的模拟测试证据，不把模拟结果标为真实生成。
- [ ] 检查失败重试与刷新恢复不产生重复采纳；更改设定后旧版本仍可查看，下游引用不被静默替换。
- [ ] 更新规格状态和验证记录，精确提交本任务文档。交付时说明完成范围、验证结果、真实运行证据和仍存在的限制。

## 自检映射

规格入口／角色由任务 2、4 覆盖；交互与样式由任务 5、7 覆盖；结构化候选与采纳由任务 2、3、6 覆盖；运行、恢复与冲突由任务 3、6、7 覆盖。候选键名统一为 title、premise、protagonist_goal、conflict、hook、expansion；settings 沿用 ScriptSettings。所有写路径使用既有作用域和版本控制，未安排全局主题重做、直接模型调用或强制流水线。
