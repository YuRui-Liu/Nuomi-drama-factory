# 本机生产环境验收（未通过完整出片验收）

## 实际运行基准

- 后端工作目录：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。
- 后端入口：`/Users/liuyuxiang05/Liu/.venv/bin/novelvideo api --host 127.0.0.1 --port 8780`。
- 环境：`ST_EDITION=ce`，`NOVELVIDEO_DATA_ROOT=/Users/liuyuxiang05/Liu/nuomi-drama-data`，`NOVELVIDEO_STATE_DIR=/Users/liuyuxiang05/Liu/nuomi-drama-data/state`。
- 前端：仓库 `frontend` 目录，现有 Vite 服务 `127.0.0.1:5173`；用户标准命令为 `pnpm dev --host 127.0.0.1`。
- 原后端 PID 78129 从 9 月 11 日运行，未加载本次修复。确认所有项目无活动任务后，经授权按上述命令重启；本轮依次加载修复，当前 PID 50725（此前 88451、12201 已停止）。
- 先前使用仓库虚拟环境、临时目录和 8782/5175 的验证仅算隔离冒烟；临时服务已停止，不能替代上述环境验收。

## 测试数据及证据

专用项目：`acceptance_lighthouse_20260915`，ID `01M2HPJFW5HGXYC8F9C94ZKWZS`。新建两集自造灯塔短剧本，未覆盖已有 `bei` 项目。

1. 独立 Chrome 配置真实打开前端，项目创建成功，创作画布显示已连接。
2. 分集导入预览正确识别第 1、2 集及标题「灯塔」「黎明」。
3. 导入任务 `f595589b-7156-4c7f-88ec-bfaabbef2ce7` 完成，原文版本 1，两集均 added，`source_committed=true`。
4. 自动图谱任务 `dc00fc00-498a-4fa2-89c9-e09f141be204` 完成：1 个分组、5 个实体、4 个事件、7 条关系。实际文本路由为 Codex / gpt-5.6-sol / low。
5. 图片 grsai-main、视频 runninghub-main 均报告凭据已配置。用户已授权 50 元小样本验收，平台以积分计量，不作未经证实的人民币换算。首张肖像任务 `bca7bb83-4e62-45f7-8c6c-e311a156f3e4` 已提交，尚待结果；未提交视频。

浏览器临时脚本和截图保存在 `/private/tmp/nuomi-acceptance-bjqAJw/`，不含日常浏览器配置。内置 Browser 插件因 trusted code path 校验失败不可连接，改用独立 Playwright + 本机 Chrome。未做 Windows 实机验证。

## 本机新发现与修复状态

| 编号 | 现象与证据 | 最小修复建议 |
| --- | --- | --- |
| L01 | CE 本机服务无需登录，但 CLI 在 `_perform` 中发现无 NUOMI_TOKEN/SESSION 即退出 | 已修复：无凭据时由服务端鉴权；真实无登录 CLI 已完成解析、角色构建和导演任务提交，401/403 回归保留 |
| L02 | 陪伴挂件随机落在顶部导航；点击「剧本导入」15 秒超时，拖动 handle 拦截 pointer events；键盘导航可进入 | 保留挂件，限制显示/拖动位置不遮挡顶部导航；补实际点击回归 |
| L03 | pnpm 的 Inter 字体位于仓库根 node_modules/.pnpm，超出 Vite 默认 frontend allow list，浏览器返回 403 | 仅额外允许解析后的字体包目录，保留其余文件访问限制 |

L02/L03 最小设计已获用户批准并实现，实测证据见后文。

### 真实流程新阻断及修复

