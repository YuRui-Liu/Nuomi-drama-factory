<!-- lang-switch -->
[English](../../en/getting-started/configuring-models.md) · **简体中文**

# 配置模型供应商

Nuomi Drama Factory CE 通过 NewAPI 兼容网关接入文本、图片、视频、音频和 embedding 模型。可以使用官方 RelayClaw，也可以使用 CE 随附的本地 NewAPI。

## 先选一种接入方式

| 方式 | 适合场景 | 是否需要映射模型 |
|---|---|---|
| 官方渠道 / RelayClaw | 想最快跑通，使用官方预置模型 | 不需要 |
| 本地 NewAPI | 希望在 Nuomi Drama Factory UI 里配置本机 NewAPI 的上游渠道、模型映射、媒体模型和 embedding | 需要在 UI 里保存映射 |

## 配置入口

启动后打开 `http://localhost:8080`，进入设置 → 模型配置。渠道选择、网关地址和 token 都保存在本机 `settings.db`，不从环境变量读取。

启动后打开 `http://localhost:8080`，进入设置 -> 模型配置。

在网页里可以完成：

- 官方渠道：填写 RelayClaw Nuomi Drama Factory key，点击“保存并启用”。官方地址固定。
- 本地 NewAPI：初始化本机 NewAPI、创建或复用 runtime token、配置供应商渠道、保存模型映射。
- 媒体存储：配置 OSS 或 Cloudinary。
- Embedding：配置模型、维度和批量大小。

更换 key、渠道或模型后，新任务会读取新配置；已经运行中的任务不会强制中途切换。

如果启用网页“本地 NewAPI”初始化向导，只需开启 provisioner：

```bash
NEWAPI_PROVISIONER_ENABLED=true
NEWAPI_ADMIN_BASE_URL=http://127.0.0.1:3000
```

CE 固定使用 `${NOVELVIDEO_STATE_DIR}/newapi/one-api.db`，无需配置数据库 DSN、
SQLite 路径或管理员用户名。

## A. 官方渠道 / RelayClaw 推荐

默认 `docker-compose.yml` 已使用官方渠道。启动后：

1. 浏览器打开 `http://localhost:8080`。
2. 进入设置 -> 模型配置 -> 官方渠道。
3. 网关地址默认是 `https://relayclaw.cdnfg.com/v1`。
4. 粘贴你的 Nuomi Drama Factory key，点击“保存并启用”。

RelayClaw 后台已经配置好 Nuomi Drama Factory 需要的逻辑模型名，所以不需要手动映射 `*_MODEL`。

还没有 Nuomi Drama Factory key 时，到 <https://relayclaw.cdnfg.com> 注册 / 购买。

如果你只是更换 Nuomi Drama Factory key，直接在网页里更新并保存即可。

## B. 本地 NewAPI

“本地 NewAPI”适合 CE 单机用户在 Nuomi Drama Factory 设置页里管理：

- NewAPI runtime token。
- 上游供应商渠道和 key。
- 文本、图片、视频、音频模型映射。
- Cognee embedding 模型、维度和批量大小。

### 1. 准备 NewAPI

如果用仓库内置 selfhosted 编排：

```bash
docker compose -f docker-compose.selfhosted.yml up -d --build
```

该编排会启动 `api`、`web` 和内置 `newapi`。NewAPI 后台默认在 `http://localhost:3000`。

`docker-compose.selfhosted.yml` 会把同一个 `newapi-data` 卷分别挂载到 NewAPI 的
`/data` 和 Nuomi Drama Factory API 的 `/newapi-data`。初始化向导会调用 NewAPI `/api/setup`
创建 root 管理员，再创建或复用 runtime token；用户无需进入 NewAPI 后台复制令牌。

### 2. 初始化配置

进入设置 -> 模型配置 -> 本地 NewAPI：

1. 如果 NewAPI 尚未初始化，为 root 管理员设置首次初始化密码。
2. 点击“初始化本地 NewAPI”。

初始化按钮会做这些事：

- 如果 NewAPI 没初始化，调用 NewAPI `/api/setup` 创建管理员。
- 如果 NewAPI 已初始化，会跳过管理员创建；你填的初始化密码不会修改已有管理员密码。
- 创建或复用名为 `dramaclaw-ce-runtime` 的 runtime token；这是必须原样保留的内部兼容名称。
- 把 runtime token 写入 Nuomi Drama Factory 本地配置数据库。
- 将模型网关模式切换为 `custom`。

