# 前端界面加载性能优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变画布、RunningHub MiniMax 和 API 行为的前提下，将 Freezone 路由入口 JavaScript 从约 2.37 MB 降至不高于 2.02 MB，并消除 `canvasNodes.ts` 动态/静态重复导入警告。

**架构：** 保留 Canvas、AssetLibraryPanel 和同步链路为核心同步路径；将 Freezone 低频弹窗、糯米助手、节点工具、Konva 标注和 Mediabunny 转码按首次交互懒加载。Vite 产出 manifest，由无依赖脚本对 Freezone 路由入口实施 2,020,000 字节预算门禁。

**技术栈：** React 19、React.lazy/Suspense、Zustand、TanStack Router、Vite 6、Vitest 4、Node.js ESM。

---

## 文件职责

- 修改 `frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`：源码级懒加载边界回归契约。
- 修改 `frontend/src/features/freezone/FreezoneShell.tsx`：Freezone 可选功能懒加载及 `canvasNodes` 静态常量导入。
- 创建 `frontend/src/features/canvas/ui/LazyNodeToolDialog.tsx`：首次打开后加载并保留节点工具弹窗。
- 创建 `frontend/src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx`：验证关闭不加载、打开显示状态、加载后转交控制。
- 修改 `frontend/src/features/canvas/Canvas.tsx`：使用轻量 `LazyNodeToolDialog` 入口。
- 修改 `frontend/src/features/canvas/ui/NodeToolDialog.tsx`：标注编辑器二级懒加载。
- 修改 `frontend/src/features/canvas/nodes/VideoNode.tsx`：上传时才加载 Mediabunny 转码链路。
- 创建 `frontend/scripts/check-freezone-bundle-budget.mjs`：读取 manifest、报告依赖尺寸并执行预算。
- 创建 `frontend/src/__tests__/performance/freezone-bundle-budget.test.js`：预算脚本纯函数测试。
- 修改 `frontend/vite.config.ts`：生成 `.vite/manifest.json`。
- 修改 `frontend/package.json`：生产构建后自动执行包体预算检查。

### 任务 1：建立懒加载边界红灯

**文件：**
- 修改：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`
- 参考：`frontend/src/features/viewer-kit/three-d/LazyThreeDDirectorDialog.tsx`

- [ ] **步骤 1：添加失败的源码契约测试**

在 `navigation resource loading` 测试组中加入以下测试：

```tsx
it("defers optional freezone features until interaction", () => {
  const shell = readSource("src/features/freezone/FreezoneShell.tsx");
  const deferredModules = [
    "@/features/superchat/superchat-panel",
    "./commit/CommitDialog",
    "@/pipeline-import/CreateIdentityDialog",
    "@/pipeline-import/CompareDialog",
    "@/pipeline-import/MaskEditor",
  ];

  for (const modulePath of deferredModules) {
    expect(shell, modulePath).not.toMatch(
      new RegExp(`^\\s*import(?!\\s+type\\b)[^\\r\\n]*["']${modulePath.split("/").join("\\/")}["']`, "m"),
    );
    expect(shell, modulePath).toContain(`import("${modulePath}")`);
  }

  expect(shell).toContain('role="status"');
  expect(shell).toContain('aria-live="polite"');
});

