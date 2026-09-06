# 全局设置顶部入口恢复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 CE 与 EE 运行时都在全局 Header 显示设置按钮，并能打开现有设置弹窗，同时保留 CE 专属配置告警逻辑。

**架构：** Header 继续统一持有 `settingsOpen` 状态，但不再用 `ceRuntime` 卸载设置按钮和弹窗。运行时差异只保留在网关配置查询与告警判定中，避免 EE 发起 CE 专属查询或显示错误告警。

**技术栈：** React、TypeScript、Vitest、Testing Library、TanStack Query

---

## 文件结构

- 修改：`frontend/src/__tests__/components/layout/header.test.tsx` — 增加 CE/EE 设置入口与弹窗交互回归测试，并观测 CE 专属查询是否启用。
- 修改：`frontend/src/components/layout/header.tsx` — 移除设置按钮及 `SettingsDialog` 的 CE 条件渲染，保留查询与警告门控。

### 任务 1：恢复全局设置入口

**文件：**
- 修改：`frontend/src/__tests__/components/layout/header.test.tsx`
- 修改：`frontend/src/components/layout/header.tsx`

- [ ] **步骤 1：编写失败的 EE 回归测试**

在 Header 测试中记录 `useModelGatewayConfig` 的 `enabled` 参数，并将设置弹窗替换为可观察的轻量 mock：

```tsx
const modelGatewayState = vi.hoisted(() => ({ enabledCalls: [] as boolean[] }));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: (enabled: boolean) => {
    modelGatewayState.enabledCalls.push(enabled);
    return { data: undefined };
  },
}));

vi.mock("@/components/settings/settings-dialog", () => ({
  SettingsDialog: ({ open }: { open: boolean }) =>
    open ? <div role="dialog" aria-label="Settings dialog" /> : null,
}));
```

为翻译 mock 增加 `header.settings`，并增加 EE 行为测试：

```tsx
it("keeps global settings available in EE without enabling the CE gateway query", () => {
  runtimeState.isCe = false;
  renderHeader();

  const settingsButton = screen.getByRole("button", { name: "Settings" });
  expect(settingsButton).toBeInTheDocument();
  expect(modelGatewayState.enabledCalls.at(-1)).toBe(false);

  fireEvent.click(settingsButton);
  expect(screen.getByRole("dialog", { name: "Settings dialog" })).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证旧实现正确失败**

运行：

```bash
cd frontend
./node_modules/.bin/vitest run src/__tests__/components/layout/header.test.tsx
```

预期：新增 EE 测试失败，找不到名称为 `Settings` 的按钮；原有测试继续通过。

- [ ] **步骤 3：增加 CE 入口覆盖并编写最小实现**

增加 CE 测试，确认按钮存在且 CE 查询仍启用：

```tsx
it("keeps the settings entry and gateway warning query enabled in CE", () => {
  runtimeState.isCe = true;
  renderHeader();

  expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
  expect(modelGatewayState.enabledCalls.at(-1)).toBe(true);
});
```

在每个测试前恢复运行时及调用记录：

```tsx
runtimeState.isCe = false;
modelGatewayState.enabledCalls.length = 0;
```

在 Header 中删除设置按钮外层的 `ceRuntime ? ... : null`，始终渲染原有按钮；同时将：

```tsx
{ceRuntime ? <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} /> : null}
```

改为：

```tsx
<SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
```

不改动以下门控：

```tsx
const modelGatewayConfig = useModelGatewayConfig(ceRuntime);
const hasSettingsWarning = Boolean(ceRuntime && gatewayConfig && ...);
```

- [ ] **步骤 4：运行定向测试验证通过**

运行：

```bash
cd frontend
./node_modules/.bin/vitest run src/__tests__/components/layout/header.test.tsx
```

预期：1 个测试文件全部通过，包含新增 CE、EE 回归用例。

- [ ] **步骤 5：运行类型、格式与差异检查**

运行：

```bash
cd frontend
./node_modules/.bin/tsc -b --pretty false
cd ..
git diff --check
git diff -- frontend/src/components/layout/header.tsx frontend/src/__tests__/components/layout/header.test.tsx
```

预期：TypeScript 与 `git diff --check` 退出码均为 0；差异只包含测试与最小条件渲染调整。

- [ ] **步骤 6：提交实现**

```bash
git add frontend/src/components/layout/header.tsx frontend/src/__tests__/components/layout/header.test.tsx
git commit -m "fix: restore global settings header entry"
```

