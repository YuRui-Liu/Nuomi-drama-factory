# v2 阶段 A：大项目性能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 复用 v2.0.1 的缩略图、聚合查询、按需读取和视口加载，降低大画布与大资产库首屏开销。

**架构：** 后端提供项目隔离的缩略图和轻量资产索引；前端用 `ViewportLazyImage` 按真实可见性请求媒体。上传与普通 JSON 超时策略分离。

**技术栈：** Python、FastAPI、Pillow、SQLite、React、TypeScript、TanStack Query、IntersectionObserver、pytest、Vitest。

---

### 任务 1：项目隔离的缩略图

**参考：** `E:\Proj\dramaclaw-main\src\novelvideo\utils\thumbnails.py`

**文件：**
- 创建：`src/novelvideo/utils/thumbnails.py`
- 修改：`src/novelvideo/freezone/history.py`
- 测试：`tests/test_thumbnail_cache.py`

- [ ] 编写失败测试：

```python
def test_cache_key_separates_projects_and_source_versions(tmp_path):
    a = thumbnail_path(tmp_path, "p1", "asset", "v1", (480, 480))
    b = thumbnail_path(tmp_path, "p2", "asset", "v1", (480, 480))
    c = thumbnail_path(tmp_path, "p1", "asset", "v2", (480, 480))
    assert len({a, b, c}) == 3
    assert a.is_relative_to(tmp_path.resolve())
```

- [ ] 运行 `python -m pytest tests/test_thumbnail_cache.py -q`，确认因模块缺失而 FAIL。
- [ ] 移植缓存键、Pillow `thumbnail()`、临时文件原子替换和根目录越界校验；GET 只返回已预热缩略图，不同步解码原图。
- [ ] 重跑确认 PASS，提交 `perf: add project-scoped thumbnail cache`。

### 任务 2：轻量索引与聚合引用

**文件：**
- 修改：`src/novelvideo/api/routes/assets.py`
- 修改：`frontend/src/lib/queries/asset-references.ts`
- 测试：`tests/test_api_assets.py`
- 测试：`frontend/src/__tests__/lib/queries/asset-references.test.tsx`

- [ ] 写红灯，要求 `GET /projects/{project}/assets?view=index` 只返回 `id/name/media_type/thumbnail_url/thumbnail_status`，且 `/assets/references` 一次返回 `asset_id -> usages[]`。
- [ ] 运行：

```powershell
python -m pytest tests/test_api_assets.py -q
Set-Location frontend
& .\node_modules\.bin\vitest.CMD run src/__tests__/lib/queries/asset-references.test.tsx
```

- [ ] 实现轻量投影与聚合端点；详情 hook 仅在展开卡片时 `enabled: true`。
- [ ] 重跑确认 PASS，提交 `perf: aggregate asset references`。

### 任务 3：视口图片与资产库接入

**参考：** `E:\Proj\dramaclaw-main\frontend\src\components\viewport-lazy-image.tsx`

**文件：**
- 创建：`frontend/src/components/viewport-lazy-image.tsx`
- 修改：`frontend/src/features/freezone/AssetLibraryPanel.tsx`
- 修改：`frontend/src/features/canvas/ui/CanvasHistoryAssetsModal.tsx`
- 测试：`frontend/src/__tests__/components/viewport-lazy-image.test.tsx`

- [ ] 写红灯：

```typescript
render(<ViewportLazyImage src="/full.webp" alt="asset" />);
expect(screen.getByRole("img")).not.toHaveAttribute("src");
intersectionCallback([{ isIntersecting: true }]);
expect(screen.getByRole("img")).toHaveAttribute("src", "/full.webp");
```

- [ ] 运行目标 Vitest，确认 FAIL。
- [ ] 实现 `rootMargin="320px"`、`eager` 例外、加载后不回收；卡片只请求 `thumbnail_url`，缺失时显示占位。
- [ ] 重跑确认 PASS，提交 `perf: lazy load visible asset media`。

### 任务 4：慢上传策略

**文件：**
- 修改：`frontend/src/api/ops.ts`
- 测试：`frontend/src/__tests__/api/ops-upload.test.ts`

- [ ] 写红灯，断言 `uploadFreezoneImage` 与 `uploadFreezoneVideo` 默认向 ky 传递 `{ timeout: false, signal }`，且不重试非幂等 POST。
- [ ] 运行 `vitest run src/__tests__/api/ops-upload.test.ts`，确认 `uploadFreezoneImage` 仍继承普通请求超时而 FAIL。
- [ ] 将媒体上传的默认 `timeout` 改为 `false`，保留显式 `timeoutMs`、用户主动取消和服务器大小限制。
- [ ] 重跑确认 PASS，提交 `fix: allow slow media uploads`。

### 任务 5：阶段 A 验收

- [ ] 运行新增后端测试与 `tests/test_freezone_asset_library_backend.py`。
- [ ] 运行新增前端测试与 `frontend/src/__tests__/performance/navigation-resource-loading.test.tsx`。
- [ ] 运行 `tsc -b`、生产构建和 `git diff --check`。
- [ ] 只修复本阶段新增的失败；已有基线错误单独记录。
