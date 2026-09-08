# “规划道具”Codex Agent 路由实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让“规划道具”只使用冻结的 Codex Agent 路由，不再要求普通文本 API Key。

**架构：** 为场景/道具规划注册独立文本任务角色，入队时冻结 Codex 路由并由任务执行 scope 注入。`AssetCompiler` 使用 `StructuredRuntimeAgent` 完成已有 Pydantic 结构化输出和校验，不保留普通文本回退。

**技术栈：** Python、Pydantic、Codex CLI structured output、pytest

---

## 文件职责

- 修改 `src/novelvideo/text_task_runtime/models.py`：提供场景/道具规划的 Codex 默认路由。
- 修改 `src/novelvideo/text_task_runtime/settings.py`：按任务角色选择默认路由。
- 修改 `src/novelvideo/api/routes/model_gateway.py`：公开“场景/道具规划”角色。
- 修改 `src/novelvideo/task_backend/runners/episode_assets.py`：将场景和道具规划任务注册到专用角色。
- 修改 `src/novelvideo/text_task_runtime/runtime.py`：让 runtime Agent 传递 Pydantic validation context。
- 修改 `src/novelvideo/agents/asset_compiler.py`：通过当前 Codex runtime 执行结构化规划。
- 修改 `tests/test_text_task_runtime_settings.py`、`tests/test_task_episode_asset_bindings.py`、`tests/test_asset_compiler_prop_planning.py`：覆盖默认路由、任务注册与无普通文本 Key 的规划路径。

### 任务 1：锁定专用 Codex 路由契约

**文件：**
- 测试：`tests/test_text_task_runtime_settings.py`
- 测试：`tests/test_task_episode_asset_bindings.py`
- 修改：`src/novelvideo/text_task_runtime/models.py`
- 修改：`src/novelvideo/text_task_runtime/settings.py`
- 修改：`src/novelvideo/api/routes/model_gateway.py`
- 修改：`src/novelvideo/task_backend/runners/episode_assets.py`

- [ ] **步骤 1：编写失败的默认路由和注册测试**

```python
def test_episode_asset_planning_defaults_to_codex():
    snapshot = resolve_configured_agent_task_route(ctx=ctx, task_role="episode_asset_planning")
    assert snapshot.runtime == "codex"
    assert snapshot.model == "gpt-5.6-sol"
    assert snapshot.reasoning_effort == "low"

def test_episode_prop_planner_uses_asset_planning_role():
    registration = get_project_task_runner_registration("episode_prop_planner")
    assert registration.text_task_role == "episode_asset_planning"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_text_task_runtime_settings.py tests/test_task_episode_asset_bindings.py -q`

预期：FAIL，角色仍采用普通文本默认值或任务没有 `text_task_role`。

- [ ] **步骤 3：实现按角色选择默认路由及任务注册**

```python
AGENT_TASK_ROLE_DEFAULTS = {
    "episode_asset_planning": AgentTaskRoute(
        runtime="codex",
        model="gpt-5.6-sol",
        reasoning_effort="low",
        fallback="stop",
    )
}
```

`resolve_configured_agent_task_route()` 从该映射取得基线；模型网关公开角色；两个 episode asset runner 使用 `text_task_role="episode_asset_planning"` 注册。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_text_task_runtime_settings.py tests/test_task_episode_asset_bindings.py -q`

预期：PASS。

### 任务 2：让道具分析只调用冻结 runtime

**文件：**
- 测试：`tests/test_asset_compiler_prop_planning.py`
- 修改：`src/novelvideo/text_task_runtime/runtime.py`
- 修改：`src/novelvideo/agents/asset_compiler.py`

- [ ] **步骤 1：编写失败的无普通文本 Key 测试**

```python
async def test_prop_planner_uses_scoped_codex_runtime(monkeypatch):
    monkeypatch.setattr(asset_compiler, "get_newapi_text_pydantic_model", fail_if_called)
    with text_task_runtime_scope(codex_snapshot, backend=fake_backend):
        result = await compiler._analyze_block_props(block, [], [])
    assert result[0].name == "石碑"
```

测试同时断言 validation context 仍进入 `BlockPropRequirements` 校验。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_compiler_prop_planning.py -q`

预期：FAIL，当前实现调用 `get_newapi_text_pydantic_model()`。

- [ ] **步骤 3：实现 scoped runtime Agent**

```python
runtime = current_text_task_runtime()
if runtime is None:
    raise RuntimeError("episode asset planning requires a frozen Agent route")
agent = StructuredRuntimeAgent(
    runtime,
    output_type=BlockPropRequirements,
    system_prompt=BLOCK_PROP_PROMPT,
    validation_context={
        "block_text": block_text,
        "allowed_existing_names": allowed_existing_names,
    },
)
```

`StructuredRuntimeAgent.run()` 对 runtime 原始结果再次调用 `model_validate(..., context=validation_context)`，保持已有证据约束。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_compiler_prop_planning.py tests/test_text_task_runtime_routing.py -q`

预期：PASS，且测试替身确认普通文本模型函数未被调用。

### 任务 3：回归验证

**文件：**
- 验证：上述所有修改文件

- [ ] **步骤 1：运行目标测试集**

运行：`uv run pytest tests/test_text_task_runtime_settings.py tests/test_text_task_runtime_routing.py tests/test_task_run_core_text_runtime.py tests/test_asset_compiler_prop_planning.py tests/test_task_episode_asset_bindings.py -q`

预期：全部 PASS。

- [ ] **步骤 2：运行静态检查**

运行：`uv run ruff check src/novelvideo/text_task_runtime src/novelvideo/agents/asset_compiler.py src/novelvideo/task_backend/runners/episode_assets.py tests/test_text_task_runtime_settings.py tests/test_text_task_runtime_routing.py tests/test_asset_compiler_prop_planning.py tests/test_task_episode_asset_bindings.py`

预期：无错误。

- [ ] **步骤 3：检查补丁边界**

运行：`git diff --check`

预期：无空白错误；只提交本计划列出的相关变更，保留工作区其他用户改动。