管理员密码只用于首次初始化 NewAPI。Nuomi Drama Factory 不保存这个密码，也不会用它做后续管理操作。初始化完成后请自行保存 NewAPI 管理员账号密码，用于登录 NewAPI 后台。

推荐直接使用 Nuomi Drama Factory 初始化向导：不需要先注册 NewAPI；在设置页填写首次管理员密码并点击“初始化配置”，runtime token 会自动保存到 `settings.db`。

如果 NewAPI 已经初始化过，初始化密码可以留空；点击“初始化配置”只会创建或复用 Nuomi Drama Factory runtime token，不会重置已有管理员密码。

### 3. 配置供应商渠道

供应商渠道用于保存上游模型厂商的 key 和可选 Base URL 覆盖，例如 Ali、OpenRouter、OpenAI、Midjourney 等。

页面里的按钮含义：

- “保存渠道配置”：只保存到 Nuomi Drama Factory 本地配置，作为后续模型映射的渠道预设；不会立刻修改 NewAPI 已有渠道。
- “更新 NewAPI 渠道”：立刻把当前这一行的 key / Base URL 更新到 NewAPI 对应渠道。
- “保存模型映射”：根据当前文本、图片、视频、音频、embedding 映射，把需要的渠道和模型写入 NewAPI，并保存本地配置。

如果你更换某个逻辑模型的供应商，保存映射时会把该逻辑模型从旧渠道的模型列表移除，再写入新渠道，避免 NewAPI 在多个渠道之间随机选择同名模型。

### 4. 配置模型映射

Nuomi Drama Factory 使用一组内部逻辑模型名。你可以把每个逻辑模型映射到某个供应商渠道和真实上游模型。

页面里的模型配置分为几类：

- 纯文本模型：只发送文本输入，例如 Hermes、Cognee、身份规划、场景规划、道具规划、内容改写、脚本规范化等。
- 多模态模型：会把图片发送给上游模型，例如 AI 优化提示词、AI 检测身份与道具颜色标记、上传参考图创建自定义风格。这里必须选择支持图片输入 / 视觉理解的上游模型；纯文本模型可能运行失败。
- Embedding：`DC-cognee-embedding`，用于导入小说、Cognee 建图和向量检索。
- 图片：`LingShan-G2`、`LingShan-NB-2` 及场景、角色、草图相关图片模型。
- 视频：`seedance-*`、`happyhorse-1.0` 等。
- 音频：`index-tts-2`、`LingShan-MU-11` 等。

纯文本模型和多模态模型区块顶部都有批量填充控件。选择渠道、填写上游模型名后点击“应用到全部”，只会把当前区块的模型草稿填到页面里，不会立即写入 NewAPI。你可以继续展开分组，单独调整某一行的渠道或上游模型名；最后点击“保存映射”才会写入 NewAPI 并保存到 Nuomi Drama Factory 本地配置。

如果使用 RelayClaw 官方 Nuomi Drama Factory key，可以跳过映射；官方已配置好默认逻辑模型。

如果使用本地 NewAPI，请保持 Nuomi Drama Factory 内部逻辑模型名不变，只在 NewAPI 渠道里映射到真实上游模型。

## 媒体能力配置基础层

Nuomi Drama Factory 正在把图片、视频和 TTS 从具体 backend 名称迁移到稳定的“媒体能力”契约。该基础层与上文的 NewAPI 逻辑模型映射并存，不会自动替换现有生产链路。

### 当前实现范围

当前代码已提供：

- 图片、视频和 TTS 的结构化请求模型，以及帧引用等输入校验。
- `ProviderAccount`、`WorkflowProfile`、`CapabilityImplementation` 和 `RoutingPolicy` 配置模型。
- 配置的 SQLite 持久化、参数继承、显式候选路由解析。
- RunningHub Workflow API JSON 的受限导入、语义绑定校验和 SHA-256 摘要。
- 按供应商账号和能力限制的异步并发租约；例如同一 RunningHub 账号最多同时运行 5 个远端任务。
- 可恢复的本地媒体任务与 Attempt 账本。

