# 前端界面切换性能优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变业务行为的前提下，从工作台主加载链移除登录动画、Piko 游戏和 3D Director，并降低素材列表首次挂载的媒体请求压力。

**架构：** 保留认证和路由守卫的同步边界，将纯视觉或按需功能放入 TanStack Router lazy route、React.lazy 与 Suspense 边界。素材列表只调整加载策略，不改变资产数据、点击、拖拽和提交行为。

**技术栈：** React 19、TanStack Router、React.lazy/Suspense、Vite/Rollup、Vitest、Testing Library。

---

## 文件职责

- `frontend/src/main.tsx`：路由预加载缓存时间。
- `frontend/src/routes/login.tsx`：仅保留登录守卫，不再同步引用视觉页面。
- `frontend/src/routes/login.lazy.tsx`：懒加载登录视觉页面。
- `frontend/src/routes/_app.tsx`：Piko 动态导入和局部 Suspense。
- `frontend/src/features/viewer-kit/three-d/LazyThreeDDirectorDialog.tsx`：3D Director 统一懒加载边界。
- `frontend/src/features/viewer-kit/three-d/ThreeDDirectorDialog.tsx`：只导出 props 类型供轻量 wrapper 使用。
- 八个 3D 调用文件：只把导入切换到 lazy wrapper。
- `frontend/src/features/freezone/AssetLibraryPanel.tsx`：图片 eager 上限与无封面视频占位策略。
- `frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`：动态加载与资源策略契约。
- `frontend/src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx`：关闭不加载、打开才加载的行为测试。

### 任务 1：登录路由与预加载缓存

