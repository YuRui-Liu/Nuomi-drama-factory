# NuomiDrama Current Main CLI 执行提示

你正在 `dramaclaw` 当前本地 checkout/main 上执行已批准的 NuomiDrama 吸收方案。

## 必读文件

先完整阅读：

1. 当前工作区和仓库内所有适用的 `AGENTS.md`。
2. `docs/plans/2026-08-30-current-main-script-semantics-absorption-addendum.md`
3. `docs/plans/2026-08-30-current-main-absorption-decisions-v2.md`
4. `docs/plans/2026-08-30-higgsfield-production-workflow-integration-plan.md`

使用 `executing-plans`、`subagent-driven-development`、`dispatching-parallel-agents`、`test-driven-development`、`systematic-debugging`、`requesting-code-review` 和 `verification-before-completion` 等适用技能。

## 工作树保护

- 直接建立在当前 main，不创建 worktree。
- current main 可能有大量其他终端的修改和未跟踪测试目录。
- 第一步只读执行 `git branch --show-current`、`git status --short`、`git log -10 --oneline`、`git diff --check`。
- 建立本任务文件所有权清单；不得清理、回退、覆盖、删除或提交未归属改动。
- 禁止 `git reset --hard`、`git checkout --` 和递归删除临时目录。
- 每个提交必须精确暂存本任务拥有的文件，并使用 `Hermes:` 提交信息。

## 动态并行规则

- 先并行派只读 explorer 审计三个独立领域：剧本/EpisodeGraph 后端、角色视觉资产、剧集制作前端。
- explorer 返回精确入口后，再派 worker；每个 worker 必须拥有互斥文件集合。
- worker 必须知道共享脏工作树，不得回退其他人的修改。
- 存在重叠文件时由主代理串行处理，不得并行抢写。

## 第一优先级：P1-P5

按 TDD 顺序实现：

1. 拆分 `existing_script` 与 `story_adaptation`：已有剧本只解析/校对，绝不再次生成剧本。
2. 彻底废弃逐行 Beat：移除模型、模式、文案、API 参数、runner、前端和 fallback。
3. 建立 `SourceDocument -> ScriptRevision -> Scene -> DramaticBeat -> DirectorShotIntent -> ShotPlan -> NarrativeGroupPlan`。
4. 旧逐行 Beat 派生媒体保留为 `legacy_unbound`，不自动关联新结构。
5. 分离 `CharacterNarrativeProfile`、`CharacterDesignBrief`、`CharacterVisualBible`；剧情句子不得作为面部提示词。
6. 改造四阶段前端：`剧本校对 -> 导演拆解 -> 镜头设计 -> 成片合成`。
7. 资产规划只产生证据化 `AssetRequirement`；剧情时期标签不自动成为视觉状态。
8. 实现依赖感知失效：局部过期、旧媒体保留、人工触发重建，严格模式阻断过期关键链交付。

每项必须先写能复现当前错误的失败测试，再写最小实现并运行定向测试。

## 后续主计划

P1-P5 验收通过后，再推进：

- 角色身份锚点和三视图状态资源图。
- 场景状态、空间锚点和道具精确内容层。
- 类型化 ReferencePlan，风格/角色/场景/道具默认引用且可取消。
- 1～4 镜头叙事组一次组合出图，9:16/16:9 作为硬约束。
- RunningHub MiniMax H3 默认核心；i2va/fl2va 生成前可调；禁止静默降级。
- GenerationAttempt、AssetSlot、候选采用、分阶段 QC、严格模式和批量生产。

## 验证要求

- 不得把“测试未运行”表述为通过。
- 每批记录实际命令、退出码、通过/失败数。
- 运行定向测试后再运行后端回归、前端测试/类型检查/构建和 `git diff --check`。
- 最终必须做代码审查，检查静默降级、重复付费、错误画幅、候选覆盖、旧 Beat 泄漏和无来源面部提示词。
- 真实 RunningHub 测试只能使用已有配置，严禁打印或提交密钥；先做最低合理成本烟测。

持续执行，不要在仅完成审计或计划后停止。只有遇到未归属重叠修改、需要破坏性操作、缺少真实凭据或无法安全决定的产品歧义时才暂停报告。

