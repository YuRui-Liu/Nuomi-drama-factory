# 灯塔 E1 修复版真实验收记录

日期：2026-09-20，持续验收更新至 2026-09-22。范围：`acceptance_lighthouse_20260915` 第 1 集，项目 ID `01M2HPJFW5HGXYC8F9C94ZKWZS`。未修改 `bei`。

## 当前结论

9 月 22 日 r6 两段及组合预览已生成并完成技术播放检查，但跨段占位视觉审查失败，尚未取得合格修复版成片。正式合成仍被阻止，不能据此宣称生产可用。具体结果见文末。

用户已另行授权本轮新增人民币 50 元以内。各供应商按积分计费，当前没有可核实的人民币消耗账本；提交次数不能换算为实际费用。供应商调用失败不自动重购。

## 计划与素材

以下为 9 月 20 日素材状态；当前视频已改用下文 9 月 22 日恢复的 79143 来源，不再使用 46167 切格。

- 活动导演版本：`01M2M466TWH0W8VW22QSH0784H`。
- 前两镜合为一个 6.5 秒连续动作段，首帧 I2VA；第三镜 3.5 秒，单独 I2VA，明确有意切镜。
- 角色青年时期、海边灯塔、旧油灯均复用已有资产；新计划绑定任务未新增角色、基础场景或道具。
- 未生成草图。原成片与原参考图保存在项目 `acceptance_archive/pre_repair_20260916`。
- 2K 图任务：`3f3ebd9d-ed52-48ab-9e88-d63600e8e026`，已完成。普通 `gpt-image-2` 只支持 1K，先前 2K 请求被 API 422 拒绝，改用同供应商 `gpt-image-2-vip` 后成功。
- 实际画布 `2160×3840`；切格后输出 `1152×2048`。受画布与边框限制，原有效格约 `1071×1904`，仍有轻度上采样，状态保留 `degraded=true`，不宣称原生逐格 2K。
- 图像记录已保存角色、场景、道具的资产版本、SHA-256、镜头范围和绑定 ID，供视频连续性质检校验。

画布路径（相对项目输出目录）：

`grids/ep001/narrative_groups/group-e1fe7595ab1e5a062f4b_batch-e3b0c44298fc1c149afb_render_r1_46167_20260920221835527841.png`

## 实测发现与修复

1. 旧两段视频实际尺寸不一致：`736×1280` 与 `896×1184`。现逐段校验 H3 输出尺寸，超出允许的编码尺寸误差时保留片段并标记质量不匹配，不直接拼接。不能靠强制拉伸掩盖比例错误。
2. 资产规划的缓存归一化数据与 SQLite 原始基线不同，导致道具规划出现虚假并发冲突。改为从持久化记录获取 CAS 基线，仍拒绝真实并发修改；真实旧油灯绑定已成功。
3. 图像生成未向后续视频保留逐镜头资产证据。现保存并验证版本、文件摘要及镜头范围，角色身份同时保留角色名映射；不伪造历史任务的资产证据。
4. 同一生成段内部的前镜没有独立视频，不应要求其已观测视频状态；跨段要求仍保留。
5. 实测任务 `bf7031d9-3c67-44f1-a6d9-1887171984b1` 被 `h3.exact_terminal_requires_terminal_frame` 拦截，`transport_called=false`。合并风险把段内连续性错误用于整段尾帧要求。现仅依据最后一镜判定精确段尾，其他风险仍保留；回归同时检查真实段尾约束不会消失。
6. 实测任务 `70dc626f-2a2d-4fe7-9f84-da38e502fad4` 在提示词构建失败：摄影事实多行字符串进入禁止控制字符的单条 `continuity_locks`。现按行传递全部摄影事实，不放宽下游输入校验。失败发生在视频供应商调用前。

两项本次新增回归均先确认失败再修复。最新专项命令：

```sh
/Users/liuyuxiang05/Liu/.venv/bin/python -m pytest \
  tests/shot_continuity \
  tests/media_capabilities/video/test_h3_prompt_optimizer.py \
  tests/test_task_narrative_group_video_runner.py \
  tests/test_video_plan_unit_modes.py -q --tb=short
```

结果：457 passed，8 条第三方弃用警告。`git diff --check` 通过。

扩大回归覆盖 `tests/shot_continuity`、`tests/director_plan`、`tests/media_capabilities/video`，以及视频执行器、H3 前检、导演/叙事组 API、批处理 CLI、资产证据和逐段模式测试：1,658 passed，8 条第三方弃用警告，23.25 秒。编译器、视频执行器和新增编译回归测试的定向 Ruff 检查通过。这仍非全仓生产认证。

