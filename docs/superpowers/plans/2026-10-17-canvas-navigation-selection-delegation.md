# 按需画布导航与选择委托实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 在当前会话逐任务执行此计划；需要并行实施时可选择 superpowers:subagent-driven-development。步骤使用复选框跟踪进度。

**目标：** 实现已确认的 A 方案，让画布导航和创建按需出现，配置统一收纳，新委托严格绑定用户选择。

**架构：** 复用画布注册表、路由切换、React Flow 选择状态和现有 Agent 侧栏。新增小型入口组件与范围校验模块，避免继续向 FreezoneShell 堆积交互逻辑。历史任务范围与恢复保持原契约。

**技术栈：** React、TypeScript、TanStack Router、Zustand、React Flow、Vitest、Testing Library、Pencil MCP。

**依据：** `docs/superpowers/specs/2026-10-17-canvas-navigation-selection-delegation-design.md`，用户已完成书面确认。

---

## 执行约束与文件归属

当前工作区存在大量未提交的 Agent 与节点执行改动，不能提交或覆盖他人修改。计划阶段只新增本文件；代码执行前比较现有差异，使用可包含所需基线的隔离工作区，或明确保留当前已修改实现并逐块合入。不能从旧 HEAD 创建工作区后假定这些运行能力已存在。

| 文件 | 职责与操作 |
| --- | --- |
| `frontend/src/features/creation-workspace/CanvasWorkspaceSwitcher.tsx` | 改为紧凑按钮、切换浮层，创建表单按需显示 |
| `frontend/src/features/creation-workspace/CreateCanvasDialog.tsx` | 新增独立创建表单，复用上层创建回调 |
| `frontend/src/features/creation-workspace/CreationWorkspace.tsx` | 保留刷新、请求幂等、切换前保存；向入口传递状态 |
| `frontend/src/features/creation-workspace/ProjectSettingsEntry.tsx` | 新增配置聚合入口，不复制配置编辑器 |
| `frontend/src/components/layout/project-header-navigation.tsx` | 在项目导航中挂载设置入口 |
| `frontend/src/features/freezone/FreezoneShell.tsx` | 移除常驻配置按钮与空选区委托按钮；连接已有侧栏 |
| `frontend/src/features/canvas/ui/SelectionDelegationToolbar.tsx` | 新增选区就近委托操作，支持单选与多选 |
| `frontend/src/features/agent-runtime/selectionDelegation.ts` | 新增纯函数：范围差异、缺失节点检测 |
| `frontend/src/features/agent-runtime/useAgentRuntime.ts` | 冻结输入、显式更新选区、发送校验与原有任务恢复 |
| `frontend/src/features/agent-runtime/AgentRuntimePanel.tsx` | 移除新委托的整画布范围选项；展示选区变化和明确范围 |
| `frontend/src/__tests__/features/creation-workspace/workspace-switcher.test.tsx` | 新增导航、创建、取消及失败重试测试 |
| `frontend/src/__tests__/features/creation-workspace/project-settings.test.tsx` | 新增入口聚合及配置作用域测试 |
| `frontend/src/__tests__/features/agent-runtime/selection-delegation.test.ts` | 新增纯函数边界测试 |
| `frontend/src/__tests__/features/agent-runtime/agent-runtime.test.tsx` | 扩展真实发送体、切换、失效输入与恢复测试 |
| `frontend/src/__tests__/features/creation-workspace/selection.test.tsx` | 保留现有跨画布选择及视角回归 |

## 任务 1：在 Pencil 中完成 A 方案状态图

- [ ] 通过 Pencil MCP 检查当前 `E:/Proj/nuomi.pen`，读取最新顶层画板，保留 P/R/U 与 A01—A06 历史稿。
- [ ] 新增独立的「按需导航与选择委托」画板组：默认态、切换列表、新建表单、单选委托、多选委托、范围变化／删除节点错误态。
- [ ] 默认态只保留当前画布按钮、项目设置入口与默认收起的助手。移除常驻创建表单及画布右上成排配置入口。
- [ ] 每张委托状态图明确显示所选节点、目标、只读关联参考与候选交付；未选中时没有委托入口。沿用实际节点外观，不用通用卡片代替节点编辑器。
- [ ] 检查实际截图中的字体、浮层层级、弹窗关闭按钮、文字裁切与画布占用，修复后导出 PNG 与 PDF 到 `docs/design/on-demand-canvas-2026-10-17/`。只有实际验证过的跳转才标注为可点击交互。

## 任务 2：紧凑切换器与创建表单

