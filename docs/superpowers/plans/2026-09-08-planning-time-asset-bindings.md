# 规划期资产绑定与生成前引用选择实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在身份、场景和道具规划阶段持久化规范引用绑定，让生成弹窗只选择这些绑定或临时上传，并提供清晰、可取消、可访问的选中反馈。

**架构：** SQLite 保存按集数划分的 `PlannedReferenceBinding`，绑定指向集中定义的生产资产槽位，而不是显示名称或文件路径。规划器原子发布实体、变体和绑定；生成预览通过槽位解析当前版本并返回引用修订值，提交端重新校验并冻结快照。前端使用统一的 `selectedBindingIds` 和临时上传状态，移除生成阶段的项目资产追加、归类和变体创建入口。

**技术栈：** Python 3.12、Pydantic、aiosqlite、FastAPI、React、TypeScript、TanStack Query、Vitest、Testing Library、pytest。

---

## 文件职责

新增后端文件：

- `src/novelvideo/narrative_groups/planned_bindings.py`：绑定模型、稳定 ID 和规划状态。
- `src/novelvideo/narrative_groups/planned_binding_service.py`：规划投影、槽位版本解析、预览修订和快照冻结。
- `src/novelvideo/production_workflow/slot_ids.py`：角色、场景和道具槽位 ID 的唯一构造入口。

修改后端文件：

- `src/novelvideo/sqlite_store.py`：绑定表、查询、替换和规划原子发布接口。
- `src/novelvideo/agents/identity_planner.py`：先构造身份规划草稿，再原子发布。
- `src/novelvideo/agents/asset_compiler.py`：先构造场景/道具规划草稿，再原子发布。
- `src/novelvideo/task_backend/runners/identity.py`：将活动导演方案范围投影为身份绑定。
- `src/novelvideo/task_backend/runners/episode_assets.py`：将场景/道具规划结果投影为绑定。
- `src/novelvideo/task_backend/runners/character_image.py`、`src/novelvideo/api/routes/characters.py`：复用角色槽位 ID factory。
- `src/novelvideo/task_backend/runners/scene_reference.py`：复用场景槽位 ID factory。
- `src/novelvideo/task_backend/runners/prop_reference.py`：复用道具槽位 ID factory。
- `src/novelvideo/api/schemas.py`：生成选择收敛为绑定 ID、临时上传 ID 和引用修订。
- `src/novelvideo/api/routes/narrative_groups.py`：预览和提交切换到持久化绑定，上传强制临时。
- `src/novelvideo/narrative_groups/reference_decisions.py`：快照记录绑定、槽位、版本和摘要。
- `src/novelvideo/narrative_groups/reference_matching.py`：移除生成期场景变体创建副作用。

新增前端文件：

- `frontend/src/components/episode/narrative-workbench/planned-reference-picker.tsx`：正式规划引用的分组选择器。
- `frontend/src/components/episode/narrative-workbench/temporary-reference-picker.tsx`：仅本次生效的上传列表。

修改/删除前端文件：

- `frontend/src/lib/queries/narrative-groups.ts`：新预览/提交类型和纯临时上传请求。
- `frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`：统一选择状态、计数和提交。
- `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：处理过期绑定刷新和规划入口。
- `frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`：提供返回规划页的导航回调。
- 删除 `frontend/src/components/episode/narrative-workbench/free-reference-picker.tsx`。
- 删除 `frontend/src/components/episode/narrative-workbench/reference-resolution-dialog.tsx`。
- 删除 `frontend/src/components/episode/narrative-workbench/matched-reference-section.tsx`。
- 删除 `frontend/src/components/episode/narrative-workbench/unresolved-reference-section.tsx`。

测试文件在各任务中逐一列出。当前这些前端和路由文件存在其他未提交修改，执行者必须按 hunk 编辑与提交，不能整文件覆盖或回退用户改动。

## 依赖与并行顺序

1. 先完成任务 1，锁定持久化模型和槽位契约。
2. 任务 2 与前端任务 6 可在任务 1 完成后并行。
3. 任务 2 完成后，任务 3 与任务 5 可并行；任务 3 完成后执行任务 4，避免两个工作者同时修改 `sqlite_store.py`。
4. 任务 7 依赖任务 5 和 6，并可与任务 4 并行。
5. 任务 8 在全部功能任务合入后执行。

### 任务 1：绑定模型、槽位 ID 与 SQLite 存储

**文件：**
- 创建：`src/novelvideo/narrative_groups/planned_bindings.py`
- 创建：`src/novelvideo/production_workflow/slot_ids.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 修改：`src/novelvideo/task_backend/runners/character_image.py`
- 修改：`src/novelvideo/api/routes/characters.py`
- 修改：`src/novelvideo/task_backend/runners/scene_reference.py`
- 修改：`src/novelvideo/task_backend/runners/prop_reference.py`
- 创建：`tests/test_planned_reference_bindings.py`
- 创建：`tests/production_workflow/test_slot_ids.py`
- 修改：`tests/test_character_state_assets.py`
- 修改：`tests/test_scene_reference_runner.py`
- 修改：`tests/test_prop_reference_runner.py`

