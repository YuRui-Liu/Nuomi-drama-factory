# E01 · Shot 3 视频试点（GEO）· v28 wire prompt

> 试点状态：**成功**。第 3 次提交（最小差异：首帧换成 `storyboard_cells/shot-3`、image_id 改回 `shot-3`，RunningHub 已充值）完成，产出 **1 条 5.167s / 1920×1088 / 24fps** 视频。
> 前两次提交均在 `submitting` 阶段被 provider 拒绝；第 3 次通过后按约束未再提交。

## 1. 现场与结果

| 项 | 值 |
| --- | --- |
| project | `01M57PERGF7FHMF0QYWH1SD133` |
| canvas | `7fd6de4beabf48128dcfb5beb29637ec` |
| 导演节点 | `memory-e01-director-shot3-pilot`（`videoDirectorNode`，displayName `E1 · Shot3 试点（GEO）`） |
| 段 | 仅 `shot-3`，请求 5.0s → H3 对齐 **124 帧 = 5.1667s** |
| 路由 / profile | `h3_ref` / `minimax-h3-director` @ 28 |
| 输出 | **1920×1088 @ 24fps，5.167s，h264 + aac，4,703,421 bytes** |
| 成功 attempt / task | `31d29d7316c9482c9d9f4e533214d2a0` / `b0326bce-45fb-4d78-8a60-7603ca72aa84` |
| provider task id | `2112937260885442561` |
| videoUrl | `/static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/video_director/31d29d7316c9482c9d9f4e533214d2a0.mp4?v=1792599222636376535` |
| 节点回写 | `videoUrl` / `activeAttemptId` / `resultRevision=1` 已按前端同一逻辑投影，canvas rev 195 |

## 2. 输入装配

### 2.1 参考图（Picture 顺序 = Subject 顺序，三次提交均未改）

| Picture | Subject | 来源节点 | sha256 |
| --- | --- | --- | --- |
| `<Picture 1>` | `<Subject 1>` 林渡 | `memory-e01-asset-lindu` | `8dbddeea3c4045eb111da92e0d04ebcf2c1d06944f3b3e8a0f179f52da94c678` |
| `<Picture 2>` | `<Subject 2>` 老陈（感染者） | `memory-e01-asset-laochen` | `daf548a9eb12efeb921a866fcd0b01f27e5f7fe09b7efda3456cfbcd933cd253` |
| `<Picture 3>` | `<Subject 3>` 被追幸存者 | `memory-e01-asset-survivor` | `6268681316a073af6ef2ab77b553da1802213101d42baa1e7e3167a81…` |
| `<Picture 4>` | `<Subject 4>` 主干道街景 | `memory-e01-asset-street` | `785c0b978b607491ae85ea7ff4717dd0e2f34b2341a385aedc83f7ed48377411` |

### 2.2 首帧（第 3 次修正点）

第 1、2 次用的是 `_uploads/` 下手动裁切 PNG（558×314，image_id `shot-3-first`）；
第 3 次改为 `prepare-storyboard` 从新 g1 宫格（`a60aee49453044b9.png`）按 `cellRects[2]=[1114,78,1672,392]` 切出的 **storyboard cell**：

```text
image_id: shot-3
url: /static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/storyboard_cells/97c56d38c4859eeaf2d179e11b9a14ee06963bf709859c402aa81e6c5e05ce06.png
尺寸: 544×306（16:9）
```

该次 `prepare-storyboard` 同时把 g1 节点的 `cells` 刷新为新宫格的 6 张裁图（此前 cells 仍指旧宫格）；随后把导演节点 draft 恢复为本次 wire 的 v3（rev 3），未改宫格画面本身。

### 2.3 段来源 prompt（briefs-14 · Shot 3 全文，逐字）

```text
**GEO**：同 Shot 1；三角行动轴，机位在道路东侧路缘，不越轴。
- **镜头任务**：威胁从林渡转移到幸存者；观众在 2 秒内看懂“丧尸绕过林渡，去追背包的人”。
- **首 2 秒**：丧尸左腿承重、右靴拖过沥青，躯干先转向街角；林渡护喉手放下半寸。
- **节拍**：
  0.0–1.0s 林渡侧身站画面中央偏左；背景 3–5 名感染者分散游荡，不同步。
  1.0–2.4s 丧尸拖步转向街角，右手不伸向林渡，肘先抬后垂。
  2.4–4.0s 幸存者脚跟蹭地后退、攥紧背包带；丧尸绕过林渡肩侧，从左前景切向右侧街角；林渡侧身让开半步。
  4.0–5.0s 丧尸前冲半拍停住；幸存者退到护栏边；林渡留在两人之间，呼吸一次。
- **表演**：林渡不伸手、不喊；丧尸关节迟滞、视线锁定后不立刻伸手；幸存者恐惧在手指和膝盖。
- **运镜**：Static Shot，wide_medium/eye_level；不 Pan、不 Push。
- **光**：同 Shot 1；曝光优先背包轮廓与丧尸路线。
- **声音**：风、拖步擦沥青、背包带摩擦、三人呼吸；无配乐。
```

