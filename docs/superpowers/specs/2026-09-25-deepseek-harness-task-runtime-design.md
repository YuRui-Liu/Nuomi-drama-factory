# DeepSeek Harness 文本任务运行时 设计

## 背景

「设置 → 运行时与媒体 → 文本任务路由」当前提供三个执行运行时：`codex`、`workbuddy`、`model_api`。每个逻辑任务角色（`episode_normalization`、`knowledge_extraction`、`director_plan`、`episode_asset_planning` 等）各存一条路由，包含运行时、模型和推理强度，配置在任务入队时冻结成快照。

现有交互有两个问题：

1. 切换运行时不会恢复该运行时上次使用的模型与推理强度，用户每次都要手动重选。
2. 缺少 DeepSeek Harness 作为可选运行时。

本设计要求新增 `deepseek_harness` 运行时，并引入按运行时记忆模型与推理强度的机制。

## 目标

- 新增 `deepseek_harness` 运行时，通过 `dsh --profile headless` 执行结构化文本任务。
- 切换运行时（任意方向）时，自动恢复该运行时上次使用的模型与推理强度，无需手动重选。
- DeepSeek Harness 的默认模型为 `deepseek-v4-flash-vision-exp`，默认推理强度为 `low`。
- DeepSeek Harness 的模型与推理强度为**运行时级统一值**，所有任务角色共用同一组。

## 非目标

- 不改动图片、视频、配音供应商的选择逻辑。
- 不支持 DeepSeek Harness 的图片输入（headless 无图像通道）。
- 不引入多轮交互会话；headless 每次调用只执行一个任务。
- 不改动 `AgentTaskRoute` 的字段集合，不改动任务入队时的快照冻结语义。
- 不为 codex / workbuddy / model_api 增加运行时级统一约束（它们仍允许每个角色各自选择模型）。

## 已验证事实（spike）

以下事实由本机实测确认，是本设计的事实基础：

| 项 | 结论 |
|---|---|
| headless 入口 | `dsh --profile headless "<task>"`，stdout 输出最终 assistant 文本，stderr 输出推理过程，退出码 0 表示完成、1 表示失败 |
| headless 配置项 | 仅 `task` 一个字段（`z.object({ task: z.string().required() })`），**没有任何模型或推理强度参数** |
| 模型来源 | `agentDefaultModel.currentSelection()`，即 `$DSH_HOME/settings.yaml` 中 `agent-default-model` 命名空间的 `{provider, model, reasoningEffort?}` |
| settings 字段名 | camelCase `reasoningEffort`，且为可选字段 |
| provider 路由名 | `deepseek-official`（base 组合层默认，non-`deepseek`） |
| base 默认模型 | `deepseek-flash` |
| 隔离 DSH_HOME 下的 auto-init | **成功**。生成完整 profile（`cordis.patch.yml`、`cordis.yml`、`package.json`、`pnpm-workspace.yaml`），插件树正常组合挂载，调用一路推进到 provider 请求阶段 |
| 首次初始化成本 | 低（约 44K）。profile 使用模块回退（`.dsh-module-fallback`），不执行重量级 `pnpm install` |
| 凭据 | 需要 `DEEPSEEK_API_KEY`（进程环境）或 `$DSH_HOME/.credentials.yaml`（凭据服务管理）；本机当前未设置 |
| headless 图像支持 | 无。源码中不存在任何 image / binary / media 处理路径 |
| headless 结构化输出 | 无 JSON 输出开关，只能把 JSON Schema 拼入 prompt 并解析 stdout |

## 关键决策

### 决策 1：DeepSeek Harness 的模型与推理强度是运行时级统一值

所有任务角色共用同一组 `(model, reasoning_effort)`，不允许按角色分化。

**理由**：headless 的模型来自进程级 `agent-default-model` 设置，而非按次调用参数。若允许每个角色各自选择，则并发任务会争抢同一份 `settings.yaml`，产生读写竞争与模型串用。统一一组从语义上消除了这个问题。

该统一性由**后端在路由解析收口处强制施加**，而不是只靠前端只读约束。前端只读只是表现层，后端不变量才是保证。

