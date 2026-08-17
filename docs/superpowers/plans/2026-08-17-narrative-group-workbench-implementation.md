# 叙事组媒体生产工作台实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有多宫格、自动切分与 RunningHub Minimax H3 能力接入统一叙事组工作台，使多宫格成为默认生产链路并保留单 Beat 修复。

**架构：** 后端提供叙事组聚合资源，持久化稳定的 group/revision/cell-to-beat 映射，并在生成任务内编排自动切分。视频模型通过媒体能力目录暴露，H3 runner 按实际帧输入自动选择 FL2VA/I2VA。前端只消费后端叙事组真相，统一展示阶段、产物和恢复动作。

**技术栈：** FastAPI、Pydantic、SQLite 项目存储、现有 task backend、React 19、TanStack Query、Zustand、Vitest、pytest。

---

## 文件结构与所有权

### 后端叙事组域

- 创建 `src/novelvideo/narrative_groups/models.py`：叙事组、阶段、revision 与格位映射 DTO。
- 创建 `src/novelvideo/narrative_groups/service.py`：2×2/2×3/3×3 分组、聚合状态、revision 与动作编排。
- 创建 `src/novelvideo/api/routes/narrative_groups.py`：聚合查询、rebuild、generate、split、regenerate API。
- 修改 `src/novelvideo/api/app.py`：注册路由。
- 修改 `src/novelvideo/task_backend/runners/sketch.py`：生成成功后服务端自动切分，结果返回实际映射。
- 修改 `src/novelvideo/task_backend/runners/grid.py`：渲染格生成后自动切分并保留部分成功。
- 测试 `tests/test_narrative_group_service.py`、`tests/test_api_narrative_groups.py`、现有 sketch/grid runner 测试。

### H3 媒体目录域

- 创建 `src/novelvideo/media_capabilities/video/catalog.py`：无密钥泄露的视频模型目录。
- 创建 `src/novelvideo/media_capabilities/video/runtime.py`：生产级 H3 pipeline 组装与模式解析。
- 创建 `src/novelvideo/media_capabilities/video/profiles/minimax_h3.json`：版本化 bindings/outputs。
- 修改 `src/novelvideo/api/routes/media_capabilities.py`：`GET /video/models`。
- 修改 `src/novelvideo/api/schemas.py`：H3 模式覆盖字段。
- 修改 `src/novelvideo/api/routes/generation.py`：入队前验证并携带实际模型/模式。
- 修改 `src/novelvideo/task_backend/runners/video.py`：H3 分流到 RunningHub runtime。
- 测试 `tests/media_capabilities/video/test_catalog.py`、`test_h3_runtime.py`、`tests/test_task_video_runner_h3.py`、`tests/test_api_media_capabilities.py`。

### 前端工作台域

- 创建 `frontend/src/lib/queries/narrative-groups.ts`、`media-models.ts`：聚合资源与媒体目录 hooks。
- 创建 `frontend/src/components/episode/narrative-workbench/` 下工作台、组列表、阶段、格位检查和视频阶段组件。
- 修改 `frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`：默认呈现叙事组工作台，旧双栏作为单 Beat 修复视图。
- 修改 `frontend/src/components/episode/beat-workbench/single-beat-panel.tsx`：新视频目录、继承/临时覆盖和实际模式。
- 修改 `frontend/src/lib/query-keys.ts`、`frontend/src/stores/episode-workbench-store.ts`。
- 测试 queries、store、组件、路由合同与构建。

## 共享合同（先锁定）

```json
{
  "id": "ng-01",
  "ordinal": 1,
  "beat_ids": ["beat-1", "beat-2"],
  "layout": {"rows": 2, "columns": 2, "capacity": 4},
  "stages": {
    "sketch": {"status": "completed", "revision": 1},
    "render": {"status": "running", "revision": 2},
    "video": {"status": "pending", "revision": 0}
  },
  "cell_to_beat": [{"cell": 0, "beat_id": "beat-1"}],
  "errors": []
}
```

视频目录项固定为：

```json
{
  "id": "runninghub:minimax-h3",
  "label": "MiniMax H3",
  "provider": "runninghub",
  "available": true,
  "supported_modes": ["auto", "i2va", "fl2va"],
  "default_mode": "auto"
}
```

任务 scope 固定为 `group_{group_id}_{stage}_r{revision}`。任务结果必须包含 `group_id`、`stage`、`revision`、`cell_to_beat`、`actual_provider`、`actual_model`、`actual_mode`。

### 任务 1：叙事组纯领域模型与布局

