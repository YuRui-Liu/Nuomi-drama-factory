# Nuomi 现状与本轮研究边界

核查日期：2026-10-16。分支 `feat/canvas`，基线提交 `b59cc055dfa9a7d729bf162c11906b7b0c65e9dc`。此前 `git pull` 成功返回 Already up to date。本轮只读代码、研究公开来源并保存研究文档，未编写产品代码或新 PRD，未执行付费生成。

## 已有产品约定

[已批准 PRD](../../superpowers/specs/2026-10-13-agent-canvas-prd.md)明确：想法、剧本、素材进入同一工作空间；构思、分集、自由画布共享能力；主创统筹，专业 Agent 局部委托；项目内容与可复用配置分离；已有项目主动采用新版本；局部任务不强制推进到完整成片。

用户本轮选择 A（专业 Agent 目录＋剧本交接画布＋主创接手），并明确要求先并行深度调研，再写 PRD。前一轮自动推进程度的选项是①②③，随后用户回复“A”；不能把这解释为已精确批准自动整理或自动分镜的具体深度。本轮不再追问，先完成研究；自动推进边界留在之后的设计讨论。

## 可复用基础与实际缺口

| 范围 | 代码证据 | 结论 |
| --- | --- | --- |
| 角色配置 | [agent_profiles.py](../../../src/novelvideo/creation/agent_profiles.py)、[builtin_agents.py](../../../src/novelvideo/creation/builtin_agents.py) | 有职责、方法、参考、技能说明、能力声明、默认偏好、Studio 引用与固定版本；内置仅通用主创、文本编剧 |
| 配置管理界面 | [AgentConfigurationPanel.tsx](../../../frontend/src/features/agent-configuration/AgentConfigurationPanel.tsx) | 已有新建、复制、个人发布、项目采用与局部配置基础；不应另建第二套版本系统 |
| 主创与委托入口 | [AgentRuntimePanel.tsx](../../../frontend/src/features/agent-runtime/AgentRuntimePanel.tsx) | 已有主创选择、当前节点引用、画布范围、专业 Agent 勾选、运行状态；缺少专业目录的浏览与理解体验 |
| 真实受限运行 | [agent_tools.py](../../../src/novelvideo/creation/agent_tools.py)、[delegations.py](../../../src/novelvideo/creation/delegations.py)、[agent_runs.py](../../../src/novelvideo/creation/agent_runs.py) | 可读取授权上下文、保存文本候选与对象定义草案；输出有委托、对象与操作关联，不只是聊天文字 |
| 旧交接 | [handoff.py](../../../src/novelvideo/script_creation/handoff.py)、[handoff-dialog.tsx](../../../frontend/src/features/script-creation/handoff-dialog.tsx) | 有冻结正文、参考与实体、来源修订、差异、确认和 outbox；确认写入 episode source 并派发 screenplay_semantics，界面回到本集剧本，未接到统一画布 |

注意：`AgentProfiles.capabilities()` 中 `execution_enabled: False` 表示配置声明不直接授予执行权限，不能据此断言整个 Agent 运行时未实现。需同时考察委托和运行模块。专业角色名称也不能证明对应媒体能力已接通。

## 研究应解决的问题

1. 用户如何知道一个专业 Agent 能交付什么，以及需要给它什么？区分角色职责、创作方法、模型和可调用能力。
2. 剧本进入画布后形成哪些可编辑对象？原文、AI 推断、已确认设定如何区分？
3. 内容更新如何保留人工编辑和旧产物？来源修订与当前采用版本如何可见？
4. 专业 Agent 如何把结果交还当前画布，并保持任务范围、来源和候选身份？
5. 案例能证明什么？展示成片、公开提示词、公开工作流和独立复现应分别标记。
6. 失败恢复怎样接续原任务，避免重复提交、重复购买与状态误报？

## 不能被竞品研究替代的约束

已有生产任务仍遵守仓库 AGENTS.md 与 nuomi-production：叙事组制作、保存的并发、支持的 CLI/API、按失败组恢复。统一画布 PRD 的自由创作交互与当前生产入口约束属于不同层面；不能用竞品的单镜操作建议绕过生产入口，也不能把旧生产阶段重新作为所有画布任务的前置条件。

本次未运行测试或服务。代码审阅证明可见实现路径，不代表真实模型、UI 或成片端到端验收通过。
