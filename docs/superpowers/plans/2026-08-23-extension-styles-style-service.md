# 漫剧扩展风格注册到 StyleService 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 18 项 `drama_ext.*` 目录风格作为正式只读预设接入 StyleService、视觉风格管理页和项目默认生成链路，同时保证原 6 项预设语义零修改。

**架构：** 后端新增一个只负责 `ExtensionStyle → StyleConfig/API DTO` 的适配模块，StyleService 通过该模块统一列举和解析扩展预设，写接口在服务端按预设身份拒绝修改。前端沿用现有 styles API，在视觉风格页按 `preset_group` 分组，并使用独立只读详情组件；ImageGenNode 根据项目默认、基础模板和节点扩展三者计算唯一有效扩展，避免重复或双扩展叠加。

**技术栈：** Python 3.11、FastAPI、Pydantic、pytest、React 19、TypeScript、TanStack Query/Router、Vitest、Testing Library。

---

## 文件结构

- 创建 `src/novelvideo/extension_styles/style_service_adapter.py`：唯一的 ExtensionStyle 到 StyleConfig/列表元数据适配边界。
- 修改 `src/novelvideo/services/style_service.py`：解析、列举和只读识别 18 项扩展预设。
- 创建 `tests/test_extension_style_service.py`：冻结 6+18 列表、详情映射、顺序和原预设语义。
- 修改 `src/novelvideo/api/routes/styles.py`：扩展详情 DTO、预览 fallback 与写端点只读保护。
- 修改 `tests/test_api_styles.py`：API 列表、详情、预览和创建/删除拒绝契约。
- 修改 `src/novelvideo/api/routes/projects.py`：通过 StyleService 接受合法 `drama_ext.*` 项目默认。
- 修改 `frontend/src/types/style.ts`：声明预设分组与扩展详情元数据。
- 创建 `frontend/src/components/styles/ExtensionStyleDetailPanel.tsx`：18 项只读详情面板。
- 修改 `frontend/src/routes/_app/projects.$project/styles.tsx`：三分组列表与详情路由；保留当前未提交页面改动。
- 修改 `frontend/src/__tests__/routes/styles.ce.test.tsx`：管理页分组、只读、默认选择和预览 fallback 行为。
- 修改 `frontend/src/features/canvas/extension-styles/composePrompt.ts`：项目默认与节点扩展的覆盖/去重纯函数。
- 修改 `frontend/src/features/canvas/nodes/ImageGenNode.tsx`：把项目默认 ID 传入请求构建器。
- 修改 `frontend/src/__tests__/features/canvas/extension-style-compose.test.ts` 与 `image-gen-prompt.test.ts`：四种组合的最终请求行为。

### 任务 1：StyleService 扩展预设适配

**文件：**
- 创建：`src/novelvideo/extension_styles/style_service_adapter.py`
- 修改：`src/novelvideo/services/style_service.py`
- 创建：`tests/test_extension_style_service.py`

- [ ] **步骤 1：编写失败的服务测试**

```python
def test_style_service_lists_six_builtin_then_eighteen_extension_presets():
    styles = StyleService.list_preset_styles()
    builtin = [item for item in styles if item["preset_group"] == "builtin"]
    extension = [item for item in styles if item["preset_group"] == "extension"]
    assert len(builtin) == 6
    assert len(extension) == 18
    assert all(item["id"].startswith("drama_ext.") for item in extension)


def test_extension_style_adapts_to_readonly_style_config():
    style = StyleService.get_style("drama_ext.japanese_cel_animation")
    assert style is not None
    assert style.is_preset is True
    assert style.style_instructions.index("cel") < style.style_instructions.index("lighting")
    assert style.avoid_instructions == ""
```

同时读取六个 `styles/presets/*.json`，按已有 canonical JSON SHA-256 基线断言内容未变。

- [ ] **步骤 2：运行测试验证失败**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_extension_style_service.py -q`

预期：FAIL；列表没有 `preset_group=extension`，且 `get_style("drama_ext...")` 返回 `None`。

- [ ] **步骤 3：实现确定性适配器**

```python
FRAGMENT_ORDER = ("medium", "rendering", "lighting", "color", "camera", "constraints")

