# H3 Ref 视频生成卡片选择器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在叙事组「视频生成」卡片内展示 Base H3 与 H3 Ref 选择器，同时保留 H3 Ref 的未验证禁用门禁。

**架构：** `NarrativeGroupWorkbench` 继续解析当前可用模型和保存选择，把完整 catalog 与现有保存回调传给 `GroupVideoStage`。`GroupVideoStage` 复用 `ProjectVideoModelSelect` 渲染卡片内选择器；选择器展示不可用条目及原因，但只允许选择 `available=true` 的模型。

**技术栈：** React、TypeScript、Base UI Select、TanStack Query、Vitest、Testing Library。

---

## 文件职责

- 修改：`frontend/src/components/episode/narrative-workbench/project-video-model-select.tsx`：渲染完整模型目录、禁用不可用项、显示原因。
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`：在视频卡片标题栏承载模型选择器。
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：移除顶部重复入口，向视频卡片传递 catalog 和保存回调。
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx`：覆盖卡片内选择器与禁用状态。
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`：覆盖完整 catalog 透传和可用 H3 Ref 的保存链路。

### 任务 1：选择器可见性和门禁

- [ ] **步骤 1：编写失败测试**

在 `group-video-stage.test.tsx` 增加测试，传入一个可用 Base H3 和一个 `hybrid_input_unverified` 的 H3 Ref，断言卡片存在「视频模型」组合框、两个条目均可见、H3 Ref 条目禁用且显示「需完成 RunningHub 验证」。再增加可用 H3 Ref 测试，选择后断言 `onModelChange("runninghub:minimax-h3-ref")`。

- [ ] **步骤 2：验证红灯**

运行：

```bash
cd frontend && ./node_modules/.bin/vitest run src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx
```

预期：FAIL，`GroupVideoStage` 尚无模型组合框。

- [ ] **步骤 3：最少实现**

在 `ProjectVideoModelSelect` 中移除单模型静态分支；空目录仍显示「暂无可用视频模型」。对每个 `SelectItem` 设置 `disabled={!model.available}`，并将 `hybrid_input_unverified` 映射为「需完成 RunningHub 验证」。新增独立 `disabled` 属性，使任务运行时可以锁定控件而不误显示保存 spinner。

在 `GroupVideoStage` 增加以下属性并把选择器放到标题栏操作区：

```ts
models?: VideoModelCatalogItem[];
modelSaving?: boolean;
onModelChange?: (modelId: string) => void;
```

任务状态为 `queued` 或 `running` 时向选择器传递 `disabled=true`。

- [ ] **步骤 4：验证绿灯**

重新运行步骤 2 命令，预期该文件全部通过。

### 任务 2：接入工作台现有保存链路

- [ ] **步骤 1：编写失败测试**

在 `narrative-group-workbench-references.test.tsx` 的 `GroupVideoStage` mock 中渲染模型按钮；断言完整 catalog（包括不可用 H3 Ref）被传入。对可用 H3 Ref 点击按钮，断言现有 `updateDefaults` 和 `updateNarrativeGroupVideoSettings` 收到 Ref 模型 ID。

- [ ] **步骤 2：验证红灯**

运行：

```bash
cd frontend && ./node_modules/.bin/vitest run src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：FAIL，工作台尚未把完整 catalog 和回调传入卡片。

- [ ] **步骤 3：最少实现**

从工作台顶部移除 `ProjectVideoModelSelect` 和对应 import。向 `GroupVideoStage` 传入：

```tsx
models={catalog}
modelSaving={updateDefaults.isPending || videoSettingsSaving}
onModelChange={changeVideoModel}
```

当前模型解析和提交守卫继续使用 `availableVideoModels(catalog)`，因此不可用模型不能进入保存或生成链路。

- [ ] **步骤 4：验证绿灯**

重新运行步骤 2 命令，预期该文件全部通过。

### 任务 3：回归验证与提交

- [ ] **步骤 1：运行相关测试**

```bash
cd frontend && ./node_modules/.bin/vitest run \
  src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx \
  src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx \
  src/__tests__/lib/queries/media-models.test.ts
```

预期：全部通过。

- [ ] **步骤 2：运行类型和格式验证**

```bash
cd frontend && ./node_modules/.bin/tsc -b
git diff --check
```

预期：零错误。

- [ ] **步骤 3：提交实现**

只暂存并提交上述五个前端文件，提交信息：

```text
fix(frontend): expose H3 Ref selector in video stage
```
