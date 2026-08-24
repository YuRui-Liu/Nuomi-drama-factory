# H3 导演计划质量自修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让中文动作描述和可机械修正的时间轴偏差不再误阻塞视频生成，并让真正的语义质量问题获得有限次数的 DeepSeek 修稿机会。

**架构：** 质量门保持 fail-closed；候选计划先经过纯函数时间轴归一化，再进入质量检查。语义失败通过结构化报告反馈给同一导演 Agent，最多修稿 2 次，只有最终通过的计划才能缓存和调用 RunningHub。

**技术栈：** Python 3.11、Pydantic、pydantic-ai、pytest、RunningHub MiniMax H3

---

## 文件结构

- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_quality.py`：语言无关动作判定与时间轴归一化纯函数。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：有限质量反馈修稿循环。
- 修改 `tests/media_capabilities/video/test_h3_prompt_quality.py`：中文与时间轴回归测试。
- 修改 `tests/media_capabilities/video/test_h3_prompt_optimizer.py`：质量修稿成功和耗尽测试。

### 任务 1：语言无关动作质量判定

- [ ] 编写中文详细动作通过、中文短动作拒绝的测试。
- [ ] 运行 `python -m pytest tests/media_capabilities/video/test_h3_prompt_quality.py -q`，确认中文详细动作测试失败。
- [ ] 使用英文词数加 CJK 字符数作为内容单位，并识别中英文分句符及连接词。
- [ ] 将 `H3_PROMPT_QUALITY_VERSION` 升级到 3，使旧缓存不复用旧规则。
- [ ] 再次运行同一测试文件，预期全部通过。

### 任务 2：机械时间轴归一化

- [ ] 构造 actions 存在正向间隙的 `H3DirectorPlan`，断言归一化后动作连续且内容、阶段、顺序不变。
- [ ] 运行该测试，确认因归一化函数缺失而失败。
- [ ] 在 `h3_prompt_quality.py` 实现 `normalize_h3_action_timeline(plan)`；仅在所有区间保持正长度时返回修正版，否则返回原计划。
- [ ] 运行 quality 测试，预期通过且原有 gap 检查仍能拒绝未归一化计划。

### 任务 3：质量反馈修稿循环

- [ ] 在 optimizer 测试中创建按顺序返回“不合格计划、合格计划”的真实 FakeAgent，断言调用 2 次、只缓存合格计划、修稿任务包含 issue code 和 location。
- [ ] 添加始终返回不合格计划的测试，断言达到质量修稿上限后抛出最后的 `H3PromptQualityError` 且缓存为空。
- [ ] 运行新增测试，确认当前实现首次质量失败即退出。
- [ ] 修改 `H3PromptOptimizer.optimize_segment`：首次任务后进入候选计划循环；每轮归一化、检查，失败时用 `_build_quality_revision_task` 生成修稿请求。
- [ ] 在 `create_h3_prompt_optimizer` 读取 `DRAMACLAW_H3_PROMPT_QUALITY_REVISIONS`，默认 2。
- [ ] 运行 optimizer 测试，预期全部通过。

### 任务 4：回归与真实验收

- [ ] 运行 `python -m pytest tests/media_capabilities/video/test_h3_prompt_quality.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/test_task_narrative_group_video_runner.py -q`，预期零失败。
- [ ] 运行 `git diff --check`，预期无空白错误。
- [ ] 重启可联网的 8780 后端以加载代码。
- [ ] 从正式 UI 重试一个本轮失败的最小叙事组，记录 DeepSeek/RunningHub 任务状态和供应商任务号。
- [ ] 用 `ffprobe -v error -show_entries stream=codec_name,width,height,duration -show_entries format=duration,size -of json <video>` 验证真实 MP4。

