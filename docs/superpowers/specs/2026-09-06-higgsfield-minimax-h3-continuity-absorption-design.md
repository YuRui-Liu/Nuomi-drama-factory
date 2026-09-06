# Higgsfield 方法论 × MiniMax H3 连续性吸收设计

## 1. 摘要

本设计将 `assets/higgsfield` 中可迁移的导演方法论吸收到 Nuomi Drama Factory 的现有 H3 生产链中，优先解决两个问题：

1. 提高 MiniMax H3 单镜头第一次付费生成即可进入剪辑的概率；
2. 提高相邻镜头在人物、空间、道具、摄影机和灯光上的连续性。

方案采用已经确认的路线：

- **B 为主**：在 `DirectorPlan` 与 H3 Prompt/Provider 之间引入结构化 `ShotContinuityContract`、风险审计器、共享编译核心和可复现 Manifest；
- **C 按镜头风险启用**：只对高空间风险镜头调用现有 Director World，生成精确 camera/actor/prop snapshot 或控制帧；
- 同时覆盖现有 `runninghub:minimax-h3` 和规划中的 `runninghub:minimax-h3-ref`；
- Ref 增强身份与资产锚定，但不替代首帧、尾帧或空间控制；
- 运动不可达、动作过载等问题通过拆镜或改写解决，不用更长 Prompt 或 Director World 掩盖。

本设计不直接复制 Higgsfield 的平台参数或提示词模板，而是吸收其稳定的方法：先建立场景与主体事实，再做风险诊断、首帧占位、空间调度、动作时序、镜头结果描述和模型能力路由。

## 2. 背景与当前状态

### 2.1 当前已有能力

项目已经具备本设计所需的大部分基础设施：

- `src/novelvideo/director_plan/models.py` 中的 `DirectorShotIntent` 和 `ShotPlan` 已表达主体、动作、空间锚点、起止可见状态、景别、机位、构图、摄影机运动和资产需求；
- `src/novelvideo/director_world/` 已保存 camera、actors、props 和 stagings，可为高风险镜头提供精确空间快照；
- `src/novelvideo/task_backend/runners/narrative_group_video.py` 已能把 Director World 上下文传给 H3 optimizer；
- `src/novelvideo/media_capabilities/video/h3_director_plan.py` 已区分 I2VA 与 FL2VA，并表达动作时序、摄影机计划、连续性锁和首尾帧差异；
- `h3_prompt_compiler.py`、`h3_prompt_profile.py` 与 `h3_prompt_quality.py` 已提供 typed plan、Prompt 编译和付费调用前质量门；
- `docs/superpowers/specs/2026-09-06-minimax-h3-reference-director-design.md` 已定义 H3 Ref 的独立模型、全局 Ref、Subject/Picture 映射、能力门控和混合输入兼容性关卡。

### 2.2 当前缺口

现有信息分散在 Screenplay Semantics、DirectorPlan、Director World、H3DirectorPlan、资产引用和任务 payload 中，缺少一个跨阶段、可版本化、可比较的镜头连续性真相层。因此：

- 上一镜头的可见末态不能可靠地成为下一镜头的明确入口；
- 空间、身份、运动和连续性风险未被分开诊断；
- I2VA/FL2VA 的选择更接近输入可用性判断，而不是端点可达性判断；
- 当前 Prompt profile 要求指定摄影机运动，容易被误解为每个镜头都必须运动；
- Provider 重试、Prompt 改写、素材变化和契约修订之间缺少清晰边界；
- 当前运行 registry 只注册 `runninghub:minimax-h3`，H3 Ref 仍属于需经真实工作流能力验证后才能启用的设计能力。

## 3. 目标与非目标

### 3.1 目标

- 以结构化事实而非长 Prompt 传递跨镜头连续性；
- 在付费生成前发现缺素材、状态冲突、端点不可达和动作过载；
- 为 Base H3 与 H3 Ref 提供同源、可测试的编译路径；
- 只把高空间风险镜头送入 Director World；
- 明确区分计划末态与生成视频实际末态；
- 使每次生成输入、路由、风险、尝试和验收结果可追溯、可重放；
- 通过基线、影子运行和小流量 A/B 验证实际收益。

