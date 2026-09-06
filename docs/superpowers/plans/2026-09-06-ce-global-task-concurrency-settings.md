# CE 全局任务并发设置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在“设置 → 运行时与媒体”中增加四类全局任务并发配置，使 CE 的项目准入、用户准入和实际执行线程数在重启后使用同一持久化值。

**架构：** 新建 SQLite 设置模块，以 `task_concurrency_v1` 保存四类 lane 的完整配置，并提供进程级冻结快照。现有限额解析和 `InlineTaskBackend` 从同一快照读取运行值；新的 CE 管理 API 返回已保存值、当前进程值和 active 数，前端卡片负责编辑、校验和重启提示。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLite、pytest；React 19、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构

- 创建 `src/novelvideo/task_concurrency_settings.py`：SQLite 持久化、校验、环境变量检测和进程快照。
- 修改 `src/novelvideo/task_backend/limits.py`：把保存值作为 CE 各层限额的共同 fallback。
- 修改 `src/novelvideo/ports/tasks.py`、`src/novelvideo/ports/local/tasks.py`：声明并实现 lane 运行状态接口。
- 创建 `src/novelvideo/api/routes/task_runtime.py`，修改 `src/novelvideo/api/__init__.py`：提供并注册全局 GET/PUT API。
- 创建 `frontend/src/lib/queries/task-concurrency.ts`，修改 `frontend/src/lib/query-keys.ts`：前端数据访问。
- 创建 `frontend/src/components/settings/task-concurrency-card.tsx`，修改 `frontend/src/components/settings/knowledge-runtime-section.tsx`：设置卡片及集成。
- 修改中英文 locale、README，并创建/修改对应测试文件。

### 任务 1：实现版本化持久化设置

**文件：**
- 创建：`src/novelvideo/task_concurrency_settings.py`
- 创建：`tests/test_task_concurrency_settings.py`

- [ ] **步骤 1：编写失败的持久化和校验测试**

```python
import sqlite3
import pytest
from novelvideo import config


def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    from novelvideo.task_concurrency_settings import reset_process_task_concurrency_for_tests
    reset_process_task_concurrency_for_tests()


def test_defaults_and_atomic_round_trip(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        DEFAULT_TASK_CONCURRENCY,
        load_task_concurrency_settings,
        save_task_concurrency_settings,
    )
    assert load_task_concurrency_settings().lanes == DEFAULT_TASK_CONCURRENCY
    values = {"default": 8, "video": 6, "world": 2, "ffmpeg": 2}
    assert save_task_concurrency_settings(values).lanes == values
    assert load_task_concurrency_settings().lanes == values
    with sqlite3.connect(tmp_path / "state" / "local" / "settings.db") as connection:
        rows = connection.execute(
            "SELECT key FROM runtime_settings WHERE key = ?", ("task_concurrency_v1",)
        ).fetchall()
    assert rows == [("task_concurrency_v1",)]


@pytest.mark.parametrize("values", [
    {"default": 3, "video": 5, "world": 1},
    {"default": 0, "video": 5, "world": 1, "ffmpeg": 1},
    {"default": 33, "video": 5, "world": 1, "ffmpeg": 1},
    {"default": 3.5, "video": 5, "world": 1, "ffmpeg": 1},
])
def test_rejects_incomplete_or_invalid_values(monkeypatch, tmp_path, values):
    isolate(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        TaskConcurrencySettingsError,
        save_task_concurrency_settings,
    )
    with pytest.raises(TaskConcurrencySettingsError) as exc_info:
        save_task_concurrency_settings(values)
    assert exc_info.value.code == "TASK_CONCURRENCY_INVALID"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/test_task_concurrency_settings.py`

预期：FAIL，包含 `ModuleNotFoundError: novelvideo.task_concurrency_settings`。

- [ ] **步骤 3：实现最小持久化模块**

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from novelvideo.sqlite_pragmas import configure_sqlite_connection

TASK_CONCURRENCY_SETTINGS_KEY = "task_concurrency_v1"
TASK_CONCURRENCY_LANES = ("default", "video", "world", "ffmpeg")
DEFAULT_TASK_CONCURRENCY = {"default": 3, "video": 5, "world": 1, "ffmpeg": 1}


class TaskConcurrencySettingsError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaskConcurrencySettings:
    lanes: dict[str, int]


