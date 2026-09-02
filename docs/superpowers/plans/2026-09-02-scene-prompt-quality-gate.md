# 场景提示词质量门实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 阻止元指令式场景提示词落库和出图，并让重新规划能够修复历史污染、标记旧参考图过期。

**架构：** `cognee.pipeline` 提供唯一的纯函数质量检查器、历史污染识别和两次生成质量门；`AssetCompiler` 只负责决定哪些场景要重建并在整批验证成功后落库。SQLite 保存按图片种类划分的过期状态，场景 API 原样暴露，参考图 runner 在新版本成功采纳后清除对应状态，前端只负责告知用户重新生成。

**技术栈：** Python 3.11、Pydantic、SQLite、pytest/pytest-asyncio、React、TypeScript、Vitest、Testing Library。

---

## 文件结构

- 创建 `tests/test_scene_environment_prompt_quality.py`：质量合同、重试和明确失败的单元测试。
- 修改 `src/novelvideo/cognee/pipeline.py`：严格质量检查、历史模板识别、一次定向重试及稳定错误码。
- 修改 `tests/test_asset_compiler_scene_enrichment.py`：历史脏提示词重规划、正常提示词保留和批次不部分写入的回归测试。
- 修改 `src/novelvideo/agents/asset_compiler.py`：识别历史污染，先完成 enrich 再批量持久化。
- 修改 `src/novelvideo/models.py`：在 `NovelScene` 增加 `stale_reference_kinds`。
- 修改 `src/novelvideo/sqlite_store.py`：迁移、序列化、更新并清理按种类的参考图过期状态。
- 修改 `src/novelvideo/api/routes/scenes.py`：场景响应暴露 `stale_reference_kinds`。
- 修改 `tests/test_scene_reference_runner.py`：新参考图采纳后清除对应过期状态。
- 修改 `src/novelvideo/task_backend/runners/scene_reference.py`：仅在候选被采纳并复制为 canonical 后清理对应状态。
- 修改 `frontend/src/types/scene.ts`：补充场景过期状态类型。
- 修改 `frontend/src/components/assets/scene-asset-card.tsx`：在旧图槽位和卡片上显示明确警告。
- 修改 `frontend/src/__tests__/components/assets/scene-asset-card.test.tsx`：验证警告只出现在仍有旧图的种类上。

### 任务 1：建立严格的场景提示词质量合同

**文件：**
- 创建：`tests/test_scene_environment_prompt_quality.py`
- 修改：`src/novelvideo/cognee/pipeline.py`

- [ ] **步骤 1：编写质量检查失败测试**

```python
from novelvideo.cognee.pipeline import scene_environment_prompt_issues

VALID = """正面：磨砂玻璃双开门居中，门内连接三米宽直走廊。\n左侧：灰色吸音板墙沿走廊延伸，嵌入两扇隔音门。\n右侧：连续观察窗下方固定金属线槽，与设备间门相接。\n背面：走廊尽头为防火门，旁侧固定配电箱。\n光源：顶面冷白条形灯沿中轴连续布置。\n材质/风格：灰色环氧地坪、吸音板墙面、白色矿棉板吊顶。\n禁止元素：人物、文字、水印、移动道具、临时剧情状态。"""

def test_quality_gate_accepts_concrete_seven_section_prompt():
    assert scene_environment_prompt_issues(VALID) == []

def test_quality_gate_rejects_known_meta_template():
    issues = scene_environment_prompt_issues("正面：以广播站最能代表地点身份的入口，根据原文证据确定结构。\n左侧：布置与场景功能一致。\n右侧：合理连续补全。\n背面：必须构成完整360度。")
    assert "missing_section:光源" in issues
    assert any(item.startswith("meta_instruction:") for item in issues)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_environment_prompt_quality.py -q`

预期：FAIL，`scene_environment_prompt_issues` 尚不存在。

- [ ] **步骤 3：实现纯函数检查器和历史模板识别器**

```python
SCENE_PROMPT_SECTIONS = ("正面", "左侧", "右侧", "背面", "光源", "材质/风格", "禁止元素")
LEGACY_META_MARKERS = ("最能代表地点身份", "根据原文证据", "布置与", "合理连续补全", "必须构成完整 360")

def scene_environment_prompt_issues(prompt: str) -> list[str]:
    sections = parse_scene_environment_sections(prompt)
    issues = [f"missing_section:{name}" for name in SCENE_PROMPT_SECTIONS if not sections.get(name)]
    compact = re.sub(r"\s+", "", prompt or "")
    issues.extend(
        f"meta_instruction:{marker}"
        for marker in LEGACY_META_MARKERS
        if marker.replace(" ", "") in compact
    )
    return issues

def is_legacy_meta_scene_prompt(prompt: str) -> bool:
    normalized = re.sub(r"\s+", "", prompt or "")
    return sum(marker.replace(" ", "") in normalized for marker in LEGACY_META_MARKERS) >= 2
```