it("uses one static canvas node domain import in the freezone shell", () => {
  const shell = readSource("src/features/freezone/FreezoneShell.tsx");

  expect(shell).not.toMatch(/await import\([\s\S]*?canvasNodes[\s\S]*?\)/);
  expect(shell).toMatch(
    /import\s*\{[\s\S]*CANVAS_NODE_TYPES[\s\S]*DEFAULT_NODE_WIDTH[\s\S]*\}\s*from\s*["']@\/features\/canvas\/domain\/canvasNodes["']/,
  );
});

it("defers the node tool dialog and annotate editor", () => {
  const canvas = readSource("src/features/canvas/Canvas.tsx");
  const lazyDialog = readSource("src/features/canvas/ui/LazyNodeToolDialog.tsx");
  const dialog = readSource("src/features/canvas/ui/NodeToolDialog.tsx");

  expect(canvas).not.toContain("./ui/NodeToolDialog");
  expect(canvas).toContain("./ui/LazyNodeToolDialog");
  expect(lazyDialog).toMatch(/lazy\(\(\)\s*=>\s*import\(["']\.\/NodeToolDialog["']\)/);
  expect(lazyDialog).toContain('role="status"');
  expect(dialog).not.toMatch(
    /^\s*import(?!\s+type\b)[^\r\n]*["']\.\/tool-editors\/AnnotateToolEditor["']/m,
  );
  expect(dialog).toMatch(/lazy\(\(\)\s*=>\s*import\(["']\.\/tool-editors\/AnnotateToolEditor["']\)/);
});

it("loads video transcoding only after a video upload starts", () => {
  const videoNode = readSource("src/features/canvas/nodes/VideoNode.tsx");

  expect(videoNode).not.toMatch(
    /^\s*import(?!\s+type\b)[^\r\n]*["']@\/features\/canvas\/application\/videoTranscode["']/m,
  );
  expect(videoNode).toContain(
    'await import("@/features/canvas/application/videoTranscode")',
  );
});
```

- [ ] **步骤 2：运行测试确认红灯**

运行：

```powershell
pnpm exec vitest run src/__tests__/performance/navigation-resource-loading.test.tsx
```

预期：新增的四项测试失败，分别指出可选模块仍静态导入、`canvasNodes` 仍动态导入、节点工具仍同步加载、视频转码仍静态导入。

- [ ] **步骤 3：提交红灯契约**

```powershell
git add -- frontend/src/__tests__/performance/navigation-resource-loading.test.tsx
git commit -m "Hermes: 添加前端懒加载性能红灯契约"
```

### 任务 2：延后 FreezoneShell 低频功能

**文件：**
- 修改：`frontend/src/features/freezone/FreezoneShell.tsx`
- 测试：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`

- [ ] **步骤 1：加入核心静态导入与懒组件定义**

将 React 导入扩展为 `lazy`、`Suspense`，静态导入画布常量，并为五个低频模块定义命名导出适配：

```tsx
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CANVAS_NODE_TYPES,
  DEFAULT_NODE_WIDTH,
} from "@/features/canvas/domain/canvasNodes";

const LazySuperChatPanel = lazy(() =>
  import("@/features/superchat/superchat-panel").then((module) => ({
    default: module.SuperChatPanel,
  })),
);
const LazyCommitDialog = lazy(() =>
  import("./commit/CommitDialog").then((module) => ({ default: module.CommitDialog })),
);
const LazyCreateIdentityDialog = lazy(() =>
  import("@/pipeline-import/CreateIdentityDialog").then((module) => ({
    default: module.CreateIdentityDialog,
  })),
);
const LazyCompareDialog = lazy(() =>
  import("@/pipeline-import/CompareDialog").then((module) => ({
    default: module.CompareDialog,
  })),
);
const LazyMaskEditor = lazy(() =>
  import("@/pipeline-import/MaskEditor").then((module) => ({ default: module.MaskEditor })),
);

function DeferredFeatureBoundary({ label, children }: React.PropsWithChildren<{ label: string }>) {
  return (
    <Suspense
      fallback={(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55">
          <div role="status" aria-live="polite" className="rounded-lg bg-background px-4 py-3 text-sm">
            {label}
          </div>
        </div>
      )}
    >
      {children}
    </Suspense>
  );
}
```

- [ ] **步骤 2：仅在打开状态挂载懒组件**

将 `CommitDialog`、`CreateIdentityDialog`、`CompareDialog`、`MaskEditor` 替换为对应 `Lazy*` 组件，并用 `DeferredFeatureBoundary` 包裹。现有条件 `pushState`、`createIdentitySource`、`comparePair`、`maskTarget` 保持在边界外侧，从而关闭状态不触发网络加载。

`FreezoneChatDock` 内的 `SuperChatPanel` 替换为：

```tsx
<Suspense
  fallback={<div role="status" aria-live="polite" className="p-4 text-sm text-muted-foreground">正在加载糯米助手…</div>}
>
  <LazySuperChatPanel variant="freezone" onRequestClose={() => onOpenChange(false)} />
</Suspense>
```

- [ ] **步骤 3：移除无效动态导入**

将 `handleMaskEditResult` 中的动态导入删除：

```tsx
const handleMaskEditResult = async (newUrl: string) => {
  const addNode = useCanvasStore.getState().addNode;
```

继续使用文件顶部静态导入的 `CANVAS_NODE_TYPES` 和 `DEFAULT_NODE_WIDTH`，其余节点创建逻辑不变。

- [ ] **步骤 4：运行 Freezone 契约测试**

```powershell
pnpm exec vitest run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/features/viewer-kit/freezone-viewer-contract.test.ts
```

预期：Freezone 两项新增契约通过；现有 Freezone viewer 契约无回归。

- [ ] **步骤 5：提交 Freezone 拆分**

```powershell
git add -- frontend/src/features/freezone/FreezoneShell.tsx
git commit -m "Hermes: 延后加载画布低频功能"
```

### 任务 3：延后节点工具和 Konva 标注编辑器

**文件：**
- 创建：`frontend/src/features/canvas/ui/LazyNodeToolDialog.tsx`
- 创建：`frontend/src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx`
- 修改：`frontend/src/features/canvas/Canvas.tsx`
- 修改：`frontend/src/features/canvas/ui/NodeToolDialog.tsx`

- [ ] **步骤 1：编写 LazyNodeToolDialog 行为红灯**

测试使用延迟模块 mock，验证关闭时不加载、首次打开显示可访问状态、模块解析后渲染，并在关闭后继续保留已加载容器以交给内部退出动画：

```tsx
import type { ComponentType } from "react";
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "@/stores/canvasStore";

const deferredModule = vi.hoisted(() => {
  let resolveModule!: (module: { NodeToolDialog: ComponentType }) => void;
  const promise = new Promise<{ NodeToolDialog: ComponentType }>((resolve) => {
    resolveModule = resolve;
  });
  return { load: vi.fn(() => promise), resolveModule };
});

vi.mock("@/features/canvas/ui/NodeToolDialog", () => deferredModule.load());

import { LazyNodeToolDialog } from "@/features/canvas/ui/LazyNodeToolDialog";

describe("LazyNodeToolDialog", () => {
  beforeEach(() => {
    useCanvasStore.setState({ activeToolDialog: null });
  });

  it("loads on first open and keeps the loaded container mounted", async () => {
    render(<LazyNodeToolDialog />);
    expect(deferredModule.load).not.toHaveBeenCalled();

    act(() => useCanvasStore.setState({ activeToolDialog: { nodeId: "node-1", toolType: "crop" } }));
    expect(await screen.findByRole("status")).toHaveAttribute("aria-live", "polite");

    deferredModule.resolveModule({ NodeToolDialog: () => <div>node tool loaded</div> });
    expect(await screen.findByText("node tool loaded")).toBeInTheDocument();

    act(() => useCanvasStore.setState({ activeToolDialog: null }));
    expect(screen.getByText("node tool loaded")).toBeInTheDocument();
  });
});
```

- [ ] **步骤 2：运行行为测试确认红灯**

```powershell
pnpm exec vitest run src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx
```

预期：FAIL，因为 `LazyNodeToolDialog.tsx` 尚不存在。

- [ ] **步骤 3：实现首次打开加载的轻量包装器**

```tsx
import { lazy, Suspense, useEffect, useState } from "react";
import { useCanvasStore } from "@/stores/canvasStore";

const NodeToolDialog = lazy(() =>
  import("./NodeToolDialog").then((module) => ({ default: module.NodeToolDialog })),
);

export function LazyNodeToolDialog() {
  const active = useCanvasStore((state) => Boolean(state.activeToolDialog));
  const [hasOpened, setHasOpened] = useState(active);

  useEffect(() => {
    if (active) setHasOpened(true);
  }, [active]);

  if (!hasOpened) return null;

  return (
    <Suspense
      fallback={<div role="status" aria-live="polite" className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 text-sm">正在加载节点工具…</div>}
    >
      <NodeToolDialog />
    </Suspense>
  );
}
```

在 `Canvas.tsx` 中将同步 `NodeToolDialog` 导入和 JSX 替换为 `LazyNodeToolDialog`。

- [ ] **步骤 4：二级懒加载 AnnotateToolEditor**

在 `NodeToolDialog.tsx` 中删除其值导入并定义：

```tsx
const AnnotateToolEditor = lazy(() =>
  import("./tool-editors/AnnotateToolEditor").then((module) => ({
    default: module.AnnotateToolEditor,
  })),
);
```

在 `activePlugin.editor === "annotate"` 分支中使用带 `role="status"`、`aria-live="polite"` 的 `Suspense` 包裹编辑器；其他工具分支保持不变。

- [ ] **步骤 5：运行节点工具和导航测试**

```powershell
pnpm exec vitest run src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/features/canvas/canvas-add-node-panel.test.tsx
```

预期：全部通过。

- [ ] **步骤 6：提交节点工具拆分**

```powershell
git add -- frontend/src/features/canvas/ui/LazyNodeToolDialog.tsx frontend/src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx frontend/src/features/canvas/Canvas.tsx frontend/src/features/canvas/ui/NodeToolDialog.tsx
git commit -m "Hermes: 延后加载节点工具与标注引擎"
```

### 任务 4：延后 Mediabunny 视频转码链路

**文件：**
- 修改：`frontend/src/features/canvas/nodes/VideoNode.tsx`
- 测试：`frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`

- [ ] **步骤 1：实现上传时动态加载**

删除顶层 `ensureWebSafeVideo` 值导入，在 `processFile` 的 `try` 块中替换为：

```tsx
const { ensureWebSafeVideo } = await import(
  "@/features/canvas/application/videoTranscode"
);
const prepared = await ensureWebSafeVideo(file);
```

动态导入发生在设置 `isUploading: true` 之后，因此下载期间继续复用现有上传状态。异常仍由现有 `catch` 和 toast 路径处理。

- [ ] **步骤 2：运行视频与导航回归测试**

```powershell
pnpm exec vitest run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/features/canvas/video-error-notification.test.ts src/__tests__/features/canvas/video-model-capabilities.test.ts
```

预期：全部通过。

- [ ] **步骤 3：提交视频转码拆分**

```powershell
git add -- frontend/src/features/canvas/nodes/VideoNode.tsx
git commit -m "Hermes: 延后加载视频转码链路"
```

### 任务 5：建立 Freezone 构建产物预算

**文件：**
- 创建：`frontend/scripts/check-freezone-bundle-budget.mjs`
- 创建：`frontend/src/__tests__/performance/freezone-bundle-budget.test.js`
- 修改：`frontend/vite.config.ts`
- 修改：`frontend/package.json`

- [ ] **步骤 1：编写预算纯函数红灯测试**

```js
import { describe, expect, it } from "vitest";
import {
  FREEZONE_ENTRY_SOURCE,
  FREEZONE_MAX_BYTES,
  assertFreezoneBudget,
  findFreezoneEntry,
} from "../../../scripts/check-freezone-bundle-budget.mjs";

describe("freezone bundle budget", () => {
  it("finds the freezone route entry by source path", () => {
    const entry = { file: "assets/freezone.js", src: FREEZONE_ENTRY_SOURCE };
    expect(findFreezoneEntry({ freezone: entry })).toBe(entry);
  });

  it("rejects an entry larger than the approved budget", () => {
    expect(() => assertFreezoneBudget(FREEZONE_MAX_BYTES + 1)).toThrow(/exceeds/);
    expect(() => assertFreezoneBudget(FREEZONE_MAX_BYTES)).not.toThrow();
  });
});
```

- [ ] **步骤 2：运行预算测试确认红灯**

```powershell
pnpm exec vitest run src/__tests__/performance/freezone-bundle-budget.test.js
```

预期：FAIL，因为预算模块尚不存在。

- [ ] **步骤 3：实现预算脚本**

脚本导出以下稳定接口：

```js
import { readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const FREEZONE_ENTRY_SOURCE = "src/routes/_app/projects.$project/freezone.lazy.tsx";
export const FREEZONE_MAX_BYTES = 2_020_000;

export function findFreezoneEntry(manifest) {
  const entry = Object.values(manifest).find((item) => item.src === FREEZONE_ENTRY_SOURCE);
  if (!entry) throw new Error(`Freezone manifest entry not found: ${FREEZONE_ENTRY_SOURCE}`);
  return entry;
}

export function assertFreezoneBudget(bytes) {
  if (bytes > FREEZONE_MAX_BYTES) {
    throw new Error(`Freezone entry ${bytes} bytes exceeds ${FREEZONE_MAX_BYTES} byte budget`);
  }
}

export function checkFreezoneBundle(rootDir) {
  const distDir = resolve(rootDir, "dist");
  const manifest = JSON.parse(readFileSync(resolve(distDir, ".vite/manifest.json"), "utf8"));
  const entry = findFreezoneEntry(manifest);
  const bytes = statSync(resolve(distDir, entry.file)).size;
  const dependencyFiles = [...(entry.imports ?? []), ...(entry.dynamicImports ?? [])]
    .map((key) => manifest[key]?.file)
    .filter((file) => typeof file === "string");

  console.log(`[bundle-budget] freezone ${entry.file}: ${bytes} bytes`);
  for (const file of dependencyFiles) {
    console.log(`[bundle-budget] dependency ${file}: ${statSync(resolve(distDir, file)).size} bytes`);
  }
  assertFreezoneBudget(bytes);
}

const scriptPath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === scriptPath) {
  checkFreezoneBundle(resolve(dirname(scriptPath), ".."));
}
```

- [ ] **步骤 4：启用 manifest 和构建门禁**

在 `vite.config.ts` 的 `build` 中加入：

```ts
manifest: true,
```

将 `package.json` 中的构建脚本改为：

```json
"build": "tsc -b && vite build && node scripts/check-freezone-bundle-budget.mjs"
```

- [ ] **步骤 5：运行预算单测**

```powershell
pnpm exec vitest run src/__tests__/performance/freezone-bundle-budget.test.js
```

预期：2 项测试全部通过。

- [ ] **步骤 6：提交预算门禁**

```powershell
git add -- frontend/scripts/check-freezone-bundle-budget.mjs frontend/src/__tests__/performance/freezone-bundle-budget.test.js frontend/vite.config.ts frontend/package.json
git commit -m "Hermes: 增加 Freezone 包体预算门禁"
```

### 任务 6：完整验证与包体复盘

**文件：**
- 验证：`frontend/` 全部生产代码与测试
- 对照：`docs/superpowers/specs/2026-08-29-frontend-loading-performance-design.md`

- [ ] **步骤 1：运行全部定向性能回归**

```powershell
pnpm exec vitest run src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/performance/freezone-bundle-budget.test.js src/__tests__/features/canvas/lazy-node-tool-dialog.test.tsx src/__tests__/features/viewer-kit/three-d/LazyThreeDDirectorDialog.test.tsx src/__tests__/features/viewer-kit/freezone-viewer-contract.test.ts
```

预期：所有测试通过，0 项失败。

- [ ] **步骤 2：运行完整前端测试**

```powershell
pnpm test
```

预期：所有测试通过，0 项失败；若存在与本次文件无关的既有失败，记录精确测试名并停止完成声明。

- [ ] **步骤 3：运行完整生产构建和预算门禁**

```powershell
pnpm run build
```

预期：退出码 0；`tsc -b`、Vite 构建和预算脚本全部成功；日志不再包含 `canvasNodes.ts is dynamically imported ... but also statically imported`；预算日志报告 Freezone 入口不高于 2,020,000 字节。

- [ ] **步骤 4：检查提交边界和工作区**

```powershell
git diff --check
git status --short
git log -6 --oneline
```

预期：本计划文件均已提交；用户原有未提交文件保持原状态；不存在本次修复的未提交残留。

- [ ] **步骤 5：本地界面冒烟验证**

启动现有后端和 `pnpm run dev`，打开项目 Freezone 路由，按顺序验证：

1. 从“视觉风格”切换到“创作画布”，核心画布和资产面板先显示。
2. 打开“糯米助手”，加载状态立即出现，随后助手面板可交互。
3. 打开节点工具，首次显示“正在加载节点工具…”，随后裁剪工具可打开和关闭。
4. 打开标注工具，首次显示局部加载状态，画笔/矩形标注可用。
5. 选择一个本地视频，上传状态立即出现，转码完成后视频预览正常。
6. 浏览器控制台没有 ChunkLoadError、未处理 Promise rejection 或 React hook 错误。

- [ ] **步骤 6：记录最终证据**

最终回复必须列出：优化前后 Freezone 原始/gzip 体积、消除的构建警告、定向测试数、完整测试数、生产构建退出码和各实现提交哈希。不得把 Vite 仍存在的其他大块警告描述为已经消除。
