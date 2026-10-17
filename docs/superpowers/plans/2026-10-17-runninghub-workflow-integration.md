# RunningHub 通用工作流接入实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务实现；用户选择委派时使用 subagent-driven-development。使用复选框跟踪进度。

**目标：** API JSON 加接入描述即可独立运行普通 RunningHub 工作流，并将兼容工作流注册到项目生产流程。

**架构：** 在现有 media_capabilities 内新增 workflows 包，负责定义、导入、校验、文件处理与注册。扩展现有 TaskStore/RunningHubExecutor 管理多输出和恢复，通过显式业务适配器接入音乐、视频和 TTS。独立 CLI 调用同一服务，整集生产仍走 nuomi。

**技术栈：** Python 3.11、Pydantic、SQLite、Typer、httpx、pytest/pytest-asyncio、uv。

**依据：** `docs/superpowers/specs/2026-10-17-runninghub-workflow-integration-design.md`，用户已于 2026-10-17 确认。本文为计划，所有复选框均待执行。

---

## 实施边界与准备

本计划拆为四个可验证里程碑：M1 离线工作流包（任务 1–3）；M2 通用运行（任务 4–7）；M3 项目注册与两个真实图样本（任务 8–9）；M4 兼容入口及交付（任务 10–11）。只有 M1–M4 都满足验收，才能宣称范围 B 完成。

当前工作区存在大量无关变更。文档在当前分支单独提交；实现前检查附加 worktree，优先使用合适的已有隔离工作区，或从包含本规格/计划的明确提交创建 worktree。不得把未提交的其他功能一并提交或清理。每项提交使用精确文件清单。

新核心文件位于 `src/novelvideo/media_capabilities/workflows/`：

| 文件 | 单一职责 |
| --- | --- |
| models.py | manifest、输入、绑定、输出、运行结果的严格类型 |
| graph.py | 图解析、摘要、字段与连线检查 |
| importer.py | inspect 报告与未激活的包草稿 |
| compiler.py | 输入校验、默认值与线格式编码 |
| adapters.py | 有版本的 prepare/compile 适配器注册表 |
| files.py | 有类型文件引用、上传记录与恢复 |
| registry.py | 包注册、版本查询、兼容性与业务集成索引 |
| service.py | Python 公共服务与任务驱动 |
| builtin_adapters.py | 现有 H3/TTS 等业务逻辑到新接口的薄封装 |
| migration.py | 固定设置到通用注册的幂等迁移 |

修改 `models.py`、`store.py`、`task_store.py` 及 `runtime/executor.py` 扩展已有存储和执行机制；新增 `runtime/output_collection.py` 隔离多产物逻辑。新增 `src/novelvideo/runninghub_cli.py` 提供命令行。测试与新增包同名放入 `tests/media_capabilities/workflows/`。

所有任务按“写有意义的失败断言 → 运行并确认失败原因 → 实现 → 同一测试通过 → 精确提交”推进。测试命令在仓库根执行，预期退出码 0；缺少新模块时首次测试应在导入或新行为断言处失败，不能把环境安装错误当成有效红灯。不运行收费任务作为单元测试。

## 任务 1：严格工作流定义

文件：新建 `workflows/__init__.py`、`workflows/models.py`；新建 `tests/media_capabilities/workflows/test_models.py`。

- [ ] 写 `WorkflowManifest` 校验测试。完整最小样本如下，测试内直接定义为 `payload`；未知键、同目标重复绑定、错误 schema_version、空远端 ID、非法输出数量分别参数化验证。

```python
payload = {
    "schema_version": 1, "id": "custom-audio", "version": 1,
    "label": "Custom audio", "provider": "runninghub", "workflow_id": "123",
    "source": {"path": "workflow_api.json", "sha256": "0" * 64,
               "graph_sha256": "1" * 64},
    "inputs": {"prompt": {"type": "string", "required": True}},
    "bindings": [{"source": "prompt", "targets": [
        {"node_id": "1", "field": "text", "encoding": "native"}]}],
    "outputs": {"audio": {"node_id": "2", "kind": "audio",
                          "min_count": 1, "max_count": 1}},
    "integrations": [],
}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_models.py -q`，确认失败后实现严格 Pydantic 模型，extra=forbid；输入含 nullable、default、minimum/maximum、enum、min_length/max_length、items 和文件约束。用 fields_set 区分未提供 default 与显式 null。
- [ ] 定义 `WorkflowError(code, phase, message, recoverable=False)`，以及 `RunResult(run_id, provider_task_id, status, artifacts)`；artifacts 为输出名到 MediaArtifact 列表。对象输入使用明确的 JSON schema 子集，未支持关键字拒绝接受。
- [ ] 重跑模型测试并提交 `feat: define RunningHub workflow package contracts`。