- [ ] 在 `workspace-switcher.test.tsx` 写失败测试：默认只能看到当前画布按钮，名称输入框不存在；展开后可搜索已有画布；选择触发正确 ID 并关闭；创建失败保留名称和类型；取消不触发创建或切换；空白名称禁用提交。

核心断言使用现有组件 props，避免耦合内部 CSS：

```tsx
const onCreate = vi.fn().mockResolvedValue(false);
render(<CanvasWorkspaceSwitcher canvases={[]} onSelect={vi.fn()}
  onCreate={onCreate} pending={false} />);
expect(screen.queryByRole('textbox', { name: '画布名称' })).not.toBeInTheDocument();
await userEvent.click(screen.getByRole('button', { name: '选择或新建画布' }));
await userEvent.click(screen.getByRole('button', { name: '新建画布' }));
await userEvent.type(screen.getByRole('textbox', { name: '画布名称' }), '雨夜');
await userEvent.click(screen.getByRole('button', { name: '创建并打开' }));
await waitFor(() => expect(onCreate).toHaveBeenCalledWith('concept', '雨夜'));
expect(screen.getByRole('textbox', { name: '画布名称' })).toHaveValue('雨夜');
```

- [ ] 在 `frontend` 下运行 `pnpm exec vitest run src/__tests__/features/creation-workspace/workspace-switcher.test.tsx`，确认因默认展开及缺少新交互而失败。
- [ ] 将 `open` 初始值设为 `false`，按钮文案使用 `canvases.find(item => item.canvas_id === selectedId)?.title ?? '选择或新建画布'`。列表过滤后只渲染有条目的分类；无搜索结果提供清空搜索及新建。
- [ ] 创建 `CreateCanvasDialog`，props 为 `open`、`initialKind`、`pending`、`error`、`onClose`、`onCreate: (kind: WorkspaceKind, title: string) => Promise<boolean>`；使用现有 Dialog 组件管理焦点、Escape 与外部点击。提交只在 `await onCreate(kind, title.trim())` 返回 true 时关闭，失败保持输入。不要修改 `CreationWorkspace` 的 request_id 复用机制。
- [ ] 成功路由切换后关闭列表；保存失败被路由阻止时保留当前画布和错误提示。创建取消返回触发按钮；提交中禁止重复动作。
- [ ] 运行上述测试及 `pnpm exec vitest run src/__tests__/features/creation-workspace/workspace.test.tsx src/__tests__/features/creation-workspace/selection.test.tsx`，预期全部通过。提交只包含本任务实际改动，提交名 `feat(canvas): make canvas navigation on demand`。

## 任务 3：统一项目设置

