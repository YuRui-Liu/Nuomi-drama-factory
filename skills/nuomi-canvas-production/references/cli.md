# 公共 CLI 执行手册

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
