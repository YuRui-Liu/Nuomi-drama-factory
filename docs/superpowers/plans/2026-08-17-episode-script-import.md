# 分集剧本追加与批量导入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度，所有生产代码必须遵循 RED → GREEN → REFACTOR。

**目标：** 在保留现有第一集、图谱和下游资产的前提下，支持分集剧本单集追加、批量预检、冲突覆盖/跳过、强一致图谱更新及旧项目迁移。

**架构：** 新增独立 `episode_sources` 领域与存储层，分集原文是唯一真相源，`novel.txt` 是确定性兼容产物。导入采用 preview/commit 两阶段合同与项目 revision CAS；新增集走图谱增量更新，覆盖集在影子运行时完成全量构建后才切换正式内容。前端新增独立批量导入对话框，不复用会污染 durable chapters 状态的旧 `preview_only` 缓存。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite/aiosqlite、Cognee、React 19、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构与职责

- 创建 `src/novelvideo/episode_sources.py`：集号识别、领域 DTO、冲突决策、哈希和 canonical 原文合成。
- 创建 `src/novelvideo/episode_source_store.py`：SQLite manifest、preview staging、revision CAS、原子文件切换和旧项目迁移。
- 创建 `src/novelvideo/episode_import_service.py`：preview/commit 编排、候选版本、图谱增量/影子重建与补偿。
- 创建 `src/novelvideo/api/routes/episode_imports.py`：preview、commit、list API。
- 创建 `src/novelvideo/task_backend/runners/episode_import.py`：标准项目任务 runner。
- 修改 `src/novelvideo/sqlite_store.py`：创建 episode source 表，并让 `load_episode_content` 优先读取 source、兼容旧 raw_content。
- 修改 `src/novelvideo/cognee/store.py`：提供对明确 runtime/state 目录工作的导入入口，不再用 prune-active 实现覆盖。
- 修改 `src/novelvideo/api/__init__.py`、`src/novelvideo/task_backend/runners/__init__.py`、`src/novelvideo/task_identity.py`：注册新路由和任务类型。
- 创建 `frontend/src/types/episode-import.ts`：前端 preview、resolution、commit、list DTO。
- 修改 `frontend/src/lib/queries/ingest.ts`、`frontend/src/lib/query-keys.ts`：新增三类 API hooks 和缓存失效。
- 创建 `frontend/src/components/ingest/EpisodeImportDialog.tsx`：文件队列、预检、手填集号、冲突决策和提交结果。
- 修改 `frontend/src/routes/_app/projects.$project/ingest.tsx`：始终显示追加单集/批量导入入口。
- 修改 `frontend/public/locales/{zh,en}/translation.json`：新增交互和错误文案。

## 动态工作流 DAG

```text
第 1 层（并行）
  A. 领域模型与纯函数
  B. 影子图谱可切换运行时调查/合同
  C. 前端查询合同与 DTO

第 2 层（并行）
  D. SQLite repository、迁移和 revision CAS（依赖 A）
  E. 前端批量导入对话框（依赖 C）

第 3 层
  F. preview/commit service、API 和 runner（依赖 A、B、D）

第 4 层
  G. 页面接入、stale 展示和端到端合同（依赖 E、F）

第 5 层
  H. 规格审查 → 代码质量审查 → 全量聚焦回归
```

### 任务 1：分集来源领域模型

**文件：**
- 创建：`src/novelvideo/episode_sources.py`
- 测试：`tests/test_episode_sources.py`

- [ ] **步骤 1：编写集号识别与冲突决策失败测试**

```python
def test_body_episode_number_wins_over_filename():
    item = inspect_episode_source("E03.md", "第2集 地下追逐\n正文")
    assert item.episode_number == 2
    assert item.number_source == "body"
    assert item.warnings == ("正文集号 2 与文件名集号 3 不一致",)

def test_unresolved_conflict_blocks_commit():
    with pytest.raises(EpisodeImportValidationError, match="冲突集号尚未处理"):
        validate_resolutions(items=[source_item(2)], existing={2: 4}, resolutions={})
```

- [ ] **步骤 2：运行 RED**

运行：`python -m pytest tests/test_episode_sources.py -q`

预期：因 `novelvideo.episode_sources` 不存在而失败。

- [ ] **步骤 3：实现最小纯领域 API**

```python
@dataclass(frozen=True, slots=True)
class EpisodeSourceCandidate:
    file_id: str
    filename: str
    title: str
    content: str
    content_hash: str
    episode_number: int | None
    number_source: Literal["body", "filename", "manual"] | None
    warnings: tuple[str, ...] = ()

def compose_canonical_novel(sources: Iterable[EpisodeSource]) -> str:
    return "\n\n".join(item.content.strip() for item in sorted(sources, key=lambda x: x.episode_number)) + "\n"
```