- [ ] 为 `ProjectSettingsEntry` 添加失败测试：未打开时四个配置入口不出现；打开后显示项目范围；点击对应入口可访问现有配置面板；关闭不清除画布选择。
- [ ] 创建聚合入口，复用 `AgentConfigurationEntry`、`CreativeSettingsEntry`、`TemplateLibraryEntry` 和风格配置入口，不复制配置状态或提交函数。检查 `DirectionReferenceEntry` 的 `selectedNodes` 语义：项目默认入口不可传入局部选区模式，局部设置留在节点操作附近。
- [ ] 在 `project-header-navigation.tsx` 中从当前项目画布路由解析有效 workspace；未找到画布时不伪造 canvasId，按各配置入口现有必需参数禁用并说明。FreezoneShell 仅移除常驻入口，保留现有编辑器与能力。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/creation-workspace/project-settings.test.tsx src/__tests__/features/creation-workspace/agent-configuration.test.tsx src/__tests__/features/creation-workspace/direction-references.test.tsx src/__tests__/features/creation-workspace/creative-settings.test.tsx src/__tests__/features/creation-workspace/template-library.test.tsx`，预期通过。
- [ ] 提交名 `feat(canvas): consolidate project settings entry`；不包含工作区中已有的 Agent 编辑器修改。

## 任务 4：选区范围与就近委托

- [ ] 新建范围函数及其失败测试，先执行测试确认模块缺失：

```ts
import { describe, expect, it } from 'vitest';
import { selectionChanged, missingSelection } from '@/features/agent-runtime/selectionDelegation';
describe('selection delegation', () => {
  it('ignores ordering but detects scope changes', () => {
    expect(selectionChanged(['a', 'b'], ['b', 'a'])).toBe(false);
    expect(selectionChanged(['a'], ['b'])).toBe(true);
    expect(selectionChanged(['a'], [])).toBe(true);
  });
  it('reports missing frozen inputs', () => {
    expect(missingSelection(['a', 'b'], ['b'])).toEqual(['a']);
  });
});
```

- [ ] 在 `selectionDelegation.ts` 实现：

```ts
export function selectionChanged(frozen: readonly string[], current: readonly string[]): boolean {
  const left = new Set(frozen);
  const right = new Set(current);
  return left.size !== right.size || [...left].some(id => !right.has(id));
}
export function missingSelection(frozen: readonly string[], available: readonly string[]): string[] {
  const existing = new Set(available);
  return [...new Set(frozen)].filter(id => !existing.has(id));
}
```

- [ ] 新增 `SelectionDelegationToolbar`，读取 store 的 `nodes.filter(node => node.selected)`，空数组直接返回 null；使用 React Flow `NodeToolbar` 的选区定位能力和当前边界策略。传入当前项目、画布及就绪状态，点击先打开现有助手，再发出已有 `nuomi:agent-selection` 事件；保留原 `sync.hydratedCanvasId === canvasId` 与 conflict/error 门禁。
- [ ] 单选与多选只出现一个委托入口，不重复覆盖现有编辑栏。检查 `MultiSelectionToolbar` 与 `useNodeToolbarPlacement` 的位置，必要时向既有栏添加动作槽位，禁止两个浮动条占同一坐标。
- [ ] 新委托发送校验改为 `if (!nodes.length) { setError('请先选择要委托的节点'); return false; }`；请求体始终使用 `scope: { kind: 'nodes', node_ids: selection.map(n => n.id) }`，`inputEvidence` 的新委托参数使用 false。移除 `AgentRuntimePanel` 的整画布准备范围复选框，不修改历史任务契约或恢复调用。
- [ ] 选择状态变化只触发提醒，不调用 `setNodes` 自动替换草稿。仅点击「更新为当前选择」时调用现有 `captureSelection`；清空旧操作授权，保留目标文字。发送前运行 `missingSelection`，并继续使用已有 `inputEvidence` 校验内容和分组归属。
- [ ] 在 `agent-runtime.test.tsx` 增加请求体断言：空选区不请求；单选／多选发送的 node_ids 精确匹配；改变选择不改旧草稿；显式更新范围清除旧授权但保留目标；删除冻结节点后阻止发送；恢复历史 canvas 范围任务仍使用原契约。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/agent-runtime/selection-delegation.test.ts src/__tests__/features/agent-runtime/agent-runtime.test.tsx src/__tests__/features/agent-runtime/run-operation.test.ts`，预期通过。提交名 `feat(agent): bind new delegations to explicit node selection`。

## 任务 5：草稿隔离、集成与交付

- [ ] 查明当前目标输入与草稿保存位置，沿用其按 user/project/canvas/conversation 隔离的存储。将所选 RuntimeNode 数组随目标保留，增加进入画布时的节点存在性检查；不把旧节点 ID 自动映射到同名新节点。
- [ ] 在现有 runtime 测试夹具中加入 A → B → A：A 的目标与冻结范围保留；B 不显示 A 的草稿；返回 A 恢复目标与范围；A 在 B 激活后返回的异步响应不能覆写 B 的状态。关闭侧栏不停止现有任务。
- [ ] 保留普通聊天入口，未选择时仍可讨论；执行新委托时复用任务 4 的门禁。删除讨论原型中的快捷自动选择演示行为，不将其移植到产品。
- [ ] 在 `frontend` 执行 `pnpm exec vitest run src/__tests__/features/creation-workspace src/__tests__/features/agent-runtime` 和 `pnpm exec tsc -b --pretty false`。对照执行前基线记录既有失败，不能把既有失败报为通过。
- [ ] 启动实际前端，使用隔离演示项目，走查 1600×1000、1280×800、1024×768 下的切换、创建取消与失败、单选、多选、选择变化、删除输入、设置关闭和画布切换。用浏览器截图核对 Pencil；不发起付费生成。
- [ ] 若浏览器工具仍不可用，明确记录未验证的视觉与交互检查，不用源码推断或 HTTP 200 代替。交付 Pencil 文件、预览图、精确测试结果和未完成项。
- [ ] 最终检查 `git diff --check`，逐文件确认仅包含本任务变更。提交名 `test(canvas): verify on-demand navigation and delegation scope`。

## 覆盖自检

规格第 3 节由任务 2 覆盖；第 4 节由任务 3 覆盖；第 5 节由任务 4、5 覆盖；第 6 节由任务 5 覆盖；第 7 节通过现有组件边界和请求归属校验实现；第 8 节由任务 1、2—5 的测试与真实页面验收覆盖。历史任务、模板应用、项目默认与局部设置均保留各自语义。任务中的新增类型引用现有 `WorkspaceKind` 和 `RuntimeNode`，不另建平行模型。