**文件：**
- 创建：`src/novelvideo/narrative_groups/models.py`
- 创建：`src/novelvideo/narrative_groups/service.py`
- 测试：`tests/test_narrative_group_service.py`

- [ ] **步骤 1：编写失败的布局与稳定映射测试**

```python
@pytest.mark.parametrize(("count", "shape"), [(1, (2, 2)), (4, (2, 2)), (5, (2, 3)), (6, (2, 3)), (7, (3, 3)), (9, (3, 3))])
def test_layout_for_group_uses_supported_shapes(count, shape):
    assert layout_for_group(count).shape == shape

def test_group_beats_spills_after_nine_and_keeps_order():
    groups = group_beats([beat(str(index)) for index in range(11)])
    assert [len(group.beat_ids) for group in groups] == [9, 2]
    assert groups[1].cell_to_beat[0].beat_id == "9"
```

- [ ] **步骤 2：运行测试验证因模块缺失而失败**

运行：`pytest tests/test_narrative_group_service.py -q`
预期：FAIL，`ModuleNotFoundError: novelvideo.narrative_groups`。

- [ ] **步骤 3：实现不可变 DTO、布局和稳定 ID**

实现 `GridLayout`、`CellMapping`、`NarrativeGroup`、`GroupStageState`，并让 `group_beats()` 只根据已排序 Beat 输入构建稳定映射；不足格位不创建映射。

- [ ] **步骤 4：验证绿灯并提交**

运行：`pytest tests/test_narrative_group_service.py -q`
预期：全部通过。

提交：`feat: add narrative group domain model`

### 任务 2：叙事组聚合 API 与 revision

**文件：**
- 创建：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/api/app.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 测试：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写 GET/rebuild/action 合同测试**

```python
def test_generate_action_uses_stable_group_revision(client):
    response = client.post("/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch-grid/generate")
    assert response.status_code == 202
    task = response.json()["data"]
    assert task["scope"] == "group_ng-01_sketch_r1"
    assert task["metadata"]["revision"] == 1
```

覆盖：GET 返回稳定映射、rebuild 不移动旧资产、重复 action 幂等、regenerate revision +1、未知 group 为 404。

- [ ] **步骤 2：运行测试验证 404**

运行：`pytest tests/test_api_narrative_groups.py -q`
预期：FAIL，路由不存在。

- [ ] **步骤 3：实现路由与 settings/project-sidecar 持久化**

叙事组索引写入项目输出目录的版本化 sidecar JSON；写入使用临时文件 + replace。API 不从前端 scene_ref 接受分组真相。

- [ ] **步骤 4：验证路由和现有合同**

运行：`pytest tests/test_api_narrative_groups.py tests/contract/test_m09_route_contracts.py -q`
预期：全部通过。

提交：`feat: expose narrative group orchestration api`

### 任务 3：服务端自动切分与失败恢复

**文件：**
- 修改：`src/novelvideo/task_backend/runners/sketch.py`
- 修改：`src/novelvideo/task_backend/runners/grid.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 测试：`tests/test_task_narrative_group_runners.py`

- [ ] **步骤 1：编写任务编排红灯测试**

```python
async def test_successful_grid_generation_splits_before_task_completion(fake_generator, fake_splitter):
    result = await run_group_grid(group_payload(stage="render"))
    assert fake_splitter.calls == [("ng-01", 1)]
    assert result["cell_to_beat"][0] == {"cell": 0, "beat_id": "beat-1"}

async def test_split_retry_does_not_generate_again(fake_generator, failing_once_splitter):
    await retry_split(group_payload(stage="render"))
    assert fake_generator.calls == []
```

- [ ] **步骤 2：验证测试因行为缺失而失败**

运行：`pytest tests/test_task_narrative_group_runners.py -q`。

- [ ] **步骤 3：实现后端自动切分、逐格结果和幂等恢复**

生成成功后由 runner 调用 splitter；部分格失败保留成功资产并返回 `partial_failure`。单独 split action 不调用 provider，整组 regenerate 才创建新 revision。

- [ ] **步骤 4：运行聚焦与回归测试**

运行：`pytest tests/test_task_narrative_group_runners.py tests/test_api_generation_sketches.py -q`。

提交：`feat: orchestrate group grid generation and splitting`

### 任务 4：视频能力目录与 H3 可用性

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/catalog.py`
- 修改：`src/novelvideo/api/routes/media_capabilities.py`
- 测试：`tests/media_capabilities/video/test_catalog.py`
- 测试：`tests/test_api_media_capabilities.py`

- [ ] **步骤 1：编写目录红灯测试**

