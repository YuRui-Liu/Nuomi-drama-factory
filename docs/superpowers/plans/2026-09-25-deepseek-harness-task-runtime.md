# DeepSeek Harness 文本任务运行时 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为文本任务路由新增 `deepseek_harness` 运行时，并让切换运行时时按运行时记忆模型与推理强度。

**架构：** 在 `AgentTaskRoutingConfig` 顶层新增 `runtime_presets`（与 `routes` 并列、同一 KV key、一次原子写入），保持 `AgentTaskRoute` 及快照冻结逻辑不变。`deepseek_harness` 的模型与推理强度是运行时级统一值，由 `resolve_configured_agent_task_route` 收口处强制施加，前端只读只是表现层。harness 通过 `dsh --profile headless` 子进程执行，使用 Nuomi 专属 `DSH_HOME` 与独立 `settings.yaml`，不触碰用户的 `~/.dsh`。

**技术栈：** Python 3.11 / Pydantic v2 / pytest / asyncio subprocess；React + TypeScript / TanStack Query / Vitest + Testing Library。

**规格：** `docs/superpowers/specs/2026-09-25-deepseek-harness-task-runtime-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/novelvideo/text_task_runtime/models.py` | 修改 | 新增 `RuntimePreset`；`TextTaskRuntimeName` 增加成员；`AgentTaskRoutingConfig` 增加 `runtime_presets` |
| `src/novelvideo/text_task_runtime/settings.py` | 修改 | 内置 preset 默认、`runtime_preset_for`、`_clamp_harness_route` 收口 |
| `src/novelvideo/text_task_runtime/models_catalog.py` | 修改 | `_RUNTIMES` 增加 `deepseek_harness` 及其候选模型 |
| `src/novelvideo/text_task_runtime/deepseek_harness.py` | 创建 | harness 子进程适配器：命令解析、home 解析、settings 写入、argv、结构化解析、错误与取消处理 |
| `src/novelvideo/text_task_runtime/runtime.py` | 修改 | `build_text_task_runtime` 增加分发分支 |
| `src/novelvideo/api/routes/model_gateway.py` | 修改 | 请求体与响应体支持 `runtime_presets` |
| `frontend/src/lib/queries/model-gateway.ts` | 修改 | 前端类型与保存签名 |
| `frontend/src/components/settings/text-task-routing-panel.tsx` | 修改 | per-runtime 记忆、harness 行只读、harness 统一配置区 |
| `.env.example` | 修改 | 新增 harness 相关环境变量说明 |
| `tests/test_text_task_runtime_settings.py` | 修改 | preset 解析、clamp 不变量、旧格式兼容 |
| `tests/test_deepseek_harness_runtime.py` | 创建 | 适配器行为测试 |
| `frontend/src/__tests__/components/settings/workbuddy-routing.test.tsx` | 修改 | 断言旧「跨运行时保留同一模型」行为的部分必须更新 |
| `frontend/src/__tests__/components/settings/deepseek-harness-routing.test.tsx` | 创建 | per-runtime 记忆与 harness 只读测试 |

---

## 任务 1：数据模型 —— RuntimePreset 与 deepseek_harness

**文件：**
- 修改：`src/novelvideo/text_task_runtime/models.py`
- 测试：`tests/test_text_task_runtime_settings.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_text_task_runtime_settings.py` 末尾追加：

```python
def test_runtime_presets_round_trip_and_reject_unsafe_model():
    from novelvideo.text_task_runtime.models import (
        AgentTaskRoutingConfig,
        RuntimePreset,
    )

    config = AgentTaskRoutingConfig(
        runtime_presets={
            "deepseek_harness": RuntimePreset(
                model="deepseek-v4-flash-vision-exp",
                reasoning_effort="low",
            )
        }
    )
    reloaded = AgentTaskRoutingConfig.model_validate_json(config.model_dump_json())
    assert reloaded.runtime_presets["deepseek_harness"].model == "deepseek-v4-flash-vision-exp"
    assert reloaded.runtime_presets["deepseek_harness"].reasoning_effort == "low"

    with pytest.raises(ValidationError):
        RuntimePreset(model="  ")


def test_routing_config_without_runtime_presets_still_loads():
    from novelvideo.text_task_runtime.models import AgentTaskRoutingConfig

    legacy = '{"routes": {"director_plan": {"runtime": "codex", "model": "gpt-5.6-sol"}}}'
    config = AgentTaskRoutingConfig.model_validate_json(legacy)
    assert config.runtime_presets == {}
    assert config.routes["director_plan"].model == "gpt-5.6-sol"
```

确认文件顶部已导入 `pytest`；若未导入 `ValidationError`，在导入区补 `from pydantic import ValidationError`。

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_settings.py -q -k "runtime_presets or without_runtime_presets"`
预期：FAIL，`ImportError: cannot import name 'RuntimePreset'`

- [ ] **步骤 3：编写最少实现代码**

在 `src/novelvideo/text_task_runtime/models.py` 中：把 `TextTaskRuntimeName` 改为

```python
TextTaskRuntimeName = Literal["codex", "model_api", "workbuddy", "deepseek_harness"]
```

在 `AgentTaskRouteOverride` 之后、`AgentTaskRoutingConfig` 之前插入 `RuntimePreset`：

```python
class RuntimePreset(BaseModel):
    """按运行时记忆的模型与推理强度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    reasoning_effort: TextTaskReasoningEffort | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_text_task_model_name(value)
```

把 `AgentTaskRoutingConfig` 改为：

```python
class AgentTaskRoutingConfig(BaseModel):
    """Versioned mapping from logical task roles to route overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: dict[str, AgentTaskRouteOverride] = Field(default_factory=dict)
    runtime_presets: dict[TextTaskRuntimeName, RuntimePreset] = Field(default_factory=dict)