def extension_style_to_style_config(style: ExtensionStyle) -> StyleConfig:
    instructions = "\n".join(
        phrase
        for key in FRAGMENT_ORDER
        for phrase in style.prompt_fragment[key]
    )
    return StyleConfig(
        id=style.id,
        name=style.name,
        label=style.name,
        is_preset=True,
        style_instructions=instructions,
        avoid_instructions="",
        style_tag=style.id,
        style_family="animation" if style.category in {"2d", "3d", "chinese", "experimental"} else "live_action",
        animation_subtype=style.category if style.category in {"2d", "3d"} else None,
    )
```

适配器另提供 `extension_style_metadata(style)`，返回 `preset_group="extension"`、summary/category/use_cases/preview_asset/version。StyleService 的查找顺序保持“自定义 → 原预设 → 扩展预设”，但禁止自定义风格覆盖 `drama_ext.*`。

- [ ] **步骤 4：运行服务测试验证通过**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_extension_style_service.py tests/test_extension_style_catalog.py -q`

预期：PASS；6 项原预设哈希不变，18 项适配完整。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/extension_styles/style_service_adapter.py src/novelvideo/services/style_service.py tests/test_extension_style_service.py
git commit -m "feat: register extension styles as presets"
```

### 任务 2：API 详情、预览与只读保护

**文件：**
- 修改：`src/novelvideo/api/routes/styles.py`
- 修改：`src/novelvideo/api/routes/projects.py`
- 修改：`tests/test_api_styles.py`

- [ ] **步骤 1：编写失败的 API 测试**

```python
def test_extension_style_api_is_readonly_and_project_selectable(client):
    detail = client.get("/api/v1/styles/drama_ext.japanese_cel_animation").json()["data"]
    assert detail["preset_group"] == "extension"
    assert detail["summary"]
    assert detail["use_cases"]

    created = client.post("/api/v1/styles", json={
        "id": "drama_ext.japanese_cel_animation", "name": "覆盖", "project": PROJECT,
        "config": {},
    }).json()
    deleted = client.delete(
        "/api/v1/styles/drama_ext.japanese_cel_animation", params={"project": PROJECT}
    ).json()
    assert created == {"ok": False, "error": "Cannot override preset style 'drama_ext.japanese_cel_animation'"}
    assert deleted == {"ok": False, "error": "Cannot delete preset styles"}

    updated = client.patch(f"/api/v1/projects/{PROJECT}", json={
        "visual_style": "drama_ext.japanese_cel_animation"
    })
    assert updated.status_code == 200
```

- [ ] **步骤 2：运行测试验证失败**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_api_styles.py -k extension -q`

预期：FAIL；详情缺扩展元数据，创建保护未识别扩展预设或项目默认校验拒绝 ID。

- [ ] **步骤 3：实现 API DTO 与服务端保护**

列表与详情从 StyleService 元数据适配器取得扩展字段；`create_style` 与 `delete_style` 统一调用 `StyleService.is_readonly_preset(style_id)`。预览端点对扩展项返回本地 `preview_asset` 对应文件，文件缺失时返回 404 JSON，不访问远程地址。项目更新继续使用 `get_style_labels()`，使合法扩展 ID 自动通过。

