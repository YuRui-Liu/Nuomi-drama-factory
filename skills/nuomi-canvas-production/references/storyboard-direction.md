# 分镜表到宫格：导演处理

在生成宫格之前读取本文件。分镜表说明叙事、动作和声音；宫格只呈现导演选择的静止瞬间，两者不能简单复制。

## 风格绑定

读取项目设置和正式 `/api/v1/styles` 风格目录。用户指定优先于旧项目默认。查询所选风格详情，记录 ID、名称和实际片段；不要只手写风格名称。

区分两个入口：扩展风格使用节点 `extensionStyleId`，其内容由适配层追加到提示词；图片模板使用 `styleTemplateId`，编译为请求的 `style.template_id`。它们不是同一个目录，不要把扩展 ID 填进模板字段。扩展风格生效时，请求 `style` 可以为空，但提示词必须包含正确的 `[Extension style: ...]` 片段。用 `canvas preview` 验证编译结果及参考图顺序，不能只看节点标题或项目设置。

已有参考图可能带有与目标冲突的渲染风格。明确其用途仅为身份、衣服或空间；不能声称加上风格字段就保证结果。用户要求不重出资产时保留资产，在首组实际结果中检验风格转化。

## 导演工作稿（保存在本地项目正文）

1. **空间与轴线**：同一场景先定义世界方向、固定地标、人物初始位置、各动作的路径和终点。标出对话/运动轴线与摄像机所在侧，选择是否跨轴及所需过渡镜头。画面左右由机位投影得出，不独立为每格编造左右。
2. **机位**：为每镜说明机位位置、朝向、景别、镜头视角、机高及是否移动。近远景切换交代共同地标；没有叙事理由不跳换人物方位。区分角色身体朝向与眼睛目标。
3. **光线**：锁定场景内主光的世界方向、软硬、色温感受与环境补光。换机位时推导明暗面的变化，不让每格都机械套用“左侧光”。光线突出叙事焦点而不改变时间天气。
4. **表演**：写出当前情绪、强度、眉眼嘴、肩颈与手势，以及本镜相对上镜的变化。不要仅写“紧张”或让所有人统一瞪眼。独白和非说话人物不自动加张嘴口型。
5. **道具**：列明由谁持有、哪只手、朝向、首次出现时刻及前后状态。背包在背上、抱在胸前、手提是不同状态；不能把资产设计图的佩戴方式当成剧情持物状态。
6. **静帧选择**：从连续动作中选一个明确瞬间，注明 start/end/selected；不得在单格里同时画动作起点和终点。对白与运动仍保存在分镜表，图中只保留该瞬间可见的事实。

## 有 3D 场景工作台时的空间预演

需要真实可旋转场景锚点、演员/道具走位或机位关系时，可打开场景的 3D Director，而不是把二维静帧当成立体测量。先确认活动背景/场景资产和该场景中的演员、道具身份；只用能对应到剧本或已批准美术的对象。演员代理模型用于站位、朝向和姿态预演，不等于最终角色模型或动画验收。

`ThreeDSceneSnapshot` 当前保存的数据是 `schemaVersion: 1`、Unix 毫秒 `savedAt`、`actors`、`props`、`stagings`、编辑相机 `camera`、输出相机 `outputCamera`、可选 `lights`，以及保留场景源数据的 `world`/`timeline`。每个 actor/prop/staging 标记包含 `label`、`color`、三数 `position: [x,y,z]`、`yawDeg`、三数正值 `scale: [x,y,z]`，可选 `id`、`pose`、`actionPlaying`、`shapeHint`、`placement`、`mount: {kind,index,attachPointId}`。`placement` 可为 `{space:"world",position:[x,y,z],yawDeg}`，或 `{space:"pano_view",yawDeg,pitchDeg,distance}`；挂接 `kind` 是 `prop` 或 `staging`。`camera` 和 `outputCamera` 都是 `{azim,elev,distance,focalPoint:[x,y,z],fov}`；灯包括真实 `id`、`label`、`color`、`intensity`、三数 `position`、三数 `direction`、`enabled`。应通过工作台拖放、选姿势、放置灯和调相机，再点击“保存 3D 世界”回写快照；不要手工构造 UI 内部对象来伪造已保存场景。

下例展示完整快照的嵌套形状；标记 ID 必须换成当前 director plan 中对应的 subject、prop、light ID，色值用 `#RRGGBB`，向量均为三个数：