## 任务 2：图检查和导入草稿

文件：新建 `workflows/graph.py`、`workflows/importer.py`、`tests/media_capabilities/workflows/test_importer.py`；新增脱敏夹具 `tests/fixtures/runninghub/acestep_api.json`、`h3_reference_api.json`。

- [ ] 从 runninghub 两个 API JSON 提取图，清理个人提示词、文件路径和预览文本；保留节点类型、字段与连接。使用下列图形断言检测共享输入，另测无法判定的数组、断裂引用、空图和画布格式被明确拒绝。

```python
graph = {
    "205": {"class_type": "Float", "inputs": {"value": 15.0}},
    "94": {"class_type": "Encode", "inputs": {"duration": ["205", 0]}},
    "98": {"class_type": "Latent", "inputs": {"seconds": ["205", 0]}},
}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_importer.py -q`。
- [ ] 实现 `inspect_graph(graph)` 和 `import_workflow(source_path, *, identifier, workflow_id, out_dir)`。读取 UTF-8/BOM；原文件摘要与去除展示元数据后的稳定图摘要分别存储。候选推断不自动暴露参数，不把源文件内容当默认值；草稿带独立候选报告，注册校验拒绝未完成描述。
- [ ] 验证图文件路径只能指向包内文件；import 不覆盖已有不同文件、不调用网络。重跑测试并提交 `feat: inspect and import RunningHub API graphs`。

## 任务 3：确定性参数编译和部署图校验

文件：新建 `workflows/compiler.py`、`tests/media_capabilities/workflows/test_compiler.py`；扩展 `workflows/graph.py`；保留旧 `runtime/compiler.py` 兼容入口。

- [ ] 用任务 1 的最小 manifest 写 `compile_inputs(manifest, graph, values)` 测试；预期是以下精确线格式，并测试 unknown 参数、非有限数字、必填、null、布尔和整数不混淆、一对多以及连线覆盖失败。

```python
expected = [{"nodeId": "1", "fieldName": "text", "fieldValue": "hello"}]
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_compiler.py tests/media_capabilities/runtime/test_compiler.py -q`。
- [ ] 实现 native/string/json 编码、严格输入类型与所有已声明约束、默认值和自然节点排序；对象和数组不隐式字符串化；uint64 种子以显式字符串规则验证。目标 class_type/field/连线均与图核对。
- [ ] 为 `validate_deployed_graph(manifest, local_graph, deployed_graph)` 添加测试：展示标题变化不阻断，节点类型/字段/执行结构漂移阻断；远端读取失败不可标记验证成功。业务输入覆盖字段的字面值不作为结构变化。
- [ ] 重跑上述命令通过后提交 `feat: compile and validate workflow parameter bindings`。

## 任务 4：有类型文件与适配器接口

文件：新建 `workflows/files.py`、`workflows/adapters.py`；新建 `test_files.py`、`test_adapters.py` 于 workflows 测试目录。

- [ ] 为如下引用写测试：普通字符串不可被猜成路径；文件列表保序；artifact_id 通过注入的资产解析器查找；provider_file 不重复上传；本地文件缺失在 submit 前失败。

```python
file_input = {"kind": "local_path", "value": "reference.wav"}
remote_input = {"kind": "provider_file", "value": "uploaded/reference.wav"}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_files.py tests/media_capabilities/workflows/test_adapters.py -q`。
- [ ] 定义 `PreparedInputs(values, files)`，`WorkflowAdapter.prepare(values)` 返回该结构，`compile(prepared, uploaded)` 返回语义绑定值。通过 `(adapter_id, version)` 注册；普通工作流使用 identity 适配器，配置不能动态导入任意模块。
- [ ] 实现文件摘要与账号作用域上传记录；利用注入 checkpoint 保存记录。未提交且远端文件明确失效才允许重传，不能因为网络查询失败重传或重新提交。测试两次恢复复用上传结果、不同账号不复用、适配器版本缺失明确失败。
- [ ] 通过测试后提交 `feat: prepare workflow files and versioned adapters`。