### 3.2 非目标

- 不构建所有镜头共享的完整三维世界或全量 3GS 流程；
- 不替换 `DirectorPlan`、`H3DirectorPlan` 或现有任务基础设施；
- 不把 `assets/higgsfield` 中的平台专属参数、UI 操作或“魔法词”直接写入 H3 Prompt；
- 不在第一阶段引入完整 ACTING 表演系统；
- 不为所有视频供应商设计通用 Prompt DSL；
- 不在 H3 Ref 能力尚未验证时模拟 Ref 或静默回退 Base；
- 不自动把生成结果中的模型偏差升级为新的项目事实。

## 4. Higgsfield 方法论吸收映射

| Higgsfield 原则 | 在本项目中的落点 | 是否进入第一阶段 |
|---|---|---|
| 先建立 location/coverage，再生成镜头 | `SceneLock`、场景主图、反打图、空间布局与 Director World snapshot | 是 |
| Active refs 与首帧占位 | `SubjectLock`、`PropLock`、首帧事实校验、H3 Ref Subject/Picture 映射 | 是 |
| 空间 blocking、视线与身体朝向 | `SceneLock`、`SubjectLock`、S 风险与 Director World 路由 | 是 |
| 镜头描述以可见结果为中心 | 共享 H3 Compiler 的 camera/result/terminal-state 结构 | 是 |
| 动作时间与物理可达性 | H3 action timeline、M 风险、FL2VA endpoint reachability | 是 |
| 模型特性决定提示词密度与路由 | capability snapshot、Base/Ref adapter、I2VA/FL2VA selector | 是 |
| 表演目标、障碍、策略、潜台词和微反应 | 未来 PerformancePlan，与连续性契约分层 | 否 |
| 平台专属 prompt 模板和操作步骤 | 不吸收 | 否 |

## 5. 总体架构

```text
Screenplay Semantics
        │
        ▼
DirectorPlan ───── Assets / References
        │                    │
        └────────┬───────────┘
                 ▼
      ShotContinuityContract
                 │
                 ▼
      H3 Shot Risk Auditor
       S / I / M / C = 0..2
          │          │
          │          └── S2 → Director World snapshot/control frames
          ▼
      Shared Shot Compiler
      ├── mode selector: I2VA / FL2VA / reject
      ├── Base H3 Adapter
      └── H3 Ref Adapter (capability gated)
                 │
                 ▼
       CompiledShotBundle
                 │
                 ▼
       Existing task/provider runtime
                 │
                 ▼
      Postflight + observed carry_out
                 │
                 ▼
        Generation Manifest
```

`ShotContinuityContract` 是跨阶段连续性事实的唯一权威；`DirectorPlan` 仍是叙事与导演意图权威；`H3DirectorPlan` 仍是 H3 内部动作时序权威；Compiler 负责供应商措辞。三者不互相复制完整内容。

## 6. ShotContinuityContract v1

### 6.1 顶层结构

```json
{
  "schema_version": 1,
  "revision": 3,
  "shot_id": "shot-12",
  "scene_id": "scene-4",
  "predecessor_shot_id": "shot-11",
  "scene": {},
  "subjects": [],
  "props": [],
  "camera": {},
  "lighting": {},
  "boundary": {},
  "director_world": null
}
```

### 6.2 锁类型

#### SceneLock

- 可见场景状态与时间状态；
- 稳定地标和主体相对地标的位置；
- 动作轴线与 camera side；
- 屏幕方向约束；
- 场景主图或空间布局的稳定资产 ID 与内容哈希。

#### SubjectLock[]

