# 场景变体粒度收敛实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将自动场景变体规划从逐场次猜测改为整集同基础场景聚合决策，只保留跨至少两个场次块、影响整体环境且确需共享 plate 的最多两个变体。

**架构：** `AssetCompiler` 先照常解析、复用并补全基础场景，同时按规范化后的基础场景名收集场次块；完成基础场景遍历后，每个基础场景只调用一次派生分析器。模型返回可追溯的场次头、整体环境影响和共享 plate 必要性，确定性后处理校验证据、排除局部状态、去重排序并执行每集上限，最终菜单和绑定只看到收敛后的结果。

**技术栈：** Python 3.12、Pydantic、pytest、异步 `AssetCompiler`

---

## 文件结构

- 修改：`src/novelvideo/agents/asset_compiler.py` —— 扩展派生候选契约、聚合同基础场景场次、执行确定性筛选与上限。
- 修改：`tests/test_asset_compiler_scene_enrichment.py` —— 覆盖单块降级、跨块保留、证据校验、局部状态过滤和最多两个变体。
- 修改：`tests/test_newapi_text_gateway.py` —— 校验聚合分析仍使用既有 NewAPI 模型通道及整集场次输入。

### 任务 1：锁定派生候选契约和确定性门槛

**文件：**
- 修改：`tests/test_asset_compiler_scene_enrichment.py`
- 修改：`src/novelvideo/agents/asset_compiler.py:311-350,2034-2103`

- [ ] **步骤 1：编写失败的筛选测试**

```python
def test_derived_scene_specs_require_cross_block_global_plate_evidence():
    candidates = [
        DerivedSceneRequirement(label="暴雨积水版", evidence_scene_headers=["场次1"], affects_whole_environment=True, requires_shared_plate=True),
        DerivedSceneRequirement(label="油灯侧光版", evidence_scene_headers=["场次1", "场次2"], affects_whole_environment=False, requires_shared_plate=True),
        DerivedSceneRequirement(label="封控版", evidence_scene_headers=["场次1", "场次2"], affects_whole_environment=True, requires_shared_plate=True),
    ]
    result = AssetCompiler._build_derived_scene_specs(candidates, valid_scene_headers={"场次1", "场次2"})
    assert [item.label for item in result] == ["封控版"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_compiler_scene_enrichment.py::test_derived_scene_specs_require_cross_block_global_plate_evidence -q`

预期：FAIL，因为 `DerivedSceneRequirement` 尚无证据/范围字段，且筛选器不接受场次头集合。

- [ ] **步骤 3：实现最少契约与筛选代码**

```python
class DerivedSceneRequirement(BaseModel):
    label: str
    description: str = ""
    lighting: str = ""
    atmosphere: str = ""
    evidence_scene_headers: list[str] = Field(default_factory=list)
    affects_whole_environment: bool = False
    requires_shared_plate: bool = False

@classmethod
def _build_derived_scene_specs(cls, derived_scenes, *, valid_scene_headers):
    eligible = [
        item for item in derived_scenes
        if item.affects_whole_environment
        and item.requires_shared_plate
        and len(set(item.evidence_scene_headers) & valid_scene_headers) >= 2
    ]
    return cls._normalize_derived_scenes(eligible)[:2]
```