- [ ] **步骤 1：为模型、稳定 ID、槽位 ID 和 Store 写失败测试**

在 `tests/test_planned_reference_bindings.py` 覆盖同一规范实体重新规划后 `binding_id` 不变、范围可以更新、按叙事组过滤以及替换失败回滚：

```python
binding = PlannedReferenceBinding.create(
    project_id="p1",
    episode_number=1,
    source_plan_revision_id="director-r3",
    asset_kind="scene_variant",
    entity_id="scene-state-01",
    base_entity_id="scene-01",
    variant_id="rain-night",
    asset_slot_id="scene:scene-01:state:scene-state-01:master",
    group_ids=("group-01",),
    beat_ids=("beat-07", "beat-08"),
    shot_ids=("shot-07",),
    required=True,
    status="ready",
    resolution="auto_matched",
    display_label="谢家碑坊 · 暴雨天井",
)
await store.replace_planned_reference_bindings_atomic(
    1, asset_kinds={"scene_base", "scene_variant"}, bindings=[binding]
)
assert await store.list_planned_reference_bindings(1, group_id="group-01") == [binding]
```

在 `tests/production_workflow/test_slot_ids.py` 参数化锁定现有规则：

```python
assert character_state_slot_id("石九", "石九_青年时期") == "character:石九:state:石九_青年时期"
assert scene_base_slot_id("谢家碑坊", "master") == "scene:谢家碑坊:base:master"
assert scene_state_slot_id("谢家碑坊", "暴雨天井", "master") == "scene:谢家碑坊:state:暴雨天井:master"
assert prop_reference_slot_id("深灰功德碑") == "prop:深灰功德碑:reference"
```

- [ ] **步骤 2：运行测试确认红灯**

运行：

```bash
.venv/bin/python -m pytest tests/test_planned_reference_bindings.py tests/production_workflow/test_slot_ids.py -q
```

预期：FAIL，模块和 Store 接口尚不存在。

- [ ] **步骤 3：实现最小模型和槽位 factory**

`planned_bindings.py` 定义冻结模型并只使用规范字段生成 ID：

```python
ReferenceKind = Literal["character_identity", "scene_base", "scene_variant", "prop"]
BindingStatus = Literal["ready", "pending_confirmation", "missing_asset", "missing_image"]
BindingResolution = Literal["auto_matched", "manually_confirmed", "explicit_fallback"]

class PlannedReferenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding_id: str
    project_id: str
    episode_number: int = Field(gt=0)
    source_plan_revision_id: str
    asset_kind: ReferenceKind
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""
    asset_slot_id: str
    group_ids: tuple[str, ...] = ()
    beat_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()
    required: bool = True
    status: BindingStatus
    resolution: BindingResolution
    display_label: str

    @classmethod
    def create(cls, **fields: Any) -> "PlannedReferenceBinding":
        identity = {
            key: fields[key]
            for key in ("project_id", "episode_number", "asset_kind", "entity_id",
                        "base_entity_id", "variant_id", "asset_slot_id")
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        binding_id = f"planned-ref-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"
        return cls(binding_id=binding_id, **fields)
```

同时导入 `Any`、`hashlib` 和 `json`。`binding_id` 的哈希输入故意不包含 revision、显示标签和叙事范围，保证同一规范实体重新规划后 ID 不漂移。`slot_ids.py` 暴露四个纯函数并拒绝空字段。不要在 factory 中读取文件或数据库。

- [ ] **步骤 4：新增 SQLite 表和 Store 接口**

在 `SQLITE_SCHEMA_SQL` 新增单表；范围数组使用 JSON，查询时反序列化后按 `group_id` 过滤：

```sql
CREATE TABLE IF NOT EXISTS planned_reference_bindings (
    binding_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    episode_number INTEGER NOT NULL,
    source_plan_revision_id TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    base_entity_id TEXT NOT NULL DEFAULT '',
    variant_id TEXT NOT NULL DEFAULT '',
    asset_slot_id TEXT NOT NULL,
    group_ids_json TEXT NOT NULL DEFAULT '[]',
    beat_ids_json TEXT NOT NULL DEFAULT '[]',
    shot_ids_json TEXT NOT NULL DEFAULT '[]',
    required INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    resolution TEXT NOT NULL,
    display_label TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_planned_refs_episode
ON planned_reference_bindings(episode_number, asset_kind);
```

实现三个 Store 接口：`list_planned_reference_bindings(self, episode_number, *, group_id=None)` 返回反序列化模型并可按 `group_ids` 过滤；`get_planned_reference_bindings(self, episode_number, binding_ids)` 保持请求 ID 顺序且拒绝重复 ID；`replace_planned_reference_bindings_atomic(self, episode_number, *, asset_kinds, bindings)` 负责整类替换。