```

并在 `__init__.py` 的导入与 `__all__` 中加入 `RuntimePreset`。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_settings.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/text_task_runtime/models.py src/novelvideo/text_task_runtime/__init__.py tests/test_text_task_runtime_settings.py
git commit -m "feat(task-runtime): add RuntimePreset and deepseek_harness runtime name"
```

---

## 任务 2：preset 解析与 harness 统一性收口

**文件：**
- 修改：`src/novelvideo/text_task_runtime/settings.py`
- 测试：`tests/test_text_task_runtime_settings.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_text_task_runtime_settings.py` 末尾追加（`ctx` 用一个最小替身，`tmp_path` 由 pytest 注入）：

```python
def test_harness_route_is_clamped_to_runtime_preset(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import (
        AgentTaskRouteOverride,
        AgentTaskRoutingConfig,
        RuntimePreset,
    )
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="deepseek_harness",
                    model="whatever-the-role-asked-for",
                    reasoning_effort="high",
                )
            },
            runtime_presets={
                "deepseek_harness": RuntimePreset(
                    model="deepseek-v4-flash-vision-exp",
                    reasoning_effort="low",
                )
            },
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
        task_override=AgentTaskRouteOverride(model="task-level-attempt", reasoning_effort="xhigh"),
    )
    assert snapshot.runtime == "deepseek_harness"
    assert snapshot.model == "deepseek-v4-flash-vision-exp"
    assert snapshot.reasoning_effort == "low"


def test_harness_defaults_when_no_preset_saved(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import AgentTaskRouteOverride, AgentTaskRoutingConfig
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={"director_plan": AgentTaskRouteOverride(runtime="deepseek_harness")}
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path),
        task_role="director_plan",
    )
    assert snapshot.model == "deepseek-v4-flash-vision-exp"
    assert snapshot.reasoning_effort == "low"


def test_non_harness_route_is_not_clamped(tmp_path, monkeypatch):
    from novelvideo.text_task_runtime.models import AgentTaskRouteOverride, AgentTaskRoutingConfig
    from novelvideo.text_task_runtime import settings as runtime_settings

    monkeypatch.setattr(
        runtime_settings,
        "load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="codex", model="gpt-5.6-sol", reasoning_effort="medium"
                )
            }
        ),
    )
    monkeypatch.setattr(
        runtime_settings, "load_project_routes", lambda ctx: AgentTaskRoutingConfig()
    )

    snapshot = runtime_settings.resolve_configured_agent_task_route(
        ctx=SimpleNamespace(state_dir=tmp_path), task_role="director_plan"
    )
    assert snapshot.model == "gpt-5.6-sol"
    assert snapshot.reasoning_effort == "medium"
```

确认文件顶部有 `from types import SimpleNamespace`，没有则补上。

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_settings.py -q -k harness`
预期：FAIL，`AttributeError: module ... has no attribute '_clamp_harness_route'` 或断言 `snapshot.model` 不等于 `deepseek-v4-flash-vision-exp`

- [ ] **步骤 3：编写最少实现代码**

在 `src/novelvideo/text_task_runtime/settings.py` 的 `_ROLE_DEFAULTS` 之后加入：

```python
_RUNTIME_PRESET_DEFAULTS: dict[str, RuntimePreset] = {
    "deepseek_harness": RuntimePreset(
        model="deepseek-v4-flash-vision-exp",
        reasoning_effort="low",
    ),
}


def runtime_preset_for(
    config: AgentTaskRoutingConfig, runtime: str
) -> RuntimePreset | None:
    """用户保存的 preset 优先，回落内置默认，两者皆无返回 None。"""

    saved = config.runtime_presets.get(runtime)
    if saved is not None:
        return saved
    return _RUNTIME_PRESET_DEFAULTS.get(runtime)


def _clamp_harness_route(
    snapshot: AgentTaskRouteSnapshot,
    config: AgentTaskRoutingConfig,
) -> AgentTaskRouteSnapshot:
    """deepseek_harness 的模型与推理强度由运行时级 preset 唯一决定。"""

    if snapshot.runtime != "deepseek_harness":
        return snapshot
    preset = runtime_preset_for(config, "deepseek_harness")
    if preset is None:
        return snapshot
    return snapshot.model_copy(
        update={
            "model": preset.model,
            "reasoning_effort": preset.reasoning_effort,
        }
    )
```

同时把 `RuntimePreset` 加入该文件的 `from novelvideo.text_task_runtime.models import (...)` 导入列表。

把 `resolve_configured_agent_task_route` 整体替换为：

```python
def resolve_configured_agent_task_route(
    *,
    ctx: Any,
    task_role: str,
    task_override: AgentTaskRouteOverride | dict[str, Any] | None = None,
) -> AgentTaskRouteSnapshot:
    """Resolve persisted routes once, at enqueue time."""

    global_config = load_global_routes()
    global_override = global_config.routes.get(task_role)
    global_route = default_agent_task_route(task_role)
    if global_override is not None:
        global_route = _apply_override(global_route, global_override)
    project_override = load_project_routes(ctx).routes.get(task_role)
    parsed_task_override = (
        AgentTaskRouteOverride.model_validate(task_override)
        if task_override is not None
        else None
    )
    snapshot = resolve_agent_task_route(
        task_role=task_role,
        global_route=global_route,
        project_override=project_override,
        task_override=parsed_task_override,
    )
    return _clamp_harness_route(snapshot, global_config)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_settings.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/text_task_runtime/settings.py tests/test_text_task_runtime_settings.py
