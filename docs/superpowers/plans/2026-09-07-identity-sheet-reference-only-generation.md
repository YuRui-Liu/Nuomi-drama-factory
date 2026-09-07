# Identity Sheet Reference-Only Generation 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让人物头像只作为身份参考生成完整 Identity Sheet，消除头像覆盖和身体硬切，并为 QC 技术故障提供安全的人工采用入口。

**架构：** 图像模型继续一次返回完整 3:2 三栏画布，后端不再裁切或粘贴任何面板。视觉 QC 增加遮脸、越栏和身体裁切缺陷，并保留脱敏技术诊断。生产资产采用接口仅对纯 `qc_unavailable` 候选开放显式人工确认，其他 QC 失败仍强制阻断。

**技术栈：** Python 3.12、Pydantic、FastAPI、Pillow、pytest、React、TypeScript、TanStack Query、Vitest/Testing Library。

---

## 文件职责

- 修改 `src/novelvideo/character_visual/identity_sheet.py`：加强完整画布生成契约，提供不改像素的候选落盘函数。
- 修改 `src/novelvideo/generators/nanobanana_character.py`：同步生成入口停止头像像素覆盖。
- 修改 `src/novelvideo/task_backend/runners/character_image.py`：异步任务保存 provider 原图并记录 `provider_canvas` 元数据。
- 修改 `src/novelvideo/api/routes/characters.py`：同步生成路径采用相同的原图落盘逻辑和元数据。
- 修改 `src/novelvideo/character_visual/identity_sheet_qc.py`：新增稳定缺陷码并保留脱敏 QC 异常。
- 修改 `src/novelvideo/production_workflow/adoption.py`、`store.py`：仅允许显式采用纯 `qc_unavailable` 候选。
- 修改 `src/novelvideo/api/routes/production_assets.py`：接收并传递 `confirm_qc_unavailable`。
- 修改 `frontend/src/lib/queries/production-assets.ts`：扩展采用请求类型。
- 修改 `frontend/src/components/assets/character-state-versions.tsx`：QC 不可用候选显示警告确认，其余失败继续禁用。
- 修改中英文翻译与对应 Python/React 测试。

### 任务 1：停止破坏性拼接

**文件：**
- 修改：`tests/character_visual/test_identity_sheet.py`
- 修改：`tests/test_character_state_assets.py`
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`src/novelvideo/generators/nanobanana_character.py`
- 修改：`src/novelvideo/task_backend/runners/character_image.py`
- 修改：`src/novelvideo/api/routes/characters.py`

- [ ] **步骤 1：编写失败测试**

增加断言：生成提示包含 `identity reference only`、`no hand-to-face gesture`、`must not cross panel boundaries`；PNG 输出保持 provider 原始字节，JPEG/WebP 输出仅规范化为同尺寸 PNG；同步和异步生成元数据包含：

```python
assert metadata["composition_mode"] == "provider_canvas"
assert metadata["face_source_usage"] == "reference_only"
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
.venv/bin/python -m pytest tests/character_visual/test_identity_sheet.py tests/test_character_state_assets.py -q
```

预期：FAIL，现有代码仍调用 `compose_identity_sheet_v2` 并覆盖左半画布。

- [ ] **步骤 3：最小实现**

在 `identity_sheet.py` 增加只做格式规范化、不裁切或缩放内容的原子保存函数。PNG 保持原始字节，其他 Pillow 可解码格式规范化为同尺寸 PNG，并在发布前验证完整 3:2 画布：

```python
def save_provider_identity_sheet(candidate_path: str | Path, output_path: str | Path) -> Path:
    # copy to same-directory temp -> validate/normalize -> atomic replace
    ...
```

生成提示明确头像仅为 reference，禁止遮脸、越栏和裁切。同步、异步路径改用该函数，并记录 `composition_mode` 与 `face_source_usage`。

- [ ] **步骤 4：运行绿灯测试并提交**

运行上面的 pytest 命令，预期全部 PASS。

提交：

```bash
git add src/novelvideo/character_visual/identity_sheet.py src/novelvideo/generators/nanobanana_character.py src/novelvideo/task_backend/runners/character_image.py src/novelvideo/api/routes/characters.py tests/character_visual/test_identity_sheet.py tests/test_character_state_assets.py
git commit -m "fix: keep identity sheet provider canvas intact"
```

### 任务 2：扩展视觉 QC 与可诊断性

**文件：**
- 修改：`tests/character_visual/test_identity_sheet_qc.py`
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`src/novelvideo/character_visual/identity_sheet_qc.py`

- [ ] **步骤 1：编写失败测试**

验证 QC 要求并解析全部新字段：

```python
assert report.checks["portrait_face_occluded"] is True
assert report.checks["panel_boundary_intrusion"] is False
assert report.checks["body_cropped"] is False
```

模拟 gateway 抛出 `ValueError("普通文本模型 API key 未配置")`，断言：

```python
assert report.issues == ["qc_unavailable"]
assert report.technical_error == "ValueError"
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
.venv/bin/python -m pytest tests/character_visual/test_identity_sheet_qc.py -q
```