## 3. prepare 结果（第 3 次 PASS）

```text
draft_fingerprint 23a77c1d79cd462e7c73655c678faed2139cd6be7edb2ed25e8a747e18f543ac
revision 3 / route h3_ref / shot-3 ref2va / 5.0s → 124 帧 = 5.1667s / prompt 4685 字符
```

服务端 `timeline_data.global.prompt` 与本机 `compile_h3_wire(wire)` **逐字节一致**。

## 4. v28 自检

| # | 规则 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 六段顺序 subject_definitions → summary → retention_analysis → detailed_description → overall_soundscape → non_diegetic_music | PASS | `compile_h3_wire` 固定产出 |
| 2 | 正文英文，仅对白/画面文字保留原语言 | PASS | 去标签后 CJK 计数 = **0**；本镜无台词 |
| 3 | 先 1–2 句风格，再 `[Shot 1]`；后续镜号严格递增 | PASS | `[Shot 1]` 前恰好 2 句；shot 标签序列 = `['1']` |
| 4 | 说话人 `(S1)`、对白 `<d>[Chinese] …</d>` | PASS（N/A） | `<d>` 计数 = 0，未硬造 `(S1)` |
| 5 | summary 以 `[reference generation]` 开头 | PASS | 见 §5 |
| 6 | retention_analysis 每条含 `(appears in [Shot N])` | PASS | 4 条 Subject 全部带 `(appears in [Shot 1])` |
| 7 | detailed_description 350–500 英文词 | PASS | **413** 词；整段 wire **4685** 字符 |

机器硬门：`inspect_h3_prompt(prompt, 'ref2va', 5.1667)` → `passed=True`、`codes=()`；
`validate_prepared` 本地与服务端均 PASS。
保留：`[Shot 1]` 单镜、首 2 秒事件（左腿承重/右靴拖地/躯干先转）、感染者运动语法（延迟/惯性/非同步）、Static Shot、无台词。

## 5. 最终 wire prompt

```text
subject_definitions:
<Subject 1> comes from <Picture 1>: Lin Du, a lean young man in his twenties with dense black hair, a grey crew-neck T-shirt under a charcoal hooded work jacket, dark cargo trousers, a small dark teal backpack with black straps, and worn black boots.
<Subject 2> comes from <Picture 2>: Lao Chen, a gaunt infected man around fifty with thinning grey-black hair, hollow cheeks, dull greyish skin, unfocused pale eyes, a faded navy work jacket over a grey crew-neck shirt, dark brown cargo trousers, and black cloth shoes.
<Subject 3> comes from <Picture 3>: the fleeing survivor, a man around forty with a slightly round face, sparse short black hair, a faded rust-red zip jacket over a light beige shirt, grey-blue straight trousers, old grey sneakers, and a dark brown soft cloth backpack.
<Subject 4> comes from <Picture 4>: the ruined city avenue, a cracked four-lane asphalt road with faded white crosswalk stripes, a collapsed concrete overpass, a rusted green city bus at the right kerb, abandoned cars, weeds in the kerb seams, and a grey metal guardrail under cold overcast daylight.

summary:
[reference generation] A single continuous wide shot on a ruined avenue in which the infected man at Lin Du's shoulder turns away from him and drags toward the corner, the survivor backs toward the guardrail, and three to five infected extras wander out of sync behind them.

retention_analysis:
- <Subject 1> (appears in [Shot 1]): fully_preserved - face identity, black hair, charcoal hooded jacket, cargo trousers, and teal backpack
- <Subject 2> (appears in [Shot 1]): fully_preserved - face identity, greyish skin, navy work jacket, and dragging gait
- <Subject 3> (appears in [Shot 1]): fully_preserved - face identity, rust-red jacket, grey-blue trousers, and dark backpack
- <Subject 4> (appears in [Shot 1]): fully_preserved - avenue layout, crosswalk stripes, green bus, guardrail, and collapsed overpass

detailed_description:
Rendered as 3D Chinese animation: cold overcast daylight on a wrecked megacity avenue, a desaturated grey-blue palette, soft filmic contrast, matte stylised surfaces, and a clear sculpted silhouette with shallow depth of field.
The tone is quiet dread rather than gore, with a cool 5200K key from the upper left foreground, a 6500K ambient fill from above, and shadows laid to the back right.
[Shot 1] The camera holds at eye level on the eastern curb of a north-south avenue, perfectly still on a wide-medium frame; the grey guardrail and street corner sit east, wrecked cars and debris line both kerbs, and the collapsed overpass hazes the skyline. <Subject 1> Lin Du stands side-on in the centre-left, half turned to the corner, his right hand at his throat but already slipping a finger's width lower; shoulders down, weight on the back foot, neither reaching nor calling. His jaw is tight and his breath stays held. <Subject 2> Lao Chen, the infected man, is at Lin Du's left shoulder with his torso swinging toward the corner: the left leg carries the weight, the right boot drags flat across the asphalt, the right arm stays low and the elbow lifts a beat before it drops. His grey face is slack and his unfocused eyes lock on the corner, not on Lin Du. <Subject 3> the survivor backs away in the right mid-ground with his heels scraping, both hands clenched on the straps of the backpack pressed to his chest, knees bent inward and shoulders hunched, one heel skidding on loose grit. Three to five infected extras are scattered behind them at different distances, each halted at a different angle, pale and unfocused with lagging joints, never in step and never turning together. <Subject 4> the ruined avenue carries the depth: faded crosswalk stripes, the rusted green bus at the right kerb, weeds in the seams, abandoned cars, and the guardrail running from the near corner into the background. Lao Chen's path bends around Lin Du's shoulder from the left foreground toward the right corner while Lin Du gives half a pace; the survivor retreats until his back nears the guardrail, and the infected man stalls mid-step instead of reaching. The camera never pans, pushes, or tilts; the threat comes from blocking, not lens movement. Keep the axis north-south, screen direction from the left foreground to the right corner, and hold the last beat with the survivor at the guardrail, the infected man stalled, and Lin Du breathing once between them.

overall_soundscape:
Diegetic only: low wind between the towers, a boot sole dragging across asphalt, grit under shifting weight, dry friction as backpack straps pull tight, cloth shifting as the survivor backs away, and three uneven breaths.

non_diegetic_music:
N/A
```

