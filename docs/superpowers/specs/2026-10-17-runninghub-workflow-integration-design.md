# RunningHub 通用工作流接入设计

日期：2026-10-17

状态：用户已确认架构和第一版范围；本规格待书面审查。本文定义目标行为，不代表功能已经实现。

## 1. 目标与范围

通过 API 格式工作流 JSON、远端 workflow ID 和一份接入描述，将新的 RunningHub 工作流接入统一 Python API、CLI 及项目生产流程。普通工作流增加配置即可接入；复杂业务结构允许使用已注册的 Python 适配器。

首版支持 API 节点图，不解析包含 nodes、links、widgets_values 的画布导出。支持字符串、整数、数字、布尔、对象、数组、文件和文件列表；支持多个命名输出。提供导入、校验、注册、执行、状态查询、恢复及取消。没有前端编辑器、远端工作流发布器、任意代码生成器或新的生产编排引擎。

“通用”指通过现有 RunningHub 工作流提交协议能够调用的节点图；本地 JSON 不会自动部署到远端。平台未暴露的节点参数或输出不能通过本层凭空获得。业务步骤只有在输入输出契约匹配时才能使用一个工作流。

## 2. 现有基础与实际缺口

- `runtime/runninghub_client.py` 已具备上传、远端图查询、提交、查询、下载、取消。
- `WorkflowProfile` 与 `runtime/compiler.py` 已提供语义参数到节点字段的映射，但没有完整类型、默认值、文件处理和复杂转换契约。
- `runtime/executor.py` 已具备持久任务、提交占用、未知提交状态、并发与费用上下文，但当前只下载一个匹配产物。
- `store.py` 已持久化工作流、能力实现与路由，扩展这些结构，不创建另一套账号或路由系统。
- 视频注册表、RunningHub 设置枚举以及 TTS、音乐节点映射存在固定工作流假设。
- `MediaCapability` 与 `MediaTaskRecord` 使用封闭枚举，音乐尚无对应能力。通用执行不能继续依赖从能力前缀推断媒体类型。

本地依据为 `runninghub` 下 ACE-Step 和 MiniMax H3 参考版的两个 `_api.json`。ACE-Step 使用 94.tags、203.value、205.value、109.value，107 输出音频；H3 使用 12.timeline_data，7 输出视频，8 是报告预览。图结构可确认字段存在，不能单凭图确认平台会返回预览文本。

## 3. 工作流包与版本

一个工作流包包含 `workflow_api.json`、`manifest.json`；必要时引用仓库内已注册适配器。运行时导入副本存放在现有应用数据目录，生产素材和运行记录不进入 Git。仓库只保存经过清理的内置配置和测试夹具。

manifest 的必要字段：

| 字段 | 契约 |
| --- | --- |
| schema_version | 描述格式版本，首版为 1 |
| id / version / label | 稳定本地 ID、正整数版本、人类可读名称 |
| provider / workflow_id | provider 为 runninghub；远端 ID 为十进制字符串 |
| source | 包内图文件相对路径、原始文件 SHA-256、规范化图 SHA-256 |
| inputs | 参数名到类型、必填、默认值、范围、枚举、长度及文件规则的定义 |
| bindings | 参数或适配器结果到节点字段的映射及线格式编码 |
| outputs | 命名输出到节点 ID、产物类型、数量约束的定义 |
| adapter | 可选，已注册的适配器 ID 和版本 |
| integrations | 可选，项目能力、生产场景、业务适配器及参数映射 |

schema_version 与工作流 version 分离。同一 ID/version 不允许覆盖不同内容；更新产生新版本。任务固定完整配置、图摘要和适配器版本，恢复不会使用后来编辑的配置。重新注册相同内容为幂等操作。

## 4. 导入与参数编译

`inspect` 只读解析节点 ID、class_type、inputs、标题和连线。只有符合上游节点引用结构的字段才标记为连线，结构无法确定时标记歧义，不自动转换。`import` 产生草稿和候选参数报告，不自动激活业务路由。

导入器可以根据字面值推断基础类型、列出候选输出，但不自动公开全部参数，不把示例提示词、参考素材路径、预览内容和随机种子当成业务默认值。缺失的业务参数定义在注册前必须补齐。加载模型、采样器等字段只有显式声明后才能覆盖。

每个绑定包含 source 参数名、targets 数组；每个 target 包含 node_id、field、encoding。首版 encoding 为 native、string、json：分别传 JSON 值、确定性标量字符串、序列化 JSON 字符串。布尔 string 编码为 true/false；对象和数组须明确 native 或 json。整数种子支持声明为十进制字符串，避免精度损失。

