# 剧本专用知识图谱五集分组实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将批量剧集导入从 Cognee 默认逐块 `cognify()` 切换为每组最多 5 集、最多 6 组并发、失败组可续跑的剧本专用图谱管线，并让正文入库不等待图谱完成。

**架构：** `episode_import` 先通过现有 revision-safe repository 提交正文，随后入队独立的 `episode_graph_index`。图谱任务读取目标 revision 的变更集，按连续集号最多 5 集分组，用 DeepSeek 每组执行一次结构化抽取，检查点缓存成功组，本地确定性合并后把结果批量写入候选 Cognee runtime，最后沿用现有 pointer journal 原子激活。

**技术栈：** Python 3.11、Pydantic/PydanticAI、FastAPI task backend、SQLite、Cognee 1.0.5、pytest/pytest-asyncio、Ruff。

---

## 文件结构

- 创建 `src/novelvideo/episode_graph/models.py`：抽取输入、结构化输出、合并结果和进度 DTO。
- 创建 `src/novelvideo/episode_graph/grouping.py`：连续集号、最多 5 集的纯分组逻辑与稳定哈希。
- 创建 `src/novelvideo/episode_graph/checkpoints.py`：revision-scoped 原子检查点读写与失效校验。
- 创建 `src/novelvideo/episode_graph/extractor.py`：DeepSeek/PydanticAI 单组抽取和 6 组并发调度。
- 创建 `src/novelvideo/episode_graph/merge.py`：实体、事件、关系的确定性合并与来源保留。
- 创建 `src/novelvideo/episode_graph/writer.py`：候选 Cognee runtime 的来源替换、批量节点/边和 Embedding 写入。
- 创建 `src/novelvideo/episode_graph/service.py`：检查点、抽取、合并、候选写入和原子激活编排。
- 创建 `src/novelvideo/task_backend/runners/episode_graph.py`：独立 `episode_graph_index` task runner。
- 修改 `src/novelvideo/task_backend/runners/episode_import.py`：正文提交成功后入队图谱任务，不同步运行 Cognee。
- 修改 `src/novelvideo/task_backend/runners/__init__.py`：注册新 runner。
- 修改 `src/novelvideo/episode_source_store.py`：提供 revision-safe 的目标剧集读取接口。
- 修改 `frontend/src/lib/task-types.ts`：增加 `EPISODE_GRAPH_INDEX` 常量。
- 修改 `frontend/public/locales/zh/translation.json` 与 `frontend/public/locales/en/translation.json`：增加 `tasks.types.episode_graph_index` 名称。
- 新增 `tests/episode_graph/` 下的领域、抽取、检查点、合并、writer、service 和 runner 测试。

### 任务 1：定义剧集分组与抽取合同

**文件：**
- 创建：`src/novelvideo/episode_graph/__init__.py`
- 创建：`src/novelvideo/episode_graph/models.py`
- 创建：`src/novelvideo/episode_graph/grouping.py`
- 测试：`tests/episode_graph/test_grouping.py`

- [ ] **步骤 1：编写失败的分组测试**

```python
from novelvideo.episode_graph.grouping import group_episode_sources
from novelvideo.episode_graph.models import EpisodeGraphSource


def source(number: int) -> EpisodeGraphSource:
    return EpisodeGraphSource(number=number, title=f"E{number}", content=f"正文{number}", source_revision=1)


def test_groups_e2_to_e30_into_six_groups_of_at_most_five():
    groups = group_episode_sources([source(number) for number in range(2, 31)])
    assert [[item.number for item in group.episodes] for group in groups] == [
        [2, 3, 4, 5, 6], [7, 8, 9, 10, 11], [12, 13, 14, 15, 16],
        [17, 18, 19, 20, 21], [22, 23, 24, 25, 26], [27, 28, 29, 30],
    ]


def test_gap_starts_a_new_group():
    groups = group_episode_sources([source(2), source(3), source(8), source(9)])
    assert [[item.number for item in group.episodes] for group in groups] == [[2, 3], [8, 9]]
```

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_grouping.py -q`

预期：收集失败，`ModuleNotFoundError: novelvideo.episode_graph`。

- [ ] **步骤 3：实现最小 DTO 与纯分组函数**

```python
class EpisodeGraphSource(BaseModel):
    number: int = Field(gt=0)
    title: str
    content: str = Field(min_length=1)
    source_revision: int = Field(gt=0)