### 决策 2：preset 存放于 `AgentTaskRoutingConfig` 顶层

按运行时记忆的模型与推理强度存放在 `AgentTaskRoutingConfig.runtime_presets`，与 `routes` 并列，共用同一个 KV key，一次原子落盘。

**理由**：不把 preset 放进 `AgentTaskRoute`，可保持 `AgentTaskRoute`、`AgentTaskRouteOverride`、`AgentTaskRouteSnapshot` 的字段集合完全不变，快照冻结逻辑零改动。同时避免引入第二个 KV key 与双写。

### 决策 3：使用 Nuomi 专属 DSH_HOME

后端调用 harness 时注入独立的 `DSH_HOME`，不触碰用户自己的 `~/.dsh`。

**理由**：`$DSH_HOME/settings.yaml` 正是用户交互式 dsh 会话（含本仓库的 Web GUI）读取的同一份配置文档。若共用，Nuomi 写入 `agent-default-model` 会顺带改掉用户自己的会话模型，属于跨系统污染。独立 DSH_HOME 同时隔离 profile、settings、session 与凭据。

### 决策 4：切换运行时时以 preset 覆盖当前行，而非保留当前值

现有实现（`changeRuntime`）在切换运行时后保留用户已填的模型值。本设计将其替换为：切换时先把当前行的 `(model, effort)` 存入 `presets[旧运行时]`，再用 `presets[新运行时]`（若不存在则用该运行时的内置默认）填充该行。

**理由**：这正是「避免每次都手动改」的落点。旧的「跨运行时保留同一个值」行为与按运行时记忆的语义直接冲突，必须替换而非叠加。

### 决策 5：`deepseek-v4-flash-vision-exp` 仅作为 harness 运行时的默认值

需求中「deepseek 默认是 deepseek-v4-flash-vision-exp 低思考模型」被明确理解为：`deepseek_harness` 运行时的默认 preset 为 `(deepseek-v4-flash-vision-exp, low)`。

`model_api` 运行时的既有默认值 `deepseek-v4-flash`（见 `AgentTaskRoute.model`）以及前端 `DEFAULT_API_MODEL` **保持不变**。若需求实际意图是同时改掉 `model_api` 的默认模型，则需在实现前修订本节。

## 数据模型

文件：`src/novelvideo/text_task_runtime/models.py`

```python
TextTaskRuntimeName = Literal["codex", "model_api", "workbuddy", "deepseek_harness"]


class RuntimePreset(BaseModel):
    """按运行时记忆的模型与推理强度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    reasoning_effort: TextTaskReasoningEffort | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_text_task_model_name(value)


class AgentTaskRoutingConfig(BaseModel):
    """Versioned mapping from logical task roles to route overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: dict[str, AgentTaskRouteOverride] = Field(default_factory=dict)
    runtime_presets: dict[TextTaskRuntimeName, RuntimePreset] = Field(default_factory=dict)
```

向后兼容：旧存储数据不含 `runtime_presets`，因该字段有默认值而正常通过校验。

`AgentTaskRoute` 不变：

```python
class AgentTaskRoute(BaseModel):
    runtime: TextTaskRuntimeName = "model_api"
    model: str = "deepseek-v4-flash"
    reasoning_effort: TextTaskReasoningEffort | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    fallback: TextTaskFallback = "stop"
```

## 存储与解析

文件：`src/novelvideo/text_task_runtime/settings.py`

内置默认 preset：

```python
_RUNTIME_PRESET_DEFAULTS: dict[str, RuntimePreset] = {
    "deepseek_harness": RuntimePreset(
        model="deepseek-v4-flash-vision-exp",
        reasoning_effort="low",
    ),
}
```

解析函数：

```python
def runtime_preset_for(
    config: AgentTaskRoutingConfig, runtime: str
) -> RuntimePreset | None:
    """用户值优先，回落内置默认，两者皆无返回 None。"""

    saved = config.runtime_presets.get(runtime)
    if saved is not None:
        return saved
    return _RUNTIME_PRESET_DEFAULTS.get(runtime)
```

