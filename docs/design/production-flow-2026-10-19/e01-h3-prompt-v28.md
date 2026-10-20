# E01 · g1 批次 1 · H3 参考模式 wire prompt（profile v28）

本轮只做**免费重编 + prompt 校验**：`canvas director preview` / `canvas director prepare` 只调用只读 preview 接口，
未调用 `submit`，未产生 attempt，未提交任何付费生成；未改 `.env`、前端或业务代码，仓库内只新增本文件。

## 1. 读取的现场

| 项 | 值 |
| --- | --- |
| project | `01M57PERGF7FHMF0QYWH1SD133` |
| canvas | `7fd6de4beabf48128dcfb5beb29637ec` |
| canvas revision（读取时） | **174**（`updated_at` 2026-10-20T16:40:00.359558Z） |
| canvas revision（prepare 之后） | **174**，`updated_at` 未变 → prepare 全程只读，画布未被写回 |
| 宫格 | `memory-e01-grid-g1`，`storyboardGenNode`，2×3，1672×941，图像 `0d802d00adee498a.png` |
| 使用的镜格 | `shot-1`、`shot-2`（g1 前两镜） |
| 导演节点 | `memory-e01-director-g1-b1`（`videoDirectorNode`），已保存 draft revision 5，`activeAttemptId` `eb003c16ec3c46b283ea86f6d2a1fed4`（历史视频，本轮未触碰） |

## 2. 输入装配（免费、只读）

- **段来源**：`shot-1` / `shot-2` 的段 prompt 取自当前宫格 `memory-e01-grid-g1.data.frames[].description`（现行「感染者」口径）；
  `shot-2` 末尾的锁定台词 `幸存者（画外，急促）：“老陈……是我！别过来！”` 取自节点已保存 draft 的段 prompt（宫格分镜格不带对白列）。
- **参考图**（沿用节点已完成的 storyboard 装配顺序，Picture 顺序 = Subject 顺序）：

| Picture | Subject | 参考节点 | sha256 |
| --- | --- | --- | --- |
| `<Picture 1>` | `<Subject 1>` 老陈（感染者） | `memory-e01-asset-laochen` | `daf548a9eb12efeb921a866fcd0b01f27e5f7fe09b7efda3456cfbcd933cd253` |
| `<Picture 2>` | `<Subject 2>` 林渡 | `memory-e01-asset-lindu` | `8dbddeea3c4045eb111da92e0d04ebcf2c1d06944f3b3e8a0f179f52da94c678` |
| `<Picture 3>` | `<Subject 3>` 主干道街景 | `memory-e01-asset-street` | `785c0b978b607491ae85ea7ff4717dd0e2f34b2341a385aedc83f7ed48377411` |

- **首帧**：沿用节点已保存 draft 的两个 storyboard cell（`aff432a4…`、`1ebddbec…`）。
- **draft revision**：`6`（已保存 draft revision 5 + 1，表示本次重编；不写回画布）。
- **对齐**：路由 `h3_ref`，每段 `ref2va` / `ref_plus_first`，请求 5.5s → **141 帧 = 5.875s**，
  合计 **282 帧 = 11.75s**（≤ 15s 上限），输出 1920×1088 @ 24fps。
- **draft fingerprint（服务端）**：`7311cff2ee011a76c4c53d0ece0278f5a7842ca195d0167904b3a95f51c1dc05`
- **profile**：`minimax-h3-director` @ **28**。

## 3. prepare 状态：PASS

```text
$ .venv/bin/python -m novelvideo.production_cli --project 01M57PERGF7FHMF0QYWH1SD133 canvas director preview --body-file draft.json
{"ok": true, "data": {"draft_fingerprint": "7311cff2…c1dc05", "revision": 6,
 "profile_id": "minimax-h3-director", "profile_version": 28, "route": "h3_ref",
 "reference_image_ids": ["memory-e01-asset-laochen","memory-e01-asset-lindu","memory-e01-asset-street"],
 "segments": [{"segment_id": "shot-1", "mode": "ref2va", "requested_duration_seconds": 5.5, "duration_seconds": 5.875, "frames": 141},
              {"segment_id": "shot-2", "mode": "ref2va", "requested_duration_seconds": 5.5, "duration_seconds": 5.875, "frames": 141}]}}

$ .venv/bin/python -m novelvideo.production_cli --project 01M57PERGF7FHMF0QYWH1SD133 canvas director prepare --body-file draft.json --prepared-file prepared.json
exit=0；返回 ok=true 且带 preview.timeline_data（两次只读 preview：先取指纹、再校验并编译）
```

