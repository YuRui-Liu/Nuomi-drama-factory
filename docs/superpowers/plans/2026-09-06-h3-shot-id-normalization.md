# H3 镜头编号规范化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 防止 Codex 将外层业务镜头 ID 复制到 H3 内部 `shot_id` 后导致 episode pack Schema 校验失败。

**架构：** 在 H3 episode pack 专属边界增加一个纯函数，对原始映射中的内部 shots 按数组顺序重编号，再交给现有 Pydantic 模型做完整严格校验。同时增强 episode prompt，降低模型首先产生错误编号的概率。

**技术栈：** Python 3.12、Pydantic 2、pytest、pydantic-ai

---

### 任务 1：锁定回归行为

**文件：**
- 修改：`tests/media_capabilities/video/test_h3_episode_pack.py`

- [ ] **步骤 1：编写失败测试**

新增一个 optimizer 测试，让 fake agent 返回普通字典，其中两个 H3 shots 的 `shot_id` 为 `shot-01`、`shot-02`；断言优化结果内部 ID 为 `("1", "2")`，外层输入 `shot_ids` 保持不变。

- [ ] **步骤 2：编写提示词测试**

直接调用 `_episode_task`，断言提示词明确包含内部 ID 必须从 `"1"` 连续编号、不得复制外层 `shot_ids` 的英文规则。

- [ ] **步骤 3：运行红灯测试**

运行：

```bash
.venv/bin/python -m pytest tests/media_capabilities/video/test_h3_episode_pack.py -q
```

预期：新增回归测试因 `shot_id values must be continuous string numbers from 1` 失败，提示词测试因规则缺失失败。

### 任务 2：实现窄范围规范化

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/h3_episode_pack.py`
- 测试：`tests/media_capabilities/video/test_h3_episode_pack.py`

- [ ] **步骤 1：实现纯规范化函数**

添加 `_normalize_h3_shot_ids(value: object) -> object`：仅复制并处理映射结构的 `segments[].director_plan.shots`，按数组顺序写入字符串编号；遇到非 list/mapping 结构时不修复，由 Pydantic 保持拒绝。

- [ ] **步骤 2：在两个严格校验入口调用**

初次 episode 输出和质量修复输出都使用：

```python
H3EpisodePromptPack.model_validate(_normalize_h3_shot_ids(response.output))
```

- [ ] **步骤 3：增强提示词**

在 `_episode_task` 中加入明确规则：每个 `director_plan.shots[].shot_id` 必须从 `"1"` 开始连续编号，且不得复制外层业务 `shot_ids`。

- [ ] **步骤 4：运行绿灯测试**

运行：

```bash
.venv/bin/python -m pytest tests/media_capabilities/video/test_h3_episode_pack.py -q
```

预期：全部通过。

### 任务 3：回归验证与提交

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/h3_episode_pack.py`
- 修改：`tests/media_capabilities/video/test_h3_episode_pack.py`

- [ ] **步骤 1：运行相关测试**

```bash
.venv/bin/python -m pytest tests/media_capabilities/video/test_h3_episode_pack.py tests/media_capabilities/video/test_h3_director_plan.py tests/test_task_narrative_group_video_runner.py -q
```

预期：零失败。

- [ ] **步骤 2：运行格式检查**

```bash
git diff --check -- src/novelvideo/media_capabilities/video/h3_episode_pack.py tests/media_capabilities/video/test_h3_episode_pack.py
```

预期：退出码 0，无输出。

- [ ] **步骤 3：仅提交修复文件**

```bash
git add src/novelvideo/media_capabilities/video/h3_episode_pack.py tests/media_capabilities/video/test_h3_episode_pack.py docs/superpowers/plans/2026-09-06-h3-shot-id-normalization.md
git commit -m "fix(video): normalize H3 internal shot IDs"
```
