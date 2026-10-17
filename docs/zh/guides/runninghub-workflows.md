# 接入 RunningHub 工作流

API 格式 JSON 描述节点图；manifest 描述你允许调用者修改的参数、文件和输出。远端工作流 ID 指向 RunningHub 上已部署的工作流。本工具不发布或修改远端节点图。

## 从现有音乐示例开始

在项目根目录运行以下 PowerShell 命令。包目录须不存在，防止覆盖已有配置。

```powershell
uv run python -m novelvideo.runninghub_cli inspect 'runninghub/Ace-Step1.5X工作流(打造你的专属音乐)_api.json'
uv run python -m novelvideo.runninghub_cli import 'runninghub/Ace-Step1.5X工作流(打造你的专属音乐)_api.json' --id my-music --workflow-id 2059090557116440578 --out .runtime/my-music
uv run python -m novelvideo.runninghub_cli configure .runtime/my-music --template acestep
uv run python -m novelvideo.runninghub_cli validate .runtime/my-music
uv run python -m novelvideo.runninghub_cli register .runtime/my-music
```

`configure` 是两个已知图的显式模板，不是通用自动推断。另一模板 `h3-reference` 支持目录中的 MiniMax H3 参考版，公开 `timeline_data` 对象并将其编码为节点 12 的 JSON 字符串。普通新图使用 `import` 生成的 candidates.json 查看节点与字段，自行编辑 manifest。

账号在软件现有 RunningHub 设置中配置。以下命令只查询部署图，不触发生成：

```powershell
uv run python -m novelvideo.runninghub_cli validate .runtime/my-music --remote --account runninghub-main
```

将下面内容保存为 `inputs.json`。seed 必须显式提供，以免供应商自动重置种子；可在调用脚本中生成新的 uint64 十进制字符串。

```json
{"tags":"Instrumental, quiet piano", "bpm":75, "duration":15, "seed":"42"}
```

以下 `run` 命令会提交实际生成任务，使用供应商额度。默认返回任务句柄；`--wait` 等待完成。

```powershell
uv run python -m novelvideo.runninghub_cli run my-music --version 1 --inputs inputs.json --account runninghub-main --idempotency-key my-music-test-1
uv run python -m novelvideo.runninghub_cli status <run-id>
uv run python -m novelvideo.runninghub_cli resume <run-id>
```

`status` 只读本地记录。`resume` 驱动一次上传、提交、查询或下载步骤；已有远端任务不会再次提交。相同幂等键和输入复用原任务，不同输入冲突。结果 `artifacts` 按输出名分组，文件位于应用本地状态目录的 `runninghub/artifacts`。`--state-dir` 可在命令前显式指定另一套本地状态目录。

## 通用 manifest

`import` 已生成 id、version、source 摘要和远端 ID。补充以下字段，并把 status 从 draft 改为 active；保留 source 原值。

```json
{
  "inputs": {
    "prompt": {"type":"string", "required":true},
    "reference": {"type":"file", "required":true, "extensions":[".png",".jpg"], "max_bytes":20971520}
  },
  "bindings": [
    {"source":"prompt", "targets":[{"node_id":"6","field":"text","encoding":"native"}]},
    {"source":"reference", "targets":[{"node_id":"10","field":"image","encoding":"native"}]}
  ],
  "outputs": {"image":{"node_id":"9","kind":"image","min_count":1,"max_count":4}},
  "status":"active"
}
```

这里的节点编号只是格式示例，必须替换成实际 API 图中的编号。不能直接覆盖 `["节点ID", 输出序号]` 连线；应绑定该连线上游节点的可写字段。`native` 保留 JSON 类型，`string` 只转换标量，`json` 将对象或数组序列化成字符串。一个参数可以指定多个 targets，同一个目标字段不能被重复绑定。

文件输入必须显式标明来源：

```json
{"prompt":"a mountain", "reference":{"kind":"local_path","value":"E:/assets/reference.png"}}
```

也支持 provider_file（已上传标识）及 artifact_id（Python 服务须注入资产解析器）。files 接收此类引用的列表。普通字符串不会被猜测为文件路径，也不会被自动当成下载 URL。

## Python 接口与适配器

`open_workflow_service` 使用软件保存的账号、并发及任务存储。高级调用者也可直接构造 WorkflowService 并注入客户端和资产解析器。

```python
import asyncio
from novelvideo.media_capabilities.workflows.runtime import open_workflow_service

async def main():
    async with open_workflow_service(account_id="runninghub-main") as service:
        result = await service.run("my-music", 1,
            {"tags": "quiet piano", "seed": "42"},
            idempotency_key="music-request-42", wait=True)
        print(result.model_dump())

asyncio.run(main())
```

复杂输入通过代码注册 `WorkflowAdapter.prepare(values)` 和 `compile(prepared, uploaded)`；manifest 仅引用 adapter 的 id/version。prepare 返回规范化值和显式待上传文件，compile 接收上传标识生成绑定值。适配器不自行提交或轮询。导入包不会动态加载 Python 文件。

## 生产注册与恢复

独立注册不会修改默认模型。业务 integration 需通过对应适配器的契约校验，不能仅把标签改成 video 就用于叙事组。H3 参考版通过现有导演台适配器保留素材快照、时间线和成片检查。

注册的 H3 兼容包在项目模型列表中以 `runninghub:workflow:<id>:v<version>` 出现。选择它后，原有叙事组任务携带完整工作流版本快照。首版内置生产适配器支持 H3 参考版契约；其他模型仍可独立调用，需要新增业务适配器才能接入叙事组。

配乐生成 API 的原有请求可增加 `workflow`（本地注册 ID）和 `workflowVersion` 字段，使用注册的 music_desk 能力。作业仍在原有队列执行，结果仍登记为项目候选配乐，不自动采用。

`migrate --source-dir runninghub` 可用目录内已知样本为保存的 ACE-Step/H3 参考版设置注册包。它不改变默认路由；缺少图或需要显式描述的条目报告为 legacy_unverified，继续保留旧入口。更新内容须增加版本，不覆盖已有版本。

剧集生产、续制与失败组恢复继续使用 `nuomi`，按叙事组完成分镜图、组视频和整集合成；独立 CLI 用于工作流接入与调试。

常见错误：

| code | 处理 |
| --- | --- |
| draft_package | 补齐输入、绑定、输出并启用包 |
| source_digest | 原图已改变，重新导入并增加版本 |
| linked_field | 改绑上游节点的可写字段 |
| graph_drift | 本地与部署图执行结构不一致，核对后导入新版本 |
| adapter_unavailable | 安装并注册对应版本的代码适配器 |
| output_count / output_type | 检查输出节点、类型和数量约束 |
| unknown | 远端提交结果不确定，先对账，不要自动重试 |

已知远端任务的临时下载错误通过 resume 恢复。明确失败后 `retry` 创建新 attempt，可能再次计费；UNKNOWN 不允许 retry。配置升级增加 version，运行中的任务继续使用原始快照。
