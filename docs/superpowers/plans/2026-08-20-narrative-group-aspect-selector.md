# 叙事组画幅选择器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在叙事组页面标题栏提供持久化的 `9:16 / 16:9` 选择器，并确保生成、重生成、拆分和组合视频都使用当前项目画幅。

**架构：** 新增一个纯展示的 `NarrativeAspectSelector`，业务容器继续通过项目画幅 store 读取即时值，并通过项目更新 mutation 保存 `2:3 / 16:9` 后端配置。切换时先乐观更新 store；保存失败则恢复原 orientation。所有叙事组动作从同一 orientation 推导 `9:16 / 16:9` 请求值。

**技术栈：** React、TypeScript、TanStack Query mutations、Zustand、Vitest、Testing Library、Tailwind/Radix UI Button。

---

## 文件结构

- 创建：`frontend/src/components/episode/narrative-workbench/narrative-aspect-selector.tsx`——纯展示分段选择器、保存状态和既有素材提示。
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx`——锁定互斥选择、无障碍属性与禁用状态。
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`——接入项目持久化、乐观更新/失败回滚，并把选择器放入标题栏。
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`——验证项目保存和四条媒体动作使用同一画幅。

### 任务 1：实现纯展示画幅选择器

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/narrative-aspect-selector.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NarrativeAspectSelector } from "@/components/episode/narrative-workbench/narrative-aspect-selector";

describe("NarrativeAspectSelector", () => {
  it("exposes one pressed aspect and submits the other orientation", () => {
    const onChange = vi.fn();
    render(<NarrativeAspectSelector orientation="portrait" saving={false} onChange={onChange} />);

    expect(screen.getByRole("group", { name: "目标画幅" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "9:16" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "16:9" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "16:9" }));
    expect(onChange).toHaveBeenCalledWith("landscape");
    expect(screen.getByText("仅影响后续生成；现有素材需重新生成或重新切分。")).toBeInTheDocument();
  });

  it("disables both choices while saving", () => {
    render(<NarrativeAspectSelector orientation="landscape" saving onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "9:16" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "16:9" })).toBeDisabled();
  });
});
```

- [ ] **步骤 2：运行测试并确认红灯**

运行：

```powershell
Set-Location frontend
& '.\node_modules\.bin\vitest.CMD' run src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx
```

预期：FAIL，提示无法解析 `narrative-aspect-selector` 模块。

- [ ] **步骤 3：编写最小组件实现**

```tsx
// SPDX-License-Identifier: Elastic-2.0
import type { Orientation } from "@/lib/aspect-ratio";
import { Button } from "@/components/ui/button";

export function NarrativeAspectSelector({
  orientation,
  saving,
  onChange,
}: {
  orientation: Orientation;
  saving: boolean;
  onChange: (orientation: Orientation) => void;
}) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">目标画幅</span>
        <div role="group" aria-label="目标画幅" className="inline-flex rounded-md border border-white/10 bg-black/20 p-0.5">
          {([
            ["portrait", "9:16"],
            ["landscape", "16:9"],
          ] as const).map(([value, label]) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={orientation === value ? "secondary" : "ghost"}
              className="h-7 px-3 text-xs"
              aria-pressed={orientation === value}
              disabled={saving}
              onClick={() => onChange(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">仅影响后续生成；现有素材需重新生成或重新切分。</p>
    </div>
  );
}
```

- [ ] **步骤 4：运行测试并确认绿灯**

运行同步骤 2 的命令。

预期：2 tests passed。

- [ ] **步骤 5：提交组件**

```powershell
git add -- frontend/src/components/episode/narrative-workbench/narrative-aspect-selector.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx
git commit -m "feat: add narrative aspect selector"
```

### 任务 2：接入项目持久化与失败回滚

**文件：**
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

- [ ] **步骤 1：扩展测试 mock 并编写失败测试**

在 hoisted mock 中加入：

