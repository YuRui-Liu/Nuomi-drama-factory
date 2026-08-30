# NuomiDrama 版本更新弹窗品牌替换实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用立即渲染的 NuomiDrama 内建品牌头图替换版本更新弹窗中烧录旧 DramClaw 标识的远程视频。

**架构：** 保持 `VersionUpdateDialog` 的数据流、尺寸和交互不变，仅将头图媒体层替换为复用 `BrandMark` 的语义化 React 结构。回归测试从用户可见行为验证 NuomiDrama 头图存在，并验证 DOM 不再创建视频媒体元素。

**技术栈：** React 19、TypeScript、Tailwind CSS、Vitest、Testing Library

---

## 文件结构

- 修改 `frontend/src/__tests__/features/version-update/VersionUpdateDialog.test.tsx`：新增品牌头图回归测试。
- 修改 `frontend/src/features/version-update/VersionUpdateDialog.tsx`：删除旧视频 URL，渲染 NuomiDrama 内建品牌头图。

### 任务 1：用测试锁定品牌头图行为

**文件：**
- 测试：`frontend/src/__tests__/features/version-update/VersionUpdateDialog.test.tsx`

- [ ] **步骤 1：编写失败的测试**

```tsx
it("renders the built-in NuomiDrama hero without remote video media", async () => {
  renderDialog();

  expect(
    await screen.findByRole("img", { name: "NuomiDrama 版本更新" }),
  ).toBeInTheDocument();
  expect(document.querySelector("video")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pnpm test -- src/__tests__/features/version-update/VersionUpdateDialog.test.tsx
```

预期：新增用例 FAIL，因为当前弹窗没有名为 `NuomiDrama 版本更新` 的图像角色，且仍渲染 `<video>`。

### 任务 2：替换旧品牌视频

**文件：**
- 修改：`frontend/src/features/version-update/VersionUpdateDialog.tsx`

- [ ] **步骤 1：编写最少实现代码**

删除 `UPDATE_HERO_VIDEO_URL`，导入 `BrandMark`，并把 `<video>` 容器替换为：

```tsx
<div
  aria-label="NuomiDrama 版本更新"
  className="relative flex aspect-[2/1] items-center justify-center overflow-hidden rounded-[12px] bg-[#0D0E10]"
  role="img"
>
  <div aria-hidden="true" className="absolute inset-0 ..." />
  <div aria-hidden="true" className="absolute ... bg-[#E5FF5C]/20 blur-3xl" />
  <BrandMark className="relative z-10 [--brand-accent:#E5FF5C] text-white" />
  <span aria-hidden="true" className="absolute bottom-3 left-3 ...">
    RELEASE NOTES · 2026
  </span>
</div>
```

- [ ] **步骤 2：运行定向测试验证通过**

运行：

```bash
pnpm test -- src/__tests__/features/version-update/VersionUpdateDialog.test.tsx
```

预期：3 个用例全部 PASS。

- [ ] **步骤 3：运行类型与生产构建验证**

运行：

```bash
pnpm build
```

预期：TypeScript、Vite 构建和 Freezone bundle budget 全部 exit 0。

- [ ] **步骤 4：检查差异并提交目标文件**

```bash
git diff --check -- frontend/src/features/version-update/VersionUpdateDialog.tsx frontend/src/__tests__/features/version-update/VersionUpdateDialog.test.tsx
git add frontend/src/features/version-update/VersionUpdateDialog.tsx frontend/src/__tests__/features/version-update/VersionUpdateDialog.test.tsx
git commit -m "fix(frontend): 替换版本弹窗旧品牌头图"
```

提交只包含本任务目标文件，不纳入工作区其他并行改动。