- L04：`1-1 海边灯塔 外 夜` 不被场头解析器识别，导致零场次、零节拍仍被标记校验通过。增加「地点 内/外 时间」顺序支持；无场次生成直接报错；历史空场次/空节拍版本禁止激活。修复后两集实际重跑均产生一个有效场次与节拍，并成功激活：第 1 集 `01M2HQX1T0QKC23BPS4SZT9PP6`，第 2 集 `01M2HQX0PX9Z868D812W52Q9C9`。
- L05：角色多个字段引用同一句证据，落库时完全相同的 evidence 行触发 UNIQUE，整批发布回滚。仅对完全相同的持久化行去重，保留冲突数据的事务失败保护。真实重跑任务 `3289f9fb-a37e-45d8-a424-d3804b7993db` 已发布林舟及三个视觉提案；选择无质量问题的推荐方案 `linzhou_a`，确认者明确记为 `acceptance-agent`。
- 首集导演任务 `bc142f4a-554a-44fb-b450-2ba8bcd7b80f` 完成且验证通过。过早提交的场景、道具和身份规划因尚无激活导演方案失败；这些结果不算素材规划通过，需要按依赖顺序重跑。
- 本轮实际父级虚拟环境回归：场头解析、语义服务/存储/API/任务、结构化角色发布、CLI 和批量流程合计 **149 passed，8 warnings**。`git diff --check` 通过。
- L06：场景补全内部 `_create_scene_build_agent` 忽略任务冻结的 Codex 路由，尝试旧模型 API，任务降级为只有名称的场景却仍显示完成。已让该代理优先使用当前任务运行时，无路由时保留旧兼容分支；新增无 API-key 路由回归先失败后通过。场景质量、场景编译、场头标准化和运行时路由合计 **92 passed，8 warnings**。真实重跑 `24aab491-138e-4a7a-9f88-339e175390fd` 已进入生成后的质量检查，但因 `direction_not_concrete:正面/左侧/背面, material_not_concrete` 失败；路由修复不代表场景规划已经可用。下一步须审查面向室外场景的质量规则与实际模型输出，当前规则的实体和材料词表主要偏向室内建筑。
- L07（通道与本例误拦截已修复）：原身份图任务 `5de7847f-29f5-4762-9f0e-9bd5c98413bd` 的 `qc_unavailable` / `ValueError` 已通过独立 Codex 图片质检通道解决；用户确认平整颈部截面允许后，同一原图重检通过，证据见下节。仍不可将单张图片通过等同于整条生产链验收通过。
- 已实际生成两张图片：肖像与身份三视图。未提交视频，尚未完成端到端出片验收。

## 判断修正及未验收范围

文本网关接口 configured=false 不等于文本任务不可用：本环境使用 Codex 文本运行时，上述真实图谱任务已证明可执行。此前将网关状态直接解释为文本流程阻断的判断已撤回。

已验证真实场次解析、导演规划激活、角色构建、肖像生成和 Codex 身份图重检。尚未验证渲染、视频生成、整集合成与批量重入的完整真实链路，也未验收声画同步、供应商费用与远端取消。不应据此宣称生产可用或完整验收通过。

道具策略判断修正（用户确认）：道具库由外部导入，规划只把已有道具与分镜关联，禁止为分镜自动创建道具，避免道具膨胀。因此空库返回零关联不构成「未自动建库」缺陷，撤回此前要求补建道具的建议。后续只验证已有道具匹配、别名复用、重复规划不新增实体，以及必要引用缺失时是否明确报告；不把实际尚未验证的提示问题写成已确认缺陷。测试中的旧油灯需按外部导入工作流准备。

## 2026-09-15 原图质检真实验收