```tsx
updateProject: vi.fn(),
setOrientation: vi.fn(),
orientation: "landscape" as "portrait" | "landscape",
error: vi.fn(),
```

将项目画幅和项目更新 mock 改为：

```tsx
vi.mock("@/stores/aspect-ratio-store", () => ({
  useProjectAspectRatio: () => ({
    orientation: m.orientation,
    spec: {},
    setOrientation: m.setOrientation,
  }),
}));
vi.mock("@/lib/queries/projects", () => ({
  useUpdateProject: () => ({ mutateAsync: m.updateProject, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: m.success, error: m.error } }));
```

在 `beforeEach` 重置为横屏并让保存成功，然后增加：

```tsx
it("saves the selected project aspect and uses it for later split", async () => {
  m.setOrientation.mockImplementation((next) => { m.orientation = next; });
  const view = render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "9:16" }));
  await waitFor(() => expect(m.updateProject).toHaveBeenCalledWith({ aspect_ratio: "2:3" }));
  expect(m.setOrientation).toHaveBeenCalledWith("portrait");

  view.rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()} />);
  expect(screen.getByRole("button", { name: "9:16" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByText("切分"));
  await waitFor(() => expect(m.mutate).toHaveBeenCalledWith({
    groupId: "g1",
    stage: "render",
    action: "split",
    aspectRatio: "9:16",
  }));
});

it("rolls back the optimistic aspect when project persistence fails", async () => {
  m.updateProject.mockRejectedValueOnce(new Error("save failed"));
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "9:16" }));
  await waitFor(() => expect(m.setOrientation.mock.calls).toEqual([["portrait"], ["landscape"]]));
  expect(m.error).toHaveBeenCalledWith("save failed");
});
```

- [ ] **步骤 2：运行聚焦测试并确认红灯**

```powershell
Set-Location frontend
& '.\node_modules\.bin\vitest.CMD' run src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：FAIL，因为标题栏尚无 `9:16` 按钮，且工作台尚未调用 `useUpdateProject`。

- [ ] **步骤 3：实现持久化、回滚和标题栏布局**

在工作台导入：

```tsx
import { aspectRatioForOrientation, type Orientation } from "@/lib/aspect-ratio";
import { useUpdateProject } from "@/lib/queries/projects";
import { NarrativeAspectSelector } from "./narrative-aspect-selector";
```

把画幅 hook 解构和项目 mutation 改为：

```tsx
const { orientation, setOrientation } = useProjectAspectRatio(project);
const updateProject = useUpdateProject(project);
const aspectRatio = orientation === "landscape" ? "16:9" as const : "9:16" as const;
```

在 loading/empty return 之前加入保存处理函数：

```tsx
const changeAspect = async (nextOrientation: Orientation) => {
  if (nextOrientation === orientation || updateProject.isPending) return;
  const previousOrientation = orientation;
  setOrientation(nextOrientation);
  try {
    await updateProject.mutateAsync({
      aspect_ratio: aspectRatioForOrientation(nextOrientation),
    });
    toast.success("项目画幅已保存");
  } catch (error) {
    setOrientation(previousOrientation);
    toast.error(error instanceof Error ? error.message : "项目画幅保存失败");
  }
};
```

把标题栏右侧的模型选择器包成横向容器并加入：

```tsx
<div className="flex flex-wrap items-start justify-end gap-3">
  <NarrativeAspectSelector
    orientation={orientation}
    saving={updateProject.isPending}
    onChange={changeAspect}
  />
  <ProjectVideoModelSelect
    value={modelId}
    models={models}
    saving={updateDefaults.isPending}
    onChange={async (videoModel) => {
      try {
        await updateDefaults.mutateAsync({
          videoModel,
          videoMode: mediaDefaults?.h3_mode ?? "auto",
          narrativeSketchProvider: mediaDefaults?.narrative_sketch_provider,
          narrativeSketchModel: mediaDefaults?.narrative_sketch_model,
          narrativeRenderProvider: mediaDefaults?.narrative_render_provider,
          narrativeRenderModel: mediaDefaults?.narrative_render_model,
        });
        toast.success("项目默认视频模型已保存");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "默认模型保存失败");
      }
    }}
  />