实现体必须使用 `BEGIN IMMEDIATE`，先删除本集对应 `asset_kind` 的旧行，再插入新行；任何异常通过 `asyncio.shield(db.rollback())` 回滚。

- [ ] **步骤 5：把现有生产槽位调用点收敛到 factory**

修改 `character_image.py`、角色 API、`scene_reference.py` 和 `prop_reference.py`，删除其中手写的 `f"character:..."`、`f"scene:..."`、`f"prop:..."` 槽位构造，统一调用 `slot_ids.py`。扩充现有 runner/API 测试，证明改造前后的槽位 ID 完全一致；不要修改仅用于提示词或 usage scope 的字符串。

- [ ] **步骤 6：运行测试并提交**

运行：

```bash
.venv/bin/python -m pytest tests/test_planned_reference_bindings.py tests/production_workflow/test_slot_ids.py tests/test_character_state_assets.py tests/test_scene_reference_runner.py tests/test_prop_reference_runner.py -q
.venv/bin/ruff check src/novelvideo/narrative_groups/planned_bindings.py src/novelvideo/production_workflow/slot_ids.py src/novelvideo/task_backend/runners/character_image.py src/novelvideo/api/routes/characters.py src/novelvideo/task_backend/runners/scene_reference.py src/novelvideo/task_backend/runners/prop_reference.py tests/test_planned_reference_bindings.py tests/production_workflow/test_slot_ids.py
```

预期：全部 PASS。

提交：

```bash
git add src/novelvideo/narrative_groups/planned_bindings.py src/novelvideo/production_workflow/slot_ids.py src/novelvideo/sqlite_store.py src/novelvideo/task_backend/runners/character_image.py src/novelvideo/api/routes/characters.py src/novelvideo/task_backend/runners/scene_reference.py src/novelvideo/task_backend/runners/prop_reference.py tests/test_planned_reference_bindings.py tests/production_workflow/test_slot_ids.py tests/test_character_state_assets.py tests/test_scene_reference_runner.py tests/test_prop_reference_runner.py
git commit -m "feat: persist planned reference bindings"
```

### 任务 2：从导演方案生成无副作用的规范绑定

**文件：**
- 创建：`src/novelvideo/narrative_groups/planned_binding_service.py`
- 修改：`src/novelvideo/narrative_groups/reference_requirements.py`
- 创建：`tests/test_planned_reference_binding_service.py`
- 修改：`tests/test_narrative_group_reference_requirements.py`

- [ ] **步骤 1：写精确匹配和零副作用失败测试**

构造包含角色身份、基础场景、场景状态和道具的 DirectorPlan fixture，断言：

```python
bindings = bindings_for_director_plan(
    project_id="p1",
    episode_number=1,
    source_plan_revision_id="director-r3",
    groups=plan.groups,
    shots=plan.shots,
    characters=characters,
    scenes=scenes,
    props=props,
)
assert {(item.asset_kind, item.status) for item in bindings} == {
    ("character_identity", "ready"),
    ("scene_base", "ready"),
    ("scene_variant", "ready"),
    ("prop", "ready"),
}
assert bindings_by_kind(bindings, "scene_variant")[0].variant_id == "rain-night"
```

另写用例证明只有拼接名称、多个同名候选或缺失结构化变体字段时返回 `pending_confirmation`，且传入集合没有被修改。

- [ ] **步骤 2：运行测试确认红灯**

```bash
.venv/bin/python -m pytest tests/test_planned_reference_binding_service.py tests/test_narrative_group_reference_requirements.py -q
```

预期：FAIL，`bindings_for_director_plan` 尚不存在，现有逻辑仍会解析拼接名称。

- [ ] **步骤 3：实现纯投影服务**

实现 `bindings_for_director_plan(*, project_id, episode_number, source_plan_revision_id, groups, shots, characters, scenes, props) -> tuple[PlannedReferenceBinding, ...]`。

只读取 `asset_requirements.kind/entity_key/visible_change/required` 和场景的 `base_scene_id/variant_id`。`scene_state` 必须通过结构化字段精确找到场景变体；不得调用 `parse_scene_requirement`、`ensure_draft_scene_variant` 或任何 Store 写方法。`group_ids`、`beat_ids`、`shot_ids` 从 DirectorPlan 的组和镜头关系聚合，输出按首次出现顺序稳定排序。

- [ ] **步骤 4：运行测试并提交**

```bash
.venv/bin/python -m pytest tests/test_planned_reference_binding_service.py tests/test_narrative_group_reference_requirements.py -q
.venv/bin/ruff check src/novelvideo/narrative_groups/planned_binding_service.py src/novelvideo/narrative_groups/reference_requirements.py tests/test_planned_reference_binding_service.py
git add src/novelvideo/narrative_groups/planned_binding_service.py src/novelvideo/narrative_groups/reference_requirements.py tests/test_planned_reference_binding_service.py tests/test_narrative_group_reference_requirements.py
git commit -m "feat: project director assets into stable bindings"
```