- [ ] **步骤 4：运行质量检查测试确认通过**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_environment_prompt_quality.py -q`

预期：PASS。

- [ ] **步骤 5：提交质量合同**

```powershell
git add -- tests/test_scene_environment_prompt_quality.py src/novelvideo/cognee/pipeline.py
git commit -m "fix: 拦截场景元指令提示词"
```

### 任务 2：增加一次修复重试并取消静默兜底

**文件：**
- 修改：`tests/test_scene_environment_prompt_quality.py`
- 修改：`src/novelvideo/cognee/pipeline.py`

- [ ] **步骤 1：编写重试与失败测试**

```python
@pytest.mark.asyncio
async def test_enrich_retries_invalid_prompt_once(monkeypatch):
    outputs = [invalid_enrichment(), valid_enrichment()]
    monkeypatch.setattr(pipeline, "llm_extract", AsyncMock(side_effect=outputs))
    scene = await pipeline.enrich_scene_environment_from_context(scene_name="走廊", scene_type="interior", context_lines=["固定设备走廊"])
    assert scene.environment_prompt == VALID
    assert pipeline.llm_extract.await_count == 2

@pytest.mark.asyncio
async def test_enrich_fails_after_two_invalid_attempts(monkeypatch):
    monkeypatch.setattr(pipeline, "llm_extract", AsyncMock(return_value=invalid_enrichment()))
    with pytest.raises(pipeline.ScenePromptQualityError, match=r"SCENE_PROMPT_QUALITY_FAILED: 走廊"):
        await pipeline.enrich_scene_environment_from_context(scene_name="走廊", scene_type="interior", context_lines=["固定设备走廊"])
```

- [ ] **步骤 2：运行测试确认旧代码错误返回兜底文本**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_environment_prompt_quality.py -q`

预期：FAIL，第二次无效仍返回 `NovelScene` 或未进行两次调用。

- [ ] **步骤 3：实现稳定错误与两次调用**

```python
class ScenePromptQualityError(RuntimeError):
    code = "SCENE_PROMPT_QUALITY_FAILED"

for attempt in range(2):
    try:
        request = user_text if attempt == 0 else build_scene_prompt_repair_request(user_text, rejected_prompt, last_issues)
        enrichment = (await agent.run(request)).output
        prompt = render_scene_environment_prompt(enrichment)
        issues = scene_environment_prompt_issues(prompt)
        if not issues:
            return build_scene(enrichment, prompt)
    except Exception as exc:
        issues = [f"model_error:{type(exc).__name__}"]
raise ScenePromptQualityError(f"SCENE_PROMPT_QUALITY_FAILED: {scene_name}: {', '.join(issues[:4])}")
```

第二次请求必须带上第一次被拒绝文本和问题列表；删除 `_ensure_directional_environment_prompt` 的元指令兜底成功分支。

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_environment_prompt_quality.py tests\test_cognee_pipeline_concurrency.py -q`

预期：PASS，且没有旧模块引用泄漏。

- [ ] **步骤 5：提交重试流程**

```powershell
git add -- tests/test_scene_environment_prompt_quality.py src/novelvideo/cognee/pipeline.py
git commit -m "fix: 场景提示词失败时定向重试"
```

### 任务 3：重新规划修复历史污染且批次不部分写入

**文件：**
- 修改：`tests/test_asset_compiler_scene_enrichment.py`
- 修改：`src/novelvideo/agents/asset_compiler.py`

- [ ] **步骤 1：编写历史污染与正常内容回归测试**

```python
@pytest.mark.asyncio
async def test_compile_reenriches_known_legacy_meta_prompt(monkeypatch):
    existing = NovelScene(name="设备走廊", environment_prompt=LEGACY_META_PROMPT)
    # fake enrich 返回有效提示词
    # 断言 update_scene 写入有效提示词，而不是直接跳过。

@pytest.mark.asyncio
async def test_compile_preserves_nonlegacy_manual_prompt(monkeypatch):
    existing = NovelScene(name="设备走廊", environment_prompt="用户手工空间描述")
    # 断言 enrich 未调用、update_scene 未调用。

@pytest.mark.asyncio
async def test_reconcile_does_not_persist_any_scene_when_one_enrichment_fails(monkeypatch):
    # 两个 create decision，第二个抛 ScenePromptQualityError。
    # 断言 store.added == []。
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv\Scripts\python.exe -m pytest tests\test_asset_compiler_scene_enrichment.py -q`

预期：FAIL，旧提示词被非空判断跳过，或首个场景已部分落库。

- [ ] **步骤 3：实现历史识别和两阶段持久化**

```python
if prompt and not is_legacy_meta_scene_prompt(prompt):
    return scene