- 稳定 subject ID 与身份资产；
- 当前外观、服装和可见伤痕等状态；
- 屏幕位置、朝向、视线目标和可见性；
- 与其他主体、道具、地标的关系；
- 事实来源：`explicit`、`director_world` 或 `inferred`；
- inferred 值的 confidence，关键锁必须经过人工或 Director World 确认才能作为硬约束。

#### PropLock[]

- 稳定 prop ID 与视觉资产；
- 当前状态、owner、持有手；
- 接触、支撑或遮挡关系；
- 是否属于跨镜必须保持的关键道具。

#### CameraLock

- 起始和结束的景别、机位、构图结果；
- static 或有明确意图的单一 camera movement；
- 运动方向、幅度、速度和结束构图；
- 首帧、尾帧、Director World 控制帧的稳定引用；
- 轴线与 screen direction 约束。

#### LightingLock

- 主光来源和方向；
- 阴影方向与关键曝光优先级；
- 场景连续性需要保留的色温或时间状态；
- 不保存供应商专属调色参数。

#### BoundaryState

- `carry_in`：镜头 frame 0 必须可见的状态；
- `planned_carry_out`：镜头计划达到的末态；
- `observed_carry_out`：最终采用视频中验收得到的实际末态；
- 相邻镜头默认要求前镜头 `observed_carry_out` 与后镜头 `carry_in` 一致；
- 若不一致，必须有显式时间跳转、画外动作、镜头设计或已批准 revision 解释。

### 6.3 契约明确不保存

- 不保存最终自然语言大 Prompt；
- 不复制剧情目标、台词或完整 DirectorPlan；
- 不复制逐帧 action timeline；
- 不写死 Workflow ID、分辨率、供应商上限等 Provider 配置；
- 不把自动视觉分析或模型猜测无条件写成硬事实；
- v1 不保存任意三维坐标。精确 3D 数据只通过高风险镜头的 Director World snapshot 引用。

### 6.4 Revision 语义

以下任一变化都产生新 Contract revision：

- 锁定事实或事实来源变化；
- 首尾帧、引用资产或 Director World snapshot 变化；
- 对上一镜头 observed carry_out 的接受方式变化；
- 人工把 inferred 值提升为 confirmed；
- 显式接受连续性偏差并修改后续入口。

Provider 的瞬时重试不会修改 Contract revision。

## 7. H3 Shot Risk Auditor

风险按四个独立维度评分，每项为 0、1、2。系统不能只使用一个相加总分，因为不同风险对应不同处置。

### 7.1 空间风险 S

- `S0`：单主体、稳定背景、无关键相对位置变化；
- `S1`：多主体或简单调度，但现有首尾帧足以表达；
- `S2`：反打、过肩、复杂遮挡、跨区移动、精确轴线/视线/地标关系。

`S2` 路由到 Director World，生成或读取精确 camera/actor/prop snapshot 与控制帧。Director World 不用于修复身份和动作过载。

### 7.2 身份风险 I

- `I0`：单主体、无遮挡、无显著外观变化；
- `I1`：多主体、角度变化或轻遮挡；
- `I2`：多人相似外观、强遮挡、极端角度或必须保持关键资产细节。

`I2` 优先要求 H3 Ref。若 registry 未确认 Ref capability，系统报告 `missing_reference_capability`，不得静默回退 Base。用户只有在修改镜头或显式降低风险要求后才能走 Base。

### 7.3 运动风险 M

- `M0`：一个明确动作或轻微表演；
- `M1`：2–3 个单向、连续、物理可达的动作节拍；
- `M2`：动作过载、方向反复、多主体复杂交互或动作与复杂运镜竞争。

`M2` 的默认处置是拆镜、简化动作或简化摄影机。不能通过 Director World、Ref 或增加 Prompt 长度自动解决。

### 7.4 连续性风险 C

- `C0`：镜头不依赖上一镜头的精确可见末态；
- `C1`：需要保持身份、服装、环境等常规状态；
- `C2`：下一镜头依赖精确姿态、道具状态、屏幕位置、朝向或末端构图。

