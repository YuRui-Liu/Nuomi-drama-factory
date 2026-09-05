# NuomiDrama 一致性制作工作流整合实施计划

> **执行终端必读：** 使用 `executing-plans` 技能逐任务执行本计划；所有功能修复遵循 TDD。工作目录是现有 `dramaclaw` 的 `main`，不得清理、覆盖或提交其他终端留下的无关修改。

**目标：** 将 Higgsfield 展示的角色/场景一致性方法和影视制作方法提炼进 NuomiDrama，同时保留“叙事组一次组合出图”的成本与一致性优势，并将 RunningHub MiniMax H3 固定为默认核心视频能力。

**架构原则：** 创作层、执行层、采用层分离。`ProductionRecipe` 定义方法，`DirectorPlanRevision` 定义创作意图，`ReferencePlan` 决定本次参考装配，`GenerationAttempt` 记录实际执行，`AssetSlot` 决定当前采用版本。供应商适配器只能翻译结构化计划，不能反向污染导演方案。

**技术范围：** Python/Pydantic/FastAPI 后端、现有任务后端、React/TypeScript 前端、现有持久化层、RunningHub MiniMax H3 工作流、现有图片供应商。不得引入新的远端基础设施。

---

## 0. 已冻结产品决策

以下决策不再重新讨论，执行中不得自行改写：

1. RunningHub MiniMax H3 是叙事组生成视频的默认核心能力。
2. 仅允许在“能力完全等价且项目已预批准”的工作流间自动切换；否则停止并提示，禁止静默降级。
3. 叙事组包含 1～4 个镜头，一组只发起一次组合出图调用；5 个以上必须在导演规划阶段拆组。
4. 叙事组的目标单格画幅可配置为 9:16 或 16:9；组合画布尺寸必须由单格画幅和格数推导，不能反向破坏单格画幅。
5. 风格、角色、场景、道具参考默认自动带入，提交前全部可见且可逐项取消。
6. 参考图超过模型能力上限时必须明确显示取舍，禁止静默丢弃。
7. 角色资产使用“身份锚点 + 三视图状态资源图”；主要/常驻角色需要人工确认，临时角色可按项目策略自动暂用。
8. 场景分为基础场景、场景状态、空间锚点和镜头连续性参考；结构/陈设变化建立新状态，纯光照变化默认 relight。
9. 道具按类别生成资源图；剧情关键、特写、跨两个以上镜头、反复使用或包含精确内容时建立正式资产。
10. 导演推荐 `still_hold`、`post_motion`、`generated_video` 或 `composite`；实际生成视频前用户可调整。
11. H3 的 i2va/fl2va 是导演推荐，不是不可修改的自动决定；实际选择记录在 `GenerationAttempt`。
12. 主角/常驻角色默认使用锁定的外部声音（如 IndexTTS2）；临时角色、动作喊声可选择 H3 原声。
13. 资源槽为空时，首个通过 QC 的候选可自动标记为“暂用”；已有当前版本后，新候选只能进入候选区，必须人工替换。
14. 严格模式允许暂用素材参与中间制作，但最终出片、导出或锁定前必须人工确认。
15. 技术硬错误绝对阻断；内容软问题可以保存候选，并允许用户“带理由采用”。
16. 同一资源槽默认只能有一个活跃生成任务；不同资源槽可以并行。
17. 提交结果不明时先查询供应商原任务，禁止盲目重提并重复扣费。
18. “一键生成”只运行当前批次；已有满意结果默认跳过；视频批次执行前仍允许逐组调整。
19. 标准生产入口采用“阶段工作台 + 统一检查器”；创作画布保留为辅助探索入口。
20. 旧项目增量兼容、延迟迁移，素材不搬动，未知历史参数不得伪造。

## 1. 合并与不合并边界

### 1.1 必须合并

- Higgsfield 的角色三视图/状态资源图方法。
- 角色和场景一致性锚点方法。
- 镜头、机位、运动、主体动作的结构化表达。
- 制作配方、参考装配、候选版本和分阶段 QC。
- i2va/fl2va 推荐与生成前调整。
- 分阶段半自动批量生产。

### 1.2 明确不合并

