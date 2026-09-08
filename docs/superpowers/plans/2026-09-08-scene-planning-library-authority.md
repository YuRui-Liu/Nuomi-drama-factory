# 场景规划资产库权威与宽松引用实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让场景规划只从资产库和规范剧本场次建立基础场景，导演方案只能在已知基础场景上请求变体，同时允许缺失引用时继续生成。

**架构：** 将基础场景身份和导演镜头状态拆成两条确定性数据流。剧本解析器输出规范基础场景，AssetCompiler 复用或受控新增这些场景，再把导演 `scene_state` 投影为已知基础场景的结构化变体；引用预览把完整性缺口降级为警告，提交端只校验实际选择和安全边界。

**技术栈：** Python 3.12、Pydantic、FastAPI、pytest、React、TypeScript、TanStack Query、Vitest。

---

## 文件结构

- `src/novelvideo/utils/screenplay_scene_parser.py`：规范化 Markdown 场次标题。
- `src/novelvideo/agents/asset_compiler.py`：分离基础场景来源与导演变体请求。
- `src/novelvideo/narrative_groups/planned_binding_service.py`：宽松预览与选择式快照。
- `frontend/src/lib/api-errors.ts`、`frontend/src/lib/queries/narrative-groups.ts`：解析结构化错误和预览警告。
- `frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`：展示警告且不因缺项禁用提交。
- 相应 Python/TypeScript 测试文件：锁定上述边界。

### 任务 1：规范剧本场景标题

**文件：**
- 修改：`src/novelvideo/utils/screenplay_scene_parser.py`
- 测试：`tests/test_screenplay_scene_parser.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_markdown_numbered_scene_heading_uses_only_location_as_identity():
    blocks = parse_scene_blocks("### 1-1 谢家碑坊\n石九走入雨中。")
    assert len(blocks) == 1
    assert blocks[0].scene_no == "1"
    assert blocks[0].location == "谢家碑坊"
    assert blocks[0].header_line == "### 1-1 谢家碑坊"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest -q tests/test_screenplay_scene_parser.py::test_markdown_numbered_scene_heading_uses_only_location_as_identity`

预期：FAIL，带 `###` 的行未被识别为编号场次。

- [ ] **步骤 3：实现最小规范化**

```python
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+")

def _semantic_header_text(value: str) -> str:
    return MARKDOWN_HEADING_RE.sub("", str(value or "").strip()).strip()
```

保留原始 `source_line.text` 作为 `header_line`，只用规范化文本参与所有场次结构正则匹配。

- [ ] **步骤 4：验证并提交**

运行：`uv run pytest -q tests/test_screenplay_scene_parser.py`

预期：全部 PASS。

```bash
git add src/novelvideo/utils/screenplay_scene_parser.py tests/test_screenplay_scene_parser.py
git commit -m "fix: normalize markdown scene headings"
```

### 任务 2：基础场景服从资产库，导演只产生变体

**文件：**
- 修改：`src/novelvideo/agents/asset_compiler.py`
- 测试：`tests/test_asset_compiler_scene_enrichment.py`

- [ ] **步骤 1：编写三个失败测试**