- 确认两个项目均无活动任务后，按用户指定环境重启 API，当前进程 PID 71740；未修改 `bei` 项目。
- 异步原图重检任务 `928a1459-a0a8-408d-a8bf-c54392b15b0c` 于 06:18:31–06:18:52 UTC 执行完成，实际路由 `identity_sheet_qc / codex / gpt-5.6-sol / low / fallback=stop`。
- 运行中第二次独立 POST 返回相同 task_id 和 `reused=true`，没有重复调用或重新生图。完成后普通 POST 返回 409，要求显式 `retry=true`，避免失败结果触发隐式循环消耗。
- 报告 `technical_error=null`，阻断项 `front_face_detected`，建议项 `style_mismatch`；版本继续为 candidate。CLI wait 返回非零退出码，未将“任务完成”误报为“质量通过”。
- 视觉核对：正面图没有头或多余人脸，但存在平整颈部截面。现行提示词把 exposed anatomy 也归入 front_face_detected；这说明错误码语义过宽，并留下是否允许无血腥颈部截面的验收政策问题。未擅自放行，已询问用户。
- 原版本 PNG 的执行时摘要与完成后磁盘摘要一致：`69ccb2edf68642d4aab561563a7e9a67dbf085d3684844df978f30bc250e7666`。历史包含原 generation 的 qc_unavailable 报告及本次真实报告，没有覆盖旧证据。仅本次新增一条质检任务，未调用图片生成。
- 独立规格/质量复核发现并修复：排队期间风格家族未冻结、旧失败缓存阻断运行中重试复用、部分暂存写入失败遗留文件。复核闭环通过。
- 父级虚拟环境执行 `tests/character_visual`、`tests/test_identity_qc_image_runtime.py`、`tests/test_text_task_runtime_routing.py`、`tests/production_workflow`、`tests/test_character_state_assets.py`：**231 passed，8 warnings**。新增相关文件 Ruff 与 `git diff --check` 通过。
- CLI 文档与 `nuomi-production` Skill 已补原图重检、显式 retry、CE 无登录与外部道具库只关联约束；Skill 结构验证通过。通过后首次候选自动采纳与成功缓存复用目前是自动化测试证据，尚无真实通过样本，不冒充实机通过。

### 用户确认颈部截面政策后的复测

用户明确允许无头正面图的平整颈部截面：全身参考提供服装和体态，面部细节使用脸部特写，避免小头干扰。生成与 QC 提示词同步允许无血腥平整截面及颈部皮肤；仍禁止多余头脸、伤口、血腥、露骨及内部组织。未关闭其他结构检查。

- 新测试先失败，再修改提示词及旧政策断言；相关测试 **141 passed，8 warnings**，Ruff 与 diff 检查通过。
- 实际 API 按指定环境重启，进程 PID 76636。原图重检任务 `80f04d5e-1397-4348-8f5a-e135762c30bb` 于 06:24:07–06:24:19 UTC 完成，`qc_passed=true`、`front_face_detected=false`、`blocking_issues=[]`；保留 `style_mismatch` 建议。
- 首次合格候选自动变为 `provisional`，没有标记为人工确认。版本原图及 canonical 采用图摘要均为 `69ccb2edf68642d4aab561563a7e9a67dbf085d3684844df978f30bc250e7666`，没有重新生图。
- 完成后再次独立 POST 返回 `reused=true` 和合格报告，不产生新任务。由此补齐上一节尚缺的首次自动采纳与成功缓存复用实机证据。
- 本次只验证这张参考图及重检流程，场景规划、视频生成与整集合成仍未通过完整生产验收。

## 后续 E1 验收及资产来源约束

用户明确先完成灯塔验收项目 E1（3 镜头、计划 10 秒），不是 `bei` 第 1 集；本轮付费上限 50 元。场景也必须来自外部场景表，允许基于导入基础场景生成必要变体；不允许临时扩建基础场景库。