服务端编译结果与本机 `compile_h3_wire(wire)` **逐字节一致**（两个 segment 均为 `identical True`），
所以下面导出的是 prepare 实际产出的 wire prompt，而不是另抄的一份文本。

本机共用校验（同一套代码，不联网、不付费）：

```text
validate_prepared: PASS
shot-1 dialogue identity PASS
shot-1 reference identity PASS
shot-2 dialogue identity PASS
shot-2 reference identity PASS
shot-1 quality passed= True codes= ()     # inspect_h3_prompt(prompt, 'ref2va', 5.875)
shot-2 quality passed= True codes= ()     # inspect_h3_prompt(prompt, 'ref2va', 5.875)
prompt lengths [('shot-1', 3989), ('shot-2', 4021)]
```

`preview.timeline_data` 关键封包：`totalFrames=282`、`frameRate=24`、
`taskType="r2v — 参考主体生视频(Reference to Video)"`、`refs` 为三张 `preview-image:*` 占位且顺序 = Picture 1→3、
每段 `taskType="i2v"`（各带首帧）、`timelineMode="prompt_batch"`。

## 4. 规则自检（6 条规则 / 7 个机检点，逐条 PASS，含证据）

> 上面 6 条规则里 `detailed_description` 有两条要求（结构 + 350–500 词），拆成第 3、7 行分别核对；其余每条一行。
> 批次 1 的两段各自是一条独立的 H3 请求（单镜），所以每条规则分别核对两个段。

| # | 规则 | shot-1 | shot-2 | 证据片段 |
| --- | --- | --- | --- | --- |
| 1 | 六段顺序 subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music | PASS | PASS | 解析所得字段顺序 `['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music']`，与官方顺序完全一致；由 `compile_h3_wire` 固定产出，`inspect_h3_prompt` 的 `h3.wire_field_order` / `h3.wire_field_set` 无告警 |
| 2 | 正文英文，仅对白/画面文字保留原语言 | PASS | PASS | shot-1 去掉 `<d>` 与 `<Subject/Picture N>` 标签后 **CJK 计数 = 0**；shot-2 仅剩 `幸存者`（对白说话人标识符，源数据，见 §6）以及 `<d>` 内的中文台词，其余正文全英文；本镜无画面文字（world text）需保留 |
| 3 | detailed_description 先 1–2 句风格，再 `[Shot 1]`；后续 `[Shot N] At MM:SS.mmm` 严格递增 | PASS | PASS | `[Shot 1]` 之前恰好 **2 句**风格句（"Rendered as 3D Chinese animation: …" / "The tone is quiet dread rather than gore, …"），随后才是 `[Shot 1]`；shot 标签序列 = `['1']`（单镜，无后续镜号，严格递增成立）；全文唯一的 `At` 时间是台词 cue `At 00:02.400`（≤ 5.875s），不是镜号时间戳 |
| 4 | 说话人 `(S1)/(S2)`，对白 `<d>[Chinese] …</d>` | PASS（源镜头无台词，N/A） | PASS | shot-1 `<d>` 计数 0，`check_dialogue_identity` 通过；shot-2 台词行：`At 00:02.400, 幸存者 (S1) says: <d>[Chinese] 老陈……是我！别过来！</d>`，与源台词逐字一致（speaker=`幸存者`、text=`老陈……是我！别过来！`） |
| 5 | summary 以 `[reference generation]` 开头 | PASS | PASS | shot-1：`[reference generation] A single continuous close shot on a ruined avenue …`；shot-2：`[reference generation] A single continuous over-the-shoulder shot …` |
| 6 | retention_analysis 每条含 `(appears in [Shot N])` | PASS | PASS | 3 条 `<Subject 1..3>` 全部带注记，例：`- <Subject 2> (appears in [Shot 1], offscreen identity context only): fully_preserved - …`（shot-1，林渡本镜不入画，按 v28 "offscreen identity context" 处理）；shot-2 三条均为 `(appears in [Shot 1])` |
| 7 | detailed_description 350–500 英文词 | PASS | PASS | shot-1 = **367** 词；shot-2 = **379** 词（仅统计拉丁字母词，不含 `<d>` 内台词与 CJK） |