def _connect() -> sqlite3.Connection:
    from novelvideo import config
    path = Path(config.STATE_DIR) / "local" / "settings.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    configure_sqlite_connection(connection)
    connection.execute("""CREATE TABLE IF NOT EXISTS runtime_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    connection.commit()
    return connection


def _validated(values: Mapping[str, object]) -> dict[str, int]:
    if set(values) != set(TASK_CONCURRENCY_LANES):
        raise TaskConcurrencySettingsError("四类任务并发值必须完整提供", code="TASK_CONCURRENCY_INVALID")
    result = {}
    for lane in TASK_CONCURRENCY_LANES:
        value = values[lane]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 32:
            raise TaskConcurrencySettingsError(f"{lane} 并发值必须是 1–32 的整数", code="TASK_CONCURRENCY_INVALID")
        result[lane] = value
    return result


def load_task_concurrency_settings() -> TaskConcurrencySettings:
    with _connect() as connection:
        row = connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (TASK_CONCURRENCY_SETTINGS_KEY,)
        ).fetchone()
    if row is None:
        return TaskConcurrencySettings(dict(DEFAULT_TASK_CONCURRENCY))
    try:
        merged = {**DEFAULT_TASK_CONCURRENCY, **dict(json.loads(str(row[0])))}
        return TaskConcurrencySettings(_validated(merged))
    except (TypeError, ValueError, json.JSONDecodeError, TaskConcurrencySettingsError):
        return TaskConcurrencySettings(dict(DEFAULT_TASK_CONCURRENCY))


def save_task_concurrency_settings(values: Mapping[str, object]) -> TaskConcurrencySettings:
    lanes = _validated(values)
    payload = json.dumps(lanes, ensure_ascii=False, separators=(",", ":"))
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (TASK_CONCURRENCY_SETTINGS_KEY, payload, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    return TaskConcurrencySettings(lanes)


@lru_cache(maxsize=1)
def process_task_concurrency_settings() -> TaskConcurrencySettings:
    return load_task_concurrency_settings()


def reset_process_task_concurrency_for_tests() -> None:
    process_task_concurrency_settings.cache_clear()
```

- [ ] **步骤 4：运行测试验证绿灯**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/test_task_concurrency_settings.py`

预期：全部通过。

- [ ] **步骤 5：提交任务 1**

```bash
git add src/novelvideo/task_concurrency_settings.py tests/test_task_concurrency_settings.py
git commit -m "feat: persist CE task concurrency settings"
```

### 任务 2：让准入和执行器使用同一进程快照

**文件：**
- 修改：`src/novelvideo/task_backend/limits.py`
- 修改：`src/novelvideo/ports/tasks.py`
- 修改：`src/novelvideo/ports/local/tasks.py`
- 修改：`tests/contract/test_l014_inline_task_backend_concurrency.py`

- [ ] **步骤 1：编写失败的统一快照测试**

```python
def test_saved_ce_concurrency_unifies_default_lane_until_restart(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
        save_task_concurrency_settings,
    )
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for name in (
        "ST_PROJECT_MAX_ACTIVE_DEFAULT_TASKS",
        "ST_PROJECT_MIN_ACTIVE_DEFAULT_TASKS",
        "ST_PROJECT_USER_MAX_ACTIVE_DEFAULT_TASKS",
        "ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings({"default": 7, "video": 5, "world": 1, "ffmpeg": 1})
    assert project_lane_active_limit("default") == 7
    assert project_lane_min_active_limit("default") == 7
    assert project_user_lane_active_limit("default") == 7
    assert global_lane_concurrency("default") == 7
    save_task_concurrency_settings({"default": 9, "video": 5, "world": 1, "ffmpeg": 1})
    assert global_lane_concurrency("default") == 7


def test_environment_override_wins(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import reset_process_task_concurrency_for_tests, save_task_concurrency_settings
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings({"default": 7, "video": 5, "world": 1, "ffmpeg": 1})
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS", "4")
    assert global_lane_concurrency("default") == 4


def test_inline_backend_reports_frozen_status(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import reset_process_task_concurrency_for_tests, save_task_concurrency_settings
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings({"default": 6, "video": 5, "world": 1, "ffmpeg": 1})
    backend = InlineTaskBackend()
    assert backend.lane_runtime_status()["default"] == {
        "active": 0, "queued": 0, "executor_limit": 6, "queue_limit": 512,
    }
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/contract/test_l014_inline_task_backend_concurrency.py -k 'saved_ce_concurrency or environment_override_wins or reports_frozen_status'`

预期：FAIL；保存值尚未影响 limits，且 `lane_runtime_status` 不存在。

- [ ] **步骤 3：接入 CE 保存值 fallback**

在 `limits.py` 增加：

```python
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.task_concurrency_settings import process_task_concurrency_settings


def _saved_ce_limit(queue_kind: str | None) -> int | None:
    if not is_ce_effective():
        return None
    return process_task_concurrency_settings().lanes[normalize_queue_kind(queue_kind)]
```

修改 `_lane_active_limit`、`project_lane_min_active_limit`、`_positive_lane_int`：现有环境变量有效时保持原语义；没有显式环境变量时使用 `_saved_ce_limit`；非 CE 时回退原代码默认值。

- [ ] **步骤 4：扩展 TaskBackend 协议与本地状态接口**

```python
class TaskBackend(Protocol):
    async def enqueue_project_task(...): ...
    async def cancel_project_task(self, ctx, task_state) -> bool: ...
    def lane_runtime_status(self) -> dict[str, dict[str, int]]: ...
```

```python
def lane_runtime_status(self) -> dict[str, dict[str, int]]:
    return {
        lane: {
            "active": state.active,
            "queued": len(state.queued),
            "executor_limit": state.concurrency,
            "queue_limit": state.queue_limit,
        }
        for lane, state in self._lanes.items()
    }
```

- [ ] **步骤 5：运行合同回归并提交**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/contract/test_l014_inline_task_backend_concurrency.py tests/test_task_concurrency_settings.py`

预期：全部通过，原环境变量合同不回归。

```bash
git add src/novelvideo/task_backend/limits.py src/novelvideo/ports/tasks.py src/novelvideo/ports/local/tasks.py tests/contract/test_l014_inline_task_backend_concurrency.py
git commit -m "feat: apply frozen CE task concurrency limits"
```

### 任务 3：增加全局 task-runtime 管理 API

**文件：**
- 创建：`src/novelvideo/api/routes/task_runtime.py`
- 修改：`src/novelvideo/api/__init__.py`
- 创建：`tests/test_api_task_runtime.py`

- [ ] **步骤 1：编写失败的 API 合同测试**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sqlite3
from novelvideo import config
from novelvideo.api.auth import get_api_user

ADMIN_USER = {"id": "local", "username": "local", "role": "owner"}


class FakeBackend:
    def lane_runtime_status(self):
        limits = {"default": 3, "video": 5, "world": 1, "ffmpeg": 1}
        return {lane: {"active": 0, "queued": 0, "executor_limit": limit, "queue_limit": 64} for lane, limit in limits.items()}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.routes import task_runtime
    from novelvideo.task_concurrency_settings import reset_process_task_concurrency_for_tests
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setattr(task_runtime, "get_task_backend", lambda: FakeBackend())
    reset_process_task_concurrency_for_tests()
    app = FastAPI()
    app.include_router(task_runtime.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: ADMIN_USER
    return TestClient(app)


def test_get_and_put_contract(client):
    assert client.get("/api/v1/task-runtime/concurrency").json()["data"]["lanes"]["default"]["configured"] == 3
    response = client.put("/api/v1/task-runtime/concurrency", json={
        "lanes": {"default": 8, "video": 6, "world": 2, "ffmpeg": 2}
    })
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["restart_required"] is True
    assert data["lanes"]["default"]["configured"] == 8
    assert data["lanes"]["default"]["running_limits"] == {"project": 3, "user": 3, "executor": 3}


@pytest.mark.parametrize("value", [0, 33, 1.5])
def test_put_rejects_invalid_value(client, value):
    response = client.put("/api/v1/task-runtime/concurrency", json={
        "lanes": {"default": value, "video": 5, "world": 1, "ffmpeg": 1}
    })
    assert response.status_code == 422
    assert response.json()["errorCode"] == "TASK_CONCURRENCY_INVALID"


def test_environment_override_is_reported(client, monkeypatch):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "2")
    response = client.get("/api/v1/task-runtime/concurrency")
    assert response.json()["data"]["lanes"]["world"]["managed_by_environment"] is True


def test_put_is_ce_only(client, monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ee")
    response = client.put("/api/v1/task-runtime/concurrency", json={
        "lanes": {"default": 3, "video": 5, "world": 1, "ffmpeg": 1}
    })
    assert response.status_code == 403
    assert response.json()["errorCode"] == "TASK_CONCURRENCY_CE_ONLY"


def test_put_reports_sqlite_failure(client, monkeypatch):
    from novelvideo.api.routes import task_runtime
    monkeypatch.setattr(
        task_runtime,
        "save_task_concurrency_settings",
        lambda _values: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )
    response = client.put("/api/v1/task-runtime/concurrency", json={
        "lanes": {"default": 3, "video": 5, "world": 1, "ffmpeg": 1}
    })
    assert response.status_code == 503
    assert response.json()["errorCode"] == "TASK_CONCURRENCY_SAVE_FAILED"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/test_api_task_runtime.py`

预期：FAIL，`novelvideo.api.routes.task_runtime` 不存在。

- [ ] **步骤 3：实现路由模型与状态序列化**

```python
class LaneValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: int = Field(ge=1, le=32)
    video: int = Field(ge=1, le=32)
    world: int = Field(ge=1, le=32)
    ffmpeg: int = Field(ge=1, le=32)


class TaskConcurrencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lanes: LaneValues


router = APIRouter(
    prefix="/task-runtime",
    dependencies=[Depends(require_media_capability_admin)],
)
```

实现 `_runtime_data()`：读取 `load_task_concurrency_settings()`；从 `get_task_backend().lane_runtime_status()` 读取 active/executor；从 `project_lane_effective_active_limit(..., eligible_user_count=1)` 和 `project_user_lane_active_limit` 读取两个准入值；检测四类既有环境变量；只有非环境变量管理 lane 的保存值与三个运行值不一致时设置 `restart_required=true`。

GET 返回状态。PUT 使用 `body: dict[str, Any]` 接收 JSON，并在 handler 内调用 `TaskConcurrencyBody.model_validate(body)`，这样可以捕获 `ValidationError` 并稳定返回 422/`TASK_CONCURRENCY_INVALID`。随后要求 `is_ce_effective()` 并原子保存完整四项；非 CE、SQLite 失败分别返回 403/`TASK_CONCURRENCY_CE_ONLY`、503/`TASK_CONCURRENCY_SAVE_FAILED`。

- [ ] **步骤 4：在主 API 注册路由**

```python
from novelvideo.api.routes import task_runtime
api_router.include_router(task_runtime.router, tags=["task-runtime"])
```

- [ ] **步骤 5：运行 API 回归并提交**

运行：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/test_api_task_runtime.py tests/test_task_concurrency_settings.py tests/contract/test_l014_inline_task_backend_concurrency.py`

预期：全部通过。

```bash
git add src/novelvideo/api/routes/task_runtime.py src/novelvideo/api/__init__.py tests/test_api_task_runtime.py
git commit -m "feat: expose CE task concurrency settings API"
```

### 任务 4：实现前端查询和任务并发卡片

**文件：**
- 创建：`frontend/src/lib/queries/task-concurrency.ts`
- 修改：`frontend/src/lib/query-keys.ts`
- 创建：`frontend/src/components/settings/task-concurrency-card.tsx`
- 创建：`frontend/src/__tests__/components/settings/task-concurrency-card.test.tsx`

- [ ] **步骤 1：编写失败的卡片测试**

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  data: {
    restart_required: false,
    lanes: {
      default: { configured: 3, running_limits: { project: 3, user: 3, executor: 3 }, active: 2, managed_by_environment: false },
      video: { configured: 5, running_limits: { project: 5, user: 5, executor: 5 }, active: 0, managed_by_environment: false },
      world: { configured: 1, running_limits: { project: 1, user: 1, executor: 1 }, active: 0, managed_by_environment: true },
      ffmpeg: { configured: 1, running_limits: { project: 1, user: 1, executor: 1 }, active: 0, managed_by_environment: false },
    },
  },
}));

vi.mock("@/lib/queries/task-concurrency", () => ({
  useTaskConcurrencySettings: () => ({ data: mocks.data, isLoading: false, isError: false }),
  useSaveTaskConcurrencySettings: () => ({ mutateAsync: mocks.save, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TaskConcurrencyCard } from "@/components/settings/task-concurrency-card";

beforeEach(() => mocks.save.mockReset().mockResolvedValue(mocks.data));

it("shows all lanes and current usage", () => {
  render(<TaskConcurrencyCard open />);
  expect(screen.getByLabelText("默认任务并发上限")).toHaveValue(3);
  expect(screen.getByText("当前 2 / 上限 3")).toBeInTheDocument();
  expect(screen.getByLabelText("视频任务并发上限")).toHaveValue(5);
  expect(screen.getByLabelText("世界构建并发上限")).toBeDisabled();
  expect(screen.getByText("由环境变量管理")).toBeInTheDocument();
});

it("saves a complete valid configuration", async () => {
  render(<TaskConcurrencyCard open />);
  fireEvent.change(screen.getByLabelText("默认任务并发上限"), { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "保存任务并发设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({
    default: 8, video: 5, world: 1, ffmpeg: 1,
  }));
});

it("blocks out-of-range values", () => {
  render(<TaskConcurrencyCard open />);
  fireEvent.change(screen.getByLabelText("默认任务并发上限"), { target: { value: "33" } });
  expect(screen.getByRole("button", { name: "保存任务并发设置" })).toBeDisabled();
  expect(screen.getByText("请输入 1–32 的整数")).toBeInTheDocument();
});

it("keeps edited values after save failure", async () => {
  mocks.save.mockRejectedValueOnce(new Error("保存失败"));
  render(<TaskConcurrencyCard open />);
  const input = screen.getByLabelText("默认任务并发上限");
  fireEvent.change(input, { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "保存任务并发设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  expect(input).toHaveValue(8);
});
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`cd frontend && pnpm exec vitest run src/__tests__/components/settings/task-concurrency-card.test.tsx`

预期：FAIL，无法导入 `TaskConcurrencyCard`。

- [ ] **步骤 3：实现查询类型和 hooks**

```ts
export type TaskLane = "default" | "video" | "world" | "ffmpeg";
export type TaskConcurrencyValues = Record<TaskLane, number>;
export interface TaskConcurrencyLaneStatus {
  configured: number;
  running_limits: { project: number; user: number; executor: number };
  active: number;
  managed_by_environment: boolean;
}
export interface TaskConcurrencyStatus {
  restart_required: boolean;
  lanes: Record<TaskLane, TaskConcurrencyLaneStatus>;
}

export function useTaskConcurrencySettings(enabled = true) {
  return useQuery({
    queryKey: queryKeys.taskConcurrency(),
    queryFn: ({ signal }) => api.get("api/v1/task-runtime/concurrency", { signal })
      .json<OkResponse<TaskConcurrencyStatus>>().then((response) => response.data),
    enabled,
    retry: false,
  });
}

export function useSaveTaskConcurrencySettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (lanes: TaskConcurrencyValues) => api.put(
      "api/v1/task-runtime/concurrency", { json: { lanes } }
    ).json<OkResponse<TaskConcurrencyStatus>>().then((response) => response.data),
    onSuccess: (data) => client.setQueryData(queryKeys.taskConcurrency(), data),
  });
}
```

在 query keys 增加：

```ts
taskConcurrency: () => ["task-runtime", "concurrency"] as const,
```

- [ ] **步骤 4：实现最小卡片组件**

- `open=false` 时禁用查询；数据到达后复制四个 configured 值到本地字符串表单。
- 非空、整数、1–32 才合法；任一可编辑项非法时禁用保存。
- 环境变量管理项禁用输入，但保存请求仍携带服务端 configured 值。
- current 文案使用 active/executor；三个运行值不一致时显示“项目 / 个人 / 执行”明细。
- 保存成功显示重启 toast；失败时保留表单。

```tsx
const handleSave = async () => {
  if (!valid) return;
  try {
    await save.mutateAsync(
      Object.fromEntries(LANES.map((lane) => [lane, Number(draft[lane])])) as TaskConcurrencyValues,
    );
    toast.success(t("settings.taskConcurrency.savedRestartRequired"));
  } catch (error) {
    toast.error(error instanceof Error ? error.message : String(error));
  }
};
```

- [ ] **步骤 5：运行卡片测试并提交**

运行：`cd frontend && pnpm exec vitest run src/__tests__/components/settings/task-concurrency-card.test.tsx`

预期：全部通过。

```bash
git add frontend/src/lib/queries/task-concurrency.ts frontend/src/lib/query-keys.ts frontend/src/components/settings/task-concurrency-card.tsx frontend/src/__tests__/components/settings/task-concurrency-card.test.tsx
git commit -m "feat: add task concurrency settings card"
```

### 任务 5：集成设置页、国际化和完整验证

**文件：**
- 修改：`frontend/src/components/settings/knowledge-runtime-section.tsx`
- 修改：`frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`README.md`

- [ ] **步骤 1：编写失败的设置页集成测试**

```tsx
vi.mock("@/components/settings/task-concurrency-card", () => ({
  TaskConcurrencyCard: ({ open }: { open: boolean }) => (
    <div data-testid="task-concurrency-card">任务并发：{open ? "已启用" : "已禁用"}</div>
  ),
}));

it("shows global task concurrency settings in the runtime page", () => {
  render(<KnowledgeRuntimeSection open />);
  expect(screen.getByTestId("task-concurrency-card")).toHaveTextContent("任务并发：已启用");
});
```

- [ ] **步骤 2：运行集成测试验证红灯**

运行：`cd frontend && pnpm exec vitest run src/__tests__/components/settings/knowledge-runtime-section.test.tsx`

预期：FAIL，卡片尚未渲染。

- [ ] **步骤 3：插入卡片并补齐中英文文案**

在 `TextTaskRoutingPanel` 后插入：

```tsx
<TaskConcurrencyCard open={open} />
```

locale 增加以下精确结构；英文文件提供对应英文值。所有用户可见字符串使用 `t("settings.taskConcurrency.*")`。

```json
{
  "taskConcurrency": {
    "title": "任务并发",
    "description": "统一管理本机全部项目的任务准入与实际执行并发，保存后重启服务生效。",
    "lanes": {
      "default": "默认任务",
      "video": "视频任务",
      "world": "世界构建",
      "ffmpeg": "音视频合成"
    },
    "inputLabel": "{{lane}}并发上限",
    "usage": "当前 {{active}} / 上限 {{limit}}",
    "environmentManaged": "由环境变量管理",
    "mixedLimits": "项目 {{project}} / 个人 {{user}} / 执行 {{executor}}",
    "invalid": "请输入 1–32 的整数",
    "save": "保存任务并发设置",
    "savedRestartRequired": "任务并发配置已保存，重启服务后生效",
    "restartRequired": "已保存新配置，当前服务仍使用启动时并发值。"
  }
}
```

- [ ] **步骤 4：更新 README**

说明并发优先通过“设置 → 运行时与媒体 → 任务并发”管理；既有 `ST_PROJECT_*` 和 `ST_CE_GLOBAL_MAX_ACTIVE_*` 仍具有最高优先级；保存后必须重启 `scripts/start-ce.sh`。

- [ ] **步骤 5：运行设置页回归**

运行：`cd frontend && pnpm exec vitest run src/__tests__/components/settings/task-concurrency-card.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx`

预期：全部通过。

- [ ] **步骤 6：运行完整验证**

```bash
/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/pytest -q tests/test_task_concurrency_settings.py tests/test_api_task_runtime.py tests/contract/test_l014_inline_task_backend_concurrency.py
cd frontend && pnpm test
cd frontend && pnpm run build:ce
/Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/ruff check src/novelvideo/task_concurrency_settings.py src/novelvideo/task_backend/limits.py src/novelvideo/ports/tasks.py src/novelvideo/ports/local/tasks.py src/novelvideo/api/routes/task_runtime.py tests/test_task_concurrency_settings.py tests/test_api_task_runtime.py tests/contract/test_l014_inline_task_backend_concurrency.py
git diff --check
```

预期：全部退出码为 0；Vitest/pytest 无失败；CE 构建成功；Ruff 与 diff-check 无错误。

- [ ] **步骤 7：提交集成**

```bash
git add frontend/src/components/settings/knowledge-runtime-section.tsx frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json README.md
git commit -m "feat: integrate global task concurrency settings"
```

- [ ] **步骤 8：最终代码审查**

审查范围限定为本计划文件；重点检查环境变量兼容、进程快照冻结、保存原子性、非 CE 写入权限、表单非法值和环境变量只读行为。修复 Critical/Important 后重新执行步骤 6。