```json
{
  "schemaVersion": 1,
  "savedAt": 1771885600000,
  "actors": [{"id":"lin-du","label":"林渡","color":"#bb6688","position":[0,0,0],"yawDeg":90,"scale":[1,1,1],"pose":"standing","actionPlaying":false}],
  "props": [{"id":"teacup","label":"茶杯","color":"#aaaaaa","position":[1,0,0],"yawDeg":0,"scale":[1,1,1],"shapeHint":"cup"}],
  "stagings": [{"label":"门框锚点","color":"#888888","position":[-2,0,1],"yawDeg":0,"scale":[1,2,0.2],"shapeHint":"door"}],
  "camera": {"azim":0,"elev":20,"distance":8,"focalPoint":[0,1,0],"fov":60},
  "outputCamera": {"azim":12,"elev":10,"distance":6,"focalPoint":[0,1,0],"fov":45},
  "lights": [{"id":"window-key","label":"窗侧主光","color":"#f0e0c0","intensity":1,"position":[-3,4,2],"direction":[1,-1,-1],"enabled":true}],
  "world": null,
  "timeline": null
}
```

可保存的定格快照/截图用于复核场景关系、角色朝向、道具归属、机位构图与灯光落点。该视图的演员是轻量代理，舞台灯光只照亮简化代理几何；捕获画面是空间参考，不是最终渲染、光照物理验证或影视级动作预览。把通过人工确认的起始/结束布局再投影到导演计划，而不是让代理场景覆盖剧本。

导演计划的 scene-blocking REST 端点保存单场景版位并支持版本冲突检测：`PUT /api/v1/projects/{project}/episodes/{episode}/director-workspace/scene-blocking/{scene_id}`，正文包括 `base_version`、`blocking`、`group_ids`、`director_revision_id`。`blocking` 严格字段是 `label`、`actors`、`props`、`camera`、`lights`、`stage_snapshot`、`stage_capture`。scene-map actors 使用 0–100 规范化 `x/y`、`facing`、`gaze_target` 和按 `at_seconds` 递增的 path 点；props 还可带 `held_by`，camera 保存 `x/y/facing`，灯保存 `x/y/facing/intensity`。

`blocking.stage_snapshot` 使用上面的 `ThreeDSceneSnapshot` schema；`blocking.stage_capture` 是 `{filename,url,captured_at,scene_id}`，`captured_at` 使用 Unix 毫秒。此 capture 对象保存截图文件元数据，不会单独创建 storyboard image node 或 `referenceBindings`。要把截图用于出图，仍需把实际上传图片作为带资产 ID/version/hash、角色 `composition` 和 panel 范围的引用连到目标 storyboard node。只有画布读回能看到此绑定时，才能说截图会参与 render。

stage snapshot 编译到 director shot 时，演员 ID 必须匹配关联 group 中的 `cinematography.subjects[].subject_id`，道具 ID 必须匹配对应资产要求或 `cinematography.props[].prop_id`，灯 ID 必须匹配 `cinematography.lights[].light_id`；不匹配会拒绝编译。快照的 `camera` 是编辑观察机位，不能替代输出机位；只有存在 `outputCamera` 时它才会被投影成导演计划的 `cinematography.camera_side` 文本，没有 `outputCamera` 就保留 shot 原有相机数据。actor/prop/light 的 3D 位置与光方向是代理舞台数据，不等于测量过的真实空间或物理照明。场景图必须由代理实体直接构建，不能声称单张全景图被重建成了精确几何。

可通过 `directorWorkspaceNode` 的公开通用 CLI 操作保存 blocking；不是独立的 `canvas director save-blocking` 子命令。先读 `canvas get` 和 `episodes/EPISODE/director-workspace`，取当前 canvas revision、节点绑定的 director revision、scene/group IDs 与 `scene_blocking` 当前 revision/version。把 `baseVersion` 填成服务返回的 scene-blocking revision ID 或 version；首次保存用 `null` 或 `0`。`groupIds` 与节点绑定的 director revision 必须指向当前 scene。然后将 `directorOperationInputs` 写进该节点的 `data`：

```json
{
  "data": {
    "directorOperationInputs": {
      "operation": "save_blocking",
      "sceneId": "SCENE_ID",
      "groupIds": ["GROUP_ID"],
      "baseVersion": "CURRENT_BLOCKING_REVISION_OR_VERSION",
      "blocking": {
        "label": "茶室空间预演",
        "actors": [],
        "props": [],
        "camera": null,
        "lights": [],
        "stage_snapshot": {
          "schemaVersion": 1,
          "savedAt": 1771885600000,
          "actors": [], "props": [], "stagings": [],
          "camera": null, "outputCamera": null,
          "lights": [], "world": null, "timeline": null
        },
        "stage_capture": {
          "filename": "scene-blocking-01.png",
          "url": "PROJECT_UPLOADED_IMAGE_URL",
          "captured_at": 1771885600000,
          "scene_id": "SCENE_ID"
        }
      }
    }
  }
}
```