## 在途任务与后续验收

任务 `6105ffe9-4bd0-4aa4-90eb-d77550e3b2aa` 在文本网关结构校验阶段失败：Codex 连续返回带 `frame_differences` 的 I2VA 计划，违反 schema v3 模式限制。未进入视频供应商提交。现 H3 提示词 profile v12 明确列出各模式必填字段和空值要求，不静默清洗错误模型输出；相关编译器、优化器与整集提示词测试 135 passed。

两处运行时修复另经独立只读审查，未发现 Critical/Important 问题。根据审查建议补充双镜头末镜要求尾帧的拒绝反例，以及保留 merged 高风险的断言；2 个参数化用例通过。

v12 任务 `546b6068-4d7d-4896-8d6c-fa9edc2f7658` 通过结构校验，但被 `lighting_source_conflict` 拦截，`transport_called=false`。检查发现：英文输出要求与导演中文原值精确匹配存在冲突；修订合并又保留错误的非空灯光字段，使纠正候选无法生效。

新增修复：profile v13 明确锁定灯光值及别名映射必须原样复制；合并器仅允许替换被 `lighting_source_conflict` 明确拒绝的字段，之后仍执行同一事实校验。回归先复现正确候选仍被拒的失败，再验证正确修订通过、错误修订仍拒、未拒字段不被顺带覆盖，并覆盖整集优化器路径。最新相关测试 138 passed；独立审查未发现 Critical/Important 问题。

加载修复后的 API 已按指定 CE 数据目录重启。v13 实测任务 `3d68f8d4-35b2-46ea-aab8-078b83c8d44c` 通过 CLI 复用参考图提交；单轮最多提交一次组视频任务，含两个视频段。该任务已通过提示词结构和质量检查，以及两段首帧的真实 Codex 视觉审查；第一段已提交供应商，任务号 `2101864208420524035`，尚待结果。

首帧视觉审查记录位于项目状态目录 `cinematography_review_cache`：`07f836cca2459bc0adfa58fde6bb531a516a73b8a0d70af38269ea0e634fc874.json` 与 `fc2b512a281ebc5b2af20027990d07da1263c5b7a9da441aa7bf56670bb3c70c.json`，均为 passed，包含侧背面上坡、右手提灯和冷暖光源的可见观察。

v13 与灯光修订合并后的扩大回归：1,661 passed，8 条第三方弃用警告，16.85 秒。

## 2026-09-22：79143 分镜来源恢复（没有新视频）

用户授权重启与发布来源后，确认灯塔和 bei 项目均无 queued/running 任务。按指定 `ST_EDITION=ce`、`NOVELVIDEO_DATA_ROOT` 和 `NOVELVIDEO_STATE_DIR` 重启后端，实际 OpenAPI 已包含来源查询、选择和启用接口。未调用图像或视频供应商，也未调用提示词模型。

恢复使用用户指定的 `group-e1fe7595ab1e5a062f4b_batch-e3b0c44298fc1c149afb_render_r1_79143_20260916002606528394.png`。原图 SHA-256 为 `5bb91886a6e8e704cc52c77a75e985d302b34fef72edf09193aeffa9e5eb8dec`。实际布局为 2×2、第四格空白，与后来 DirectorPlan 的 triptych 布局不同；本次记录真实裁切布局，不重新生成或更改原图。

先在临时项目切分并逐图核对，再经来源服务发布。当前选择 ID 为 `fdf1ed6133ffd44568a3161fb4be900bbd5462a21f0c9bdd37d7bb33f623e72c`，版本目录相对项目输出目录为：

`frames/ep001/storyboard_versions/fdf1ed6133ffd44568a3161fb4be900bbd5462a21f0c9bdd37d7bb33f623e72c/`

| 镜头 | 文件 | 原图格位 | 当前两段 I2VA 的提示词图片角色 |
| --- | --- | --- | --- |
| shot-1 | cell_00.png | 左上 | unit-01 start_frame |
| shot-2 | cell_01.png | 右上 | unit-01 storyboard_context，不作为尾帧 |
| shot-3 | cell_02.png | 左下 | unit-02 start_frame |

三张输出均为 1152×2048，由约 718×1278 的有效格放大，不能称为原生逐格 2K。逐格 SHA-256、原始裁切框、缩放尺寸和冻结原图均保存到版本目录。来源的 generation_id 使用明确的 `recovery:79143:<原图摘要>`，不声称这是历史供应商任务 ID。

