# 叙事组生成前引用解析与补救实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完整展示人物、基础场景、场景变体和道具需求，并支持改绑、回退、忽略、项目资产选择及本地临时上传。

**架构：** 后端先将导演镜头解析为与图片无关的 `ReferenceRequirement`，再匹配项目资产并生成 `ReferenceBinding`。用户决策固化为 `ReferenceDecisionSnapshot`，执行端只读取快照中的安全图片。前端使用「待处理问题、已匹配引用、自由追加」三段式弹窗。

**技术栈：** Python、FastAPI、Pydantic、pytest；React、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构

- 创建 `src/novelvideo/narrative_groups/reference_requirements.py`：需求模型、状态和场景变体解析。
- 创建 `src/novelvideo/narrative_groups/reference_matching.py`：项目资产匹配与草稿变体创建。
- 创建 `src/novelvideo/narrative_groups/reference_decisions.py`：用户决策校验和快照生成。
- 创建 `src/novelvideo/narrative_groups/reference_uploads.py`：临时上传和可选正式保存。
- 修改 `src/novelvideo/narrative_groups/references.py`、`service.py`：新预览适配和导演需求保真投影。
- 修改 `src/novelvideo/api/routes/narrative_groups.py`、`api/schemas.py`：预览、候选、上传和生成决策 API。
- 修改 `src/novelvideo/task_backend/runners/narrative_group.py`：执行前复核引用快照。
- 修改 `frontend/src/lib/queries/narrative-groups.ts`：前端 API 类型和请求。
- 重构 `group-reference-dialog.tsx`，新增 `unresolved-reference-section.tsx`、`matched-reference-section.tsx`、`free-reference-picker.tsx` 和 `reference-resolution-dialog.tsx`。

### 任务 1：建立需求模型并保留导演资产语义

**文件：**
- 创建：`src/novelvideo/narrative_groups/reference_requirements.py`
- 修改：`src/novelvideo/narrative_groups/service.py:632-682`
- 测试：`tests/test_narrative_group_reference_requirements.py`
- 测试：`tests/director_plan/test_legacy_projection.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_flat_scene_state_uses_longest_known_base_name():
    assert parse_scene_requirement(
        "谢家_碑坊_暴雨天井", {"谢家", "谢家_碑坊"}
    ) == ("谢家_碑坊", "暴雨天井")

def test_unknown_scene_does_not_guess_base():
    assert parse_scene_requirement("未知地点_雨夜", {"谢家碑坊"}) == ("未知地点_雨夜", "")

def test_generation_beat_keeps_character_scene_and_prop_requirements(active_plan):
    [beat] = generation_beats_for_group(active_plan.path, 1, "group-1", [])
    assert [item["kind"] for item in beat["asset_requirements"]] == [
        "character_identity", "scene_state", "prop"
    ]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/pytest tests/test_narrative_group_reference_requirements.py tests/director_plan/test_legacy_projection.py -q`

预期：FAIL，解析模块不存在，且投影缺少完整 `asset_requirements`。

- [ ] **步骤 3：实现精确类型和解析**

```python
ReferenceKind = Literal["character_identity", "scene_base", "scene_variant", "prop"]
ReferenceStatus = Literal["matched", "fallback", "draft_variant", "missing_asset", "missing_image", "temporary", "ignored", "invalid"]

@dataclass(frozen=True)
class ReferenceRequirement:
    id: str
    kind: ReferenceKind
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""
    shot_ids: tuple[str, ...] = ()
    required: bool = True

def parse_scene_requirement(value: str, known: set[str]) -> tuple[str, str]:
    if value in known:
        return value, ""
    matches = [base for base in known if value.startswith(base + "_")]
    if not matches:
        return value, ""
    base = max(matches, key=len)
    return base, value[len(base) + 1:]
```

- [ ] **步骤 4：保真投影导演要求**

在 `generation_beats_for_group` 中加入：

```python
"asset_requirements": [item.model_dump(mode="json") for item in shot.asset_requirements]
```

相同需求合并 `shot_ids`，保持首次出现顺序；不同变体不得合并。旧的 `detected_identities` 和 `scene_ref` 暂时保留兼容。

- [ ] **步骤 5：验证并提交**

```bash
.venv/bin/pytest tests/test_narrative_group_reference_requirements.py tests/director_plan/test_legacy_projection.py -q
git add src/novelvideo/narrative_groups/reference_requirements.py src/novelvideo/narrative_groups/service.py tests/test_narrative_group_reference_requirements.py tests/director_plan/test_legacy_projection.py
git commit -m "feat: model narrative reference requirements"
```

### 任务 2：匹配资产并建立草稿场景变体