- Higgsfield 品牌、页面外观、专有命名或社交发布能力。
- 对 Higgsfield 平台、API 或模型的运行时依赖。
- 将开源技能长提示词原样散落到业务代码。
- “一镜头一次出图”替换现有叙事组组合出图。
- 从剧本到成片的无确认全自动模式。
- 将创作画布改成唯一生产入口。
- 新候选自动覆盖当前采用版本。
- RunningHub H3 静默降级为普通视频模型。
- 一次性重建数据库或强制迁移所有旧项目。

## 2. 执行纪律与工作树保护

### Task 0：基线审计

**只读检查，不提交。**

1. 阅读本计划、仓库 `AGENTS.md` 和已有设计文档：
   - `docs/plans/2026-08-30-character-state-sheet-generation-attempt-design.md`
   - `docs/plans/2026-08-29-minimax-h3-codex-prompt-generator-design.md`
2. 执行：

   ```powershell
   git branch --show-current
   git status --short
   git log -10 --oneline
   git diff --check
   ```

3. 当前 main 已存在大量修改和临时测试目录。建立“本终端拥有文件清单”，后续只暂存清单内文件。
4. 禁止执行 `git reset --hard`、`git checkout --`、递归删除测试目录或清理其他人的未跟踪文件。
5. 使用 `rg` 查找已有的同名类型与半成品，优先扩展已有实现，禁止创建第二套平行领域模型：

   ```powershell
   rg -n "GenerationAttempt|AssetSlot|AssetVersion|DirectorPlanRevision|ProductionRecipe|ReferencePlan|QCReport|WorkflowProfile|CapabilityProfile|RoutingPolicy" src frontend tests
   rg -n "narrative_group|runninghub|minimax|i2va|fl2va|aspect_ratio|image_source_selection" src frontend tests
   ```

6. 检查 `src/novelvideo/episode_graph/` 等未跟踪目录是否属于其他终端；没有明确归属前不得修改或纳入提交。

### Task 1：建立测试基线

**目标：** 区分既有失败和本次回归。