- [ ] **步骤 4：运行 API 回归**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_api_styles.py tests/test_extension_style_service.py tests/test_project_spine_template.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/api/routes/styles.py src/novelvideo/api/routes/projects.py tests/test_api_styles.py
git commit -m "feat: expose readonly extension style presets"
```

### 任务 3：视觉风格页分组与只读详情

**文件：**
- 修改：`frontend/src/types/style.ts`
- 创建：`frontend/src/components/styles/ExtensionStyleDetailPanel.tsx`
- 修改：`frontend/src/routes/_app/projects.$project/styles.tsx`
- 修改：`frontend/src/__tests__/routes/styles.ce.test.tsx`

- [ ] **步骤 1：先保存并核对现有 dirty diff**

运行：`git diff -- frontend/src/routes/_app/projects.$project/styles.tsx frontend/src/__tests__/routes/styles.ce.test.tsx`

预期：记录用户现有页面改动；实现时逐行保留，不使用 checkout、restore 或整文件覆盖。

- [ ] **步骤 2：编写失败的页面行为测试**

```tsx
it("groups extension presets and renders a readonly detail", async () => {
  renderRouteWithStyles([
    builtinStyle("anime"),
    extensionStyle("drama_ext.japanese_cel_animation", "日系赛璐璐"),
    customStyle("ink"),
  ]);
  expect(screen.getByText("默认风格（1）")).toBeInTheDocument();
  expect(screen.getByText("漫剧新增风格（1）")).toBeInTheDocument();
  expect(screen.getByText("自定义风格（1）")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /日系赛璐璐/ }));
  expect(screen.getByText("只读预设")).toBeInTheDocument();
  expect(screen.getByText("适用场景")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /删除|保存|重命名/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "设为项目默认" })).toBeEnabled();
});
```

- [ ] **步骤 3：运行测试验证失败**

运行：`frontend\node_modules\.bin\vitest.cmd run src/__tests__/routes/styles.ce.test.tsx`

预期：FAIL；没有分组标题或扩展详情组件。

- [ ] **步骤 4：实现类型、分组和独立详情组件**

```ts
export interface Style {
  // existing fields unchanged
  preset_group?: "builtin" | "extension";
  summary?: string;
  category?: "2d" | "3d" | "realistic" | "chinese" | "experimental";
  use_cases?: readonly string[];
  preview_asset?: string;
  version?: string;
}
```

页面用 `useMemo` 构造 builtin/extension/custom 三组。扩展项选中时渲染 `ExtensionStyleDetailPanel`；其他项继续渲染现有 `StyleDetailPanel`。详情预览 `onError` 后展示“预览暂不可用”，仍保留“设为项目默认”。

- [ ] **步骤 5：运行页面测试与类型检查**

运行：

```powershell
frontend\node_modules\.bin\vitest.cmd run src/__tests__/routes/styles.ce.test.tsx src/__tests__/features/canvas/extension-style-catalog.test.ts
frontend\node_modules\.bin\tsc.cmd --noEmit
```

预期：全部 PASS。

- [ ] **步骤 6：提交且不夹带其他 dirty 文件**

```powershell
git add frontend/src/types/style.ts frontend/src/components/styles/ExtensionStyleDetailPanel.tsx frontend/src/routes/_app/projects.$project/styles.tsx frontend/src/__tests__/routes/styles.ce.test.tsx
git diff --cached --name-only
git commit -m "feat: show extension presets in visual styles"
```

### 任务 4：项目默认与 ImageGenNode 覆盖去重

**文件：**
- 修改：`frontend/src/features/canvas/extension-styles/composePrompt.ts`
- 修改：`frontend/src/features/canvas/nodes/ImageGenNode.tsx`
- 修改：`frontend/src/__tests__/features/canvas/extension-style-compose.test.ts`
- 修改：`frontend/src/__tests__/features/canvas/image-gen-prompt.test.ts`

- [ ] **步骤 1：编写四种组合的失败测试**

```ts
it.each([
  { project: "drama_ext.japanese_cel_animation", base: null, node: null, count: 0 },
  { project: "drama_ext.japanese_cel_animation", base: null, node: "drama_ext.japanese_cel_animation", count: 0 },
  { project: "drama_ext.japanese_cel_animation", base: null, node: "drama_ext.shojo_manga", count: 1 },
  { project: "anime", base: "anime", node: "drama_ext.shojo_manga", count: 1 },
])("resolves extension exactly once: $project/$node", ({ project, base, node, count }) => {
  const prompt = composeImagePrompt("BASE", node, { projectVisualStyleId: project, baseStyleTemplateId: base });
  expect(prompt.split("medium phrase").length - 1).toBe(count);
});
```

请求级测试必须断言：同 ID 不重复，另一扩展覆盖项目默认，基础风格加节点扩展仍追加一次，并且 base payload/node data 未被修改。

- [ ] **步骤 2：运行测试验证失败**

运行：`frontend\node_modules\.bin\vitest.cmd run src/__tests__/features/canvas/extension-style-compose.test.ts src/__tests__/features/canvas/image-gen-prompt.test.ts`

预期：FAIL；现有 composer 不知道项目默认 ID，会重复追加或无法覆盖。

- [ ] **步骤 3：实现纯解析函数并接入项目查询**

```ts
export function resolveNodeExtensionStyle(
  projectVisualStyleId: string | null | undefined,
  baseStyleTemplateId: string | null | undefined,
  nodeExtensionStyleId: string | null | undefined,
): string | null {
  if (nodeExtensionStyleId) {
    if (nodeExtensionStyleId === projectVisualStyleId || nodeExtensionStyleId === baseStyleTemplateId) return null;
    return nodeExtensionStyleId;
  }
  return null;
}
```

若项目默认是扩展 A、节点显式选择扩展 B，请求必须将有效模板设为 B，而不是让服务端继续应用 A 后再追加 B；因此请求构建器同时返回规范化的 `style.templateId` 与 prompt。ImageGenNode 使用现有项目 ID 调用 `useProject(projectId)`，只读取 `visual_style`，不得写回项目或用户 prompt。

- [ ] **步骤 4：运行画布测试与类型检查**

运行：

```powershell
frontend\node_modules\.bin\vitest.cmd run src/__tests__/features/canvas/extension-style-compose.test.ts src/__tests__/features/canvas/image-gen-prompt.test.ts src/__tests__/features/canvas/extension-style-drawer.test.tsx
frontend\node_modules\.bin\tsc.cmd --noEmit
```

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add frontend/src/features/canvas/extension-styles/composePrompt.ts frontend/src/features/canvas/nodes/ImageGenNode.tsx frontend/src/__tests__/features/canvas/extension-style-compose.test.ts frontend/src/__tests__/features/canvas/image-gen-prompt.test.ts
git commit -m "fix: deduplicate project extension styles"
```