预期：测试和 Ruff 全部通过。

### 任务 3：身份规划原子发布身份与绑定

**依赖：** 任务 1、2。可与任务 5、6 并行。

**文件：**
- 修改：`src/novelvideo/agents/identity_planner.py`
- 修改：`src/novelvideo/task_backend/runners/identity.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 创建：`tests/test_task_identity_planner_bindings.py`
- 修改：`tests/test_identity_planner_character_promotion.py`

- [ ] **步骤 1：写规划成功和回滚失败测试**

成功用例断言 runner 完成后，本集身份和绑定同时可见：

```python
result = await _run_identity_planner(envelope, ctx)
bindings = await sqlite_store.list_planned_reference_bindings(1)
assert result["binding_count"] == len(bindings)
assert {item.asset_kind for item in bindings} == {"character_identity"}
assert all(item.asset_slot_id.startswith("character:") for item in bindings)
```

回滚用例 monkeypatch `_insert_planned_binding_row` 在写入绑定时抛出 `RuntimeError`，断言身份 JSON、episode identity map 和旧绑定均保持调用前值。

- [ ] **步骤 2：运行测试确认红灯**

```bash
.venv/bin/python -m pytest tests/test_task_identity_planner_bindings.py tests/test_identity_planner_character_promotion.py -q
```

预期：FAIL，身份规划仍逐项写入且没有绑定发布。

- [ ] **步骤 3：将身份规划拆成草稿与发布**

新增冻结结果：

```python
class IdentityPlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    new_count: int
    resolved_count: int
    characters: tuple[NovelCharacter, ...]
    episode_identity_ids: tuple[str, ...]
    identity_default_map: dict[str, str]
```

`IdentityPlanner.plan_single_episode` 在内存副本上完成解析并返回 `IdentityPlanDraft`，不调用 `add_character` 或 `update_episode`。在 `SQLiteStore.publish_identity_plan_atomic` 中用一个 `BEGIN IMMEDIATE` 写 characters、episode identity 字段和 `character_identity` bindings；异常整体 rollback，再刷新内存 graph state。

- [ ] **步骤 4：runner 生成并发布绑定**

runner 加载活动 DirectorPlan，使用完整的项目、集数、revision、groups、shots、characters、scenes 和 props 参数调用 `bindings_for_director_plan`，并只取 `character_identity` 结果，再调用：

```python
await sqlite_store.publish_identity_plan_atomic(
    episode_number=episode,
    characters=draft.characters,
    episode_identity_ids=draft.episode_identity_ids,
    identity_default_map=draft.identity_default_map,
    bindings=identity_bindings,
)
```

结果增加 `binding_count` 和按状态聚合的 `binding_statuses`。

- [ ] **步骤 5：运行测试并提交**

```bash
.venv/bin/python -m pytest tests/test_task_identity_planner_bindings.py tests/test_identity_planner_character_promotion.py tests/test_identity_planner_appearance_validation.py -q
.venv/bin/ruff check src/novelvideo/agents/identity_planner.py src/novelvideo/task_backend/runners/identity.py tests/test_task_identity_planner_bindings.py
git add src/novelvideo/agents/identity_planner.py src/novelvideo/task_backend/runners/identity.py src/novelvideo/sqlite_store.py tests/test_task_identity_planner_bindings.py tests/test_identity_planner_character_promotion.py
git commit -m "feat: publish identity bindings during planning"
```

预期：全部通过，失败规划不留下半成品身份关系。

### 任务 4：场景和道具规划原子发布变体与绑定

**依赖：** 任务 1、2、3。可与任务 5、6、7 并行。

**文件：**
- 修改：`src/novelvideo/agents/asset_compiler.py`
- 修改：`src/novelvideo/task_backend/runners/episode_assets.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 创建：`tests/test_task_episode_asset_bindings.py`
- 修改：`tests/test_asset_compiler_scene_enrichment.py`
- 修改：`tests/test_asset_compiler_prop_planning.py`

- [ ] **步骤 1：写场景变体、道具绑定和回滚测试**

```python
scene_result = await _run_episode_asset_planner(scene_envelope, ctx)
scene_bindings = await store.list_planned_reference_bindings(1)
assert scene_result["binding_count"] == len(scene_bindings)
assert any(item.asset_kind == "scene_variant" and item.variant_id for item in scene_bindings)

prop_result = await _run_episode_asset_planner(prop_envelope, ctx)
prop_bindings = await store.list_planned_reference_bindings(1)
assert any(item.asset_kind == "prop" for item in prop_bindings)
```

