# MiniMax H3、案例库与 Higgsfield Cinema Studio：制作工作流证据

研究日期：2026-10-16。本文为实现 Agent 角色与脚本到画布交接提供证据，不是 PRD，也没有执行登录、付费生成或代码修改。

## 结论与证据等级

MiniMax H3 适合将一个短叙事单元描述成带镜头变化、动作路径和原生音频的 4–15 秒视频；H3 的官方方法不是“关键词堆叠”，而是把主体、场景锚点、动作变化、镜头切换、声音与音乐整理成时间线。官方开源的是 H3-Base 生成权重；官方多模态理解/提示词整理 H3-Context-IR 和 2K 再生 H3-Regenerate-2K 仍由托管 API 提供。[MiniMax 的开源说明](https://www.minimax.io/news/minimax-h3-open-source)、[H3 提示词指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

案例库里“有可播放输出”“公开原始 Prompt”“官方可复现”是不同证据。官方案例有请求脚本和输出，可按给定技术条件复跑；社区案例的完整 Prompt 是作者公开文本的来源记录，不等于维护者复现，也不等于所有附件和模型参数都已保存。[案例库 CONTRIBUTING 规则](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CONTRIBUTING.md)明确：只有官方可复现案例标为 verified，人工核对的 X 社区案例保持 false；缺失 Prompt 不可反推。

证据标记：

- **A｜官方复现材料**：MiniMax 官方请求脚本及对应公开输出；API/模型文档用于界定能力与输入限制，不单独视为复现证据。本轮核对了材料，未实际运行生成。
- **B｜来源可追溯的社区 Prompt**：案例库标为作者逐字公开，并链接原作者；仍不是独立复现。
- **C｜厂商产品说明**：Higgsfield 的官方产品/工作流说明，能证明厂商宣称的产品能力，不能证明每次生成都达到该效果。
- **D｜迁移建议**：由上述规范和案例归纳的 Agent/画布结构，属于设计推论。

## 命名与版本边界

官方模型和 API 使用 MiniMax H3 / MiniMax-H3。H3 于 2026-07-31 发布，H3 权重于 2026-08-03 开源。案例仓库为检索覆盖面，将 Hailuo H3、Hailuo 3.0、海螺 H3 也列作常见搜索别名；这不构成 MiniMax 官方对多个独立模型名称的定义。MiniMax 的研究页把 Hailuo 01 和 Hailuo 02称为先前世代，不能把它们的旧功能说明直接套到 H3。[官方发布与版本说明](https://www.minimax.io/blog/minimax-h3)、[官方开源说明](https://www.minimax.io/news/minimax-h3-open-source)、[案例库 README](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases)

API 文档另列 MiniMax-H3-Max，这是快速生成变体：文档给出的范围为 5–15 秒、480P/768P、不支持 2K；常规 MiniMax-H3 为 4–15 秒、768P/2K。H3-Max 与公开权重仓库中的 H3-Base checkpoint 不能混为同一运行路径。[Create Video Generation Task API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)

截至研究日期，案例库 Git 仓库显示 301 stars；直接读取的公开统计快照生成于 2026-10-16，列出 2,680 个案例、726 条完整 Prompt、25 篇教程。README 页面缓存与实时图库在研究期间出现不同数字，因此这些数量只适合作为带日期的项目快照，不是稳定产品规格。[公开统计快照](https://raw.githubusercontent.com/SkyNotSilent/awesome-MiniMax-H3-cases/main/data/project-stats.json)、[仓库 README](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases)

## MiniMax H3 官方方法与实际能力

**输出与输入。** H3 官方说明给出 4–15 秒、24 FPS、32 kHz 立体声，支持多种画幅，默认短边 768 像素；2K 走再生流程。开放的 H3-Base-FL2VA checkpoint 覆盖 T2VA 以及首帧/尾帧路径：零张图可做文生视频，一张图可作首帧或尾帧，两张图可锚定首尾帧；H3-Base-Ref2VA 用于多模态参考输入。官方列出的 Ref2VA 上限为 9 张图、3 段视频、3 段音频，混合文件合计最多 12 个；视频和音频单段 2–15 秒且总长不超过 15 秒，音频必须与图像或视频一起输入。[H3 模型/任务规格](https://www.minimax.io/news/minimax-h3-open-source)、[API 输入组合与限制](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)

这里要区分提示词指南的 I2VA、FL2VA、L2VA 任务名称和 checkpoint 的 FL2VA 家族名：官方写明 FL2VA checkpoint 接受零、一或两张图片，所以它不是“必须同时有首帧和尾帧”。在 API 中，first_frame / last_frame 和 reference_image / reference_video / reference_audio 两种模式互斥，不能把首尾帧与 Ref2VA 参考混在一个请求里。画布应保存每个素材的输入角色，不能只保存一组无类型的附件。[官方任务说明](https://www.minimax.io/news/minimax-h3-open-source)、[API 模式互斥说明](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)

**官方提示词结构。** H3 指南将最终文本拆成三个栏目：integrated_multimodal_description（画面、动作、镜头、说话人、台词与同步的场景内声音）、overall_soundscape（全片环境与动作声音）、non_diegetic_music（角色听不到的配乐）。图像输入的对齐说明先于正文；首帧模式从参考画面的构图、人物、服装、物件和空间关系开始，再写动作开始、连续变化与结果；首尾帧模式写两张图之间如何到达，不要只复述两张静帧。首尾帧任务通常建议单镜头，只有明确要求时才加切镜，并让最后一个镜头到达尾帧。[官方提示词指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

镜头语法要求第二镜头开始使用严格递增、落在片长内的切点时间；切镜应提供新的空间、状态、信息或视点。如果仅需轻微换景别，指南偏向在原镜头里移动摄像机。运镜由类型、幅度、速度组成，写成自然语言动作。对话使用跨镜头稳定的说话人 ID，台词保留用户提供的原文；环境声和非剧情配乐分开描述。这给脚本 Agent 一个可解析的交接格式，而不只是审美偏好。[官方镜头、运镜和音频规则](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

**开放与托管边界。** H3-Context-IR 会理解文本、图像、视频和音频之间的关系，输出结构化的语义增强 Prompt；官方说它可能补足未描述清楚的细节，但实现包含多阶段托管模型与服务，没有开源。Context-IR API 只返回增强后的 Prompt，不生成视频。H3-Regenerate-2K 也未开源；它将符合 H3 768P 规格的视频与原始上下文结合后再生成 2K。MiniMax 同时提供 API，供开发者复现官方工作流效果，但这不代表全部内部处理代码开放。[Context-IR API 文档](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)、[2K 再生 API 文档](https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration)、[官方开源说明](https://www.minimax.io/news/minimax-h3-open-source)

因此应分别保存用户原稿、模型/Agent 改写稿、Context-IR 返回稿和实际生成请求。否则，生成成片里新增的动作或语义无法追溯到谁作出的决策。这是基于官方“可能补足未明确细节”说明的审计建议（D）。

## 三个案例：从可复现到来源公开

### 1. 官方 T2VA《舰桥上的跃迁余震》｜A

MiniMax 的官方 10 秒、16:9 案例从星舰舰桥中广景推近，描述舰队充能后在 4.5 秒切到舰长特写；爆闪和舰桥震动发生时，人物从前镜头延续到特写并产生身体反应。提示词再将低频舰船底噪、充能高音、爆发冲击、环境声回落至安静，与逐渐增强后突然截断的管弦配乐分开安排。它示范“戏剧变化点”同时驱动镜头、人物动作和声音设计，而不仅是换一个景别。[官方 Prompt 和生成结果索引](https://www.minimax.io/news/minimax-h3-open-source)、[官方 768P 本地请求脚本](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/reproducible-768p-t2va-request.sh)

官方文章提供 Context-IR、H3-Base、2K 再生和 API 直出结果的请求/结果链接；官方本地脚本提供使用完整 H3-Context-IR Prompt 调用 H3-Base 的 768P 生成路径。本轮未实际运行该脚本。限制是 Context-IR 与 2K 再生需要托管服务；本地部署还依赖相应推理框架和硬件，不是“一份 Prompt 即可在任意环境复刻”。[官方完整工作流与结果表](https://www.minimax.io/news/minimax-h3-open-source)

### 2. 官方 I2VA《拉面与家庭晚餐》｜A

官方 8 秒示例以一张首帧图为场景锚点：前景是一碗拉面，背景有一家人；模型保持摄像机位置，逐步把焦点从碗和蒸汽移到背景人物。提示词同时写明人物服装与位置、焦点变化、持续上升的蒸汽、碗筷声和较轻的配乐。这个案例把“故事动作”压到一个可执行的摄影动作——拉焦，而由首帧锁定构图和空间关系。[官方 I2VA 示例及输出](https://www.minimax.io/news/minimax-h3-open-source)、[官方 2K I2VA 输入脚本](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/full-2k-i2va-h3-context-ir.sh)

该例是单镜头的图生视频，不证明人物身份能跨多个独立生成镜头稳定保持；可把它看作“一个镜头内动作路径”的官方样例。

### 3. 社区《八镜头奢华护肤广告》｜B

案例库记录该作品为 15 秒、2560×1440、作者标注 MiniMax H3，并保留作者公开的完整 Prompt。Prompt 要求以 Storyboard 作为摄影指导，在八个有明确时间段的部分中保持同一人物、服装、发型、妆容、房间和产品；动线从窗边和镜前，经精华液与滴管的微距、上脸、渐暖光线，收束到产品 Hero Shot 和 CTA。推进、跟手、微距跟随、绕脸和弧拍构成预定镜头路径。[案例文件与逐字 Prompt](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/data/cases.json)、[案例目录](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CATALOG.md)、[作者原帖](https://x.com/Maercihh/status/2083407991114805571)

可迁移的做法是全局连续性约束先写一次，之后每一段只描述人物动作、产品状态、摄影机移动和转场如何变化。需要注意，这个 Prompt 将八个有标题的镜头段落又描述成“不切镜”的连续 Match Cut 路径；若 Agent 需要执行真实剪辑，应把“镜头内运镜”和“剪辑点”拆成不同字段，消除语义冲突。案例库把社区条目标为 verified=false；其 inputTypes 也未单独记录。X 原帖本次无法直接抓取（403），因此此例证明的是案例库保留了一条作者公开 Prompt 的来源记录，不是我独立复核了附件和生成设置。

## 案例仓库能证明什么

README 将案例分成可看成片、只看完整 Prompt、官方可复现例子和教程等入口；贡献规则要求链接原作者帖子、Prompt 必须在原帖/作者回复/官方脚本里完整公开并逐字复制，未公开的 Prompt 记为缺失，禁止从视频反推。项目的 verified=true 仅用于官方可复现案例，社区 X 案例即便通过人工核对仍为 false。适合把它当成可追溯的案例索引和提示词资料库，不应把库中所有长视频当成 H3 单次生成时长，也不应把源码仓库的 MIT 代码许可证扩展到收录的视频、Prompt 或创作者作品。[README 的证据/许可证说明](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases#data-automation-and-trust)、[贡献与核验规则](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CONTRIBUTING.md)

本次对 X 原帖的静态访问返回 403，因此社区案例的来源链条由仓库记录和其来源 URL 支撑；Prompt 文本与原帖是否完全一致、附加图片/音频究竟是哪一个文件，本次不能独立复核。案例数据中的 verified、promptProvenance、inputTypes 应在 Agent 输出中保留，不能扁平化成“已验证的生产流程”。

## Higgsfield Cinema Studio 官方工作流：产品能力与外推边界

截至 2026-10-16，Higgsfield 官方当前介绍的是 Cinema Studio 4.0；2.0/3.0/3.5 的旧教程不能直接当成 4.0 的功能规格。4.0 发布指南描述：单次生成最长 30 秒、最多 50 张参考图、节奏控制（Chaotic / Dynamic / Calm / Single Shot）、30+ 运镜预设、相机类型/镜头/光圈、年代、类型、色彩、灯光与角色情绪控制；团队功能包括可复用的 AI Cast 和 Location、共享 Elements、Project Brief、Canvas Integration，以及对多模型的支持。[Cinema Studio 4.0 官方指南](https://higgsfield.ai/blog/cinema-studio-4-0)、[Cinema Studio 官方产品页](https://higgsfield.ai/cinematic-video-generator)

Higgsfield 的脚本到分镜指南将 Popcorn 放在生成视频之前：从脚本拆成场景与视觉节拍，制作镜头表，绑定角色/地点/道具参考，再生成 4、6 或 8 格序列；Auto 自动分配节拍，Manual 按帧指定。确认分镜后，将相同面板交给 Cinema Studio 4.0，为镜头选择摄影机、镜头、光线和运动。官方页面描述 Popcorn 每次最多 4 张图片参考；Cinema Studio 4.0 则宣传单次生成最多 50 张图。两处是不同阶段、不同工具的限制，不能把“4 张”误写为整个流程上限。[脚本到 AI 分镜/镜头表指南](https://higgsfield.ai/blog/script-to-ai-storyboard-shot-list)、[4.0 生成控制](https://higgsfield.ai/blog/cinema-studio-4-0)

这是一组厂商官方文档对产品能力的描述：镜头、节奏和外观控制作为参数进入生成，而非只依赖提示词；Cinema Studio 是建在可替换视频模型上的导演界面。它说明文档所述的控制项，不说明模型一定能按每个摄影参数精确执行，也不代表 H3 本身具有同一套 UI 控件。[Cinema Studio 产品构建说明](https://higgsfield.ai/blog/how-we-built-cinema-studio)

**成片剪辑边界仍有官方文档差异。** 7 月的短片流程指南写 Supercomputer 能保持镜头清单/顺序并协助组织生成；9 月的动画短片指南明确写 Cinema Studio 生成单条 Shot，不能将多条 Clip 拼成连续电影，需要用单独剪辑器组装，并提到 Genjutsu 做局部修改、Lipsync Studio 做口型同步。[7 月全流程指南](https://higgsfield.ai/blog/ai-short-film-pipeline)、[9 月短片指南](https://higgsfield.ai/blog/ai-animated-short-film-2026)。为产品设计，应把生成、局部修改和最终时间线编辑视为分开的阶段，除非实际当前 UI/团队账户另行确认已有完整 NLE。

**同一官方资料存在规格不一致。** 4.0 发布文章的 FAQ 写最高 1080p，而另一篇官方构建说明列出 720p；后续短片指南按 720p 给价格实例。同时，该短片指南写 Cinema Studio 4.0 使用 Seedance 2.5，而 4.0 发布文章描述的是内置模型加可选外部模型。因产品版本、账户或“所选模型”可能不同，像素规格和默认后端应在当前 UI 按具体模型核对，不能写成固定模型行为。[4.0 发布指南](https://higgsfield.ai/blog/cinema-studio-4-0)、[产品构建说明](https://higgsfield.ai/blog/how-we-built-cinema-studio)、[短片指南](https://higgsfield.ai/blog/ai-animated-short-film-2026)

## 可迁移的 Agent 角色与脚本到画布交接

以下是把官方输入结构、分镜产品流程和已公开案例转成工程数据模型的建议（D），不是已验证的单一厂商流程。

| Agent 角色 | 负责内容 | 应交接的数据 |
| --- | --- | --- |
| 剧本分析 Agent | 将剧本拆为场景和可见节拍；标出角色目标、障碍、策略变化、关键揭示、对话和音频节拍 | Scene ID、beat 顺序、必须表达的因果、镜头时长预算、角色/地点/道具需求 |
| 连续性与资产 Agent | 固定角色身份、服装/湿身/受伤等状态、道具左右手/位置、场景地图与光向；维护每份素材的来源 | 稳定的 CHAR / LOC / PROP / STATE 资产 ID，正侧面/地点图/参考片段/声音参考及其 provenance |
| 导演与分镜 Agent | 选择每个视觉节拍要一镜或切镜，明确该镜对叙事的作用、景别、机位、运镜、动作和结束状态 | Shot/Beat 表：目的、画面主体、可观察动作、镜头/切点、镜头运动类型/幅度/速度、参考素材角色、声音点 |
| 提示词编排 Agent | 将已批准的分镜转成目标模型需要的结构；H3 可采用官方三栏与时间戳，或调用 Context-IR | 原始创意稿、模型增强稿、正式请求文本分版本保存；保留 H3 输入类型、比例、片长、参考角色与任务 ID |
| 制片/连续性复核 Agent | 按叙事单元检查输出是否成片、是否漏关键节拍、角色和场景锚点是否漂移、开尾状态能否接剪 | 输出文件、模型/版本/参数、成功状态、问题标注、可复现 Prompt、最终剪辑顺序与人工确认记录 |

建议画布把项目简报作为根节点，下面连接 Scene / Narrative Group，再连接 Storyboard Frames 和 Shot/Clip 节点；角色、地点、服装状态、道具、音频各自作为有稳定 ID 的资产节点，以引用关系连到镜头，而不是复制粘贴长段提示词。每个 Clip 节点都应保存：原 Prompt、增强 Prompt、模型版本、任务模式、每个参考素材的类型、片长/画幅/分辨率、生成状态、输出与复核意见。这样才能区分“写了要保持一致”和“输入了能提供一致性的信息”。H3 Context-IR 可能增加细节，故改写前后文本也应保留。[H3 Context-IR 能力边界](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)

一份可操作的 Shot 节点可以分为：

- **场景锚点**：地点 ID、光向/时间、屏幕方向、角色与服装状态、关键道具状态。
- **本镜头唯一主要变化**：谁采取了什么可见动作，动作如何开始、推进、落点/反应是什么。
- **摄影设计**：景别/角度、运镜类型/幅度/速度、是否切镜及切点；若只需一个镜头内连续动作，明确“不切镜”。
- **参考绑定**：哪个文件仅用于人物身份，哪个仅用于地点、风格、动作参考或音色；H3 中正确选择首帧/尾帧或 Ref2VA，不混用。
- **声音**：剧情内台词/音乐、环境/动作音效、非剧情配乐，必要时与镜头节拍对齐。
- **出镜状态**：末帧角色方向、手里道具、位置、情绪和光线状态，用于下一镜的连续性检查。

对于 Nuomi 的剧集生产流程，H3 的 4–15 秒规格更适合容纳一个叙事组或其中一小段连贯节拍；组内可以有多个时间化镜头。Canvas 可将相关分镜作为一组先生成参考图，再将组内获批镜头编成视频单元，最后由剧集剪辑/合成阶段按已批准的节奏组装。不能把 H3 一次生成最长 15 秒误写成可以直接覆盖整集，也不应将每个镜头脱离叙事因果独立成任务。[H3 片长规格与多镜头结构](https://www.minimax.io/news/minimax-h3-open-source)、[官方镜头编排规范](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)

## 实现边界与待确认项

1. **身份一致性是输入条件加适度核验，不是 Prompt 保证。** H3 可接收角色参考图，Higgsfield 宣称有身份资产/引用机制；但没有证据证明任何平台对长项目所有镜头都提供确定性身份锁定。应在叙事组/交付单元层面按风险检查关键角色与状态锚点，核验程度遵循现有制作流程，不新增重复或付费质检轮次。
2. **15 秒内的多镜头是短序列，不是完整电影。** H3 API 明确限制生成 4–15 秒，Ref2VA 的参考视频/音频总长也不超过 15 秒；案例库中长于此的内容不能据此推断为 H3 单次原生生成，需验证是否经过拼接、循环、后期或其他模型。
3. **文本层不是生成链路全貌。** H3-Context-IR 和 H3-Regenerate-2K 的内部实现未开源。社区 case file 即使公开完整 Prompt，也未必公开模型权重版本、所有参考文件、参数、重试记录和剪辑工程。
4. **工具文档是有日期的产品承诺。** Cinema Studio 4.0 片长/分辨率/底层模型在其官方网页间存在版本差异；产品账号可用能力也可能随模型或计划变化。实现前应以用户实际账户的当前 Studio 控件核对，而不是把 2.0/3.5 教程直接当作当前规格。
5. **最终剪辑能力未有单一一致说法。** 一份 Higgsfield 指南提到 Supercomputer 组织镜头顺序；另一份更新的指南要求导出后用独立 NLE 组装。若 Agent 负责交付最终成片，应保留明确的 edit/timeline 阶段及外部导出接口。

## 主要一手来源

- MiniMax H3 发布页与公开研究说明：[H3 技术/能力介绍](https://www.minimax.io/blog/minimax-h3)、[H3 开源及官方示例](https://www.minimax.io/news/minimax-h3-open-source)。
- MiniMax 官方实现入口：[H3 模型仓库](https://github.com/MiniMax-AI/MiniMax-H3)、[Video Prompt Writing Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)、[生成 API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)、[Context-IR API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)、[2K Regeneration API](https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration)。
- 官方案例脚本：[可复现 T2VA](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/reproducible-768p-t2va-request.sh)、[I2VA Full 2K 工作流](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/full-2k-i2va-h3-context-ir.sh)、[Ref2VA Full 2K 工作流](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/scripts/readme/full-2k-ref2va-h3-context-ir.sh)。
- MiniMax 社区案例库：[仓库与 README](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases)、[机器可读案例](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/data/cases.json)、[生成统计](https://raw.githubusercontent.com/SkyNotSilent/awesome-MiniMax-H3-cases/main/data/project-stats.json)、[案例核验规则](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CONTRIBUTING.md)。
- Higgsfield 官方产品/工作流页：[Cinema Studio 4.0](https://higgsfield.ai/blog/cinema-studio-4-0)、[当前 Cinema Studio 页面](https://higgsfield.ai/cinematic-video-generator)、[脚本到 Storyboard / Shot List](https://higgsfield.ai/blog/script-to-ai-storyboard-shot-list)、[短片制作与剪辑说明](https://higgsfield.ai/blog/ai-animated-short-film-2026)、[Cinema Studio 构建说明](https://higgsfield.ai/blog/how-we-built-cinema-studio)。