1. 从 `pyproject.toml`、`frontend/package.json` 确认仓库声明的 Python 和前端测试命令。
2. 先运行与本计划直接相关的现有测试：

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests\media_capabilities\video tests\test_task_video_runner_h3.py -q
   .\.venv\Scripts\python.exe -m pytest tests\media_capabilities\image tests\test_api_media_capabilities.py -q
   ```

3. 前端使用仓库声明的包管理器运行以下现有测试：
   - `frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx`
   - `frontend/src/__tests__/lib/queries/narrative-groups.test.ts`
   - `frontend/src/__tests__/routes/episodes-workbench-integration.test.ts`
4. 保存既有失败的命令、错误摘要和当前提交号；禁止为了“变绿”弱化已有断言。

---

## 3. 第一批：执行与采用领域契约

### Task 2：定义正交状态模型

**目标：** 执行状态和采用状态不得混在一个枚举中。

**优先文件：** 先复用 `src/novelvideo/media_capabilities/models.py`、现有项目存储模型和任务模型；只有不存在合适模块时，才新增 `src/novelvideo/production_workflow/models.py`。

**测试：** 新增或扩展 `tests/production_workflow/test_models.py`。

1. 先写失败测试，覆盖：
   - `GenerationAttempt` 可记录输入快照、路由快照、供应商任务 ID、费用信息、错误和输出。
   - 执行状态至少区分 `draft/preflight_failed/queued/running/provider_processing/succeeded/failed/cancelled/unknown`。
   - `AssetVersion` 与 `AssetSlot` 分离。
   - 采用状态至少区分 `candidate/provisional/adopted/rejected/superseded`。
   - `ProjectProductionSettings` 包含画幅、严格模式、制作配方版本、默认路由、并发与自动暂用策略。
2. 运行测试，确认因类型不存在或行为不符而失败。
3. 最小实现 Pydantic/现有仓库模型；沿用现有序列化约定。
4. 再运行定向测试和 `git diff --check`。
5. 仅暂存本任务文件并提交：

   ```text
   Hermes: 建立制作执行与采用领域契约
   ```

### Task 3：实现首次暂用和后续人工替换策略

**测试：** `tests/production_workflow/test_adoption_policy.py`，以及现有角色/场景/道具选图 API 测试。

1. 先写失败测试：
   - 空槽位 + 首个 QC 通过候选 -> `provisional`。
   - 已有 `provisional` 或 `adopted` -> 新候选仍为 `candidate`。
   - 人工替换才更新 `AssetSlot.current_version_id`。
   - 新尝试失败不清空旧版本。
   - 严格模式最终交付要求所有关键槽位为人工确认状态。
   - 内容软问题可带理由采用；技术硬错误不允许越过。
2. 实现纯函数策略，API/服务层只调用该策略，禁止在多个路由重复写条件判断。
3. 所有状态变化记录操作者、时间、原因和来源 `GenerationAttempt`。
4. 定向测试通过后提交：

   ```text
   Hermes: 实现候选暂用与人工替换策略
   ```

### Task 4：持久化与旧项目兼容

**测试：** 扩展项目存储、角色/场景/道具选择和旧项目加载测试。

1. 先写失败测试：
   - 旧的已选图片映射为 `legacy_import` 当前版本。
   - 未知生成参数显示未知，不生成伪造的 provider/model/prompt。
   - 打开旧项目不立刻重写全部数据。
   - 首次编辑目标对象时只补齐该对象的新结构。
   - 迁移失败时旧项目仍可只读打开。
2. 使用增量字段或附属表；禁止移动已有媒体文件。
3. 对旧的 5+ 镜头叙事组保持只读兼容，再次生成前才要求导演拆组。
4. 提交：

   ```text
   Hermes: 增加制作资产的延迟迁移兼容层
   ```

---

## 4. 第二批：模型、工作流和可靠任务执行

### Task 5：统一能力档案、工作流档案与路由策略

**主要文件：**

- `src/novelvideo/media_capabilities/models.py`
- `src/novelvideo/media_capabilities/video/runtime.py`
- `src/novelvideo/media_capabilities/video/runninghub_h3.py`
- `src/novelvideo/media_capabilities/diagnostics.py`
- 对应 `tests/media_capabilities/` 测试

1. 先写失败测试，要求：
   - `CapabilityProfile` 描述媒体类型、模式、参考图额度、画幅、分辨率、时长、对白和音频能力。
   - `WorkflowProfile` 描述 provider、workflow ID/revision、API Schema 指纹、输入绑定、输出绑定与健康状态。
   - `RoutingPolicy` 的优先级为：单次用户选择 > 项目默认 > 阶段默认 > 预批准等价回退。
   - 不满足 i2va/fl2va、对白、音频或画幅任一硬能力时，不能作为 H3 等价回退。
2. 将现有字符串默认值包装到档案中，保留旧 API 兼容视图。
3. `GenerationAttempt` 冻结实际 provider、model、workflow revision、bindings、aspect、duration 和模式。
4. 提交：

   ```text
   Hermes: 统一媒体能力与工作流路由档案
   ```

### Task 6：固定 RunningHub MiniMax H3 核心路由

**主要文件：**

- `src/novelvideo/media_capabilities/video/runtime.py`
- `src/novelvideo/media_capabilities/video/runninghub_h3.py`
- `src/novelvideo/task_backend/runners/narrative_group_video.py`
- `tests/test_task_video_runner_h3.py`
- `tests/media_capabilities/video/` 下 H3 测试

**工作流事实来源：**

- `runninghub/MiniMax H3 导演台全能工作流.json`
- `runninghub/MiniMax H3 导演台全能工作流_api.json`

1. 写失败测试覆盖：
   - 叙事组视频默认解析为 RunningHub MiniMax H3。
   - i2va/fl2va 推荐可以被单次用户选择覆盖。
   - workflow ID/revision/Schema 不匹配在排队前失败。
   - 有对白意图但缺少对白时在提交前返回字段级错误。
   - 9:16/16:9 不受支持时阻断，不改成供应商默认画幅。
   - H3 不可用时，没有等价预批准配置则停止并提示。
   - 任何路径都不得静默降级为普通 T2V/I2V。
2. 从 API 工作流 JSON 建立唯一绑定映射；不要在 runner 中再次硬编码第二份节点表。
3. 错误必须包含用户可读摘要、失败字段、workflow revision 和关联 `GenerationAttempt`。
4. 提交：

   ```text
   Hermes: 固定 RunningHub MiniMax H3 核心视频路由
   ```

### Task 7：幂等、查单与恢复

**主要文件：** 现有任务存储、`narrative_group_video.py`、RunningHub 客户端和任务中心查询。

1. 先写失败测试：
   - 同一冻结输入生成稳定的幂等键。
   - 同槽存在 active attempt 时，重复点击不会产生第二个供应商提交。
   - 提交超时且有供应商任务 ID 时先查单。
   - 提交结果未知且无可验证状态时保持 `unknown`，不自动重提。
   - 服务重启后从持久化检查点恢复轮询。
   - 用户明确修改参数后创建新的 attempt，不复用旧幂等键。
2. 只对明确的提交前传输错误或供应商声明未创建任务的错误自动重试。
3. 取消 attempt 不删除已采用素材。
4. 提交：

   ```text
   Hermes: 增加生成任务幂等查单与恢复
   ```

---

## 5. 第三批：制作配方与参考装配

### Task 8：实现版本化 ProductionRecipe

**优先文件：** 复用导演计划模块；只有无合适位置时新增 `src/novelvideo/production_workflow/recipe.py`。

1. 写失败测试覆盖内置配方：
   - `consistency_first`
   - `balanced`（默认）
   - `motion_enhanced`
   - `cost_first`
2. 配方包含角色、场景、道具、镜头语法、叙事组、视频策略、参考优先级、QC 和成本权重。
3. 配方不包含供应商密钥、RunningHub 节点 ID 或具体模型调用代码。
4. 项目固定 recipe revision；系统升级后旧项目仍能解析旧版本。
5. 提交：

   ```text
   Hermes: 增加版本化影视制作配方
   ```

### Task 9：建立 ReferencePlan

**建议文件：** `src/novelvideo/media_capabilities/reference_planner.py` 及对应测试。若已有参考装配服务，扩展原服务而非新建平行模块。

1. 写失败测试覆盖类型化参考：
   - `style`
   - `character_identity`
   - `character_state`
   - `scene_base`
   - `scene_state`
   - `prop`
   - `previous_shot`
   - `first_frame`
   - `last_frame`
2. 默认装配风格、当前镜头角色、当前场景及剧情关键道具。
3. 用户取消项进入 `excluded_by_user`，不得在下游重新补回。
4. 超额时输出保留、排除、原因和优先级；禁止静默截断数组。
5. 规划器读取 `CapabilityProfile.max_references` 等能力，不按模型名字写 if/else。
6. 实际提交快照写入 `GenerationAttempt.reference_summary`。
7. 提交：

   ```text
   Hermes: 实现类型化参考图装配计划
   ```

---

## 6. 第四批：角色、场景与道具一致性资产

### Task 10：角色身份锚点与三视图状态图

**后端候选文件：** 先搜索并扩展现有角色参考图 generator/service/API；不得同时保留旧新两条无关联生成链。

1. 写失败测试：
   - 主要/常驻角色默认生成身份锚点和三面状态资源图。
   - 三个面板分别是正面、侧面、背面，保持身份、服装、体型和风格一致。
   - 目标图是“资源图”，不是三张随机剧情照。
   - 状态变化生成新 `character_state`，不覆盖身份锚点。
   - 首次 QC 通过可暂用；后续必须人工替换。
2. 输出元数据记录面板布局、状态 ID、recipe revision 和参考来源。
3. 前端资产中心显示当前版本、候选区、三视图状态和人工确认动作。
4. 提交：

   ```text
   Hermes: 增加角色三视图状态资源图
   ```

### Task 11：场景状态与空间锚点

**已知候选文件：** `src/novelvideo/generators/scene_reference_images.py` 及现有场景服务/API/前端资产中心。

1. 写失败测试：
   - `SceneBase` 保存稳定空间结构。
   - 结构或陈设变化产生 `SceneState`。
   - 纯照明变化默认复用 base 并 relight。
   - 连续 3 个以上镜头或叙事关键时间状态可以固化为场景状态。
   - `SceneAnchorPack` 能为不同机位提供同一空间参照。
2. 镜头只引用所需场景状态，禁止把所有场景参考无差别塞入请求。
3. 提交：

   ```text
   Hermes: 增加场景状态与空间一致性锚点
   ```

### Task 12：分类道具资源图与精确内容层

1. 写失败测试：
   - 剧情关键、特写、跨 2+ 镜头、重复使用或精确内容触发正式道具资产。
   - 外观状态和可读文字/屏幕内容分别建模。
   - 精确内容不能依赖图片模型自由生成后被当作正确结果。
2. 对信件、手机屏幕、证件等建立 `PropContentLayer`，允许后期确定性叠加。
3. 提交：

   ```text
   Hermes: 增加分类道具资产与精确内容层
   ```

---

## 7. 第五批：叙事组组图与画幅

### Task 13：导演阶段限制 1～4 镜头

1. 写失败测试：
   - 新导演计划不会产出 5+ 镜头组。
   - 5+ 镜头在最弱连续性边界拆组。
   - 执行层收到 5+ 镜头只返回可操作错误，不静默拆分。
   - 每个生成叙事组对应恰好一次图片供应商调用。
2. 保留旧项目超大组只读兼容。
3. 提交：

   ```text
   Hermes: 固化叙事组单次组合出图边界
   ```

### Task 14：画幅与组合画布推导

**已知前端文件：**

- `frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- `frontend/src/routes/_app/projects.$project/episodes.tsx`
- `frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx`
- `frontend/src/__tests__/lib/queries/narrative-groups.test.ts`

