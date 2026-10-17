# 创作节点工作区 A 实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 或 executing-plans 逐任务实施，逐项记录结果。

**目标：** 将已确认的工作区原型接入真实业务，媒体生成只从对应画布节点显式发起。

**架构：** 四个独立交付分项，先建立共享工作区壳，再并行处理设置、声音和导演链路。复用现有 API、画布 store 与运行时校验，新增内容保持小组件和纯函数边界。

**技术栈：** React、TypeScript、TanStack Query、Base UI、React Flow、Vitest；必要后端契约检查使用 pytest。

**设计依据：** `docs/superpowers/specs/2026-10-17-production-workspaces-a-design.md`，已获用户批准，原型 `E:/Proj/nuomi.pen`。

## 执行环境与边界

当前 `feat/canvas` 为多线程共享脏工作区，含大量已暂存和未跟踪依赖。先记录目标文件基线与代理占用，按文件所有权执行；不要用仅含 HEAD 的新 worktree 丢失这些依赖。若需要隔离，逐项复制并校验所需依赖后再运行。禁止 reset、批量暂存或提交其他线程文件。

本轮只做实现计划。各任务的 commit 步骤在独立文件验证后执行，仅提交明确清单；若共享文件仍由其他任务修改，保留工作树结果并在报告说明，不强行提交混合差异。

原型有三项必须保持的边界：导演工作区不直接生成；出图宫格不是多结果画册；声音工作区可生成试听，正式配音在独立节点。

## 分项与依赖

1. 共享壳与专业剧本：本文件任务 1–2。
2. 导演、宫格与拆帧：`2026-10-17-production-grid-extraction.md`，依赖任务 1。
3. 角色声音与配音：`2026-10-17-production-voice-workspace.md`，依赖任务 1。
4. 项目设置：`2026-10-17-project-settings-workspace.md`，可独立进行。

共享 `canvasNodes.ts`、`ProductionNodeShell.tsx` 由集成者单独负责，其他实现者通过明确接口提出字段补丁，不同时编辑。先定绑定类型，再开始依赖其结构的任务。

## 任务 1：共享工作区与节点摘要

文件：修改 `frontend/src/features/canvas/production/ProductionNodeShell.tsx`；创建 `frontend/src/features/canvas/production/workspace/ProductionWorkspaceLayout.tsx`、`production-workspace.css`；测试 `frontend/src/__tests__/features/canvas/production-workspace-shell.test.tsx`。

- [ ] 写失败用例：节点状态与主入口可访问；普通节点仍用旧壳；工作区返回触发关闭守卫；拒绝关闭后内容保留。
- [ ] 在 frontend 运行 `pnpm exec vitest run src/__tests__/features/canvas/production-workspace-shell.test.tsx`，确认预期失败。
- [ ] 新布局只负责导航、正文、按需详情和固定操作栏。保持现有 `workspace`、`beforeClose` 与 `ProductionNodeShellControls`，不把业务请求放入壳。

```ts
export interface ProductionWorkspaceLayoutProps {
  title: string;
  onBack: () => void;
  navigation: React.ReactNode;
  children: React.ReactNode;
  inspector?: React.ReactNode;
  actions: React.ReactNode;
}
```

- [ ] 增加可选状态与操作标签 props，默认值保持现有节点调用兼容；使用真实状态，禁止原型示例进入产品。
- [ ] 1024 宽下详情收拢，正文最小宽度不被挤压；键盘入口、返回焦点与固定底栏可见。
- [ ] 同命令通过后检查差异，提交 `feat: add production workspace shell and node summaries`。

## 任务 2：专业剧本阅读优先

文件：修改 `frontend/src/features/canvas/nodes/ScreenplayNode.tsx`；创建 `frontend/src/features/canvas/production/workspace/ScreenplayWorkspace.tsx`；复用 `RetainedScreenplayDraft.tsx`、`ScreenplayHandoffPreview.tsx`、`ScreenplaySceneStructure.tsx`；创建测试 `frontend/src/__tests__/features/canvas/screenplay-workspace.test.tsx`。

- [ ] 使用真实 QueryClient 和 mock API 构造已采用 v1、当前草稿 v2；测试默认阅读不出现编辑表单，显式编辑后显示；取消还原，保存失败保留文字。
- [ ] 断言仅查看或保存不调用采用/交接；采用后还需点击交接预览并确认。未保存编辑时切文档和返回都可取消。

```ts
expect(adoptSpy).not.toHaveBeenCalled();
expect(handoffSpy).not.toHaveBeenCalled();
// adoptSpy 与 handoffSpy 分别 spy 现有节点所调用的采用与交接方法。
```

- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/screenplay-workspace.test.tsx` 确认失败。
- [ ] 将现有正文与导航迁入工作区，规划确认、停止、恢复仅在相应阶段出现；选段委托按选择展开。保留原请求去重、版本冲突和上游修订影响逻辑。
- [ ] 明确状态 `reading | editing`，已采用标记只匹配 document ID + revision，不因选中文档或保存自动迁移。
- [ ] 运行新增测试及 `screenplay-review.test.tsx`、`retained-screenplay-draft.test.tsx`、`screenplay-scene-structure-revision.test.tsx`；通过后提交 `feat: make screenplay workspace reading first`。

## 最终集成验收

- [ ] 分项分别完成规格审查与代码审查，修复实质问题后才集成；不删旧入口契约测试来适配新行为。
- [ ] 运行各分项列明的定向测试，再从 frontend 运行一次 `pnpm exec tsc -b --pretty false`，记录完整退出码。只有出现新错误或后续变更才重跑相关检查。
- [ ] 对本轮文件执行 `git diff --check`。接口、配置或网关发生修改时执行仓库要求的 gitleaks 检查。
- [ ] 浏览器用固定数据验证 1280×800、1024×768：各工作区返回、滚动、焦点、草稿保护；导演到出图节点接线；直接裁切；高清模式模型缺失/失败/重试；角色绑定后配音节点引用；四类设置切换与保存范围。
- [ ] 禁止为验收触发付费模型。媒体生成用 mock 任务与已存在固定图片，不用直连 provider 脚本绕过产品入口。
- [ ] 保存实际页面截图与命令结果至 `docs/design/production-workspaces-a-2026-10-17/` 和本轮报告。静态原型不能替代真实产品验收。

覆盖自检：节点摘要（1）、剧本（2）、导演/宫格/高清（分项 2）、声音（分项 3）、设置（分项 4）、草稿/版本/失败/响应式（各分项与最终验收）。
