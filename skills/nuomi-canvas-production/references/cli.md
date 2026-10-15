# 公共 CLI 执行手册

## 分镜交接导演台

分镜只提供视频帧输入，绝不进入主体 Ref 列表。Ref 使用人物基础形象。逐片段调用 `POST freezone/video-director/recommend-frames`，传入当前 `frames` 和既定 `segments`（id、description、duration_seconds），获取 AI 推荐的首帧或首尾帧及理由；不得改写剧情或拆成逐镜付费任务。推荐不自动保存。没有手动覆盖时，可将返回 `pairs` 写入分镜节点 `data.videoFramePairs`；已有 `selection_source=manual` 的选择必须保留，除非用户明确要求替换。网页先展示建议，再由用户应用；CLI 写入时使用当前 base_revision 防止覆盖并发编辑。

`mode=first` 时 `last_frame_id` 为空，`mode=first_last` 必须指定真实起止画格。首尾模式要求已设计的 start/end 状态，不能将相邻镜头机械配对。用户可调整模式、首帧和尾帧。人物 Ref 与帧可同时提交；无人物 Ref 时按所选帧使用普通路线。网页与 CLI 以保存的 `data.activeInputMode`、`videoFramePairs` 和编辑后的 draft 为准。先保存选择，再 prepare；不能把整张四宫格当作首帧。

推荐调用示例：`nuomi --project PROJECT --request-timeout 180 request freezone/video-director/recommend-frames --method POST --body-file recommendation-input.json`。这是只读 AI 建议，不是付费视频提交；输出核对后再按上述修订保护保存。

`nodes update --body-file` 的节点参数必须放在 `{"data":{...}}` 内，写入后回读验证。先调用公开 `freezone/video-director/prepare-storyboard`，核对返回的 `draft.references`、首尾裁图、镜头提示词、时长和图片顺序，再提交视频。Ref 的 Picture 编号仅对应人物/资产参考，首尾帧另附，不占 Ref 编号；禁止照抄上游生图提示词中的旧图片编号。复用已验收分镜时设置 `data.referenceOnly=true`，避免节点运行重新生成上游图片。以任务完成、视频落盘可播放且原节点回显结果为验收，prepare 成功不等于生成完成。

在分镜节点保存 `videoFramePairs`：每项包含稳定 `id`、`first_frame_id`、可空的 `last_frame_id`、该镜头的 `description` 和 `duration_seconds`。四格若代表四个导演镜头，就必须产生四个对应的视频片段，各自推荐模式、独立绑定帧与时长，再按顺序组成叙事组时间线提交。不能压缩成第 1 格／第 4 格包整组的一对首尾帧。只有导演方案明确把多格设计为同一镜头的起止状态时，才按该镜头配对；若用户明确合并镜头，先修改导演方案，不能在推荐或执行阶段偷偷合并。

首尾 ID 指向 `frames[].id`，首尾模式必须是同一镜头明确设计的不同画格，静态合同 anchor 分别为 start/end；首帧模式无需尾帧，不能借用下一镜填充。公开 `prepare-storyboard` 裁图并保存逐片段草稿，执行检查同一份绑定；修改选择须重新准备。网页分镜节点提供同字段的逐片段编辑入口。叙事组仍是付费生产单位，不另写逐镜头供应商调用循环。

## 叙事分镜最终请求检查

执行前调用 `nuomi --project PROJECT canvas preview CANVAS --node BOARD`。该入口只读，不生成媒体；返回已保存画布版本、最终提示词、按实际附件排序的 `reference_manifest` 和依赖摘要。缺少 `frames[].static_panel` 时需要补齐选定瞬间，不能退回整段剧本拼接或通过重复出图掩盖。

预览确认后，用 `canvas run CANVAS --node BOARD --operation generate --expected-revision REVISION --request-id STABLE_ID` 提交；REVISION 取刚才预览的 canvas_revision。版本冲突时回读用户修改并重新预览，不能移除版本检查强行执行。新静态合同的画面或绑定参考变化会标记本组及下游旧产物 stale；文件保留供比较，stale 不可作为已验收结果。