断言场景实体的 `name`、`base_scene_id`、`variant_id` 来自规划产物，生成绑定时没有创建 `f"{base}_{variant}"` 名称。用绑定插入失败验证 scene/prop menu、实体和旧绑定整体回滚。

- [ ] **步骤 2：运行测试确认红灯**

```bash
.venv/bin/python -m pytest tests/test_task_episode_asset_bindings.py tests/test_asset_compiler_scene_enrichment.py tests/test_asset_compiler_prop_planning.py -q
```

预期：FAIL，当前 compiler 在内部提前写入实体/menu，runner 没有绑定。

- [ ] **步骤 3：让 compiler 返回未发布草稿**

定义：

```python
class ScenePlanDraft(BaseModel):
    scenes: tuple[NovelScene, ...]
    scene_menu: tuple[SceneMenuItem, ...]
    new_count: int

class PropPlanDraft(BaseModel):
    props: tuple[NovelProp, ...]
    prop_menu: tuple[PropMenuItem, ...]
```

`compile_episode_scenes` 和 `compile_episode_props` 只构造草稿；移除其中的 `_persist_scene_plan_atomic` 和 `update_episode` 调用。runner 根据 DirectorPlan 投影相应绑定。

- [ ] **步骤 4：新增两个原子发布接口并接入 runner**

```python
await sqlite_store.publish_scene_plan_atomic(
    episode_number=episode,
    scenes=scene_draft.scenes,
    scene_menu=scene_draft.scene_menu,
    bindings=scene_bindings,
)
await sqlite_store.publish_prop_plan_atomic(
    episode_number=episode,
    props=prop_draft.props,
    prop_menu=prop_draft.prop_menu,
    bindings=prop_bindings,
)
```

每个接口都在一个 `BEGIN IMMEDIATE` 中 upsert 实体、更新 episode menu、替换对应 binding kind 后 commit。Cognee 图同步只能在 SQLite commit 成功后执行，失败时保留 outbox 供现有恢复机制处理。

- [ ] **步骤 5：运行测试并提交**

```bash
.venv/bin/python -m pytest tests/test_task_episode_asset_bindings.py tests/test_asset_compiler_scene_enrichment.py tests/test_asset_compiler_prop_planning.py -q
.venv/bin/ruff check src/novelvideo/agents/asset_compiler.py src/novelvideo/task_backend/runners/episode_assets.py tests/test_task_episode_asset_bindings.py
git add src/novelvideo/agents/asset_compiler.py src/novelvideo/task_backend/runners/episode_assets.py src/novelvideo/sqlite_store.py tests/test_task_episode_asset_bindings.py tests/test_asset_compiler_scene_enrichment.py tests/test_asset_compiler_prop_planning.py
git commit -m "feat: publish scene and prop bindings during planning"
```

### 任务 5：生成预览、当前版本解析与冻结快照

**依赖：** 任务 1、2。可与任务 3、4 并行。

**文件：**
- 修改：`src/novelvideo/narrative_groups/planned_binding_service.py`
- 修改：`src/novelvideo/narrative_groups/reference_decisions.py`
- 修改：`src/novelvideo/api/schemas.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 创建：`tests/test_planned_reference_binding_resolver.py`
- 修改：`tests/test_api_narrative_groups.py`
- 修改：`tests/test_narrative_group_reference_decisions.py`
- 修改：`tests/test_narrative_group_reference_uploads.py`

- [ ] **步骤 1：写只读预览、越权、过期版本和快照测试**

测试必须覆盖：

```python
before_db = database_path.read_bytes()
before_workflow = workflow_path.read_bytes()
preview = await resolve_planned_reference_preview(
    store, workflow_store,
    project_id="p1", episode_number=1, group_id="group-01",
    project_dir=project_dir,
)
assert database_path.read_bytes() == before_db
assert workflow_path.read_bytes() == before_workflow
assert preview.bindings[0].selected_by_default is True
assert preview.reference_revision
```

提交测试发送其他项目/集数/叙事组的 `binding_id` 必须 422；预览后更换 current version，再用旧 `reference_revision` 提交必须返回 409 和 `STALE_REFERENCE_BINDING`；快照包含 `binding_id/asset_slot_id/version_id/sha256`。

- [ ] **步骤 2：运行测试确认红灯**

```bash
.venv/bin/python -m pytest tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py tests/test_narrative_group_reference_decisions.py tests/test_narrative_group_reference_uploads.py -q
```

预期：FAIL，预览仍重新匹配名称并可能创建场景变体，请求仍接受 decisions 和 additional assets。

- [ ] **步骤 3：实现预览与当前版本解析**

定义响应模型：

```python
class ResolvedPlannedReference(BaseModel):
    binding_id: str
    asset_kind: ReferenceKind
    display_label: str
    variant_id: str = ""
    beat_ids: tuple[str, ...] = ()
    required: bool
    status: BindingStatus
    selected_by_default: bool
    asset_slot_id: str
    version_id: str = ""
    adoption_status: str = ""
    thumbnail_url: str = ""
    warning: str = ""