上例的空标记数组只展示 schema；实际要编译到 shot 的演员、道具与灯必须填入真实 IDs，不可用名字或自行发明的短名代替。先保存 blocking，再从其返回的 `scene_blocking_revision_id` 读取最新版本；将节点输入改为下列 `compile_blocking` 操作，`baseRevisionId` 必须匹配 `directorRevisionId`：

```json
{"data":{"directorOperationInputs":{"operation":"compile_blocking","baseRevisionId":"CURRENT_DIRECTOR_REVISION"}}}
```

```bash
nuomi --project PROJECT request episodes/003/director-workspace
nuomi --project PROJECT canvas nodes update CANVAS DIRECTOR_NODE \
  --body-file director-node-update.json --base-revision CANVAS_REVISION \
  --request-id episode-003-scene-blocking-config-1
nuomi --project PROJECT canvas run CANVAS --node DIRECTOR_NODE \
  --operation save_blocking --expected-revision CANVAS_REVISION_AFTER_UPDATE \
  --request-id episode-003-scene-blocking-save-1
nuomi --project PROJECT canvas status RUN_ID
nuomi --project PROJECT canvas wait RUN_ID --timeout 3600
```

保存成功回执包含 `scene_blocking_revision_id`。`compile_blocking` 使用新版本再次更新 `directorOperationInputs` 并运行同一 `directorWorkspaceNode`；它生成 `review_required` 的新导演计划候选，不会激活。先核对组/镜头投影及 `outputCamera`，再按导演计划的人类审阅和激活路径处理。

对已保存、版本匹配的 scene blocking，REST 编译路由是 `POST /api/v1/projects/{project}/episodes/{episode}/director-workspace/scene-blocking/compile`，正文为 `{ "base_revision_id": "CURRENT_DIRECTOR_REVISION" }`。与通用 `compile_blocking` 操作相同，它只创建新候选，不会自动采用。若当前 `canvas capabilities` 不暴露 director workspace 的 `save_blocking` / `compile_blocking`，使用上述 REST 路由（如公开可用）或保留在本地导演工作稿；不要声称调用了未暴露的节点操作。

## 编译与检查

当前静帧契约是严格对象：

```json
{
  "id":"panel-07", "shot_id":"shot-07", "anchor":"selected",
  "state":"她已经停住，右手仍握住杯柄",
  "framing":"中近景，平视",
  "subjects":[{"entity_id":"lin-du","appearance_reference":"asset-lin-du","screen_position":"画面右侧","pose":"眉头收紧，嘴唇微启，右手握杯柄","gaze":"看向门外"}],
  "setting":"沿用已确认的茶室；门在画面左后方",
  "lighting":"窗外冷日光为侧逆光，暖色室内补光",
  "visible_text":[]
}
```

`static_panel.subjects[]` 只允许 `entity_id`, `appearance_reference`, `screen_position`, `pose`, `gaze`。没有独立 `expression`、动作时间线、相机运动、景别或灯光强度字段；景别和机位语言进 `framing`，当前表情和姿势写进 `pose`/`state`，静态空间锚点进 `setting`，本帧可见光线进 `lighting`。连续运动、照明设备、镜头移动和时间仍留在 director shot/timeline 中，不能伪造面板字段。`description` 与 `static_source_description` 同步；修改镜头后重编译。

### 单镜表情／机位修正：改导演镜头，再重渲染受影响静帧

如果某一格的表情或镜头不对，先区分静帧和镜头层：面部/身体表现写入 `static_panel.subjects[].pose`（必要时同步 `static_panel.state`）；静帧取景写在 `static_panel.framing`；连续景别、角度、构图和运动写入 director shot 的 `shot_size`、`camera_angle`、`composition`、`camera_motion`。不要在静帧对象添加 `expression` 或 `camera_motion` 字段。

当前有公开通用 CLI 包装，不是专用 `director edit-shot` 命令。先 `canvas get` 读取当前画布 revision、`directorWorkspaceNode` 的 ID 与 `data.directorRevisionId`，并确认目标 `groupId`、`shotId`。用下例写入 `director-node-update.json`；所有 ID、revision 和改动文字都必须换成当前真实值：