- 室外词表回归发现具体灯塔、礁石、海面、砂土描述被检查误拒。补充室外实体、固定光源和材质词汇后，场景质量测试 **29 passed，8 warnings**，原有空泛和剧情污染检查仍保留。真实场景规划 `3d93186d-c46c-4d63-9860-c70c9b15dab9` 在新资产约束确认后主动取消，不记为真实规划通过。
- 取消后 GET 核对仍只有原空白「海边灯塔」记录，无主参考图，未新增基础场景。缺少外部场景表及「旧油灯」道具来源，已请求提供素材路径或授权固定验收导入夹具。未继续付费生图或视频生成。
- 渲染引用预览还报告将「发现塔灯」「停步仰望」作为必需角色状态，以及重复的塔灯场景变体；需继续审查规划的资产粒度与复用逻辑，不能通过补生成所有变体掩盖资产膨胀。
- CLI 通用 request 不支持含查询参数的任务取消路径，返回参数错误。本次使用同一项目 HTTP DELETE API 成功取消；该 CLI 功能缺口尚未修复。
- L02：默认位置、历史保存位置、拖动和窗口尺寸变化均限制在导航下方，动画向上溢出也裁剪在导航边界之外。L03：Vite 保持默认工作区访问范围，仅额外允许解析后 Inter 字体包的真实目录，未放行整个 node_modules 或仓库父目录。
- 前端相关 **7 项测试通过**；TypeScript 检查通过。独立 Chrome 实际点击「剧本导入」进入 `/ingest`，无字体 403；仍观察到默认头像接口 404。截图 `/private/tmp/nuomi-acceptance-bjqAJw/navigation-fixed.png`，未重新导入剧本或修改 `bei`。
- E1 成片仍未生成，不能宣称完整出片验收通过。

### 固定夹具导入后继续 E1

用户授权仅为本次验收准备固定灯塔场景与油灯导入夹具。`/private/tmp/nuomi-acceptance-bjqAJw/fixture-scene.json` 已通过 PATCH 导入原空白场景，`fixture-prop.json` 通过 POST 导入单个「旧油灯」，notes 明确记录验收来源。中断恢复先 GET 检查，未重复创建。

- 灯塔参考图任务 `5f6b86d8-e9fd-4ac0-a07d-40571313de51` 完成，版本 `master-20260915T064807403523Z`，provisional。已视觉检查灯塔、石阶与海岸布局。
- 油灯首次任务 `f7a86cf1-6f88-41c5-91a6-fad49a8a8649` 在本地失败：空请求体的 `model=None` 被字符串化为 `"None"`。回归先失败后修复为默认空值；显式模型重试 `6ba5fc63-6b4e-40f6-a820-39a107a64dbb` 完成，版本 `prop-reference-20260915T064837357236Z`，provisional，已视觉检查。前一次未进入供应商调用。
- 导演提示词补充动作/表情不新增角色状态、镜头变化不新增基景的约束。真实任务 `45b0bf0c-002a-4d35-8393-8740be76e817` 生成并激活 `01M2HXAFEPDKV790SCFSF0WZ7X`：3 镜头、10 秒，全部只引用林舟、海边灯塔、旧油灯。未通过改写通过标志绕过验证。资产 API 与导演提示词测试 **53 passed**。
- 身份绑定任务 `c58c9ef2-b737-44f9-a9a3-eb65117c2771` 新增 0、复用 1；场景任务 `7ebaee3a-36e4-4672-bd0f-e3bc62051a5d` 新增 0、总计 1；道具任务 `2e46ded0-bf6f-42a2-901c-38b470ade15f` 关联已有油灯。三类引用全部 ready。身份规划日志还报告 Ladybug 版本映射异常（非致命），图谱运行环境风险仍需审计。
- CLI 首次实图任务 `acd09df8-1163-4dc5-b372-6648b66f86c9` 在模型调用前失败：`REFERENCE_SNAPSHOT_INVALID`。API 把原文行 ID 放进允许范围，但绑定快照使用戏剧节拍 ID，造成合法引用被拒。现从同一冻结导演版本提取戏剧节拍与镜头范围，保留所有路径、摘要和项目校验。API **135 passed**，执行器 **18 passed**。
- 已按实际数据目录重启并用 `batch --episodes 1 --through render --max-submissions 1 --retry-failed` 显式重试一次，任务 `faf0b20d-4f4d-4a00-ad3d-649a4ae84999`。本记录写入时仍在等待实图，不把提交成功记为成片完成。

### 实图切分恢复与视频衔接审计