class PlannedReferencePreview(BaseModel):
    reference_revision: str
    bindings: tuple[ResolvedPlannedReference, ...]
    max_images: int
```

通过 `ProductionWorkflowStore.get_slot(asset_slot_id)` 读取 `current_version_id`。接受 `provisional` 或 `adopted` current version；校验 slot/version 一致、路径位于项目根内、文件存在且 Pillow 可识别。不要扫描 canonical 文件名作为静默回退。

- [ ] **步骤 4：收敛请求和冻结快照**

将 `NarrativeReferenceResolutionRequest` 改为：

```python
class NarrativeReferenceResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_binding_ids: list[str] = Field(default_factory=list)
    upload_ids: list[str] = Field(default_factory=list)
    reference_revision: str = Field(min_length=1)
```

`build_planned_reference_snapshot` 重新解析所有绑定并比较 revision。冻结项必须记录 `binding_id`、`asset_slot_id`、`version_id`、安全相对路径、SHA-256、asset kind 和适用范围。删除对 `style_asset_id`、`additional_asset_ids` 和生成期 decision action 的依赖；项目风格仍由 `use_style` 控制。

- [ ] **步骤 5：上传端强制临时且 API 返回结构化错误**

`upload_group_reference` 只接受 `file`；调用 `save_reference_upload` 时固定传入 `persist=False`。删除 Form 中的 `persist/requirement_id/asset_kind/target_entity_id/base_entity_id/variant_id`。没有本集绑定时 preview 返回：

```json
{"detail":{"code":"PLANNED_REFERENCES_REQUIRED","message":"请先重新规划本集身份、场景和道具引用"}}
```

必需绑定未解决返回 `UNRESOLVED_PLANNED_REFERENCE`；revision 不一致返回 `STALE_REFERENCE_BINDING`。

- [ ] **步骤 6：运行测试并提交**

```bash
.venv/bin/python -m pytest tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py tests/test_narrative_group_reference_decisions.py tests/test_narrative_group_reference_uploads.py -q
.venv/bin/ruff check src/novelvideo/narrative_groups/planned_binding_service.py src/novelvideo/narrative_groups/reference_decisions.py src/novelvideo/api/schemas.py src/novelvideo/api/routes/narrative_groups.py tests/test_planned_reference_binding_resolver.py
git add src/novelvideo/narrative_groups/planned_binding_service.py src/novelvideo/narrative_groups/reference_decisions.py src/novelvideo/api/schemas.py src/novelvideo/api/routes/narrative_groups.py tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py tests/test_narrative_group_reference_decisions.py tests/test_narrative_group_reference_uploads.py
git commit -m "feat: generate from planned reference bindings"
```

### 任务 6：前端引用契约与正式绑定选择器

**依赖：** 任务 1 的字段契约。可与任务 2–5 并行。

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 创建：`frontend/src/components/episode/narrative-workbench/planned-reference-picker.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/planned-reference-picker.test.tsx`
- 修改：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`

- [ ] **步骤 1：写类型映射、整卡选择和上限失败测试**

使用 ready、pending 和 missing fixtures，覆盖：ready 默认全选；整卡鼠标、Enter、Space 和复选框均可切换；`aria-pressed`、高对比 class 和“已选择/未选择”文字同步；全选/清空不越过上限。

```tsx
expect(screen.getByRole("button", { name: /石九 · 青年时期/ })).toHaveAttribute("aria-pressed", "true");
fireEvent.click(screen.getByRole("button", { name: /石九 · 青年时期/ }));
expect(onChange).toHaveBeenLastCalledWith([]);
expect(screen.getByText("已选 4 张 / 上限 8 张")).toBeVisible();
```

查询测试断言 JSON 只包含 `selected_binding_ids`、`upload_ids`、`reference_revision` 和生成配置。

- [ ] **步骤 2：运行测试确认红灯**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/episode/narrative-workbench/planned-reference-picker.test.tsx src/__tests__/lib/queries/narrative-groups.test.ts
```

预期：FAIL，新组件和请求字段尚不存在。

- [ ] **步骤 3：更新 TypeScript 契约**

```typescript
export type PlannedReferenceStatus =
  | "ready" | "pending_confirmation" | "missing_asset" | "missing_image";

export interface PlannedReferenceBinding {
  binding_id: string;
  asset_kind: "character_identity" | "scene_base" | "scene_variant" | "prop";
  display_label: string;
  variant_id?: string;
  beat_ids: string[];
  required: boolean;
  status: PlannedReferenceStatus;
  selected_by_default: boolean;
  version_id?: string;
  adoption_status?: "provisional" | "adopted" | "candidate";
  thumbnail_url?: string;
  warning?: string;
}