```python
def test_h3_is_listed_when_unconfigured_without_leaking_key(client):
    response = client.get("/api/v1/media-capabilities/video/models")
    item = next(item for item in response.json()["data"] if item["id"] == "runninghub:minimax-h3")
    assert item["available"] is False
    assert item["supported_modes"] == ["auto", "i2va", "fl2va"]
    assert "key" not in response.text.lower()
```

- [ ] **步骤 2：运行测试验证路由不存在**

运行：`pytest tests/media_capabilities/video/test_catalog.py tests/test_api_media_capabilities.py -k video_models -q`。

- [ ] **步骤 3：实现目录与 availability**

可用性仅由 RunningHub 主账号 enabled、凭据可解析、H3 workflow ID/profile 有效共同决定；未配置仍列出但禁用。

- [ ] **步骤 4：验证并提交**

运行：`pytest tests/media_capabilities/video/test_catalog.py tests/test_api_media_capabilities.py -q`。

提交：`feat: expose runninghub video model catalog`

### 任务 5：H3 生产 runtime 与 runner 分流

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/runtime.py`
- 创建：`src/novelvideo/media_capabilities/video/profiles/minimax_h3.json`
- 修改：`src/novelvideo/api/schemas.py`
- 修改：`src/novelvideo/api/routes/generation.py`
- 修改：`src/novelvideo/task_backend/runners/video.py`
- 测试：`tests/media_capabilities/video/test_h3_runtime.py`
- 测试：`tests/test_task_video_runner_h3.py`

- [ ] **步骤 1：编写自动模式与 runner 红灯测试**

```python
@pytest.mark.parametrize(("last_frame", "expected"), [("last.png", "fl2va"), (None, "i2va")])
async def test_h3_runner_selects_actual_mode(last_frame, expected, h3_service):
    result = await run_single_video(h3_payload(last_frame=last_frame))
    assert result["actual_provider"] == "runninghub"
    assert result["actual_model"] == "runninghub:minimax-h3"
    assert result["actual_mode"] == expected
