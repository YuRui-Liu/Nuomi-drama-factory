# Current Main 剧本语义与角色视觉输入纠偏补充方案

> **执行关系：** 本文是 `2026-08-30-higgsfield-production-workflow-integration-plan.md` 的强制前置补充。另一终端必须先完成本文，再执行原计划的角色三视图、参考装配、组图和 H3 工作。

## 已冻结决策

1. “导入已有剧本”和“故事/大纲改编”是两个明确入口。
2. 导入已有剧本只能解析、校对和结构化，禁止再次生成或改写剧本。
3. 原“逐行 Beat / 一行一个 Beat”模型、模式、文案、API 参数和执行分支全部废弃，不保留只读兼容或隐藏回退。
4. 正确结构是 `SourceDocument -> ScriptRevision -> Scene -> DramaticBeat -> ShotPlan -> NarrativeGroupPlan`。
5. 场景标题、人物名单、孤立格式行和普通章节字幕不得自动成为 DramaticBeat。
6. 剧本只能提供带原文证据的角色叙事事实，不能直接生成“面部提示词”。
7. 人物五官、脸型、发型、体型、标志特征、服装状态和风格进入独立 `CharacterVisualBible`。
8. 旧逐行 Beat 派生媒体保留为 `legacy_unbound` 历史产物，不删除、不自动关联新结构；用户可手动绑定。

## 当前 main 的吸收方式

保留并修正当前 main 已有边界，不另建平行系统：

- `src/novelvideo/episode_source_store.py`：保存原文、输入意图和剧本修订。
- `src/novelvideo/episode_import_service.py`：负责输入分类建议、格式规范化和场次解析，不负责“再次生成脚本”。
- `src/novelvideo/episode_graph/`：表达 Scene、DramaticBeat、角色出场、对白、道具和来源证据。
- `src/novelvideo/task_backend/runners/episode_graph.py`：拆分为剧本解析任务与镜头规划任务。
- `frontend/src/routes/_app/projects.$project/episodes.tsx`：保留页面骨架，修正阶段、按钮、统计和逐行模式。
- 现有资产中心角色详情：分离人物小传、剧本事实、视觉身份和系统编译提示词。

执行前必须先用 `rg` 查找以上文件的当前实现和未提交归属；current main/工作树已有其他终端修改，禁止清理、覆盖或混合提交。

## Task P1：拆分导入与改编入口

先写失败测试：

- `existing_script` 只保存原文、规范化、识别场次和构建派生结构，不能调用剧本生成模型。
- `story_adaptation` 只有在用户明确选择后才允许生成新的 `ScriptRevision`。
- 自动输入识别只能建议，不能静默改变入口。
- 原文 `SourceDocument` 不得被下游任务覆盖；编辑产生新修订。

前端动作：

- 已有剧本未结构化：`解析场次`。
- 已有场次/节拍：`生成镜头方案`。
- 只有故事/大纲入口显示：`改编为剧本`。
- 移除已有剧本页面上的“生成脚本”。

提交：`Hermes: 拆分剧本导入与故事改编入口`

## Task P2：建立短剧语义结构

先写失败测试，证明：

- 场景标题只产生 Scene 元数据。
- 人物名单只产生出场关系。
- 连续动作、反应和对白可以组成一个 DramaticBeat。
- 一个 DramaticBeat 可以跨多行，并规划为多个镜头。
- 章节标题只有导演明确选择视觉呈现时才生成镜头。
- Scene、Beat、Shot 和 NarrativeGroup 均可追溯稳定 `source_span`。
- `DirectorPlanRevision` 消费 DramaticBeat，不消费原始行号数组。

Beat 定义：完成一次可识别戏剧变化的动作/反应/对白单元，而不是文本行。

提交：`Hermes: 建立场次节拍镜头的短剧语义结构`

## Task P3：彻底废弃逐行 Beat

