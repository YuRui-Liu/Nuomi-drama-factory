# 同一镜头的首尾帧

用户接受宫格作预演，不代表宫格中任一格都适合作为最终视频端点。保留原预演，只准备缺少的真实首尾帧；不自动重画已经接受的素材。按现有叙事组准备和排队，不另建逐镜收费循环。

## 准备和生成

1. 从当前正式剧本、已采用导演方案和资产版本固定每镜的起止状态。每对图属于同一个镜头：机位、焦距、空间和身份保持连续，尾帧是动作可达的结果。明确断封/闭盖/开盖、持物的手、动物出现时刻和唯一数量。
2. 用严格 `static_panel` 分别写首、尾两个瞬间，不把整个动作段复制进静帧。通过 `storyboardGenNode` 按组生成首尾帧联系表，`referenceBindings` 指向真实连线和文件哈希。参考图只约束声明的身份、场景或道具，不自动增加入画人物。
3. 付费前检查实际模型支持的尺寸档位和当前配置。例如当前 `gpt-image-2` 仅支持 1K；2K 使用支持该档位的模型（如已配置的 `gpt-image-2-vip`），不能仅凭节点下拉选项推断支持。`canvas preview` 可检查编译静帧和引用，但不保证供应商支持所有规格。
4. 单格比例与联系表整体比例分开填写；例如三行两列的 9:16 单格，对应整体 3:8。每个确切输入只提交一次，保存 run；未知结果先查原任务，不换 request ID 重抽。
5. 看实际图片再裁切。模型可能生成纵排或混合格位，不按请求行列盲切，也不为了排版再次付费。保存实际裁切范围和原图 SHA。不能把裁错的图当成完整首尾帧。

## 通过公开入口提取单帧

`POST /api/v1/projects/{project}/freezone/orchestration/canvases/{canvas}/images/split-grid`

均匀布局正文：

```json
{"source_url":"/static/projects/PROJECT/freezone/_outputs/GRID.png","source_sha256":"64位小写十六进制","rows":3,"columns":2,"cell_ids":["S01-start","S01-end","S02-start","S02-end","S03-start","S03-end"],"separator_px":0}
```

实际为混合布局时，省略 `rows`/`columns`，用与 `cell_ids` 一一对应的 `regions:[{"x":0,"y":0,"width":720,"height":1280}, ...]`。坐标是原图像素，必须在边界内且不重叠。响应返回每格 `path,url,sha256,width,height,crop_box`。此操作只裁切真实已校验的图片，不调用模型、不拉伸画面；同一输入可复用相同输出。

## 绑定到组视频

先 `GET /api/v1/projects/{project}/episodes/{episode}/narrative-groups/{group}/video/keyframes`，再用当前 revision 写入：

```json
{"expected_revision":0,"expected_director_revision_id":"CURRENT_ACTIVE_REVISION","bindings":[{"shot_id":"S01","first_frame":{"path":"freezone/_outputs/grid_cells/ID/S01-start.png","sha256":"64位小写十六进制"},"last_frame":{"path":"freezone/_outputs/grid_cells/ID/S01-end.png","sha256":"64位小写十六进制"}}]}
```

`PUT` 同一路径，`bindings` 必须按当前导演镜序完整覆盖该组全部镜头。路径是已有的项目相对图片路径，SHA 不带 `sha256:` 前缀。冲突后重读，不能强制覆盖。写完再 GET，核对 director revision、binding revision/hash、每对路径与实际图片字节。

组视频排队时冻结绑定和图片字节；每镜使用自己的首尾，不用下一镜的首帧充当上一镜尾帧。旧预演只保留来源记录，不作为额外画面条件混入端点。保持原有组视频任务和恢复链路。

## 外部 Codex 编写视频提示词

外部导演方案不等于视频阶段也已使用外部提示词。默认组视频规划器仍可能调用内部文字模型，不能仅凭导演 revision 以 `external-` 开头就宣称全链路外部编排。