实现正文优先、文件名兜底、手动集号校验、批内重复、`overwrite|skip` 决策和 SHA-256。

- [ ] **步骤 4：运行 GREEN 并提交**

运行：`python -m pytest tests/test_episode_sources.py -q`

提交：`git commit -am "feat: add episode source domain model"`

### 任务 2：分集来源 repository、revision CAS 与迁移

**文件：**
- 创建：`src/novelvideo/episode_source_store.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 修改：`src/novelvideo/models.py`
- 测试：`tests/test_episode_source_store.py`
- 测试：`tests/test_episode_source_migration.py`

- [ ] **步骤 1：编写事务与迁移失败测试**

```python
async def test_commit_rejects_stale_project_revision_without_writes(store):
    preview = await store.save_preview(base_revision=3, items=[candidate(2)])
    await store.bump_revision_for_test()
    with pytest.raises(EpisodeSourceRevisionConflict):
        await store.commit_preview(preview.id, expected_revision=3, resolutions={})
    assert await store.list_sources() == []

async def test_overwrite_preserves_downstream_episode_fields(store):
    await seed_planned_episode(store, number=1, adapted_content="已改写", beats_json="[]")
    await store.upsert_sources([source(1, "新版")], expected_revision=1)
    episode = await store.get_episode(1)
    assert episode.adapted_content == "已改写"