class EpisodeGraphGroup(BaseModel):
    first_episode: int
    last_episode: int
    episodes: tuple[EpisodeGraphSource, ...]
    content_hash: str


def group_episode_sources(sources: Sequence[EpisodeGraphSource], max_size: int = 5) -> list[EpisodeGraphGroup]:
    if max_size != 5:
        raise ValueError("episode graph max group size is fixed at 5")
    ordered = sorted(sources, key=lambda item: item.number)
    # 重复集号报错；遇到集号间隙或已有 5 集时结束当前组。
```

稳定哈希必须覆盖集号、source revision、标题和正文 SHA-256，不包含绝对路径。

- [ ] **步骤 4：运行分组测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_grouping.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph tests/episode_graph/test_grouping.py
git commit -m "feat(graph): add five-episode semantic grouping"
```

### 任务 2：实现 revision-scoped 成功组检查点

**文件：**
- 创建：`src/novelvideo/episode_graph/checkpoints.py`
- 测试：`tests/episode_graph/test_checkpoints.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_checkpoint_is_reused_only_for_matching_revision_and_hash(tmp_path):
    store = EpisodeGraphCheckpointStore(tmp_path)
    store.save_success(target_revision=2, group_key="e2-e6", content_hash="abc", result={"entities": []})
    assert store.load_success(target_revision=2, group_key="e2-e6", content_hash="abc") == {"entities": []}
    assert store.load_success(target_revision=3, group_key="e2-e6", content_hash="abc") is None
    assert store.load_success(target_revision=2, group_key="e2-e6", content_hash="changed") is None
```

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_checkpoints.py -q`

预期：导入 `EpisodeGraphCheckpointStore` 失败。

- [ ] **步骤 3：实现原子 JSON 检查点**

检查点路径固定为 `state/episode_graph/checkpoints/rev_<revision>/<group_key>.json`；使用 `mkstemp`、`flush`、`fsync`、`os.replace`。读取时同时校验 `schema_version == 1`、revision、group key 和 content hash；任何 JSON/校验异常返回 `None`，不删除其他成功组。

- [ ] **步骤 4：运行测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_checkpoints.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/checkpoints.py tests/episode_graph/test_checkpoints.py
git commit -m "feat(graph): persist revision scoped group checkpoints"
```

### 任务 3：实现每组一次 DeepSeek 结构化抽取和 6 组并发

**文件：**
- 创建：`src/novelvideo/episode_graph/extractor.py`
- 修改：`src/novelvideo/episode_graph/models.py`
- 测试：`tests/episode_graph/test_extractor.py`

- [ ] **步骤 1：编写失败测试，锁定一次调用与峰值 6**

```python
@pytest.mark.asyncio
async def test_extracts_each_group_once_with_peak_concurrency_six():
    active = peak = calls = 0

    async def invoke(group, prompt):
        nonlocal active, peak, calls
        calls += 1; active += 1; peak = max(peak, active)
        await asyncio.Event().wait() if False else asyncio.sleep(0.01)
        active -= 1
        return EpisodeGraphExtraction(group_key=group.key)

    results = await extract_groups(groups_of_2_to_30(), invoke=invoke, concurrency=6)
    assert calls == 6
    assert peak == 6
    assert len(results) == 6
```

再增加断言：提示词中每集各有一次 `<episode number="N">...</episode>`，不得出现按 Token 拆块的重复集号。

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_extractor.py -q`

预期：`extract_groups` 不存在。

- [ ] **步骤 3：实现抽取器**

```python
async def extract_groups(groups, *, invoke=None, concurrency: int = 6, on_group_event=None):
    semaphore = asyncio.Semaphore(concurrency)
    call = invoke or _invoke_deepseek

    async def one(group):
        async with semaphore:
            on_group_event and on_group_event("started", group)
            result = await call(group, build_group_prompt(group))
            on_group_event and on_group_event("completed", group)
            return result

    return await asyncio.gather(*(one(group) for group in groups), return_exceptions=True)
```

`_invoke_deepseek` 使用 `Agent(get_newapi_text_pydantic_model("EPISODE_GRAPH_MODEL", "deepseek-chat"), output_type=EpisodeGraphExtraction, retries=1)`。输出 DTO 明确包含：`entities`（character/identity/scene/prop）、`events`、`relations`，每项 `source_episodes: set[int]` 必须是当前组集号子集。

- [ ] **步骤 4：运行测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_extractor.py -q`