- 上述实图已生成，但四格布局只有三条镜头映射，切分时访问第四个镜头编号触发 IndexError；异常又导致已付费图片引用丢失。新增回归复现两种失败，修复空余格不提升为镜头、切分异常返回带原图的 partial_failure，供 CLI 仅重试切分。相关执行器测试 **20 passed**。
- 固定恢复脚本 `recover_grid.py` 仅接受灯塔 E1 render revision 2 和明确的已生成文件，检查图片可读，调用既有状态服务记录恢复事实并运行本地 split runner；未再调用供应商。实际产出 `frames/ep001/beat_01.png` 至 `beat_03.png`，均为 720×1280。原供应商单格约 459×816，存在放大降级，不宣称原生 720p。
- 目视实图呈黄昏而非原剧本夜景；属于尚未通过的画面质量项。第四格没有映射到镜头，已排除。测试视频可用于链路验证，不能以此宣称夜景创作质量通过。
- CLI 在视频付费前发现空推荐计划。根因为激活不同结构导演方案时丢弃新推导的视频计划；修复保留新计划，并恢复同结构历史空计划，保留已有媒体与用户手工计划。
- 下一次提交在 API 校验前被拒：规划器允许三个连续镜头合为一个片段，但现有执行器只支持单帧或首尾双帧。规划器限制连续合并最多两镜，推荐计划自动迁移，手工计划不自动覆盖。灯塔 E1 保留三镜、计划总时长 10 秒，拆为前两镜 + 第三镜。
- 视频规划/执行器测试 **117 passed**；API/批量 CLI 测试 **154 passed**。存在 8 条依赖弃用警告；不代表全仓测试通过。
- 07:21 UTC 后真实提交视频任务 `19bee673-95e2-4cde-bf36-aeae4126f8c2`；此前空计划及 422 阻断均未提交视频供应商任务。实际结果待核验。
- 该视频任务在提示词编译时失败：H3BaseWire 要求至少 4 秒，第三镜为 3.5 秒。H3 片段构建现在在快照和提示词之前固定至少 4 秒的生成时长，原导演方案不改写；这意味着不能承诺输出恰好 10 秒，须核对实际成片。执行器/API **230 passed**。显式重试任务 `3f021306-97fd-4bf5-b2b2-6b74a0635f6e` 正在等待。

### 扩大回归后的风险分类

- 默认全仓首次运行 `pytest -q --maxfail=5`：**5195 passed, 5 failed, 5 skipped, 2 deselected**，提前停止，非全量通过。
- 发布 wheel 测试失败原因为沙箱禁止 uv 访问用户缓存；授权重跑同一单项 **1 passed**，未更改打包代码。
- 原文新鲜度两个测试使用空 scenes/beats，触发已有“禁止激活空语义结果”规则，未实际到达新鲜度断言。改为有效场景/节拍夹具；原文新鲜度与语义存储 **20 passed**，生产保护规则未放宽。
- 许可证生成器无法解析锁定版本 `antlr4-python3-runtime 4.9.3` 的本地许可证证据；库存校验发现 836 个当前 Git 跟踪文件未列入清单。这三项失败仍未关闭，不能把运行时成功等同于可发布合规通过。
- 继续全仓默认集合，仅用 `-k` 排除上述三个已知许可证失败及已单独通过的 wheel 测试：**5784 passed, 16 skipped, 6 deselected, 87 warnings**（6 deselected 包含默认 EE/E2E 排除）。没有额外测试失败。存在依赖弃用与多线程进程 fork 警告，未宣称清零。
- 代码复核另发现两个未关闭的功能边界：旧实图组入口未采用新 generation_batches 三联图布局；场景编译器仍可从缺失的规范场次地点创建基景，尚未全局强制“外部导入基景，仅允许变体”。本次灯塔已导入固定基景，未走自动扩库路径；不能据此宣称全项目已满足外部场景库政策。

### H3 提示词自动修复无效