媒体能力管理 API 已注册在 `/api/v1/media-capabilities`。它提供配置管理和校验，不执行真实供应商生成任务。

### 管理 API 概览

以下路径均相对于 `/api/v1/media-capabilities`：

| 资源 | Endpoint | 用途 |
|---|---|---|
| Provider | `GET /providers`、`GET /providers/{provider_id}` | 列出或读取供应商账号的脱敏配置。 |
| Provider | `PUT /providers/{provider_id}`、`DELETE /providers/{provider_id}` | 创建、更新或删除供应商账号。 |
| Workflow | `GET /workflows`、`GET /workflows/{profile_id}/{version}` | 列出或读取工作流档案。 |
| Workflow | `POST /workflows/import` | 以 `multipart/form-data` 上传 Workflow API JSON 和 semantic bindings，创建草稿。 |
| Workflow | `POST /workflows/{profile_id}/{version}/publish`、`DELETE /workflows/{profile_id}/{version}` | 发布或删除允许删除的工作流版本。 |
| Implementation | `GET /implementations`、`GET /implementations/{implementation_id}` | 列出或读取能力实现。 |
| Implementation | `PUT /implementations/{implementation_id}`、`DELETE /implementations/{implementation_id}` | 创建、更新或删除能力实现。 |
| Route | `GET /routes`、`GET /routes/{capability}` | 列出或读取能力路由策略。 |
| Route | `PUT /routes/{capability}`、`DELETE /routes/{capability}` | 创建、更新或删除路由策略。 |

这是系统级管理接口。所有 GET、PUT、POST 和 DELETE 操作都只允许角色为 `admin` 或 `owner` 的系统用户会话；`agent_session` 即使携带 `owner` 角色也返回 403，普通 `viewer` 同样不能读取或写入这些全局配置。

以下内容尚未接入真实生产执行：

- GRSAI 宫格出图、质量预检，以及超分、切割和去边流水线。
- RunningHub 的上传、提交、异步轮询、下载、取消和错误归一执行器。
- RunningHub 首帧、尾帧、首尾帧、图生视频和 TTS 工作流。
- MiniMax H3 提示词编译、视频质量门和产物晋升。
- 管理 UI 和旧 NewAPI 媒体路径的默认切换。

因此，保存以下配置只表示基础层能够校验、解析和持久化配置，**不表示已经能够调用 GRSAI 或 RunningHub 生成媒体**。

### 参数优先级

有效参数按以下顺序合并，左侧优先级最高：

```text
task overrides > project overrides > system implementation defaults > provider defaults
单次任务覆盖 > 项目级覆盖 > 系统级能力实现默认值 > 供应商默认值
```

值为 `null` 的上层字段不会抹掉下层有效值。合并结果仍需通过能力实现和工作流约束校验；未知字段或不支持的参数不会静默生效。

路由同样是显式的。系统只按 `default_implementation` 与 `fallback_chain` 中声明的候选顺序解析可用实现，不会因为某个供应商失败而自动选择未列出的供应商。未配置回退链时，只在默认实现内部按策略重试。

### 配置模型示例

Provider PUT 写请求必须包含 `credential_ref`，但任何 Provider GET 或 PUT 响应都不会回读该引用原值或真实密钥。响应只用 `credential_configured` 表示是否已配置，并用 `credential_scheme` 返回引用类型。

例如，请求 `PUT /api/v1/media-capabilities/providers/runninghub-main` 的 JSON body 为：

```json
{
  "provider_type": "runninghub",
  "base_url": "https://www.runninghub.cn",
  "credential_ref": "env://RUNNINGHUB_API_KEY",
  "enabled": true,
  "max_concurrency": 5,
  "poll_concurrency": 20,
  "queue_limit": 100,
  "capability_limits": {
    "video.*": 3,
    "tts.*": 2,
    "image.grid_upscale_split": 2
  }
}
```

对应的脱敏响应为：

```json
{
  "id": "runninghub-main",
  "provider_type": "runninghub",
  "base_url": "https://www.runninghub.cn",
  "enabled": true,
  "max_concurrency": 5,
  "poll_concurrency": 20,
  "queue_limit": 100,
  "capability_limits": {
    "video.*": 3,
    "tts.*": 2,
    "image.grid_upscale_split": 2
  },
  "credential_configured": true,
  "credential_scheme": "env"
}
```