enriched = await enrich_scene_environment_from_context(
    scene_name=scene.name,
    aliases=scene.aliases,
    scene_type=scene.scene_type,
    time_of_day=str(getattr(block, "time_of_day", "") or ""),
    interior=str(getattr(block, "interior_exterior", "") or "内") != "外",
    episodes=[int(getattr(episode, "number", 1) or 1)],
    characters=list(getattr(block, "characters", []) or []),
    context_lines=list(getattr(block, "lines", []) or []),
)

# reconcile 中先将所有 enrich 结果放入 prepared_scenes；全部成功后再 add_scene。
for scene in prepared_scenes:
    await store.add_scene(scene)
```

修复历史提示词时，把当前确实存在的 `master.png`、`reverse_master.png`、`pano_360.png` 对应种类写入 `stale_reference_kinds`；此任务先调用后续任务定义的 store helper，测试可用 fake helper 记录参数。

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv\Scripts\python.exe -m pytest tests\test_asset_compiler_scene_enrichment.py -q`

预期：PASS。

- [ ] **步骤 5：提交规划器修复**

```powershell
git add -- tests/test_asset_compiler_scene_enrichment.py src/novelvideo/agents/asset_compiler.py
git commit -m "fix: 重规划历史场景提示词"
```

### 任务 4：持久化并通过 API 暴露参考图过期状态

**文件：**
- 修改：`src/novelvideo/models.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 修改：`src/novelvideo/api/routes/scenes.py`
- 修改：`tests/test_api_assets.py`

- [ ] **步骤 1：编写迁移、更新和 API 序列化测试**

```python
scene = NovelScene(name="设备走廊", stale_reference_kinds=["master", "pano"])
await store.add_scene(scene)
loaded = await store.get_scene(scene.name)
assert loaded.stale_reference_kinds == ["master", "pano"]
await store.clear_scene_stale_reference_kind(scene.name, "master")
assert (await store.get_scene(scene.name)).stale_reference_kinds == ["pano"]
ctx = SimpleNamespace(owner_username="frank", project_name="demo")
payload = _scene_payload(loaded, ctx=ctx, project_dir=tmp_path)
assert payload["stale_reference_kinds"] == ["master", "pano"]
```

- [ ] **步骤 2：运行测试确认字段尚不存在**

运行：`.venv\Scripts\python.exe -m pytest tests\test_api_assets.py -q`

预期：FAIL，模型/API 没有 `stale_reference_kinds`。

- [ ] **步骤 3：实现向后兼容字段与 helper**

```python
class NovelScene(BaseModel):
    stale_reference_kinds: list[Literal["master", "reverse_master", "pano"]] = Field(default_factory=list)

ALTER TABLE scenes ADD COLUMN stale_reference_kinds_json TEXT NOT NULL DEFAULT '[]'

async def clear_scene_stale_reference_kind(self, name: str, kind: str) -> bool:
    scene = await self.get_scene(name)
    remaining = [item for item in scene.stale_reference_kinds if item != kind]
    return await self.update_scene(name, stale_reference_kinds=remaining)
```

`add_scene`、`add_scenes_atomic`、`update_scene` 和 `_row_to_scene` 使用 JSON 数组序列化；损坏或旧值读取为 `[]`。

- [ ] **步骤 4：运行相关存储和 API 测试**

运行：`.venv\Scripts\python.exe -m pytest tests\test_api_assets.py tests\test_asset_compiler_scene_enrichment.py -q`

预期：PASS。

- [ ] **步骤 5：提交数据合同**

```powershell
git add -- src/novelvideo/models.py src/novelvideo/sqlite_store.py src/novelvideo/api/routes/scenes.py tests/test_api_assets.py tests/test_asset_compiler_scene_enrichment.py
git commit -m "feat: 标记场景参考图提示词过期"
```

### 任务 5：新参考图采纳后清理对应过期状态

**文件：**
- 修改：`tests/test_scene_reference_runner.py`
- 修改：`src/novelvideo/task_backend/runners/scene_reference.py`

- [ ] **步骤 1：编写成功采纳和未采纳测试**

```python
assert calls["clear_stale"] == ("大厅", "master")

# 当 workflow_result["adoption_status"] 不是 provisional/approved/current，
# 或 canonical copy 未发生时，断言 clear helper 未调用。
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_reference_runner.py -q`

预期：FAIL，没有清理调用。

- [ ] **步骤 3：在 canonical 版本成功采纳后清理状态**

```python
if workflow_result["adoption_status"] in {"provisional", "approved"} and canonical_path.is_file():
    await store.clear_scene_stale_reference_kind(scene_name, kind)
