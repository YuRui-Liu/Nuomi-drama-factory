# Higgsfield 刚性镜头提示词与灯光能力设计

> 状态：待书面审阅  
> 日期：2026-09-07  
> 范围：Nuomi Drama Factory 的 MiniMax H3 导演计划、提示词编译与付费调用前质量门

## 1. 目标

把 Higgsfield《Hell Grind》公开制作方法中的十五段镜头提示词骨架完整吸收到现有 H3 生产链，并将灯光从普通连续性文字升级为可推导、可编辑、可编译、可校验的一级能力。

实现后：

- 保留 MiniMax H3 现有外层 wire 格式；
- `integrated_multimodal_description` 内部固定按十五段协议编译；
- 新生成的导演计划使用结构化字段，不再依赖模型在一段长文本中自行补齐；
- 人工填写的内容优先，自动推导只补空字段；
- 2D、2.5D、3D 等表现形式严格继承项目当前风格，不强制写实、IMAX、胶片或摄影机术语；
- 在付费视频生成前发现人物数量、引用、空间、灯光、对白和时长冲突。

## 2. 固定十五段协议

`integrated_multimodal_description` 必须按以下顺序输出，标题保持稳定：

1. `SCENE CONTEXT`
2. `ACTIVE REFERENCES`
3. `LOCATION MAP`
4. `FIRST FRAME AND SPATIAL BLOCKING`
5. `FORMAT MODE`
6. `OPTICS`
7. `CAMERA`
8. `ACTION TIMING`
9. `PHYSICS`
10. `LIGHTING`
11. `AUDIO`
12. `CHARACTER ACTING`
13. `STYLE`
14. `QUALITY`
15. `POSITIVE CONSTRAINTS`

该顺序是编译契约，不由运行时模型自由改写。现有 H3 外层图片对齐语句、`overall_soundscape` 和 `non_diegetic_music` 字段继续保留。

## 3. 结构化导演计划

### 3.1 场景与引用

`SCENE CONTEXT` 保存精确人物数量、人物 ID、事件、地点、时间状态和镜头时长。编译时首行固定生成 `EXACT N CHARACTERS — NO DUPLICATES`，数量必须与实际 active characters 一致。

`ACTIVE REFERENCES` 中每项至少包含：

- 稳定 `@tag`；
- `character` 或 `location` 类型；
- 本镜头承担的角色；
- 只允许继承的视觉信息；
- 禁止继承的构图、角度、起始帧或调色信息。

引用必须解析到真实资产，禁止生成不存在的标签。角色状态、场景日夜或天气状态使用独立资产标签，不在同一描述中混合多个状态。

### 3.2 空间、格式、光学与摄影机

`LOCATION MAP` 保存稳定地标、frame-left/frame-right 位置、距离、动作轴线、camera side 和必要的空间光源锚点。它只描述空间，不复制人物动作。

`FIRST FRAME AND SPATIAL BLOCKING` 保存 frame 0 中每个主体的位置、朝向、视线、相互距离、持有物和摄影机初始状态。所有 active characters 必须在首帧有明确状态。

`FORMAT MODE` 保存：

- `single_take` 或 `hard_cuts`；
- 总时长；
- 是否实时；
- 是否允许变速；
- 切点列表。

`OPTICS` 独立于摄影机运动，保存镜头或视场角、机位高度、主体距离、景深及焦点转移计划。

`CAMERA` 扩展现有摄影机计划，保存行为、方向、幅度、速度、结束构图以及禁止的额外运动。静态摄影机仍是合法选择。

### 3.3 动作、物理与表演

`ACTION TIMING` 继续以帧为内部真相，编译成秒。每个动作节拍必须覆盖明确区间、使用现在时、描述可见动作与结果；单个节拍最多三句。

`PHYSICS` 为移动主体和道具保存重量、接触、支撑、惯性、动量变化及接触阴影。它描述可观察的物理结果，不使用“自然地”“有力量感”等空泛形容词。

`CHARACTER ACTING` 按 active character 保存：

- 当前状态；
- 当下想要什么；
- 隐藏什么；
- 主导身体节奏；
- 可见习惯或微反应；
- 镜头内发生的变化。

对白文本只进入 `AUDIO`，不得在动作段重复。

## 4. 灯光一级能力

灯光分为三个层级，并按以下优先级合并：

1. 场景/场景状态的稳定光源事实；
2. 当前镜头的灯光调度；
3. 项目 Style Prefix 的审美倾向。