`static_panel` 包含 id、shot_id、anchor、state、framing、subjects、setting、lighting、visible_text。每个 subject 绑定 entity_id 与 appearance_reference，并描述 screen_position、pose、gaze。`referenceBindings` 把实际上游 node_id 映射到 entity_id、name、role 和 panel_ids；人物 role 必须是 base_design。每次采用静态画面，保存 `static_source_description` 为当前导演 description，后续导演编辑不同步时执行会提示冲突。完整剧情只保留在导演设计中，不复制进静态字段。

不要手工按 `referenceOrder` 编造图片编号。核对预览中实际图片、身份、适用画格和节点模型参数；同一设计图的多个视图代表一个角色。模型参考容量仍需核对实际供应商配置，当前图片目录没有完整的统一容量字段，不能声称编排已自动检查所有模型上限。

画布可以自行创建角色。角色库中没有采用版本，不应阻止整集规划：缺失基础形象时建立角色生成节点，并作为分镜依赖。有现成全身设计图时直接复用，不使用头像代替，也不重复生成。规划节点可通过 `nodes update` 设置 `data.baseDesignBindings`，每项为 `{"name":"剧本角色名","role":"base_design","image_url":"当前项目媒体地址"}`；随后对该 script 节点执行 `run --operation plan_episode`。服务端校验图片并记录内容哈希。此绑定只作用于当前画布规划，不冒充角色库已采用状态，也不覆盖已有角色库基础形象。

