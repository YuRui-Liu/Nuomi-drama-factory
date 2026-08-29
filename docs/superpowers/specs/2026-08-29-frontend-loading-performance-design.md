# 前端界面加载性能优化设计

## 目标

在不改变创作画布、RunningHub MiniMax 视频能力或用户工作流的前提下，减少进入“创作画布”时必须下载、解析和执行的 JavaScript，并消除 `canvasNodes.ts` 的无效动态导入警告。所有延后加载的功能必须在用户真正打开或触发时保持可用，并显示可访问的局部加载状态。

## 现状与根因

2026-08-29 的生产构建基线：

- `freezone.lazy`：约 2.37 MB，gzip 后约 682 KB。
- `ThreeDDirectorDialog`：约 2.06 MB，gzip 后约 529 KB，当前已正确独立懒加载。
- 主 `index`：约 1.32 MB，gzip 后约 385 KB。
- Vite 警告 `canvasNodes.ts` 同时被动态和静态导入。

根因不是单一文件过大，而是画布入口同步连接了多条低频功能链路：

1. `FreezoneShell` 同步导入糯米助手、提交、身份创建、对比和蒙版编辑弹窗。
2. `Canvas` 常驻挂载工具弹窗，标注编辑器进一步同步带入 `react-konva`/`konva`。
3. `VideoNode` 同步导入 `videoTranscode.ts`，使 `mediabunny` 在用户未上传视频时也进入加载图。
4. 蒙版结果处理器动态导入 `canvasNodes.ts` 中的常量，但同一模块已被画布系统广泛静态导入，所以该动态导入无法形成独立分块。
5. 现有导航性能测试只验证源码中存在懒加载边界，没有对生产构建产物设置预算，无法阻止包体回涨。

## 选定方案：安全结构性懒加载

### 1. 修正无效导入

`FreezoneShell` 直接静态导入 `CANVAS_NODE_TYPES` 和 `DEFAULT_NODE_WIDTH`，移除事件处理器中的 `await import(canvasNodes)`。该模块本来就是画布核心依赖，因此静态导入不会增加实际首屏负担，并能消除误导性的 Vite 警告。

### 2. 延后 FreezoneShell 的低频功能

以下组件改为 `React.lazy`，且只有对应状态为打开时才挂载：

- `SuperChatPanel`
- `CommitDialog`
- `CreateIdentityDialog`
- `CompareDialog`
- `MaskEditor`

`AssetLibraryPanel`、`Canvas`、同步状态和冲突处理保持同步加载，因为它们是进入画布后立即需要的核心体验。

每个懒加载入口使用局部 `Suspense` fallback，显示 `role="status"` 和 `aria-live="polite"`。加载失败继续交给现有全局错误/ChunkLoadError 恢复机制，不新增另一套错误状态。

### 3. 延后画布工具与标注引擎

`NodeToolDialog` 仅在存在 `activeToolDialog` 时加载和挂载；其中 `AnnotateToolEditor` 再按 `editor === "annotate"` 二次懒加载。这样普通画布浏览不加载工具弹窗，裁剪或表单工具也不会提前加载 Konva 标注引擎。

加载期间保留遮罩和明确状态提示，避免用户点击后界面无反馈。

### 4. 延后视频转码链路

`VideoNode` 不再顶层导入 `ensureWebSafeVideo`。只有用户选择或拖入视频、进入上传处理后，才动态导入 `videoTranscode.ts`；FFmpeg 兜底继续保留其现有的第二级动态导入。

该变更只调整代码加载时机，不改变转码判定、文件格式、上传接口或 RunningHub/MiniMax 视频生成能力。

### 5. 构建产物预算

启用 Vite manifest，并增加无第三方依赖的产物检查脚本。脚本从 manifest 定位 Freezone 路由入口并读取实际文件大小：

- 第一阶段硬门槛：Freezone 路由入口 JavaScript 相比 2.37 MB 基线至少下降 15%，即不高于 2.02 MB。
- 同时打印该入口的直接静态依赖与动态依赖尺寸，便于后续继续优化。
- `pnpm run build` 在 Vite 构建后执行预算检查，超标时返回非零退出码。

不通过单纯提高 `chunkSizeWarningLimit` 隐藏警告，也不在本阶段强行拆分所有画布节点。

## 数据与交互流程

1. 用户切换到创作画布，路由只加载核心画布、资产面板和同步逻辑。
2. 用户打开糯米助手或低频弹窗时，对应 chunk 开始加载，局部 fallback 立即显示。
3. 用户打开节点工具时加载工具容器；只有选择标注功能时才加载 Konva。
4. 用户上传视频时加载 Mediabunny 转码链路；只有快速路径失败时才继续加载 FFmpeg。
5. 画布 API 请求、缓存键、超时和后端数据格式保持不变。

## 测试策略

先扩展 `navigation-resource-loading.test.tsx` 形成红灯，约束：

- FreezoneShell 不再动态导入 `canvasNodes.ts`。
- 五个低频组件不存在生产值静态导入，并且只在打开状态下通过懒加载包装器挂载。
- Canvas 不再无条件挂载同步 `NodeToolDialog`。
- VideoNode 不再静态导入 `videoTranscode.ts`。
- 懒加载 fallback 均具备可访问状态。

然后运行相关组件测试、画布测试、完整前端测试和 `pnpm run build`。最终记录优化前后 chunk 的原始大小与 gzip 大小。

## 非目标

- 不拆分 React Flow 的每一种节点组件。
- 不修改画布同步、API 超时或后端持久化逻辑。
- 不修改视频模型、RunningHub MiniMax 工作流或生成参数。
- 不以增加大量人工 `manualChunks` 作为本轮主要手段；只有实测证明能减少关键路径时才加入。

## 验收标准

1. `pnpm run build` 成功，且不再出现 `canvasNodes.ts` 动态/静态重复导入警告。
2. Freezone 路由入口 JavaScript 不高于 2.02 MB。
3. 打开糯米助手、提交、身份创建、对比、蒙版、节点工具、标注和视频上传仍可正常工作。
4. 所有新增与相关回归测试通过。
5. 其他用户未提交改动不被覆盖或混入提交。