- 视频 revision 2 任务 `3f021306-97fd-4bf5-b2b2-6b74a0635f6e` 于 07:32:38 UTC 失败，`H3_PROMPT_QUALITY_REJECTED / physics_incomplete`，明确 `transport_called=false`。未生成视频。manifest 有错误报告但不保留失败候选全文，诊断证据保存仍需完善。
- 代码发现真实缺陷：episode pack 和单片段 optimizer 都用 `fill_empty_fields(previous, candidate)` 合并质量修复，原有非空错误 physics statements 永远覆盖模型修正结果。新增两条测试均先复现失败。
- 修复仅针对报告明确标记 `physics_incomplete/physics_required` 的 `rigid_prompt.physics.statements`：允许替换该字段，其他非空事实继续保留，修复后仍运行完整质量门。未降低物理维度检查，保留禁止覆盖风格事实测试。三组提示词测试 **221 passed**，Ruff 与 diff 检查通过。
- 重新启动 API 后显式重试任务 `85977ba4-f54d-483f-a3b3-6c7cc85c4e61`，仍复用实图，等待实际视频及成片。

### 原文事实、镜头规范和取消传播

- revision 3 视频任务最终在 08:03:16 UTC 失败：模型输出 `Static camera` 被当作动态镜头，因无 direction 触发结构校验；未进入视频供应商调用。现仅规范大小写、空白及明确的 static/fixed/locked camera 别名，真正动态镜头仍要求方向。
- 该任务 08:00:06 UTC 收到取消请求，却继续运行约三分钟。执行器补接共享取消 watcher，并保留冻结输入和付费产物证据；只更新相同 revision 的运行中阶段，不覆盖新版本。包含受控 Codex 进程终止的回归测试独立复跑 **136 passed**；真实运行中取消仍待再次验证。
- 发现导演镜头投影只携带 dialogue_source_ids，没有传递台词正文和场次时间。现从导演所绑定的语义版本解析原文台词与说话者，缺失来源立即失败，并把场景和时间写入生成描述。真实灯塔数据只读核对：shot-2 为林舟「灯还亮着。」，三镜均为夜；没有编造台词。导演/API/视频模型相关回归 **591 passed**。
- 按原数据目录重启加载修复，08:15 UTC 后提交夜景分镜任务 `803f689a-8310-4842-905f-2100c5123371`，复用原角色、灯塔、油灯引用，不扩库。此次重绘是修正原文夜景偏差，并非重买已成功切分的图片。结果待实际核验。
- 合成还需核验音轨选择：现有 H3 默认 external_tts，但验收项目尚无独立 TTS 文件；不能把缺少台词的静音文件作为完整 E1。须验证供应商原生语音或补齐正确的配音链路。
- 夜景实图任务完成，产物 `group-e1fe7595ab1e5a062f4b_batch-e3b0c44298fc1c149afb_render_r2_72544_20260915161725976083.png` 已目视检查夜空、塔灯和油灯，三张镜头成功切分；黑色补位格不作为镜头。实际单格仍由约 459×816 放大，cleanup_reports 明确 degraded=true；顶层 provider_parameters 却为 false，存在质量元数据汇总不一致，不能宣称原生 720p。
- 本次误用了 generate 而非 regenerate，API 在相同 render revision 2 重新生成；独立原网格文件仍在，但 canonical 帧被正常更新。该入口未自动增加 revision、下游失效语义仍有风险；本次旧视频全为失败，没有复用错误的已完成视频。
- CLI 复用夜景帧并提交视频任务 `4c5efe52-d9d9-4bbf-bf2c-58422edb9410`；等待真实产物。
- 加载全部上述修复后的扩大回归，沿用明确排除的四项测试：**5805 passed, 16 skipped, 6 deselected, 88 warnings**。新增 aiosqlite worker 的 Event loop is closed 警告，需继续定位连接关闭边界；测试退出码 0 不意味着这些运行风险消失。相关文件 Ruff 和 diff 检查通过。

### 首次进入视频供应商与附加功能修复