响应中不会出现 `credential_ref`。无效引用返回 422 时也不会在错误响应中回显提交值。

`max_concurrency: 5` 表示最多有 5 个远端任务同时占用该账号配额。第 6 个任务留在本地队列等待租约；这不是用 5 个线程串行阻塞请求。能力限额和账号总限额必须同时满足。

凭据引用支持 `env://`、`secret://`，当前模型也接受 `keyring://`。例如：

```text
env://RUNNINGHUB_API_KEY
secret://media/runninghub-main
```

示例、Workflow JSON、日志、任务快照和版本库中都不得出现真实 API key。

能力实现将稳定能力绑定到具体账号和可选工作流；路由策略只列出明确允许的候选：

```json
{
  "implementation": {
    "id": "minimax-h3-fl2va-runninghub",
    "capability": "video.fl2va",
    "provider_account": "runninghub-main",
    "workflow_profile": "minimax-h3-video",
    "prompt_profile": "minimax-h3-v1"
  },
  "routing_policy": {
    "capability": "video.fl2va",
    "default_implementation": "minimax-h3-fl2va-runninghub",
    "fallback_chain": ["newapi-seedance-fl2va"],
    "concurrency_limit": 3
  }
}
```

若不允许跨供应商回退，请将 `fallback_chain` 设为空数组。候选 ID 重复、默认实现重复出现在回退链、能力不匹配或所有候选均不可用都会被拒绝。

### 导入 RunningHub Workflow JSON

工作流导入处理的是 RunningHub 导出的 API JSON 内容，而不是由客户端提供任意服务器文件路径。当前导入器可接收 UTF-8 JSON bytes、字符串或对象；仅在调用方同时限定 `allowed_root` 时才允许读取本地 `Path`。导入限制为 5 MiB，只接受 JSON object，不执行其中的脚本或指令，并拒绝疑似凭据字段、重复 key、过深结构和无效节点引用。

语义字段必须显式绑定到节点和输入字段，输出也必须指定节点与媒体类型。MiniMax H3 导演台工作流 `2089723723468328961` 只接受完整的 `timeline_data`，档案模型为：

```json
{
  "id": "minimax-h3-video",
  "version": 1,
  "workflow_id": "2089723723468328961",
  "capabilities": ["video.i2va", "video.fl2va"],
  "bindings": {
    "timeline_data": {"node_id": "12", "field": "timeline_data"}
  },
  "outputs": {
    "video": {"node_id": "7", "media_type": "video"}
  },
  "constraints": {},
  "status": "draft"
}
```

导入会生成规范化内容的 `source_sha256` 并返回草稿档案。当前配置存储保存档案元数据、绑定、约束和摘要，不应被理解为已经持久化并可执行原始 Workflow JSON。发布后的同 ID/版本不可就地覆盖；调整 workflow ID、绑定、输出或约束时应创建新版本。

### 后续供应商能力

- **GRSAI**：计划实现 `image.storyboard_grid` 和 `image.single`，按叙事组生成多宫格；后续再通过 `image.grid_upscale_split` 完成超分、切割、去边和确定性格位映射。
- **RunningHub 视频**：能力契约已为 `video.i2va`（首帧）、`video.l2va`（尾帧）、`video.fl2va`（首尾帧）、`video.ref2va`（参考素材）和 `video.t2va` 预留扩展边界；真实工作流执行器仍需后续接入。
- **RunningHub TTS**：契约已包含 `tts.synthesize`、`tts.voice_design` 和 `tts.voice_clone`；工作流提交、分段并行、顺序合并和音频质量检查仍属后续实现。
- **MiniMax H3 Skills**：这是将结构化分镜编译为 H3 视频提示词的增强层，不是模型供应商，也不负责提交任务。参考 [MiniMax H3 官方 Skills](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills)。

### MiniMax H3 导演台运行约定