## 任务 5：注册持久化与能力兼容

文件：修改 `media_capabilities/models.py`、`store.py`、`task_store.py`；新建 `workflows/registry.py`、`test_registry.py`。

- [ ] 增加 `WORKFLOW_EXECUTE="workflow.execute"`、`MUSIC_GENERATE="music.generate"`。WorkflowProfile 增加可选 manifest 快照字段，旧记录缺失时保持旧解析。测试旧数据库载入与新的能力任务序列化。
- [ ] 为注册编写测试，固定预期：相同 ID/version/内容两次注册只产生一条记录；不同内容报 StoreConflictError；注册不会新增或更改默认路由。

```python
assert MediaCapability("workflow.execute").value == "workflow.execute"
assert MediaCapability("music.generate").value == "music.generate"
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_registry.py tests/media_capabilities/test_store.py tests/media_capabilities/test_task_store.py tests/media_capabilities/test_workflow_profiles.py -q`。
- [ ] 在现有事务和表内持久化完整 manifest，复用 profile 版本键。调整 save_workflow 对相同不可变内容幂等返回，不放开不同内容覆盖。包图进入现有应用数据路径并保存摘要，状态读取不依赖用户原始导入路径。
- [ ] 任务快照固定 manifest/图摘要/适配器版本/显式媒体类型；上传和下载 checkpoint 使用 TaskStore 事务写入专用 attempt checkpoint JSON 列，采用检查列存在后添加的幂等迁移，默认空对象。重跑测试并提交 `feat: persist generic workflow registrations and snapshots`。

## 任务 6：多产物与可恢复执行

文件：修改 `runtime/executor.py`、`runtime/recovery.py`、`task_store.py`；新增 `runtime/output_collection.py`、`tests/media_capabilities/runtime/test_output_collection.py`；扩展 `test_executor.py`。

- [ ] 编写输出选择测试，传入 node 7 的两个 video 和 node 8 的预览；声明 video 数量 2 时保留两个，声明 1 时明确失败。输出名与下载完成记录分别持久化。

```python
output_contract = {
    "video": {"node_id": "7", "kind": "video", "min_count": 2, "max_count": 2}
}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/runtime/test_output_collection.py tests/media_capabilities/runtime/test_executor.py tests/media_capabilities/runtime/test_recovery.py -q`。
- [ ] 提取输出选择/数量与类型检查；允许 image/video/audio/document/file 产物类别，按提供方返回 MIME/类型归一化，无法证明满足类型则返回契约错误。旧 profile 兼容现有单主产物行为，新 manifest 使用命名列表并保留 artifacts 平铺兼容字段。
- [ ] 将 prepare/upload/compile 接入提交前阶段；恢复从冻结快照读输入，不重新读取注册表当前配置。checkpoint 记录输出节点、结果序号、文件摘要、资产 ID，不持久化短期签名 URL；恢复时重新 query 获取 URL。
- [ ] 添加模拟网络测试：第二个下载失败后 resume 仅补第二个；UNKNOWN 无远端 ID 永不 submit；两个进程争同一提交只有一个成功；取消中返回的远端 ID 不丢失。恢复下载错误允许回到对应下载阶段，不能泛化为失败任务自动重试。
- [ ] 媒体归属从快照显式字段取值，旧任务保留兼容映射；调用现有费用与并发机制。通过上述测试及 `uv run pytest tests/costs/test_provider_capture.py tests/media_capabilities/test_concurrency.py -q` 后提交 `feat: resume generic workflow execution with multiple outputs`。

## 任务 7：Python 服务与独立 CLI

文件：新增 `workflows/service.py`、`src/novelvideo/runninghub_cli.py`、`tests/media_capabilities/workflows/test_service.py`、`tests/test_runninghub_cli.py`；修改 `runtime/configuration.py`。