1. 写失败测试：
   - 叙事组页面可直接选 9:16/16:9。
   - 请求保存项目/集/组约定的 aspect。
   - 单格目标画幅与组合画布画幅分别计算。
   - 拆格后每张图严格回到目标单格画幅。
   - 视频继承目标画幅而非原始错误图片尺寸。
2. 画幅是硬约束；模型不支持时显示错误而不是隐式近似。
3. 提交：

   ```text
   Hermes: 统一叙事组图片与视频画幅契约
   ```

### Task 15：风格和默认参考真正进入组图请求

1. 写回归测试复现：项目配置“动漫风格”，实际请求不能缺失风格指令或风格参考。
2. 验证角色参考来自当前采用的身份/状态版本，场景参考来自当前场景状态。
3. 前端显示自动带入的参考托盘，每项支持取消和恢复。
4. 超额排除必须显示原因。
5. 提交：

   ```text
   Hermes: 修复叙事组风格与资产参考装配
   ```

### Task 16：拆格、局部修复与候选管理

1. 写失败测试：组图生成成功后确定性拆格；失败格可单独修复，不重做整组。
2. 修复结果生成新的 `AssetVersion`，不直接覆盖原格。
3. 记录父版本、修复区域、提示词和参考快照。
4. 提交：

   ```text
   Hermes: 增加叙事组拆格与局部候选修复
   ```

