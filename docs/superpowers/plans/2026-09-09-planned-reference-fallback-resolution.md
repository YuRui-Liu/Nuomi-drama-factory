# 规划引用身份对齐与基础资产回退实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让规划引用使用本集已规划的真实身份 ID，并在人物身份图或场景变体图不可用时默认回退到对应基础资产。

**架构：** 在规划绑定投影器中加入本集身份规划上下文，按“真实 ID → 默认映射 → 本集唯一身份 → 同角色兼容阶段名”的顺序确定身份。回退仍作为持久化 `PlannedReferenceBinding` 写入，但使用基础资产槽位和 `explicit_fallback`，因此现有预览解析器可验证真实采用版本并默认选中，无需前端伪造状态。

**技术栈：** Python 3.11、Pydantic、pytest、现有 SQLite/ProductionWorkflow 绑定模型。

---

## 文件结构

- 修改 `src/novelvideo/production_workflow/slot_ids.py`：提供规范角色头像槽位 ID 构造器。
- 修改 `src/novelvideo/narrative_groups/planned_binding_service.py`：解析规划身份并建立人物/场景基础资产回退绑定。
- 修改 `src/novelvideo/task_backend/runners/identity.py`：把身份规划草稿中的本集身份集合和默认映射传入绑定投影器。
- 修改 `tests/test_task_identity_planner_bindings.py`：覆盖角色名到已规划身份的映射和头像回退。
- 修改 `tests/test_task_episode_asset_bindings.py`：覆盖场景变体优先及基础场景回退。
- 修改 `tests/production_workflow/test_slot_ids.py`：覆盖角色头像槽位 ID 的合法性与拒绝非法输入。

### 任务 1：用规划结果解析真实人物身份

**文件：**
- 修改：`tests/test_task_identity_planner_bindings.py`
- 修改：`src/novelvideo/narrative_groups/planned_binding_service.py`
- 修改：`src/novelvideo/task_backend/runners/identity.py`

- [ ] **步骤 1：编写角色名映射到规划默认身份的失败测试**

在 `tests/test_task_identity_planner_bindings.py` 新增用例：导演需求使用 `entity_key="陆无咎"`，草稿包含 `episode_identity_ids=("陆无咎_青年时期",)` 和 `identity_default_map={"陆无咎": "陆无咎_青年时期"}`，断言绑定的 `entity_id` 是真实身份 ID，状态为 `ready`。

```python
def test_character_name_requirement_uses_planned_default_identity():
    identity = _identity("陆无咎_青年时期", with_image=True)
    draft = IdentityPlanDraft(
        new_count=0,
        resolved_count=1,
        characters=(_character("陆无咎", identity),),
        episode_identity_ids=(identity.identity_id,),
        identity_default_map={"陆无咎": identity.identity_id},
        identity_baseline_digests={"陆无咎": "baseline"},
        episode_identity_baseline_digest="episode-baseline",
    )
    plan = _plan_for_character_requirement("陆无咎")

    bindings = _character_identity_bindings(
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        draft=draft,
        characters=(_character("陆无咎"),),
        scenes=(),
        props=(),
    )

    assert bindings[0].entity_id == "陆无咎_青年时期"
    assert bindings[0].status == "ready"
```

- [ ] **步骤 2：运行测试并验证它因精确 ID 匹配失败**

运行：`uv run pytest tests/test_task_identity_planner_bindings.py::test_character_name_requirement_uses_planned_default_identity -q`

预期：FAIL；当前结果的 `entity_id` 仍为 `陆无咎`，状态为 `missing_asset`。

- [ ] **步骤 3：向绑定投影器传入本集规划上下文**

为 `bindings_for_director_plan()` 和 `_binding()` 增加默认兼容参数：

```python
episode_identity_ids: Iterable[str] = (),
identity_default_map: Mapping[str, str] | None = None,
```

身份候选按以下顺序解析：精确 `identity_id`；角色默认映射；该角色在 `episode_identity_ids` 中的唯一身份；同角色唯一身份。精确 ID 始终优先，多个候选返回 `pending_confirmation`。

在 `_character_identity_bindings()` 调用中传入：

```python
episode_identity_ids=draft.episode_identity_ids,
identity_default_map=draft.identity_default_map,
```

- [ ] **步骤 4：补充阶段名称兼容及歧义保护测试**

新增两个测试：`陆无咎_青年期` 只在角色 `陆无咎` 的本集身份内解析到 `陆无咎_青年时期`；同角色有两个本集身份且没有默认映射时保持 `pending_confirmation`，不得按列表顺序选择。

- [ ] **步骤 5：运行身份绑定测试**

运行：`uv run pytest tests/test_task_identity_planner_bindings.py -q`

预期：全部 PASS。

- [ ] **步骤 6：提交人物身份解析变更**

```bash
git add src/novelvideo/narrative_groups/planned_binding_service.py src/novelvideo/task_backend/runners/identity.py tests/test_task_identity_planner_bindings.py
git commit -m "fix: bind planned character identities by episode selection"
```

### 任务 2：人物身份图不可用时回退角色头像

**文件：**
- 修改：`tests/production_workflow/test_slot_ids.py`
- 修改：`tests/test_task_identity_planner_bindings.py`
- 修改：`src/novelvideo/production_workflow/slot_ids.py`
- 修改：`src/novelvideo/narrative_groups/planned_binding_service.py`

- [ ] **步骤 1：编写规范头像槽位和回退绑定的失败测试**

