# 专业智能体外部参考调研（2026-10-19）

调研目的：为 A（资产）、B（分镜表与执行导演）、C（H3 与配音）、D（合成与配乐）四份规格补齐“专业岗位”的外部依据。只吸收可迁移的方法，不搬运具体剧情、提示词或素材。

## 1. 来源与可信度

| 来源 | 类型 | 关注度 | 本次取值 |
| --- | --- | --- | --- |
| [OpenMontage](https://github.com/calesthio/OpenMontage) | 开源 Agent 编排的视频生产系统 | 约 69.8k star | pipeline manifest + stage skill + checkpoint + 预算治理 |
| [DramaClaw](https://github.com/dramaclaw/dramaclaw) | 开源短剧生产引擎 + Agent Kit | 约 6.3k star | 跨 Agent Skill/MCP、本地授权与审批、画布写入链 |
| [Higgsfield Hell Grind 生产手册](https://github.com/OSideMedia/higgsfield-ai-prompt-skill/blob/main/skills/higgsfield-seedance/HELL-GRIND.md) | 95 分钟 AI 长片的生产系统开源摘要 | 约 764 star（仓库） | 资产=文字+图、GEO 空间图、台词构建、表演层、迭代纪律 |
| [LibTV 视频 Agent](https://github.com/PomeloR611/libtv-video-agent) | 分镜→成片的 Agent 知识包（30 镜实战） | 约 59 star（垂直） | 九宫格→clean→单镜 image2video→ffmpeg；prompt 铁律库 |
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | 开源 TTS（含 VoiceDesign / Clone） | 约 13.9k star | 音色设计 instruct、设计后再克隆的官方路径 |
| [IndexTTS](https://github.com/index-tts/index-tts) | 开源零样本 TTS | 约 24.6k star | 身份优先克隆；语速/停顿整形，强情绪权重会损相似度 |
| [Seko（商汤）](https://www.sensetime.com/cn/products/seko) | 商业多模态短片 Agent | — | 意图→策划→成片；角色一致性、自然语言改分镜、模型路由 |
| [小云雀（字节）](https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html) | 商业短剧 Agent（Seedance 2.0） | — | 长剧本解析、角色全生命周期形象、按集按场并行、可编辑分镜 |

说明：star 数为调研当日 GitHub API 读取值；商业产品只取公开介绍与实测报道，不把营销表述当技术事实。

## 2. 十条可执行结论

1. **资产是“文字 + 图”成对存在**，文字是逐字复用的描述符，图是锚点；两者缺一都会漂移（Hell Grind）。
2. **角色定版图只需要一张可读的脸**：脸部特写 + 正面全身（去掉头部）+ 背面全身；中性灰底、平光、不烘焙电影感（Hell Grind）。
3. **点改（衣服、伤、血）用蒙版叠回原图**，不整图二次过模型；二次全图会毁纹理并让表情变“塑料”（Hell Grind）。
4. **场景定版用 3/4 视角**，留一个锚点物（柱子、灯、沙发），全场只允许一套光逻辑（Hell Grind）。
5. **每个场景写一份 GEO 空间图**：只有地标、左右、机位侧、不可越过的轴线，没有人物、没有动作；逐镜原样复用（Hell Grind）。
6. **屏幕左右只从机位说**；“在角色左边”模型不认（Hell Grind）。
7. **台词结构固定**：声音与情绪 → 引号台词 → 物理动作 → 面部反应；台词只出现在音频段（Hell Grind）。
8. **表演用肌肉与身体写**，不用“悲伤/愤怒”；静止写成“被压住的张力”，不是冻结（Hell Grind）。
9. **音色在前期锁定为描述符**（音域、语速、口音、说话方式），每句逐字复用；先设计再克隆是官方推荐路径（Hell Grind + Qwen3-TTS）。
10. **迭代是外科手术**：一次只改一处，其余逐字不动；同一镜改 10–15 次仍不成立，问题在镜头设计而不是措辞（Hell Grind）。

## 3. 逐来源要点

### 3.1 OpenMontage：Agent 即控制平面

- 没有运行时 Python 编排器，Agent 读 pipeline manifest（YAML），按阶段读 stage-director skill、调 Python 工具、写 checkpoint（JSON）、自审、过多道人工审批。
- 工具契约统一（base_tool + registry），成本治理是 estimate → reserve → reconcile，预算硬约束。
- 对 Nuomi 的启示：我们的 `canvas capabilities` + `canvas patch` + progress 停点已同构；缺的是**阶段技能与检查表的显式化**，以及**预算前置估算**。

### 3.2 DramaClaw：跨 Agent 的 Skill + MCP 边界

- Agent Kit 不复制业务实现：第三方 Agent 只拿到 Workflow Skill、Recipe 编译器与只读校验；所有项目、任务、画布写入仍由本机 CE API 执行。
- 本地免 Token 仅限回环地址；需要审批时由浏览器页面展示并执行。
- 对 Nuomi 的启示：与 A3 决策一致——方法论在 skill，执行与校验在后端；确认必须由用户权威完成，Agent 不能自签。

### 3.3 Hell Grind：长片生产手册（本次最重要的来源）

**资产**：文字描述符逐字段复用；角色定版图三面板；场景 3/4；道具与几何输入同属资产；参考图必须声明“取什么、不取什么”，场景参考要显式禁止继承构图/角度/调色。

**GEO 空间图**（每场景一份，逐镜原样粘贴）：
```
GEO SPATIAL LAYOUT (locked across every shot — pure spatial map):
— PLATFORM = ...（地标）
— ALTAR: MID-RIGHT relative to the platform.
— RITUAL CENTER: CENTER-LEFT, ~3 m from the altar.
— 180° AXIS: camera ALWAYS stays on the ... side — it NEVER crosses the line.
— BACK-LIGHTING: ... from BEHIND the platform, rim-lighting silhouettes.
```
规则：GEO 只有地图，不含人物与动作；左右只从机位描述；位置用地标 + 米数；每场第一秒一个宽景锁定站位。

**角色数量头**：`EXACT 3 CHARACTERS — NO DUPLICATES: ...`；计数类物件用正向锁 + 明确的 “NEVER a second one”。

**台词与声音**：台词只在音频段；未说话角色要写“嘴部静止”；跨接缝把上一段结尾台词喂进下一段开头；混音写明“人声近、环境声在其下、说话时环境声下压”。

**表演**：情绪用肌肉工作写；`INNER (unspoken)` 内心独白不进声音；分段眨眼；每 1–2 秒一个微事件；反应先于对方台词结束；情绪不会瞬间关闭；手上有活。

**迭代纪律**：批次按场景推进；每次只改一行；全量记录 prompt 版本与判定；10–15 次不成立就改镜头结构。

### 3.4 LibTV：分镜到成片的五段管线

- 定调（锁质感词）→ 定版资产 → 黑白九宫格 → clean 喂片版 → 单镜 image2video → ffmpeg 终剪。
- 铁律：模型对参考图忠实、对文字健忘；负面指令弱，排除元素用“正面穷举”；参考图分工必须显式声明；风格/色彩由定版图 + 质感词承担，构图/表演由九宫格承担。
- 九宫格格内零文字，格号/箭头是标注层，必须声明忽略；否则视频会照抄。
- 对 Nuomi 的启示：B 的 panel_spec 本质上是“用文字承担九宫格的分工”，因此必须把“标注层/画面层”分开，且排除元素用正向描述。

### 3.5 Seko 与 小云雀：产品形态参考

- Seko：意图理解 → 智能策划（剧本、角色、镜头、风格）→ 成片（画面、配音配乐、剪辑）；自然语言改分镜；角色一致性；多模型路由。
- 小云雀：整本剧本解析、角色全生命周期形象、按集按场并行、分镜与角色可编辑；实测中多人同框仍会人物混淆，需要手动改分镜提示词。
- 对 Nuomi 的启示：①多人同框是行业共性难点，B 的 panel_spec 必须显式写“人数头 + 每人位置”，C 的段规划要限制单段同框人数；②“可编辑分镜”是刚需，验证了 B 的“表是编辑面”。

### 3.6 Qwen3-TTS：音色设计与克隆

- `generate_voice_design(text, language, instruct)`，`instruct` 是自然语言描述，官方示例同时包含**性别、年龄、音域、当前状态**：
  `"Male, 17 years old, tenor range, gaining confidence - deeper breath support now, though vowels still tighten when nervous"`。
- 官方推荐 **Voice Design → Clone**：先用设计模型产出短参考音，再用 `create_voice_clone_prompt` 建可复用克隆提示，后续多句复用，不必每次重算特征。
- 克隆需要 `ref_audio` + `ref_text`；只用说话人向量（x_vector_only）质量会下降。

**给 Qwen3 音色设计的提示词模板（建议写进 C 规格）**：
```
{性别}，{年龄}岁，{音域/音高}，{音色质感}，{共鸣}，{咬字}，{语速}，{口音/地域}，
情绪基线：{基线}；本剧状态：{随剧情变化的限制}。避免：{负向，如播音腔/过度甜美/气声}。
```
英文（与官方示例同构）：
```
{Gender}, {age} years old, {register} range, {texture} timbre, {resonance} resonance, 
{articulation} articulation, {pace} pacing, {accent} accent; emotional baseline: {baseline}; 
in this story: {state constraint}. Avoid: {negatives}.
```

### 3.7 IndexTTS2：身份优先的克隆

- 官方推理文档明确：**身份优先时不要加显式情绪控制**，用 `speakingRate` 与 `maxInternalPauseDuration` 做节奏整形；高情绪权重会降低说话人相似度。
- 长文本按句切分再合并，段间插入静音（默认 0.2s）。
- 对 Nuomi 的启示：C 的配音必须拆成“身份（参考音 + 克隆提示）”与“表演（语速/停顿/重音）”两层；场景情绪不能通过克隆权重表达。

## 4. 映射到四份规格

| 外部规则 | 落到哪 | 具体改动 |
| --- | --- | --- |
| 资产=文字+图；描述符逐字复用 | A | brief 的 `design` 增加“逐字描述符”产物；编译结果固定不缩写 |
| 角色三面板、只有一张可读脸 | A | 角色定版图默认面板与可选“正面去头”变体 |
| 点改用蒙版 | A | 变体/点改规则：不整图二次过模型 |
| 场景 3/4 + 锚点 + 单一光逻辑 | A | 场景 brief 的 `space` 必填锚点与光逻辑 |
| 参考图角色声明与禁止继承 | A/B | `reference_roles`：每张参考图“取什么、不取什么” |
| GEO 空间图 | B | 每场景一份 `geo_layout`，逐镜编译进 panel_spec，不含人物 |
| 屏幕左右只从机位说 | B | panel_spec 的 `axis` 段禁用“角色左/右”表述 |
| 人数头与计数锁 | B/C | panel_spec 首行人数；单段同框人数上限 |
| 台词结构、嘴部静止、跨接缝 | C | 段提示词的音频段与台词构建规则 |
| 音色描述符逐字复用 | C | `VoiceSpec` 编译为固定描述符，逐句复用 |
| Qwen 设计 → 克隆 | C | 配音流程改为“设计参考音 → 克隆提示 → 多句复用” |
| IndexTTS2 身份优先 | C | 表演只调语速/停顿，不用情绪权重 |
| SFX only，音乐留后期 | D | H3 段默认只要 SFX；配乐在 D 阶段独立 |
| 迭代外科手术 + 10–15 规则 | C/D | 重试只改一处；超限改镜头结构，不无限重抽 |

## 5. 未验证与风险

- Hell Grind 的若干数字（10–15 次、3000–4000 词）出自单一长片项目，属于经验值，不作为硬门槛；我们只用其“结构性”结论。
- Seko / 小云雀的公开材料含营销表述，只取流程与已知共性难点，不当作能力承诺。
- Qwen3-TTS 与 IndexTTS2 的官方示例是通用场景；中文短剧的情绪表达仍需本项目试听验收。
- 角色定版“正面去头”与用户现有提示词（正面/侧面/背面 + 脸部特写）不同：作为**可选变体**提供，不默认替换用户方案。

## 6. 引用

- OpenMontage: https://github.com/calesthio/OpenMontage ; https://github.com/calesthio/OpenMontage/blob/main/docs/ARCHITECTURE.md ; https://github.com/calesthio/OpenMontage/blob/main/AGENT_GUIDE.md
- DramaClaw: https://github.com/dramaclaw/dramaclaw ; https://github.com/dramaclaw/dramaclaw/blob/main/agent-kit/README.md
- Hell Grind: https://github.com/OSideMedia/higgsfield-ai-prompt-skill/blob/main/skills/higgsfield-seedance/HELL-GRIND.md
- LibTV: https://github.com/PomeloR611/libtv-video-agent
- Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS
- IndexTTS: https://github.com/index-tts/index-tts ; https://github.com/soniqo/speech-swift/blob/main/docs/inference/indextts2.md
- Seko: https://www.sensetime.com/cn/products/seko
- 小云雀: https://www.stdaily.com/web/gdxw/2026-03/20/content_488925.html ; https://m.jiemian.com/article/14150816.html