低优先级不得覆盖高优先级的物理事实。出现“窗外日光”与“顶灯为唯一光源”等矛盾时，付费调用前阻断，而不是静默拼接。

镜头级 `LightingPlan` 至少包含：

- `source_logic`：本镜头唯一、连贯的动机光逻辑；
- `primary_source` 与明确来源位置；
- 照射方向和阴影方向；
- 光质：软硬、扩散或聚束；
- 色温或颜色关系；
- 主体面部、身体、服装和关键道具的受光结果；
- 背景与主体的曝光层级；
- 补光、反光或明确无补光；
- 可见眼睛的眼神光策略；
- 接触阴影与运动中的光影连续性；
- 跨镜头连续性键。

“唯一光源逻辑”表示所有可见光必须能由同一套物理逻辑解释，不要求所有场景只能存在一个灯具。项目为 2D、2.5D 或 3D 时仍生成同样的光源关系，但用当前风格的视觉语言表达，不自动加入写实皮肤、胶片颗粒或物理摄影机效果。

## 5. 音频、风格、质量和正向约束

`AUDIO` 保存环境声、SFX、逐字对白、说话人、稳定声音描述、表演方式和对白时序。生成阶段固定无配乐；H3 外层 `non_diegetic_music` 编译为明确的 `No music. SFX only.`，音乐留给后期。

`STYLE` 必须逐字使用当前项目或扩展风格的 Style Prefix。若项目没有显式 Style Prefix，才由现有风格字段确定性生成一次，并在同一项目内复用。

`QUALITY` 保存当前风格适用的细节与稳定性要求，包括身份、服装、道具、空间、灯光、帧间稳定、闪烁和抖动约束；不得无条件加入“8K”“毛孔级皮肤”等写实专属要求。

`POSITIVE CONSTRAINTS` 保存精确数量、左右侧、尺寸、比例、状态和可见结果。所有禁止项都必须同时转写成模型应当呈现的正向事实；不能只输出负向提示。

## 6. 推导与编辑规则

字段来源优先级为：

1. 用户在导演台明确填写或锁定的值；
2. 已确认的角色、场景、道具及其状态资产；
3. DirectorPlan、ShotContinuityContract 和 Director World；
4. 剧本语义与相邻镜头边界；
5. 项目风格与确定性默认值。

自动优化只补全空字段，不覆盖非空字段。不同来源冲突时输出字段级错误，错误必须包含冲突来源和建议修复位置。

## 7. 编译与兼容

现有外层格式继续为：

```text
[Picture alignment when required]

integrated_multimodal_description: [十五段正文]

overall_soundscape: [diegetic ambience and SFX]

non_diegetic_music: No music. SFX only.
```

旧的 H3 typed plan 和已保存结果保持可读；新生成和重新优化统一产生新版计划。旧计划只有在需要重新生成时才迁移，迁移遵循“保留非空、仅补空字段”，不批量重写历史项目。

## 8. 付费调用前质量门

质量门至少验证：

- 十五段齐全且顺序固定；
- exact character count 与 active characters 一致，无重复主体；
- 所有 active references 可解析且角色用途明确；
- location map 有地标、camera side 和轴线；
- active characters 在 frame 0 均有站位、朝向和视线；
- format duration 与总帧数一致，切点合法；
- optics 同时包含镜头/视场角和焦点计划；
- camera 行为与禁止运动不矛盾；
- action timeline 连续覆盖镜头时长；
- 移动物体具备重量、接触或惯性描述；
- lighting 有来源、位置、方向、阴影、主体结果和环境结果，且不存在互相冲突的光源逻辑；
- dialogue 逐字保持，只由指定人物说出；
- 所有 active characters 都有 acting plan；
- Style Prefix 与项目风格一致；
- 正向数量约束与场景、引用和道具数量一致；
- `No music. SFX only.` 存在。

质量门失败时不调用付费视频供应商，并将结构化问题交给现有自动修订流程；自动修订仍不得覆盖用户锁定字段。

## 9. 实施范围与验证

首轮只修改 H3 领域模型、优化器输入、编译器和质量门，以及对应定向测试。暂不新增导演台 UI 表单；现有导演计划和资产信息先自动投影到新字段，最终提示词仍可在当前结果界面查看。

采用 TDD，只运行以下针对性测试：

- H3 director plan schema；
- H3 prompt compiler；
- H3 prompt optimizer；
- H3 quality gate；
- H3 pipeline 的付费调用前阻断测试。

不在本轮执行全仓库测试、真实付费生成或视觉回归测试。