实际 API 和生产 CLI 查询均成功：来源 `validation.valid=true`、组级 `storyboard_contract_version=1`、三张 cell 指向独立目录，旧视频 `needs_regeneration=true`。本地使用真实已发布图片执行冻结与角色构建验证，输出与上表一致；模型和视频供应商调用数均为零。该检查不等同于真实提示词模型已经看图或生成视频已遵循分镜。

恢复前元数据及证据保存在：

`acceptance_archive/storyboard_recovery_20260922T090840925084Z/`

其中 `ep001.before.json` 为发布前组状态，`rehearsal.json` 保存旧媒体摘要，`published.json` 保存发布后的来源和路径。79143/46167 两张原组图、三个公共 frames、r5 两段视频及原 manifest 摘要均未改变。没有删除旧媒体，也没有修改 bei 的素材或配置。

代码验证：广域回归 1298 passed，8 条既有依赖弃用警告；策略启用、来源 API、组服务与快照专项 74 passed；本批 Python 文件 Ruff 通过。新增用例先确认接口不存在时失败，再验证启用与拒绝路径。

## 2026-09-22：真实视觉提示词与 r6 验收

实际路由为 Codex `gpt-5.6-sol`、low。原图字节确实经 `--image` 传入模型：初次整组调用三张，失败段修订只传该段两张。独立探针记录在 `/private/tmp/nuomi-e1-visual-acceptance-8oy6rj7g/`，该次未调用视频供应商，失败于实体标识质量检查，不能视为验收通过。

本轮修复和验证：

- 视觉策略升级为 2：ready 还须明确确认必需起始事实 verified，且逐图提供道具和灯光观察。缺少确认的旧缓存不能直接发布；不影响精确焦距等非必需未知项。
- profile 升级为 14：角色和道具标识按原值一致引用，不受英文描述要求影响；修订只允许替换被门禁明确拒绝的 physics 实体或陈述，重新执行原质量检查，不改空间站位和已接受事实。回归先复现正确修订仍失败，再验证正确候选通过、错误候选仍拒。
- 回滚历史版本保留失效状态，并重新比较分镜来源与视频来源，避免回滚使旧来源视频变成有效成片。
- 前端保留 paired I2VA 的 unit.mode，正确显示首帧，编辑其他单元时不转换为 FL2VA；工作流未同步提示与实际保存中状态分离。真实计划仍为 revision 2，未通过浏览器改写。
- 本轮广域回归 1309 passed；实体修复后视频能力与视频 runner 992 passed，均有 8 条既有弃用警告。前端复跑模式与 query 两文件 64 passed；浏览器来源显示 79143、两段显示首帧，无 pageerror。

加载修复后通过生产 CLI 提交 r6：任务 `7608d64a-a074-430c-92d7-a97f3d7a9e17`，冻结来源为 `fdf1ed6133ffd44568a3161fb4be900bbd5462a21f0c9bdd37d7bb33f623e72c`。两段真实视觉提示词 ready/verified，质量门禁通过；两段参考图摄影审查 passed。第一段 RunningHub 任务为 `2102367641992912898`。当前尚待供应商输出、实际尺寸与跨段衔接审查，不宣称成片合格。

### r6 终态：跨段视觉验收未通过

两段供应商任务均完成，第二段任务为 `2102370505230348290`。本次只提交一份组任务、共两段视频，没有重新生成图片，没有在验收失败后自动重购。

| 检查 | 真实结果 |
| --- | --- |
| 第一段 | 736×1280、24 fps、6.583333 秒视频，AAC 音轨 |
| 第二段 | 736×1280、24 fps、4.458333 秒视频，AAC 音轨 |
| 组合预览 | 736×1280、24 fps、11.041667 秒；浏览器播放至 ended，媒体 error=null |
| 解码与音频信号 | 265 帧解码完成；音频均值 -28.4 dB、峰值 -3.5 dB，非静音；未据此声称台词语义或口型已经验收 |
| 两段独立视觉检查 | 均 passed，朝向、右手油灯、夜景灯光及镜内固定机位通过 |
| 段间切点检查 | failed：第一段末人物位于画面中部偏右，第二段首帧恢复到左下区域，未满足既定占位约束 |
| 最终任务状态 | failed / PARTIAL_FAILURE，manifest 为 quality_mismatch，qc_passed=false |
| 正式成片合成 | 实际读取当前状态被 DIRECTOR_COMPOSITION_INCOMPLETE 阻止，未覆盖旧 ep001_final.mp4 |

相对项目输出目录的保留文件：