先运行 `nuomi --project PROJECT canvas --help` 核对当前版本。以下大写标识替换为命令返回值；ID 不从显示标题推断。建议每次写请求给一个稳定 `--request-id`，提交超时后用原 ID 核对，不能换 ID 盲目重发。

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas create --name '这次，别留白' --canvas-id no-blanks-episode
nuomi --project PROJECT canvas plan no-blanks-episode --script script.md --duration 90 --aspect-ratio 16:9 --request-id plan-no-blanks-1
nuomi --project PROJECT canvas wait PLAN_RUN_ID --timeout 3600
nuomi --project PROJECT canvas validate no-blanks-episode --plan PLAN_RUN_ID
nuomi --project PROJECT canvas get no-blanks-episode
nuomi --project PROJECT canvas apply no-blanks-episode --plan PLAN_RUN_ID --base-revision REVISION
nuomi --project PROJECT canvas run no-blanks-episode --target final --request-id episode-no-blanks-1
nuomi --project PROJECT canvas wait EPISODE_RUN_ID --timeout 3600
nuomi --project PROJECT canvas export EPISODE_RUN_ID --output episode.mp4
```

画幅应取用户要求或项目设置；示例的 16:9 不是强制默认。规划成功与生成成功是两个运行，需分别等待。`wait` 到时只退出 CLI，不取消服务端任务；再用原 ID 等待即可。`apply` 需要 `get` 返回的最新 revision；不要猜值。

导演要求通过 `canvas plan ... --director-brief '固定机位，母亲说完后切莉娅反应'` 传入，保存为规划输入。新规划调用主线 DirectorPlanner，返回每组目标、可见转折和逐镜导演方案；四格不是镜头数量要求。必须核对各镜头的目的、起止状态、动作、声音与时长，不能仅看原文覆盖通过。网页叙事组节点可编辑逐镜描述与时长；CLI 对同一节点更新 `frames`，保留镜头 ID。修改后回读并重新准备视频；不要把旧视频算成新导演方案的结果。字段锁定和自动局部重导尚未全链路验收，不宣称修改会自动保留或旧素材自动失效。

返回包是 JSON。`data.plan_id` 同时是规划 `run_id`；`run` 返回 `data.id`。`status` 的 `data.attempts` 为节点 ID 到尝试数组，最后一项是当前尝试，保留历史供核对。

手动编排或逐节点验收：

```bash
nuomi --project PROJECT canvas nodes add CANVAS --body-file node.json --base-revision REVISION --request-id add-input
nuomi --project PROJECT canvas nodes update CANVAS NODE --body-file patch.json --base-revision REVISION --request-id edit-input
nuomi --project PROJECT canvas edges add CANVAS --body-file edge.json --base-revision REVISION --request-id connect-input
nuomi --project PROJECT canvas run CANVAS --node NODE --operation OPERATION --request-id node-check-1
```

原文节点 `node.json`：

```json
{"id":"source","type":"textAnnotationNode","position":{"x":0,"y":0},"data":{"content":"完整剧本文字","referenceOnly":true}}
```

节点修改 `patch.json`：

```json
{"data":{"prompt":"更新后的制作要求"}}
```

生成前必须设置节点参数，不能用提示词代替界面参数：

```bash
nuomi --project PROJECT canvas nodes configure CANVAS NODE --base-revision REVISION --aspect-ratio 16:9 --size 1K --model gpt-image-2 --style drama_ext.cinematic_photorealism
```

`configure` 用于图片节点，写入网页相同字段。`--aspect-ratio` 同时保存 `aspectRatio` 和 `requestAspectRatio`；后者表示生成目标，不随产物自然比例改变。`--style` 是扩展风格 ID（不是预览图片文件名），`--style-template` 是内置模板 ID，空字符串表示清除。模型、风格、摄像机的 ID 先从项目目录查询：`request freezone/image/models`、`request freezone/image/style-templates`、`request freezone/image/camera-options`；扩展风格用全局 styles 目录或项目网页读取，不能凭名称猜 ID。

其他选项：`--quality low|medium|high`、`--reference-image URL`、`--camera-body ID`、`--lens ID`、`--focal-length MM`、`--aperture f/2.8`。摄像机选项会整体替换 `cameraSelection`，需要保留的项应一并传入。其余节点字段仍用 `nodes update --body-file`，视频导演的画幅与分辨率属于 `data.draft`，不要将图片字段套到视频节点。

图片执行时以节点的模型、尺寸、画质、风格、摄像机和请求画幅为准，旧 `data.request` 不应覆盖这些已选参数。固定参考图使用 `referenceOnly: true`，并保留 `imageUrl`；与手动生成的 `referenceImageUrl` 区分。

当前 GrsAI 能力限制：项目共享分辨率目录规定 `gpt-image-2` 为 1K，`gpt-image-2-vip` 支持 1K/2K/4K。16:9 的 1K 请求为 1280×720，VIP 2K 为 2048×1152；不支持的档位会明确失败，不能静默降级。GrsAI 画质档位的供应商传递仍未完成验收，不能因为节点显示 high 就宣称高画质生效。编排的多张生成 `count > 1` 尚未验收，整集资产暂用 1 张。

每次配置后 `canvas get` 核对持久化字段，生成后核对实际尺寸与目视效果。风格、人设一致性、供应商参数生效是不同检查项，不能以任务 succeeded 代替它们。

连线 `edge.json`：

```json
{"id":"source-image","source":"source","target":"image","sourceHandle":"source","targetHandle":"target","data":{"sourcePort":"text","targetPort":"text"}}
```

输入输出类型以 capabilities 的 ports 为准。生成节点可使用 `data.request` 提供现有公开操作的请求字段；操作由类型/operation 白名单选择，不接受自定义执行 URL。media URL 必须属于当前项目。

导演节点的图片输入连线还需 `data.edgeKind="videoDirectorImage"` 和 `data.slot={"kind":"reference"}`。`plan/apply` 会自动添加；手动建图也必须遵守网页的绑定契约。图片模型应从项目公开 `freezone/image/models` 目录读取，第一项是当前配置默认值，不使用旧 provider 默认值。

生产顺序写入合成节点 `data.timeline`，每项是 `node_id`、`duration` 和可选 `source_start`。不要通过节点位置或生成完成顺序决定片段顺序。复杂音轨可通过已支持的 `request.tracks` 契约提供。

退出码：0 表示该命令成功；1 表示运行失败、阻塞或等待超时；2 表示 CLI 输入不合法。`run` 返回 0 只说明服务端接受运行。
