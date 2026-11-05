# 专业岗位方法与 H3 案例边界

本文件把专业方法分配到对应岗位，并说明哪些做法来自公开提示指南、哪些只是案例启发。它不是供应商承诺或额外批准门槛；项目事实、当前 canvas contract 与 `canvas capabilities` 优先。执行步骤与字段见 [stage-contracts.md](stage-contracts.md)、[storyboard-direction.md](storyboard-direction.md)、[cinematic-direction.md](cinematic-direction.md)、[h3-experience.md](h3-experience.md) 和 [voice-and-audio.md](voice-and-audio.md)。外部来源清单、访问限制与调研记录见 [`docs/research/2026-10-25-studio-reference-map.md`](../../../docs/research/2026-10-25-studio-reference-map.md)。

## 岗位到交付物

| 岗位 | 制作输入与决策 | 可检查交付物 |
| --- | --- | --- |
| 编剧／故事编辑 | 锁定事实、人物目标、冲突、转折、可见结果；保留原台词和来源 | 完整剧本、分场因果、人物关系／连续性表 |
| 造型／场景／道具设计 | 角色身份与衣着锚点、场景地标、道具状态；区分事实与提案 | 带来源的 asset brief、批准设计版本、用途明确的参考图 |
| 分镜／执行导演 | 场景坐标、轴线与机位侧、人物和道具走位、景别、连续性、单格所选瞬间 | 一镜一行的分镜表、静帧 panel、两者一致的宫格 |
| 场景预演／摄影 | 3D 站位、相机视角、光源方位与强弱；先确认代理对象身份 | 已保存 3D world / scene-blocking 版本及人工核对的场面调度 |
| 表演指导 | 由剧本动机推出可见表情、视线、姿态、手势或停顿；不添加无因果动作 | director shot 的连续动作与当前面板 `pose/state` |
| 配音／声音设计 | 稳定角色音色与逐句表演分离；对白、对白外声、环境声、配乐分层 | 人工选定的 voice version、独立 take、音轨清单与混音意图 |
| 剪辑／交付 | 依故事顺序、节奏、持续性与声画关系组装；检查版本及完整性 | 预览版、修订记录、明确标签的 final composition |

岗位交接时传事实、版本、引用角色和待决问题，不把“电影感”“悲伤”这类抽象标签当作已经完成的镜头／表演指令。项目没有提供的位置、光源、台词或动作保持未知，交给真实用户确认。

Higgsfield 的 [Hell Grind 项目 brief](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind) 提供了一组制作流程经验：角色／场景使用“文字描述 + 图像参考”，角色不同角度保持同一可复用描述，场景固定地标、轴线和单一主光逻辑；在剪辑切换后重申人物位置与视线；从角色目标、阻碍与策略推导可见动作，并把必要的反应余韵带入后镜。团队也建议按场景批次制作、先清理瑕疵再统一调色及连续环境声。这些是该项目团队的工作方式，适合在类似问题上试用，不是 Nuomi 字段、MiniMax H3 功能保证或必须套用的统一流程。

## 资产与视觉连续性

- 角色、场景、道具同时维护可复用的文字描述和实际定版图；文字只写来源支持的稳定锚点。衣着、伤痕、持物等剧情状态与角色基础造型分开记录。
- 需要 360 度造型检查时，按需要准备正面、侧面、背面视图；Hell Grind brief 提到过为避免全身正面图出现第二张可读人脸而制作去头躯干图，这是该团队的生成器技巧，不是角色表规范。视图是身份参考，不是模型一定会保持身份的保证；单人多视图仍指向同一 entity 和同一个版本。
- 每张图标明用途：`base_design` 提供身份/造型，`scene` 提供环境锚点，`prop` 提供道具特征，`composition` 提供构图，`style` 提供视觉语言。对原始参考明确“只取身份／空间／材质”等用途；不能暗示它也继承了构图、光线、角度或调色。不要把身份参考误当成最终构图或当前镜头帧。
- 对跨机位或走位关键的场景，建立一个共享 GEO 位置参考，固定有来源的地标、世界方向、轴线、演员路径、机位侧和主光逻辑。剪切后只重申影响当前镜头理解的人物位置与视线；不是让每条 H3 prompt 机械重复整张地图。只有来源或导演提供的距离才写数值；不得从图片比例臆测米数。
- 修正局部衣着或伤痕时，先确认该变化是剧情已发生还是新设计；只更新受影响资产和下游引用，检查冻结版本/hash 后再继续。

## 分镜、空间、摄影与表演

- 一格只画一个明确的 `start`、`end` 或 `selected` 瞬间。动作的发生过程写在镜头表／导演计划，不把多个时间状态塞进静帧。
- 先定世界方向和轴线，再从机位投影出画面左／右。切换景别时复用可辨认地标；跨轴要有明确意图或过渡，不因每格单独编写而意外翻转人物方位。
- 摄影指令说清镜头高度/角度、景别、主体在画面的关系、构图变化和有意的运动。没有来源或导演决定的运镜，不写默认推近、跟拍、摇摄或静止镜头。
- 灯光首先写光源的场内方向、质感、色温感受与补光；换机位时推导人物亮暗面，但不改变剧本没有改变的时间、天气或光源事实。把 3D 工作台代理灯光当作空间参考，不能据此宣称物理灯光或最终画质已验收。
- 表演把情绪转成有因果的可见变化（如下颌绷紧、手停在门把上、视线移向出口），并记录相对上一镜的变化。不要给所有镜头设统一的眨眼、呼吸、抖手频率；独白也不自动变成开口画面。
- 需要稳定人物行为时，先写跨场景稳定的行为基线，再按本场目标、阻碍、当下策略推导具体动作和反应；只有确有剧情延续时才把反应余韵带到后镜。不得把一个角色行为 profile 扩成每镜固定动作。
- 出图／出片后逐项对照轴线、走位、视线、道具状态、光向、机位和表情。判断失败来自导演选择、字段编译还是模型执行，按本地工作流只修影响的节点和派生产物。