git commit -m "feat(task-runtime): clamp deepseek_harness routes to a runtime-level preset"
```

---

## 任务 3：API 契约支持 runtime_presets

**文件：**
- 修改：`src/novelvideo/api/routes/model_gateway.py:106-107, 414-458`
- 测试：`tests/test_api_model_gateway_task_runtime.py`（若不存在则创建）

- [ ] **步骤 1：编写失败的测试**

创建或追加到 `tests/test_api_model_gateway_task_runtime.py`：

```python
def test_task_runtime_config_round_trips_runtime_presets(client, admin_headers):
    from novelvideo.text_task_runtime.settings import load_global_routes

    payload = {
        "routes": {
            "director_plan": {
                "runtime": "deepseek_harness",
                "model": "ignored-placeholder",
                "reasoning_effort": None,
                "skill_id": None,
                "skill_version": None,
                "fallback": "stop",
            }
        },
        "runtime_presets": {
            "deepseek_harness": {
                "model": "deepseek-v4-flash-vision-exp",
                "reasoning_effort": "low",
            }
        },
    }
    response = client.put(
        "/api/v1/model-gateway/task-runtime/config", json=payload, headers=admin_headers
    )
    assert response.status_code == 200

    saved = load_global_routes()
    assert saved.runtime_presets["deepseek_harness"].model == "deepseek-v4-flash-vision-exp"

    fetched = client.get("/api/v1/model-gateway/task-runtime/config", headers=admin_headers)
    data = fetched.json()["data"]
    assert data["runtime_presets"]["deepseek_harness"]["reasoning_effort"] == "low"
    harness_role = next(r for r in data["roles"] if r["id"] == "director_plan")
    assert harness_role["route"]["model"] == "deepseek-v4-flash-vision-exp"
```

`client` 与 `admin_headers` fixture 沿用同目录既有 API 测试的写法；若该文件已存在，直接复用其 fixture 与导入，不要新建第二套。

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_api_model_gateway_task_runtime.py -q -k runtime_presets`
预期：FAIL，`KeyError: 'runtime_presets'`

- [ ] **步骤 3：编写最少实现代码**

`src/novelvideo/api/routes/model_gateway.py` 的导入区加入 `RuntimePreset`：

```python
from novelvideo.text_task_runtime.models import (
    AgentTaskRoute,
    AgentTaskRoutingConfig,
    RuntimePreset,
)
```

把 `TaskRuntimeConfigBody` 改为：

```python
class TaskRuntimeConfigBody(BaseModel):
    routes: dict[str, AgentTaskRoute]
    runtime_presets: dict[TextTaskRuntimeName, RuntimePreset] | None = None
```

（若未导入 `TextTaskRuntimeName`，改为 `dict[str, RuntimePreset] | None`。）

把 `_task_runtime_config_payload()` 改为：

```python
def _task_runtime_config_payload() -> dict[str, Any]:
    configured = load_global_routes()
    return {
        "roles": [
            {
                "id": role,
                "label": label,
                "route": (
                    default_agent_task_route(role).model_copy(
                        update=configured.routes.get(role).model_dump(exclude_none=True)
                    )
                    if configured.routes.get(role) is not None
                    else default_agent_task_route(role)
                ).model_dump(mode="json"),
            }
            for role, label in TEXT_TASK_ROLE_LABELS.items()
        ],
        "runtime_presets": {
            name: preset.model_dump(mode="json")
            for name in ("codex", "model_api", "workbuddy", "deepseek_harness")
            if (preset := runtime_preset_for(configured, name)) is not None
        },
    }
```

并在该文件的 `from novelvideo.text_task_runtime.settings import (...)` 中加入 `runtime_preset_for`。

把 `put_task_runtime_config` 构造配置的部分改为：

```python
    config = AgentTaskRoutingConfig(
        routes={
            role: route.model_dump(mode="json")
            for role, route in body.routes.items()
        },
        runtime_presets=body.runtime_presets or {},
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_api_model_gateway_task_runtime.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/api/routes/model_gateway.py tests/test_api_model_gateway_task_runtime.py
git commit -m "feat(api): accept and return task runtime presets"
```

---

## 任务 4：模型候选目录加入 deepseek_harness

**文件：**
- 修改：`src/novelvideo/text_task_runtime/models_catalog.py:34, 107-130`
- 测试：`tests/test_text_task_runtime_models_catalog.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_text_task_runtime_models_catalog.py` 追加：

```python
def test_catalog_exposes_deepseek_harness_models():
    from novelvideo.text_task_runtime.models_catalog import get_runtime_model_catalog

    catalog = get_runtime_model_catalog()
    assert "deepseek_harness" in catalog
    assert "deepseek-v4-flash-vision-exp" in catalog["deepseek_harness"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_models_catalog.py -q -k deepseek_harness`
预期：FAIL，`KeyError: 'deepseek_harness'`

- [ ] **步骤 3：编写最少实现代码**

把 `models_catalog.py` 第 34 行改为：

```python
_RUNTIMES = ("workbuddy", "codex", "model_api", "deepseek_harness")
```

在第 33 行 `_CODEX_DEFAULT_MODELS` 之后新增：

```python
_DEEPSEEK_HARNESS_DEFAULT_MODELS = ("deepseek-v4-flash-vision-exp",)
```

在 `_default_models_for`（第 102-111 行）的 `codex` 分支之后新增：