```

`spatial_layout` 不映射到质量门的过期种类；`pano` 由全景生成 runner 使用同一 helper 清理。

- [ ] **步骤 4：运行 runner 测试确认通过**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_reference_runner.py tests\test_scene_reference_prompt.py -q`

预期：PASS。

- [ ] **步骤 5：提交清理逻辑**

```powershell
git add -- tests/test_scene_reference_runner.py src/novelvideo/task_backend/runners/scene_reference.py
git commit -m "fix: 更新场景参考图后清除过期标记"
```

### 任务 6：前端明确显示旧提示词参考图警告

**文件：**
- 修改：`frontend/src/types/scene.ts`
- 修改：`frontend/src/components/assets/scene-asset-card.tsx`
- 修改：`frontend/src/__tests__/components/assets/scene-asset-card.test.tsx`

- [ ] **步骤 1：编写卡片警告测试**

```tsx
renderCard({
  name: "广播站设备走廊",
  environment_prompt: VALID_PROMPT,
  master_url: "/old-master.png",
  stale_reference_kinds: ["master"],
});
expect(screen.getByText("提示词已更新，现有参考图来自旧提示词，请重新生成")).toBeInTheDocument();
expect(screen.getByText("源图需重生")).toBeInTheDocument();
```

再加一个 `stale_reference_kinds: []` 用例，断言没有警告。

- [ ] **步骤 2：运行前端测试确认失败**

运行：`pnpm --dir frontend test --run src/__tests__/components/assets/scene-asset-card.test.tsx`

预期：FAIL，类型和警告尚不存在。

- [ ] **步骤 3：实现类型与就地警告**

```ts
stale_reference_kinds?: Array<"master" | "reverse_master" | "pano">;
```

仅当对应 kind 同时存在 URL 与 stale 标记时显示“需重生”徽标；卡片存在至少一种旧图时显示统一中文警告，不把无图槽位描述为过期。

- [ ] **步骤 4：运行前端单测和类型检查**

运行：`pnpm --dir frontend test --run src/__tests__/components/assets/scene-asset-card.test.tsx`

运行：`pnpm --dir frontend typecheck`

预期：全部通过。

- [ ] **步骤 5：提交前端提示**

```powershell
git add -- frontend/src/types/scene.ts frontend/src/components/assets/scene-asset-card.tsx frontend/src/__tests__/components/assets/scene-asset-card.test.tsx
git commit -m "feat: 提示场景参考图需要重生"
```

### 任务 7：集中回归和本地真实链路验收

**文件：**
- 验证：上述所有修改文件

- [ ] **步骤 1：运行后端集中测试**

运行：`.venv\Scripts\python.exe -m pytest tests\test_scene_environment_prompt_quality.py tests\test_asset_compiler_scene_enrichment.py tests\test_scene_reference_runner.py tests\test_scene_reference_prompt.py tests\test_api_assets.py -q`

预期：全部 PASS。

- [ ] **步骤 2：运行前端集中测试和构建**

运行：`pnpm --dir frontend test --run src/__tests__/components/assets/scene-asset-card.test.tsx`

运行：`pnpm --dir frontend typecheck`

运行：`pnpm --dir frontend build`

预期：全部 PASS。

- [ ] **步骤 3：验证错误不再被任务包装器吞掉**

运行：`.venv\Scripts\python.exe -m pytest tests\test_asset_compiler_scene_enrichment.py -q -k "quality or partial or legacy"`

预期：质量错误向上传播，测试明确断言零部分写入。

- [ ] **步骤 4：检查变更卫生**

运行：`git diff --check`

运行：`git status --short`

预期：`git diff --check` 无输出；仅报告本任务文件和用户已有脏文件，不改写无关内容。

- [ ] **步骤 5：只读真机检查**

通过本地 API 读取 `test2 / 广播站设备走廊`，确认旧记录仍可识别为历史污染；不自动触发 LLM 或付费图片生成。实现完成后由用户点击“重新规划场景”，预期：有效提示词落库、旧图标记需重生；若两次生成无效则任务中心显示 `SCENE_PROMPT_QUALITY_FAILED`。

- [ ] **步骤 6：提交最终集成修正（仅当集中回归产生修正）**

对照 `git diff --name-only`，只把本计划“文件结构”中列出的且仍未提交的文件加入暂存区，然后运行 `git diff --cached --name-only` 复核；提交信息固定为 `test: 验证场景提示词质量链路`。若集中回归没有产生修正，不创建空提交。