同一套硬门的机器校验（不是放宽，也不是另立标准）：

- `inspect_h3_prompt(..., 'ref2va', 5.875)` → `passed=True`、`codes=()`：字段集合/顺序、
  `h3.dialogue_wire_invalid`、`h3.reference_subject_*`、`h3.action_beat_overload`、`h3.event_timestamp_out_of_range`、
  `h3.legacy_no_music_phrase` 等全部未命中。
- `inspect_h3_reference_semantics`：定义行 3 行（一行一个 Subject，均以 `<Subject N>:` 起头，`<Picture N>` 顺序 1→3）、
  retention 与定义一一对应、`detailed_description` 覆盖全部已声明 Subject、未出现未声明的 Picture/Subject。

## 5. 本次 prepared 的最终 wire prompt（服务端编译原文）

> 以下文本直接取自 `canvas director prepare` 返回的 `preview.timeline_data → segments[].prompt`，
> 与 `prepared.json` 里的 `segments[].prompt`（本地 `compile_h3_wire` 结果）逐字节相同。

### shot-1（5.875s / 141 帧 / ref2va + ref_plus_first / 源镜头无台词）

```text
subject_definitions:
<Subject 1> comes from <Picture 1>: Lao Chen, a gaunt man around fifty with thinning grey-black hair, hollow cheeks, dull greyish skin, unfocused pale eyes, a faded navy work jacket over a grey crew-neck shirt, and dark brown cargo trousers.
<Subject 2> comes from <Picture 2>: Lin Du, a lean young man in his twenties with dense black hair, a grey crew-neck T-shirt under a charcoal hooded work jacket, dark cargo trousers, and a teal backpack with black straps.
<Subject 3> comes from <Picture 3>: the ruined city avenue, a cracked four-lane asphalt road with faded white crosswalk stripes, a collapsed concrete overpass, a rusted green city bus, abandoned cars, weeds in the kerb seams, and a grey metal guardrail under cold overcast daylight.

summary:
[reference generation] A single continuous close shot on a ruined avenue in which Lao Chen's pale hand reaches into the foreground toward the lens while the wrecked street, built from the supplied reference pictures, stays completely still.

retention_analysis:
- <Subject 1> (appears in [Shot 1]): fully_preserved - face identity, greyish skin, navy work jacket, and cargo trousers
- <Subject 2> (appears in [Shot 1], offscreen identity context only): fully_preserved - face identity, black hair, charcoal hooded jacket, and teal backpack
- <Subject 3> (appears in [Shot 1]): fully_preserved - avenue layout, crosswalk stripes, green bus, guardrail, and collapsed overpass

detailed_description:
Rendered as 3D Chinese animation: cold overcast daylight on a wrecked megacity avenue, a desaturated grey-blue palette, soft filmic contrast, and clean matte surfaces with shallow depth of field on the near foreground.
The tone is quiet dread rather than gore, and one motivated lighting logic holds across the frame: cool 5200K key light from the upper left foreground, a 6500K ambient fill from above, and shadows laid to the back right.
[Shot 1] The camera sits low on the eastern curb of a north-south avenue and stays perfectly still; the first frame shows only the empty foreground of cracked asphalt, so nothing in the near field moves at the start. From the right edge of frame <Subject 1> Lao Chen's right hand then sweeps into the foreground and reaches toward the center of the lens, fingers spread and slightly curled, knuckles leading. Keep his skin pale grey-white and waxy, without blood, cuts, or decay detail. The cuff of his navy work jacket rolls back with the reach, and the hand's shadow slides across the road toward the back right. His face rides behind the hand at the upper left of frame, hollow cheeks and thinning hair, unfocused pale eyes tracking the lens without locking onto anything visible. Beyond him <Subject 3> the ruined avenue reads clearly: faded crosswalk stripes, the rusted green bus at the right kerb, abandoned cars, weeds in the asphalt seams, the collapsed overpass hazing out the skyline, and the grey guardrail along the curb. The hand crosses roughly two thirds of the frame width and slows as the fingertips near the center, until the palm fills the near foreground and the fingers hang almost still in the last beat. The camera never pans, pushes, or tilts; the threat comes from the hand's growing scale against a static street. <Subject 2> Lin Du stays off-frame on the roadway and supplies only offscreen identity context for this framing. Keep the axis north-south, keep screen direction running from the right foreground toward center, and do not cut inside the shot. Daylight highlights stay controlled so the pale hand reads as matte skin instead of blown-out white, and everything else in the street holds still: debris, bus, guardrail, and weeds.

overall_soundscape:
Diegetic only: a low wind funnelling between the towers, a distant metallic creak from the torn overpass, dry grit under shifting weight, the soft friction of a jacket sleeve as the arm extends, and a faint hollow brush of air as the fingers slow near the lens.

non_diegetic_music:
N/A
```

