# v2 阶段 B：媒体协议与任务守卫实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改写 RunningHub MiniMax H3 内部工作流的前提下，统一媒体请求边界、诊断与入队前校验。

**架构：** 现有 `media_capabilities` 域模型作为通用外壳，各供应商适配器保持独立。RunningHub 解析优先级最高，工作流编译与提交逻辑不与 Seedance/NewAPI 共用。校验在计费和创建任务前执行。

**技术栈：** Python、Pydantic、FastAPI、SQLite、RunningHub API、pytest；React、TypeScript、Vitest。

---

### 任务 1：诊断模型与脱敏

**参考：** `E:\Proj\dramaclaw-main\src\novelvideo\llm_instrumentation.py` 和 v2.0.1 媒体错误翻译。

**文件：**
- 创建：`src/novelvideo/media_capabilities/diagnostics.py`
- 修改：`src/novelvideo/media_capabilities/task_store.py`
- 测试：`tests/media_capabilities/test_diagnostics.py`

- [ ] 写红灯：

```python
def test_diagnostics_redact_credentials_urls_and_local_paths():
    value = build_diagnostics(request, upstream)
    serialized = json.dumps(value)
    assert "Bearer " not in serialized
    assert "X-Amz-Signature" not in serialized
    assert "E:\\projects" not in serialized
    assert value["workflow_id"] == "minimax-h3-director"
```

- [ ] 运行 `python -m pytest tests/media_capabilities/test_diagnostics.py -q`，确认 FAIL。
- [ ] 实现白名单诊断：provider/channel/logical_model/resolved_model/mode/aspect/duration/quality/workflow_id/workflow_version/reference_summary/upstream_task_id/status/retries/fallback_reason。
- [ ] 重跑确认 PASS，提交 `feat: record safe media diagnostics`。

### 任务 2：RunningHub MiniMax 保护解析顺序

**文件：**
- 修改：`src/novelvideo/media_capabilities/resolver.py`
- 修改：`src/novelvideo/media_capabilities/video/catalog.py`
- 测试：`tests/media_capabilities/video/test_catalog.py`
- 测试：`tests/test_api_runninghub_h3_backend.py`

- [ ] 写红灯，同时提供项目 RunningHub、自定义和官方目录时，断言解析结果仍为 `runninghub:minimax-h3`。
- [ ] 运行目标 pytest，确认当前顺序未被契约锁定而 FAIL。
- [ ] 实现固定顺序：项目 RunningHub > 本地自定义 > 官方目录 > 系统默认；不修改 H3 workflow registry 与节点编译器。
- [ ] 重跑确认 PASS，提交 `fix: protect RunningHub MiniMax resolution`。

### 任务 3：视频模式和参考素材预检

**参考：** v2.0.1 Seedance 尺寸预检，但实现为能力驱动，不将 Seedance 限制硬编码到 RunningHub。

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/pipeline.py`
- 修改：`src/novelvideo/freezone/video_node.py`
- 修改：`src/novelvideo/task_backend/runners/video.py`
- 测试：`tests/media_capabilities/video/test_pipeline.py`
- 测试：`tests/test_task_video_runner_h3.py`

- [ ] 写红灯：素材撤空后 `resolved_mode == "t2v"`；超出模型尺寸/数量/时长限制时不创建任务；H3 的 I2V/reference 模式继续映射正确 workflow。
- [ ] 运行两个目标测试确认 FAIL。
- [ ] 实现 `preflight_video_request(request, capability)`，返回规范化请求或类型化错误；只允许明确契约定义的自动回退。
- [ ] 重跑确认 PASS，提交 `fix: validate video requests before enqueue`。

### 任务 4：语音和参考音频预检

**文件：**
- 修改：`src/novelvideo/task_backend/runners/voice_design.py`
- 修改：`src/novelvideo/media_capabilities/tts/pipeline.py`
- 测试：`tests/media_capabilities/tts/test_voice_design.py`
- 测试：`tests/test_voice_design_runner.py`

- [ ] 写红灯：缺少声线、不可读参考音频、单条或总时长越界时，任务存储中不出现新记录。
- [ ] 运行目标 pytest 确认 FAIL。
- [ ] 在计费和入队前执行 `validate_voice_request`，返回可执行中文错误。
- [ ] 重跑确认 PASS，提交 `fix: validate voice inputs before enqueue`。

### 任务 5：任务详情显示脱敏诊断

**文件：**
- 修改：`frontend/src/components/task-center/task-logs.tsx`
- 修改：`frontend/src/task-center/provider.tsx`
- 测试：`frontend/src/__tests__/task-center/provider.test.tsx`

- [ ] 写红灯，断言任务详情显示 provider/model/mode/workflow/upstream task/fallback reason，且不显示 token、签名 URL 或绝对路径。
- [ ] 运行目标 Vitest 确认 FAIL。
- [ ] 增加折叠的“模型诊断”区，只渲染后端白名单字段。
- [ ] 重跑确认 PASS，提交 `feat: expose safe task diagnostics`。

### 任务 6：阶段 B 强制验收

- [ ] 运行 `tests/media_capabilities/video/test_runninghub_h3.py`、`tests/media_capabilities/video/test_h3_workflow.py`、`tests/media_capabilities/video/test_h3_runtime.py`、`tests/test_runninghub_h3_video_generator.py`。
- [ ] 运行 `tests/test_task_video_runner_h3.py`、`tests/test_task_narrative_group_video_runner.py`、`tests/test_compose_episode_h3_director.py`。
- [ ] 运行 TTS 目标测试、前端任务详情测试、`ruff`、`tsc -b`、生产构建和 `git diff --check`。
- [ ] 任一 RunningHub MiniMax 契约失败即停止阶段 C。