**文件：**
- 创建：`src/novelvideo/narrative_groups/reference_matching.py`
- 修改：`src/novelvideo/narrative_groups/references.py`
- 测试：`tests/test_narrative_group_reference_matching.py`
- 测试：`tests/test_narrative_group_references.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_matcher_keeps_missing_prop_and_creates_variant_draft(project_store):
    preview = match_reference_requirements(project_store, [
        scene_requirement("谢家碑坊", "暴雨天井"),
        prop_requirement("染血石碑"),
    ])
    assert preview.requirements[0].status == "draft_variant"
    assert preview.requirements[0].available_actions == (
        "confirm_draft", "choose_variant", "use_base", "upload"
    )
    assert preview.requirements[1].status == "missing_asset"
    assert "ignore" in preview.requirements[1].available_actions
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/pytest tests/test_narrative_group_reference_matching.py -q`

- [ ] **步骤 3：实现匹配规则**

身份图缺失时标记 `fallback` 并使用默认肖像；变体图缺失时保留变体需求及基础场景候选；道具无记录和无图片分别标记 `missing_asset`、`missing_image`。所有路径继续通过项目资产根目录校验，缺图需求不得删除。

- [ ] **步骤 4：实现幂等草稿变体**

```python
def ensure_draft_scene_variant(store, base_scene_id: str, variant_id: str):
    name = resolve_scene_record_name(base_scene_id, variant_id)
    existing = store.get_scene(name)
    if existing:
        return existing
    return store.create_scene_variant(
        base_scene_id=base_scene_id,
        variant_id=variant_id,
        notes="由叙事组资产需求自动创建，尚未确认",
    )
```

并发冲突时重新读取，不覆盖正式变体。

- [ ] **步骤 5：验证并提交**

```bash
.venv/bin/pytest tests/test_narrative_group_reference_matching.py tests/test_narrative_group_references.py -q
git add src/novelvideo/narrative_groups/reference_matching.py src/novelvideo/narrative_groups/references.py tests/test_narrative_group_reference_matching.py tests/test_narrative_group_references.py
git commit -m "feat: resolve narrative assets and variants"
```

### 任务 3：升级 API、实现上传和决策快照

**文件：**
- 创建：`src/novelvideo/narrative_groups/reference_uploads.py`
- 创建：`src/novelvideo/narrative_groups/reference_decisions.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py:294-340,700-870`
- 修改：`src/novelvideo/api/schemas.py`
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 测试：`tests/test_narrative_group_reference_uploads.py`
- 测试：`tests/test_narrative_group_reference_decisions.py`
- 测试：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写预览、上传和决策失败测试**

```python
def test_preview_returns_prop_without_image(client):
    data = client.get(REFERENCE_URL).json()["data"]
    prop = next(item for item in data["requirements"] if item["kind"] == "prop")
    assert prop["status"] == "missing_asset"
    assert prop["bindings"] == []

def test_upload_is_temporary_by_default(tmp_path):
    result = save_reference_upload(tmp_path, b"png", "image/png", persist=False)
    assert result.temporary is True
    assert not (tmp_path / "assets" / "props").exists()

def test_draft_variant_must_be_resolved_before_snapshot():
    with pytest.raises(UnresolvedReferenceRequirements):
        build_reference_snapshot(preview_with_draft_variant(), decisions=[])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/pytest tests/test_narrative_group_reference_uploads.py tests/test_narrative_group_reference_decisions.py tests/test_api_narrative_groups.py -k reference -q`

- [ ] **步骤 3：实现 API 契约**

预览返回 `requirements`、`bindings`、`style`、`limits`、`warnings`；旧 `character_references`、`scene_references` 保留一个兼容周期，并由 binding 派生。新增项目资产候选和上传接口。

```ts
export type ReferenceStatus = "matched" | "fallback" | "draft_variant" | "missing_asset" | "missing_image" | "temporary" | "ignored" | "invalid";
export interface NarrativeReferenceRequirement {
  id: string;
  kind: "character_identity" | "scene_base" | "scene_variant" | "prop";
  entity_id: string;
  base_entity_id?: string;
  variant_id?: string;
  shot_ids: string[];
  status: ReferenceStatus;
  available_actions: string[];
  bindings: NarrativeReferenceBinding[];
}
```

- [ ] **步骤 4：实现安全上传**

验证 MIME、扩展名和大小；使用 opaque upload ID 和受控临时目录。`persist=true` 时必须提供资产类型及目标实体；正式保存失败时保留临时 binding 并返回 `persistence_warning`。

- [ ] **步骤 5：实现决策快照**

拒绝未知 requirement、跨项目 asset、重复冲突决策和超过 9 张有效图片。`draft_variant` 必须处理；`missing_*` 必须补充或显式 `ignored`。客户端只提交 ID，服务端解析真实路径。

- [ ] **步骤 6：验证并提交**

```bash
.venv/bin/pytest tests/test_narrative_group_reference_uploads.py tests/test_narrative_group_reference_decisions.py tests/test_api_narrative_groups.py -k reference -q
npm --prefix frontend run build:ce
git add src/novelvideo/narrative_groups/reference_uploads.py src/novelvideo/narrative_groups/reference_decisions.py src/novelvideo/api/routes/narrative_groups.py src/novelvideo/api/schemas.py frontend/src/lib/queries/narrative-groups.ts tests/test_narrative_group_reference_uploads.py tests/test_narrative_group_reference_decisions.py tests/test_api_narrative_groups.py
git commit -m "feat: validate narrative reference decisions"
```