`C2` 要求明确 carry state；若末态可达且有有效尾帧，优先 FL2VA。若末态不可达或缺少必要证据，则阻断并要求修正，而不是生成后再碰运气。

### 7.5 组合与优先级

1. 先处理 `M2` 和不可达端点；
2. 再检查缺失素材、Ref capability 与 Contract 冲突；
3. `S2` 可与 `I2` 组合为 Director World + H3 Ref；
4. `C2` 决定边界控制强度，但不自动覆盖运动可达性判断；
5. 每个路由结果保存 reason codes，供 UI、日志与 Manifest 使用。

## 8. 双 H3 Compiler

### 8.1 共享编译核心

Base H3 与 H3 Ref 共用 `SharedShotCompiler`，输入为：

- `H3DirectorPlan`；
- `ShotContinuityContract`；
- 风险审计报告；
- runtime capability snapshot；
- 当前视频单元首帧、可选尾帧和控制帧。

共享核心负责：

1. 校验所有稳定 ID、revision 和内容哈希；
2. 合并 DirectorPlan 意图与连续性锁，但不改变其权威边界；
3. 判断端点可达性并选择 I2VA、FL2VA 或 reject；
4. 把镜头约束压缩为适合 H3 的 Prompt；
5. 输出结构化诊断和 `CompiledShotBundle`。

### 8.2 Prompt 顺序

编译后的单镜头 Prompt 使用固定语义顺序：

1. Picture 1/frame 0 的准确可见事实；
2. 有效主体数量、身份与首帧占位；
3. 空间站位、轴线、视线与接触关系；
4. 单一摄影机意图，可为 static；
5. 2–3 个按时间推进、单向且物理可拍的动作节拍；
6. 动作的可见物理结果与必要末态；
7. 灯光、声音和最小必要连续性锁；
8. FL2VA 时明确自然收敛到 Picture 2。

Prompt 不重复所有资产描述，不添加无来源的角色、道具、文字、UI、粒子或场景变化。

### 8.3 Base H3 Adapter

Base Adapter 只提交当前工作流真实支持的输入：

- 必需首帧；
- FL2VA 时的尾帧；
- 高空间风险镜头的 Director World 控制帧；
- 共享核心编译的精简 Prompt；
- registry/profile 解析的供应商参数。

Base Adapter 不伪造全局 Ref，也不把引用图片描述塞进大段 Prompt 来模拟 Ref。

### 8.4 H3 Ref Adapter

H3 Ref Adapter 在 Base 控制之外增加：

- 叙事组级角色、场景和关键道具 Ref；
- 稳定 Picture 顺序；
- Subject/Picture definitions；
- 每个镜头对 active subjects/props 的引用；
- 独立 workflow profile 与 capability contract。

H3 Ref 仍需首帧，FL2VA 仍需尾帧。Ref 只增强身份和资产锚定，不替代 frame 0 事实、空间控制或末态可达性。

启用规则继承 `2026-09-06-minimax-h3-reference-director-design.md` 的真实烟测关卡。工作流尚未证明同时支持 Ref 与首尾帧之前，registry 不得把它标记为可用。

## 9. I2VA / FL2VA 模式选择

### 9.1 选择 I2VA

同时满足以下倾向时选择 I2VA：

- 首帧能够准确表达 frame 0；
- 结尾只有软目标，不要求精确构图或状态交接；
- 动作过程需要自然发挥空间；
- 下一镜头不依赖本镜头的精确 observed carry_out。

I2VA 不代表无末态。Compiler 仍描述可见结果，只是不使用尾帧硬锁其精确像素构图。

### 9.2 选择 FL2VA

同时满足以下条件时选择 FL2VA：

- 下一镜头依赖本镜头精确末态，或导演明确要求终止构图；
- 尾帧表达关键姿态、道具、位置或构图；
- 起止状态可由一个不中断的连续动作自然连接；
- 主体身份、地点和空间拓扑不发生跳变；
- 动作数量与时长匹配。