- `videos/ep001/narrative_groups/group-1_r6.mp4`：不合格组合预览，不是验收通过的 E1。
- `videos/ep001/narrative_groups/group-1_r6_segment_001.mp4`、`group-1_r6_segment_002.mp4`：供应商原片段。
- `videos/ep001/narrative_groups/group-1_r6.manifest.json`：绑定来源、profile 14、真实逐图观察、两次供应商任务、五份摄影审查及失败原因。
- 临时人工核对图：`/private/tmp/nuomi-r6-review-VnJxoF/segment1.png`、`segment2.png`、`boundary.png`；工作台与播放截图也保留于本轮临时目录。

抽帧支持「屏幕占位改变」这一观察；没有三维标定，不能仅凭两帧断言实际回退了几级台阶。单段末帧审查曾通过，而边界审查指出占位问题，也表明单段通过不能替代切点审查。本轮保留失败结论，不通过改导演事实、清除报告或降级门禁放行。

当前看图提示词已使用正确分镜，仍不能保证独立 I2VA 第一段的实际终态精确匹配下一段首帧。此前暂停的跨段尾帧接力没有被暗中启用，叙事组切换流程未改变。后续空间终态约束需另行设计验证；在预算消耗无法换算为人民币的情况下，不继续盲目重购。

最终代码回归：广域 1311 passed、8 条既有依赖弃用警告；前端模式/query 两文件 64 passed；TypeScript、定向 Ruff、git diff --check 通过。实体修订另经独立只读复核，未发现门禁绕过或已接受站位被覆盖。

仍待完成：真实视觉提示词/冲突输出验证、工作台浏览器与冲突详情验收、实现复核、供应商费用核对及新视频画幅、机位、切点、对白和播放验收。本次没有新 E1 成片。

## 2026-09-21：真实视频比例失败及根因

上述 v13 任务已结束，不再在途。供应商任务 `2101864208420524035` 和 `2101867259378225153` 均已返回视频，但两段均为 `896×1184`，与请求 `736×1280` 不符。逐段尺寸门禁将其标记为 `quality_mismatch`，任务返回 `partial_failure`；没有生成修复版最终成片。原始返回文件保留在 `videos/ep001/narrative_groups/group-1_r5_segment_001.mp4`、`group-1_r5_segment_002.mp4`，对应 `group-1_r5.manifest.json`。

只读检查持久化的 `effective_params_json`，两次请求的 timeline 根层及 output 层都为 `736×1280`。依据 [RunningHub 获取工作流 JSON 接口](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749014) 读取实际工作流 `2089723723468328961`，确认节点 12 的 `refine` 连接节点 18；节点 18 为 `MiniMaxH3DirectorRefine`，保存的比例为 `3:4 (竖版标准)`、宽高为 `896×1184`。节点 6 取导演节点的最终图像输出，节点 7 保存视频。本地参考 API 图未包含这个连接，不能代表线上契约。

运行时修复：仅对上述已核实工作流增加节点 18 的宽、高、比例、百万像素绑定，并通过 `director_params` 实际传递。旧版 Refine 使用“自定义”宽高；[上游新版实现](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/main/nodes/director_refine.py) 固定跟随一采比例，因此同时按其 `1024²` 面积单位同步 megapixels。其他自定义工作流不假设存在节点 18。版本递增以区分旧请求，输出尺寸门禁保持不变。此修复尚未进行新的供应商生成验收。

回归先复现缺失 `refine_width` 的失败，再验证绑定、尺寸计算和真实运行时参数转发。运行时与 pipeline 专项测试：86 passed，8 条第三方弃用警告；定向 Ruff 与 `git diff --check` 通过。

扩大回归 `tests/media_capabilities/video`、`tests/test_task_narrative_group_video_runner.py`、`tests/test_narrative_group_compose_canvas.py`：977 passed，8 条第三方弃用警告，17.18 秒。

只读供应商 `/openapi/v2/query` 返回的顶层 `usage.consumeCoins`：第一段 145、第二段 92，合计 237 积分；`taskUsageList` 是相同任务明细，不能重复累计。`taskCostTime` 725/456 为时间字段，不是金额。图像消耗与积分对应金额尚未核清。当前运行中的 API 尚未重启加载本次尺寸补丁，下一次生成前必须加载新代码。

当前不得据此宣称生产可用：还需修复后真实视频的尺寸、起中末帧及切点、对白、音轨选择、最终拼接和播放验收。已有 r5 片段抽帧显示首段有机位/灯塔亮度变化，切入第二段时人物位置与尺度变化仍需专门审查。视觉抽帧不等于逐帧质量保证；跨组切点与免重购恢复入口仍有此前审计列出的限制。任务数据库的费用字段为空，尚未核清本轮 50 元授权剩余额度；未盲目重购。