- 一个 Director 任务可产出一个包含多个镜头的视频；合成、字幕和导出都读取 manifest 的 span，不按“一个 Beat 一个 MP4”重复插入。
- 仅首帧为图生视频（i2v）；首帧加尾帧为首尾帧视频（fl2v）。产品界面不开放仅尾帧模式。
- 提示词经 H3 中文优化层生成，不直接透传草稿；有对白的镜头必须写入可辨识的说话人、台词和时间信息，保证模型可对口型。
- 默认保留 H3 原生环境声/音效；新生成 span 的对白来源默认 `external_tts`。迁移旧 MP4 时，因没有可验证的分轨，所有条目默认 `h3_native`，避免合成阶段错误地丢弃原视频音频。
- 旧 Beat/叙事组 MP4 可先执行 `python scripts/h3_director_migration.py <项目目录>` 查看 dry-run，再显式加 `--write` 生成附加 manifest。只有能映射到同 revision 叙事组的文件才会以 CAS 方式挂接为 `completed`，供生产合成读取；无组的旧 Beat 只报告 `unattached`。若要迁移为 `external_tts`，必须同时显式提供存在的 `--ambience-stem` 与 `--dialogue-stem`；原 MP4 和较新 sidecar 结果不会被改写。

### 安全清单

- 仅写入 `env://...`、`secret://...` 或受支持的 `keyring://...` 引用，不写真实密钥。
- Workflow JSON 按不可信输入处理；上传前删除 token、签名 URL、示例私有素材名等敏感内容。
- 不把客户端路径当作服务端可读取路径；文件上传必须传输 JSON 内容。
- 不在日志、异常、任务快照、导出文件或前端响应中回显凭据。
- 发布前确认所有必需 semantic bindings、输出节点、媒体类型和参数约束。
- 显式配置回退链；不要依赖供应商之间的隐式切换。
- 并发值应符合账号配额，并同时限制轮询和本地队列，避免批量生产压垮单个账号。

### 验证基础层

在项目根目录使用仓库的 Python 3.11 虚拟环境运行：

```powershell
venv\Scripts\python.exe -m pytest tests/media_capabilities -q
venv\Scripts\python.exe -m pytest tests/test_api_media_capabilities.py -q
venv\Scripts\python.exe -m ruff check src/novelvideo/media_capabilities tests/media_capabilities
venv\Scripts\python.exe -m ruff check src/novelvideo/api/routes/media_capabilities.py tests/test_api_media_capabilities.py
git diff --check
```

这些命令验证契约、配置持久化、显式路由、工作流导入、并发租约、任务账本，以及管理 API 的认证、权限、脱敏响应和 CRUD 契约；它们不执行真实供应商调用。

详细边界见[统一媒体能力设计](../../superpowers/specs/2026-08-14-media-provider-capability-design.md)和[基础层实现计划](../../superpowers/plans/2026-08-14-media-capability-foundation.md)。

## Embedding 批量大小

Cognee 建图会批量调用 embedding。默认批量大小是 36：

```bash
EMBEDDING_BATCH_SIZE=36
```

不同上游 embedding 模型的单请求 `input` 数量上限不同：

- Gemini 类 embedding 通常可以使用 36。
- 部分 Qwen / 阿里 embedding 模型上限较低，建议设为 10。

如果建图阶段出现 embedding HTTP 400/422，且错误发生在导入小说或 Cognee 阶段，优先检查：

1. embedding 真实模型是否支持当前维度。
2. `EMBEDDING_BATCH_SIZE` 是否超过上游单请求 input 上限。
3. NewAPI 渠道里 `DC-cognee-embedding` 是否映射到了正确的 embedding 模型。

在本地 NewAPI 页面保存 Embedding 配置时，维度和批量大小会同时保存到本地配置；后续建图优先使用本地配置。

## 参考媒体 relay

当上游模型需要读取本地参考图、首帧、角色图或身份图时，Nuomi Drama Factory 需要先把本地文件上传到一个公网可访问的临时地址，再把 URL 传给模型网关。

纯文本流程、纯文生图流程通常不需要配置参考媒体 relay。图生图、视频首帧、角色参考图、身份图、Freezone 图片参考等场景会用到。

支持两种方式：

### 阿里云 OSS

阿里云 OSS 需要先在阿里云控制台创建 Bucket，再创建一个有该 Bucket 读写权限的 AccessKey。推荐使用只授权到该 Bucket 的 RAM 子账号，不要使用主账号 AccessKey。

```bash
MEDIA_RELAY_PROVIDER=aliyun_oss
OSS_RELAY_ENDPOINT=oss-cn-chengdu.aliyuncs.com
OSS_RELAY_BUCKET=你的_bucket
OSS_RELAY_AK=你的_access_key_id
OSS_RELAY_SK=你的_access_key_secret
MEDIA_RELAY_TTL_SECONDS=1800
```