```python
@pytest.mark.asyncio
async def test_scene_plan_uses_source_scene_instead_of_director_display_anchor():
    existing = NovelScene(name="谢家碑坊", environment_prompt="四面结构完整")
    store = _FakeCogneeStore([existing])
    director = SimpleNamespace(groups=(SimpleNamespace(
        scene_anchor="### 1-1 谢家碑坊", time_anchor="日", shots=(),
    ),))
    compiler = AssetCompiler(store, director_plan=director)
    episode = SimpleNamespace(number=1, beat_source_text="### 1-1 谢家碑坊\n石九走入雨中。")
    draft = await compiler.build_scene_plan_draft(episode)
    assert [item.scene_id for item in draft.scene_menu if not item.base_scene_id] == ["谢家碑坊"]
    assert all(not item.scene_id.startswith("###") for item in draft.scene_menu)

@pytest.mark.asyncio
async def test_director_scene_state_creates_variant_only_under_known_base():
    existing = NovelScene(name="谢家碑坊", environment_prompt="四面结构完整")
    requirement = SimpleNamespace(
        kind="scene_state", entity_key="谢家碑坊_暴雨天井",
        visible_change="天井积水并受强降雨冲击", design_notes="",
    )
    shot = SimpleNamespace(subject="石九", action="避雨", asset_requirements=(requirement,))
    director = SimpleNamespace(groups=(SimpleNamespace(
        scene_anchor="谢家碑坊", time_anchor="日", shots=(shot,),
    ),))
    compiler = AssetCompiler(_FakeCogneeStore([existing]), director_plan=director)
    episode = SimpleNamespace(number=1, beat_source_text="### 1-1 谢家碑坊\n石九避雨。")
    draft = await compiler.build_scene_plan_draft(episode)
    variant = next(item for item in draft.scene_menu if item.variant_id == "暴雨天井")
    assert variant.base_scene_id == "谢家碑坊"

@pytest.mark.asyncio
async def test_unknown_director_scene_never_creates_base_asset():
    existing = NovelScene(name="谢家碑坊", environment_prompt="四面结构完整")
    director = SimpleNamespace(groups=(SimpleNamespace(
        scene_anchor="导演虚构的异空间", time_anchor="夜", shots=(),
    ),))
    compiler = AssetCompiler(_FakeCogneeStore([existing]), director_plan=director)
    episode = SimpleNamespace(number=1, beat_source_text="### 1-1 谢家碑坊\n石九停步。")
    draft = await compiler.build_scene_plan_draft(episode)
    assert "导演虚构的异空间" not in {scene.name for scene in draft.scenes}
```

测试将可选 AI 丰富函数替换为抛错哨兵，证明确定性路径不依赖文本模型。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest -q tests/test_asset_compiler_scene_enrichment.py -k 'source_scene_instead or variant_only or unknown_director'`

预期：FAIL；当前实现优先使用导演场景块。

- [ ] **步骤 3：实现来源分离**

```python
async def _load_source_scene_blocks(self, episode: Any) -> list[SceneBlock]:
    source_text = await self._load_source_text(episode)
    return [block for block in _build_scene_blocks(source_text) if block.location.strip()]
```

基础场景只从上述来源或现有资产库复用。新增确定性导演变体投影，使用 `parse_scene_requirement(requirement.entity_key, known_base_ids)`；只有返回的 `base_id` 已存在且 `variant_id` 非空时，才调用 `_build_derived_scene()`。`visible_change` 只进入变体描述，不参与基础场景命名；未知导演文本只保留为未匹配绑定，不写资产库。

- [ ] **步骤 4：验证并提交**

运行：`uv run pytest -q tests/test_asset_compiler_scene_enrichment.py`

预期：全部 PASS。

```bash
git add src/novelvideo/agents/asset_compiler.py tests/test_asset_compiler_scene_enrichment.py
git commit -m "fix: keep director scenes out of asset identity"
```

### 任务 3：缺失引用降级为警告

**文件：**
- 修改：`src/novelvideo/narrative_groups/planned_binding_service.py`
- 测试：`tests/test_planned_reference_binding_resolver.py`
- 测试：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写失败测试**

```python
@pytest.mark.asyncio
async def test_preview_reports_missing_required_bindings_without_rejecting(tmp_path):
    preview = await resolve_planned_reference_preview(
        _BindingStore([]), ProductionWorkflowStore(tmp_path / "workflow.json"),
        project_id="p1", episode_number=1, group_id="group-01",
        project_dir=tmp_path, active_plan_revision_id="director-r3",
        required_binding_keys=frozenset({("prop", "深灰功德碑", "")}),
    )
    assert "深灰功德碑" in preview.warnings[0]

@pytest.mark.asyncio
async def test_empty_bindings_return_upload_capable_preview(tmp_path):
    preview = await resolve_planned_reference_preview(
        _BindingStore([]), ProductionWorkflowStore(tmp_path / "workflow.json"),
        project_id="p1", episode_number=1, group_id="group-01",
        project_dir=tmp_path,
    )
    assert preview.bindings == ()
    assert preview.reference_revision