```python
    if runtime == "deepseek_harness":
        return list(_DEEPSEEK_HARNESS_DEFAULT_MODELS)
```

并在模块 docstring 的运行时说明列表末尾追加：

```
* **deepseek_harness**: 模型由运行时级 preset 决定，内置默认仅作为下拉候选，
  可用 ``TEXT_TASK_DEFAULT_MODELS_DEEPSEEK_HARNESS`` 覆盖。
```

无需改动 `get_runtime_model_catalog()`：它已按 `_RUNTIMES` 遍历，`deepseek_harness` 会自动出现在返回结果中；`_default_models_for` 的环境变量分支（`TEXT_TASK_DEFAULT_MODELS_{RUNTIME}`）也已天然支持新运行时。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_text_task_runtime_models_catalog.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/text_task_runtime/models_catalog.py tests/test_text_task_runtime_models_catalog.py
git commit -m "feat(task-runtime): list deepseek_harness model candidates"
```

---

## 任务 5：DeepSeek Harness 子进程适配器

**文件：**
- 创建：`src/novelvideo/text_task_runtime/deepseek_harness.py`
- 测试：`tests/test_deepseek_harness_runtime.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_deepseek_harness_runtime.py`：

```python
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import build_text_task_runtime
from novelvideo.text_task_runtime import deepseek_harness


class Answer(BaseModel):
    value: str


def runtime(model='deepseek-v4-flash-vision-exp', effort='low'):
    return build_text_task_runtime(
        AgentTaskRouteSnapshot(
            runtime='deepseek_harness',
            model=model,
            reasoning_effort=effort,
            task_role='director_plan',
            source='global',
        )
    )


def _spawn(payload, returncode=0):
    proc = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(payload, b'')))
    return proc, AsyncMock(return_value=proc)


@pytest.mark.asyncio
async def test_harness_runs_headless_with_isolated_home(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: tmp_path / 'dsh')
    proc, spawn = _spawn(b'{"value": "ok"}')
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', spawn)

    result = await runtime().run_structured(
        prompt='task', system_prompt='system', output_type=Answer
    )
    assert result.value == 'ok'

    argv = spawn.call_args.args
    assert argv[0] == '/app/dsh'
    assert argv[1:3] == ('--profile', 'headless')
    assert 'task' in argv[-1]

    env = spawn.call_args.kwargs['env']
    assert env['DSH_HOME'] == str(tmp_path / 'dsh')


@pytest.mark.asyncio
async def test_harness_writes_settings_document(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    home = tmp_path / 'dsh'
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: home)
    proc, spawn = _spawn(b'{"value": "ok"}')
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', spawn)

    await runtime().run_structured(prompt='task', output_type=Answer)

    text = (home / 'settings.yaml').read_text(encoding='utf-8')
    assert 'provider: deepseek-official' in text
    assert 'model: deepseek-v4-flash-vision-exp' in text
    assert 'reasoningEffort: low' in text


@pytest.mark.asyncio
async def test_harness_appends_schema_to_request(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: tmp_path / 'dsh')
    proc, spawn = _spawn(b'{"value": "ok"}')
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', spawn)

    await runtime().run_structured(prompt='do it', output_type=Answer)

    request = spawn.call_args.args[-1]
    assert 'do it' in request
    assert json.dumps(Answer.model_json_schema(), ensure_ascii=False) in request


@pytest.mark.asyncio
async def test_harness_rejects_images():
    with pytest.raises(KnowledgeRuntimeError, match='图片输入'):
        await runtime().run_structured(prompt='task', output_type=Answer, images=[object()])


def test_harness_missing_binary_reports_install_hint(monkeypatch):
    monkeypatch.delenv('DSH_BIN', raising=False)
    monkeypatch.setattr(deepseek_harness.shutil, 'which', lambda name: None)
    with pytest.raises(KnowledgeRuntimeError, match='dsh'):
        deepseek_harness.dsh_command()


@pytest.mark.asyncio
async def test_harness_failure_does_not_expose_process_output(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: tmp_path / 'dsh')
    proc, spawn = _spawn(b'secret', returncode=1)
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert 'secret' not in str(error.value)


@pytest.mark.asyncio
async def test_harness_invalid_output_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: tmp_path / 'dsh')
    proc, spawn = _spawn(b'not json at all')
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert error.value.code == 'DSH_OUTPUT_INVALID'


