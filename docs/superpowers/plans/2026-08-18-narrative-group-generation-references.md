# 叙事组图生成引用修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让叙事组图默认使用项目风格 Prompt、角色 Identity/肖像图和场景主参考图，并允许用户在每次生成前按分类或逐张临时取消。

**架构：** 新增一个纯后端叙事组引用解析器，统一完成风格、角色、场景解析、稳定 ID、去重、选择校验与 9 图上限。API 暴露预览并只接收不透明 ID；runner 重新解析后将 Prompt 和 `ImageGenerationRequest.references` 交给现有图片能力。前端新增临时确认对话框，不持久化用户选择。

**技术栈：** Python 3.11、FastAPI、Pydantic、pytest；React、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构

- 创建 `src/novelvideo/narrative_groups/references.py`：叙事组风格与图片引用的数据模型、解析、选择校验、排序和裁剪。
- 创建 `tests/test_narrative_group_references.py`：引用解析器的身份回退、场景、风格、去重、上限和安全测试。
- 修改 `src/novelvideo/api/routes/narrative_groups.py`：请求模型、引用预览路由、生成请求解析和 payload 传递。
- 修改 `tests/test_api_narrative_groups.py`：预览和生成 API 契约、默认兼容、422 与 split-only 测试。
- 修改 `src/novelvideo/task_backend/runners/narrative_group.py`：最终 Prompt、重新解析、provider `references` 传递和审计结果。
- 创建 `tests/test_narrative_group_runner_references.py`：runner 风格和引用请求测试。
- 修改 `frontend/src/lib/queries/narrative-groups.ts`：预览类型、路径、query hook 和 action payload。
- 修改 `frontend/src/__tests__/lib/queries/narrative-groups.test.ts`：前端请求契约测试。
- 创建 `frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`：临时分类/单图选择 UI。
- 创建 `frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`：对话框交互测试。
- 修改 `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：打开确认框、提交选择和 split-only 直通。
- 创建 `frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`：工作台生成接线测试。

### 任务 1：实现纯引用解析器

**文件：**
- 创建：`src/novelvideo/narrative_groups/references.py`
- 创建：`tests/test_narrative_group_references.py`

- [ ] **步骤 1：编写风格、Identity 回退和场景主图的失败测试**

```python
def test_resolve_group_references_uses_style_identity_and_scene_master(tmp_path, monkeypatch):
    write_project_config(tmp_path, {"visual_style": "anime"})
    identity = write_identity(tmp_path, "苏清晏", "苏清晏_少女")
    portrait = write_portrait(tmp_path, "谢铮")
    scene = write_scene_master(tmp_path, "旧宅大厅")
    beats = [
        beat(1, "{{苏清晏_少女}}进入大厅", identities=["苏清晏_少女"], scene_id="旧宅大厅"),
        beat(2, "{{谢铮}}回头", identities=["谢铮"], scene_id="旧宅大厅"),
    ]

    preview = resolve_group_reference_preview(tmp_path, beats, stage="render")

    assert preview.style.style_id == "anime"
    assert "2D cel animation" in preview.style.prompt
    assert [ref.source_kind for ref in preview.character_references] == ["identity", "portrait_fallback"]
    assert [ref.path for ref in preview.character_references] == [str(identity), str(portrait)]
    assert preview.scene_references[0].path == str(scene)
```

- [ ] **步骤 2：运行测试验证解析器尚不存在**

运行：`.venv\Scripts\python.exe -m pytest tests/test_narrative_group_references.py -q`

预期：FAIL，`ModuleNotFoundError: novelvideo.narrative_groups.references`。

- [ ] **步骤 3：实现专注的数据模型和默认解析**

```python
MAX_GROUP_IMAGE_REFERENCES = 9

@dataclass(frozen=True)
class GroupImageReference:
    id: str
    kind: Literal["character", "scene"]
    source_kind: Literal["identity", "portrait_fallback", "scene_master"]
    label: str
    path: str
    beat_numbers: tuple[int, ...]
    first_appearance: int
    character_name: str = ""
    identity_id: str = ""
    scene_id: str = ""
    warning: str = ""

@dataclass(frozen=True)
class GroupReferencePreview:
    style: GroupStyleReference
    character_references: tuple[GroupImageReference, ...]
    scene_references: tuple[GroupImageReference, ...]
    warnings: tuple[str, ...]