### 任务 5：全链路验证与审查

**文件：**
- 验证以上所有目标文件；除修复已证实回归外不增加新范围。

- [ ] **步骤 1：运行后端聚焦套件**

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_extension_style_catalog.py tests/test_extension_style_service.py tests/test_api_styles.py tests/test_sync_extension_style_catalog.py -q --basetemp .codex-test-temp/extension-style-service-final
\.venv\Scripts\python.exe -m ruff check src/novelvideo/extension_styles src/novelvideo/services/style_service.py src/novelvideo/api/routes/styles.py tests/test_extension_style_service.py tests/test_api_styles.py
```

预期：0 failures，Ruff `All checks passed!`。

- [ ] **步骤 2：运行前端聚焦套件**

```powershell
frontend\node_modules\.bin\vitest.cmd run src/__tests__/routes/styles.ce.test.tsx src/__tests__/features/canvas/extension-style-catalog.test.ts src/__tests__/features/canvas/extension-style-compose.test.ts src/__tests__/features/canvas/extension-style-drawer.test.tsx src/__tests__/features/canvas/image-gen-prompt.test.ts
frontend\node_modules\.bin\tsc.cmd --noEmit
```

预期：0 failures，类型检查退出码 0。

- [ ] **步骤 3：验证目录同步、原预设保护和提交边界**

```powershell
\.venv\Scripts\python.exe scripts/sync_extension_style_catalog.py --check
git diff --check
git diff --exit-code HEAD~4 HEAD -- src/novelvideo/styles/presets
git status --short
```

预期：同步检查与 diff check 退出码 0；原 6 项 presets 无差异；工作区中原有 dirty 文件仍保留但未被任何任务提交夹带。

- [ ] **步骤 4：请求独立规格审查与质量审查**

规格审查逐项核对 24 项列表、18 项只读、项目默认与生成链路；质量审查重点检查 StyleService 缓存、API 写保护、页面可访问性、预览 fallback 和画布双扩展覆盖。

- [ ] **步骤 5：最终运行受影响服务并人工验收**

重启 8780 后端，强制刷新前端；在视觉风格页确认三分组与 18 项详情，选择一项设为项目默认，再从 ImageGenNode 发起一次用户明确授权的生成任务。生成调用可能产生外部费用，未经用户明确授权不得自动执行。
