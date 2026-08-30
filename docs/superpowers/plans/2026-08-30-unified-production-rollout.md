# 统一生产能力收口实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（- [ ]）语法来跟踪进度。

**目标：** 按安全依赖顺序交付任务运行时路由、动态风格目录和统一导演工作台，并最终完成破坏性旧链路删除。

**架构：** 三个子计划分别建立独立可测试边界；路由与风格快照先成为稳定基础，导演工作台随后消费二者。只有新链路真测通过后才清理旧测试数据和删除旧入口。

**技术栈：** 参见三个子计划。

---

## 子计划

1. docs/superpowers/plans/2026-08-30-agent-task-runtime-routing.md
2. docs/superpowers/plans/2026-08-30-extension-style-catalog-live-reload.md
3. docs/superpowers/plans/2026-08-30-director-workbench-destructive-cutover.md

### 任务 1：并行完成两个基础子计划

- [ ] **步骤 1：工作流 A 执行任务运行时路由计划的任务 1–6。**
- [ ] **步骤 2：工作流 B 并行执行风格目录计划的任务 1–4。**
- [ ] **步骤 3：分别通过各自定向测试；禁止两个工作流共同修改 settings-dialog.tsx 和翻译文件。先由运行时工作流拥有共享前端文件，风格工作流在其提交后再变基或顺序应用共享文件改动。**
- [ ] **步骤 4：运行基础集成检查。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_text_task_runtime_settings.py tests/test_text_task_runtime_routing.py tests/test_extension_style_registry.py tests/test_style_resolver.py -q
Set-Location frontend
corepack pnpm exec tsc -b --pretty false
~~~~

预期：全部 PASS，TypeScript 退出码 0。

### 任务 2：建立导演后端并接入基础快照

- [ ] **步骤 1：执行导演计划任务 1–4。**
- [ ] **步骤 2：DirectorPlan runner 从 AgentTaskRouteSnapshot 读取真实 planner runtime，不再内部选 DeepSeek。**
- [ ] **步骤 3：ProductionPlan 固定引用 StyleSnapshot ID 和 hash；目录热重载不改变已生成 plan。**
- [ ] **步骤 4：运行交叉契约。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_task_run_core_text_runtime.py tests/test_style_resolver.py tests/director_plan/test_production_state.py tests/director_plan/test_workbench_snapshot.py tests/test_api_director_workbench.py -q
~~~~

### 任务 3：交付三套前端

- [ ] **步骤 1：完成运行时设置页。**
- [ ] **步骤 2：完成三组风格页。**
- [ ] **步骤 3：执行导演计划任务 5–6，完成三栏工作台。**
- [ ] **步骤 4：运行前端组合测试。**

~~~~powershell
Set-Location frontend
corepack pnpm test -- src/__tests__/components/settings/text-task-routing-panel.test.tsx src/__tests__/routes/styles.catalog-runtime.test.tsx src/__tests__/components/episode/director-workbench.test.tsx
corepack pnpm exec tsc -b --pretty false
~~~~

### 任务 4：破坏性切换

- [ ] **步骤 1：完成导演计划任务 7，但删除前先确认新 route 已只依赖 director-workbench query。**
- [ ] **步骤 2：显式清理指定测试项目旧导演数据；不修改其他项目。**
- [ ] **步骤 3：运行 rg 门禁，确保旧双工作台和 mutable sidecar 权威路径已消失。**
- [ ] **步骤 4：提交破坏性切换，commit message 必须注明旧测试数据需要重建。**

### 任务 5：最小真实验收

- [ ] **步骤 1：真测一条 Codex structured 和一条 DeepSeek structured，核对 execution manifest。**
- [ ] **步骤 2：同一后端进程调用 style status→reload→list，确认 6/18/custom。**
- [ ] **步骤 3：新测试项目跑一集 DirectorPlan，并只生成一个 GRSAI batch 和一个 MiniMax Segment。**
- [ ] **步骤 4：记录真实 task/request/job ID，检查局部 retry，不进行全量媒体生成。**
- [ ] **步骤 5：运行最终门禁。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/test_text_task_runtime_settings.py tests/test_text_task_runtime_routing.py tests/test_extension_style_registry.py tests/test_api_styles.py tests/director_plan tests/test_api_director_workbench.py -q
.\.venv\Scripts\python.exe -m ruff check src/novelvideo/text_task_runtime src/novelvideo/extension_styles src/novelvideo/director_plan src/novelvideo/api/routes
Set-Location frontend
corepack pnpm test -- src/__tests__/components/settings/text-task-routing-panel.test.tsx src/__tests__/routes/styles.catalog-runtime.test.tsx src/__tests__/components/episode/director-workbench.test.tsx
corepack pnpm build
Set-Location ..
git diff --check
~~~~

### 任务 6：最终审查

- [ ] **步骤 1：规格覆盖检查**：逐条核对设计规格第 12 节 12 项验收标准。
- [ ] **步骤 2：安全检查**：无密钥、绝对用户路径、未限定删除命令或静默 fallback。
- [ ] **步骤 3：数据边界检查**：DirectorPlanRevision 是唯一导演事实；ProductionPlan 可确定性重建；ExecutionState 不反写导演结构。
- [ ] **步骤 4：提交最终修复并输出真实验证证据。**