export interface NarrativeGroupReferencePreview {
  reference_revision: string;
  bindings: PlannedReferenceBinding[];
  max_images: number;
}

export interface NarrativeGroupGenerationSelection {
  selectedBindingIds: string[];
  uploadIds: string[];
  referenceRevision: string;
  useStyle: boolean;
  providerId?: string;
  model?: string;
  imageSize?: NarrativeImageSize;
  allowUnconstrained?: boolean;
  saveAsProjectDefault?: boolean;
}
```

- [ ] **步骤 4：实现 `PlannedReferencePicker`**

组件 props 固定为：

```typescript
interface PlannedReferencePickerProps {
  bindings: PlannedReferenceBinding[];
  selectedIds: string[];
  maxImages: number;
  temporaryCount: number;
  onChange: (ids: string[]) => void;
  onResolvePlanning: () => void;
}
```

按角色、场景/变体、道具顺序分组。每张卡为 `<button type="button" aria-pressed={selected}>`，使用清晰边框、背景、勾选图标和状态文字；不能只用颜色。不可用项 `disabled` 并显示 warning。必需不可用项在组件顶部显示“返回规划处理”。

- [ ] **步骤 5：运行测试、类型检查并提交**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/episode/narrative-workbench/planned-reference-picker.test.tsx src/__tests__/lib/queries/narrative-groups.test.ts
pnpm --dir frontend exec tsc --noEmit --pretty false
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/components/episode/narrative-workbench/planned-reference-picker.tsx frontend/src/__tests__/components/episode/narrative-workbench/planned-reference-picker.test.tsx frontend/src/__tests__/lib/queries/narrative-groups.test.ts
git commit -m "feat: select planned narrative references"
```

### 任务 7：临时上传、弹窗和工作台集成

**依赖：** 任务 5、6。

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/temporary-reference-picker.tsx`
- 删除：`frontend/src/components/episode/narrative-workbench/free-reference-picker.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/temporary-reference-picker.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`
- 删除：`frontend/src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx`

- [ ] **步骤 1：写临时上传和弹窗状态失败测试**

覆盖上传后缩略图、原文件名、“仅本次使用”、移除；关闭再打开为空；达到上限禁用上传，取消正式选择后恢复。断言弹窗不再出现“保存到项目资产”“资产类型”“目标实体”“基础场景”“变体 ID”或项目资产候选按钮。

```tsx
expect(screen.queryByText("保存到项目资产")).not.toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "移除 雨幕构图参考.png" }));
expect(screen.queryByText("雨幕构图参考.png")).not.toBeInTheDocument();
```

工作台测试模拟 409 `STALE_REFERENCE_BINDING`，断言弹窗保持打开并调用 `referencesQuery.refetch()`。

- [ ] **步骤 2：运行测试确认红灯**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/episode/narrative-workbench/temporary-reference-picker.test.tsx src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：FAIL，现有 picker 仍能追加正式资产且不能移除。

- [ ] **步骤 3：实现纯临时上传组件**

```typescript
interface TemporaryReferencePickerProps {
  uploads: NarrativeReferenceUpload[];
  disabled: boolean;
  uploading: boolean;
  onUpload: (file: File) => Promise<void>;
  onRemove: (uploadId: string) => void;
}
```

上传 hook 只发送 `file`；组件不接收 candidates 或 persist 字段。临时项使用 `upload_id` 作为 key，显示 API URL 缩略图和移除按钮。

- [ ] **步骤 4：重构弹窗为单一选择状态**

`GroupReferenceDialog` 只保存：

```typescript
const [selectedBindingIds, setSelectedBindingIds] = useState<string[]>([]);
const [temporaryUploads, setTemporaryUploads] = useState<NarrativeReferenceUpload[]>([]);
```

打开或 preview revision 变化时，从 `selected_by_default` 初始化正式选择并清空临时项。总数为两个数组长度之和。提交时构造 `selectedBindingIds/uploadIds/referenceRevision`。移除 `freeAssetIds`、`referenceResolution.decisions` 和 `onCreateProp`。

- [ ] **步骤 5：接入过期刷新与规划导航**

`NarrativeGroupWorkbench` 捕获结构化错误 code：

```typescript
if (apiErrorCode(error) === "STALE_REFERENCE_BINDING") {
  await referencesQuery.refetch();
  toast.error("资产版本已变化，请重新确认本次引用");
  return;
}
```

将 `onResolvePlanning` 从 beats route 传入 workbench，再传给 dialog/picker；回调关闭弹窗并导航到 `/projects/${project}/episodes`，使用户回到已有“规划身份/场景/道具”入口。

- [ ] **步骤 6：运行测试、类型检查并提交**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/episode/narrative-workbench/temporary-reference-picker.test.tsx src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
pnpm --dir frontend exec tsc --noEmit --pretty false
git add frontend/src/components/episode/narrative-workbench/temporary-reference-picker.tsx frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx 'frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx' frontend/src/__tests__/components/episode/narrative-workbench/temporary-reference-picker.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git add -u frontend/src/components/episode/narrative-workbench/free-reference-picker.tsx frontend/src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx
git commit -m "feat: clarify narrative reference selection"
```