def resolve_group_reference_preview(
    project_dir: Path, beats: Sequence[Mapping[str, Any]], *, stage: str
) -> GroupReferencePreview:
    config = load_project_config_file(project_dir)
    style_id = str(config.get("visual_style") or "chinese_period_drama")
    preset = get_style_preset(style_id)
    # Use real_detected_identities(), beat_scene_id(), compute_identity_path(),
    # the canonical portrait fallback, and compute_scene_master_path().
```

使用 SHA-256 对 `kind + canonical logical identity` 生成稳定不透明 ID，不把绝对路径编码进 ID。Identity 图调用 `compute_identity_path(project_dir, character_name, identity_id)`；角色默认肖像回退调用 `compute_portrait_path(project_dir, character_name)`；场景主图调用 `compute_scene_master_path(project_dir, scene_id)`，不新增第二套路径规则。

- [ ] **步骤 4：补充去重、覆盖 beat、排序、9 图裁剪和选择安全测试**

```python
def test_apply_selection_rejects_unknown_ids(preview):
    with pytest.raises(UnknownGroupReferenceIds) as exc:
        apply_group_reference_selection(
            preview,
            use_style=True,
            selected_character_reference_ids=["foreign-id"],
            selected_scene_reference_ids=None,
        )
    assert exc.value.unknown_ids == ("foreign-id",)

def test_limit_keeps_characters_before_scenes_and_reports_omissions(preview_with_twelve_refs):
    selection = apply_group_reference_selection(preview_with_twelve_refs)
    assert len(selection.image_paths) == 9
    assert all(ref.kind == "character" for ref in selection.selected[:8])
    assert selection.omitted
```

- [ ] **步骤 5：实现选择、稳定排序和裁剪的最少代码**

```python
def apply_group_reference_selection(
    preview: GroupReferencePreview,
    *,
    use_style: bool = True,
    selected_character_reference_ids: Sequence[str] | None = None,
    selected_scene_reference_ids: Sequence[str] | None = None,
) -> GroupReferenceSelection:
    available = {ref.id: ref for ref in (*preview.character_references, *preview.scene_references)}
    requested = _requested_ids(preview, selected_character_reference_ids, selected_scene_reference_ids)
    unknown = tuple(ref_id for ref_id in requested if ref_id not in available)
    if unknown:
        raise UnknownGroupReferenceIds(unknown)
    ordered = sorted((available[ref_id] for ref_id in requested), key=_priority_key)
    return GroupReferenceSelection.from_ordered(ordered, limit=MAX_GROUP_IMAGE_REFERENCES, use_style=use_style)
```

- [ ] **步骤 6：运行解析器测试并提交**

运行：`.venv\Scripts\python.exe -m pytest tests/test_narrative_group_references.py -q`

预期：PASS。

提交：

```bash
git add src/novelvideo/narrative_groups/references.py tests/test_narrative_group_references.py
git commit -m "feat: resolve narrative group generation references"
```

### 任务 2：增加引用预览与安全生成契约

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写预览响应和生成 payload 的失败测试**

```python
def test_reference_preview_returns_only_project_scoped_assets(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/references"
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["style"]["enabled_by_default"] is True
    assert all(item["thumbnail_url"].startswith("/api/v1/projects/demo/media/") for item in body["character_references"])
    assert all("path" not in item for item in body["character_references"])

def test_generate_queues_ephemeral_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "use_style": False,
            "selected_character_reference_ids": ["char-1"],
            "selected_scene_reference_ids": [],
        },
    )
    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": False,
        "selected_character_reference_ids": ["char-1"],
        "selected_scene_reference_ids": [],
    }
```

- [ ] **步骤 2：运行 API 测试确认失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_api_narrative_groups.py -q`

预期：FAIL，预览路由 404，生成请求未保存 `reference_selection`。

- [ ] **步骤 3：实现请求模型、预览序列化和 payload 传递**

```python
class NarrativeGroupGenerationRequest(BaseModel):
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None

@router.get("/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/{stage_name}/references")
async def preview_group_references(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    group, selected_beats = _selected_group_beats(groups, beats, group_id)
    preview = resolve_group_reference_preview(resolved.project_dir, selected_beats, stage=stage_name)
    return {"ok": True, "data": _serialize_reference_preview(project, resolved.project_dir, preview)}
```

