# 漫剧 H3 导演层：调研结论与改造方案（2026-10-21）

## 0. 结论

这次失败不是 H3 格式问题，也不是模型能力问题，而是**我们的链路没有导演层**：

1. 分镜表把镜头压成了静态面板元数据（count/shot/axis/blocking/lighting），丢掉了“这镜的戏眼、前 2 秒、表演节拍、感染者运动、运镜动机”。
2. 视频 draft 直接拿这份静态文本当 segment prompt，H3 优化器只能把“静态站位”翻译成英文，无法补出不存在的动作设计。
3. 宫格每一格独立生成，没有共享的 GEO 空间图；角色和场景在格与格之间重新构图，导致场景混乱、位置漂移。
4. 感染者只有“苍白、失焦、迟缓”三个形容词，没有重量、拖步、延迟、非同步、惯性，所以看起来机械。

要修的是**在 H3 之前增加一层可执行的导演 brief**，并且让分镜表、宫格 prompt、H3 draft 共用同一份事实。

## 1. 调研来源

- MiniMax H3 官方 base 指南（T2VA/I2VA/FL2VA/L2VA）：镜头、运镜、说话人、声景、配乐的硬格式。
  https://github.com/ye4wzp/minimax-h3-prompt-skill/blob/main/references/official-base-en.md
- MiniMax H3 官方全参考指南（Ref2VA）：六段式、subject_definitions、retention、350–500 词。
  https://github.com/ye4wzp/minimax-h3-prompt-skill/blob/main/references/official-ref-en.md
- MiniMax H3 prompt skill 的决策补齐与翻车表：时长/镜头数、前 2 秒、风格锚定、三层声音、台词容量、失败症状。
  https://github.com/ye4wzp/minimax-h3-prompt-skill/blob/main/SKILL.md
  https://github.com/ye4wzp/minimax-h3-prompt-skill/blob/main/references/rewriter-playbook.md
- Higgsfield Hell Grind 的外景拍摄手册：GEO SPATIAL LAYOUT、第一秒宽景、physics not adjectives、micro-life、reaction before line、iteration loop。
  https://github.com/OSideMedia/higgsfield-ai-prompt-skill/blob/main/skills/higgsfield-seedance/HELL-GRIND.md
- 本仓库既有规则：professional-references、panel_spec、director plan models。
  skills/nuomi-canvas-production/references/professional-references.md
  src/novelvideo/director_plan/panel_spec.py
  src/novelvideo/director_plan/models.py

## 2. 专业 H3 拍摄的十条硬规则

### 2.1 GEO 空间图，每场一份，逐镜原样复用

Hell Grind 的结论：模型不记得上一镜谁站哪，必须给它一张只含地标、左右、机位侧、轴线的平面图；不含人物与动作。每次切镜后重新点名谁在哪、看哪。

我们的 group.geo_layout 已有字段，但没有进入宫格 cell prompt，也没有进入 H3 draft。

### 2.2 第一秒是宽景，不是空镜

每场第一秒用宽景固定位置，不安排台词和动作；人物必须在画内，只是不做动作。这样后续切镜才不会换位。

我们现在的首帧是宫格单格裁图，既不是宽景，也没有位置锁定。

### 2.3 前 2 秒放最强视觉信息

H3 skill 明确：禁止用“缓缓推进/镜头慢慢移动”开场；静到动的突变比全程运动更抓人。

我们的旧 prompt 没有首 2 秒设计，H3 只能按静态描述平均分配注意力。

### 2.4 表演写肌肉、呼吸、视线、重心，不写情绪词

Hell Grind：愤怒/悲伤/震惊会让模型自由发挥；要写下颌收紧、鼻翼一动、呼吸变浅、手指发颤。静止要写成“被压住的张力”，不是“所有人不动”。

我们的 performance 只有“起始状态”，没有节拍，也没有微事件。

### 2.5 每 1–2 秒一个可见微事件

呼吸、眨眼、视线移动、重心转移；固定凝视会让脸死掉。台词镜要让反应先于对方台词结束。

我们的 5 秒镜头只有一句起始状态，中间 4 秒是空的。

### 2.6 运镜用官方词表，且必须有动机

Push In / Pull Out / Zoom In / Zoom Out / Pan / Truck / Tilt / Pedestal / Arc Shot / Tracking Shot / Static Shot / Roll / Shake / POV；幅度 small/large，速度 slow/fast。推近和变焦不是一回事。

我们旧 prompt 只有“机位在道路东侧”，没有运镜类型、幅度、速度，也没有为什么动。

### 2.7 切镜只在新信息时用

距离或轻微角度变化用运镜；切镜必须带来主体、空间、状态、视点或时间的新信息。

我们把多个动作塞进一个 5 秒段，H3 只能自己乱切。

### 2.8 三层声音各司其职

环境/动作/非语言人声进 overall_soundscape；台词/歌声/剧内音乐进主描述；观众才听得到的配乐进 non_diegetic_music，只写乐器、速度、力度，不写情绪。

我们旧 prompt 的 soundscape 是模板句，没有拖步、布料、呼吸这些动作音。

### 2.9 台词逐字，说话人稳定 ID

台词必须原文进 <d>[Chinese] …</d>，说话人 (S1)；只写“他说了句话”会生成乱语；台词镜通常用静止机位，台词后写 holds the pose。

我们的台词来自分镜表，但旧 draft 把说话人写成“幸存者（画外）”，没有稳定 ID 规则。