@pytest.mark.asyncio
async def test_harness_cancellation_cleans_up_process(monkeypatch, tmp_path):
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/app/dsh')
    monkeypatch.setattr(deepseek_harness, 'harness_home', lambda: tmp_path / 'dsh')
    proc = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(deepseek_harness.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    terminate = AsyncMock()
    monkeypatch.setattr(deepseek_harness, 'terminate_process_tree', terminate)
    with pytest.raises(asyncio.CancelledError):
        await runtime().run_structured(prompt='task', output_type=Answer)
    terminate.assert_awaited_once_with(proc)
```

`pyyaml` 不是本仓库的依赖，因此该用例断言文件文本而非解析 YAML。

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_deepseek_harness_runtime.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'novelvideo.text_task_runtime.deepseek_harness'`

- [ ] **步骤 3：编写最少实现代码**

创建 `src/novelvideo/text_task_runtime/deepseek_harness.py`：

```python
"""Structured text tasks through the DeepSeek Harness one-shot CLI."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

from novelvideo.knowledge_runtime.codex import build_codex_process_env, _process_group_kwargs
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

PROVIDER_ROUTE = 'deepseek-official'


def dsh_command() -> str:
    """Locate the dsh executable: DSH_BIN first, then PATH."""

    explicit = os.getenv('DSH_BIN')
    candidates = [explicit] if explicit else [shutil.which('dsh')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise KnowledgeRuntimeError(
        '未找到 dsh 可执行文件，请安装 DeepSeek Harness 或设置 DSH_BIN。',
        code='DSH_NOT_INSTALLED',
    )


def harness_home() -> Path:
    """Nuomi-owned harness home; never the user's ~/.dsh."""

    configured = os.getenv('NOVELVIDEO_DSH_HOME', '').strip()
    if configured:
        return Path(configured).expanduser()
    from novelvideo.config import STATE_DIR

    return Path(STATE_DIR) / 'dsh'


def _write_settings(home: Path, model: str, reasoning_effort: str | None) -> None:
    """Atomically write the agent-default-model settings section."""

    home.mkdir(parents=True, exist_ok=True)
    lines = [
        'agent-default-model:',
        f'  provider: {PROVIDER_ROUTE}',
        f'  model: {model}',
    ]
    if reasoning_effort:
        lines.append(f'  reasoningEffort: {reasoning_effort}')
    document = '\n'.join(lines) + '\n'

    target = home / 'settings.yaml'
    handle, temp_name = tempfile.mkstemp(dir=str(home), prefix='.settings-', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(document)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


class DeepSeekHarnessStructuredRuntime:
    def __init__(self, snapshot):
        if snapshot.runtime != 'deepseek_harness':
            raise ValueError('DeepSeekHarnessStructuredRuntime requires a deepseek_harness route')
        if snapshot.skill_id or snapshot.skill_version:
            raise ValueError('DeepSeek harness routes do not support skill overrides')
        self.snapshot = snapshot

    async def run_structured(self, *, prompt, output_type, system_prompt='', validation_context=None, images=None):
        if images:
            raise KnowledgeRuntimeError(
                '此 DeepSeek Harness 文本运行时不支持图片输入；视觉检查请使用 Codex 或模型 API。',
                code='DSH_IMAGES_UNSUPPORTED',
            )
        schema = None if output_type is str else output_type.model_json_schema()
        request = f'{system_prompt}\n\n{prompt}'
        if schema:
            request += '\nReturn only JSON matching this schema:\n' + json.dumps(schema, ensure_ascii=False)

        home = harness_home()
        _write_settings(home, self.snapshot.model, self.snapshot.reasoning_effort)

        argv = [dsh_command(), '--profile', 'headless', request]
        timeout = int(os.getenv('DSH_EXEC_TIMEOUT_SECONDS', '600'))
        if timeout <= 0:
            raise ValueError('DeepSeek Harness timeout must be positive')

        env = build_codex_process_env(environ={**os.environ, 'DSH_HOME': str(home)})
        with tempfile.TemporaryDirectory(prefix='nuomi-dsh-') as cwd:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv, cwd=cwd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env, **_process_group_kwargs(),
                )
            except OSError:
                raise KnowledgeRuntimeError(
                    'DeepSeek Harness 无法启动，请检查 dsh 安装与 Node.js。',
                    code='DSH_START_FAILED',
                ) from None
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(b''), timeout=timeout)
            except BaseException:
                await terminate_process_tree(process)
                raise

        if process.returncode != 0:
            raise KnowledgeRuntimeError(
                'DeepSeek Harness 执行失败，请检查凭据、模型权限及余额。',
                code='DSH_EXEC_FAILED',
            )
        text = (stdout or b'').decode('utf-8', 'replace').strip()
        if output_type is str:
            return text
        try:
            value = text
            if value.startswith('```') and value.endswith('```'):
                value = value.split('\n', 1)[1].rsplit('```', 1)[0]
            return output_type.model_validate_json(value, context=validation_context)
        except (ValueError, TypeError):
            raise KnowledgeRuntimeError(
                'DeepSeek Harness 未返回符合要求的结构化结果。',
                code='DSH_OUTPUT_INVALID',
            ) from None
```

若 `terminate_process_tree` 或 `_process_group_kwargs` 的实际导入路径与 `workbuddy.py` 不同，以 `workbuddy.py` 的导入为准。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_deepseek_harness_runtime.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/text_task_runtime/deepseek_harness.py tests/test_deepseek_harness_runtime.py
git commit -m "feat(task-runtime): run structured tasks through dsh headless"
```

---

## 任务 6：运行时分发分支

**文件：**
- 修改：`src/novelvideo/text_task_runtime/runtime.py:188-194`
- 测试：`tests/test_deepseek_harness_runtime.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_deepseek_harness_runtime.py` 追加：

```python
def test_build_text_task_runtime_selects_harness():
    built = runtime()
    assert type(built).__name__ == 'DeepSeekHarnessStructuredRuntime'


def test_harness_runtime_rejects_skill_override():
    with pytest.raises(ValueError, match='skill'):
        build_text_task_runtime(
            AgentTaskRouteSnapshot(
                runtime='deepseek_harness',
                model='deepseek-v4-flash-vision-exp',
                task_role='director_plan',
                source='global',
                skill_id='some-skill',
            )
        )
```

（`skill_id` 若被 `AgentTaskRouteSnapshot` 的校验拒绝，则删除第二个测试，改为直接断言构造器行为。）

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_deepseek_harness_runtime.py -q -k selects_harness`
预期：FAIL，`type(built).__name__ == 'ModelApiStructuredRuntime'`

- [ ] **步骤 3：编写最少实现代码**

把 `runtime.py` 的 `build_text_task_runtime` 改为：

```python
def build_text_task_runtime(snapshot: AgentTaskRouteSnapshot) -> StructuredTextRuntime:
    if snapshot.runtime == "workbuddy":
        from .workbuddy import WorkBuddyStructuredRuntime
        return WorkBuddyStructuredRuntime(snapshot)
    if snapshot.runtime == "deepseek_harness":
        from .deepseek_harness import DeepSeekHarnessStructuredRuntime
        return DeepSeekHarnessStructuredRuntime(snapshot)
    if snapshot.runtime == "codex":
        return CodexStructuredRuntime(snapshot)
    return ModelApiStructuredRuntime(snapshot)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_deepseek_harness_runtime.py tests/test_workbuddy_runtime.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/text_task_runtime/runtime.py tests/test_deepseek_harness_runtime.py
git commit -m "feat(task-runtime): dispatch deepseek_harness routes"
```

---

## 任务 7：前端查询层类型与保存签名

**文件：**
- 修改：`frontend/src/lib/queries/model-gateway.ts:30-31, 59-61, 357-368`

- [ ] **步骤 1：编写失败的测试**

在 `frontend/src/__tests__/components/settings/deepseek-harness-routing.test.tsx` 中先只写一个类型层断言（该文件在任务 8 扩充）：

```tsx
import { expect, it } from 'vitest';

it('exposes deepseek_harness as a task runtime name', async () => {
  const module = await import('@/lib/queries/model-gateway');
  const names: Array<module.TaskRuntimeName> = ['codex', 'model_api', 'workbuddy', 'deepseek_harness'];
  expect(names).toHaveLength(4);
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && pnpm vitest run src/__tests__/components/settings/deepseek-harness-routing.test.tsx`
预期：FAIL，TypeScript 报 `'"deepseek_harness"' is not assignable to type 'TaskRuntimeName'`

- [ ] **步骤 3：编写最少实现代码**

在 `frontend/src/lib/queries/model-gateway.ts` 中：

```ts
export type TaskRuntimeName = "codex" | "model_api" | "workbuddy" | "deepseek_harness";
```

新增接口：

```ts
export interface RuntimePreset {
  model: string;
  reasoning_effort: TaskReasoningEffort | null;
}
```

扩展配置类型：

```ts
export interface TaskRuntimeConfig {
  roles: TaskRuntimeRole[];
  runtime_presets: Partial<Record<TaskRuntimeName, RuntimePreset>>;
}
```

替换保存 hook：

```ts
export interface SaveTaskRuntimeInputPayload {
  routes: Record<string, AgentTaskRoute>;
  runtime_presets: Record<string, RuntimePreset>;
}

export function useSaveTaskRuntimeConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveTaskRuntimeInputPayload) =>
      api
        .put("api/v1/model-gateway/task-runtime/config", { json: input })
        .json<OkResponse<TaskRuntimeConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.taskRuntime() });
    },
  });
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && pnpm vitest run src/__tests__/components/settings/deepseek-harness-routing.test.tsx`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/queries/model-gateway.ts frontend/src/__tests__/components/settings/deepseek-harness-routing.test.tsx
git commit -m "feat(web): type task runtime presets and deepseek_harness"
```

