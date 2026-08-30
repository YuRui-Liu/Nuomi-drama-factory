# 智能任务运行时路由实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（- [ ]）语法来跟踪进度。

**目标：** 为后台普通文本任务提供可冻结、可追溯的 Codex/model_api 任务级路由，并在设置页支持系统默认、项目覆盖和本次覆盖。

**架构：** 在 run_project_task_core_sync() 入口解析并注入 AgentTaskRouteSnapshot，业务只依赖统一 structured runtime。Cognee 保留 Ollama embedding，但文本后端服从任务路由；聊天域不合并。

**技术栈：** Python 3.11、Pydantic、PydanticAI、Codex CLI、FastAPI、React、TanStack Query、pytest、Vitest。

---

## 文件结构

- 创建 src/novelvideo/text_task_runtime/models.py：路由、覆盖、冻结清单和状态 DTO。
- 创建 src/novelvideo/text_task_runtime/settings.py：全局/项目配置持久化和解析。
- 创建 src/novelvideo/text_task_runtime/runtime.py：统一 structured backend 与 ContextVar。
- 修改 task_backend/registry.py、run_core.py、ports/local/tasks.py：注册任务职责，入队冻结，执行注入。
- 修改 knowledge_runtime/context.py、DirectorPlan/H3 调用点：消费统一 runtime。
- 修改 api/routes/model_gateway.py 和前端设置组件：配置、状态、测试和有效值预览。

### 任务 1：冻结路由契约

**文件：**
- 创建：src/novelvideo/text_task_runtime/models.py
- 创建：src/novelvideo/text_task_runtime/settings.py
- 测试：tests/test_text_task_runtime_settings.py

- [ ] **步骤 1：编写失败测试**

~~~~python
def test_project_override_is_frozen_and_secret_free():
    snapshot = resolve_agent_task_route(
        task_role="director_plan",
        global_route=AgentTaskRoute(runtime="model_api", model="deepseek-v4-flash"),
        project_override=AgentTaskRouteOverride(
            runtime="codex", model="gpt-5.6-sol", reasoning_effort="high"
        ),
    )
    assert snapshot.runtime == "codex"
    assert snapshot.source == "project"
    assert "api_key" not in snapshot.model_dump(mode="json")
~~~~

- [ ] **步骤 2：运行测试验证失败**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_text_task_runtime_settings.py -q
~~~~

预期：无法导入 novelvideo.text_task_runtime。

- [ ] **步骤 3：实现 AgentTaskRoute、AgentTaskRouteOverride、AgentTaskRoutingConfig、AgentTaskRouteSnapshot。runtime 只允许 codex/model_api，fallback 只允许 stop/retry/explicit_backup。**

- [ ] **步骤 4：使用独立键 text_task_routing_v1 持久化；实现 load_global_routes()、load_project_routes(ctx)、resolve_agent_task_route()。**

- [ ] **步骤 5：验证并提交**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_text_task_runtime_settings.py -q
git add src/novelvideo/text_task_runtime tests/test_text_task_runtime_settings.py
git commit -m "feat(runtime): define task route snapshots"
~~~~

### 任务 2：统一 structured runtime

**文件：**
- 创建：src/novelvideo/text_task_runtime/runtime.py
- 创建：src/novelvideo/text_task_runtime/__init__.py
- 测试：tests/test_text_task_runtime_routing.py

- [ ] **步骤 1：编写协议测试**

~~~~python
class StructuredTextRuntime(Protocol):
    async def run_structured(
        self, *, prompt: str, output_type: type[T], system_prompt: str = ""
    ) -> T: ...
~~~~

同一输出模型必须能通过 fake Codex 和 fake model_api 返回。

- [ ] **步骤 2：实现 CodexStructuredRuntime，复用 CodexCliStructuredBackend，只允许只读 structured 调用，不复用聊天线程/MCP 写权限。**

- [ ] **步骤 3：实现 ModelApiStructuredRuntime，内部使用 get_newapi_text_pydantic_model()；tool_choice/model settings 只传给 model_api。**

- [ ] **步骤 4：实现 text_task_runtime_scope(snapshot) 和 current_text_task_runtime()，finally 中 reset ContextVar。**

- [ ] **步骤 5：验证并提交**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_text_task_runtime_routing.py -q
git add src/novelvideo/text_task_runtime tests/test_text_task_runtime_routing.py
git commit -m "feat(runtime): add structured runtime adapters"
~~~~

### 任务 3：任务入口冻结与传播

**文件：**
- 修改：src/novelvideo/task_backend/registry.py
- 修改：src/novelvideo/task_backend/run_core.py
- 修改：src/novelvideo/ports/local/tasks.py
- 测试：tests/test_task_run_core_text_runtime.py

- [ ] **步骤 1：编写并发隔离测试**：两个任务分别选择 Codex/model_api，并行后各自读取正确 snapshot，退出后 ContextVar 为空。

- [ ] **步骤 2：扩展注册契约**

~~~~python
@dataclass(frozen=True)
class ProjectTaskRunnerRegistration:
    runner: ProjectTaskRunner
    text_task_role: str | None = None
~~~~

- [ ] **步骤 3：在 enqueue 时解析并写入 envelope["agent_route_snapshot"]，worker 不再读取最新设置。**

- [ ] **步骤 4：run_project_task_core_sync() 校验 snapshot、建立 scope，并将脱敏 snapshot 合入 task metadata。**