同时在标准化结果中保留新增字段，并对“显字/残字/满碑/侧光/开门/关门”等局部对象或瞬时状态执行安全过滤；候选按有效证据覆盖数降序排列，同一规范化标签仅保留一次。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_compiler_scene_enrichment.py::test_derived_scene_specs_require_cross_block_global_plate_evidence -q`

预期：PASS。

- [ ] **步骤 5：提交契约和门槛**

```bash
git add -p src/novelvideo/agents/asset_compiler.py tests/test_asset_compiler_scene_enrichment.py
git commit -m "fix: gate automatic scene variants"
```

### 任务 2：按基础场景聚合整集证据

**文件：**
- 修改：`tests/test_asset_compiler_scene_enrichment.py`
- 修改：`src/novelvideo/agents/asset_compiler.py:1274-1362,1589-1620`

- [ ] **步骤 1：编写失败的聚合编译测试**

```python
@pytest.mark.asyncio
async def test_compile_scenes_analyzes_each_base_once_with_all_blocks(monkeypatch):
    calls = []
    async def fake_derived(self, scene_name, blocks):
        calls.append((scene_name, [block.header_line for block in blocks]))
        return [DerivedSceneRequirement(label="封控版", evidence_scene_headers=[b.header_line for b in blocks], affects_whole_environment=True, requires_shared_plate=True)]
    menu, pending = await compiler._compile_scenes([first_block, second_block], episode, log)
    assert calls == [("谢家碑坊", [first_block.header_line, second_block.header_line])]
    assert [item.scene_id for item in menu] == ["谢家碑坊", "谢家碑坊_封控版"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_compiler_scene_enrichment.py::test_compile_scenes_analyzes_each_base_once_with_all_blocks -q`

预期：FAIL，因为当前实现逐块调用分析器并立即创建变体。

- [ ] **步骤 3：实现基础场景聚合和一次性分析**

```python
blocks_by_base: dict[str, list[SceneBlock]] = {}
base_scenes: dict[str, NovelScene] = {}
# 基础场景循环中：
blocks_by_base.setdefault(existing.name, []).append(block)
base_scenes[existing.name] = existing
# 循环结束后：
for base_name, blocks in blocks_by_base.items():
    requirements = await self._analyze_derived_scenes(base_name, blocks)
    headers = {str(block.header_line or "").strip() for block in blocks}
    for requirement in self._build_derived_scene_specs(requirements, valid_scene_headers=headers):
        # 创建/复用变体并加入 scene_menu
```

将 `_analyze_derived_scenes` 的参数改为 `blocks: list[SceneBlock]`，任务文本按场次头分段拼接整集证据；不足两个不同场次头时直接返回空列表。更新系统提示词，明确天气细节、局部积水、临时灯光、门窗动作、文字显隐和对象损坏进度留在叙事组/分镜提示词中。

- [ ] **步骤 4：运行场景编译测试**

运行：`uv run pytest tests/test_asset_compiler_scene_enrichment.py -q`

预期：PASS；旧的单块“暴雨版”测试已改为不生成变体，跨块“封控版”测试通过。

- [ ] **步骤 5：提交聚合编译**

```bash
git add -p src/novelvideo/agents/asset_compiler.py tests/test_asset_compiler_scene_enrichment.py
git commit -m "fix: aggregate episode scene variant evidence"
```

### 任务 3：验证模型通道、上限与绑定收敛

**文件：**
- 修改：`tests/test_newapi_text_gateway.py:210-252`
- 修改：`tests/test_asset_compiler_scene_enrichment.py`

- [ ] **步骤 1：更新模型通道测试为多场次输入**

```python
result = asyncio.run(compiler._analyze_derived_scenes("古董店", [first_block, second_block]))
assert result == []
assert "古董店 内 日" in captured_task
assert "古董店 内 夜" in captured_task
```

- [ ] **步骤 2：增加最多两个候选的回归测试**

```python
def test_derived_scene_specs_keep_only_two_highest_coverage_candidates():
    result = AssetCompiler._build_derived_scene_specs(
        [candidate_two_headers, candidate_four_headers, candidate_three_headers],
        valid_scene_headers={"场次1", "场次2", "场次3", "场次4"},
    )
    assert [item.label for item in result] == ["四场复用版", "三场复用版"]
```

- [ ] **步骤 3：运行定向回归**

运行：`uv run pytest tests/test_asset_compiler_scene_enrichment.py tests/test_newapi_text_gateway.py tests/test_task_episode_asset_bindings.py -q`

预期：PASS；最终场景菜单只包含基础场景及合格的最多两个变体，因此绑定层不会为被降级状态创建 `missing_asset`。

- [ ] **步骤 4：运行静态检查**

运行：`uv run ruff check src/novelvideo/agents/asset_compiler.py tests/test_asset_compiler_scene_enrichment.py tests/test_newapi_text_gateway.py`

预期：PASS 且无新增告警。

- [ ] **步骤 5：提交回归测试**

```bash
git add -p tests/test_newapi_text_gateway.py tests/test_asset_compiler_scene_enrichment.py
git commit -m "test: cover scene variant granularity"
```