harness 统一性收口：

```python
def _clamp_harness_route(
    snapshot: AgentTaskRouteSnapshot,
    config: AgentTaskRoutingConfig,
) -> AgentTaskRouteSnapshot:
    """deepseek_harness 路由的模型与推理强度由运行时级 preset 唯一决定。"""

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

`resolve_configured_agent_task_route` 在返回前调用 `_clamp_harness_route`，并传入已加载的 global 配置。这是快照产生的唯一收口点，因此无论角色路由、项目级 override 或任务级 override 写了什么，`deepseek_harness` 的出队快照永远是统一的那一组值。

## API 契约

文件：`src/novelvideo/api/routes/model_gateway.py`

请求体扩展：

```python
class TaskRuntimeConfigBody(BaseModel):
    routes: dict[str, AgentTaskRoute]
    runtime_presets: dict[TextTaskRuntimeName, RuntimePreset] | None = None
```

`put_task_runtime_config`：

```python
config = AgentTaskRoutingConfig(
    routes={role: route.model_dump(mode="json") for role, route in body.routes.items()},
    runtime_presets=body.runtime_presets or {},
)
```

现有「未知角色名报 422」校验保持不变。`routes` 与 `runtime_presets` **均为整表替换语义**：请求体中未出现的运行时，其 preset 会被清除。前端总是提交完整的 preset 映射，因此正常路径下不会丢失记忆；直接调用 API 的调用方必须提交完整映射。

`_task_runtime_config_payload()` 增加 `runtime_presets` 字段，返回**生效值**（用户值优先，回落内置默认），使前端能够只读展示 harness 的统一配置。未定义内置默认且用户未保存的运行时不出现在该字段中。

前端查询层 `frontend/src/lib/queries/model-gateway.ts` 对应调整：

- `TaskRuntimeName` 增加 `"deepseek_harness"`。
- 新增 `RuntimePreset` 接口。
- `TaskRuntimeConfig` 增加 `runtime_presets: Partial<Record<TaskRuntimeName, RuntimePreset>>`。
- `useSaveTaskRuntimeConfig` 入参由 `Record<string, AgentTaskRoute>` 改为 `{ routes: Record<string, AgentTaskRoute>; runtime_presets: Record<string, RuntimePreset> }`。

## 运行时执行

新文件：`src/novelvideo/text_task_runtime/deepseek_harness.py`，结构参照 `workbuddy.py`。

### 可执行文件与主目录解析

```python
def dsh_command() -> str:
    """定位 dsh 可执行文件。优先 DSH_BIN，其次 PATH。"""
```

未找到时抛 `KnowledgeRuntimeError(code="DSH_NOT_INSTALLED")`。

```python
def harness_home() -> Path:
    """Nuomi 专属 harness home。

    优先 NOVELVIDEO_DSH_HOME，否则 config.STATE_DIR / "dsh"。
    """
```

### 设置文档写入

每次调用前，向 `harness_home() / "settings.yaml"` 写入：

```yaml
agent-default-model:
  provider: deepseek-official
  model: <preset.model>
  reasoningEffort: <preset.reasoning_effort>