同一步新增 `_selected_group_beats(groups, beats, group_id)`：按稳定 `beat_ids` 选取 beats，不存在的 group 抛 `KeyError` 并由路由转换为 404。生成和重生成接受同一个可选 body；body 缺失时写入默认 `None` 选择，保持旧客户端全选语义。split-only 路由不调用解析器，也不复制选择字段。

- [ ] **步骤 4：编写未知/越权 ID 返回 422、旧请求默认全选和 split-only 不解析测试**

```python
def test_generate_rejects_unknown_reference_ids(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"selected_character_reference_ids": ["cross-project"]},
    )
    assert response.status_code == 422
```

- [ ] **步骤 5：运行 API 测试并提交**

运行：`.venv\Scripts\python.exe -m pytest tests/test_api_narrative_groups.py -q`

预期：PASS。

提交：

```bash
git add src/novelvideo/api/routes/narrative_groups.py tests/test_api_narrative_groups.py
git commit -m "feat: expose narrative group reference preview"
```

### 任务 3：把风格和图片引用传入叙事组 runner

**文件：**
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py`
- 创建：`tests/test_narrative_group_runner_references.py`
- 修改：`tests/test_task_narrative_group_runners.py`

- [ ] **步骤 1：编写最终 Prompt 与 `references` 的失败测试**

```python
def test_build_generation_input_applies_anime_and_reference_mapping(tmp_path):
    payload = group_payload_with_beats(tmp_path, style="anime")
    resolved = _generation_input(payload)
    assert "2D cel animation" in resolved.prompt
    assert "FORBIDDEN: photorealistic rendering, 3D CGI" in resolved.prompt
    assert "finished cinematic frame" not in resolved.prompt
    assert "Reference 1 = character 苏清晏 (identity 苏清晏_少女)" in resolved.prompt
    assert "Reference 2 = scene 旧宅大厅" in resolved.prompt
    assert len(resolved.references) == 2

@pytest.mark.asyncio
async def test_generate_grid_passes_resolved_references_to_image_request(monkeypatch, tmp_path):
    captured = {}
    runtime = fake_runtime(captured)
    monkeypatch.setattr(narrative_group, "load_grsai_runtime_configuration", lambda *_: runtime)
    await narrative_group._generate_grid(group_payload_with_beats(tmp_path), fake_context(tmp_path))
    request = captured["request"]
    assert request.references == [str(tmp_path / "identity.png"), str(tmp_path / "master.png")]
```

- [ ] **步骤 2：运行 runner 测试确认失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_narrative_group_runner_references.py -q`

预期：FAIL，`_generation_input` 不存在且请求没有 `references`。

- [ ] **步骤 3：实现 runner 重新解析、Prompt 组合和请求字段**

```python
@dataclass(frozen=True)
class GroupGenerationInput:
    prompt: str
    references: tuple[str, ...]
    warnings: tuple[str, ...]

def _generation_input(payload: Mapping[str, Any]) -> GroupGenerationInput:
    preview = resolve_group_reference_preview(
        Path(str(payload["project_dir"])), list(payload.get("beats") or []), stage=str(payload["stage"])
    )
    selection = apply_group_reference_selection(preview, **dict(payload.get("reference_selection") or {}))
    return GroupGenerationInput(
        prompt=_grid_prompt(payload, style_prompt=selection.style_prompt, references=selection.selected),
        references=tuple(selection.image_paths),
        warnings=selection.warnings,
    )

request = ImageGenerationRequest(
    capability=MediaCapability.IMAGE_STORYBOARD_GRID,
    prompt=generation_input.prompt,
    model=runtime.model,
    references=list(generation_input.references),
    aspect_ratio="1:1",
    image_size="2K",
)
```

把 provider 相关 import 保持在函数内，延续当前 runner 的延迟加载模式。返回结果加入 `reference_count` 和 `reference_warnings`，但不返回本地绝对路径。

- [ ] **步骤 4：增加 Sketch 风格层、资产消失降级和 split-only 回归测试**

```python
def test_sketch_keeps_black_and_white_medium_with_anime_shape_language(tmp_path):
    resolved = _generation_input(group_payload_with_beats(tmp_path, stage="sketch", style="anime"))
    assert "black-and-white storyboard sketch" in resolved.prompt
    assert "3D CGI" in resolved.prompt

def test_missing_selected_asset_becomes_warning_not_stale_path(tmp_path):
    payload = group_payload_with_previewed_selection(tmp_path)
    (tmp_path / "identity.png").unlink()
    resolved = _generation_input(payload)
    assert str(tmp_path / "identity.png") not in resolved.references
    assert any("no longer exists" in warning for warning in resolved.warnings)
```