---

## 8. 第六批：分阶段 QC、工作台和批量生产

### Task 17：实现多维 QCReport

1. 写失败测试覆盖四层 QC：
   - 技术：文件、尺寸、画幅、格数、时长、音视频完整性。
   - 资产一致性：角色身份、状态、场景空间、道具和风格。
   - 叙事正确性：人物、动作、对白、镜头意图和首尾关系。
   - 人工判断：构图、表演、情绪与最终确认。
2. 不以单一总分代替维度结果。
3. 技术硬错误阻止暂用；软问题保存问题标签。
4. QC 规则和报告均带版本号。
5. 提交：

   ```text
   Hermes: 增加分阶段多维制作质量检查
   ```

### Task 18：阶段工作台与统一检查器

**主要前端区域：** 现有 episodes 工作台、叙事组组件、资产中心、任务中心；先复用现有组件，避免再建一套导航。

1. 写组件/路由测试覆盖：
   - 左侧制作树显示集、叙事组和资产确认状态。
   - 中央区域显示当前制作对象。
   - 右侧统一检查器显示参考、模型工作流、候选和 QC。
   - 底部任务条显示排队、生成中、供应商处理中、QC、失败。
   - 严格模式显示待确认数量和最终交付闸门。
2. 任务中心只做监控和诊断；普通生成操作不迫使用户离开当前页面。
3. 创作画布继续作为辅助入口，不删除现有画布能力。
4. 提交：

   ```text
   Hermes: 整合阶段工作台与制作检查器
   ```

### Task 19：分阶段批量执行与预算保护

1. 写失败测试覆盖批次：资产准备、叙事组出图、叙事组视频、最终检查。
2. “一键生成”仅执行当前确认批次。
3. 默认跳过已有满意结果；用户明确勾选才能重做。
4. 不同叙事组并行，同槽单活跃，遵守供应商并发限制。
5. 执行前显示请求次数；达到预算警戒线只暂停新任务。
6. 视频批次支持逐组调整和“生成全部组视频”。
7. 提交：

   ```text
   Hermes: 增加分阶段批量生成与预算保护
   ```

### Task 20：严格模式最终交付闸门