### 9.3 拒绝或改写

下列情况不应通过 I2VA/FL2VA 二选一兜底：

- 起止帧换人、换地或空间拓扑跳变；
- 要求 teleport、morph、无解释换装或无动作换手；
- 动作过多、方向反复或多人复杂互动超出时长；
- 复杂动作同时要求复杂运镜；
- 必需尾帧、Ref 或控制帧缺失；
- Contract 与 DirectorPlan 存在未解决冲突。

系统返回可操作诊断：拆镜、减少动作节拍、改 static camera、补资产、修订 carry state 或进入 Director World。

## 10. CompiledShotBundle

Compiler 的输出不是裸 Prompt，而是版本化 Bundle：

```json
{
  "schema_version": 1,
  "shot_id": "shot-12",
  "contract_revision": 3,
  "compiler_id": "minimax-h3-shot-compiler",
  "compiler_version": 1,
  "adapter": "base-h3",
  "mode": "fl2va",
  "prompt": "...",
  "first_frame": {"asset_id": "...", "sha256": "..."},
  "last_frame": {"asset_id": "...", "sha256": "..."},
  "control_frames": [],
  "references": [],
  "risk_report": {},
  "diagnostics": []
}
```

Bundle 内容变化必须产生新的 bundle hash。Provider 瞬时重试复用冻结的同一 Bundle，不能重新读取当前项目资产或当前 Contract。

## 11. 数据流与边界验收

### 11.1 Build

Contract Builder 从以下来源构建当前镜头契约：

- 当前 DirectorPlan 与稳定资产；
- 场景状态和镜头 coverage；
- 上一镜头已批准的 `observed_carry_out`；
- 必要时的 Director World snapshot。

如果上一镜头尚无最终采用结果，Builder 只能使用 `planned_carry_out` 并标记依赖未决，不能伪装成已观察事实。

### 11.2 Preflight

在任何付费调用前执行：

- schema、revision、资产与哈希校验；
- 前后镜头 carry state diff；
- S/I/M/C 风险评分；
- I2VA/FL2VA endpoint reachability；
- runtime capability 与 Ref availability 检查；
- H3 Prompt quality gate。

### 11.3 Compile & Run

通过 Preflight 后生成 Bundle，由现有任务、并发、上传、轮询、恢复、下载和媒体质检基础设施执行。新层不复制 Provider runtime。

### 11.4 Postflight

最终采用某个 attempt 后记录 `observed_carry_out` 并与计划末态比较：

- 一致：后续依赖镜头可以继续；
- 不一致且不可接受：重生成或修改控制；
- 不一致但导演接受：创建显式 Contract revision，并更新依赖镜头；
- 自动视觉分析可以提出候选状态或告警，但 v1 不允许其自动覆盖 confirmed 事实。

## 12. 局部串行与并行生成

- `C0/C1` 且不依赖前镜头最终画面的镜头可以基于计划契约并行生成；
- `C2` 或直接使用前镜头最终尾帧的镜头，必须等待前镜头 attempt 被选中并完成 Postflight；
- 串行化单位是跨镜传递关键可见状态的局部依赖链，而不是整场戏；
- revision 变化只使依赖该 revision 的后继镜头失效，不使无关镜头失效。

## 13. 失败分类与重试策略

| 类型 | 示例 | 处置 | 原样重试 |
|---|---|---|---|
| `contract_invalid` | 左右位置冲突、owner 缺失、revision 过期 | 修正上游事实 | 否 |
| `unreachable_motion` | 首尾帧换场、动作过载 | 拆镜、简化、改模式 | 否 |
| `missing_capability` | I2 需要 Ref，但 Ref workflow 不可用 | 阻断或显式降低需求 | 否 |
| `provider_transient` | 超时、限流、临时 5xx | 指数退避，复用 Bundle | 是 |
| `generation_defect` | 偶发肢体异常、闪烁、局部伪影 | 在预算内有限重采样 | 有限 |
| `continuity_mismatch` | observed carry_out 不符合后镜头入口 | 重编译控制、升级 FL2VA/Ref 或修订契约 | 不原样 |

