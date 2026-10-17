# 立意题材创意工作区实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 将已批准的 A 原型接入立意节点，形成完整创意编辑、候选阅读编辑、显式采用和规划流程。

**架构：** 从 ScriptSetter 提取受控的设定内容，保留旧弹窗入口；立意工作区独立组织阶段与方向详情。节点负责画布引用，状态协调层负责版本、草稿、异步请求和现有 API，后端契约不变。

**技术栈：** React 19、TypeScript、TanStack Query、现有画布 store、Base UI Dialog、Vitest、Testing Library、pnpm。

**依据：** `docs/superpowers/specs/2026-10-17-ideation-workspace-design.md` 已批准；独立 HTML 只作视觉参考，不导入模拟业务逻辑。

---

## 工作区和执行顺序

当前分支 `feat/canvas` 有大量其他任务的修改，IdeationNode 及其 API 也是未提交依赖。不能直接新建仅含 HEAD 的 worktree 后假定依赖齐全。本计划文档在现有工作区编写；执行前记录目标文件基线，检查正在工作的代理，采用明确文件所有权隔离。若使用专用 worktree，先将本任务所需的未提交依赖逐项复制并校验差异，不移动、重置或批量暂存共享目录。

任务 1 与任务 2 可独立开发；任务 3 依赖两者；任务 4 接线；任务 5 验证。每个实现提交只包含明确审阅过的文件，已有未提交文件须先区分基线与本次改动，禁止 `git add .`。

## 文件与职责

创建：
- `frontend/src/features/script-creation/script-settings-content.tsx`：受控题材、预设、故事输入、六项摘要与抽屉。
- `frontend/src/features/canvas/production/ideation/IdeationWorkspace.tsx`：两阶段布局、导航与底部操作。
- `frontend/src/features/canvas/production/ideation/IdeaDirections.tsx`：列表、只读详情、编辑、自写方向。
- `frontend/src/features/canvas/production/ideation/useIdeationWorkspace.ts`：数据加载、草稿与请求协调。
- `frontend/src/features/canvas/production/ideation/planningSettings.ts`：读取冻结简报设定。
- `frontend/src/features/canvas/production/ideation/ideation-workspace.css`：局部响应式样式。
- `frontend/src/__tests__/features/canvas/ideation-workspace.test.tsx`：主流程与失败回归。
- `frontend/src/__tests__/features/canvas/ideation-planning-settings.test.ts`：冻结版本回归。

修改：
- `frontend/src/features/script-creation/script-setter.tsx`：保留兼容弹窗包装。
- `frontend/src/features/script-creation/script-setter.css`：抽出可复用、无全局污染的内容布局。
- `frontend/src/features/canvas/nodes/IdeationNode.tsx`：挂载新工作区，保留节点摘要与连线。
- `frontend/src/features/canvas/production/ProductionNodeShell.tsx`：可选工作区布局与关闭守卫，其他调用保持默认行为。
- `frontend/src/features/canvas/domain/canvasNodes.ts`：仅在节点类型需要时声明立意草稿字段。
- `frontend/src/__tests__/features/creation-workspace/creative-settings.test.tsx`：兼容旧入口并覆盖抽屉取消。
- `frontend/src/__tests__/features/canvas/production-review-interactions.test.tsx`：更新旧交互断言，保留业务断言。

## 任务 1：提取受控设定内容

- [ ] 阅读 ScriptSetter、script-setter-data、settings、types 和 creative-settings 测试，保留预设撤销、中文输入法、自定义题材、结构归一化规则。
- [ ] 在 creative-settings 测试中增加：抽屉修改后取消不改变外层草稿；应用后改变；切换预设不覆盖故事想法。测试使用真实组件，通过 role/label 操作，不断言内部 state。
- [ ] 在 frontend 目录运行 `pnpm exec vitest run src/__tests__/features/creation-workspace/creative-settings.test.tsx`，确认新增失败来自缺失行为。
- [ ] 创建内容组件，采用以下公共边界；将现有渲染和选择逻辑移入，不复制 catalog 数据。抽屉打开复制当前值到临时状态，应用才调用 onChange，取消清空临时状态并恢复焦点。