在槽位测试中断言：

```python
assert character_portrait_slot_id("陆无咎") == "character:陆无咎:portrait"
```

在身份绑定测试中创建没有 `reference_images` 的已规划身份，断言：

```python
assert binding.entity_id == "陆无咎_青年时期"
assert binding.asset_slot_id == "character:陆无咎:portrait"
assert binding.status == "ready"
assert binding.resolution == "explicit_fallback"
```

- [ ] **步骤 2：运行测试并确认缺少头像槽位构造器且当前状态为 `missing_image`**

运行：`uv run pytest tests/production_workflow/test_slot_ids.py tests/test_task_identity_planner_bindings.py -q`

预期：FAIL；导入 `character_portrait_slot_id` 失败或身份绑定仍指向 state 槽位。

- [ ] **步骤 3：实现最小头像回退**

在 `slot_ids.py` 新增：

```python
def character_portrait_slot_id(character_name: str) -> str:
    return f"character:{_required(character_name, 'character_name')}:portrait"
```

身份候选已唯一确定但 `_explicitly_missing_image(identity, identity=True)` 为真时，保留真实身份 `entity_id`，改用头像槽位，设置：

```python
status = "ready"
resolution = "explicit_fallback"
label = f"{character_name} / {identity_name}（基础头像）"
```

预览解析器继续通过生产工作流验证头像槽位是否确有当前采用版本；没有版本时会返回 `missing_asset`/`missing_image`，不会提交空引用。

- [ ] **步骤 4：运行人物回退相关测试**

运行：`uv run pytest tests/production_workflow/test_slot_ids.py tests/test_task_identity_planner_bindings.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交人物头像回退变更**

```bash
git add src/novelvideo/production_workflow/slot_ids.py src/novelvideo/narrative_groups/planned_binding_service.py tests/production_workflow/test_slot_ids.py tests/test_task_identity_planner_bindings.py
git commit -m "fix: fall back planned identities to character portraits"
```

### 任务 3：场景变体不可用时回退基础场景

**文件：**
- 修改：`tests/test_task_episode_asset_bindings.py`
- 修改：`src/novelvideo/narrative_groups/planned_binding_service.py`

- [ ] **步骤 1：编写变体缺失和变体无图的失败测试**

新增两种输入：只有 `NovelScene(name="谢家碑坊", reference_images=[...])`；以及同时存在无图变体 `NovelScene(name="暴雨天井", base_scene_id="谢家碑坊", variant_id="暴雨天井")`。两者都断言：

```python
assert binding.asset_kind == "scene_variant"
assert binding.base_entity_id == "谢家碑坊"
assert binding.variant_id == "暴雨天井"
assert binding.entity_id == "谢家碑坊"
assert binding.asset_slot_id == "scene:谢家碑坊:base:master"
assert binding.status == "ready"
assert binding.resolution == "explicit_fallback"
```

- [ ] **步骤 2：运行测试并验证当前分别返回 `missing_asset` 和 `missing_image`**

运行：`uv run pytest tests/test_task_episode_asset_bindings.py -k 'scene_variant and fallback' -q`

预期：FAIL；当前绑定没有基础场景回退。

- [ ] **步骤 3：实现变体优先、基础场景回退**

在 `scene_variant` 分支中先解析唯一变体。仅当变体有可用图片时使用 `scene_state_slot_id`；否则精确查找 `base_entity_id` 对应的唯一基础场景，使用 `scene_base_slot_id(base_entity_id, "master")`，并设置 `ready + explicit_fallback`。基础场景不存在时保留原错误状态，多个基础候选时设置 `pending_confirmation`。

- [ ] **步骤 4：补充变体可用时仍优先变体的保护测试**

创建基础场景和带图变体，断言仍使用 `scene_state_slot_id`、变体真实 `entity_id` 和 `auto_matched`。

- [ ] **步骤 5：运行场景绑定测试**

运行：`uv run pytest tests/test_task_episode_asset_bindings.py -q`

预期：全部 PASS。

- [ ] **步骤 6：提交场景回退变更**

```bash
git add src/novelvideo/narrative_groups/planned_binding_service.py tests/test_task_episode_asset_bindings.py
git commit -m "fix: fall back missing scene variants to base scenes"
```

### 任务 4：集成验证与静态检查

**文件：**
- 验证：`src/novelvideo/narrative_groups/planned_binding_service.py`
- 验证：`src/novelvideo/task_backend/runners/identity.py`
- 验证：`src/novelvideo/production_workflow/slot_ids.py`

- [ ] **步骤 1：运行规划引用相关回归测试**

运行：

```bash
uv run pytest \
  tests/test_task_identity_planner_bindings.py \
  tests/test_task_episode_asset_bindings.py \
  tests/test_narrative_group_video_references.py \
  tests/production_workflow/test_slot_ids.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行 Ruff**

运行：

```bash
uv run ruff check \
  src/novelvideo/narrative_groups/planned_binding_service.py \
  src/novelvideo/task_backend/runners/identity.py \
  src/novelvideo/production_workflow/slot_ids.py \
  tests/test_task_identity_planner_bindings.py \
  tests/test_task_episode_asset_bindings.py \
  tests/production_workflow/test_slot_ids.py
```

预期：`All checks passed!`

- [ ] **步骤 3：确认工作区只暂存本计划文件**

运行：`git status --short`

预期：本任务文件无未提交修改；用户和其他任务原有脏文件保持不变。