预期：全部 PASS，峰值恰好 6。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/models.py src/novelvideo/episode_graph/extractor.py tests/episode_graph/test_extractor.py
git commit -m "feat(graph): extract five-episode groups concurrently"
```

### 任务 4：实现确定性合并和来源冲突保留

**文件：**
- 创建：`src/novelvideo/episode_graph/merge.py`
- 测试：`tests/episode_graph/test_merge.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_merge_unions_sources_and_preserves_conflicting_aliases():
    merged = merge_extractions([
        extraction(entity("王总", kind="character", description="董事长", sources={2})),
        extraction(entity("王总", kind="character", description="总经理", sources={7})),
    ])
    item = merged.entities[0]
    assert item.source_episodes == {2, 7}
    assert item.attributes["description"] == ["董事长", "总经理"]
    assert merged.conflict_count == 1
```

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_merge.py -q`

预期：`merge_extractions` 不存在。

- [ ] **步骤 3：实现稳定键和合并**

实体键为 `kind + NFKC/空白/大小写规范化名称`；事件键为 `episode + ordinal`；关系键为 `source_key + relation_type + target_key + episode`。属性值相同只保留一次，不同值按首次出现顺序保存列表，并递增 conflict count。禁止在 merge 中调用 LLM。

- [ ] **步骤 4：运行测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_merge.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/merge.py tests/episode_graph/test_merge.py
git commit -m "feat(graph): merge episode graph results deterministically"
```

### 任务 5：实现候选 Cognee runtime 的来源替换与批量写入

**文件：**
- 创建：`src/novelvideo/episode_graph/writer.py`
- 测试：`tests/episode_graph/test_writer.py`

- [ ] **步骤 1：编写 writer 端口合同测试**

```python
@pytest.mark.asyncio
async def test_writer_removes_only_changed_episode_contributions():
    backend = InMemoryGraphBackend(shared_entity_sources={"character:王总": {1, 2}})
    writer = EpisodeGraphWriter(backend)
    await writer.replace_sources({2}, merged_graph_for_episode_2())
    assert backend.sources("character:王总") == {1, 2}
    assert backend.removed_source_contributions == {2}
    assert backend.embedding_batches > 0
```

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_writer.py -q`

预期：writer 模块不存在。

- [ ] **步骤 3：实现端口与 Cognee adapter**

```python
class GraphWriteBackend(Protocol):
    async def remove_episode_contributions(self, episodes: set[int]) -> None: ...
    async def upsert_entities(self, entities: Sequence[MergedEntity]) -> None: ...
    async def upsert_events(self, events: Sequence[MergedEvent]) -> None: ...
    async def upsert_relations(self, relations: Sequence[MergedRelation]) -> None: ...
    async def index_embeddings(self, *, batch_size: int = 64, on_batch=None) -> None: ...
```

Cognee adapter 在候选 runtime 中按 `source_episodes` 删除贡献；共享实体扣除被覆盖集号后仍有来源则保留。节点/边稳定 ID 不使用 Python `hash()`，使用 SHA-256/UUID5。Embedding 通过 Cognee vector engine 批量写入，每批最多 64 项，不调用文本 LLM。

- [ ] **步骤 4：运行测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_writer.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/writer.py tests/episode_graph/test_writer.py
git commit -m "feat(graph): write merged episode graph in batches"
```

### 任务 6：编排检查点续跑、候选图谱与原子激活

**文件：**
- 创建：`src/novelvideo/episode_graph/service.py`
- 修改：`src/novelvideo/episode_import_service.py`
- 测试：`tests/episode_graph/test_service.py`

- [ ] **步骤 1：编写部分失败续跑测试**

```python
@pytest.mark.asyncio
async def test_retry_calls_only_failed_group_and_activates_once(tmp_path):
    first = FakeExtractor(fail={"e7-e11"})
    service = make_service(tmp_path, extractor=first)
    with pytest.raises(EpisodeGraphGroupsFailed):
        await service.build(request_e2_e30())
    assert first.calls == ["e2-e6", "e7-e11", "e12-e16", "e17-e21", "e22-e26", "e27-e30"]

    second = FakeExtractor()
    await make_service(tmp_path, extractor=second).build(request_e2_e30())
    assert second.calls == ["e7-e11"]
    assert activation_count(tmp_path) == 1
```

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_service.py -q`

预期：service 模块不存在。

- [ ] **步骤 3：实现 service**