```json
{
  "data": {
    "directorOperationInputs": {
      "operation": "edit_shot",
      "revisionId": "CURRENT_DIRECTOR_REVISION",
      "groupId": "GROUP_ID",
      "shotId": "SHOT_ID",
      "static_panel": {
        "id": "panel-07",
        "shot_id": "SHOT_ID",
        "anchor": "selected",
        "state": "人物已停住，仍握着杯柄",
        "framing": "中近景，平视，门留在人物左后方",
        "subjects": [
          {
            "entity_id": "lin-du",
            "appearance_reference": "asset-lin-du",
            "screen_position": "画面右侧",
            "pose": "眉头微蹙、下颌收紧，右手保持握杯柄",
            "gaze": "看向门外"
          }
        ],
        "setting": "沿用已确认的茶室，门在画面左后方",
        "lighting": "沿用已定的窗侧冷光与室内暖色补光",
        "visible_text": []
      },
      "shot_size": "中近景",
      "camera_angle": "平视",
      "composition": "人物在画面右侧，门作为左后方空间锚点"
    }
  }
}
```

```bash
nuomi --project PROJECT canvas nodes update CANVAS DIRECTOR_NODE \
  --body-file director-node-update.json --base-revision CANVAS_REVISION \
  --request-id episode-003-shot-07-direction-1
nuomi --project PROJECT canvas run CANVAS --node DIRECTOR_NODE \
  --operation edit_shot --expected-revision CANVAS_REVISION_AFTER_UPDATE \
  --request-id episode-003-shot-07-edit-1
nuomi --project PROJECT canvas status RUN_ID
nuomi --project PROJECT canvas wait RUN_ID --timeout 3600
```

`edit_shot` 输入要求 `revisionId` 与节点绑定的 `directorRevisionId` 相同，且 `groupId`、`shotId` 必须属于该版本；镜头字段沿用 `UpdateShot` 的 snake_case 名称，外层三个 ID 字段使用 camelCase。此 CLI professional-edit route 当前会创建 `review_required` 子版本，回执中的 `directorRevisionId` 指向该候选；它不等同于导演工作台 UI 的普通 `saveShot`，后者会直接更新 active plan。小型表情／机位修补优先用当前 UI 保存并核对更新镜头，不要额外要求用户为每个微小改动点一次 adopt；完整 storyboard 仍走既有真实人工 review checkpoint。只有选择 CLI candidate 路径时，才处理它的版本状态：读取候选与基准，确认确实包含要改的内容；激活候选会改写 active plan，必须已经有用户对该候选的明确授权，不能由代理把自己的检查冒充用户批准。

```bash
nuomi --project PROJECT request episodes/003/director-plans/CANDIDATE_REVISION
nuomi --project PROJECT request episodes/003/director-plans/BASE_REVISION
# 只有真实用户已检查并明确授权采用该候选后，才发出激活请求：
nuomi --project PROJECT request episodes/003/director-plans/CANDIDATE_REVISION/activate --method POST
nuomi --project PROJECT canvas get CANVAS
```

重新读取画布并确认其导演版本已更新；不要用节点 patch 冒充激活。

激活后只重编译／重渲染受影响的静帧或所在 storyboard node，按当前 capabilities 选择 `render`；保留未变镜头的成功产物。重建 storyboard 表和宫格，展示两者并在当前 `review` checkpoint 等真实审批；源镜头、资产或指纹变化时重新计算 stage hash。CLI 没有单独的 `director edit-shot` 子命令；如果当前 capabilities 未暴露此通用 `edit_shot` 路径，停在导演工作稿中并向用户报告接口缺口。

保存参考图的实体身份与适用格子，使用实际连接与指纹，不能只给文字编号。同一个人物设计图中的多视图不是多个人。宫格的行列、单格比例和整体比例须相容。

出图前逐镜检查：轴线是否成立；人物能否按路径到下一镜位置；视线指向是否正确；手是否同时承担冲突动作；光向是否连续；道具是否提前出现；是否把后续结果画入当前瞬间。首组图出来后与这些决定逐项对照，失败时定位是导演设计、编译输入还是模型执行问题，不以“任务成功”代替验收，也不把局部补词当作完整导演流程。

复杂多镜调度若已有合法的 director workspace/group 绑定，可复用公开的 save_blocking / compile_blocking 能力。没有绑定时在本地完成导演工作稿并编译静帧，不得谎称调用了上述服务，也不为形式完整强建一套版本节点。