### 任务 8：退役生成期匹配并完成兼容回归

**依赖：** 任务 3–7。

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/narrative_groups/reference_matching.py`
- 修改：`src/novelvideo/narrative_groups/reference_decisions.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py`
- 删除：`frontend/src/components/episode/narrative-workbench/reference-resolution-dialog.tsx`
- 删除：`frontend/src/components/episode/narrative-workbench/matched-reference-section.tsx`
- 删除：`frontend/src/components/episode/narrative-workbench/unresolved-reference-section.tsx`
- 修改：`tests/test_narrative_group_reference_matching.py`
- 修改：`tests/test_narrative_group_runner_references.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：写生成阶段零写入和旧项目门禁测试**

monkeypatch 所有 scene/prop/character 写方法在 preview 和 generate 中抛错，确认正常绑定流程不会触发它们。另在 preview 前后比较数据库和 production workflow sidecar 字节完全一致。

没有绑定的旧项目必须返回 409 `PLANNED_REFERENCES_REQUIRED`，不能走 `resolve_group_reference_preview` 或 `match_reference_requirements`；历史任务中已有的 `narrative-reference-decision/v1` 快照仍能被 runner 读取。

- [ ] **步骤 2：运行测试确认红灯**

```bash
.venv/bin/python -m pytest tests/test_narrative_group_reference_matching.py tests/test_narrative_group_runner_references.py tests/test_api_narrative_groups.py -q
```

预期：FAIL，legacy fallback、候选 API 和生成期 draft variant 仍存在。

- [ ] **步骤 3：删除生成期副作用入口**

删除/退役：

- `_active_reference_requirements` 的生成时重新投影路径；
- `list_reference_candidates` API 和前端 query；
- `_reference_persistence_target`；
- `ensure_draft_scene_variant` 及预览中的调用；
- `additional_asset_ids`、`confirm_draft`、`choose_*` 等生成期 decision；
- 无绑定时的名称/文件路径 fallback。

保留历史 runner 对已冻结 v1 snapshot 的只读兼容分支，不迁移或重写历史任务。

- [ ] **步骤 4：运行完整目标回归**

```bash
.venv/bin/python -m pytest \
  tests/test_planned_reference_bindings.py \
  tests/test_planned_reference_binding_service.py \
  tests/test_planned_reference_binding_resolver.py \
  tests/test_task_identity_planner_bindings.py \
  tests/test_task_episode_asset_bindings.py \
  tests/test_api_narrative_groups.py \
  tests/test_narrative_group_reference_decisions.py \
  tests/test_narrative_group_reference_matching.py \
  tests/test_narrative_group_reference_requirements.py \
  tests/test_narrative_group_reference_uploads.py \
  tests/test_narrative_group_runner_references.py \
  tests/test_identity_planner_character_promotion.py \
  tests/test_identity_planner_appearance_validation.py \
  tests/test_asset_compiler_scene_enrichment.py \
  tests/test_asset_compiler_prop_planning.py -q

pnpm --dir frontend exec vitest run \
  src/__tests__/components/episode/narrative-workbench/planned-reference-picker.test.tsx \
  src/__tests__/components/episode/narrative-workbench/temporary-reference-picker.test.tsx \
  src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx \
  src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx \
  src/__tests__/lib/queries/narrative-groups.test.ts

pnpm --dir frontend exec tsc --noEmit --pretty false
```

预期：全部 PASS。

- [ ] **步骤 5：静态检查、变更边界检查并提交**

```bash
.venv/bin/ruff check src/novelvideo/narrative_groups src/novelvideo/production_workflow src/novelvideo/task_backend/runners/identity.py src/novelvideo/task_backend/runners/episode_assets.py src/novelvideo/api/routes/narrative_groups.py tests/test_planned_reference_bindings.py tests/test_planned_reference_binding_service.py tests/test_planned_reference_binding_resolver.py
git diff --check
git status --short
git add src/novelvideo/api/routes/narrative_groups.py src/novelvideo/narrative_groups/reference_matching.py src/novelvideo/narrative_groups/reference_decisions.py src/novelvideo/task_backend/runners/narrative_group.py tests/test_narrative_group_reference_matching.py tests/test_narrative_group_runner_references.py tests/test_api_narrative_groups.py
git add -u frontend/src/components/episode/narrative-workbench/reference-resolution-dialog.tsx frontend/src/components/episode/narrative-workbench/matched-reference-section.tsx frontend/src/components/episode/narrative-workbench/unresolved-reference-section.tsx
git commit -m "refactor: retire runtime asset matching"
```

只提交本计划涉及的 hunk；工作树中的其他用户修改保持不变。