同一 Bundle 的每个 attempt 都保留。系统不得把修改 Prompt、素材、模式或参数后的调用计为同一次重试。

## 14. Generation Manifest

每次任务冻结：

- 项目、场景、镜头、DirectorPlan 和 Contract revision；
- Contract、DirectorPlan 与 Bundle 哈希；
- capability snapshot、Base/Ref 路由和 I2VA/FL2VA 模式；
- S/I/M/C 分数、reason codes、warnings 与 blockers；
- compiler/profile/adapter 版本；
- Prompt、首尾帧、控制帧和 Ref 的稳定 ID、顺序与 SHA-256；
- 实际 workflow、产品参数与 Provider 支持的 seed；
- provider task ID、attempt 历史和规范化错误；
- 输出 SHA-256、最终选中 attempt、质量报告和 observed carry_out；
- 若发生人工 override，记录操作者、原因、时间与前后 revision。

Manifest 不保存密钥、短期签名 URL 或无法稳定解析的临时本地路径。历史任务按冻结快照解释，不能按当前资产重新推导。

## 15. 测试策略

### 15.1 单元与 Golden 测试

- Contract schema、revision 和来源/confidence 规则；
- carry state diff 与显式变化解释；
- S/I/M/C 每个风险维度和组合优先级；
- I2VA、FL2VA、reject 的边界条件；
- Base/Ref Compiler golden outputs；
- “static camera 合法”回归，移除强制运镜假设；
- Manifest 哈希、脱敏、attempt 与 revision 语义；
- 自动观察值不能覆盖 confirmed 事实。

### 15.2 集成与 Replay 测试

- Base H3 adapter 使用现有 runtime 且不改变旧 payload 的非目标字段；
- H3 Ref capability 不可用时明确阻断；
- H3 Ref fixture 同时包含 Ref、Subject/Picture 和首尾帧；
- Director World 只在 S2 或显式导演 override 时调用；
- 历史 Manifest 可重放出字节级语义一致的 Bundle；
- Provider 瞬时错误复用同一 Bundle；其他修改产生新 revision；
- 局部依赖链的失效传播正确，不误伤无关镜头。

### 15.3 代表性真实镜头集

建立覆盖低、中、高风险的固定评测集，至少包括：

- 单主体静态或轻动作；
- 走位与道具交接；
- 双人对话、过肩与反打；
- 精确尾帧交接；
- 多人相似身份或强遮挡；
- 明确不可达的首尾帧；
- 动作过载与复杂运镜冲突。

旧流程与新流程使用相同源素材、时长、分辨率和预算进行盲评；付费真实烟测需单独授权。

## 16. 衡量指标

### 16.1 单镜头

- `first_pass_usable_rate`：第一次付费生成即可进入剪辑的镜头比例；
- `attempts_per_accepted_shot`：每个最终采用镜头的平均付费尝试次数；
- `structural_rejection_precision`：Preflight 阻断中确实需要改写或补素材的比例；
- 主体、动作、摄影机、末态各子项通过率。

### 16.2 跨镜头

- `boundary_match_rate`：相邻镜头 observed carry_out 与 carry_in 的匹配比例；
- `lock_violation_rate`：按身份、空间、道具、摄影机和灯光分别统计；
- `continuity_repair_time`：从发现不连续到获得可用镜头的人工时间。

### 16.3 成本与性能护栏

- 单个采用镜头的 Provider 成本；
- 被结构性失败浪费的付费尝试数；
- Preflight 延迟；
- Director World 被调用的镜头比例；
- 人工 override 和错误阻断比例。

发布门槛以 Phase 0 的同题材基线为准，不在缺少真实数据时虚构绝对数字。进入下一阶段至少要求主指标改善、连续性不回退、单位采用镜头成本与人工修复时间不出现不可接受回退。具体阈值在基线冻结时版本化记录。