```

另测：缺首帧拒绝入队、手动 FL2VA 缺尾帧为 400、H3 路径绝不调用 `create_video_generator()`。

- [ ] **步骤 2：运行测试验证旧 runner 被调用**

运行：`pytest tests/media_capabilities/video/test_h3_runtime.py tests/test_task_video_runner_h3.py -q`。

- [ ] **步骤 3：实现 profile loader、runtime factory 与 runner 分流**

内置 profile 提供 bindings/outputs，workflow ID 由 runtime settings 覆盖。`auto` 使用现有 `select_mode()`；结果写入现有 video pool 并带实际模型审计字段。

- [ ] **步骤 4：验证 H3 与旧视频回归**

运行：`pytest tests/media_capabilities/video tests/test_task_video_runner_h3.py tests/contract/test_m09_route_contracts.py -q`。

提交：`feat: route minimax h3 video through runninghub`

### 任务 6：前端查询合同与工作台状态

**文件：**
- 创建：`frontend/src/lib/queries/narrative-groups.ts`
- 创建：`frontend/src/lib/queries/media-models.ts`
- 修改：`frontend/src/lib/query-keys.ts`
- 修改：`frontend/src/stores/episode-workbench-store.ts`
- 测试：`frontend/src/__tests__/lib/queries/narrative-groups.test.tsx`
- 测试：`frontend/src/__tests__/lib/queries/media-models.test.tsx`
- 测试：`frontend/src/__tests__/stores/episode-workbench-store.test.ts`

- [ ] **步骤 1：编写 hooks 与持久化红灯测试**

断言 action body 不包含 API key，mutation 成功失效 `narrativeGroups/grids/beats`，v1 store 迁移后选中首个未完成组，项目默认模型保存 `runninghub:minimax-h3`。

- [ ] **步骤 2：运行 Vitest 验证模块缺失**

运行：`pnpm test -- src/__tests__/lib/queries/narrative-groups.test.tsx src/__tests__/lib/queries/media-models.test.tsx src/__tests__/stores/episode-workbench-store.test.ts`。

- [ ] **步骤 3：实现严格类型 hooks、query keys 和 store v2 migration**

前端不根据 scene_ref 重建分组；所有 scope 使用后端返回值。

- [ ] **步骤 4：验证并提交**

运行同上，预期全部通过。

提交：`feat: add narrative workbench client state`

### 任务 7：统一叙事组工作台 UI

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/narrative-group-list.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/group-pipeline.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/group-grid-stage.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/group-beat-inspector.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 修改：`frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/*.test.tsx`
- 测试：`frontend/src/__tests__/routes/beats-main-ui-contract.test.ts`

- [ ] **步骤 1：编写阶段、操作和路由红灯测试**

覆盖：默认显示工作台、组状态与异常数、继续生产、自动切分状态、仅重试切分、整组重生、单 Beat 修复入口、刷新恢复。

- [ ] **步骤 2：运行聚焦测试验证组件缺失/旧合同失败**

运行：`pnpm test -- src/__tests__/components/episode/narrative-workbench src/__tests__/routes/beats-main-ui-contract.test.ts`。

- [ ] **步骤 3：实现工作台并复用现有 gallery action**

多宫格不再藏在 Dialog；旧 gallery 保留兼容只读入口。任务完成依赖后端聚合结果，不由浏览器串联生成与切分。

- [ ] **步骤 4：验证前端工作台**

运行：`pnpm test -- src/__tests__/components/episode/narrative-workbench src/__tests__/components/episode/beat-workbench src/__tests__/routes/beats-main-ui-contract.test.ts`。

提交：`feat: make narrative groups the primary beat workflow`

### 任务 8：H3 项目默认与 Beat 临时覆盖 UI

**文件：**
- 修改：`frontend/src/components/episode/beat-workbench/single-beat-panel.tsx`
- 修改：`frontend/src/components/episode/beat-workbench/video-pane.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 测试：`frontend/src/__tests__/components/episode/beat-workbench/single-beat-panel.test.tsx`
- 测试：`frontend/src/__tests__/components/episode/beat-workbench/video-pane.test.tsx`

- [ ] **步骤 1：编写 H3 选择与实际模式红灯测试**

断言项目默认显示 H3；首尾帧显示 FL2V、仅首帧显示 I2V；临时覆盖只进入本次请求且不 patch 项目；不可用模型禁用并展示原因。

- [ ] **步骤 2：运行测试验证 H3 不在选项中**

运行：`pnpm test -- src/__tests__/components/episode/beat-workbench/single-beat-panel.test.tsx src/__tests__/components/episode/beat-workbench/video-pane.test.tsx`。

- [ ] **步骤 3：实现新目录选择器与请求字段**

显示 inherited/override、实际 provider/model/mode；不进入 Seedance 专属参数分支。

- [ ] **步骤 4：验证并提交**

运行同上，预期全部通过。

提交：`feat: add minimax h3 video controls`

### 任务 9：迁移、全链验证与文档

**文件：**
- 修改：`docs/zh/guides/media-production.md`
- 修改：与迁移相关的 API/前端合同测试

- [ ] **步骤 1：新增旧项目迁移测试**

旧 Beat 资产首次读取时生成组索引但不移动文件；旧 `video_backend` 值仍能读取，用户选择 H3 后保存稳定 ID。

- [ ] **步骤 2：运行后端与前端聚焦全套**

运行：

```powershell
pytest tests/test_narrative_group_service.py tests/test_api_narrative_groups.py tests/test_task_narrative_group_runners.py tests/media_capabilities/video tests/test_task_video_runner_h3.py tests/test_api_media_capabilities.py -q
pnpm test -- src/__tests__/lib/queries/narrative-groups.test.tsx src/__tests__/lib/queries/media-models.test.tsx src/__tests__/components/episode/narrative-workbench src/__tests__/components/episode/beat-workbench src/__tests__/routes/beats-main-ui-contract.test.ts
pnpm build
```

- [ ] **步骤 3：运行静态检查和差异检查**

运行：`ruff check src/novelvideo/narrative_groups src/novelvideo/media_capabilities/video tests/test_narrative_group_service.py tests/media_capabilities/video`、`git diff --check`。

- [ ] **步骤 4：更新操作文档并提交**

文档说明默认叙事组流程、整组重生、单 Beat 修复、H3 auto/FL2V/I2V 和供应商配置入口。

提交：`docs: document narrative group media workflow`

## 计划自检

- 规格覆盖：多宫格主流程、自动切分、整组重生、稳定映射、GRSAI 图片边界、H3 目录与实际 runner、项目默认/Beat override、异步恢复、旧资产迁移均有对应任务。
- 类型一致性：统一使用 `group_id/stage/revision/cell_to_beat`；H3 ID 固定 `runninghub:minimax-h3`；模式固定 `auto/i2va/fl2va`。
- 并行边界：后端叙事组、H3 媒体域和前端可分别实现；共享 schema 变更由集成阶段统一解决，不允许两个实现者同时修改同一文件。
- 无占位符：每个任务均给出精确文件、红灯测试、命令、最少实现与提交点。
