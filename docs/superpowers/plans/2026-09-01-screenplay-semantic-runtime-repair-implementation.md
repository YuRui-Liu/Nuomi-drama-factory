# 导演拆解 Runtime 自动修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（\`- [ ]\`）语法来跟踪进度。

**目标：** 增加独立的 \`screenplay_semantic_repair\` Runtime 任务，只修复校验失败场次，最多两轮，生成一个待人工激活的子版本。

**架构：** 领域服务承担结构化修复契约、两轮调度、确定性合并和全量重验；独立 Runner 冻结版本与 Runtime 路由并发布进度；API 负责入队；前端复用任务中心展示状态并刷新版本。层间固定使用 \`scene_id + beats\` 契约。

**技术栈：** Python 3.11、Pydantic、FastAPI、pytest、React 19、TanStack Query、Vitest、TypeScript。

---

## 文件结构

- 新建 \`screenplay_semantics/repair.py\`、\`repair_prompts.py\` 和 \`test_repair.py\`。
- 修改 \`screenplay_semantics/validation.py\`，增加完整版本重验。
- 新建 \`task_backend/runners/screenplay_semantic_repair.py\` 及 Runner 测试。
- 修改 Runner 注册、task identity、screenplay semantics API 与 API 测试。
- 精确带入主工作区尚未提交的 screenplay workbench/query 基线，不复制其他脏改动。
- 修改工作台、query、文本 Runtime 设置及对应 Vitest。

### 任务 1：领域修复引擎

**文件：**
- 创建：\`src/novelvideo/screenplay_semantics/repair.py\`
- 创建：\`src/novelvideo/screenplay_semantics/repair_prompts.py\`
- 修改：\`src/novelvideo/screenplay_semantics/validation.py\`
- 创建：\`tests/screenplay_semantics/test_repair.py\`

- [ ] **步骤 1：先写失败测试**

覆盖：通过场次不变；第二轮只处理残余错误；最多两轮；乱序返回仍按 scene ordinal 合并；单场失败保留原 Beat；越权事实记录 \`repair_contract_violation\`；最终只保存一个子版本。

\`\`\`python
async def test_second_round_only_retries_remaining_failed_scenes(tmp_path):
    calls = []
    service = ScreenplaySemanticRepairService(
        ScreenplaySemanticStore(tmp_path), invoke=scripted_invoker(calls)
    )
    child = await service.repair(base_revision(), max_rounds=2, concurrency=3)
    assert calls == [(1, ("scene-1", "scene-2")), (2, ("scene-2",))]
    assert child.parent_revision_id == "sem-base"
    assert child.status == "review_required"
\`\`\`

- [ ] **步骤 2：运行红灯**

\`\`\`powershell
$env:PYTHONPATH="$PWD\\src"
& '..\\..\\.venv\\Scripts\\python.exe' -m pytest -p no:cacheprovider tests\\screenplay_semantics\\test_repair.py -q --basetemp=.pytest_tmp\\repair-red
\`\`\`

预期：模块缺失失败。

- [ ] **步骤 3：实现最小接口**

\`\`\`python
class SceneRepairDraft(FrozenModel):
    scene_id: str
    beats: tuple[DramaticBeatDraft, ...]

class ScreenplaySemanticRepairService:
    async def repair(
        self, base: ScreenplaySemanticRevision, *, max_rounds: int = 2,
        concurrency: int = 3, before_commit: Callable[[], None] | None = None,
        on_progress: Callable[[RepairProgress], None] | None = None,
    ) -> ScreenplaySemanticRevision: ...
\`\`\`

按 \`scene_id\` 聚合 error，只替换失败场次；每轮完整重验；第二轮只提交残余失败场次；最终只调用一次 \`store.save()\`。

- [ ] **步骤 4：实现契约防线**

合并前验证 scene id、source range、角色、对白 ID 和 script facts 原文证据；原 Scene 及通过场次 Beat 不变。拒绝越权结果并保留原 Beat。

- [ ] **步骤 5：运行绿灯并提交**

\`\`\`powershell
$env:PYTHONPATH="$PWD\\src"
& '..\\..\\.venv\\Scripts\\python.exe' -m pytest -p no:cacheprovider tests\\screenplay_semantics -q --basetemp=.pytest_tmp\\repair-green
git add src/novelvideo/screenplay_semantics tests/screenplay_semantics/test_repair.py
git commit -m "feat(screenplay): add bounded semantic repair service"
\`\`\`

### 任务 2：独立任务、API 和 Runtime 路由

**文件：**
- 创建：\`src/novelvideo/task_backend/runners/screenplay_semantic_repair.py\`
- 修改：\`src/novelvideo/task_backend/runners/__init__.py\`
- 修改：\`src/novelvideo/task_identity.py\`
- 修改：\`src/novelvideo/api/routes/screenplay_semantics.py\`
- 创建：\`tests/test_task_screenplay_semantic_repair_runner.py\`
- 修改：\`tests/test_api_screenplay_semantics.py\`
- 修改：\`tests/test_task_run_core_text_runtime.py\`

- [ ] **步骤 1：先写失败测试**

\`\`\`python
def test_repair_queues_frozen_revisions(client, backend):
    response = client.post(
        "/api/v1/projects/demo/episodes/1/screenplay-semantics/sem-1/repair",
        json={"concurrency": 3},
    )
    assert response.status_code == 202
    assert backend.calls[0]["task_type"] == "screenplay_semantic_repair"
    assert backend.calls[0]["payload"]["semantic_revision_id"] == "sem-1"
    assert backend.calls[0]["payload"]["max_rounds"] == 2
\`\`\`

同时覆盖 404、来源冲突 409、专用 text role、提交前版本冲突和进度统计。

- [ ] **步骤 2：运行红灯**

\`\`\`powershell
$env:PYTHONPATH="$PWD\\src"
& '..\\..\\.venv\\Scripts\\python.exe' -m pytest -p no:cacheprovider tests\\test_api_screenplay_semantics.py tests\\test_task_screenplay_semantic_repair_runner.py -q --basetemp=.pytest_tmp\\runner-red
\`\`\`

- [ ] **步骤 3：实现 API**

新增 \`RepairSemanticRequest(concurrency=3)\`，禁止额外字段。端点为 \`POST /projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/repair\`；scope 为 \`revision:{source_revision}:semantic:{revision_id}\`；payload 固定 \`max_rounds=2\`。

- [ ] **步骤 4：实现并注册 Runner**

\`\`\`python
register_project_task_runner(
    "screenplay_semantic_repair",
    run_screenplay_semantic_repair,
    text_task_role="screenplay_semantic_repair",
)
\`\`\`

Runner 前后两次校验来源和基础版本，结果包含基础/子版本、轮次、修复/失败场次、剩余问题和契约违规。

- [ ] **步骤 5：运行绿灯并提交**

\`\`\`powershell
$env:PYTHONPATH="$PWD\\src"
& '..\\..\\.venv\\Scripts\\python.exe' -m pytest -p no:cacheprovider tests\\test_api_screenplay_semantics.py tests\\test_task_screenplay_semantic_repair_runner.py tests\\test_task_run_core_text_runtime.py tests\\test_text_task_runtime_settings.py tests\\test_text_task_runtime_routing.py -q --basetemp=.pytest_tmp\\runner-green
git add src/novelvideo/task_backend/runners src/novelvideo/task_identity.py src/novelvideo/api/routes/screenplay_semantics.py tests/test_api_screenplay_semantics.py tests/test_task_screenplay_semantic_repair_runner.py tests/test_task_run_core_text_runtime.py
git commit -m "feat(tasks): route screenplay repair through runtime"
\`\`\`

### 任务 3：工作台与设置入口

**文件：**
- 创建/带入：\`frontend/src/components/episode/screenplay-workbench/*.tsx\`
- 创建/带入：\`frontend/src/lib/queries/screenplay-semantics.ts\`
- 修改：\`frontend/src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx\`
- 修改：\`frontend/src/components/settings/text-runtime-panel.tsx\`
- 创建/修改：对应 workbench、query、settings Vitest。

- [ ] **步骤 1：带入最小 UI 基线**

从主工作区未跟踪文件带入 screenplay workbench/query 和两份测试；从 route diff 只带入 Workbench import/render。

- [ ] **步骤 2：先写失败测试**

\`\`\`tsx
it("repairs with runtime but still requires manual activation", async () => {
  render(<ScreenplayWorkbench project="demo" episode={1} />);
  expect(screen.getByRole("button", { name: "委托 Runtime 修复" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "激活拆解" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "委托 Runtime 修复" }));
  expect(repair.mutate).toHaveBeenCalledWith({ revisionId: "sem-1" });
});
\`\`\`

还要验证无 error 时禁用原因、通过后只解锁激活、query 路径以 \`/repair\` 结尾、设置页显示独立角色。

- [ ] **步骤 3：运行红灯**

\`\`\`powershell
corepack pnpm --dir frontend exec vitest run src/__tests__/components/episode/screenplay-workbench.test.tsx src/__tests__/lib/queries/screenplay-semantics.test.tsx src/__tests__/components/settings/text-runtime-panel.test.tsx
\`\`\`

- [ ] **步骤 4：实现 query、按钮和进度**

新增 \`useRepairScreenplaySemantics\`，发送 \`{concurrency: 3}\`。按钮顺序固定为解析、Runtime 修复、激活、生成镜头方案；禁用态显示原因。复用任务中心显示 Runtime/模型、轮次、目标场次、已解决和剩余问题；完成后刷新并选中新子版本，绝不自动激活。

- [ ] **步骤 5：增加设置角色**

在已有任务路由 UI 增加“导演拆解修复”键 \`screenplay_semantic_repair\`。若面板仅支持全局普通文本模型，则明确显示该角色继承全局路由，不新增无后端契约的伪保存接口。

- [ ] **步骤 6：运行绿灯、类型检查并提交**

\`\`\`powershell
corepack pnpm --dir frontend exec vitest run src/__tests__/components/episode/screenplay-workbench.test.tsx src/__tests__/lib/queries/screenplay-semantics.test.tsx src/__tests__/components/settings/text-runtime-panel.test.tsx
corepack pnpm --dir frontend exec tsc -b --pretty false
git add frontend/src/components/episode/screenplay-workbench frontend/src/lib/queries/screenplay-semantics.ts frontend/src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx frontend/src/components/settings/text-runtime-panel.tsx frontend/src/__tests__
git commit -m "feat(frontend): add runtime repair workflow"
\`\`\`

### 任务 4：集中验证

- [ ] **步骤 1：后端集中回归**

\`\`\`powershell
$env:PYTHONPATH="$PWD\\src"
& '..\\..\\.venv\\Scripts\\python.exe' -m pytest -p no:cacheprovider tests\\screenplay_semantics tests\\test_api_screenplay_semantics.py tests\\test_task_screenplay_semantics_runner.py tests\\test_task_screenplay_semantic_repair_runner.py tests\\test_task_run_core_text_runtime.py tests\\test_text_task_runtime_settings.py tests\\test_text_task_runtime_routing.py -q --basetemp=.pytest_tmp\\final
\`\`\`

- [ ] **步骤 2：前端集中回归和类型检查**

\`\`\`powershell
corepack pnpm --dir frontend exec vitest run src/__tests__/components/episode/screenplay-workbench.test.tsx src/__tests__/lib/queries/screenplay-semantics.test.tsx src/__tests__/components/settings/text-runtime-panel.test.tsx
corepack pnpm --dir frontend exec tsc -b --pretty false
\`\`\`

- [ ] **步骤 3：静态检查**

\`\`\`powershell
& '..\\..\\.venv\\Scripts\\python.exe' -m ruff check src/novelvideo/screenplay_semantics src/novelvideo/task_backend/runners/screenplay_semantic_repair.py tests/screenplay_semantics/test_repair.py tests/test_task_screenplay_semantic_repair_runner.py
git diff --check
\`\`\`

- [ ] **步骤 4：规格核对**

逐项确认：两轮上限、只修失败场次、一次保存、版本冲突、契约违规、独立任务角色、人工激活、禁用原因和进度都有实现与测试证据。