## H3 提示指南与真实可用输入

MiniMax-AI 官方公开的 `h3-prompt-writing` 技能把 base 模式写为 `T2VA/I2VA/FL2VA/L2VA`，把全参考写为 `Ref2VA`。它建议：I2VA 从首帧状态发展；FL2VA 描述从首帧到末帧的连续可见变化；全参考用稳定的 `<Subject N>` 表示可复用人物/场景/道具，用 `<Picture N>` 表示真正的关键帧或构图锚点；每镜写清构图、主体、环境与光线、动作状态、摄影机、声音和参考内容出现的时点；总时长要和时间线一致。

当前 Nuomi H3 workflow 不是直接把上述自由文本交给供应商。它使用结构化 director draft、profile 与 shared compiler；本地模式名、帧组合、引用数、时长及 sound 路由均由当前能力端点决定。不要仅凭官方示例就给本地请求增加 `T2VA`、`L2VA`、任意 `Picture` 标签、时间线、原生音轨或未暴露字段。运行 [h3-experience.md](h3-experience.md) 中的 capability → preview → prepare → submit，并检查实际编译出的提示词、引用角色、首末帧与 sound 设置。

官方技能及其参考指南：

- [MiniMax H3 prompt-writing skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)
- [Base-mode prompt guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt)
- [Full-reference prompt guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)

这些内容是提示编写参考，不是本地 provider adapter 的字段规范或成功率保证。

## 公开案例库：学习方法，不把案例当契约

- [SkyNotSilent MiniMax H3 cases](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases) 明确区分带来源的可播放案例与完整公开 Prompt，并提醒不重构或反推未公开 Prompt。先看案例原作者发布内容；只有 Prompt 明确公开时才阅读原文。
- [BeatAPI H3 prompt gallery](https://github.com/BeatAPI/awesome-minimax-h3-prompts) 按故事/电影、动作/奇幻、广告/产品、音乐/表演、Vlog 等整理带来源提示和 WebM 示例。可以借鉴单一选择（例如主体动作如何对应摄影机运动），不可把完整范例移入当前剧本。
- [stimQQ / apimodels prompt library](https://github.com/stimQQ/stunning-minimax-h3-prompts) 当前会重定向到 `apimodels`。该页面将提示、视频、作者来源与 API 代理推广并列；仅把有来源的提示写作当作社区范例，供应商价格、分辨率、引用上限或模型行为声明一律先以官方文档和当前 Nuomi capability 验证。
- [Higgsfield Hell Grind project page](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind) 是用户指定的参考页面。当前公开页面可见内容不足以检查镜头、Prompt 或制作流程；没有实际播放、逐镜查看和核对的记录时，不引用其画面或声称学到了特定方法。

从已公开且适用的案例只提炼一条可迁移的视听选择（如动作顺序、机位变化或环境声层次），记录来源、作者、版本／日期与本地改写点。按局部、一行语义改动逐版记录；若多轮同类修补仍没有改善，重新设计或简化镜头、拆分动作，不要无限重抽。剪辑阶段可先收拾已确认素材中的明显瑕疵，再做一致的整体色彩与环境声连续性处理；只有当当前 editor 能力报告相应操作时才实际执行。不要复制案例台词、完整 Prompt、品牌资产，也不要声称看过没有实际播放的影片。外部示例只说明该创作者发布过这种输入/输出组合，不能证明它对其他提示必然复现。

Hell Grind brief 列出的生产工具包括 Seedance 2.0 视频／语音、SoulCinema 人脸／场景、NanoBanana Pro 或 Seedream 4.5 编辑、GPT Image 2 道具／反向角度。它不是 MiniMax H3 测试，故不移植其模型专属的开场宽景、重复首句、prompt 字数或压力测试次数，也不据此推断本地 H3 支持相同参考和剪辑功能。

## 声音与表演

- 角色音色描述只写稳定身份特征；逐句表演另写意图、情绪、重音、停顿和力度。对白按批准剧本逐字绑定 speaker 与 dialogue ID。
- 只有确需同一参考音色时，按 [voice-and-audio.md](voice-and-audio.md) 导入参考音、真实试听、由用户批准当前候选并固定 adopted voice version；Nuomi 当前路径不提供独立托管声纹训练 endpoint 的保证。
- 默认将独立对白与视频画面分轨；外部配音替换对白时，按实际 separator 能力验证保留的环境声。H3-native audio 仅限当前接口允许并由用户明确选定的段落。不得把二轨 Demucs 输出称为完美干净的环境音分离。
- 说话口型只在用户明确要求且当前视频路线支持时添加。非说话人物不需要逐镜重复“闭嘴”限制。
