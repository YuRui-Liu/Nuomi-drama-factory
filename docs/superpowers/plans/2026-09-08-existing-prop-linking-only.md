# “规划道具”仅链接现有资产实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让“规划道具”只链接剧本明确提及的现有道具资产，彻底取消模型提取、新建和自动晋升。

**架构：** `AssetCompiler._compile_props()` 直接对现有资产名称及别名做确定性文本匹配并生成 `prop_menu`。后台仍原子发布菜单和 planned bindings，但道具任务不再建立文本 runtime scope，也不调用晋升服务。

**技术栈：** Python、Pydantic、pytest

---

### 任务 1：锁定仅链接现有资产的编译契约

**文件：**
- 修改：`tests/test_asset_compiler_prop_planning.py`
- 修改：`src/novelvideo/agents/asset_compiler.py`

- [ ] **步骤 1：编写失败测试**

测试提供“功德碑（别名：深灰功德碑）”和“令牌”两个现有资产，剧本只出现别名；断言只输出“功德碑”，跨场景去重，`_analyze_block_props()` 与 `add_prop()` 均未调用，`draft.props == ()`、`new_count == 0`。另测零命中返回空菜单。

- [ ] **步骤 2：运行失败测试**

运行：`uv run pytest tests/test_asset_compiler_prop_planning.py -q`

预期：旧实现调用模型或创建新道具，测试失败。

- [ ] **步骤 3：最小实现**

将 `_compile_props()` 改为逐块调用 `_preselect_existing_props()`，用现有资产构造菜单；删除 requirement 分析、新道具暂存和本集局部复用分支。

- [ ] **步骤 4：验证通过**

运行：`uv run pytest tests/test_asset_compiler_prop_planning.py -q`

预期：全部通过。

### 任务 2：取消道具模型路由和自动晋升

**文件：**
- 修改：`tests/test_task_episode_asset_bindings.py`
- 修改：`src/novelvideo/task_backend/runners/episode_assets.py`
- 修改：`src/novelvideo/api/routes/model_gateway.py`

- [ ] **步骤 1：编写失败测试**

断言 `episode_prop_planner.text_task_role is None`、`episode_scene_planner` 仍使用 `episode_asset_planning`，并断言 prop runner 不调用晋升服务且返回 `auto_promoted_props == []`。

- [ ] **步骤 2：运行失败测试**

运行：`uv run pytest tests/test_task_episode_asset_bindings.py -q`

预期：道具任务仍注册 Agent 路由或调用晋升，测试失败。

- [ ] **步骤 3：最小实现**

移除道具任务的 `text_task_role`，删除 runner 的晋升调用；将设置展示标签改为“场景规划”。

- [ ] **步骤 4：验证通过**

运行：`uv run pytest tests/test_task_episode_asset_bindings.py tests/test_text_task_runtime_settings.py -q`

预期：全部通过。

### 任务 3：回归和静态检查

**文件：**
- 验证：上述修改文件

- [ ] **步骤 1：运行相关回归**

运行：`uv run pytest tests/test_asset_compiler_prop_planning.py tests/test_task_episode_asset_bindings.py tests/test_api_episode_detail.py -q`

预期：全部通过。

- [ ] **步骤 2：运行 Ruff**

运行：`uv run ruff check src/novelvideo/agents/asset_compiler.py src/novelvideo/task_backend/runners/episode_assets.py tests/test_asset_compiler_prop_planning.py tests/test_task_episode_asset_bindings.py`

预期：无错误。

- [ ] **步骤 3：检查提交边界**

运行：`git diff --check`，只提交本计划相关文件并保留其他工作区改动。