- [ ] 用 fake provider 实现 service 测试：run 创建持久任务、status 无网络、resume 不重提、retry 仅接受明确可重试的失败、相同幂等键返回相同任务、异参同键冲突；run 可显式 wait，默认返回句柄。
- [ ] CLI 用 Typer CliRunner 验证 inspect/import/validate/register/run/status/resume/cancel/retry 命令，以及 `validate --remote --account`。测试 JSON 输出和非零失败退出码，不把异常堆栈混入标准输出。

```python
from typer.testing import CliRunner
from novelvideo.runninghub_cli import app

def test_cli_has_help():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "resume" in result.output
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_service.py tests/test_runninghub_cli.py tests/media_capabilities/runtime/test_configuration.py -q`。
- [ ] 实现规范中的 Python 方法；configuration 按传入账号 ID 加载凭据与现有并发配置，默认账号仅作旧入口兼容。run 远端兼容校验成功后才上传；status/cancel/resume 加载冻结账号与快照。不得让账号选择创建新的并发池绕开既有共享限制。
- [ ] 重跑测试，通过 `uv run python -m novelvideo.runninghub_cli --help`，提交 `feat: expose generic RunningHub Python and CLI interfaces`。

## 任务 8：ACE-Step 配置接入与配乐桥接

文件：新增 `workflows/builtins/acestep/manifest.json`、`workflow_api.json`；修改 `music/generation.py`、`media_capabilities/music/runninghub_acestep.py`；新增 `tests/media_capabilities/workflows/test_acestep_package.py`；扩展 `tests/music/test_generation.py`。

- [ ] 用脱敏图写纯配置映射测试，期望包括以下三个目标；tags、lyrics 和种子另行断言。独立调用不强制纯音乐，项目配乐仍生成 Instrumental 前缀与歌词标记。

```python
expected_fields = {("94", "tags"), ("203", "value"), ("205", "value")}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_acestep_package.py tests/music/test_acestep.py tests/music/test_generation.py -q`。
- [ ] 保存无私有示例内容的 manifest 和图；业务 job 持久绑定 run_id，将通用产物转换为现有配乐 asset，保留 owner/project 引用和使用记录。删除已迁移入口的重复提交/轮询，实现 job 状态与 run 状态映射。
- [ ] 测试旧 remoteTaskId 任务接管：必须先附着远端 ID，再恢复；submission_unknown 不重新提交；已生成 candidate 不重复入库。旧工作流环境变量覆盖在迁移时保留为固定快照。
- [ ] 测试通过后提交 `feat: route ACE-Step music through generic workflows`。

## 任务 9：H3 参考版与业务选择注册

文件：新建 `workflows/builtin_adapters.py`、`workflows/builtins/h3_reference/manifest.json`、`workflow_api.json`；修改 `video/workflow_registry.py`、`video/h3_reference_runtime.py`、`video/runtime.py`、`api/routes/media_capabilities.py`；新增 `tests/media_capabilities/workflows/test_integrations.py`；扩展现有 H3 测试。

- [ ] 写兼容性测试：普通 video 工作流不能仅凭标签进入叙事组；符合已注册业务请求/主视频契约后才可选择；注册不改默认；固定版本停用不破坏运行中任务。H3 adapter 编译值必须保持原 timeline_data 结构。

```python
binding = {"source": "timeline_data", "targets": [
    {"node_id": "12", "field": "timeline_data", "encoding": "json"}
]}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_integrations.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_reference_runtime.py tests/media_capabilities/video/test_workflow_registry.py tests/test_api_media_capabilities.py -q`。
- [ ] 封装现有 H3 时间线编译器，避免重新实现导演台规则；参考素材由统一上传层处理，输出必须归属 node 7。动态工作流 definition 由 registry 生成，保留内置 H3 ID 与场景约束；API 仍返回旧客户端可读字段。
- [ ] 验证 nuomi 下组级任务仍走既有 task runner；工作流切换不创建 shot/segment 循环。用 `uv run pytest tests/test_task_narrative_group_video_runner.py tests/test_narrative_group_video_settings.py -q` 回归，通过后提交 `feat: register H3 and compatible workflows for production selection`。

## 任务 10：其余现有入口迁移

文件：新建 `workflows/migration.py`、`tests/media_capabilities/workflows/test_migration.py`；修改 `tts/runninghub_indextts2.py`、`tts/runninghub_voice_design.py`、`image/pipeline.py`、`video/runninghub_h3.py`、`workflows/builtin_adapters.py`。为具备可靠图源的内置工作流在 `workflows/builtins/` 增加独立包目录。