---

## 任务 8：前端面板 per-runtime 记忆与 harness 只读

**文件：**
- 修改：`frontend/src/components/settings/text-task-routing-panel.tsx`
- 修改：`frontend/src/__tests__/components/settings/workbuddy-routing.test.tsx`
- 测试：`frontend/src/__tests__/components/settings/deepseek-harness-routing.test.tsx`

- [ ] **步骤 1：编写失败的测试**

把 `frontend/src/__tests__/components/settings/deepseek-harness-routing.test.tsx` 补全为（`vi.mock` 的 catalog 需包含 `deepseek_harness` 键，`data.data` 需包含 `runtime_presets`）：

```tsx
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({ ok: true }),
  data: { data: {
    roles: [{ id: 'director_plan', label: '导演规划', route: {
      runtime: 'codex', model: 'gpt-5.6-sol', reasoning_effort: 'medium',
      skill_id: null, skill_version: null, fallback: 'stop',
    } }],
    runtime_presets: {
      deepseek_harness: { model: 'deepseek-v4-flash-vision-exp', reasoning_effort: 'low' },
    },
  } },
  catalog: { data: {
    codex: ['gpt-5.6-sol'], workbuddy: ['default-model'], model_api: [],
    deepseek_harness: ['deepseek-v4-flash-vision-exp'],
  } },
}));
vi.mock('@/lib/queries/model-gateway', () => ({
  TASK_REASONING_EFFORTS: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
  useTaskRuntimeConfig: () => ({ data: mocks.data }),
  useTaskRuntimeModels: () => ({ data: mocks.catalog }),
  useSaveTaskRuntimeConfig: () => ({ mutateAsync: mocks.save, isPending: false }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TextTaskRoutingPanel } from '@/components/settings/text-task-routing-panel';
afterEach(cleanup);

it('remembers per-runtime model and makes harness read-only', async () => {
  const user = userEvent.setup();
  render(<TextTaskRoutingPanel open />);
  const runtimeTrigger = () => screen.getByRole('combobox', { name: '导演规划执行运行时' });
  const modelTrigger = () => screen.getByLabelText('导演规划模型');

  // 切到 WorkBuddy，手填一个模型。
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'WorkBuddy' }));
  await user.click(modelTrigger());
  await user.click(screen.getByLabelText('导演规划模型编辑'));
  fireEvent.change(screen.getByLabelText('导演规划模型编辑'), { target: { value: 'wb-model' } });
  fireEvent.keyDown(screen.getByLabelText('导演规划模型编辑'), { key: 'Enter' });

  // 切回 Codex：应恢复 Codex 自己的记忆值，而不是 wb-model。
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'Codex' }));
  expect(modelTrigger()).toHaveTextContent('gpt-5.6-sol');

  // 再切回 WorkBuddy：应恢复 wb-model。
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'WorkBuddy' }));
  expect(modelTrigger()).toHaveTextContent('wb-model');

  // 切到 Harness：模型控件只读，显示运行时级统一值。
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'DeepSeek Harness' }));
  expect(modelTrigger()).toHaveTextContent('deepseek-v4-flash-vision-exp');
  expect(screen.getByLabelText('导演规划模型')).toBeDisabled();
});

it('submits runtime presets on save', async () => {
  const user = userEvent.setup();
  render(<TextTaskRoutingPanel open />);
  await user.click(screen.getByRole('button', { name: '保存任务路由' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(
    expect.objectContaining({
      runtime_presets: expect.objectContaining({
        deepseek_harness: expect.objectContaining({ model: 'deepseek-v4-flash-vision-exp' }),
      }),
    }),
  ));
});
```