```tsx
import type { ScriptSettings } from '@/features/script-creation/types';
export interface ScriptSettingsContentProps {
  value: ScriptSettings;
  onChange: (value: ScriptSettings) => void;
  disabled?: boolean;
}
// 内容组件不负责保存 API、外层 Dialog、生成按钮或关闭工作区。
```

- [ ] ScriptSetter 保持原 `initial/onSave/onClose` 签名，在内部管理草稿，渲染内容组件和原保存操作；验证旧脚本工作区无需修改调用方。
- [ ] 重新运行该测试，全部通过后审查限定文件差异，提交 `refactor: extract reusable script settings content`。

## 任务 2：冻结简报规划设置

- [ ] 创建测试，以版本 a 为 single/1、版本 b 为 series/24，采用记录指向 a；预期只读取 a，并验证缺失和无法解码版本会报错而不是默认到 b。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/ideation-planning-settings.test.ts`，确认模块不存在导致失败。
- [ ] 创建 planningSettings.ts，实现以下函数；不新增 API，因为现有 revisions 已返回版本 Markdown。

```ts
import { scriptCreationApi } from '@/features/script-creation/api';
import { decodeBriefSettings } from '@/features/script-creation/settings';

export async function readPlanningSettings(project: string, document: string, revision: string) {
  const revisions = await scriptCreationApi.revisions(project, document);
  const saved = revisions.find(item => item.id === revision);
  if (!saved) throw new Error('找不到已采用方向对应的简报版本，请刷新后重试');
  const settings = decodeBriefSettings(saved.markdown);
  if (!settings) throw new Error('已采用简报的创作设定无法读取，请检查该版本');
  return settings;
}
```

- [ ] 编写测试时 spy `scriptCreationApi.revisions`，实际返回对象依照 ScriptRevision 类型构造；断言上述函数返回 a 的 mode 和 episodeCount，异常用 rejects.toThrow 验证。
- [ ] 运行相同测试通过，提交 `fix: resolve planning settings from adopted brief revision`。

## 任务 3：工作区状态与候选详情

- [ ] 在 ideation-workspace 测试中使用 QueryClientProvider、mock API 和真实画布 store，沿用 production-review-interactions 的节点挂载方法。建立三个候选及一个采用记录的固定数据。
- [ ] 增加行为用例：选中 b 后 a 仍已采用且 adoptIdea 没被调用；编辑 b 默认不展示 textarea，点击编辑后显示四字段；取消恢复值；保存失败保留输入；编辑 a 后的新版本不得沿用旧采用标记。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/ideation-workspace.test.tsx` 确认失败。
- [ ] 创建协调 hook，将原 IdeationNode 查询与操作迁入。定义阶段 `'creative' | 'directions'`；选中候选使用 ID，采用状态只读服务端的 ID+revision；草稿使用候选 revision 键，创意草稿使用项目/节点隔离。

```ts
const selectedIsAdopted = Boolean(selected && run?.adopted &&
  selected.id === run.adopted.candidate_id &&
  selected.revision_id === run.adopted.revision_id);
// 选择仅改变 selectedId；不能写 run.adopted。
// 保存/采用/重做成功后刷新原项目、原工作流 query key。
```

- [ ] 保留 useProductionAction 的去重和失败重试 mutation 机制。保存简报必须返回保存结果，生成必须使用该结果的 current_revision_id，不等待 React query 重渲染取得版本。

```ts
// 单次 perform 回调内，先保存再生成；两步使用稳定但不同的 mutation。
const saved = await saveBrief(draft, `${mutation}:brief`);
const created = await creativeWorkflowApi.createIdeation(project, {
  brief_document_id: saved.id, brief_revision_id: saved.current_revision_id,
  generate: true, client_mutation_id: `${mutation}:generate`,
});
```

saveBrief 为 hook 内私有函数：存在简报时调用 save，使用原 base_revision_id 和 encodeBriefSettings 保留 Markdown；不存在时调用 create；成功更新原节点引用并返回 ScriptDocument；失败直接抛出，禁止继续生成。

