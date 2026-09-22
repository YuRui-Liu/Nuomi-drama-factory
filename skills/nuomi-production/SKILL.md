---
name: nuomi-production
description: 使用 Nuomi 项目的 CLI 批量制作漫剧剧集，自动执行导演方案、实图、视频与合成，复用已有素材并处理异常。适用于从已导入剧本和项目资产连续生产多集视频、续跑中断批次或补齐失败组。
---

# Nuomi 批量漫剧制作

目标是连续交付多集成片。优先使用 `batch`，不要把批量任务拆成逐组询问用户的交互流程。正常检查通过就继续；用户已授权的剧集范围、风格和生产操作无需重复确认。

## 入口与前提

在 Nuomi-drama-factory 仓库执行：

```bash
.venv/bin/python -m novelvideo.production_cli --help
```

安装项目后，也可以用 `nuomi`；兼容入口为 `novelvideo production`。优先选独立的 `nuomi` 或 Python 模块入口，避免加载 Cognee 的启动开销。

- `NUOMI_API_URL`：包含 `/api/v1` 的 API 根地址，默认 `http://127.0.0.1:8780/api/v1`。
- `NUOMI_PROJECT`：项目 ID。
- CE 本机无需登录或 token；受保护部署使用已有 `NUOMI_TOKEN` 或 `NUOMI_SESSION`，scope 必须对应目标项目。长期外部 agent key 不能直接替代 session token。
- 不把凭据写进命令参数、脚本、请求文件或日志。
- 剧本必须已导入；角色、场景和道具引用需要有可用资产。需要补齐时按下文处理，不要求用户逐张点击确认。

## 默认批量流程

例如用户要求生产第 1～10 集，执行：

```bash
.venv/bin/python -m novelvideo.production_cli batch --episodes 1-10
```

CLI 自动完成：

1. 复用已激活的导演方案；没有激活方案时，自动激活机器校验通过的候选。没有方案时提交导演规划并等待。
2. 逐组复用完成且未过期的媒体；排队或运行中的组接续已有 task_id。
3. 自动选择 ready 的必需引用和默认引用，携带实时 reference_revision 生成实图。
4. 使用服务端推荐视频方案和保存的模型设置生成视频，携带当前版本号，等待任务并检查产物。
5. 从当前片段重新合成各集成片，汇总可下载 URL。

默认不生成草图。用户要求构图预演时加 `--with-sketch`。画幅默认跟随项目横竖方向；可以显式指定 `--aspect-ratio 9:16` 或 `16:9`。

```bash
# 连续集与离散集混合；只做到实图阶段
.venv/bin/python -m novelvideo.production_cli batch --episodes 1-5,8 --through render

# 限制本批提交规模；这是 API 提交次数上限，不是货币预算
.venv/bin/python -m novelvideo.production_cli batch --episodes 1-10 --max-submissions 30

# 无网络预览：只列集号、阶段和上限，不声称完成资产检查或费用估算
.venv/bin/python -m novelvideo.production_cli --dry-run batch --episodes 1-10
```

批量输出是 JSONL：过程事件逐行输出，最后一行为批次汇总。退出码 0 表示所选阶段全部完成；1 表示存在失败、阻断或超时。以汇总中的 `episodes[].status`、`groups[].error` 和 `video_url` 判断交付，不把队列提交成功当成视频完成。

## 自动处理异常

- 一组失败不影响其他独立组、其他集继续生产；有失败组的集不合成残缺成片。
- 图已生成但切分失败时，CLI 自动仅重试切分，不重新购买生成。
- 先前失败的付费任务默认不自动重发。查清原因并修复后，使用 `--retry-failed`，每个失败阶段在本次批次内最多尝试一次。
- `submission_unknown`：提交响应丢失，先 `status` 查任务并接续已有任务。不要立即重复提交。
- `missing_required_assets` / `reference_limit`：读取对应组的引用预览，补齐缺失资产或调整可选引用；保留所有必需的身份和场景约束。
- `plan_invalid`：读取导演方案的 validation_report，按结构化问题修复或重新规划；不要把失败报告改成通过。
- `qc_unavailable`：视觉审查服务故障，不等于图片质量差。不为此反复重生成图片。
- 身份图可通过 `request characters/角色名/identities/身份ID/versions/版本ID/recheck --method POST --json '{}'` 对原图重检。返回 task_id 则 wait；返回 reused 和 data 则复用已有报告。已失败的重检需修复原因后显式传 `{"retry":true}`，不要用重新生成图片代替重检。
- `401/403`：会话过期或项目 scope 不匹配，处理认证后续跑；不自动切换项目或账号。
- 每个项目同时使用一个批量协调进程；CLI 不是跨客户端的分布式锁。

只有遇到原文缺失、集号冲突、用户创作意图不明确或需要超出已授权范围时，才集中询问。可由现有剧本、设定和机器检查确定的常规选择由代理完成。

## 剧本、素材与单步工具

导入已有剧本：`import-preview path/to/script.txt`。可以导入包含多集的文件。根据返回的 `preview_id`、`base_revision` 和 `files`，通过 `request episode-imports/commit --method POST --body-file commit.json` 提交 `expected_revision` 和 resolutions；新集用 `action: import`，已有集不擅自 overwrite。已有剧本不能走小说改写入口。

使用 `request` 调用项目内 API。路径相对 `projects/{project}`，不带开头 `/`，名称使用原始文本，CLI 负责 URL 编码：

```bash
.venv/bin/python -m novelvideo.production_cli status
.venv/bin/python -m novelvideo.production_cli groups --episode 1
.venv/bin/python -m novelvideo.production_cli request episodes/1/narrative-groups/ng-01/render/references
.venv/bin/python -m novelvideo.production_cli request characters
.venv/bin/python -m novelvideo.production_cli request characters/林默/portrait-async --method POST --body-file portrait.json
.venv/bin/python -m novelvideo.production_cli wait TASK_ID
```

按实际响应中的 ID 和版本号构造请求，不照抄示例 ID。单步生成用 `generate --episode N --group ID --body-file request.json`；视频加 `--stage video`；合成用 `compose --episode N`。

缺少角色素材时，先从剧本与视觉设定建立 VisualBible，选择机器检查通过的设计方案，再通过现有 confirm API 确认并生成 Portrait 和身份图。代理可以完成这些选择和确认，不能伪造来源事实或将图片审查标志强改为通过。观感类 QC 警告不是必须送人审的条件；明显的人脸、身体、身份缺陷仍需修复。

道具库由外部导入，规划只关联已有道具与分镜，不自动创建道具。空库返回零关联不是自动建库信号；缺少必需道具时报告需要导入或选择已有资产，不自行扩库。

遇到具体资产或导入请求参数不清楚时，读取仓库中的维护源：

- `src/novelvideo/api/schemas.py`：导入、素材和合成请求模型。
- `src/novelvideo/api/routes/characters.py`：visual-workspace、确认、Portrait 和身份图生成。
- `src/novelvideo/api/routes/episodes.py`：单集身份、场景、道具规划。
- `src/novelvideo/api/routes/narrative_groups.py`：引用预览、视频设置和版本约束。

完成时报告成功集、下载地址和仍受阻的集及原因；无需逐个复述成功的中间步骤。