@pytest.mark.asyncio
async def test_snapshot_allows_omitting_unavailable_required_bindings(tmp_path):
    store = _BindingStore([])
    workflow = ProductionWorkflowStore(tmp_path / "workflow.json")
    preview = await resolve_planned_reference_preview(
        store, workflow, project_id="p1", episode_number=1,
        group_id="group-01", project_dir=tmp_path,
    )
    snapshot = await build_planned_reference_snapshot(
        store, workflow, project_id="p1", episode_number=1,
        group_id="group-01", project_dir=tmp_path,
        selected_binding_ids=[], upload_ids=[],
        reference_revision=preview.reference_revision,
    )
    assert snapshot.images == ()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest -q tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py -k 'missing_required or empty_bindings or omitting_unavailable'`

预期：FAIL，当前服务抛出 `PLANNED_REFERENCES_REQUIRED`。

- [ ] **步骤 3：实现宽松契约**

给 `PlannedReferencePreview` 增加默认空元组 `warnings` 字段。空绑定返回带稳定 revision 的空预览；缺少 required key 时生成中文警告，不抛异常。删除快照中“全部 required 必须可解析并被选中”的检查，只保留：未知 binding、实际选中但不可解析、版本过期、越权、路径和数量上限校验。

- [ ] **步骤 4：更新 API 断言、验证并提交**

把缺清单 API 用例改为断言 200、有效 revision 和 warnings。运行：

`uv run pytest -q tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py`

预期：全部 PASS。

```bash
git add src/novelvideo/narrative_groups/planned_binding_service.py tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py
git commit -m "fix: allow generation with partial planned references"
```

### 任务 4：前端展示警告并允许继续

**文件：**
- 修改：`frontend/src/lib/api-errors.ts`
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`
- 测试：`frontend/src/__tests__/api/client.test.ts`
- 测试：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`

- [ ] **步骤 1：编写失败测试**

```ts
it("maps structured FastAPI detail messages", () => {
  const error = errorFromBackendBody(409, {
    detail: { code: "STALE_REFERENCE_BINDING", message: "规划引用已过期" },
  }, "Conflict");
  expect(error).toBeInstanceOf(BackendStatusError);
  expect(error?.message).toBe("规划引用已过期");
});
```

Query 测试断言 `detail.message` 成为 hook 的 `error.message`；弹窗测试传入 `warnings` 和 required 缺图绑定，断言警告可见且“使用 0 张参考图生成”按钮可点击。

- [ ] **步骤 2：运行测试验证失败**

运行：`pnpm --dir frontend vitest run src/__tests__/api/client.test.ts src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`

预期：FAIL；结构化 detail 未转成 Error，query 使用原始 `.json()`，弹窗仍阻断。

- [ ] **步骤 3：实现最小前端改动**

`errorFromBackendBody()` 在特殊错误映射后为递归找到的 `message` 返回 `BackendStatusError`。引用 query 改用 `jsonWithBackendError` 包装请求。预览类型增加 `warnings: string[]`；弹窗用高对比琥珀色状态区展示警告，删除 `hasRequiredUnavailable` 及其提交禁用条件，不可用卡片仍不可选择。

- [ ] **步骤 4：验证并提交**

运行：

```bash
pnpm --dir frontend vitest run src/__tests__/api/client.test.ts src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx
pnpm --dir frontend exec tsc --noEmit
```

预期：全部 PASS。

```bash
git add frontend/src/lib/api-errors.ts frontend/src/lib/queries/narrative-groups.ts frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx frontend/src/__tests__/api/client.test.ts frontend/src/__tests__/lib/queries/narrative-groups.test.ts frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx
git commit -m "fix: warn instead of blocking incomplete references"
```

### 任务 5：回归验证

**文件：** 验证任务 1-4 的全部文件。

- [ ] **步骤 1：运行后端聚焦回归**

运行：`uv run pytest -q tests/test_screenplay_scene_parser.py tests/test_asset_compiler_scene_enrichment.py tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py`

预期：全部 PASS。

- [ ] **步骤 2：运行前端聚焦回归与类型检查**

重复任务 4 的 Vitest 和 `tsc --noEmit` 命令，预期全部 PASS。

- [ ] **步骤 3：运行静态检查**

运行：`uv run ruff check src/novelvideo/utils/screenplay_scene_parser.py src/novelvideo/agents/asset_compiler.py src/novelvideo/narrative_groups/planned_binding_service.py tests/test_screenplay_scene_parser.py tests/test_asset_compiler_scene_enrichment.py tests/test_planned_reference_binding_resolver.py tests/test_api_narrative_groups.py`

运行：`git diff --check`

预期：全部通过。

- [ ] **步骤 4：人工检查不变量**

确认变更没有让生成预览/提交写入资产；未知导演文本测试明确证明不新增基础场景；只有用户实际选择的无效引用和安全错误会阻断任务。