- 视频 revision 4 manifest 已记录 `status=submitted`，供应商任务号 `2099778726551318529`，源片段包含林舟原台词「灯还亮着。」；首次实际进入视频生成，尚未收到可验收视频。当前音轨模式仍是 external_tts，后续需选择并核验真实音轨。
- 图片降级误报已定位：retry_split 丢失 upscaled/degraded 字段，后续 generation_metadata 又可用 false 覆盖切分结果。回归先失败，修复保留字段并用逻辑或合并降级证据；两组执行器测试 **21 passed**。未为修复历史报告重新购买图片。
- 外部基景策略已收紧三个入口：规范场次、旧 AI 校对、解说 fallback，均先匹配已导入名称或别名，缺失即抛 `BASE_SCENE_IMPORT_REQUIRED`，不自动创建基景；保留已存在基景的描述补齐及必要变体。派生场景不能冒充基景。
- 任务错误序列化补缺失场景名称和集数，可供 CLI 自动识别导入阻断。回归先失败，场景、道具、绑定和错误映射 **130 passed**。此变更意味着空场景库不能再直接规划；不追溯判定已有旧库的来源。该补丁尚未加载到正在执行付费任务的后端，不为加载新代码中断供应商任务。
- 完成图像再次 generate 沿用 revision 的问题已修复：图像入口 opt-in，sidecar 锁内将 completed 转为新 revision，保存旧网格历史并标记下游过期；failed/queued/running 和 split-only 保持原版本。先复现 sketch/render 两项版本错误，独立复跑 API、服务和图像执行器 **199 passed**。该修复同样待当前付费视频任务结束后加载。
- 08:39 UTC 的只读媒体任务库显示供应商状态 running、error_code 为空；仍为同一任务号，没有额外重发。尚无可验收 MP4。
- 08:40:16 UTC 第一片段成功下载：`videos/ep001/narrative_groups/group-1_r4_segment_001.mp4`，6.584 秒、736×1280、24 fps、AAC 32 kHz 双声道，约 1.67 MB。抽帧已检查上阶、转身和仰望动作，夜景保持；音量检测均值 -25.8 dB、峰值 -2.2 dB，不是静音，但尚不能证明台词准确。第二片段供应商号 `2099780329710768130`，正在运行。
- 全部最新代码扩大回归（同样明确排除既知四项）：**5823 passed, 16 skipped, 6 deselected, 87 warnings**，未新增失败；上一轮 aiosqlite 关闭警告本次未重现，不视为已修复。

### 两段付费视频完成后，本地合成验收仍失败

- 第二段真实视频已下载：`group-1_r4_segment_002.mp4`，4.459 秒、896×1184、24 fps、AAC 双声道。抽帧可见仰望与衣角飘动。其画幅与第一段 736×1280 不一致，旧合成器直接 concat 导致任务 08:49:17 UTC 失败；没有新增供应商调用。
- 修复合成前等比缩放与补边、统一 SAR/fps/timebase；主执行器按 H3 设置传入目标画布。真实合成夹具以及执行器相关回归 **99 passed**。不拉伸人物，但非目标比例素材会留边，不能宣称画幅质量完全达标。
- 固定恢复脚本 `recover_video_local.py` 仅接受 revision 4、两条完成的供应商 attempt 和一致的 durable 视频路径，并备份原 manifest。实际恢复仍在 FFmpeg 最终阶段失败；生成的 `group-1_r4_recovered.mp4` 是部分失败产物，未更新 manifest、未登记成功。继续定位完整 stderr，不能把可读的部分 MP4 当成完成。
- 免费本地 Whisper tiny 转写第一段为「灯海亮着」(0–1.24 秒)，base 为「都还亮着」(2.08–3.14 秒)，均与原台词「灯还亮着。」不同，且时间不一致。仅证明存在接近原文的语音；尚未通过字词准确及口型同步验收。不以小模型转写直接断言供应商确实说错，也不宣称语音验收通过。
- 完整 stderr 证实恢复失败为滤镜消费者 `No space left on device`，磁盘仍有 577 GB 空闲。只将无界 apad 改为按总时长有限补音并裁剪，相同真实输入退出 0；新增音频流可终止回归，相关测试 **100 passed**。错误级日志避免真正错误被编码统计淹没。
- 08:59 UTC `recover_video_local.py` 生成 `group-1_r4_recovered_v2.mp4`：**11.041667 秒，736×1280，有音轨，全片 ffmpeg -xerror 解码通过**。通过既有服务记录实际完成与原生音轨偏好，没有设置任何质量通过标志；原两段、失败部分视频和备份 manifest 均保留。媒体 GET Range 返回 206、video/mp4、有效 Content-Range。HEAD 405 是现有 GET-only 接口行为。
- 正式 `batch --through compose --max-submissions 1` 确认 render/video 都 reused，但整集合成 API 返回 `No beats found for episode 1`；纯导演方案未创建旧 SQLite beats，不应为兼容而伪造数据。正在补纯导演完整性及版本快照校验后允许其入队，未再次生成媒体。