```

- [ ] **步骤 2：运行 RED**

运行：`python -m pytest tests/test_episode_source_store.py tests/test_episode_source_migration.py -q`

- [ ] **步骤 3：添加 schema 与 repository**

新增：

```sql
CREATE TABLE IF NOT EXISTS episode_sources (
  episode_number INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  raw_content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_revision INTEGER NOT NULL,
  downstream_stale INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_source_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  project_revision INTEGER NOT NULL,
  migrated_at TEXT
);
CREATE TABLE IF NOT EXISTS episode_import_previews (
  preview_id TEXT PRIMARY KEY,
  base_revision INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
```

所有 commit 使用 `BEGIN IMMEDIATE`，先比较 project revision，再写 source 与 `episodes.raw_content` 兼容镜像；不得调用会覆盖规划字段的 `add_episodes()`。

- [ ] **步骤 4：实现无损 lazy migration**

使用 `ChapterDetector.detect()` 的 `is_fallback`：可靠多集拆分；单集迁为第 1 集；fallback 返回 `confirmation_required` 且零写入；第二次调用保持幂等。

- [ ] **步骤 5：运行 GREEN 并提交**

运行：`python -m pytest tests/test_episode_source_store.py tests/test_episode_source_migration.py tests/test_sqlite_store.py -q`

提交：`git commit -am "feat: persist versioned episode sources"`

### 任务 3：候选内容与影子图谱事务

**文件：**
- 创建：`src/novelvideo/episode_import_service.py`
- 修改：`src/novelvideo/cognee/store.py`
- 测试：`tests/test_episode_import_transaction.py`
- 测试：`tests/test_cognee_shadow_rebuild.py`

- [ ] **步骤 1：编写失败保留活动版本的 RED 测试**

```python
async def test_overwrite_graph_failure_keeps_active_content(tmp_path, service, graph):
    seed_active_version(tmp_path, revision=4, novel="第一集旧版", graph_marker="active-v4")
    graph.fail_on_memify = True
    with pytest.raises(RuntimeError, match="memify"):
        await service.commit(overwrite_batch(episode=1, content="第一集新版"))
    assert active_revision(tmp_path) == 4
    assert (tmp_path / "novel.txt").read_text("utf-8") == "第一集旧版"
    assert active_graph_marker(tmp_path) == "active-v4"
```

- [ ] **步骤 2：运行 RED**

运行：`python -m pytest tests/test_episode_import_transaction.py tests/test_cognee_shadow_rebuild.py -q`

- [ ] **步骤 3：实现候选版本编排**

新增集：候选 source/novel → Cognee 增量成功 → repository CAS 切换。

覆盖集：在 `state/cognee_builds/<target_revision>/` 初始化独立 Cognee runtime，使用全部 canonical 原文执行 add/cognify/memify；成功后切换 active pointer，再提交 source/novel。切换失败必须恢复原 pointer；禁止调用 `_prune_cognee_only()` 删除活动图谱。

- [ ] **步骤 4：实现原子 canonical 文件**

写入 `novel.txt.<uuid>.tmp`，flush 后用 `os.replace`；失败时删除临时文件。候选 graph 与候选文件均成功前不得更新 active revision。

- [ ] **步骤 5：运行 GREEN 并提交**

运行：`python -m pytest tests/test_episode_import_transaction.py tests/test_cognee_shadow_rebuild.py tests/test_cognee_ingest_failure_contract.py -q`

提交：`git commit -am "feat: add atomic episode import graph transaction"`

### 任务 4：两阶段 API 与任务 runner

**文件：**
- 创建：`src/novelvideo/api/routes/episode_imports.py`
- 创建：`src/novelvideo/task_backend/runners/episode_import.py`
- 修改：`src/novelvideo/api/schemas.py`
- 修改：`src/novelvideo/api/__init__.py`
- 修改：`src/novelvideo/task_backend/runners/__init__.py`
- 修改：`src/novelvideo/task_identity.py`
- 测试：`tests/test_api_episode_import_preview.py`
- 测试：`tests/test_api_episode_import_commit.py`
- 测试：`tests/test_task_episode_import_runner.py`

- [ ] **步骤 1：编写 API 合同 RED 测试**

```python
def test_preview_multiple_files_does_not_mutate_project(client, project_dir):
    response = client.post(url("/episode-imports/preview"), files=[
        ("files", ("E02.md", b"# Episode 2\nB", "text/markdown")),
        ("files", ("E03.md", b"# Episode 3\nC", "text/markdown")),
    ])
    assert response.status_code == 200
    assert [x["episode_number"] for x in response.json()["data"]["files"]] == [2, 3]
    assert not (project_dir / "episode_sources.json").exists()

def test_commit_requires_resolution_for_existing_episode(client):
    response = client.post(url("/episode-imports/commit"), json=commit_body(resolutions=[]))
    assert response.status_code == 409
    assert response.json()["code"] == "EPISODE_IMPORT_CONFLICT_UNRESOLVED"
```

- [ ] **步骤 2：运行 RED**

运行：`python -m pytest tests/test_api_episode_import_preview.py tests/test_api_episode_import_commit.py -q`

- [ ] **步骤 3：实现路由与稳定错误码**

preview 接受 `files: list[UploadFile]`，沿用文件名、扩展名和大小安全限制。commit body：

```python
class EpisodeImportCommit(BaseModel):
    preview_id: str
    expected_revision: int
    resolutions: list[EpisodeImportResolution]
```

409 分别返回 `EPISODE_IMPORT_PREVIEW_STALE`、`EPISODE_IMPORT_REVISION_CONFLICT`、`EPISODE_IMPORT_CONFLICT_UNRESOLVED`。

- [ ] **步骤 4：注册标准 runner**

任务类型固定为 `episode_import_commit`，payload 保存 preview snapshot、expected/target revision 和 resolutions；runner 启动时再次 CAS，使用 `EpisodeImportService.commit()`，结果包含逐集 `added|overwritten|skipped|failed`。

- [ ] **步骤 5：运行 GREEN 并提交**

运行：`python -m pytest tests/test_api_episode_import_preview.py tests/test_api_episode_import_commit.py tests/test_task_episode_import_runner.py -q`

提交：`git commit -am "feat: add two-phase episode import API"`

### 任务 5：前端查询合同与缓存

**文件：**
- 创建：`frontend/src/types/episode-import.ts`
- 修改：`frontend/src/lib/queries/ingest.ts`
- 修改：`frontend/src/lib/query-keys.ts`
- 测试：`frontend/src/__tests__/lib/queries/episode-imports.test.tsx`

- [ ] **步骤 1：编写 FormData、commit 和失效 RED 测试**

```tsx
it("sends every selected file in one preview request", async () => {
  await preview.mutateAsync([file("E02.md"), file("E03.md")]);
  expect(receivedFormData.getAll("files")).toHaveLength(2);
});

it("commits explicit overwrite and skip resolutions", async () => {
  await commit.mutateAsync({preview_id:"p1", expected_revision:4, resolutions:[
    {file_id:"a", episode_number:2, action:"overwrite"},
    {file_id:"b", episode_number:3, action:"skip"},
  ]});
  expect(receivedJson.resolutions).toHaveLength(2);
});
```

- [ ] **步骤 2：运行 RED**

运行：`corepack pnpm exec vitest run src/__tests__/lib/queries/episode-imports.test.tsx`

- [ ] **步骤 3：实现 DTO、query key 和 hooks**

增加 `usePreviewEpisodeImports`、`useCommitEpisodeImport`、`useEpisodeImports`。commit 成功失效 episodeImports、chapters、episodes、knowledgeGraph、tasks 和 pipelineStatus；不得写入 `chapters.preview_only`。

- [ ] **步骤 4：运行 GREEN 并提交**

运行：`corepack pnpm exec vitest run src/__tests__/lib/queries/episode-imports.test.tsx`

提交：`git commit -am "feat: add episode import query contracts"`

### 任务 6：批量导入对话框

**文件：**
- 创建：`frontend/src/components/ingest/EpisodeImportDialog.tsx`
- 测试：`frontend/src/__tests__/components/ingest/episode-import-dialog.test.tsx`

- [ ] **步骤 1：编写交互 RED 测试**

覆盖：多文件显示、未识别手填、冲突未决禁用提交、覆盖/跳过互斥、批量覆盖/跳过、解析失败保留其他文件、全部 skip no-op、409 预检过期文案。

```tsx
expect(screen.getByRole("button", {name:"确认导入"})).toBeDisabled();
await user.click(screen.getByRole("radio", {name:/第 2 集.*覆盖/}));
expect(screen.getByRole("button", {name:"确认导入"})).toBeEnabled();
```

- [ ] **步骤 2：运行 RED**

运行：`corepack pnpm exec vitest run src/__tests__/components/ingest/episode-import-dialog.test.tsx`

- [ ] **步骤 3：实现 reducer 驱动对话框**

组件本地 reducer 维护 files、manual numbers、resolutions 和 result；不新增 Zustand。`onCommitted(result)` 只在服务端接受任务后调用。

- [ ] **步骤 4：运行 GREEN 并提交**

运行：`corepack pnpm exec vitest run src/__tests__/components/ingest/episode-import-dialog.test.tsx`

提交：`git commit -am "feat: add batch episode import dialog"`

### 任务 7：导入页接入、stale 状态和兼容回归

**文件：**
- 修改：`frontend/src/routes/_app/projects.$project/ingest.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`src/novelvideo/api/routes/episodes.py`
- 测试：`frontend/src/__tests__/routes/ingest-settings-save.test.tsx`
- 测试：`frontend/src/__tests__/i18n/locales-json.test.ts`
- 测试：`tests/test_episode_source_staleness.py`

- [ ] **步骤 1：编写页面与 stale RED 测试**

断言已有 chapters 时仍显示“追加单集”和“批量导入”；覆盖完成后显示“剧本已更新，部分资产待同步”；首次导入旧 `ingest_fast` 行为保持不变。

- [ ] **步骤 2：运行 RED**

运行：`corepack pnpm exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/i18n/locales-json.test.ts`

- [ ] **步骤 3：接入页面与分集状态**

页面只负责 dialog 开关、任务中心跳转和成功刷新。episodes/list DTO 返回 source_revision 与 downstream_stale；覆盖不删除 adapted content、beats 或媒体索引。

- [ ] **步骤 4：运行前后端 GREEN**

运行：

```text
python -m pytest tests/test_episode_source_staleness.py tests/contract/test_m03_episodes_scripts_content.py -q
corepack pnpm exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/i18n/locales-json.test.ts
```

- [ ] **步骤 5：提交**

提交：`git commit -am "feat: expose incremental episode import workflow"`

### 任务 8：规格审查与集成验证

**文件：**
- 审查全部本分支改动，不添加无关功能。

- [ ] **步骤 1：规格合规审查**

逐条对照设计文档 15 项验收标准，特别验证：旧图谱失败保留、skip 零写入、旧项目迁移幂等、正文号优先、覆盖不删资产。

- [ ] **步骤 2：代码质量审查**

检查 secret/路径泄漏、preview 暂存清理、文件锁、事务边界、跨进程 runner、错误码、前端缓存失效和组件可访问性。

- [ ] **步骤 3：运行后端聚焦回归**

```text
python -m pytest tests/test_episode_sources.py tests/test_episode_source_store.py tests/test_episode_source_migration.py tests/test_episode_import_transaction.py tests/test_cognee_shadow_rebuild.py tests/test_api_episode_import_preview.py tests/test_api_episode_import_commit.py tests/test_task_episode_import_runner.py tests/test_episode_source_staleness.py tests/test_cognee_ingest_failure_contract.py -q
python -m ruff check src/novelvideo/episode_sources.py src/novelvideo/episode_source_store.py src/novelvideo/episode_import_service.py src/novelvideo/api/routes/episode_imports.py src/novelvideo/task_backend/runners/episode_import.py tests/test_episode_*.py
```

- [ ] **步骤 4：运行前端聚焦回归与类型检查**

```text
corepack pnpm exec vitest run src/__tests__/lib/queries/episode-imports.test.tsx src/__tests__/components/ingest/episode-import-dialog.test.tsx src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/i18n/locales-json.test.ts
corepack pnpm exec tsc -b --pretty false
```

- [ ] **步骤 5：检查工作树并提交审查修复**

运行：`git diff --check && git status --short`

如审查产生修复，提交：`git commit -am "fix: complete episode import integration"`