搜索并移除所有 `line_beat`、`one_line_one_beat`、“一行一个 Beat”和等价分支，覆盖 schema、API、runner、前端、状态文案和测试夹具。

先写失败测试：

- 所有新建、导入、解析和生成接口均不能接受逐行模式。
- 前端完全不展示逐行模式。
- 旧项目不加载、不渲染、不执行旧 Beat，而是显示“需要重建短剧结构”。
- 重建只读取原始剧本和受信任人工修订。
- 禁止按行号、序号、文本相似度启发式映射旧 Beat。

旧媒体处理：

- 图片、视频和音频不删除、不移动。
- 登记为 `legacy_unbound` 历史产物。
- 不自动进入新镜头参考、当前版本或最终交付。
- 仅允许用户从历史区手动绑定到新 `AssetSlot`。

提交：`Hermes: 废弃逐行 Beat 并隔离历史产物`

## Task P4：分离角色叙事档案和视觉圣经

建立或扩展以下对象：

- `CharacterNarrativeProfile`：姓名、别名、年龄范围、剧本明确性别、职业、社会身份、人物关系、性格和戏剧功能。
- `CharacterDesignBrief`：时代地域、职业阶层、气质、项目风格和视觉设计目标。
- `CharacterVisualBible`：经设计/人工确认的脸型、五官、发型、体型、标志特征、服装状态和身份锚点。

先写失败测试：

- 每条剧本事实保存 `source_span`、原文证据、置信度和 `explicit/inferred`。
- 只有剧本明确写出的疤痕、残疾、制服等可成为视觉约束。
- 未写出的五官和发型保持未设定。
- 剧情句子、行为描述、人物小传和对白不能保存/显示为“面部提示词”。
- 图片生成只读取已确认 VisualBible、项目视觉风格和参考图。
- 最终模型 prompt 是下游编译产物，不是剧本字段。
- 旧字段来源不明或与剧情描述相同则标记 `legacy_untrusted`，不得进入新图片请求。

提交：`Hermes: 分离角色叙事档案与视觉身份圣经`

## Task P5：修正前端产品语义

资产中心：

- “描述”改为“人物小传”。
- 删除面向用户的“面部提示词”。
- 新增“剧本明确事实”，逐条显示来源依据。
- 新增“视觉身份设定”和“服装与状态”。
- 高级诊断区才允许显示“系统编译提示词”。

剧集制作：

- “脚本”改为“剧本校对”或等价准确名称。
- 按 Task P1 显示 `解析场次`、`生成镜头方案` 或 `改编为剧本`。
- 删除逐行模式及“当前模式：一行一个 Beat”。
- 统计改为 `原文行数 · 场次数 · 戏剧节拍数 · 镜头数 · 叙事组数`。
- 明确区分原始剧本、派生结构和导演镜头方案。

重点测试：

- `frontend/src/__tests__/routes/episodes-workbench-integration.test.ts`
- `frontend/src/__tests__/routes/episode-graph-index-contract.test.ts`
- `frontend/src/__tests__/lib/queries/asset-organization.test.tsx`

提交：`Hermes: 修正角色资产与剧集制作页面语义`

## 验收红线

| 场景 | 必须结果 |
|---|---|
| 导入已有剧本 | 只解析和校对，不调用剧本生成链 |
| 故事/大纲改编 | 用户明确选择后才生成剧本 |
| 场景标题/人物名单 | 不产生 DramaticBeat |
| 跨多行动作和对白 | 可组成一个带原文证据的 DramaticBeat |
| 逐行 Beat | 产品、API 和执行路径中完全不存在 |
| 旧逐行 Beat 项目 | 要求重建；旧媒体只进入历史产物区 |
| 剧情描述被当成面部提示词 | 标记不可信且不得进入新图片请求 |
| 角色视觉生成 | 只读取已确认 VisualBible、项目风格和参考图 |

完成 P1～P5 并通过定向测试、后端回归、前端类型检查/构建和 `git diff --check` 后，才可继续主计划后续批次。