## 灯塔 E1 正式成片结果（2026-09-15 09:12 UTC）

- 纯导演合成入口已修复：无旧 beats 时核验活动导演组完整性，冻结导演 revision、组 ID 和视频来源；运行开始与原子发布前重核，缺组、过期或变更均阻断。没有创建旧 beats。六项新回归先失败，独立相关测试 **30 passed**。
- 正式 CLI `batch --episodes 1 --through compose --max-submissions 1 --task-timeout 120` 返回 **ok=true、episode completed、submissions=1**，render/video 均 reused。合成任务 `485ca30c-b225-4583-ae29-bb0eb80b7225`，仅本地合成。
- 成片：`/Users/liuyuxiang05/Liu/nuomi-drama-data/output/local/acceptance_lighthouse_20260915/videos/episodes/ep001_final.mp4`。
- 下载：`http://127.0.0.1:8780/static/projects/01M2HPJFW5HGXYC8F9C94ZKWZS/videos/episodes/ep001_final.mp4`。
- FFprobe：**11.040 秒、720×1280、24 fps、H.264 + AAC 44.1 kHz 双声道、2,282,014 字节**；全片 `ffmpeg -xerror` 解码通过，HTTP Range GET 206。
- Browser 插件被本机 trusted RPC 路径配置阻断，未修其信任配置；使用独立无头 Chrome。直接导航 MP4 的加载检查未完成，改为前端同源测试页面内的 video 元素；实际播放至 ended=true、currentTime=duration=11.04、error=null。截图 `/private/tmp/nuomi-acceptance-bjqAJw/e1-playback-ended.png`。这是浏览器媒体播放证据，不等于软件 Compose 页全功能验收；项目路由早期截图仍显示项目列表，需另查页面加载/路由交互。
- 最新扩大回归 **5835 passed, 16 skipped, 6 deselected, 87 warnings**；仍明确排除三项已知许可证测试和已单独验证的 wheel 测试，不能宣称默认全仓零失败。相关改动 Ruff 与 diff 检查通过。
- 当前服务：API 8780 PID 14834，前端 5173 PID 12235；后端使用用户指定 CE 数据/状态目录，已加载本轮修复。

### 结论边界

灯塔 E1 已有完整技术成片，且正式 CLI 完成最终合成；不能据此宣称整个项目已达到无人值守生产标准。本次仍使用了限定项目的付费产物恢复脚本，通用 CLI 的“视频已完成但本地后处理失败”免重购恢复尚需产品化；默认 external_tts 与纯导演原生音轨选择也未自动贯通。台词逐字准确性/口型同步、第二段比例留边、镜头清晰度放大，以及许可证、部分 UI 和进度可观测性风险仍未关闭。没有修改 `bei`，没有把上述质量项强制标为通过；供应商人民币实际账单未核实，不宣称精确费用。