- [ ] **步骤 5：运行 runner 与编排回归测试并提交**

运行：`.venv\Scripts\python.exe -m pytest tests/test_narrative_group_runner_references.py tests/test_task_narrative_group_runners.py -q`

预期：PASS。

提交：

```bash
git add src/novelvideo/task_backend/runners/narrative_group.py tests/test_narrative_group_runner_references.py tests/test_task_narrative_group_runners.py
git commit -m "fix: apply references to narrative group generation"
```

### 任务 4：增加前端引用 API 类型与临时选择对话框

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`
- 创建：`frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`

- [ ] **步骤 1：编写前端路径和 payload 的失败测试**

```typescript
it("builds reference preview path and keeps explicit empty selections", () => {
  expect(narrativeGroupReferencePath("demo", 2, "ng-01", "render"))
    .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/render/references");
  expect(narrativeGroupActionPayload({
    useStyle: false,
    selectedCharacterReferenceIds: ["char-1"],
    selectedSceneReferenceIds: [],
  })).toEqual({
    use_style: false,
    selected_character_reference_ids: ["char-1"],
    selected_scene_reference_ids: [],
  });
});
```

- [ ] **步骤 2：运行 query 测试确认失败**

运行：`pnpm --dir frontend vitest run src/__tests__/lib/queries/narrative-groups.test.ts`

预期：FAIL，预览路径和选择类型不存在。

- [ ] **步骤 3：实现前端类型、query hook 和 action payload**

```typescript
export interface NarrativeGroupImageReference {
  id: string;
  kind: "character" | "scene";
  source_kind: "identity" | "portrait_fallback" | "scene_master";
  label: string;
  thumbnail_url?: string | null;
  beat_numbers: number[];
  enabled_by_default: boolean;
  warning?: string | null;
}

export interface NarrativeGroupReferencePreview {
  style: { id: string; label: string; prompt: string; enabled_by_default: boolean; warning?: string | null };
  character_references: NarrativeGroupImageReference[];
  scene_references: NarrativeGroupImageReference[];
  limits: { max_images: number; selected_images: number; omitted_reference_ids: string[] };
  warnings: string[];
}

export interface NarrativeGroupGenerationSelection {
  useStyle: boolean;
  selectedCharacterReferenceIds: string[];
  selectedSceneReferenceIds: string[];
}
```

`useNarrativeGroupReferences()` 仅在对话框打开时启用。`useNarrativeGroupAction()` 的 split action 继续发送空 JSON；generate/regenerate 才发送选择字段。

- [ ] **步骤 4：编写对话框默认、总开关、逐张取消和关闭重置测试**

```typescript
it("defaults all references on and supports category and item cancellation", async () => {
  const submit = vi.fn();
  const { rerender } = render(<GroupReferenceDialog open preview={preview} onSubmit={submit} onOpenChange={vi.fn()} />);
  expect(screen.getByRole("button", { name: "使用 3 张参考图生成" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "取消引用 苏清晏（少女）" }));
  expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("switch", { name: "场景参考" }));
  fireEvent.click(screen.getByRole("button", { name: "使用 1 张参考图生成" }));
  expect(submit).toHaveBeenCalledWith({
    useStyle: true,
    selectedCharacterReferenceIds: ["char-xiezheng"],
    selectedSceneReferenceIds: [],
  });
  rerender(<GroupReferenceDialog open={false} preview={preview} onSubmit={submit} onOpenChange={vi.fn()} />);
  rerender(<GroupReferenceDialog open preview={preview} onSubmit={submit} onOpenChange={vi.fn()} />);
  expect(screen.getByRole("button", { name: "使用 3 张参考图生成" })).toBeInTheDocument();
});
```

- [ ] **步骤 5：实现对话框的最少 UI**

使用现有 `Dialog`、`Switch`、`Button` 和项目媒体 URL。组件内部 state 在 `open` 或 `preview` 变化时从 `enabled_by_default` 重建；不写入 Zustand、localStorage 或 query cache。每张卡显示来源、覆盖 beat、回退/缺图/超限警告，并提供可访问的取消按钮标签。

- [ ] **步骤 6：运行前端单元测试并提交**

运行：`pnpm --dir frontend vitest run src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`

预期：PASS。

提交：

```bash
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/__tests__/lib/queries/narrative-groups.test.ts frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx
git commit -m "feat: add narrative group reference picker"
```

### 任务 5：把确认对话框接入生成与重生成

**文件：**
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-pipeline.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

- [ ] **步骤 1：编写生成先开确认框、split-only 直通的失败测试**

```typescript
it("opens references before generation and submits only confirmed ids", async () => {
  renderWorkbench({ preview });
  fireEvent.click(screen.getByRole("button", { name: "开始生成" }));
  expect(await screen.findByRole("dialog", { name: "生成前引用确认" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "取消引用 旧宅大厅" }));
  fireEvent.click(screen.getByRole("button", { name: "使用 2 张参考图生成" }));
  expect(actionMutation).toHaveBeenCalledWith(expect.objectContaining({
    groupId: "ng-01",
    stage: "render",
    action: "generate",
    selection: expect.objectContaining({ selectedSceneReferenceIds: [] }),
  }));
});