### 任务 4：构建问题优先弹窗和自由追加

**文件：**
- 修改：`frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/unresolved-reference-section.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/matched-reference-section.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/free-reference-picker.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/reference-resolution-dialog.tsx`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx`

- [ ] **步骤 1：编写问题优先失败测试**

```tsx
it("shows unresolved requirements before collapsed matches", () => {
  render(<GroupReferenceDialog preview={preview} open onSubmit={vi.fn()} onOpenChange={vi.fn()} />);
  expect(screen.getAllByRole("region")[0]).toHaveAccessibleName("待处理问题");
  expect(screen.getByRole("button", { name: /已匹配 2 项/ })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("谢家碑坊 / 暴雨天井")).toBeInTheDocument();
  expect(screen.getByText("染血石碑")).toBeInTheDocument();
});

it("keeps local upload temporary by default", async () => {
  render(<FreeReferencePicker {...props} />);
  await user.upload(screen.getByLabelText("本地上传图片"), imageFile);
  expect(api.upload).toHaveBeenCalledWith(expect.objectContaining({ persist: false }));
  expect(screen.getByText("仅用于本次生成")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`npm --prefix frontend test -- --run src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx`

- [ ] **步骤 3：实现三段布局和补救操作**

待处理区显示 `fallback/draft_variant/missing_*/invalid`；已匹配区默认折叠；自由追加支持项目资产和本地上传。变体操作包含确认草稿、选择已有、基础图回退和上传；道具包含选择、新建、上传和忽略。

- [ ] **步骤 4：实现提交语义**

按钮显示 `生成 · N 张参考图 · M 项已忽略`。`draft_variant` 未处理时禁用；`missing_*` 未处理时打开二次确认，确认后转成显式 `ignored` 再提交。

- [ ] **步骤 5：覆盖保存到项目资产库的条件表单**

勾选保存时要求资产类型、目标实体；场景图还要求基础场景和变体。取消选择器或上传对话框不得修改已有决策。

- [ ] **步骤 6：验证并提交**

```bash
npm --prefix frontend test -- --run src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx
git add frontend/src/components/episode/narrative-workbench frontend/src/__tests__/components/episode/narrative-workbench
git commit -m "feat: add narrative reference recovery UI"
```

### 任务 5：执行端快照复核和端到端验收

**文件：**
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 测试：`tests/test_task_narrative_group_runner.py`
- 测试：`tests/test_api_narrative_groups.py`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

- [ ] **步骤 1：编写执行前失效测试**

```python
def test_runner_stops_when_snapshot_image_disappears(tmp_path):
    snapshot = snapshot_with_image(tmp_path / "deleted.png")
    with pytest.raises(RuntimeError, match="REFERENCE_SNAPSHOT_INVALID"):
        run_group_generation(envelope_with(snapshot))
    assert fake_image_transport.calls == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/pytest tests/test_task_narrative_group_runner.py -k reference_snapshot -q`

- [ ] **步骤 3：执行前复核并输出审计摘要**

复核文件存在、路径位于项目或受控临时目录、MIME 合法、数量不超限。失败信息包含 requirement/upload ID，不泄露绝对路径。结果记录正式、临时、回退、忽略数量和快照 ID。

- [ ] **步骤 4：增加真实案例集成测试**

测试数据包含基础场景「谢家碑坊」、导演 requirement `谢家碑坊_暴雨天井`、缺图道具「染血石碑」及人物「石九」。覆盖确认草稿、忽略道具、临时上传、快照入队及执行端读取。

- [ ] **步骤 5：运行完整相关验证**

```bash
.venv/bin/pytest tests/test_narrative_group_reference_requirements.py tests/test_narrative_group_reference_matching.py tests/test_narrative_group_reference_uploads.py tests/test_narrative_group_reference_decisions.py tests/test_narrative_group_references.py tests/test_api_narrative_groups.py tests/director_plan/test_legacy_projection.py tests/test_task_narrative_group_runner.py -q
npm --prefix frontend test -- --run src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/free-reference-picker.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
npm --prefix frontend run build:ce
.venv/bin/python -m ruff check src/novelvideo/narrative_groups src/novelvideo/api/routes/narrative_groups.py src/novelvideo/task_backend/runners/narrative_group.py
git diff --check
```

预期：所有命令退出 0。若当前工作区存在无关失败，单独记录原始失败测试和所属未提交改动，不修改本功能测试来规避。

- [ ] **步骤 6：提交验收**

```bash
git add src/novelvideo/task_backend/runners/narrative_group.py src/novelvideo/api/routes/narrative_groups.py tests/test_task_narrative_group_runner.py tests/test_api_narrative_groups.py frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git commit -m "test: verify narrative reference workflow"
```