1. 写失败测试：
   - 普通模式允许暂用版本进入交付。
   - 严格模式缺少人工确认时返回完整待办清单。
   - 技术硬错误在任何模式下均不能交付。
   - 人工确认记录操作者、时间、版本和理由。
2. 最终确认不使用每一步弹窗骚扰；集中在交付检查页处理。
3. 提交：

   ```text
   Hermes: 增加严格模式最终交付闸门
   ```

---

## 9. 最终验证与真实低成本烟测

### Task 21：完整自动化验证

1. 运行所有本次新增和修改的定向测试。
2. 运行后端全量测试：

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   ```

3. 使用仓库声明的前端包管理器运行全量测试、类型检查和生产构建。
4. 运行：

   ```powershell
   git diff --check
   git status --short
   ```

5. 不得把“测试未运行”“因为环境问题跳过”表述为通过。记录命令、退出码、通过数和失败数。

### Task 22：真实链路烟测

**前提：** 使用现有已配置凭据；严禁在日志、截图、提交或文档中暴露密钥。使用专门测试项目和最低合理成本。

1. 创建动漫风格测试项目，设置 9:16。
2. 生成一个角色身份锚点和三视图状态图，检查三个视角及风格一致性。
3. 创建一个含 2～4 镜头的叙事组；确认风格、角色、场景参考默认带入并可取消。
4. 发起一次组合出图，验证供应商调用次数为 1、拆格数量正确、每格为 9:16。
5. 选择一个含对白的镜头：先验证缺少对白会被预检拦截，再补齐对白。
6. 使用 RunningHub MiniMax H3 完成一次 i2va 或 fl2va；记录 workflow revision、任务 ID、画幅、时长和结果。
7. 模拟客户端超时或中断后恢复，确认系统查询原任务而不是重提。
8. 再生成一个候选，确认不会覆盖当前暂用/采用版本。
9. 开启严格模式，确认未人工确认时最终交付被阻断；完成确认后交付通过。
10. 烟测产物放入专门测试输出目录，不提交媒体文件。

### Task 23：最终审查与交接

1. 对照第 0 节 20 条冻结决策逐条核验。
2. 请求代码审查，重点检查：
   - 是否存在静默降级。
   - 是否存在重复付费提交窗口。
   - 是否有候选自动覆盖当前版本。
   - 是否有错误画幅回退。
   - 是否重复创建领域模型或参考装配链。
   - 是否误提交其他终端文件。
3. 修复审查确认的问题并重新验证。
4. 输出交接报告：提交列表、测试证据、真实烟测证据、已知限制和回退开关。

---

## 10. 必须满足的验收矩阵

| 场景 | 预期结果 |
|---|---|
| 项目视觉风格为动漫 | 组图实际请求包含结构化风格指令/参考，不生成默认 3D 写实 |
| 角色、场景、风格参考 | 默认可见、默认启用、可逐项取消 |
| 参考图超额 | 显示被排除项及原因，不静默丢弃 |
| 1～4 镜头叙事组 | 恰好一次组合出图调用 |
| 5+ 镜头新组 | 导演阶段拆组，执行阶段不静默拆分 |
| 9:16/16:9 | 单格、拆格和视频均符合目标画幅 |
| H3 对白意图但无对白 | 提交前字段级拦截 |
| H3 不可用 | 无等价预批准配置则停止提示 |
| H3 请求超时 | 查询原任务，不盲目重提 |
| 首个 QC 通过候选 | 空槽位时自动暂用 |
| 后续候选 | 不覆盖当前版本，人工替换 |
| 技术硬错误 | 任何模式都阻断暂用/交付 |
| 内容软问题 | 保存候选，可带理由人工采用 |
| 严格模式 | 未人工确认不能最终交付 |
| 旧项目 | 正常打开，媒体不搬动，未知参数不伪造 |

## 11. 完成定义

只有同时满足以下条件，执行终端才可以声称完成：

- 20 条冻结决策均有实现或明确的测试证据。
- 所有修改均由测试先行，并保留失败到通过的证据。
- 定向测试、全量测试、前端构建和 `git diff --check` 已实际运行。
- 完成一次低成本真实组图 + RunningHub H3 视频链路。
- 真实测试没有重复提交、错误画幅或静默降级。
- 没有提交其他终端的未归属改动或测试临时目录。
- 每个垂直批次有独立、可回退的 `Hermes:` 提交。

