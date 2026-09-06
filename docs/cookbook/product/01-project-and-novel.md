# 项目与小说

## 功能概览

创建或打开项目后，从导入页上传 `.txt`、`.md` 或 `.docx` 小说，也可以粘贴文本。系统先保存上传文件并返回章节预览与格式检查，再由用户启动 `ingest_fast` 长任务，生成正式原文和知识资产。

## 适合谁看

适合编剧、制片、测试和研发确认项目边界、原文是否正式导入，以及导入失败时应查看哪一层。

## 用户操作

1. 进入项目的「小说导入」页，选择文件或粘贴文本。
2. 检查章节预览和 `ok`、`warning`、`blocking` 级别的格式提示。
3. 选择知识管线和脊柱模板，点击「开始导入」。
4. 在任务中心查看进度；完成后刷新章节和知识图谱。

## 业务流程

```mermaid
flowchart LR
    A[选择小说或粘贴文本] --> B[上传并格式检查]
    B --> C[查看章节预览]
    C --> D[启动小说导入]
    D --> E{任务状态}
    E -->|completed| F[保存正式原文与知识产物]
    E -->|failed/cancelled| G[显示错误或取消结果]
```

1. 用户选择文件或粘贴文本。
2. 系统保存到项目 `uploads/`，检测章节并返回预览。
3. 用户启动 `ingest_fast`，任务进入排队和运行状态。
4. 成功后写入 `novel.txt` 与所选管线的正式产物；失败或取消时保留状态供重试和诊断。

## 业务规则

- 上传预览只在前端 Query cache 中标记为 `preview_only`，不代表正式导入完成。
- 当前支持 `.txt`、`.md`、`.docx`；文本按 UTF-8，失败后按 GBK 解码。
- 没有有效章节才会阻断上传；有章节但存在格式 warning 时仍可上传。
- `ingest_fast` 按项目配置选择 `structured_v1` 或 `cognee_legacy`，不是两个任务同时执行。
- 分集剧本应走分集预检与提交，不应当作小说上传处理。

## 状态与异常

导入任务通常经历 `submitting` → `queued` → `running` → `completed`、`failed` 或 `cancelled`。格式 warning 不等于任务失败；结构化导入失败会保留 `structured_failed`。分集来源提交成功后，`episode_graph_index` 仍可能处于排队或失败状态，应分别查看。

## 产品验收

- 上传后能看到章节预览、格式问题和修复建议。
- 点击开始后能看到任务键、进度、日志和终态。
- 成功后重新打开项目仍能读取正式章节；失败后能重试且不会误报为成功。
- 取消后任务最终为 `cancelled`，旧正式资产不会被无条件覆盖。

## 技术实现

页面与接口、解析器、Runner、存储和任务状态的调用链见[小说导入管线](../pipelines/01-ingest.md)；项目 `output`、`state`、`runtime` 三类目录和权限边界见[共享系统地图](../system-map.md)与[存储与项目文件](../development/storage-and-files.md)。

## 相关创作专题

- [剧集规划](02-episode-planning.md)
- [小说导入管线](../pipelines/01-ingest.md)
- [剧集图谱管线](../pipelines/02-episode-graph.md)