```

`reasoning_effort` 为 `None` 时省略该行。

写入使用原子替换（临时文件 + `os.replace`），避免 `dsh-settings-file` 的 watcher 读到半截文档。

因为 harness 的模型是运行时级统一值（决策 1），该文档的内容在同一时刻对所有并发调用都相同，不存在写竞争导致的模型串用。

### 调用

```
argv = [dsh_command(), "--profile", "headless", request]
env  = {**os.environ, "DSH_HOME": str(harness_home())}
cwd  = 临时目录（tempfile.TemporaryDirectory，前缀 nuomi-dsh-）
```

`request` 的构造：当 `output_type` 不是 `str` 时，在 prompt 尾部追加该类型的 JSON Schema 与「只返回符合该 Schema 的 JSON」的约束，与 `workbuddy.py` 的现有做法同构。stdout 解析为 JSON 后用 `output_type.model_validate_json` 校验，并透传 `validation_context`。

### 限制与失败处理

| 情况 | 处理 |
|---|---|
| 传入 `images` | 抛 `KnowledgeRuntimeError(code="DSH_IMAGES_UNSUPPORTED")` |
| 进程无法启动 | `KnowledgeRuntimeError(code="DSH_START_FAILED")` |
| 退出码非 0 | `KnowledgeRuntimeError(code="DSH_EXEC_FAILED")`，不透出子进程 stderr 原文 |
| stdout 非合法 JSON 或不符 Schema | `KnowledgeRuntimeError(code="DSH_OUTPUT_INVALID")` |
| 超时 | 终止进程树后重新抛出底层超时异常（与 `workbuddy.py` 的既有行为一致，不包装成自定义错误码） |
| 任务取消 | 调用 `terminate_process_tree` 清理子进程后继续抛出取消 |

超时值必须为正数，校验方式与 `workbuddy.py` 一致。

### 分发

文件：`src/novelvideo/text_task_runtime/runtime.py`

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

模型候选目录 `src/novelvideo/text_task_runtime/models_catalog.py` 的 `_RUNTIMES` 增加 `"deepseek_harness"`，并提供候选清单（至少包含 `deepseek-v4-flash-vision-exp`）。

## 前端交互

文件：`frontend/src/components/settings/text-task-routing-panel.tsx`

### 按运行时记忆

`changeRuntime(id, next)` 的行为：

1. 若 `next` 与当前运行时相同，直接返回。
2. 把当前行的 `(model, reasoning_effort)` 写入 `presets[current.runtime]`。
3. 从 `presets[next]` 取值填充该行；`presets[next]` 不存在时使用 `defaultModelFor(next)`。

`defaultModelFor` 保留现有三个运行时的默认值（`workbuddy` → `default-model`，`codex` → `gpt-5.6-sol`，`model_api` → `deepseek-v4-flash`），并新增 `deepseek_harness` → `deepseek-v4-flash-vision-exp`。

### harness 行只读

当某行 `runtime === "deepseek_harness"`：

- 模型控件 `disabled`，显示 `presets.deepseek_harness.model`。
- 推理强度控件 `disabled`，显示 `presets.deepseek_harness.reasoning_effort`。

### harness 统一配置区

新增一个独立编辑区，读写 `presets.deepseek_harness` 的 `(model, reasoning_effort)`。该区域与角色行解耦：修改它不会逐个改写角色行，而是通过保存后重新拉取配置，让所有 harness 行显示新值。

### 保存

保存时同时提交 `routes` 与 `runtime_presets`。保存成功后由现有 `invalidateQueries` 重新拉取配置，前端状态以后端返回的生效值为准。

## 错误处理

- 后端：所有 harness 失败统一包装为 `KnowledgeRuntimeError`，携带稳定 `code`，不把子进程原始输出拼进用户可见消息（沿用 `workbuddy.py` 的既有约束）。
- 前端：面板已有 API 失败提示分支保留；保存失败通过现有 `toast.error` 呈现。
- `runtime_presets` 校验失败由 Pydantic 在 API 层拒绝，返回 422。

## 测试

后端：

- `tests/test_text_task_runtime_settings.py` 增加：
  - `runtime_presets` 的解析与回落内置默认。
  - **clamp 不变量**：角色路由中存任意 `model`，`deepseek_harness` 快照仍等于运行时级 preset 值。
  - 旧格式配置（无 `runtime_presets`）仍能加载。
- 新增 `tests/test_deepseek_harness_runtime.py`，参照 `tests/test_workbuddy_runtime.py`：
  - argv 构造包含 `--profile headless`，且 `DSH_HOME` 注入为 Nuomi 专属路径。
  - `settings.yaml` 内容包含 `provider: deepseek-official`、preset 的 `model` 与 `reasoningEffort`。
  - 传入 `images` 抛 `DSH_IMAGES_UNSUPPORTED`。
  - 退出码非 0 抛 `DSH_EXEC_FAILED` 且消息不含子进程原始输出。
  - 取消时调用 `terminate_process_tree`。
  - 非 `str` 的 `output_type` 会把 Schema 拼入请求。

前端：

- `frontend/src/__tests__/components/settings/workbuddy-routing.test.tsx` **必须更新**：其第 55–65 行断言的「跨运行时保留同一模型」正是本设计要替换的旧行为。
- 新增测试覆盖：按运行时记忆（切走再切回恢复原值）、harness 行控件为 disabled、harness 统一值修改后所有 harness 行同步。

## 运维前提与配置

新增环境变量：

| 变量 | 必需 | 说明 |
|---|---|---|
| `DSH_BIN` | 否 | `dsh` 可执行文件路径；不设则从 PATH 查找 |
| `NOVELVIDEO_DSH_HOME` | 否 | Nuomi 专属 harness home；不设则为 `STATE_DIR/dsh` |
| `DEEPSEEK_API_KEY` | 是 | harness 调用 provider `deepseek-official` 所需凭据 |

`DEEPSEEK_API_KEY` 也可改为写入 `$NOVELVIDEO_DSH_HOME/.credentials.yaml`（凭据服务管理的文档）。两者取其一。

部署要求：

- 后端进程对 `NOVELVIDEO_DSH_HOME`（或 `STATE_DIR/dsh`）必须有写权限，auto-init 需要创建 profile 目录与文件。
- 首次调用会初始化 headless profile。该过程使用模块回退，不执行重量级安装，但需要能读取全局 dsh 安装的模块。
- 容器化自托管场景需为上述路径配置可写挂载或卷。

## 未决风险

1. ~~推理强度取值合法性未端到端验证。~~ **已于 2026-09-25 端到端验证通过。** 用真实凭据走 Nuomi 适配器完整路径（`load_global_routes` → preset 解析 → 写 `settings.yaml` → `dsh --profile headless` → 解析 stdout → Pydantic 校验）返回预期结构化结果，退出码 0。确认 `deepseek-official` 路由提供 `deepseek-v4-flash-vision-exp`，且 provider 接受 `reasoningEffort: low`。同时确认调用前后用户的 `~/.dsh/settings.yaml`（sha256 `c631a42d…`）逐字节不变，隔离保证成立。
2. **harness 运行时与 Nuomi 网关的凭据关系。** 当前设计让 harness 直接使用 `DEEPSEEK_API_KEY`，与 Nuomi 自身模型网关（`compat` 渠道配置）相互独立。

   **已观察到的实际后果（2026-09-25）**：若部署把额度放在兼容网关（NewAPI）并只配置 `model_api` 运行时，`deepseek_harness` 会**绕开网关**直连 `api.deepseek.com`，因缺凭据而失败。当时的错误消息把原因误导为「余额/模型权限」，现已修复为透出 dsh 自身的 `MISSING_CREDENTIAL` / `AUTH` 等错误行。

   另需注意：为保证不覆盖用户的 `~/.dsh/settings.yaml`，`DSH_HOME` 被隔离，**凭据也随之被隔离**——用户交互式 dsh 中已存的 key 对 Nuomi 不可见，必须单独提供给 Nuomi（后端环境变量 `DEEPSEEK_API_KEY`，或 Nuomi 专属 `$DSH_HOME/.credentials.yaml`，后者权限必须为 `600`，且因文件被 watch 而无需重启）。

   若希望 harness 复用网关的 key 与 base URL，需要额外的配置映射（可经 `dsh-llm-pi-ai` 的 `providers` 字典声明 `baseURL` + `apiKeyEnv`），本设计不覆盖。
3. **`settings.yaml` 热重载时序。** 每次调用前重写该文档，`dsh-settings-file` 的 watcher 有去抖窗口。由于是进程启动时读取、且内容在并发下一致，预期无影响；若实测出现读取到旧值，可改为每次调用使用独立 settings 路径并通过 profile 补丁层覆盖。

   **2026-09-25 端到端验证未发现问题**：写入后 dsh 读取到的是本次写入的内容，`model` / `reasoningEffort` 均生效。