把 `frontend/src/__tests__/components/settings/workbuddy-routing.test.tsx` 中**第 55–65 行**（「用户手填过模型后，切换运行时不再覆盖」以及切回后断言 `my-workbuddy-model` 的部分）替换为按运行时记忆的断言：切到 Codex 后 `modelTrigger()` 应为 Codex 的记忆值，再切回 WorkBuddy 才恢复 `my-workbuddy-model`。同时更新该文件的 `vi.mock` 数据，使其 `data.data` 带上 `runtime_presets: {}`、`catalog` 带上 `deepseek_harness: []`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && pnpm vitest run src/__tests__/components/settings/deepseek-harness-routing.test.tsx`
预期：FAIL，模型框仍显示 `gpt-5.6-sol`（未按运行时记忆）、且找不到 `DeepSeek Harness` 选项

- [ ] **步骤 3：编写最少实现代码**

在 `text-task-routing-panel.tsx` 中：

顶部常量与辅助：

```tsx
const DEFAULT_HARNESS_MODEL = "deepseek-v4-flash-vision-exp";

function defaultModelFor(runtime: TaskRuntimeName): string {
  if (runtime === "workbuddy") return DEFAULT_WORKBUDDY_MODEL;
  if (runtime === "codex") return DEFAULT_CODEX_MODEL;
  if (runtime === "deepseek_harness") return DEFAULT_HARNESS_MODEL;
  return DEFAULT_API_MODEL;
}

function defaultEffortFor(runtime: TaskRuntimeName): TaskReasoningEffort | null {
  if (runtime === "codex") return "medium";
  if (runtime === "deepseek_harness") return "low";
  return null;
}
```

新增状态与同步：

```tsx
const [presets, setPresets] = useState<Record<string, RuntimePreset>>({});

useEffect(() => {
  const data = query.data?.data;
  if (!data?.roles) return;
  setRoutes(Object.fromEntries(data.roles.map((item) => [item.id, item.route])));
  setPresets(data.runtime_presets ?? {});
}, [query.data]);
```

替换 `changeRuntime`：

```tsx
const changeRuntime = (id: string, runtime: TaskRuntimeName) => {
  const current = routes[id];
  if (current?.runtime === runtime) return;
  const nextPresets = { ...presets };
  if (current) {
    nextPresets[current.runtime] = {
      model: current.model,
      reasoning_effort: current.reasoning_effort,
    };
  }
  const remembered = nextPresets[runtime];
  setPresets(nextPresets);
  patchRoute(id, {
    runtime,
    model: remembered?.model ?? defaultModelFor(runtime),
    reasoning_effort: remembered?.reasoning_effort ?? defaultEffortFor(runtime),
  });
};
```

运行时下拉增加选项与文案：

```tsx
<SelectValue>{(value: string) =>
  value === "codex" ? "Codex"
  : value === "workbuddy" ? "WorkBuddy"
  : value === "deepseek_harness" ? "DeepSeek Harness"
  : "模型 API"}</SelectValue>
...
<SelectItem value="deepseek_harness">DeepSeek Harness</SelectItem>
```

行内模型与推理强度按 harness 只读：

```tsx
const usingHarness = route.runtime === "deepseek_harness";
const harnessPreset = presets.deepseek_harness;
...
<ModelCombobox
  ariaLabel={`${item.label}模型`}
  value={usingHarness ? (harnessPreset?.model ?? DEFAULT_HARNESS_MODEL) : route.model}
  options={modelCatalog[route.runtime] ?? []}
  placeholder={defaultModelFor(route.runtime)}
  disabled={usingHarness}
  onChange={(next) => patchRoute(item.id, { model: next })}
/>
...
<Select
  value={(usingHarness ? harnessPreset?.reasoning_effort ?? "low" : route.reasoning_effort) ?? "none"}
  disabled={usingHarness}
  onValueChange={(value) => patchRoute(item.id, { reasoning_effort: value as TaskReasoningEffort })}
