# Nuomi Drama Factory 开发者 Cookbook

这组文档面向需要修改功能、理解实现原理或追踪生产管线的开发者。先按功能找到对应管线，再沿页面、API、任务、领域服务和数据产物向下追踪；共享机制只在系统地图和开发手册中解释。

## HTML 阅读入口

从仓库根目录运行以下命令，可以通过带侧边栏、全文搜索和 Mermaid 流程图的 HTML 文档站阅读本 Cookbook。Markdown 仍是唯一内容源，修改后开发服务器会自动刷新。

```bash
pnpm --dir docs/cookbook install  # 首次使用时安装独立的文档站依赖
pnpm docs:dev      # 启动本地文档站，终端会显示访问地址
pnpm docs:build    # 生成静态 HTML
pnpm docs:preview  # 预览最近一次静态构建
```

- 第一次启动项目：从[启动与本地开发](start-software.md)开始。
- 想先理解整体结构：阅读[共享系统地图](system-map.md)。
- 已经知道页面、接口或任务名：使用[功能反查手册](development/trace-a-feature.md)。

## 按修改目标直达

| 修改目标 | 首选入口 | 同时检查 |
| --- | --- | --- |
| 小说上传、格式解析、拆章或剧集来源 | [小说导入：常见修改](pipelines/01-ingest.md#常见修改) | [存储与项目文件](development/storage-and-files.md) |
| 剧集实体、关系、合并规则或断点恢复 | [剧集图谱：常见修改](pipelines/02-episode-graph.md#常见修改) | [API 与长任务](development/add-api-and-task.md) |
| 角色、场景、道具或候选资产采用 | [生产资产：常见修改](pipelines/03-production-assets.md#常见修改) | [存储与项目文件](development/storage-and-files.md) |
| 剧本文本、Beat、语义校验或修复 | [剧本与语义：常见修改](pipelines/04-screenplay.md#常见修改) | [功能反查](development/trace-a-feature.md) |
| NarrativeGroup、网格、草图或图像重生成 | [分镜与图像：常见修改](pipelines/05-storyboard.md#常见修改) | [API 与长任务](development/add-api-and-task.md) |
| 配音、旁白、声音选择或音频重生成 | [声音与音频：常见修改](pipelines/06-audio.md#常见修改) | [测试策略](development/testing-strategy.md) |
| 视频后端、参考帧、生成参数或候选采用 | [视频生成：常见修改](pipelines/07-video.md#常见修改) | [测试策略](development/testing-strategy.md) |
| 成片门禁、字幕、FFmpeg 合成或导出包 | [合成与导出：常见修改](pipelines/08-compose-export.md#常见修改) | [存储与项目文件](development/storage-and-files.md) |

如果只能看到故障现象，还不能确定所属模块，先从[共享系统地图](system-map.md)确认状态和数据流，再按[功能反查手册](development/trace-a-feature.md)从页面路由、HTTP 路径、任务类型或输出文件名定位。

## 核心生产管线

八篇专题按内容从输入到成片的顺序排列。它们分别记录功能边界、核心原理、端到端调用链、关键代码、数据落点、修改影响面和验证方式。

1. [小说导入](pipelines/01-ingest.md)：上传、格式检查、拆章与剧集来源写入。
2. [剧集图谱](pipelines/02-episode-graph.md)：分组抽取、实体关系合并、写入与检查点。
3. [生产资产](pipelines/03-production-assets.md)：角色、场景、道具的生成候选与采用版本。
4. [剧本与语义](pipelines/04-screenplay.md)：剧本生成、语义 revision、校验和修复。
5. [分镜与图像](pipelines/05-storyboard.md)：叙事分组、网格、草图、渲染与重生成。
6. [声音与音频](pipelines/06-audio.md)：对白、旁白、声音样本和 Beat 音频。
7. [视频生成](pipelines/07-video.md)：单 Beat、叙事组和分段视频的生成与采用。
8. [合成与导出](pipelines/08-compose-export.md)：前置门禁、字幕、成片合成和文件导出。

跨管线的应用结构、任务生命周期和项目目录约定统一见[共享系统地图](system-map.md)。

## 按代码层定位

| 已知线索 | 建议起点 | 继续追踪 |
| --- | --- | --- |
| 前端页面、按钮或路由 | 对应的[生产管线](#核心生产管线) | Query / mutation → API route → task scope |
| HTTP 路径或 API schema | [功能反查](development/trace-a-feature.md) | route → service / TaskBackend → Store |
| 长任务类型、进度或取消行为 | [API 与长任务](development/add-api-and-task.md) | task payload → Runner 注册 → 状态回传 |
| SQLite 记录、项目目录或媒体文件 | [存储与项目文件](development/storage-and-files.md) | ProjectContext → Store / writer → 下游消费者 |
| 不清楚共享组件的职责 | [共享系统地图](system-map.md) | 页面 → API → 任务 → 模型或 FFmpeg → 数据落点 |

追踪时优先搜索真实的路由、任务类型和文件名，不从目录名称猜测调用关系。修改跨层契约后，按[测试策略](development/testing-strategy.md)选择覆盖领域、API、任务和前端的最小验证集合。

## 开发与验证

- [启动与本地开发](start-software.md)：Docker Compose、本地前后端、端口与启动排错。
- [功能反查](development/trace-a-feature.md)：从页面、接口、任务或产物定位完整调用链。
- [新增 API 与长任务](development/add-api-and-task.md)：schema、route、payload、Runner、注册和前端 scope 的修改顺序。
- [存储与项目文件](development/storage-and-files.md)：SQLite、`state/`、`output/`、`runtime/` 与项目边界。
- [测试策略](development/testing-strategy.md)：按改动层选择后端、前端和契约测试。

开始修改前先记录当前可复现行为和代码基线；修改后验证直接受影响的层，再验证相邻契约。模型调用、媒体处理或 Docker 环境问题可从[启动页](start-software.md)进入已有排错文档。

## 扩展能力入口

以下能力与核心生产管线共用部分基础设施，但有独立的交互或运行边界。需要扩展时先从[共享系统地图](system-map.md)确认入口目录和公共依赖。

- Freezone：从 `frontend/src/routes/_app/projects.$project/freezone.lazy.tsx`、`src/novelvideo/api/routes/freezone.py` 和 `src/novelvideo/freezone/` 开始追踪。
- 导演世界：从 `frontend/src/features/viewer-kit/three-d/ThreeDDirectorDialog.tsx` 和 `src/novelvideo/director_world/` 开始追踪。
- 聊天助手：从 `frontend/src/features/superchat/superchat-panel.tsx`、`src/novelvideo/api/routes/chat.py` 和 `src/novelvideo/chat/` 开始追踪。
- 模型配置：从 `frontend/src/components/settings/settings-dialog.tsx`、`frontend/src/lib/queries/model-gateway.ts` 和 `src/novelvideo/api/routes/model_gateway.py` 开始追踪。
- 桌面壳：从 Electron 入口 `desktop/main.cjs`、运行路径处理 `desktop/runtime-paths.cjs` 和打包脚本 `desktop/scripts/stage-runtime.cjs` 开始追踪。

## 文档维护约定

- 以当前代码、注册表、schema 和测试为证据；行为发生变化时，文档与实现一同修改。
- 路径一律写成仓库相对路径，符号写到足以用 `rg` 定位的函数、类型或任务名。
- 共享机制只维护在系统地图或横向手册；管线专题通过相对链接引用，不复制长段源码和参数表。
- 每篇管线都说明上游、下游、数据与产物、常见修改场景、失败诊断和可执行的验证命令。
- 新增页面时补齐返回首页和相关上下游链接，让文档能从功能入口双向导航。