### shot-2（5.875s / 141 帧 / ref2va + ref_plus_first / 含 S1 画外台词）

```text
subject_definitions:
<Subject 1> comes from <Picture 1>: Lao Chen, a gaunt man around fifty with thinning grey-black hair, hollow cheeks, dull greyish skin, unfocused pale eyes, a faded navy work jacket over a grey crew-neck shirt, and dark brown cargo trousers.
<Subject 2> comes from <Picture 2>: Lin Du, a lean young man in his twenties with dense black hair, a grey crew-neck T-shirt under a charcoal hooded work jacket, dark cargo trousers, and a teal backpack with black straps.
<Subject 3> comes from <Picture 3>: the ruined city avenue, a cracked four-lane asphalt road with faded white crosswalk stripes, a collapsed concrete overpass, a rusted green city bus, abandoned cars, weeds in the kerb seams, and a grey metal guardrail under cold overcast daylight.

summary:
[reference generation] A single continuous over-the-shoulder shot in which Lin Du holds a defensive hand at his throat while Lao Chen's unfocused gaze passes him toward the street corner, and an off-frame survivor shouts to Lao Chen by name.

retention_analysis:
- <Subject 1> (appears in [Shot 1]): fully_preserved - face identity, greyish skin, navy work jacket, and cargo trousers
- <Subject 2> (appears in [Shot 1]): fully_preserved - face identity, black hair, charcoal hooded jacket, and teal backpack
- <Subject 3> (appears in [Shot 1]): fully_preserved - avenue layout, crosswalk stripes, green bus, guardrail, and collapsed overpass

detailed_description:
Rendered as 3D Chinese animation: cold overcast daylight on a wrecked megacity avenue, a desaturated grey-blue palette, soft filmic contrast, and clean matte surfaces with shallow depth of field on the near foreground.
The tone is quiet dread rather than gore, and one motivated lighting logic holds across the frame: cool 5200K key light from the upper left foreground, a 6500K ambient fill from above, and shadows laid to the back right.
[Shot 1] The camera holds at chest height on the eastern curb and looks north over <Subject 1> Lao Chen's right shoulder, so his dark jacket and the back of his head sit soft and large in the left foreground while <Subject 2> Lin Du stands in the right mid-ground at almost arm's length. Lin Du already has his right hand raised flat against the front of his throat, shoulders drawn up and back, weight planted, breath held low; he faces north into Lao Chen's eyes and his gaze flicks between them. Lao Chen's unfocused eyes ride past Lin Du's shoulder toward the street corner behind him, his arms stay down, and he never reaches for the young man in this shot. Behind them <Subject 3> the ruined avenue keeps its depth: faded crosswalk stripes, the rusted green bus at the right kerb, the guardrail, weeds, and the collapsed overpass hazing out the skyline.
At 00:02.400, 幸存者 (S1) says: <d>[Chinese] 老陈……是我！别过来！</d>
The call comes from off-frame beyond the corner: Lin Du's jaw tightens, his fingers press harder against his throat, and his eyes shift toward the sound while his body stays locked in the defensive hold. Lao Chen's head tilts a few degrees toward the shout without turning away from Lin Du, one slow blink follows, and then his gaze settles past the young man's shoulder again. Hold the final beat on that standoff: Lin Du's hand still at his throat, Lao Chen's hands still low, no contact between the two men, and the street unchanged behind them. Keep the over-the-shoulder axis north-south, keep screen direction running from the left foreground toward the right mid-ground, and do not cut inside the shot. Cold daylight stays consistent on both faces, with the bright overcast skyline held behind them so neither silhouette blows out, and the guardrail line stays unbroken from foreground to corner.

overall_soundscape:
Diegetic only: low wind through the avenue, a far metallic groan from the collapsed overpass, grit crunching lightly under boots, cloth shifting as Lin Du's shoulders tighten, and the off-frame shout arriving dry and close before the street noise settles back into wind.

non_diegetic_music:
N/A
```

