# 统一导演工作台破坏性切换实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（- [ ]）语法来跟踪进度。

**目标：** 以 DirectorPlanRevision 为唯一导演事实，建立强类型 ProductionPlan/ExecutionState，并用统一三栏工作台取代旧审核页和叙事组生产页。

**架构：** DirectorPlanRevision 保存不可变叙事与镜头；GenerationBatch/VideoSegment 形成确定性 ProductionPlan；供应商状态写入 revision-scoped ExecutionState。旧数据是测试数据，仅提供显式清理与重建，不实现兼容读取或自动迁移。

**技术栈：** Python、Pydantic、FastAPI、React、TanStack Query、Zustand、pytest、Vitest。

---

## 文件结构

- 创建 director_plan/production_state.py、production_store.py、workbench.py。
- 创建 api/routes/director_workbench.py。
- 修改 DirectorPlan generation/store 与生产 runners。
- 创建 frontend/lib/queries/director-workbench.ts 和 components/episode/director-workbench/*。
- 修改 beats route 与 selection store，最后删除两套旧入口和 mutable sidecar。

### 任务 1：强类型 ProductionPlan 与 ExecutionState

**文件：**
- 修改：src/novelvideo/director_plan/models.py
- 修改：src/novelvideo/director_plan/generation.py
- 创建：src/novelvideo/director_plan/production_state.py
- 测试：tests/director_plan/test_production_state.py

- [ ] **步骤 1：编写失败测试**

~~~~python
def test_two_shots_plan_a_diptych(revision):
    plan = build_production_plan(revision)
    assert plan.generation_batches[0].layout == "diptych"
    assert plan.generation_batches[0].shot_ids == ("shot-1", "shot-2")
~~~~

同时验证 1/2/3/4 镜头映射 single/diptych/triptych/grid_2x2，以及相同输入产生相同 ID。

- [ ] **步骤 2：运行测试确认缺少 ProductionPlan。**

- [ ] **步骤 3：定义 ProductionPlan、GenerationBatchPlan、VideoSegmentPlan，以及 ProductionExecutionState、GenerationBatchState、VideoSegmentState；状态禁止自由 dict。**

- [ ] **步骤 4：稳定 ID 包含 revision/group/ordered shot IDs/style snapshot；执行状态包含 provider、request/job ID、stage、heartbeat、result、error、quality/cleanup report。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\director_plan\test_generation.py tests\director_plan\test_production_state.py -q
git add src/novelvideo/director_plan tests/director_plan
git commit -m "feat(director): type production plans and state"
~~~~

### 任务 2：revision-scoped ProductionStore

**文件：**
- 创建：src/novelvideo/director_plan/production_store.py
- 创建：tests/director_plan/test_production_store.py
- 创建：scripts/reset_test_director_data.py

- [ ] **步骤 1：测试原子保存、单 Segment 更新不覆盖兄弟、旧 revision 任务不能写 active revision、并发 CAS 冲突。**

- [ ] **步骤 2：实现 director_plans/epXXX/{revision_id}/production.json，包含 schema_version 和 production_plan_hash。**

- [ ] **步骤 3：所有 mutation 要求 expected_revision_id 和 expected_plan_hash，不匹配抛 ProductionStateConflict。**

- [ ] **步骤 4：实现显式测试清理命令；仅删除指定项目的 DirectorPlan/旧 .narrative_groups 数据，先解析并验证目标位于指定项目目录。普通启动不调用。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\director_plan\test_production_store.py -q
git add src/novelvideo/director_plan/production_store.py scripts/reset_test_director_data.py tests/director_plan/test_production_store.py
git commit -m "feat(director): persist revision production state"
~~~~

### 任务 3：统一 Workbench 服务与 API

**文件：**
- 创建：src/novelvideo/director_plan/workbench.py
- 创建：src/novelvideo/api/routes/director_workbench.py
- 创建：tests/director_plan/test_workbench_snapshot.py
- 创建：tests/test_api_director_workbench.py
- 修改：src/novelvideo/api/routes/__init__.py

- [ ] **步骤 1：编写 snapshot 契约**：一次响应包含 revisions、selected revision、groups、shots、batches、segments、execution state，关联只用稳定 ID。

- [ ] **步骤 2：实现 DirectorWorkbenchSnapshot，不投影旧 NarrativeGroup sidecar。**

- [ ] **步骤 3：实现端点。**

~~~~text
GET  /projects/{project}/episodes/{episode}/director-workbench
POST /.../director-workbench/revisions
POST /.../director-workbench/{revision}/edits
POST /.../director-workbench/{revision}/activate
GET  /.../director-workbench/{revision}/impact-preview
POST /.../batches/{batch}/generate
POST /.../batches/{batch}/regenerate
POST /.../batches/{batch}/split
POST /.../segments/prompt-pack
POST /.../segments/{segment}/generate
POST /.../segments/{segment}/repair
POST /.../segments/{segment}/retry
~~~~

- [ ] **步骤 4：mutation 校验 revision/plan hash；错误带 stage、runtime/provider、task/request/job ID、retryable。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\director_plan\test_workbench_snapshot.py tests\test_api_director_workbench.py -q
git add src/novelvideo/director_plan/workbench.py src/novelvideo/api/routes/director_workbench.py src/novelvideo/api/routes/__init__.py tests
git commit -m "feat(api): add unified director workbench"
~~~~

### 任务 4：生产 Runner 写 ExecutionState

**文件：**
- 修改：src/novelvideo/task_backend/runners/director_plan.py
- 修改：src/novelvideo/task_backend/runners/narrative_group.py
- 修改：src/novelvideo/task_backend/runners/narrative_group_video.py
- 修改：src/novelvideo/media_capabilities/video/h3_episode_pack.py
- 修改对应测试。

- [ ] **步骤 1：回归测试整集 H3 pack 一次生成、单 Segment 失败只修复该 Segment、旧 revision 回包被拒绝。**

- [ ] **步骤 2：DirectorPlan 创建 revision 后立即派生 ProductionPlan 并初始化 ProductionStore。**

- [ ] **步骤 3：图片 batch 按稳定 ID 写 queued/requested/polling/persisted/quality_checked，并保存切分 cleanup report。**

- [ ] **步骤 4：视频 runner 按 segment ID 写状态；episode pack cache key 包含 revision/style/frame/compiler hash。**

- [ ] **步骤 5：默认硬切，仅确定性关系生成转场和声音先入/延出。**

- [ ] **步骤 6：验证并提交。**

~~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\director_plan\test_m3_production_integration.py tests\director_plan\test_video_segment_runner_v2.py tests\test_task_narrative_group_video_runner.py tests\media_capabilities\video\test_h3_episode_pack.py -q
git add src/novelvideo/task_backend/runners src/novelvideo/media_capabilities/video tests
git commit -m "feat(director): write production overlays"
~~~~

### 任务 5：前端 query、selection 与三栏 shell

**文件：**
- 创建：frontend/src/lib/queries/director-workbench.ts
- 创建：frontend/src/components/episode/director-workbench/director-workbench.tsx
- 创建：frontend/src/components/episode/director-workbench/narrative-column.tsx
- 创建：frontend/src/components/episode/director-workbench/shot-column.tsx
- 创建：frontend/src/components/episode/director-workbench/production-column.tsx
- 创建：frontend/src/components/episode/director-workbench/workbench-toolbar.tsx
- 修改：frontend/src/stores/episode-workbench-store.ts
- 测试：frontend/src/__tests__/components/episode/director-workbench.test.tsx

- [ ] **步骤 1：测试关联**：选 group 高亮 shots/batches/segments；选 shot 高亮所属 batch/segment；切 revision 清空失效 selection。

- [ ] **步骤 2：query 类型严格镜像 DTO；mutation 只 invalidate 当前 project/episode/revision。**

- [ ] **步骤 3：store 使用 revisionId/groupId/shotId/productionId，删除 narrativeGroupSelectionByScope。**

- [ ] **步骤 4：实现桌面三栏；窄屏用同一状态的 Tabs，不建第二套业务逻辑。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
Set-Location frontend
corepack pnpm test -- src/__tests__/components/episode/director-workbench.test.tsx src/__tests__/stores/episode-workbench-store-v2.test.ts
corepack pnpm exec tsc -b --pretty false
git add frontend/src/lib/queries/director-workbench.ts frontend/src/components/episode/director-workbench frontend/src/stores/episode-workbench-store.ts frontend/src/__tests__
git commit -m "feat(director): build unified workbench shell"
~~~~

### 任务 6：迁移编辑和生产控件

**文件：**
- 修改：frontend/src/components/episode/director-workbench/*
- 复用并拆纯展示：旧 group-grid-stage、group-video-stage、group-video-result
- 修改对应组件测试。

- [ ] **步骤 1：左栏接入 revision 历史、comparison、validation、split/merge/reorder、impact preview 和 activation。**

- [ ] **步骤 2：中栏接入 Shot 编辑、拖动、跨组移动和 batch 布局/画幅/分辨率/模型/参考图。**

- [ ] **步骤 3：右栏接入 H3 pack、quality report、真实模型、Segment 生成/修复/重试和诊断。**

- [ ] **步骤 4：重生成 batch 只替换该 batch 与切分图；重试 segment 只替换该 segment。**

- [ ] **步骤 5：验证并提交。**

~~~~powershell
Set-Location frontend
corepack pnpm test -- src/__tests__/components/episode/director-workbench.test.tsx src/__tests__/components/episode/narrative-workbench/group-grid-stage-v2.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx
git add frontend/src
git commit -m "feat(director): connect workbench production controls"
~~~~

### 任务 7：破坏性切换并删除旧链路

**文件：**
- 修改：frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx
- 删除：旧 director-review 与 narrative-group-workbench 入口组件。
- 修改/删除：src/novelvideo/narrative_groups/models.py、service.py、api/routes/narrative_groups.py 中旧 sidecar/rebuild/video-plan 契约。
- 更新相关测试。

- [ ] **步骤 1：beats route 只渲染 DirectorWorkbench，先运行路由集成测试。**

- [ ] **步骤 2：删除 workbenchMode、DirectorReviewWorkbench、NarrativeGroupWorkbench 和仅服务旧页的 list/inspector。**

- [ ] **步骤 3：删除 /narrative-groups/rebuild、旧 video plan API、load_effective_groups() 和 mutable sidecar 读写；媒体执行改经 workbench API。**

- [ ] **步骤 4：删除兼容/migration 断言，新增“检测旧结构要求显式重建”断言。**

- [ ] **步骤 5：确认旧符号无引用并提交。**

~~~~powershell
rg -n "workbenchMode|NarrativeGroupWorkbench|DirectorReviewWorkbench|load_effective_groups|narrative-groups/rebuild" frontend/src src/novelvideo tests
.\.venv\Scripts\python.exe -m pytest tests\director_plan tests\test_api_director_workbench.py -q
Set-Location frontend
corepack pnpm test -- src/__tests__/routes/episodes-beats-sub-integration.test.ts src/__tests__/components/episode/director-workbench.test.tsx
corepack pnpm build
git add src/novelvideo frontend/src tests
git commit -m "refactor(director): remove legacy workbenches"
~~~~

### 任务 8：新项目真实闭环

- [ ] **步骤 1：用显式命令清理一个测试项目旧导演数据并重新导入一集短剧本。**

- [ ] **步骤 2：真测 DirectorPlan→ProductionPlan→一个 GRSAI batch→切分→H3 pack→一个 MiniMax Segment，记录 request/job ID。**

- [ ] **步骤 3：验证 task manifest 中 runtime/model/Skill/StyleSnapshot 与设置一致。**

- [ ] **步骤 4：运行 Ruff、TypeScript、定向回归和 git diff --check，仅提交必要修复。**