>
```

`ModelCombobox` 若当前不接受 `disabled` 属性，在 `frontend/src/components/settings/model-combobox.tsx` 增加可选 `disabled?: boolean` 并透传给触发按钮与编辑输入框；不要改动其既有默认行为。

在卡片底部说明区之前插入 harness 统一配置区：

```tsx
<div className="rounded-lg border border-white/10 bg-black/15 p-3">
  <div className="text-sm font-medium text-foreground">DeepSeek Harness 统一配置</div>
  <div className="mt-1 text-[11px] text-muted-foreground">
    该运行时所有任务角色共用同一组模型与推理强度。
  </div>
  <div className="mt-3 grid gap-3 lg:grid-cols-2">
    <Field label="模型">
      <ModelCombobox
        ariaLabel="DeepSeek Harness 统一模型"
        value={presets.deepseek_harness?.model ?? DEFAULT_HARNESS_MODEL}
        options={modelCatalog.deepseek_harness ?? []}
        placeholder={DEFAULT_HARNESS_MODEL}
        onChange={(next) => setPresets((current) => ({
          ...current,
          deepseek_harness: {
            model: next,
            reasoning_effort: current.deepseek_harness?.reasoning_effort ?? "low",
          },
        }))}
      />
    </Field>
    <Field label="推理强度">
      <Select
        value={presets.deepseek_harness?.reasoning_effort ?? "low"}
        onValueChange={(value) => setPresets((current) => ({
          ...current,
          deepseek_harness: {
            model: current.deepseek_harness?.model ?? DEFAULT_HARNESS_MODEL,
            reasoning_effort: value as TaskReasoningEffort,
          },
        }))}
      >
        <SelectTrigger aria-label="DeepSeek Harness 统一推理强度">
          <SelectValue>{(value: string) => value}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {TASK_REASONING_EFFORTS.map((effort) => (
            <SelectItem key={effort} value={effort}>
              {effort === "max" ? "max（仅 WorkBuddy）" : effort}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  </div>
</div>
```

保存改为同时提交两者：

```tsx
await save.mutateAsync({ routes, runtime_presets: presets });
```

导入区补充 `type RuntimePreset`。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && pnpm vitest run src/__tests__/components/settings/`
预期：全部 PASS（含更新后的 `workbuddy-routing.test.tsx`）

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/components/settings/text-task-routing-panel.tsx frontend/src/components/settings/model-combobox.tsx frontend/src/__tests__/components/settings/
git commit -m "feat(web): remember per-runtime presets and gate harness rows read-only"
```

---

## 任务 9：环境变量文档

**文件：**
- 修改：`.env.example`

- [ ] **步骤 1：编写失败的测试**

无自动化测试。改为人工核对：确认 `.env.example` 中不存在 `NOVELVIDEO_DSH_HOME`。

运行：`grep -c NOVELVIDEO_DSH_HOME .env.example; true`
预期：`0`

- [ ] **步骤 2：添加说明**

在 `.env.example` 的模型相关配置区追加：

```
# DeepSeek Harness 文本任务运行时（runtime = deepseek_harness）
# dsh 可执行文件路径；留空则从 PATH 查找。
DSH_BIN=
# Nuomi 专属 harness home，用于隔离 profile、settings 与 session；留空则为 STATE_DIR/dsh。
# 切勿指向 ~/.dsh，否则会覆盖你交互式 dsh 会话的默认模型。
NOVELVIDEO_DSH_HOME=
# harness 调用 provider deepseek-official 所需凭据。
DEEPSEEK_API_KEY=
# harness 单次调用超时（秒）。
DSH_EXEC_TIMEOUT_SECONDS=600
```

- [ ] **步骤 3：核对**

运行：`grep -c NOVELVIDEO_DSH_HOME .env.example`
预期：`1`

- [ ] **步骤 4：Commit**

```bash
git add .env.example
git commit -m "chore(config): document deepseek harness runtime variables"
```

---

## 任务 10：验收

- [ ] **步骤 1：后端全量相关测试**

运行：

```bash
.venv/bin/python -m pytest tests/test_text_task_runtime_settings.py \
  tests/test_text_task_runtime_models_catalog.py \
  tests/test_deepseek_harness_runtime.py \
  tests/test_workbuddy_runtime.py -q
```

预期：全部 PASS

- [ ] **步骤 2：后端默认测试套件未被破坏**

运行：`.venv/bin/python -m pytest -q`
预期：无新增失败（先记录改动前的基线失败项，用于对比）

- [ ] **步骤 3：前端测试与类型检查**

运行：

```bash
cd frontend && pnpm vitest run && pnpm tsc --noEmit -p tsconfig.app.json
```

预期：全部 PASS，`tsc` 无错误

- [ ] **步骤 4：端到端确认（需要 `DEEPSEEK_API_KEY`）**

仅当环境提供 `DEEPSEEK_API_KEY` 时执行。设置 `DSH_BIN`、`NOVELVIDEO_DSH_HOME` 后启动后端，在面板中把 `director_plan` 切到 `DeepSeek Harness` 并保存，触发一次该角色的任务。

预期：

- `NOVELVIDEO_DSH_HOME/settings.yaml` 出现 `provider: deepseek-official` 与 `model: deepseek-v4-flash-vision-exp`。
- 任务产出的结构化结果可被角色 schema 校验通过。
- 用户的 `~/.dsh/settings.yaml` 内容**未发生改变**（执行前后各读一次比对）。

同时确认推理强度 `low` 被 provider 接受；若被拒绝，记录实际报错并回到本计划修订 `_RUNTIME_PRESET_DEFAULTS` 的默认档位。

- [ ] **步骤 5：需求覆盖核对**

逐条对照规格 `docs/superpowers/specs/2026-09-25-deepseek-harness-task-runtime-design.md`：新增运行时、按运行时记忆、harness 统一只读、专属 DSH_HOME、影像拒绝、错误码、环境变量，逐项指出实现位置。列出未覆盖项。

- [ ] **步骤 6：保留工作区既有改动**

提交前运行 `git status --porcelain`，确认未把与本计划无关的既有修改一并提交。