## 6. 备注与偏差（未放宽任何规则）

1. **`幸存者` 是源数据里的说话人标识符，不翻译**：`check_dialogue_identity` 用源台词的 speaker 与该标识符逐字比对，
   共享编译器 `_compile_dialogue` 同样输出 `speaker (S1) says:`（profile 第 54 条：实体标识符属精确数据，豁免英文转写）。
   除它和 `<d>` 内的中文台词，正文全英文。
2. **单镜请求**：批次 1 两段各是一部独立的 H3 请求（每段一条 draft segment），因此每条 prompt 只有一个 `[Shot 1]`，
   不存在 `[Shot 2] At MM:SS.mmm`；严格递增在这种单镜形态下自动成立，没有为了凑格式伪造多镜。
3. **`non_diegetic_music` 取 `N/A`**：本阶段没有规划非画面音乐（规范值，非 legacy 短语），环境声全部写在
   `overall_soundscape`，且不含台词。
4. **首帧 cell 复用了节点已保存的 storyboard crop**（`aff432a4…` / `1ebddbec…`）：宫格 g1 在节点当初装配之后又重绘过
   （当前 `imageUrl` `0d802d00…`，节点 `storyboardPreparation.sourceUrl` 仍是 `2bde4520…`）。
   真要提交时应先跑一次 `canvas director prepare-storyboard` 重新切格并回写画布——那会改画布，超出本次「只读免费重编」范围，故未执行。
5. **风格句**来自剧本 `episodes/001/script.md` 的「3D 国漫动画」；draft 未带 `styleTemplateId`，画布也没有 style 前缀，
   若项目设置里另有 style prefix，应把这两句替换成设置里的原值（其余事实不动）。
6. 未执行 `submit`，未创建 attempt；`canvas revision` 在 prepare 前后都是 174、`updated_at` 未变，
   可直接作为「零付费、零写入」的证据。

## 7. 复现

```bash
# 只读；不提交
.venv/bin/python -m novelvideo.production_cli --project 01M57PERGF7FHMF0QYWH1SD133 canvas director preview --body-file draft.json
.venv/bin/python -m novelvideo.production_cli --project 01M57PERGF7FHMF0QYWH1SD133 canvas director prepare --body-file draft.json --prepared-file prepared.json
```

`draft.json`（本次重编输入，revision 6）：