Service 流程固定为：校验当前 source revision → 分组 → 读取成功检查点 → 并发抽取缺失组 → 若有失败则抛出包含失败范围的稳定异常 → 本地合并 → clone active runtime → writer 替换来源并批量写入 → 使用 `CogneeShadowGraph.activate_shadow()` → revision journal 完成后 finalize。任何异常都 discard candidate 且不得修改 active pointer。

- [ ] **步骤 4：运行 service 与既有 pointer 事务测试**

运行：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_service.py tests\test_cognee_shadow_rebuild.py tests\test_episode_import_transaction.py -q
```

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/service.py src/novelvideo/episode_import_service.py tests/episode_graph/test_service.py
git commit -m "feat(graph): orchestrate resumable episode graph builds"
```

### 任务 7：正文快速提交并独立入队图谱任务

**文件：**
- 修改：`src/novelvideo/episode_source_store.py`
- 修改：`src/novelvideo/task_backend/runners/episode_import.py`
- 创建：`src/novelvideo/task_backend/runners/episode_graph.py`
- 修改：`src/novelvideo/task_backend/runners/__init__.py`
- 测试：`tests/test_task_episode_import_runner.py`
- 测试：`tests/episode_graph/test_runner.py`

- [ ] **步骤 1：编写失败合同测试**

```python
@pytest.mark.asyncio
async def test_episode_import_commits_sources_without_calling_graph(monkeypatch):
    graph = AsyncMock(side_effect=AssertionError("import must not wait for graph"))
    enqueue = AsyncMock(return_value=fake_queued_task("episode_graph_index"))
    result = await run_import_with_fakes(graph=graph, enqueue=enqueue)
    assert result["episodes_committed"] is True
    assert result["graph_task_type"] == "episode_graph_index"
    enqueue.assert_awaited_once()
```

再增加 graph runner 测试，断言 payload 携带 `target_revision`、changed episode numbers/content hashes，source revision 已变化时返回 `EPISODE_GRAPH_SOURCE_STALE`。

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\test_task_episode_import_runner.py tests\episode_graph\test_runner.py -q`

预期：旧 runner 同步调用图谱，或新 task type 未注册。

- [ ] **步骤 3：实现任务拆分**

`episode_import` 调用 repository 的 prepare/commit，不构造 `CogneeShadowGraph`；提交成功后调用 task backend 入队：

```python
await backend.enqueue_project_task(
    ctx,
    task_type="episode_graph_index",
    queue_kind="default",
    episode=0,
    scope=f"revision:{prepared.target_revision}",
    payload={
        "target_revision": prepared.target_revision,
        "episode_numbers": sorted(changed_numbers),
        "content_hashes": content_hashes,
    },
)
```

graph runner 调用 `EpisodeGraphBuildService.build()`，不得调用 `CogneeStore.ingest_novel_fast()` 或 `cognee.cognify()`。

- [ ] **步骤 4：运行 runner 测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\test_task_episode_import_runner.py tests\episode_graph\test_runner.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_source_store.py src/novelvideo/task_backend/runners/episode_import.py src/novelvideo/task_backend/runners/episode_graph.py src/novelvideo/task_backend/runners/__init__.py tests/test_task_episode_import_runner.py tests/episode_graph/test_runner.py
git commit -m "feat(import): decouple source commit from graph indexing"
```

### 任务 8：提供逐组进度、慢请求与可取消语义

**文件：**
- 修改：`src/novelvideo/episode_graph/extractor.py`
- 修改：`src/novelvideo/task_backend/runners/episode_graph.py`
- 测试：`tests/episode_graph/test_progress.py`

- [ ] **步骤 1：编写失败测试**

```python
@pytest.mark.asyncio
async def test_progress_reports_group_ranges_and_slow_groups(fake_clock):
    events = []
    await run_graph_task(groups=groups_of_2_to_30(), publish=events.append, slow_after_seconds=300)
    assert any(event.current_task == "已完成 3/6 组" for event in events)
    assert any(event.group_range == "E7-E11" for event in events)
```

增加取消测试：取消后未开始组不调用 extractor，已成功检查点仍存在。