### 2.10 感染者的运动语法

这是漫剧特有的，不在通用指南里，需要单独定义：

- 重量：先承重腿，再拖拽腿；重心先偏，再迈。
- 延迟：躯干先转，头晚 0.2–0.4 秒；视线锁定后不立刻伸手。
- 非同步：多个感染者不同步、不同速、不同朝向；禁止齐步、禁止同时转头。
- 惯性：停下时身体前冲半拍；伸手时肘先于手。
- 病态靠肤色、失焦、关节迟滞，不靠血腥。

我们旧 prompt 只有“苍白、失焦、迟缓”，没有这些运动规则，所以机械。

## 3. 我们的链路错在哪

| 症状 | 直接原因 | 代码位置 | 修法 |
|---|---|---|---|
| 场景混乱、格与格不像同一地点 | 宫格每格独立生成，prompt 只有单格 blocking | narrative_groups/image_prompt.py、storyboardFrame | 每格注入同一份 GEO；场景 plate 作为共同参考 |
| 人物位置漂移 | GEO 未进入 cell prompt；视频首帧来自单格裁图 | panel_spec.py、storyboard_adaptation.py | GEO 进入所有 prompt；首帧优先用场景 plate + 单格 |
| 丧尸机械 | 只有形容词，没有运动规则与节拍 | panel_spec.py 的 performance 只有 start_state | 新增 infected_motion + beats |
| 镜头没有戏 | segment prompt 是静态面板文本 | storyboard_adaptation.build_draft | 用 ShotPlan.intent/action/camera_motion 生成导演 brief |
| 前 2 秒空 | 没有 first_two_seconds 字段 | ShotPlan | 新增字段并强制 |
| 运镜缺失 | camera_motion 默认 static，未写入 prompt | ShotPlan.camera_motion | 映射官方词表，写入 brief |
| 声音模板化 | sound 字段为空或泛化 | rows.sound | 三层声音模板 + 动作音 |

关键发现：ShotPlan 已经有 intent（narrative_purpose / audience_attention / emotional_effect / continuity_strategy）、camera_motion、action、visible_start_state / visible_end_state，但**视频 draft 没有用它们**，只用了压平后的 description。

## 4. 改造方案

### 4.1 数据模型

在 ShotPlan 增加一个可选的 CinematicDirection（或复用 creative）：

    dramatic_task: str          # 这镜让观众得到什么
    first_two_seconds: str      # 首 2 秒的视觉事件
    beats: tuple[CinematicBeat, ...]  # start,end,action,performance
    infected_motion: str        # 感染者的重量/延迟/非同步/惯性
    camera_move: str            # 官方词表 + 幅度 + 速度 + 动机
    sound_layers: SoundLayers   # ambience / action / dialogue
    continuity_anchor: str      # 轴线、屏幕方向、道具手别、光

CinematicBeat: start_seconds, end_seconds, action, performance, gaze.

### 4.2 编译器

新增 src/novelvideo/freezone/video_director/cinematic.py：

    compile_cinematic_brief(shot, group, style) -> str

输出顺序固定：

    GEO (locked): ...
    镜头任务: ...
    首 2 秒: ...
    节拍: 0.0–1.2s ...；1.2–2.8s ...
    表演: ...
    感染者运动: ...
    运镜: ...
    光: ...
    声音: ...
    连续性: ...
    台词: ...

约束：
- 只从 ShotPlan/group 读事实，不编造角色与道具；
- 节拍覆盖整个 duration，不留空窗；
- 运镜只允许官方词表；
- 台词逐字，说话人稳定 ID；
- 不含世界坐标。

### 4.3 接入点

- 视频：storyboard_adaptation.build_draft 的 segment prompt 改为 compile_cinematic_brief 的输出，不再用压平的 panel text。
- 宫格：image_prompt 每格前置同一份 GEO + 镜头任务 + 可见起始状态；格子只负责“这一帧”，不负责动作。
- 分镜表：projection 输出增加 cinematic 摘要列，便于审阅。
- H3：优化器仍负责英文六段式，但事实来自 brief；禁止优化器自行发明动作。

### 4.4 校验门

新增 freezone/video_director/cinematic_check.py：

- GEO 必须存在且在所有 cell/segment 中一致；
- beats 覆盖 [0, duration]，相邻不重叠，间隔 ≤ 1.5s；
- camera_move 必须在官方词表内；
- 感染者在场时必须包含重量/延迟/非同步规则；
- 台词逐字匹配，说话人 ID 稳定；
- 屏幕方向不得出现“角色左边”；
- 不含数字世界坐标。

### 4.5 试点（不付费）

先用第 3 镜生成 brief，人工审阅；通过后：
1. 重写 14 镜 brief（免费）；
2. 出一张带 GEO 的 g1 宫格（1 张付费）；
3. 用新宫格出一条第 3 镜视频（1 条付费）；
4. 审片通过再整集重出。

## 5. 与“专业 skill”的对应

- 分镜/执行导演：GEO、首秒宽景、blocking、轴线、屏幕方向、每镜任务。
- 表演指导：肌肉/呼吸/视线/重心、每 1–2 秒微事件、反应先于台词。
- 摄影指导：官方运镜词表、幅度/速度、切镜条件、光逻辑。
- 声音指导：三层声音、台词逐字、说话人 ID。
- 漫剧特有：感染者运动语法、群演非同步、无血腥表达。