## 17. 灰度启用

### Phase 0：Baseline

- 从历史任务和固定评测集建立旧流程基线；
- 冻结指标口径、标注规范和题材/风险分层；
- 不改变任何生成行为。

### Phase 1：Observe

- 影子生成 Contract、风险报告、模式选择和新 Prompt；
- 不阻断、不调用 Director World、不改变 Provider payload；
- 检查误报、漏报、Golden 和 replay 稳定性。

### Phase 2：Guard

- 只启用确定性阻断：缺资产、revision 冲突、明确状态矛盾、不可达端点；
- 阻断必须带修复建议，并可通过修改输入恢复。

### Phase 3：Base Pilot

- 通过功能开关让小流量镜头使用 Base H3 Compiler；
- S2 镜头按风险进入 Director World；
- 其余镜头继续使用现有首/尾帧资产路径；
- 与旧流程进行同分层 A/B。

### Phase 4：Ref Enable

- 只有 `runninghub:minimax-h3-ref` 真实 capability contract 与授权烟测通过后才开放；
- I2 镜头可以路由 H3 Ref；
- Ref 不可用时不自动回退 Base；
- 保持独立功能开关与回滚路径。

## 18. 第一阶段实施边界

### 18.1 纳入

- `ShotContinuityContract v1`、存储与 revision；
- Contract Builder 和相邻镜头 carry state diff；
- S/I/M/C 风险审计与 reason codes；
- I2VA/FL2VA endpoint reachability；
- Base H3 共享 Compiler 与 Adapter；
- H3 Ref Adapter 接口、fixtures 和 capability gate；
- S2 的 Director World 路由；
- `CompiledShotBundle`、Generation Manifest 和 replay；
- Postflight planned/observed state 验收入口；
- 指标采集、影子模式和功能开关。

### 18.2 延后

- 完整 PerformancePlan 与 Higgsfield ACTING 方法；
- 自动视觉理解生成高置信 observed state；
- 全镜头三维空间重建；
- 通用多供应商 Compiler DSL；
- 自动剪辑点、节奏或声音设计重构；
- 供应商未证明支持的 Ref/帧组合。

## 19. 兼容性与迁移

- 现有 `runninghub:minimax-h3` 保持稳定模型 ID 和默认地位；
- Observe 阶段只旁路计算，不改变历史任务与当前 payload；
- 没有 Contract 的历史镜头按 legacy 路径解释；需要新流程时显式生成 revision 1；
- 新 manifest 字段必须版本化并允许旧记录缺失；
- `H3DirectorPlan.continuity_locks` 在迁移期由 Contract Compiler 生成，但旧直接输入仍可读取；
- 当前 profile 中“Specify the camera movement”应改为“Specify whether the camera is static or moving; when moving, state direction and result”，并通过回归测试保证 static camera 合法；
- 新层复用现有任务、上传、并发、轮询、恢复、下载和媒体质检，不建立第二套运行时。

## 20. 已确认决策与剩余关卡

### 已确认

- 优先级同时覆盖 H3 单镜头成功率和跨镜头连续性；
- B 为主、C 按镜头空间风险启用；
- Base H3 与 H3 Ref 共享结构化核心、使用独立 Adapter；
- Ref 不替代首尾帧；
- I2VA/FL2VA 由末态必要性与可达性决定；
- `planned_carry_out` 与 `observed_carry_out` 分离；
- 结构性错误不原样重试；
- 第一阶段不引入完整表演系统和完整 3D 世界。

### 实施前仍需通过的客观关卡

- 以真实 RunningHub fixture 和一次单独授权的最低成本烟测，证明 H3 Ref workflow 支持 Ref 与首/尾帧混合输入；
- Phase 0 冻结评测集、人工“可用”口径和灰度晋级阈值；
- 实施计划必须逐文件确认现有模型、存储、API、runner 与前端的最小改动边界，避免与当前未提交工作冲突。