预期：FAIL，新字段和诊断尚不存在。

- [ ] **步骤 3：最小实现**

将三个缺陷码加入 `_ISSUE_CODES` 和提示；为 `IdentitySheetQualityReport` 增加可选 `technical_error`。异常仅保存经过白名单校验和长度限制的异常类型名，不读取异常消息、请求头、API Key、响应体或异常实例属性。

- [ ] **步骤 4：运行绿灯测试并提交**

运行上述 pytest，预期全部 PASS。

提交：

```bash
git add src/novelvideo/character_visual/identity_sheet.py src/novelvideo/character_visual/identity_sheet_qc.py tests/character_visual/test_identity_sheet_qc.py
git commit -m "fix: report identity sheet qc failures precisely"
```

### 任务 3：为纯 QC 不可用候选增加人工采用

**文件：**
- 修改：`tests/production_workflow/test_adoption_policy.py`
- 修改：`tests/test_api_production_assets.py`
- 修改：`src/novelvideo/production_workflow/adoption.py`
- 修改：`src/novelvideo/production_workflow/store.py`
- 修改：`src/novelvideo/api/routes/production_assets.py`

- [ ] **步骤 1：编写失败测试**

覆盖三条规则：未确认的 `qc_unavailable` 被拒绝；显式确认且问题集合严格等于 `{qc_unavailable}` 时允许；任何真实视觉问题即使确认也拒绝。

```python
slot, versions, event = adopt_version(
    slot, versions,
    version_id="candidate",
    actor="tester",
    reason="人工核验画面完整",
    at=now,
    confirm_qc_unavailable=True,
)
assert versions["candidate"].adoption_status.value == "adopted"
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
.venv/bin/python -m pytest tests/production_workflow/test_adoption_policy.py tests/test_api_production_assets.py -q
```

预期：FAIL，请求字段和受控例外尚不存在。

- [ ] **步骤 3：最小实现**

为采用函数和 API 增加默认关闭的 `confirm_qc_unavailable: bool = False`。只有 `soft_issues` 去重后严格等于 `{"qc_unavailable"}`、没有 `technical_error` 且理由非空时允许人工采用。

- [ ] **步骤 4：运行绿灯测试并提交**

运行上述 pytest，预期全部 PASS。

提交：

```bash
git add src/novelvideo/production_workflow/adoption.py src/novelvideo/production_workflow/store.py src/novelvideo/api/routes/production_assets.py tests/production_workflow/test_adoption_policy.py tests/test_api_production_assets.py
git commit -m "feat: confirm adoption when visual qc is unavailable"
```

### 任务 4：前端确认交互

**文件：**
- 修改：`frontend/src/__tests__/components/assets/character-state-versions.test.tsx`
- 修改：`frontend/src/__tests__/lib/queries/production-assets.test.tsx`
- 修改：`frontend/src/components/assets/character-state-versions.tsx`
- 修改：`frontend/src/lib/queries/production-assets.ts`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

- [ ] **步骤 1：编写失败测试**

测试纯 `qc_unavailable` 候选按钮可点击，点击后出现警告确认；确认后请求包含：

```ts
{
  versionId: "candidate-qc-unavailable",
  reason: "人物身份卡人工核验后采用（自动 QC 不可用）",
  confirmQcUnavailable: true,
}
```

包含 `body_cropped` 或其他真实视觉错误的候选按钮仍禁用。

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-state-versions.test.tsx src/__tests__/lib/queries/production-assets.test.tsx
```

预期：FAIL，QC 不可用候选当前不可点击。

- [ ] **步骤 3：最小实现**

复用现有 AlertDialog 组件。仅为纯 `qc_unavailable` 显示“人工核验后采用”；对话框列出“头像无遮挡、正身完整、背身完整”三项核验提示。普通 QC 通过候选保持一键采用。

- [ ] **步骤 4：运行绿灯测试并提交**

运行上述 Vitest，预期全部 PASS。

提交：

```bash
git add frontend/src/components/assets/character-state-versions.tsx frontend/src/lib/queries/production-assets.ts frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json frontend/src/__tests__/components/assets/character-state-versions.test.tsx frontend/src/__tests__/lib/queries/production-assets.test.tsx
git commit -m "feat: confirm identity sheet adoption without qc"
```

### 任务 5：回归验证

**文件：** 无新增文件。

- [ ] **步骤 1：运行后端目标测试**

```bash
.venv/bin/python -m pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py tests/test_character_state_assets.py tests/production_workflow/test_adoption_policy.py tests/test_api_production_assets.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端目标测试和类型检查**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-state-versions.test.tsx src/__tests__/lib/queries/production-assets.test.tsx
pnpm --dir frontend exec tsc --noEmit
```

预期：测试全部 PASS，TypeScript 退出码 0。

- [ ] **步骤 3：检查变更边界**

```bash
git diff --check
git status --short
```

预期：无空白错误；用户已有的无关改动仍被保留。