缺省使用明确声明的 default；没有 default 的可选输入不生成覆盖；null 仅允许在 nullable 参数上使用。未知参数、类型不匹配、非有限数字、缺少必填项、越界及重复目标映射在上传前报错。一个参数可绑定多个目标，但不同参数不能覆盖同一字段。

目标节点和字段必须存在；不能直接覆盖图连线。ACE-Step 时长修改 205.value，由原图同时驱动 94.duration 与 98.seconds，不重写这两个连线。

本地校验不联网。显式远端校验通过现有 workflow_json 读取部署图，核对绑定节点类型、字段、声明输出以及相关连线；记录部署图摘要。提交前对选定版本做远端兼容校验，单次运行内复用结果；只忽略展示元数据，不忽略影响执行的结构。无法读取时保持未验证，不能声称已通过；实际提交前仍无法验证则返回明确错误。API 不支持原子固定远端版本，校验后远端仍可能变动，这一限制随运行记录保留。

## 5. 文件与复杂适配器

文件值必须是有类型的引用：local_path、artifact_id、provider_file 三选一，不能凭字符串猜测它是路径还是远端文件名。artifact_id 经现有资产库解析。首版不新增任意 URL 拉取器；已有业务引用下载逻辑留在相应适配器内。

文件解析、可读性和类型约束在上传前检查。上传后得到的提供方文件标识替换输入值；文件列表保持顺序。目标接收列表时显式声明 native 或 json 编码。嵌套于时间线的参考图、音频等由适配器声明为待上传引用，由统一文件层完成上传，不在通用编译器中搜索和改写任意字符串。

适配器分为 prepare 与 compile：prepare 将业务输入规范化并列出命名文件引用；统一文件层解析、上传；compile 接收规范化输入及文件标识，产生可校验的节点绑定值。适配器不提交任务、不轮询、不管理并发。适配器由代码注册表按 ID/version 解析，manifest 不接受 Python 文件路径、eval 或 shell 命令。

同一次任务持久化文件摘要、账号与上传标识以便恢复；不把上传标识永久视为有效。若失效且尚未提交，可重新上传并记录新值；已提交任务不得因此重新生成。H3 保留既有时间线编译和参考素材规则；ACE-Step 独立调用通过配置映射，项目配乐入口的纯音乐约束继续由业务层加入。

## 6. 执行、恢复与输出

统一执行顺序：冻结版本与输入 → 本地校验 → 远端兼容校验 → 适配器 prepare → 文件上传 → compile 与最终校验 → 持久化提交意图 → 提交并保存 provider task ID → 查询 → 按契约收集与下载输出 → 完成。

复用 TaskStore、RunningHubClient、并发协调器、费用捕获和资产存储。为独立执行增加一个固定通用能力 `workflow.execute`，为配乐业务增加 `music.generate`；新增工作流不再新增枚举。媒体类型由工作流/业务运行上下文明确提供，通用任务不能被统计成名为 workflow 的媒体。并发读取已保存的账号、能力和队列配置，不自行降低；未绑定业务的运行使用通用能力及账号限制。

每个命名输出包含 node_id、kind、min_count、max_count（可为空）。收集该节点所有匹配结果，以稳定的提供方结果顺序保存；缺少必需输出、类型不符、超出声明数量、节点 ID 缺失导致归属不明，均返回输出契约错误。不得取第一个无关文件兜底。未声明的预览结果不作为主要产物，保留必要诊断元数据即可。首版处理提供方实际返回的文件产物；报告文本节点只有 API 返回了可识别结果才可声明支持。

返回结构包含本地 run_id、provider_task_id、status，以及按输出名分组的 artifacts。单产物旧调用者通过兼容适配取得主产物，多产物不被静默丢弃。

本地任务 ID 是 status/resume 的标识。重复幂等键和相同输入返回已有任务；同键不同输入报冲突。已知远端 ID 时恢复查询或下载，不重复提交。提交结果未知且没有远端 ID 时维持 UNKNOWN，要求对账；resume 不自动重试付费提交。已下载产物分别记录，下载失败恢复只补缺失项。远端明确失败后的重新生成必须是新 attempt，并由显式 retry 发起。取消尽力调用现有提供方取消能力，不承诺撤销费用。

