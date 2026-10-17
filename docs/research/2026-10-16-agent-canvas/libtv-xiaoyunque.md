# LibTV 与小云雀：Nuomi Agent 目录和剧本交接画布研究

检索日期：2026-10-16。范围仅为公开的一手资料：LibTV 仓库当前 main 的代码和文档；小云雀官方网站的产品手册、教程目录。未登录、未调用生成服务、未更改产品。下文区分源码可见、官方手册描述、营销性说法和设计推断。

## 核查结论

LibTV 公开的是一个“单一宽触发 Skill → 后端 Agent 对话”的薄接入层，适合借鉴任务交接、上传、会话轮询和结果取回；它没有公开剧本编辑器、Agent 角色编排或版本模型。小云雀官方资料展示了短剧专用的分阶段流程和资产画布：剧本 → 全局角色/场景资产 → 分集分镜 → 预演/视频 → 合成导出；画布节点及引用关系承担跨阶段上下文。对 Nuomi，值得适配的是显式内容与任务交接、可编辑资产及可回溯版本；不要把 LibTV 的聊天会话等同于剧本项目或完整任务状态。

## LibTV：已检查的仓库代码

检查的仓库版本为 main HEAD `c609246c1eca69f6bc129bcbb5d64c36734e4a4a`，提交日 2026-03-18（`feat: add download_results script and update docs`）；GitHub 远端未返回 tag refs。代码清单只有一个 Skill 和五个 Python 脚本，未发现后端 Agent 实现、剧本 schema 或测试。可复查：[固定提交](https://github.com/libtv-labs/libtv-skills/tree/c609246c1eca69f6bc129bcbb5d64c36734e4a4a)、[SKILL.md](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md#L1)、[公共 API 封装 _common.py](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/_common.py#L73)、[创建会话](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/create_session.py#L46)、[查会话](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/query_session.py#L28)、[上传](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/upload_file.py#L20)、[下载](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/download_results.py#L17)。

**交互和 Agent 配置（源码可见）。** Skill manifest 只有 `name`、宽泛的 `description`、OpenClaw 的 Python/环境变量要求；`LIBTV_ACCESS_KEY` 为必填，`OPENAPI_IM_BASE`/`IM_BASE_URL` 可选。创建/续用会话向 `POST /openapi/session` 传可选的 `sessionId`、`message`，响应给 `projectUuid`、`sessionId`；GET 查询按会话返回消息，支持 `afterSeq` 增量读取；换项目会改变 accessKey 当前绑定项目（见 [_common.py 的 API 函数](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/_common.py#L73)）。代码没有 Agent 角色列表、角色配置、模型选择或任务类型参数；“后端 Agent 自主编排”是 [SKILL 文档](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md#L26)描述，不是本仓库中可审查的后端实现。

**两个公开调用示例（未实跑）。** ① 生视频：[README 示例](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/README.md#L61)用“生一个动漫视频”创建会话；[SKILL 轮询说明](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md#L121)规定每 8 秒按 `afterSeq` 查询，看到 assistant 结果 URL 后批量下载，返回画布链接。② 编辑现有视频：先 [上传](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/upload_file.py#L20) 得 OSS URL，再把“把四周的纸船都换成白色的纸爱心”及 URL 原样发进新会话，随后查询/下载；[SKILL 示例](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md#L133)说明此消息交接方式。技能还举了“推演后续故事、生成 16:9 九宫格”并附参考图的例子。以上是可见脚本与技能说明的调用路径，不代表本次实际调用了服务。

**异步、失败、版本/来源。** Python 脚本以同步 HTTP 请求包装服务（通用请求超时 30 秒、上传 120 秒）；异步进度来自对话消息轮询，没有可见的 job/task ID 或标准 queued/running/failed 状态。[SKILL 轮询段](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md#L158)规定 8 秒轮询、3 分钟后停止、单次失败最多再试 1 次、连续失败 3 次停止；这是 Agent 操作说明，不是脚本实现的重试机制。[下载脚本](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/download_results.py#L17)从 tool 消息的 `task_result.images/videos` 或 assistant 文本提取 URL，并发下载，返回逐文件错误。可见标识只有 project/session/message（示例含 message id）和增量 seq；没发现生成版本 ID、提示词快照、资产到镜头的来源映射。项目切换是 accessKey 级当前绑定，潜在并行隔离风险是推断，服务端行为未公开。

**值得核实的文档差异。** [README API 表](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/README.md#L199)把上传写成 `/openapi/file/upload`，实际[上传脚本](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/scripts/upload_file.py#L62)请求 `/openapi/upload`。SKILL 声称图片/视频上传上限 200MB，但客户端未执行大小校验；MIME 无法识别时会以 octet-stream 上传。应以服务端契约验证，不直接把说明当 API 保证。

## 小云雀：官方手册描绘的工作流

来源为[Web 产品手册（短剧流程 §1.3）](https://xyq.jianying.com/tutorials/web-manual)、[剧本助手（阶段定稿与版本记录）](https://xyq.jianying.com/tutorials/short-drama-agent-script-assistant)、[短剧资产画布（节点、连线、资产库）](https://xyq.jianying.com/tutorials/short-drama-agent-canvas)、[智能预演指南](https://xyq.jianying.com/tutorials/short-drama-agent-smart-preview)、[创作 Agent 画布](https://xyq.jianying.com/tutorials/creation-agent-canvas)和[官方教程目录](https://xyq.jianying.com/tutorials)。教程页未标产品版本；创作画布手册说明部分实测截图摄于 2026-09-23—24，因此此处按 2026-10-16 可见文档描述，不推定后端/API版本。

**Agent/工具目录。** 官方目录把短剧工作流拆成剧本助手、资产创作画布、3D 导演台、智能预演、重制转绘；通用创作 Agent 的对话画布是另一种工作区，营销 Agent 又是独立入口。这是用户可见的能力目录，不是公开的多 Agent 配置表。短剧手册公开了项目名、画风、比例等全局设定；通用 Agent 文档声称可根据需求规划路径/调度模型，但没有披露路由规则、角色提示词或配置接口。“专业/行业最强模型”“降低抽卡率”等属于产品宣传或体验主张，不能由手册验证算法效果。

**主流程（官方操作指南）。** ① 输入：上传 TXT/DOCX 或粘贴，也可要求 AI 起草；文档称最长 10 万字。② 结构化确认：生成概要后确认/编辑，填写项目名、画风、比例，再整理成剧集剧本。③ 资产拆解：自动识别角色、跨时空妆造及出场集数；角色图可上传，角色/音色在资料库修改后会同步到该角色出场处；再生成全剧场景。④ 分集故事板：自动拆镜并关联人物妆造、场景图；用户可增删镜头、手改脚本、用 @ 关联角色/场景；可批量生成多集/多镜头。⑤ 剪辑/导出：截前一镜尾帧给下一镜做首帧参考、加入道具/氛围/站位参考；最后合成并导出全集。手册仅说明“一键导出”，未说明格式、分辨率、导出状态 API 或版本兼容。

**两个具体用例。** ① 长篇剧本交接：标准场次文本导入后，先审概要和全局设定；再检查全局角色/场景库，按需替换主图或音色；分集视图里逐镜增删/改稿并引用资产；批量生成后合成全集。这是手册描述的完整路径。② 多镜头预演：选取提示词已确认且超过 2 个的片段，引用素材不超过 9 个；提交智能预演，关键分镜图自动插回相应片段，用户确认后再出视频。失败重试政策没有公开。另一个可观察的画布机制是角色/场景节点之间连线代表引用上下文；节点可保存为资产库素材并在故事板复用，资产改动做全局联动。通用画布允许并行会话、@历史/画布/角色/商品资产，保留过程和参考依据。

**版本与状态。** 剧本助手官方指南说历史可查看、比较、回退不同阶段版本；资产画布维护角色/场景节点和引用边；通用创作画布把同提示词重复生成结果折叠为一组，可选定版本供后续引用。因此它在产品层面比 LibTV 的“会话消息 + URL”表达出更多来源线索。但公开手册未给稳定的版本/节点 ID、不可变快照语义或剧本版本到分镜/视频的映射规则。后台任务在画布卡片继续运行，手册让用户查看每卡状态/结果；未见开放 job id、轮询接口、失败分类或自动重试约定。重拍/再生成是可见编辑动作，不等同于服务端重试协议。

## 对 Nuomi 的可借鉴点（推断，不是对标结论）

Nuomi 既有已批 PRD 采用统一画布，因此可借鉴小云雀画布中的内容关联、选定版本引用和就近编辑，用资产与镜头关系承接上下文；不应据此恢复阶段门禁，也不应照搬短剧 Agent 的“剧本助手→资产画布→分集故事板→导出”流程。小云雀的短剧 Agent 资产画布与通用创作 Agent 画布是两条不同产品流，不能拼成一个统一产品模型。LibTV 可借鉴的是轻量生成委派边界：传入原始意图与素材引用，通过会话 ID 增量取状态和结果；在 Nuomi 中应挂载于统一画布。长任务仍需有可见状态、重试入口和失败原因。

适配时应超越来源的薄弱处：Nuomi 自身任务状态应有明确 ID/state/error/retry 与版本 lineage；避免仅以“assistant 文本中出现 URL”判完成、依赖 accessKey 的活动项目切换或按下载顺序命名来承载可追溯性。未知项：Nuomi 需要的 Agent 角色/权限/模型配置颗粒度；剧本版本是分支还是线性覆盖；剧本、资产、镜头、生成文件间的稳定 ID 和回滚影响范围；离线失败/重新提交的幂等规则；导出格式及多集交付状态；小云雀的实际并行上限、API 状态字段与服务端重试规则。两家公开资料均不足以回答这些实现问题。

