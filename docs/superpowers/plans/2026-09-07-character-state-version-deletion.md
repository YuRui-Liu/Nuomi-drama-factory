# 人物身份图历史版本删除实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 允许用户删除任意人物身份图版本，并在删除当前版本时自动采用最新剩余版本。

**架构：** 在 `ProductionWorkflowStore` 增加纯状态删除能力，由 production-assets DELETE 路由在项目锁内协调 workflow 快照、版本文件和 canonical 文件。前端复用 production asset slot 查询，新增删除 mutation与确认框。

**技术栈：** FastAPI、Pydantic、JSON sidecar store、React、TanStack Query、Vitest、Testing Library。

---

## 文件结构

- `src/novelvideo/production_workflow/store.py`：删除版本并选择最新回退版本。
- `tests/production_workflow/test_legacy_migration_store.py`：Store 删除与回退规则。
- `src/novelvideo/api/routes/production_assets.py`：安全删除文件、canonical 切换与失败回滚。
- `tests/test_api_production_assets.py`：DELETE API 文件系统行为与错误响应。
- `frontend/src/lib/queries/production-assets.ts`：删除 mutation 和查询失效。
- `frontend/src/components/assets/character-state-versions.tsx`：卡片删除按钮和确认对话框。
- `frontend/src/__tests__/components/assets/character-state-versions.test.tsx`：删除交互测试。
- `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`：删除文案。

### 任务 1：Store 删除和自动回退

- [ ] **步骤 1：编写失败测试**

在 `tests/production_workflow/test_legacy_migration_store.py` 构造三个带 `created_at` 的版本，调用：

```python
slot, versions, deleted, fallback = store.delete_version(
    slot_id=slot.slot_id,
    version_id="v-current",
)
assert deleted.version_id == "v-current"
assert fallback.version_id == "v-newest"
assert slot.current_version_id == "v-newest"
assert "v-current" not in versions
```

另测删除非当前版本保持 current，以及删除最后版本得到 `current_version_id is None`。

- [ ] **步骤 2：验证红灯**

运行：`uv run pytest -q tests/production_workflow/test_legacy_migration_store.py -k delete_version`

预期：FAIL，`ProductionWorkflowStore` 没有 `delete_version`。

- [ ] **步骤 3：最少实现**

实现 `delete_version(slot_id, version_id)`：校验 slot/version，按 `created_at` 和原 `version_ids` 顺序选择最新剩余版本，移除记录；删除当前版本时更新 adoption status，保存并返回 `(slot, versions, deleted, fallback)`。

- [ ] **步骤 4：验证绿灯**

运行同一步骤 2，预期全部 PASS。

### 任务 2：DELETE API 与文件事务

- [ ] **步骤 1：编写失败测试**

在 `tests/test_api_production_assets.py` 创建 current/older/newer 三个版本文件和 canonical 文件，请求：

```python
response = client.delete(
    f"/api/v1/projects/demo/production-assets/slots/{slot_id}/versions/current"
)
assert response.status_code == 200
assert response.json()["data"]["slot"]["current_version_id"] == "newer"
assert canonical.read_bytes() == newer.read_bytes()
assert not current.exists()
```

另测删除最后版本清空 canonical、404、路径越界和 `os.replace`/文件删除失败后的 workflow 与 canonical 回滚。

- [ ] **步骤 2：验证红灯**

运行：`uv run pytest -q tests/test_api_production_assets.py -k delete_version`

预期：FAIL，DELETE 路由返回 405。

- [ ] **步骤 3：最少实现**

新增 DELETE 路由。进入项目锁后捕获 workflow 与涉及文件的快照；调用 Store 删除；若删除当前版本，则复制 fallback 到 canonical，或在无 fallback 时清空 canonical；原子保存成功后删除无人引用的版本文件。异常时恢复 workflow 和所有文件快照。

- [ ] **步骤 4：验证绿灯**

运行同一步骤 2，预期全部 PASS。

### 任务 3：前端删除交互

- [ ] **步骤 1：编写失败测试**

在组件测试中 mock `useDeleteProductionAssetVersion`，断言每张卡片存在带版本 ID 的删除按钮；点击 current 版本后显示自动回退提示，确认调用：

```ts
expect(deleteMock).toHaveBeenCalledWith({ versionId: "state-v1" });
```

取消确认不得调用 mutation。

- [ ] **步骤 2：验证红灯**

运行：`pnpm --dir frontend test -- --run src/__tests__/components/assets/character-state-versions.test.tsx`

预期：FAIL，删除 hook 和按钮不存在。

- [ ] **步骤 3：最少实现**

新增删除 mutation；卡片设为相对定位并在右上角放置 `Trash2` 图标按钮；用 AlertDialog 展示普通删除或当前版本自动回退说明；成功 toast，失败 toast；增加中英文文案。

- [ ] **步骤 4：验证绿灯和构建**

运行：

```bash
pnpm --dir frontend test -- --run src/__tests__/components/assets/character-state-versions.test.tsx
pnpm --dir frontend build
```

预期：测试与构建均成功。

### 任务 4：完整验证与提交

- [ ] 运行 `uv run pytest -q tests/production_workflow/test_legacy_migration_store.py tests/test_api_production_assets.py`。
- [ ] 运行相关前端测试与 TypeScript 构建。
- [ ] 运行 Ruff 和 `git diff --check`。
- [ ] 仅提交本计划列出的文件，避免夹带工作区已有修改。