## 6. 提交历史与结果

| # | request | attempt | 首帧 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | `pilot-shot3-video-20261021-1` | `edb8aa5872234af3aa13f159f9f804fe` | `_uploads` 手动裁切 558×314 / `shot-3-first` | `failed` @ submitting：Provider rejected the submission |
| 2 | `pilot-shot3-video-20261021-2` | `62acd268a5cf4276bc31f6b26908c372` | 同上（仅 wire 压到 4685 字符） | `failed` @ submitting：同上 |
| 3 | `pilot-shot3-video-20261022-3` | `31d29d7316c9482c9d9f4e533214d2a0` | `storyboard_cells` 544×306 / `shot-3` | **`completed`**，provider task `2112937260885442561` |

第 3 次完成后 `wait` 返回：

```text
stage completed   error None
result_url /static/projects/01M57PERGF7FHMF0QYWH1SD133/freezone/_outputs/video_director/31d29d7316c9482c9d9f4e533214d2a0.mp4?v=1792599222636376535
ffprobe: h264 1920×1088 24fps 5.166667s + aac 5.167s，4,703,421 bytes
```

抽帧检查（0.05s / 2.5s / 4.9s）：起始构图与第 3 格一致；感染者从林渡左肩侧绕向右侧街角；幸存者退到护栏边；林渡留在两人之间；机位全程静止、不推不摇；背景感染者不同步。

**失败归因**：第 1、2 次与第 3 次同时改了两件事——RunningHub 账户充值 + 首帧来源/image_id。无法在不做第 4 次提交的前提下拆分归因；从工程记录看，第 3 次是最小差异的成功配置，后续按此配置复现。

## 7. 偏差与备忘

1. **首帧路径**：第 3 次用 `prepare-storyboard` 生成的 storyboard cell，并顺带刷新了 g1 的 `cells`（此前指向旧宫格）；宫格 `imageUrl` 未变。
2. **节点投影**：CLI 提交不会自动把 attempt 结果写回节点，已按前端 `useVideoDirectorTask` 的同一逻辑补写 `videoUrl` / `activeAttemptId` / `resultRevision`（未改其他字段）。
3. **宫格风格**仍偏半写实 CG，`@image5` 3D 国漫锚点只部分生效。
4. **服装**：第 4 格及本视频中的追击感染者与林渡同款深色连帽外套，正式重出时需在 cells 描述里给追击者换成浅褐/脏灰外套（本轮未动）。
5. 只产出 1 条视频；未动 g2/g3/分镜表/其他导演节点；未提交 git；未改 .env/前端。