- [ ] **步骤 5：验证正常、异常、取消路径并提交**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_task_run_core_text_runtime.py tests\contract\test_l014_inline_task_backend_concurrency.py -q
git add src/novelvideo/task_backend/registry.py src/novelvideo/task_backend/run_core.py src/novelvideo/ports/local/tasks.py tests/test_task_run_core_text_runtime.py
git commit -m "feat(tasks): freeze agent routes at enqueue"
~~~~

### 任务 4：接管 Cognee、DirectorPlan 和 H3

**文件：**
- 修改：src/novelvideo/knowledge_runtime/context.py
- 修改：src/novelvideo/director_plan/planner.py
- 修改：src/novelvideo/media_capabilities/video/h3_episode_pack.py
- 修改：src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py
- 修改：src/novelvideo/task_backend/runners/episode_import.py、episode_graph.py、director_plan.py、narrative_group_video.py

- [ ] **步骤 1：扩展测试**：Cognee 选 model_api 时不实例化 Codex；DirectorPlan/H3 选 Codex 时不调用 DeepSeek 工厂。

- [ ] **步骤 2：build_project_knowledge_runtime() 使用 current_text_task_runtime()；Ollama embedding 保持独立。**

- [ ] **步骤 3：DirectorPlanner 与 H3 优化器接收 StructuredTextRuntime，移除内部 _default_director_model_factory() 隐式读取。**

- [ ] **步骤 4：注册任务职责 episode_normalization、knowledge_extraction、director_plan、h3_episode_pack、h3_segment_repair。**

- [ ] **步骤 5：验证并提交**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_knowledge_runtime.py tests/test_cognee_codex_adapter.py tests/test_task_episode_import_runner.py tests/test_task_episode_graph_runner.py tests/test_task_director_plan_runner.py tests/media_capabilities/video/test_h3_episode_pack.py -q
git add src/novelvideo/knowledge_runtime src/novelvideo/director_plan src/novelvideo/media_capabilities/video src/novelvideo/task_backend/runners tests
git commit -m "feat(runtime): route knowledge and director tasks"
~~~~

### 任务 5：任务路由 API

**文件：**
- 修改：src/novelvideo/api/routes/model_gateway.py
- 创建：tests/test_api_text_task_routes.py

- [ ] **步骤 1：编写 GET/PUT/effective/status/test 契约，项目覆盖要求 editor 权限，响应脱敏。**

- [ ] **步骤 2：实现端点**

~~~~text
GET  /model-gateway/task-runtime/config
PUT  /model-gateway/task-runtime/config
GET  /projects/{project}/task-runtime/config
PUT  /projects/{project}/task-runtime/config
POST /projects/{project}/task-runtime/effective-preview
GET  /model-gateway/task-runtime/status
POST /model-gateway/task-runtime/test
~~~~

- [ ] **步骤 3：status 同时报告 Codex CLI 与 model_api capability；test 只执行一个最小 structured 请求。**

- [ ] **步骤 4：验证并提交**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_text_task_routes.py tests/test_model_gateway_settings.py -q
git add src/novelvideo/api/routes/model_gateway.py tests/test_api_text_task_routes.py
git commit -m "feat(api): expose task runtime routing"
~~~~

### 任务 6：设置页路由表

**文件：**
- 创建：frontend/src/components/settings/text-task-routing-panel.tsx
- 修改：frontend/src/lib/queries/model-gateway.ts
- 修改：frontend/src/components/settings/settings-dialog.tsx
- 修改：frontend/src/components/settings/knowledge-runtime-section.tsx
- 修改：frontend/public/locales/zh/translation.json、en/translation.json
- 测试：frontend/src/__tests__/components/settings/text-task-routing-panel.test.tsx

- [ ] **步骤 1：编写失败测试**：展示任务、运行时、模型、推理强度、Skill、来源、有效值和恢复继承。

- [ ] **步骤 2：添加 useTaskRuntimeConfig、useSaveTaskRuntimeConfig、useTaskRuntimeStatus、useTestTaskRuntime。**

- [ ] **步骤 3：实现路由表；Codex 行不显示 API Key，model_api 行引用兼容网关凭证。**

- [ ] **步骤 4：知识运行时按 effective route 展示，不再写死 Cognee=Codex。**

- [ ] **步骤 5：验证并提交**

~~~~powershell
Set-Location frontend
corepack pnpm test -- src/__tests__/components/settings/text-task-routing-panel.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx
corepack pnpm exec tsc -b --pretty false
git add frontend/src frontend/public/locales
git commit -m "feat(settings): configure task runtimes"
~~~~

### 任务 7：完整回归

- [ ] **步骤 1：运行后端定向套件与 Ruff。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_text_task_runtime_settings.py tests/test_text_task_runtime_routing.py tests/test_task_run_core_text_runtime.py tests/test_api_text_task_routes.py tests/test_api_knowledge_runtime.py tests/test_task_director_plan_runner.py -q
.\.venv\Scripts\python.exe -m ruff check src/novelvideo/text_task_runtime src/novelvideo/task_backend/run_core.py src/novelvideo/api/routes/model_gateway.py src/novelvideo/knowledge_runtime
~~~~

- [ ] **步骤 2：运行前端定向测试与 TypeScript。**

- [ ] **步骤 3：真实冒烟各执行一次 Codex structured 与 DeepSeek structured，记录 task manifest，不触发媒体。**

- [ ] **步骤 4：确认没有聊天域改动、密钥和绝对用户路径进入 Git。**