- [ ] 创建 IdeaDirections：候选摘要按钮、四字段只读详情、编辑表单、保存/取消、单方向重做、采用和自写方向。编辑尚未保存时禁用执行按钮；切换候选、切换阶段或关闭工作区时给出保存/放弃/继续编辑选择。
- [ ] 加入 loading/error/retry 中文反馈。生成或重做运行时轮询包含 queued/pending/running；失败保留旧结果；单方向运行状态只禁用对应方向。
- [ ] 为异步更新捕获 project/node/workflow 身份；切换上下文后旧结果只能刷新原查询，不能写新节点。补充 deferred promise 测试，切换项目后 resolve，验证新节点数据不变。
- [ ] 增加保存失败不生成、快速双击仅一次请求、自写方向无需 AI、切换阶段保留草稿测试；运行本任务测试通过后提交 `feat: add ideation workspace state and candidate interactions`。

## 任务 4：节点接线与布局

- [ ] 创建 IdeationWorkspace，创意页嵌入内容组件，方向页挂载 IdeaDirections。首开进入创意页；已有候选可切换方向页；成功提交生成后进入方向页显示进度。
- [ ] ProductionNodeShell 增加可选 workspace 布局类和关闭请求守卫，默认调用保持原样。工作区使用 flex 列布局、min-height:0 的滚动内容、不可压缩底栏；其他节点不改变宽度、滚动和标题行为。
- [ ] IdeationNode 仅保留项目/节点适配、摘要和工作区挂载。移除嵌套 ScriptSetter 及旧三列 IdeaCard；保留原自写方向能力和现有节点复用/连线行为。
- [ ] 规划调用 readPlanningSettings(project, briefId, adopted.brief_revision_id)，将返回的 mode/episodeCount 与采用记录一起传给 plan。只在明确点击后调用，不在采用成功回调中调用。
- [ ] 添加端到端组件断言：采用成功只请求 adoptIdea；点击规划才请求 plan，且 payload 使用冻结版本及其设置；已有 E01 节点复用不新增重复节点。
- [ ] CSS 限定在工作区根类，正文约 14px、辅助至少 12px；1024px 下调整列宽，窄屏预设折叠、详情纵排；不引用原型的内联 important 覆盖或示例内容。
- [ ] 更新 production-review-interactions 中旧按钮和 textarea 定位，只改变交互步骤，不删除原版本或 API 断言。运行工作区与生产交互测试通过，提交 `feat: integrate ideation workspace into canvas node`。

## 任务 5：验证与交付

- [ ] 从 frontend 执行以下定向验证，要求新增用例和受影响旧用例通过；出现无关既有失败时单独记录具体文件，不掩盖新增回归。

```powershell
pnpm exec vitest run src/__tests__/features/creation-workspace/creative-settings.test.tsx src/__tests__/features/canvas/ideation-workspace.test.tsx src/__tests__/features/canvas/ideation-planning-settings.test.ts src/__tests__/features/canvas/production-review-interactions.test.tsx src/__tests__/features/canvas/creative-workflow-api.test.ts
pnpm exec tsc -b --pretty false
```

- [ ] 对本次文件运行 `git diff --check`，审查是否存在未处理 Promise、跨项目写入、版本混用、旧入口破坏和不必要 API 变化。
- [ ] 在真实前端页面使用 mock/固定测试数据，以 1280×800 和 1024×768 检查完整主流程、错误重试、抽屉取消与焦点返回、键盘操作及底部操作可见性。不得为验收触发付费模型调用。
- [ ] 保存创意页、设定抽屉、方向只读/编辑/采用截图到 `docs/design/ideation-workspace-2026-10-17/`；如浏览器策略阻止操作，报告未验收项目，不能以原型语法检查替代产品验收。
- [ ] 复核规格每项要求对应以上任务，记录执行命令与结果，执行代码审查并修复实质问题后交付。只提交任务文件，不包含其他线程修改。

## 自检记录

覆盖：完整创意首屏（1、4）、六项抽屉及旧入口（1）、候选阅读编辑与自写（3）、保存生成顺序与异步隔离（3）、显式采用与冻结规划（2、4）、响应式/焦点/错误反馈（3、4、5）。无后端字段扩张，无真实生成验收。计划阶段尚未修改产品代码或执行产品测试。