字段说明：

| 网页字段 | 环境变量 | 说明 |
|---|---|---|
| Endpoint / 地域 | `OSS_RELAY_ENDPOINT` | OSS 外网 Endpoint，不要带 `https://`，例如 `oss-cn-chengdu.aliyuncs.com`。 |
| Bucket | `OSS_RELAY_BUCKET` | 用于临时参考图 relay 的 Bucket 名称。 |
| AccessKey ID | `OSS_RELAY_AK` | 有 Bucket 上传和签名读取权限的 AccessKey ID。 |
| AccessKey Secret | `OSS_RELAY_SK` | 对应的 AccessKey Secret。 |
| 有效期 | `MEDIA_RELAY_TTL_SECONDS` | 生成签名 URL 的有效时间，默认 1800 秒。 |

Bucket 需要允许后端上传对象，并能生成临时签名 URL 供上游模型读取。一般不需要把 Bucket 设为公开读；Nuomi Drama Factory 会使用签名 URL 暂时授权访问。建议单独建一个 Bucket 或独立前缀，只给 Nuomi Drama Factory 存放参考图临时文件。

### Cloudinary 免费方案

还没有 Cloudinary 账号时，到 <https://cloudinary.com/users/register_free> 注册免费账号。

```bash
MEDIA_RELAY_PROVIDER=cloudinary
CLOUDINARY_RELAY_CLOUD_NAME=你的_cloud_name
CLOUDINARY_RELAY_API_KEY=你的_api_key
CLOUDINARY_RELAY_API_SECRET=你的_api_secret
CLOUDINARY_RELAY_FOLDER=relay
MEDIA_RELAY_TTL_SECONDS=1800
```

Cloudinary 的 Cloud name、API Key、API Secret 可以在 Cloudinary 控制台的 API Keys 页面查看。进入控制台后，打开 Product environment settings -> API Keys，即可看到 `CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>` 格式的提示。

`CLOUDINARY_RELAY_FOLDER` 对应网页里的“API 文件夹（可选）”。这里填的是 Cloudinary 后台里的 folder 名称，不是本地文件夹路径。例如填 `dramaclaw-relay` 后，上传的参考图会放在 Cloudinary 的 `dramaclaw-relay` 文件夹下，便于后台管理；该 folder 名是内部兼容示例，必须原样保留。留空时上传到 Cloudinary 根目录。

网页设置中保存媒体存储配置后，本地 SQLite 配置优先生效，后端不会回传完整密钥给前端显示。

## 常见问题

| 现象 | 处理 |
|---|---|
| 保存官方渠道后仍调用旧 key | 确认当前生效渠道是否为“官方渠道”；新任务会读取最新本地配置，已在运行中的任务不会强制中途切换。Cognee 已初始化时需要重启 Nuomi Drama Factory。 |
| 本地 NewAPI 初始化失败 | 确认本地 NewAPI 服务已启动、SQLite 文件目录可写、`NEWAPI_PROVISIONER_ENABLED=true` 是否生效。 |
| 已初始化 NewAPI 后再次填密码点击初始化 | 不会修改已有管理员密码；密码只在首次初始化时使用。 |
| NewAPI 报 `No available channel for model ...` | 对应逻辑模型没有写入 NewAPI 渠道，或渠道未启用，或模型映射保存失败。 |
| 更换供应商后仍偶尔走旧渠道 | 检查 NewAPI 后台是否存在多个渠道同时包含同一个逻辑模型；重新保存模型映射或更新对应渠道。 |
| embedding 建图 400/422 | 降低 `EMBEDDING_BATCH_SIZE`，并确认 embedding 模型、维度和渠道映射正确。 |
| 参考图 / 视频首帧上传失败 | 检查 OSS 或 Cloudinary 配置；纯文本流程不需要媒体 relay，但带参考图的图片/视频模型需要。 |

## 相关文件

- `.env.example`：完整环境变量列表和默认值。
- `docker-compose.yml`：默认官方渠道部署。
- `docker-compose.selfhosted.yml`：内置 NewAPI 自托管部署。
- [自托管手册](../guides/self-hosting.md)
- [环境变量参考](../reference/environment-variables.md)
