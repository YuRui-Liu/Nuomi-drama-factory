# 扩展风格目录动态加载实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（- [ ]）语法来跟踪进度。

**目标：** 稳定展示内置 6、扩展 18 和自定义风格，并支持无整机重启的目录重载、诊断和版本化 StyleSnapshot。

**架构：** 使用线程安全、last-known-good 的 ExtensionStyleRegistry 替代永久 _extension_cache。服务端输出明确类型与诊断，项目生产任务继续冻结不可变 StyleSnapshot。

**技术栈：** Python、Pydantic、FastAPI、React、TanStack Query、pytest、Vitest。

---

## 文件结构

- 创建 src/novelvideo/extension_styles/registry.py：动态目录、代际和诊断。
- 修改 services/style_service.py、styles/resolver.py：从一致 catalog generation 解析。
- 修改 api/routes/styles.py：status/reload。
- 修改前端 style 类型、query、风格页和翻译：三组列表和只读扩展详情。

### 任务 1：线程安全 Registry

**文件：**
- 创建：src/novelvideo/extension_styles/registry.py
- 测试：tests/test_extension_style_registry.py

- [ ] **步骤 1：编写失败测试**

~~~~python
def test_invalid_reload_keeps_last_known_good(registry, catalog_path):
    first = registry.snapshot()
    catalog_path.write_text("not-json", encoding="utf-8")
    second = registry.reload()
    assert second.styles == first.styles
    assert second.diagnostics.degraded is True
~~~~

同时覆盖首次加载 18 项、指纹变化、修复恢复和并发只解析一次。

- [ ] **步骤 2：运行测试确认模块不存在。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension_style_registry.py -q
~~~~

- [ ] **步骤 3：实现 CatalogDiagnostics、CatalogSnapshot、ExtensionStyleRegistry，使用 RLock 和 mtime_ns/size/sha256 指纹。**

- [ ] **步骤 4：成功时原子替换；失败时保留 last-known-good；没有旧目录时 fail closed。错误只含相对文件名和安全代码。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension_style_registry.py -q
git add src/novelvideo/extension_styles/registry.py tests/test_extension_style_registry.py
git commit -m "feat(styles): add live extension registry"
~~~~

### 任务 2：StyleService 与快照一致性

**文件：**
- 修改：src/novelvideo/extension_styles/__init__.py
- 修改：src/novelvideo/services/style_service.py
- 修改：src/novelvideo/styles/resolver.py
- 测试：tests/test_style_resolver.py、tests/test_api_styles.py

- [ ] **步骤 1：测试一次 resolve_style_snapshot() 只使用一个 catalog generation，扩展项 type=extension 且 read_only=true。**

- [ ] **步骤 2：用 registry 替换 _extension_cache；clear_cache() 委托 registry.reload(force=True)。**

- [ ] **步骤 3：list_all_styles() 输出稳定 type/group/order/read_only/category/preview_url，保持 6/18/custom 计数。**

- [ ] **步骤 4：StyleSnapshot 使用单条 style payload hash 加 catalog generation；已冻结任务不追随热目录改变。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension_style_registry.py tests/test_extension_style_catalog.py tests/test_style_resolver.py tests/test_api_styles.py -q
git add src/novelvideo/extension_styles src/novelvideo/services/style_service.py src/novelvideo/styles/resolver.py tests
git commit -m "feat(styles): resolve catalog generations"
~~~~

### 任务 3：状态与重载 API

**文件：**
- 修改：src/novelvideo/api/routes/styles.py
- 测试：tests/test_api_styles.py

- [ ] **步骤 1：先写静态路由测试，确认不会被 /styles/{style_id} 截获。**

~~~~text
GET  /styles/catalog-status
POST /styles/catalog-reload
~~~~

- [ ] **步骤 2：将两个静态端点定义在动态详情端点之前；reload 要求 editor/admin 权限。**

- [ ] **步骤 3：响应包含 generation、catalog_hash、discovered、loaded、failed、degraded、last_attempt_at、last_success_at、errors，禁止绝对路径。**

- [ ] **步骤 4：失败 reload 继续返回 last-known-good 和 degraded；修复后再次 reload 清除 degraded。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_styles.py -q
git add src/novelvideo/api/routes/styles.py tests/test_api_styles.py
git commit -m "feat(api): expose style catalog diagnostics"
~~~~

### 任务 4：三组风格前端

**文件：**
- 修改：frontend/src/types/style.ts
- 修改：frontend/src/lib/queries/styles.ts
- 修改：frontend/src/lib/query-keys.ts
- 修改：frontend/src/routes/_app/projects.$project/styles.tsx
- 创建：frontend/src/components/styles/style-catalog-diagnostics.tsx
- 修改：frontend/public/locales/zh/translation.json、en/translation.json
- 测试：frontend/src/__tests__/routes/styles.catalog-runtime.test.tsx

- [ ] **步骤 1：编写失败测试**：分别渲染“内置 6 / 扩展 18 / 自定义”，扩展详情无保存/删除，缺预览显示占位。

- [ ] **步骤 2：Style.type 改为 preset | extension | custom，补 read_only/category/summary/use_cases/source_group/catalog_generation。**

- [ ] **步骤 3：添加 useStyleCatalogStatus() 和 useReloadStyleCatalog()；reload 成功 invalidate list/detail/snapshot。**

- [ ] **步骤 4：页面按 type 分三个 section 并稳定排序；顶部展示发现/加载/失败及重载按钮。**

- [ ] **步骤 5：扩展详情只读但允许“应用到项目”。**

- [ ] **步骤 6：验证并提交。**

~~~~powershell
Set-Location frontend
corepack pnpm test -- src/__tests__/routes/styles.ce.test.tsx src/__tests__/routes/styles.catalog-runtime.test.tsx
corepack pnpm exec tsc -b --pretty false
git add frontend/src frontend/public/locales
git commit -m "feat(styles): group and reload style catalog"
~~~~

### 任务 5：Canvas 一致性与真实重载

- [ ] **步骤 1：保留 Canvas 编译期副本，运行同步检查；UI 目录以 API 为准，不引入第二个动态缓存。**

~~~~powershell
.\.venv\Scripts\python.exe scripts/sync_extension_style_catalog.py --check
~~~~

- [ ] **步骤 2：运行后端与 Ruff。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_extension_style_registry.py tests/test_extension_style_catalog.py tests/test_style_resolver.py tests/test_api_styles.py tests/test_sync_extension_style_catalog.py -q
.\.venv\Scripts\python.exe -m ruff check src/novelvideo/extension_styles src/novelvideo/services/style_service.py src/novelvideo/styles/resolver.py src/novelvideo/api/routes/styles.py
~~~~

- [ ] **步骤 3：运行前端风格测试和 TypeScript。**

- [ ] **步骤 4：启动后端后真测 status→reload→list，确认同一进程显示 18 个扩展风格。**

- [ ] **步骤 5：提交仅包含验证修复的最终变更。**

