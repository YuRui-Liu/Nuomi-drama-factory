# MiniMax H3 导演台契约修复实施计划

> 目标：以 TDD 修复标准 Beat → H3 提示词 → RunningHub 导演台的完整契约，并保持画幅比例可配置。

## Task 1：锁定标准 Beat 对白回归

**Files:**
- Modify: `tests/test_task_narrative_group_video_runner.py`
- Modify: `tests/test_task_video_runner_h3.py`
- Create: `src/novelvideo/media_capabilities/video/h3_beat_adapter.py`
- Modify: `src/novelvideo/task_backend/runners/narrative_group_video.py`
- Modify: `src/novelvideo/task_backend/runners/video.py`

1. 新增使用 `narration_segment/audio_type/speaker` 的失败测试，断言对白、说话人和对白意图正确传入优化器。
2. 运行聚焦测试确认失败原因是正文为空。
3. 实现共享适配器，并替换两条 runner 的重复字段读取。
4. 运行聚焦测试至通过。

## Task 2：对齐 MiniMax H3 官方提示词

**Files:**
- Modify: `tests/media_capabilities/video/test_h3_prompt.py`
- Modify: `tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- Modify: `src/novelvideo/media_capabilities/video/h3_prompt.py`
- Modify: `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`

1. 新增 I2VA/FL2VA 官方帧引用句、三个核心字段、稳定 speaker ID、`<d>[Chinese]` 原文对白测试。
2. 新增 `tone` 缺失仍可优化、对白缺失仍明确失败的测试。
3. 运行测试确认旧自定义格式红灯。
4. 更新 system prompt、校验和确定性 renderer；对白由 renderer 注入，禁止优化器改写。
5. 运行聚焦测试至通过。

## Task 3：对齐真实 RunningHub 节点 12

**Files:**
- Modify: `tests/fixtures/runninghub/minimax_h3_api.json`
- Modify: `src/novelvideo/media_capabilities/video/profiles/minimax_h3.json`
- Modify: `src/novelvideo/media_capabilities/video/pipeline.py`
- Modify: `src/novelvideo/media_capabilities/video/runtime.py`
- Modify: `tests/media_capabilities/video/test_h3_pipeline.py`
- Modify: `tests/media_capabilities/video/test_runtime.py`

1. 用真实工作流节点 12/7 的输入形状扩展 fixture。
2. 新增失败测试，断言提交同时覆盖 task type、prompt、帧率、宽高、参考图上限、总帧数和 timeline。
3. 新增多段名义时长/对齐帧数测试，以及 `9:16` 与非默认比例测试。
4. 扩展 profile bindings 和 pipeline 参数透传。
5. 修正 runtime 序列化并补参考图宽高。
6. 运行聚焦测试至通过。

## Task 4：贯通请求级画幅配置

**Files:**
- Modify: `src/novelvideo/api/routes/narrative_groups.py`
- Modify: `frontend/src/lib/queries/narrative-groups.ts`
- Modify: relevant narrative workbench component/tests if an existing selector is present
- Modify: `tests/test_api_narrative_groups.py`

1. 先用搜索确认现有 UI/请求是否已有比例与分辨率状态。
2. 新增 API 契约测试，断言未传时默认 `9:16`，显式非默认值完整进入任务 payload。
3. 实现后端模型与 payload 透传；若已有前端选择器则接线，若没有则保持 API 可配置且不扩大 UI 范围。
4. 运行后端与前端聚焦测试。

## Task 5：端到端本地契约验证

**Files:**
- Modify: tests only if verification reveals an uncovered regression

1. 使用 ng-02 的标准 Beat 形状构造本地 dry-run，确认四段对白均保留原文。
2. 验证多段 timeline 与节点 12 顶层参数一致。
3. 运行相关 pytest 套件、ruff、前端聚焦测试和 `git diff --check`。
4. 审查改动不包含用户已有的无关工作树文件。

