# 漫剧工作室 Skills：外部工作流参考图

调研日期：2026-10-25（Asia/Shanghai）。本文是已确认工作室 PRD 的参考映射，不是实现或成片验收记录。只阅读公开网页、仓库文档与既有本地研究；未安装或运行第三方代码，未提交生成任务，也未观看或复现案例视频。

核心来源按用户给定的九个来源组去重：LibTV、小云雀、DramaClaw、OpenMontage、MiniMax 官方 H3 prompt-writing Skill、BeatAPI、stimQQ、SkyNotSilent 与 Higgsfield。H3 的 base/ref 指南是同一官方 Skill 下的两份参考文件，不算两个来源；Higgsfield 项目页与官方电影节页合并在一个来源项下，分别标明可见证据。stimQQ 原链接现在重定向到 `github.com/apimodels/stunning-minimax-h3-prompts`，按同一个案例库计。SkyNotSilent 在其他材料重复出现时只列一次。来源页面与仓库会更新，本文不把星数、案例总数或网页宣传语当作稳定能力指标。

## 来源清单与能支持的结论

| 来源 | 直接可复查的位置 | 证据与限制 |
| --- | --- | --- |
| [LibTV Skills](https://github.com/libtv-labs/libtv-skills) | `skills/libtv-skill/SKILL.md`、`scripts/create_session.py`、`scripts/query_session.py`、`scripts/upload_file.py`、`scripts/download_results.py`；此前本地审阅固定到提交 `c609246c1eca69f6bc129bcbb5d64c36734e4a4a` | 技能层公开的是会话创建、上传、增量轮询与结果下载；其指令要求前端 Agent 原样转交用户 prompt，由后端 Agent 编排。没有公开后端剧本 schema、角色目录或画布交接实现，因此只适合作为委派/取回边界参考。 |
| [小云雀 Web 产品手册](https://xyq.jianying.com/tutorials/web-manual) | §1.3：上传/生成剧本、确认概要和项目设定、拆角色/场景、编辑分镜、合成导出 | 官方手册描述了可编辑的短剧顺序：剧本 → 全局角色与场景 → 分集分镜和视频 → 合成导出；支持在分镜中增删镜头、引用资产，以及上一镜尾帧作为下一镜首帧。手册是产品说明，不公开稳定 API、任务状态协议或版本映射。 |
| [DramaClaw](https://github.com/dramaclaw/dramaclaw) | README 的 XiaHua（画布）/XiaJi（剧集流水线）说明；代码路径 `agent-kit/skills/dramaclaw-workflows/SKILL.md`、`schemas/workflow/v1/stable-contract.json`、`src/novelvideo/freezone/workflow_drafts.py`、`workflow_runs.py`；本地旧审阅固定到 `a627206b3919f78769bd5efe4e6b3cd3946eeb93` | README 把结构化剧本、角色/场景/道具/音色资产、分镜/首帧、旁白/合成作为可分解步骤；旧提交的本地代码审阅看到 skill/recipe scope、plan 草稿 revision/digest、task/run/action/node 关联、幂等与恢复字段。README 当前声明和旧提交源码不是同一版本的端到端运行证明。Nuomi 的 Freezone 路径与它有重合，不能把同名模块直接当作 Nuomi 缺失能力或复制实现。 |
| [OpenMontage](https://github.com/calesthio/OpenMontage) | `skills/pipelines/cinematic/idea-director.md`、`skills/pipelines/cinematic/compose-director.md`、`pipeline_defs/character-animation.yaml` | README 描述 `research → proposal → script → scene_plan → assets → edit → compose`，每阶段有独立 director skill；动画 manifest 把输入产物、输出产物、工具、审阅关注点和成功条件并列。适合借鉴 skill 的合同写法。仓库还要求 checkpoint 与阶段 approval，和 Nuomi 的默认体验不同；其工具栈/产物校验未在本任务安装或运行。 |
| [MiniMax H3 官方 prompt-writing Skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing) | 官方 [`SKILL.md`](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md)、[`base-en.txt`](https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing/references/base-en.txt)、[`ref-en.txt`](https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing/references/ref-en.txt) | base 模式是三字段：`integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`；ref 模式是六字段：`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。ref 中 `<Subject N>` 是可复用的可见身份/内容，`<Picture N>` 是实际帧或镜头规划锚点；身份图若不承担镜头参考作用，只写在 Subject 的来源说明里。base 规定首镜无切点时间，后续 cut times 按目标视频从头累计并严格递增；I2VA 从首帧锚点持续发展，FL2VA 描述首态到尾态之间连续可见的变化。Skill 还列有 T2VA/I2VA/FL2VA/L2VA/Ref2VA，但这是写作格式指导，不证明 Nuomi route 支持所有模式；实际操作以本地 capabilities 和 shared compiler 为准。 |
| [BeatAPI H3 prompts](https://github.com/BeatAPI/awesome-minimax-h3-prompts) | README 示例 14 “Quiet live-action dialogue with voice reference” 与示例 17 “Fleeing toward the wall” | 17 把湿石走廊左右地标、摄影机轴线、人物路径与项链/宝石的持有和位置连续写在一起；14 用不同图分别说明人物身份、亲密姿态/夜景光色，并把女声对白的音量、距离、环境声和配乐分开描述。属于公开 prompt 的写法样本；本次没有核验生成结果，不能证明模型会精确遵循。 |
| [stimQQ H3 prompt library](https://github.com/stimQQ/stunning-minimax-h3-prompts) | README “Two kinds of prompt” 与 `prompts/` 目录；打开旧地址时重定向至 `apimodels/stunning-minimax-h3-prompts` | 维护者区分作者公开且带原帖出处的 prompt 与从少量抽帧重建的 prompt；后者描述可见结果，不能还原原作者的负面约束、准确对白或参考图工作流，必须标成 reconstructed。它不是创作者原始意图；原始文本的权利也不因仓库代码许可证而改变。 |
| [SkyNotSilent H3 cases](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases) | README、`CONTRIBUTING.md`、`data/cases.json` | README 明确区分完整逐字公开 prompt 与未公开 prompt，并声明不重建、不改写、不反推缺失 prompt。适合做“公开原文/未公开/来源待核”分类；不根据视频或案例标题补写 prompt。仓库持续更新，本次未逐条审阅全部案例。 |
| [Higgsfield Hell Grind 项目与官方电影节页面](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind)、[Global Film Festival 页面](https://higgsfield.ai/contests/higgsfield-global-film-festival?tab=timeline) | Project Brief、`CINEDANCE V4 — Seedance2.0 Prompt Director System` 附件、项目资产树及电影节 timeline | 浏览器已读到 Project Brief、附件核心段落和项目资产结构；项目页显示 Cinema Studio、Seedance 2.0 相关工作流，并按 `Assets`、`Regenerations`、`1. COLD OPEN` 及多个 Scene/镜头组组织素材。可借鉴场景分组、可复用素材与单独再生成的组织方式。电影节页提供官方梗概。没有观看 95:59 全片，也没有逐项审看生成素材或验证输出质量；不能将项目称为 H3 案例。 |

## 按制作阶段映射

以下是具体吸收建议，不要求按九个外部工作流重建一套新流程。已确认产品边界以 [2026-10-25 主 PRD](../superpowers/specs/2026-10-25-agent-studio-prd-discussion.md) 为准：Codex 调用 Skills，画布保留可编辑成果；默认完整分镜审阅后再推进视频与声音。OpenMontage 的逐阶段审批和小云雀的产品步骤都不能覆盖这条边界。

| 阶段 | 可借鉴的结构与例子 | 不照搬的部分 | Nuomi 落点与可验证目标 |
| --- | --- | --- | --- |
| 立意 | OpenMontage `idea-director.md` 先辨明素材现实（仅素材、静帧主导、需要生成素材等），再写情绪弧线、交付形态、锚点素材、音乐策略、画幅计划与权利约束；并明确作品承诺是否依赖动态画面。 | 不把 `motion_required` 等 OpenMontage 元数据硬塞成 Nuomi schema；它的“cinematic”项目不等于短剧立意；小说创作无需默认联网研究。 | 放进现有 [script-master](../../skills/script-master/SKILL.md) 的创意起步和 [Nuomi 制作入口](../../skills/nuomi-canvas-production/SKILL.md)：区分用户锁定事实、待选方向、Codex 提案；输出故事目标与期望情绪，再交剧本。校验提案不把未确认内容写成用户事实。 |
| 剧本 | 小云雀手册先给概要/全局设定，再整理剧集正文；DramaClaw 把 manuscript、episode/beat 规划与 script 作为可分解阶段；OpenMontage 将 screenplay/script 当成显式阶段产物。 | 不沿用小云雀强制概要确认交互，也不把 DramaClaw 的流水线节点直接变成用户必须操作的审批队列；LibTV 原样传 prompt 是它的后端代理架构，不适用于 Codex 负责创作的 Nuomi Skill。 | 落在 [creation.md](../../skills/nuomi-canvas-production/references/creation.md) 与 `script-master`：保留唯一正本、原文/改编/新写内容的来源边界和剧本到资产/镜头的语义交接。检查对白、因果、角色事实和集尾状态均能回溯到用户材料或明确标为创作提案。 |
| 角色 | 小云雀按全剧管理角色和不同时空造型；OpenMontage animation manifest 给角色设计写清角色作用、剪影、情绪范围、动作清单和风格锚点；H3 `Subject` 语义适合稳定复用可见身份。 | 不把三面设定图、rig 或某一种风格当成每个项目的硬门槛；参考图不会自动保证跨镜身份一致。 | 落在 [asset-design.md](../../skills/nuomi-canvas-production/references/roles/asset-design.md)：继续用剧本逐字引用支持 facts，角色变体只表达差异；在 H3 准备阶段将身份图标为 Subject 来源，只有实际承担镜头/帧作用的图才建 Picture。检查 identity、造型变体和本镜剧情状态不互相覆盖。 |
| 场景 | OpenMontage 的 scene plan 把角色、动作、摄影/构图、背景和效果落到场次，并映射所需资产；小云雀手册支持全剧场景资料复用；BeatAPI 17 用同一走廊地标描述屏幕左右和连续动作。 | 不从单张全景图声称恢复精确几何；不默认每场都要地图、固定米数或复杂场景 brief。3D 示例不证明 Nuomi 运行时有同样空间精度。 | 落在 asset-design 与 [cinematic-direction.md](../../skills/nuomi-canvas-production/references/cinematic-direction.md)：仅对已知的地标、机位侧、轴线、灯光逻辑和资产引用建立共享空间事实；未知位置保持未知。目标是场景事实能被分镜与 H3 准备复用，且不把 360 旋转称为空间平移。 |
| 分镜 | 小云雀允许逐镜增删/修改并引用角色和场景，说明分镜是可编辑交接面；前镜尾帧可作为后镜首帧，另可附道具、光线、站位参考；OpenMontage 将 scene plan 与剪辑/动画计划作为不同中间产物。 | 不要求所有短剧先做预演或一次生成整组媒体；“最多引用数”等厂商功能限制不能变成 Nuomi 的固定要求。图像宫格也不能替代可编辑分镜表。 | 落在 [storyboard-direction.md](../../skills/nuomi-canvas-production/references/storyboard-direction.md) 与 `creation.md`：分镜表承载动作/对白/时长，格图只定一个可见时刻；表格和宫格保持镜头 ID、次序、状态一致。目标是已批准的整套分镜可审阅，且表、图和来源引用对应。 |
| 站位 / 灯光 / 摄影 blocking | BeatAPI 17 的走廊例把同场地标、人物移动路径、道具状态和相机在轴线哪一侧写成一段连续事实；示例 14 把人物身份图、姿态/光色参考及声音空间分开。H3 full-reference 的 Subject/Picture 语义可防止把资产身份图误当镜头关键帧。 | 不复制示例中的剧情、具体距离、灯光颜色、表演节拍；不把 prompt 细节误当 3D 编辑服务实际完成；不凭示例宣称“严格不越轴”可被模型保证。 | 落在 [storyboard-direction.md](../../skills/nuomi-canvas-production/references/storyboard-direction.md) 和 `cinematic-direction.md`：先有空间、人物/道具初态与动作终态，再派生机位、屏幕方向和光线。缺少世界坐标或可移动机位能力时，只交付 blocking 方案或示意截图，不标为真实空间解算完成。 |
| H3 视频准备与生成 | MiniMax 官方 Skill 将 base 写成三字段，将 full-reference 写成六字段；`Subject` 与 `Picture` 区分“可复用身份/内容”与“实际镜头参考/构图锚点”。首镜不标 cut time，后续时间从目标片段起点累计并递增；FL2VA 需要描述从首帧到末帧的连续可见状态变化。 | 不把 base 三字段与 ref 六字段混成一个 schema；不把累积 cut time 当成每镜各自从 0 开始的相对时长；不从 Skill 列举的模式、公开 prompt 或作品页面推断当前 Nuomi route、参考上限或供应商能力。 | 落在 [h3-experience.md](../../skills/nuomi-canvas-production/references/h3-experience.md) 与 [cinematic-direction.md](../../skills/nuomi-canvas-production/references/cinematic-direction.md)：先读当前 capabilities，再把受支持的结构化导演输入映射到相应 compiler；检查 cut 时间递增、人物/道具状态连续、镜头末态能承接下一镜初态。模型回包成功不等于画面连续通过。 |
| 音色与配音 | BeatAPI 14 分开写人物身份、参考姿态/灯光、对白声音的距离/音量/音色情绪、环境声和音乐；H3 ref 的 `<Audio N>` 明确区分原音复制与仅参考音色，speaker `(S1...)` 跨段维持稳定 ID。 | 不把案例中的日语对白、声线和空间混音照搬；不将 H3 原生音频或 `<Audio>` 标记当作独立配音/克隆 API 能力；不要求口型同步。 | 落在 [professional-references.md](../../skills/nuomi-canvas-production/references/professional-references.md) 和 `nuomi-canvas-production/SKILL.md`：角色身份声线与单句表演分开；剧本对白逐字保留，默认独立配音，视频内嵌对白按既定音轨策略静音/替换。目标是对白归属、实际时长、声线引用和环境声/配乐路由都可检查。 |
| 合成与成片检查 | 小云雀手册明确把多镜视频合成作为交付步骤；OpenMontage compose skill 先选真实 render runtime，输出 timeline/audiomap/render report，并做流、尺寸、时长和完整解码检查；character-animation manifest 也将 render report、final review 与角色 QA 分列。 | 不照搬 Remotion/HyperFrames/FFmpeg 选型、额外成本审批或独立 QC 门禁；技术检查不能替代成片观看，导出成功不代表叙事/视听验收完成。 | 落在现有 `nuomi-canvas-production/SKILL.md` 的 audio/composition 步骤：按时间线顺序，每段和每条独立音轨只加入一次；检查缺片、重复音轨、静音对白、意外空隙和总时长，并播放连续成片。最后按 PRD 做用户成片审阅，不新增多层审批。 |

## H3 参考与案例的使用边界

- MiniMax 官方 Skill 的 base 指南使用三字段 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`；ref 指南使用六字段 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。这些是两种不同的提示词写作格式，不能压成 Nuomi 的单一运行 schema。
- ref 指南中 `<Subject N>` 表示可复用的可见身份/内容，`<Picture N>` 表示实际帧、关键帧或分镜规划锚点；`<Video N>` 表示剪辑/续接/时序来源，`<Audio N>` 表示复制音频或引用声音特征。图片若只描述角色外观，应放在 Subject 的来源说明里，不应另列为镜头 Picture。
- 官方 base 指南要求第一镜不写 cut time，后续镜头用目标片段时间线上严格递增的切点；对 FL2VA，描述首态、可见中间变化直到尾态的连续路径。Nuomi 的准备检查应校验累计时间、人物/道具状态连续，以及镜头终态承接下一镜初态。这是本地 Skill 的可验证编译规则，不意味着提示词可以保证模型输出严格满足连续性。
- BeatAPI 的示例 17 可作为“空间轴线 + 持物状态 + 行动因果”练习材料；示例 14 可作为“身份图、姿态/灯光图、对白/环境声”分工材料。两条都是作者公开 prompt 的写法，不是经过本项目播放或生成复现的效果证据。
- stimQQ 当前同时收录原作者公开 prompt 与 reconstructed prompt。下游索引必须保存 provenance 标签、作者/原帖链接和原始出处状态；重建文本只能作为维护者对画面的描述示例，不得伪装成作者原 prompt。SkyNotSilent 的规则更严格：没有原帖公开全文时保留缺失状态，不从视频猜写。

## Higgsfield 页面状态

Higgsfield 是上方九个来源组之一，并非补充来源。2026-10-25 的初始文本抓取只取到 [Hell Grind 项目页](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind) 的导航和页面外壳；随后通过浏览器进入项目，读到完整 Project Brief，并看到项目资产树。页面显示播放器标注 95:59、Powered by Cinema Studio、Generations 115446；文件夹计数显示 `Assets` 8986、`Regenerations` 14593、`1. COLD OPEN` 1746，另可见 Scene 13–74 和如 `69B.15` 的镜头组。这些是页面呈现的项目组织信息，未逐个检查生成素材，也没有播放审看 95:59 全片，因此不构成成片质量验证。

Project Brief 全文包含一个 12 秒、三人训练房场景示例，以及 scene context、refs、location map、first frame、format、optics、camera、timing、physics、light、audio、acting、style、quality、positive constraints 共 15 个提示分区。Brief 强调可复用的资产描述与图像、角色表/中性设定图、场景地标和轴线、参考图“继承/不继承”边界、行为目标/阻碍/策略转化为可见行动和反应，以及切后重述站位/视线。还描述了按场景批量组织、逆向补拍规划、先清理缺陷再做色彩/氛围处理和长时间失败后简化或拆分的做法。这些可作为制作组织和连续性检查的参考；画面示例是该项目的说明文本，不是我们验证过的渲染结果。

项目附件 `CINEDANCE V4 — Seedance2.0 Prompt Director System` 可读到 Deconstruct/Diagnose 等提示导演方法、避免 stale tags 与 context leakage 的要求，以及角色数量、首帧、站位/视线、地标、机位光学、物理和对白时序 QA 项。它建议使用简短且局部的负面约束，避免压过正向要求。可借鉴这些检查维度作为镜头简报的审阅清单；这些内容属于附件作者面向 Seedance 2.0 的制作建议，不是 H3 官方规则，也不代表已通过成片测试。

Brief 中提及 Seedance 2.0 用于视频/语音、SoulCinema 用于人脸/地点、NanoBananaPro 与 Seedream 4.5 用于编辑、GPTImage2 用于道具/反角素材。该工具分工以及“第一秒宽镜头”“重复首句”“10 个压力点”“3–4K prompt”等具体供应商建议只属于此 Brief 的语境，不应移植为 Nuomi 或 MiniMax H3 的运行能力、提示词规则或验收标准。项目材料没有把 Hell Grind 的模型标为 H3；不得把它归为 H3 案例。

[Global Film Festival 页面](https://higgsfield.ai/contests/higgsfield-global-film-festival?tab=timeline)可读到官方故事梗概。对项目页和电影节页的观察仅支持上述页面内容、项目组织方式和梗概；不从项目名推断制作版本、实际方法或质量。

本地旧研究里有归于 Hell Grind 的生产手册结论，指向另一份第三方 GitHub Skill（`OSideMedia/higgsfield-ai-prompt-skill`）；它与以上 Higgsfield 官方项目/节展页面是不同来源，不能借官方页面状态为那些结论背书。可继续查看 [2026-10-19 外部参考报告](2026-10-19-professional-agent-external-references.md) 中各条来源与限制。

## 对新 Skills 的落地约束

现有主 Skill 已有从创意/剧本、资产、分镜、H3、配音到合成的分工；`script-master` 已负责完整剧本创作，`nuomi-canvas-production` 已覆盖生产交接和能力检查。因此阶段 Skill 应重用这些本地规则，避免复制第二套剧本 schema、prompt 合同或任务状态机。OpenMontage 可借用的是“触发条件 → 输入与阶段方法 → 现有工具 → 结构化交付 → 自检/恢复”的说明结构；DramaClaw 可借用 skill scope 与 run/provenance 的显式边界；LibTV 只作为外部任务适配模式。最终每条技能指令都要落到已有画布节点、共享 compiler、现有服务或实际可执行的 Codex 方法，并明确无法执行时的诚实交付状态。

来源证据只支持形成上述设计建议，不证明新 Skills 已创建、端到端工作流已跑通、3D 调度已达到真实几何精度，或 H3/配音/合成结果已通过用户验收。
