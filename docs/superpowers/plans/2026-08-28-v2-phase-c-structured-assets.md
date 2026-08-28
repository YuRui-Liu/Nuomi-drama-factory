# v2 阶段 C：结构化导入与虚拟资产实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新项目默认使用不依赖 Cognee/嵌入的 `structured_v1`，旧项目保持 `cognee_legacy`；同时增加不移动物理文件的虚拟文件夹和用途分类。

**架构：** 项目持久化知识流水线及状态机，结构化导入以稳定分块和一次性原子发布生成分集、角色和场景。资产文件夹是独立 SQLite 元数据，原路径、稳定 ID 和 RunningHub 引用不变。

**技术栈：** Python、FastAPI、Pydantic、SQLite、pytest；React、TypeScript、TanStack Query、Vitest。

---

### 任务 1：知识流水线领域模型

**参考：** `E:\Proj\dramaclaw-main\src\novelvideo\knowledge_pipeline.py`

**文件：**
- 创建：`src/novelvideo/knowledge_pipeline.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 测试：`tests/test_knowledge_pipeline.py`
- 测试：`tests/test_project_dependencies.py`

- [ ] 写红灯：新项目是 `structured_pending`；无字段旧项目解释为 `cognee_legacy`；只有未产生正式资产的 `structured_pending` 可切换旧轨。
- [ ] 运行 `python -m pytest tests/test_knowledge_pipeline.py tests/test_project_dependencies.py -q`，确认 FAIL。
- [ ] 移植常量、类型化错误和迁移解释；以事务更新项目流水线。
- [ ] 重跑确认 PASS，提交 `feat: add dual-track project pipeline`。

### 任务 2：结构化原文导入

**参考：** `E:\Proj\dramaclaw-main\src\novelvideo\structured_ingest.py`

**文件：**
- 创建：`src/novelvideo/structured_ingest.py`
- 修改：`src/novelvideo/episode_import_service.py`
- 修改：`src/novelvideo/task_backend/runners/episode_import.py`
- 测试：`tests/test_structured_ingest.py`

- [ ] 写红灯：结构化导入不调用 `cognee.add/cognify/memify`；相同原文与 schema 重试复用 run；分块版本变更创建新 run。
- [ ] 运行 `python -m pytest tests/test_structured_ingest.py -q`，确认 FAIL。
- [ ] 移植确定性分块、source hash、pipeline/schema version 和进度事件；使用当前 `episode_source_store`。
- [ ] 重跑确认 PASS，提交 `feat: import projects without graph embeddings`。

### 任务 3：分集、角色和场景原子发布

**参考：** `E:\Proj\dramaclaw-main\src\novelvideo\structured_builders.py`和 `story_analysis.py`

**文件：**
- 创建：`src/novelvideo/structured_builders.py`
- 修改：`src/novelvideo/api/routes/episodes.py`
- 修改：`src/novelvideo/task_backend/runners/episode_assets.py`
- 测试：`tests/test_structured_builders.py`
- 测试：`tests/test_api_characters_asset_contract.py`

- [ ] 写红灯：部分角色/场景结果不覆盖上次正式结果；成功时分集、角色和场景在单事务中可见；稳定逻辑 ID 不随重试变化。
- [ ] 运行目标 pytest 确认 FAIL。
- [ ] 适配 NuomiDrama 现有 Episode Graph/叙事组数据；角色包含 face/role/build，场景包含 location/time/environment/spatial anchors/source references。
- [ ] 重跑确认 PASS，提交 `feat: publish structured project assets atomically`。

### 任务 4：默认轨道与失败选择界面

**文件：**
- 修改：`src/novelvideo/api/routes/projects.py`
- 修改：`src/novelvideo/api/routes/ingest.py`
- 修改：`frontend/src/routes/_app/projects.$project/ingest.tsx`
- 修改：`frontend/src/lib/queries/ingest.ts`
- 测试：`frontend/src/__tests__/routes/ingest-settings-save.test.tsx`
- 测试：`frontend/src/__tests__/lib/queries/ingest.test.tsx`

- [ ] 写红灯：新建项目 API 返回 `structured_v1`；结构化失败页同时提供“重试”和“切换知识图谱”；未确认时不切换。
- [ ] 运行后端路由测试和两个目标 Vitest，确认 FAIL。
- [ ] 实现显式切换 API，后端重新校验“尚无正式资产”；禁止自动 fallback。
- [ ] 重跑确认 PASS，提交 `feat: default new projects to structured import`。

### 任务 5：虚拟文件夹与用途存储

**文件：**
- 创建：`src/novelvideo/assets/organization.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 修改：`src/novelvideo/api/routes/assets.py`
- 测试：`tests/test_asset_organization.py`

- [ ] 写红灯：

```python
def test_deleting_folder_unfiles_assets_without_deleting_media(store, asset):
    folder = store.create_folder(project="p", name="分镜")
    store.place_asset(asset.id, folder.id, purpose="storyboard")
    store.delete_folder(folder.id)
    assert store.get_asset(asset.id).path == asset.path
    assert store.get_placement(asset.id).folder_id is None
```

- [ ] 运行 `python -m pytest tests/test_asset_organization.py -q`，确认 FAIL。
- [ ] 新增 `asset_folders` 与 `asset_placements`；限定单资产单文件夹，purpose 为 character/scene/prop/storyboard/video/audio/other；删文件夹只解除归类。
- [ ] 重跑确认 PASS，提交 `feat: add virtual asset folders`。

### 任务 6：资产中心文件夹 UI

**文件：**
- 修改：`frontend/src/lib/queries/assets.ts`
- 修改：`frontend/src/features/freezone/AssetLibraryPanel.tsx`
- 创建：`frontend/src/features/freezone/AssetFolderTree.tsx`
- 测试：`frontend/src/__tests__/features/freezone/asset-folder-tree.test.tsx`

- [ ] 写红灯：建立、重命名、移动、删除文件夹时调用正确 API；删除后资产出现在“未归类”；用途与文件夹可组合筛选。
- [ ] 运行目标 Vitest 确认 FAIL。
- [ ] 实现树与过滤器，不更改拖拽 payload 中的稳定 asset ID/path。
- [ ] 重跑确认 PASS，提交 `feat: organize assets with virtual folders`。

### 任务 7：阶段 C 验收

- [ ] 运行结构化、旧 Cognee、项目迁移、角色、场景和资产组织测试。
- [ ] 运行 `tests/test_task_narrative_group_video_runner.py`、`tests/test_narrative_group_runner_references.py`和全部 RunningHub H3 契约。
- [ ] 运行导入和资产中心前端测试、`ruff`、`tsc -b`、生产构建和 `git diff --check`。
- [ ] 人工验证：新项目默认结构化；失败不自动降级；虚拟文件夹操作前后物理路径不变；RunningHub MiniMax 仍能使用原资产出片。