- [ ] **步骤 2：运行测试验证 RED**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_progress.py -q`

预期：没有逐组事件或慢请求状态。

- [ ] **步骤 3：实现进度事件**

进度按阶段分配：分组 5%，抽取 5%–70%，合并 75%，Embedding 80%–95%，激活 98%。每组事件仅记录范围、字符数、耗时、重试次数和输出计数；不得记录正文、完整响应或凭据。使用现有 cancel watcher 传播 `CancelledError`。

- [ ] **步骤 4：运行测试验证 GREEN**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_progress.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```powershell
git add src/novelvideo/episode_graph/extractor.py src/novelvideo/task_backend/runners/episode_graph.py tests/episode_graph/test_progress.py
git commit -m "feat(graph): report resumable group progress"
```

### 任务 9：前端任务名称与导入完成提示

**文件：**
- 修改：`frontend/src/lib/task-types.ts`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`frontend/src/routes/_app/projects.$project/ingest.tsx`
- 测试：`frontend/src/__tests__/routes/ingest-settings-save.test.tsx`

- [ ] **步骤 1：编写失败测试**

测试断言正文任务完成后显示“剧本已导入，知识图谱正在后台构建”，并且 `episode_graph_index` 在任务中心显示为“构建剧集知识图谱”，不会让页面保持导入遮罩。

- [ ] **步骤 2：运行测试验证 RED**

运行：`cd frontend; corepack pnpm exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx`

预期：旧页面仍把 episode import completion 当作全部完成，或 task type 显示原始英文。

- [ ] **步骤 3：实现最小 UI 文案与映射**

仅增加新 task type 元数据和导入完成提示；图谱详细进度继续复用任务中心，不新建第二套轮询状态。

- [ ] **步骤 4：运行测试与 TypeScript 检查**

运行：

```powershell
cd frontend
corepack pnpm exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx
corepack pnpm exec tsc -b --pretty false
```

预期：测试 PASS，TypeScript exit 0。

- [ ] **步骤 5：提交**

```powershell
git add frontend/src frontend/public/locales
git commit -m "feat(import): show background graph indexing status"
```

### 任务 10：端到端合同与回归门禁

**文件：**
- 创建：`tests/episode_graph/test_episode_graph_integration.py`
- 修改：`tests/test_task_episode_import_runner.py`

- [ ] **步骤 1：编写 E2–E30 集成合同**

使用 fake DeepSeek invoke 和内存 graph backend，断言：正文提交先完成；随后图谱任务只生成 6 个组、主要 LLM 调用恰好 6 次、峰值并发 6、不调用 `cognee.cognify()`；模拟一组失败后重试只增加 1 次调用；最终 pointer 只激活一次。

- [ ] **步骤 2：运行集成测试验证 RED/GREEN 状态**

运行：`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests\episode_graph\test_episode_graph_integration.py -q`

预期：实现完成后全部 PASS；若测试首次写入时直接 PASS，必须检查测试是否真正 monkeypatch `cognee.cognify` 为抛错函数并统计 invoke 次数。

- [ ] **步骤 3：运行聚焦后端回归**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest tests\episode_graph tests\test_task_episode_import_runner.py tests\test_episode_source_store.py tests\test_episode_import_transaction.py tests\test_cognee_shadow_rebuild.py -q
.\.venv\Scripts\python.exe -m ruff check src\novelvideo\episode_graph src\novelvideo\task_backend\runners\episode_graph.py src\novelvideo\task_backend\runners\episode_import.py tests\episode_graph tests\test_task_episode_import_runner.py
git diff --check
```

预期：pytest 全部 PASS；Ruff 输出 `All checks passed!`；diff check exit 0。

- [ ] **步骤 4：运行前端聚焦回归**

```powershell
cd frontend
corepack pnpm exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx
corepack pnpm exec tsc -b --pretty false
```

预期：全部 exit 0。

- [ ] **步骤 5：提交最终合同**

```powershell
git add tests/episode_graph tests/test_task_episode_import_runner.py
git commit -m "test(graph): verify five-episode batch pipeline"
```

## 实施注意事项

- 不要通过修改 Cognee site-packages 实现本功能；所有 adapter 必须位于 `src/novelvideo`。
- 不要把 `COGNEE_LLM_CONCURRENCY` 从 2 改成 6 来伪装完成；新抽取器有独立且可测试的 6 组并发。
- 不要在正文 import task 内 `await` 图谱任务。
- 不要把完整 31 万字作为一个请求，也不要在 5 集组内再次按固定 Token 调用多次 LLM。
- 不要让成功检查点绕过 source revision/content hash 校验。
- 不要记录正文、API key、Authorization header 或完整模型响应。