**文件：**
- 创建：`frontend/src/routes/login.lazy.tsx`
- 修改：`frontend/src/routes/login.tsx`
- 修改：`frontend/src/main.tsx`
- 创建：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`

- [ ] **步骤 1：编写失败的契约测试**

测试读取真实源码并断言：`login.tsx` 不含 `LoginCinematicPage` 静态 import；`login.lazy.tsx` 使用 `createLazyFileRoute("/login")` 并渲染视觉页面；`main.tsx` 包含 `defaultPreloadStaleTime: 30_000`。

```ts
expect(loginRoute).not.toMatch(/import\s+\{\s*LoginCinematicPage/);
expect(loginLazy).toContain('createLazyFileRoute("/login")');
expect(main).toContain("defaultPreloadStaleTime: 30_000");
```

- [ ] **步骤 2：运行红灯**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx`

预期：因同步登录 import、缺少 lazy route、预加载时间为 0 而失败。

- [ ] **步骤 3：最小实现**

`login.tsx` 删除 component import 与 `component` 字段；新增：

```tsx
import { createLazyFileRoute } from "@tanstack/react-router";
import { LoginCinematicPage } from "@/components/login/cinematic/LoginCinematicPage";

export const Route = createLazyFileRoute("/login")({
  component: LoginCinematicPage,
});
```

将 `main.tsx` 的预加载有效期改为 `30_000`。

- [ ] **步骤 4：运行绿灯和登录守卫回归**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/routes/auth-gating.test.ts`

预期：通过；登录 beforeLoad 行为不变。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/main.tsx frontend/src/routes/login.tsx frontend/src/routes/login.lazy.tsx frontend/src/__tests__/performance/navigation-resource-loading.test.tsx
git commit -m "perf: lazy load the login experience"
```

### 任务 2：Piko 游戏按打开加载

**文件：**
- 修改：`frontend/src/routes/_app.tsx`
- 修改：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`
- 测试：`frontend/src/__tests__/routes/app-project-guard.test.tsx`

- [ ] **步骤 1：扩展失败测试**

断言应用外壳使用 `lazy(() => import(...PikoInspirationStation))`，不再出现同步静态 import，并且只在 `pikoStationOpen` 为真时挂载懒组件。

- [ ] **步骤 2：运行红灯**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx`

预期：因 `_app.tsx` 仍同步导入并始终挂载 Piko 而失败。

- [ ] **步骤 3：最小实现**

从 React 导入 `lazy`、`Suspense`，定义：

```tsx
const PikoInspirationStation = lazy(() =>
  import("@/features/piko-mini-game/PikoInspirationStation").then((module) => ({
    default: module.PikoInspirationStation,
  })),
);
```

仅在 `pikoStationOpen` 时渲染局部 Suspense 与组件；保持 `onClose` 逻辑不变。

- [ ] **步骤 4：运行绿灯与布局回归**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/routes/app-project-guard.test.tsx`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/routes/_app.tsx frontend/src/__tests__/performance/navigation-resource-loading.test.tsx
git commit -m "perf: defer loading the Piko station"
```

### 任务 3：3D Director 统一懒加载边界

**文件：**
- 创建：`frontend/src/features/viewer-kit/three-d/LazyThreeDDirectorDialog.tsx`
- 修改：`frontend/src/features/viewer-kit/three-d/ThreeDDirectorDialog.tsx`
- 修改：`frontend/src/components/assets/scenes-panel.tsx`
- 修改：`frontend/src/components/episode/beat-workbench/sketch-section.tsx`
- 修改：`frontend/src/components/episode/beat-workbench/render-section.tsx`
- 修改：`frontend/src/features/canvas/nodes/ImageGenNode.tsx`
- 修改：`frontend/src/features/canvas/nodes/SkillNode.tsx`
- 修改：`frontend/src/features/canvas/nodes/UploadNode.tsx`
- 修改：`frontend/src/features/canvas/nodes/ThreeDWorldNode.tsx`
- 修改：`frontend/src/features/canvas/ui/CanvasHistoryAssetsModal.tsx`
- 创建：`frontend/src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx`
- 修改：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`

- [ ] **步骤 1：编写 wrapper 红灯测试**

mock `ThreeDDirectorDialog` 模块，渲染 `open={false}` 后断言实现未执行；重新渲染 `open={true}`，等待实现标记出现。

```tsx
const { rerender } = render(<LazyThreeDDirectorDialog open={false} {...requiredProps} />);
expect(screen.queryByTestId("heavy-3d-dialog")).not.toBeInTheDocument();
rerender(<LazyThreeDDirectorDialog open {...requiredProps} />);
expect(await screen.findByTestId("heavy-3d-dialog")).toBeInTheDocument();
```

契约测试同时断言八个生产调用文件不再直接从 `ThreeDDirectorDialog` 导入运行时组件。

- [ ] **步骤 2：运行红灯**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx src/__tests__/performance/navigation-resource-loading.test.tsx`

预期：wrapper 不存在或调用方仍直接静态导入。

- [ ] **步骤 3：最小实现**

导出 `ThreeDDirectorDialogProps`，创建 wrapper：

```tsx
const HeavyThreeDDirectorDialog = lazy(() =>
  import("./ThreeDDirectorDialog").then((module) => ({ default: module.ThreeDDirectorDialog })),
);

export function LazyThreeDDirectorDialog(props: ThreeDDirectorDialogProps) {
  if (!props.open) return null;
  return (
    <Suspense fallback={<div role="status" aria-label="正在加载 3D 导演" />}>
      <HeavyThreeDDirectorDialog {...props} />
    </Suspense>
  );
}
```

八个调用文件将组件导入改为 wrapper；纯类型导入仍可指向原实现文件。

- [ ] **步骤 4：运行绿灯及现有 3D 契约**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/features/viewer-kit/freezone-viewer-contract.test.ts`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/features/viewer-kit/three-d frontend/src/components/assets/scenes-panel.tsx frontend/src/components/episode/beat-workbench/sketch-section.tsx frontend/src/components/episode/beat-workbench/render-section.tsx frontend/src/features/canvas/nodes frontend/src/features/canvas/ui/CanvasHistoryAssetsModal.tsx frontend/src/__tests__/performance/navigation-resource-loading.test.tsx
git commit -m "perf: load the 3D director on demand"
```

### 任务 4：素材列表降低媒体抢占

**文件：**
- 修改：`frontend/src/features/freezone/AssetLibraryPanel.tsx`
- 修改：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`
- 测试：`frontend/src/__tests__/features/freezone/asset-library-panel-beat-context.test.tsx`

- [ ] **步骤 1：扩展失败测试**

断言图片 eager 条件不超过 `index < 8`，文件中不存在 `index < 20`；无封面视频分支不再渲染真实 `<video preload="metadata">`。

- [ ] **步骤 2：运行红灯**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx`

预期：命中当前 `index < 20` 和视频 metadata 实现。

- [ ] **步骤 3：最小实现**

网格图片 eager 上限改为 8；移除两个卡片实现中的 `videoPosterUrl` metadata video 分支，无封面视频使用现有 Video 图标占位。保留 `asset.url`、点击、拖拽 payload 与详情播放器行为。

- [ ] **步骤 4：运行绿灯与素材面板回归**

运行：`.\\node_modules\\.bin\\vitest.CMD run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/features/freezone/asset-library-panel-beat-context.test.tsx`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/features/freezone/AssetLibraryPanel.tsx frontend/src/__tests__/performance/navigation-resource-loading.test.tsx
git commit -m "perf: reduce eager media loading in the asset library"
```

### 任务 5：综合验证与产物检查

**文件：**
- 不新增生产文件
- 按构建结果仅更新性能契约中的稳定边界断言

- [ ] **步骤 1：运行聚焦测试组**

运行：

```powershell
.\\node_modules\\.bin\\vitest.CMD run `
  src/__tests__/performance/navigation-resource-loading.test.tsx `
  src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx `
  src/__tests__/routes/auth-gating.test.ts `
  src/__tests__/routes/app-project-guard.test.tsx `
  src/__tests__/features/viewer-kit/freezone-viewer-contract.test.ts `
  src/__tests__/features/freezone/asset-library-panel-beat-context.test.tsx
```

预期：全部通过。

- [ ] **步骤 2：执行 TypeScript 与生产构建**

运行：`.\\node_modules\\.bin\\tsc.CMD -b`，成功后运行 `.\\node_modules\\.bin\\vite.CMD build`。

预期：无本次变更新增错误；若基线错误仍存在，记录文件和错误并证明其不在本次 diff。

- [ ] **步骤 3：检查产物依赖边界**

列出 `frontend/dist/assets` 最大 JS 文件，并检查登录、Piko、3D Director 形成独立异步 chunk；比较优化前记录的主包 1.63 MB、Freezone 2.33 MB、3D Director 2.08 MB，至少确认主加载链不再同步依赖三者。

- [ ] **步骤 4：检查变更完整性**

运行：`git diff --check`、`git status --short`、`git log --oneline --max-count=6`。

预期：无空白错误；只包含计划内文件；主工作区原有未提交改动未进入隔离分支。

- [ ] **步骤 5：最终审查**

逐项对照规格确认登录守卫、Piko 关闭行为、3D props、素材点击/拖拽和路由预加载模式均未改变，随后请求代码审查。