```json
{
  "schema_version": 1,
  "revision": 6,
  "model_id": "minimax-h3",
  "aspect_ratio": "16:9",
  "resolution": "1080p",
  "references": [
    {
      "image_id": "memory-e01-asset-laochen",
      "url": "/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/freezone_gen/adf814bc4a474acb.png?v=1792371254079064579"
    },
    {
      "image_id": "memory-e01-asset-lindu",
      "url": "/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/freezone_gen/7528468a38d9403b.png?v=1792372305303051321"
    },
    {
      "image_id": "memory-e01-asset-street",
      "url": "/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/freezone_gen/18392a5b7d794738.png?v=1792507868555717673"
    }
  ],
  "segments": [
    {
      "id": "shot-1",
      "prompt": "画面：count: 画面内恰好 1 人：感染者\n感染者特征：苍白皮肤、失焦眼神、迟缓步伐、无血色；无血、无伤口特写。\nshot: medium_close_up；slightly_low；手从右侧前景伸向中央\naxis: 轴线 主干道南北向；机位在 道路东侧路缘；屏幕运动 手从画面右侧前景伸向中央；屏幕方向只从机位描述\nblocking: 无站位要求\nlighting: sun sun_day 来自左前上方照向路面，5200K 冷灰日光，强度key 1.0，动机日间外景主光；ambient sky_day 来自自上而下柔和漫射，6500K 冷灰天空光，强度fill 0.4，动机阴天式环境补光；关键光 sun_day；投影朝手影落向右后方路面；曝光优先：控制日间高光，保持手部苍白肤色可读\nperformance: 起始：日间城市主干道，南北路面可见废弃车辆与散落碎片，街角护栏静置；前景空无手部入画。\nprops: 无额外道具\ncontinuity: 场景空间图：南北向主干道，东侧街角与护栏，两侧废车与碎片。；起点后动作留给视频\n景别：medium_close_up",
      "duration_seconds": 5.5,
      "first_frame": {
        "image_id": "shot-1",
        "url": "/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/storyboard_cells/aff432a465989fe6d6262735007bee8860d3c490c548dee66bac6bdfacd4f1c5.png?v=1792509964952551739"
      },
      "last_frame": null,
      "technique": null,
      "case_reference": null
    },
    {
      "id": "shot-2",
      "prompt": "画面：count: 画面内恰好 2 人：林渡、感染者\nshot: medium；eye_level；越过感染者肩头看林渡\naxis: 轴线 林渡与感染者之间的南北向轴线；机位在 道路东侧，越过感染者肩头；屏幕运动 林渡位于画面右侧，感染者由左前方压近；屏幕方向只从机位描述\nblocking: 林渡 在画面右侧中景，面向北侧感染者，视线近身感染者的眼睛；感染者 在画面左侧前景至中景，面向林渡肩后街角，视线越过林渡望向街角\nlighting: sun sun_day 来自左前上方照向路面，5200K 冷灰日光，强度key 1.0，动机日间外景主光；ambient sky_day 来自自上而下柔和漫射，6500K 冷灰天空光，强度fill 0.4，动机阴天式环境补光；关键光 sun_day；投影朝人物影子落向右后方；曝光优先：保留感染者面部失焦眼睛与林渡抬手手\nperformance: 起始：林渡停在主干道路中，右手抬在喉前防御，肩背绷紧；近身感染者位于他左前方，苍白面孔靠近，眼睛失焦；街角护栏在背景东侧。\nprops: 无额外道具\ncontinuity: 场景空间图：南北向主干道，东侧街角与护栏，两侧废车与碎片。；起点后动作留给视频\n表情：呼吸压低，手指贴喉，目光随感染者视线微转。；失焦眼睛转动，手不抓林渡。\n景别：medium\n幸存者（画外，急促）：“老陈……是我！别过来！”",
      "duration_seconds": 5.5,
      "first_frame": {
        "image_id": "shot-2",
        "url": "/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/storyboard_cells/1ebddbec5e821475b08b5a76b057684d919ada4b871fda6bd6d801130b734306.png?v=1792509964953211278"
      },
      "last_frame": null,
      "technique": null,
      "case_reference": null
    }
  ]
}
```

`prepared.json` 顶层（完整 wire 正文见 §5，`prepared.json` 里的 `wire` 是同一结构的结构化形态）：

```json
{
  "revision": 6,
  "route": "h3_ref",
  "profile_id": "minimax-h3-director",
  "profile_version": 28,
  "optimized_at": "2026-10-20T16:56:01Z",
  "segments": [
    {
      "segment_id": "shot-1",
      "mode": "ref2va",
      "requested_duration_seconds": 5.5,
      "duration_seconds": 5.875,
      "frames": 141,
      "wire": "...",
      "prompt": "... 见 §5 ..."
    },
    {
      "segment_id": "shot-2",
      "mode": "ref2va",
      "requested_duration_seconds": 5.5,
      "duration_seconds": 5.875,
      "frames": 141,
      "wire": "...",
      "prompt": "... 见 §5 ..."
    }
  ]
}
```

每个 `segments[]` 条目：`segment_id` / `mode: ref2va` / `requested_duration_seconds: 5.5` / `duration_seconds: 5.875` /
`frames: 141` / `wire`（= §5 对应段的六段结构）/ `prompt`（= `compile_h3_wire(wire)`）。
`draft_fingerprint` 由 CLI 在 prepare 时按服务端冻结的 draft 注入，不写回 `prepared.json`。