生成前读取组视频 `GET .../video/prepared-context?mode=auto&dialogue_source=external_tts`，用其中的确切段 ID、帧数、首尾 SHA、风格原文、活动角色和编译上下文编写每段的 `H3DirectorPlan`。保持一镜一段的已确认切点；静音视频不携带对白，独立配音另行制作。

逐镜核对所有段落中的实体范围，包括物理、灯光、质量和正向约束。不要把“朏朏保持四足和白尾”等模板句复制到尚未出现动物的镜头；这类文字会成为新增对象的正向提示。闭盖镜头也不要放入开盖运动规则。角色只露出手、背影或局部时，端点不包含足够身份信息；需要从实际组视频 reference-preview 确认已选身份图，并选择支持“身份参考＋首尾帧”的已配置 H3 引用工作流。不要把独立 Director preview 的参考列表当成组队列已经选了参考；`selected:[]` 表示实际没有这些身份参考。若暂时仅用首尾帧，明确其身份漂移风险，不声称人物一致性已解决。

`prepared-context` 支持基础 `runninghub:minimax-h3` 和引用 `runninghub:minimax-h3-ref` 路线；以实际保存的工作流、返回的 provider workflow ID 和引用摘要为准。引用路线使用 `REF2VA` typed plan，按返回的引用顺序填写 `reference_subjects`；该计划不填写仅属于 FL2V 的首尾锚点，真实首尾图仍由段落输入传递。身份图、首尾图或工作流变化后，重新读取上下文并编写计划，不手改旧摘要、不回退内部规划器。

通过原有组级 `POST .../video/generate` 提交 `prepared_h3_plans`，包含 `schema_version:1`、当前 `preparation_sha256`、上下文原样返回的 `pins` 和完整 `segments:[{segment_id,input_hash,director_plan}]`。提交前运行确定性编译和质量校验；输入变化后重新读取上下文，不能覆盖哈希或回退内部模型。按组提交一次，保留任务 ID，使用原有任务恢复和视频拼接链路。

视频并发遵循用户指定值。通过 `/api/v1/task-runtime/concurrency` 保留其他 lane，仅修改 `video`；若返回 `restart_required:true`，确认无付费任务运行后重启，重新 GET 验证 project/user/executor 均已生效。费用约束沿用用户最新授权；不要把缺失报价或 CE 的零报价占位当成免费。

## 只修复指定镜头

用户已授权局部修正时，保留整组原视频、manifest 和所有已有片段。先绑定修正镜头的新端点，其他镜头绑定不变，再读取 `prepared-context?...&repair_shot_ids=S03`（多个 ID 用逗号连接）。返回的 `segments` 仅包含要付费修正的部分，`source_reuse_snapshot` 固定旧 manifest、片段字节、顺序和实际时长；不要自行拼造该快照。

仍通过同一个组级 `video/generate` 提交，除完整 `prepared_h3_plans` 外原样带上 `repair_shot_ids` 和 `source_reuse_snapshot`。队列校验快照后，只为指定镜头调用供应商，按原顺序复用其他片段并合成完整组。提交前后的未修正片段 SHA 应相同。原片段缺失、已变更或修正范围无法对应原段落时先解决来源问题，不悄悄扩大收费范围。

## 预览与限制

读取 `canvas director capabilities`。有身份参考与两端图时，检查 `ref_plus_first_last` 对应的当前 H3 引用路线；没有参考时可用受支持的 `fl2v`。不要显式选择会丢弃尾帧的仅首帧模式。

只读 Director preview 对整份 draft 的对齐后帧数执行每请求 15 秒上限。三镜创作时长共 15 秒，按 `17k+5` 对齐后可能超限；只读预览可按镜头边界分包验证，不能缩短剧情去凑上限。实际生成仍使用既有组队列；不要把预览分包另变成收费任务循环。核对组内实际视频段 ID 和单段对齐后帧数。

裁切尺寸不同或画面比例偏离预期时如实记录，不能声称已经通过最终视频构图验收。首尾图不能证明中间动作正确，生成视频后仍须播放检查。用户未要求重抽时保留可用结果及已知局部偏差。