</div>
```

- [ ] **步骤 4：运行两个聚焦测试文件并确认绿灯**

```powershell
Set-Location frontend
& '.\node_modules\.bin\vitest.CMD' run src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：全部通过。

- [ ] **步骤 5：提交持久化接线**

```powershell
git add -- frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git commit -m "feat: persist narrative group aspect choice"
```

### 任务 3：锁定生成、重生成和组合视频的画幅一致性

**文件：**
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

- [ ] **步骤 1：让视频 mock 可观测并添加媒体链路回归测试**

在 hoisted mock 中加入 `generateVideo: vi.fn()`，并调整 mock：

```tsx
useGenerateNarrativeGroupVideo: () => ({ mutateAsync: m.generateVideo }),
vi.mock("@/components/episode/narrative-workbench/group-video-stage", () => ({
  GroupVideoStage: ({ onGenerate }: any) => (
    <button onClick={() => onGenerate({ video_model: "minimax-h3", h3_mode: "i2va" })}>
      生成组合视频
    </button>
  ),
  groupFrameSummary: () => ({ allHaveFirst: true, allHaveLast: false }),
}));
```

增加测试：

```tsx
it("uses the current landscape aspect for generate, regenerate, split, and group video", async () => {
  m.generateVideo.mockResolvedValue({ scope: "video-x" });
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()} />);

  for (const actionLabel of ["生成", "重生成"]) {
    fireEvent.click(screen.getByText(actionLabel));
    fireEvent.click(screen.getByText("确认"));
  }
  fireEvent.click(screen.getByText("切分"));
  fireEvent.click(screen.getByText("生成组合视频"));

  await waitFor(() => expect(m.generateVideo).toHaveBeenCalledWith({
    groupId: "g1",
    model: "minimax-h3",
    mode: "i2va",
    aspectRatio: "16:9",
    revision: 0,
  }));
  expect(m.mutate.mock.calls.map(([request]) => request.aspectRatio)).toEqual(["16:9", "16:9", "16:9"]);
});
```

- [ ] **步骤 2：运行聚焦测试**

```powershell
Set-Location frontend
& '.\node_modules\.bin\vitest.CMD' run src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：全部通过。

- [ ] **步骤 3：运行 TypeScript 与相关前端回归**

```powershell
Set-Location frontend
& '.\node_modules\.bin\tsc.CMD' -b
& '.\node_modules\.bin\vitest.CMD' run src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/lib/narrative-groups.test.ts
```

预期：TypeScript 构建成功，相关测试全部通过。若全项目存在与本次文件无关的既有 TypeScript 错误，记录精确文件和错误，并继续用聚焦类型/测试证据判断本次变更。

- [ ] **步骤 4：检查变更边界和格式**

```powershell
git diff --check -- frontend/src/components/episode/narrative-workbench/narrative-aspect-selector.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-aspect-selector.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git status --short
```

预期：四个目标文件无 whitespace error；其他既有工作区修改保持原样。

- [ ] **步骤 5：提交最终回归测试**

```powershell
git add -- frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git commit -m "test: cover narrative aspect media flow"
```

## 计划自检结果

- 规格覆盖：标题栏入口、项目持久化、乐观更新/失败回滚、既有媒体提示、generate/regenerate/split/video 四条链路和无障碍状态均有对应任务。
- 占位符扫描：无 `TODO`、`待定`、“添加适当处理”等不可执行占位符。
- 类型一致性：UI 使用 `Orientation = portrait | landscape`；项目保存使用 `ProjectAspectRatio = 2:3 | 16:9`；叙事组请求使用 `9:16 | 16:9`，三层映射保持明确且一致。