错误包含稳定 code、phase、run_id、可恢复标识及定位到参数/节点的说明；凭据通过现有 credential_ref 解析，不写入包和日志。诊断只输出必要字段，不回显完整时间线中的素材地址。

## 7. Python 与 CLI 入口

Python 服务统一提供 inspect、import_workflow、validate、register、run、status、resume、cancel、retry；运行操作为 async。run 返回持久任务句柄，wait 为可选等待方式。调用者传入账号引用，不传入包内密钥。

独立工具的预期命令形态（待实现）：

```text
uv run python -m novelvideo.runninghub_cli inspect <workflow_api.json>
uv run python -m novelvideo.runninghub_cli import <workflow_api.json> --id <local-id> --workflow-id <remote-id> --out <package-dir>
uv run python -m novelvideo.runninghub_cli validate <package-dir>
uv run python -m novelvideo.runninghub_cli validate <package-dir> --remote --account <account-id>
uv run python -m novelvideo.runninghub_cli register <package-dir>
uv run python -m novelvideo.runninghub_cli run <local-id> --version 1 --inputs <inputs.json> --account <account-id>
uv run python -m novelvideo.runninghub_cli status <run-id>
uv run python -m novelvideo.runninghub_cli resume <run-id>
```

各命令输出结构化 JSON；校验失败返回非零退出码。inspect/import/register 不提交生成任务。注册使工作流可供选择，不更改项目默认路由；默认路由变更是独立配置动作。

## 8. 项目集成与兼容迁移

通用注册表从已保存的工作流定义构建；不再要求每个新工作流新增 RunningHubWorkflowSettingsKey。现有固定设置继续作为内置工作流的兼容入口，在迁移时转成相同本地 ID 的定义。迁移幂等且保留原 workflow ID、账号、默认选择、并发和引用数量限制。

integrations 声明目标业务能力、场景和已注册业务适配器。注册时检查业务请求能否转成所需参数、输出能否满足调用者（例如叙事组需要一个主视频）。单纯标注 video 不足以获得叙事组资格。现有支持该能力的生产选择入口使用通用注册表；不新增工作流配置 UI。

迁移顺序：先引入通用定义与执行层，接入 ACE-Step 和 H3 参考版作为两个验收样本；随后将现有 H3 基础版、IndexTTS2、音色设计及图像放大配置接入相同层。缺少导出图的内置工作流先保留兼容路径并明确标记，不伪造图校验结果。以已有夹具和显式远端只读查询补全图后迁移；迁移完成的入口不再维护第二套提交/轮询代码。

本工具用于工作流接入和独立调试。整集、叙事组生成、续制与失败批次恢复仍通过 `novelvideo.production_cli`（nuomi）进入既有“组分镜图 → 组视频 → 整集”生产链。业务适配器把选择的工作流交给统一运行器，不引入镜头循环或直接提供方脚本绕过生产入口。

## 9. 验证与完成标准

自动化验证使用脱敏图夹具和模拟提供方，不触发收费生成。

1. ACE-Step 可由 API JSON 和 manifest 注册、编译并经通用执行器产生主音频；验证共享时长连线未被覆盖。
2. H3 参考版复用时间线适配器，文件上传后正确生成 timeline_data 字符串，只选择节点 7 的主视频。
3. 加入一个不属于内置名单的模拟工作流，无需增加设置枚举或修改核心执行器即可运行；多个输出完整保留。
4. 验证未知字段、必填、类型、数字边界、连线误覆盖、版本冲突、远端结构漂移、文件缺失与输出缺失。
5. 验证提交未知状态不重提、查询恢复、部分下载恢复、取消和幂等键冲突。
6. 验证账号并发配置、费用归属、旧版本任务恢复及现有业务入口回归，生产入口与组级编排保持一致。

本轮交付设计规格。实现阶段以针对性测试证明上述契约；实际供应商协议差异在实现前以官方资料或只读远端结果核实。真实运行仅使用明确选定的样本和输入，生成结果、明显内容遗漏及最终播放检查即可，不增加 ASR 或重复付费质量检查。

## 10. 设计自检

- 已区分通用执行、业务注册与远端发布；首版不承诺自动部署或推断全部语义。
- 已明确输入默认值、文件引用、编码、输出数量、多产物与恢复契约。
- 已处理封闭能力枚举、单产物执行器和音乐费用归属的现有边界。
- 迁移按入口逐步推进，缺少图的既有能力不因注册机制上线而失效。
- 未修改生产代码或触发收费任务；书面规格审查后进入实现计划。