it("retries split without opening the reference dialog", () => {
  renderWorkbench({ renderStatus: "partial_failure" });
  fireEvent.click(screen.getByRole("button", { name: "仅重试切分" }));
  expect(screen.queryByRole("dialog", { name: "生成前引用确认" })).not.toBeInTheDocument();
  expect(actionMutation).toHaveBeenCalledWith(expect.objectContaining({ action: "split" }));
});
```

- [ ] **步骤 2：运行工作台测试确认失败**

运行：`pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

预期：FAIL，生成仍直接提交且确认框不存在。

- [ ] **步骤 3：实现待确认 action 状态和接线**

```typescript
const [pendingGridAction, setPendingGridAction] = useState<{
  stage: NarrativeGridStage;
  action: "generate" | "regenerate";
} | null>(null);

const runAction = async (stage: NarrativeGridStage, nextAction: NarrativeGroupAction) => {
  if (nextAction !== "split") {
    setPendingGridAction({ stage, action: nextAction });
    return;
  }
  await submitGridAction(stage, nextAction);
};
```

选择确认后调用现有 mutation 并启动 task controller；加载失败时对话框显示错误和重试，不绕过确认直接生成。叙事组切换时关闭对话框并清除 pending action。

- [ ] **步骤 4：运行工作台、query 和对话框测试并提交**

运行：`pnpm --dir frontend vitest run src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

预期：PASS。

提交：

```bash
git add frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/components/episode/narrative-workbench/group-pipeline.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git commit -m "feat: confirm references before group generation"
```

### 任务 6：全链路验证与回归

**文件：**
- 验证：上述全部文件

- [ ] **步骤 1：运行后端聚焦测试**

运行：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_narrative_group_references.py tests/test_api_narrative_groups.py tests/test_narrative_group_runner_references.py tests/test_task_narrative_group_runners.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行前端聚焦测试**

运行：

```powershell
pnpm --dir frontend vitest run src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

预期：全部 PASS。

- [ ] **步骤 3：运行静态检查和相关回归**

运行：

```powershell
.venv\Scripts\python.exe -m ruff check src/novelvideo/narrative_groups src/novelvideo/api/routes/narrative_groups.py src/novelvideo/task_backend/runners/narrative_group.py tests/test_narrative_group_references.py tests/test_api_narrative_groups.py tests/test_narrative_group_runner_references.py
pnpm --dir frontend exec tsc --noEmit
git diff --check
```

预期：全部退出码 0，`git diff --check` 无输出。

- [ ] **步骤 4：核对工作树只包含本计划文件和用户原有改动**

运行：`git status --short`

预期：本计划所列文件均已提交；任务开始前已经存在的其他改动保持原样，没有被暂存、覆盖或回滚。

- [ ] **步骤 5：如验证阶段产生必要修正，独立提交**

若验证修正后端，仅暂存本功能的后端文件：

```bash
git add src/novelvideo/narrative_groups/references.py src/novelvideo/api/routes/narrative_groups.py src/novelvideo/task_backend/runners/narrative_group.py tests/test_narrative_group_references.py tests/test_api_narrative_groups.py tests/test_narrative_group_runner_references.py tests/test_task_narrative_group_runners.py
git commit -m "test: cover narrative group reference workflow"
```

若验证修正前端，仅暂存本功能的前端文件：

```bash
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/__tests__/lib/queries/narrative-groups.test.ts frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/components/episode/narrative-workbench/group-pipeline.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-pipeline.test.tsx
git commit -m "test: cover narrative group reference picker"
```