- [ ] 测试固定设置迁移两次后 ID/远端 workflow ID/账号/默认路由/引用数量上限不变；只有新版本显式选择才改变新任务。缺少图的旧条目保留兼容运行并显示 `legacy_unverified` 迁移状态，不伪装成通过新验证。

```python
legacy_workflow_ids = {
    "video_minimax_h3": "2089723723468328961",
    "video_minimax_h3_ref": "2096502793044582401",
}
```

- [ ] 运行 `uv run pytest tests/media_capabilities/workflows/test_migration.py tests/media_capabilities/tts/test_runninghub_indextts2.py tests/media_capabilities/tts/test_runninghub_voice_design_runtime.py tests/media_capabilities/image/test_pipeline.py tests/media_capabilities/video/test_runninghub_h3.py -q`。
- [ ] 逐个迁移已知图入口，IndexTTS2 保留情绪互斥/向量约束和旧结果结构，音色设计保留声音描述契约，基础 H3 保留模式规则。对缺图条目显式只读查询远端图；账号未配置则记录迁移报告，旧入口继续可用。
- [ ] 对已迁移条目移除重复远端生命周期代码；未迁移条目保持旧分支，仅在迁移报告明确列出原因。不得因迁移不完整宣称所有内置工作流已统一。通过测试后提交 `refactor: migrate built-in RunningHub workflows compatibly`。

## 任务 11：交付与回归验收

文件：新增 `tests/media_capabilities/workflows/test_end_to_end.py`、`docs/zh/guides/runninghub-workflows.md`；按最终实现更新规格中的状态和限制。

- [ ] 添加 fake provider 端到端验收：定义未列入任何内置 ID 的 workflow，导入→补全 manifest→校验→注册→Python/CLI 执行→多产物下载；构造一次进程重启再恢复。同一个断言链验证不改枚举、不使用硬编码 ID、提交一次。
- [ ] 指南提供真实可执行的 ACE-Step manifest 示例、CLI 完整流程、Python async 调用、错误对照、升级版本与恢复示例。说明独立运行和 nuomi 生产入口的边界，不把示例描述为已执行的真实供应商验收。
- [ ] 运行 `uv run pytest tests/media_capabilities tests/music tests/test_runninghub_cli.py tests/test_api_media_capabilities.py tests/test_task_narrative_group_video_runner.py tests/test_narrative_group_video_settings.py tests/test_music_workflow_settings.py tests/costs -q`。这是阶段性综合回归，不在每个子任务后重复运行。
- [ ] 运行 `uv run pre-commit run gitleaks --all-files` 检查配置、凭据和网关相关变更；失败区分工具不可用和实际发现，不能跳过结果。随后 `git diff --check`，检查只包含本任务文件。
- [ ] 依据实际 API 实现涉及的字段查 RunningHub 官方文档；只读验证不会自动升级成付费试跑。若用户选择实际样本试跑，记录工作流版本/任务 ID/产物和最终播放结果，不做 ASR 或重复付费质量检查。
- [ ] 进行代码审查，修正会造成重复计费、丢输出、并发绕过或默认路由回归的问题后，仅复测受影响范围。提交 `docs: document generic RunningHub workflow integration` 并汇报测试证据、未迁移条目和供应商实测状态。

## 覆盖自检

| 规格要求 | 对应任务 |
| --- | --- |
| API JSON、包格式与版本 | 1、2、5 |
| 类型、默认值、编码、连线和部署图检查 | 1、3 |
| 文件及复杂适配器 | 4、9 |
| 多输出、恢复、未知提交、取消、重试 | 6、7 |
| Python/CLI 与动态注册 | 5、7 |
| 业务契约、默认路由和组级生产入口 | 8、9 |
| 内置兼容、迁移与旧任务 | 8、9、10 |
| 并发、费用、凭据与脱敏 | 4、6、7、11 |
| 两类样本与未列名工作流验收 | 8、9、11 |

执行方式默认为当前会话内联执行；如用户选择子代理，按任务依赖依次委派，避免同时修改 models/store/executor。独立探索或测试可并行，代码迁移不在共享入口上并行写入。
