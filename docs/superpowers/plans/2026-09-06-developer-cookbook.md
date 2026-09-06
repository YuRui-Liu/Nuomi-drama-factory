# DramaClaw 开发者 Cookbook 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 建立 Wiki 式开发者 Cookbook，让开发者能从功能名称追踪核心生产管线的原理、前后端调用链、数据落点、修改影响面和验证方式。

**架构：** 文档分为功能入口、共享系统地图、横向开发手册和八篇纵向管线专题。共享机制只在一个权威页面解释；管线专题用普通 Markdown 相对链接组成知识网络，并以当前代码、注册表和契约测试为证据。

**技术栈：** Markdown、Mermaid、Python/FastAPI、React/TypeScript、TanStack Router/Query、TaskBackend、SQLite、pytest、Vitest。

---

## 文件结构

**创建：**

- `docs/cookbook/README.md`：按「我要修改什么」组织的入口。
- `docs/cookbook/system-map.md`：共享架构和状态流。
- `docs/cookbook/development/trace-a-feature.md`：功能反查方法。
- `docs/cookbook/development/add-api-and-task.md`：API 与长任务扩展规范。
- `docs/cookbook/development/storage-and-files.md`：SQLite 与项目文件边界。
- `docs/cookbook/development/testing-strategy.md`：按改动层选择测试。
- `docs/cookbook/pipelines/01-ingest.md`：小说导入。
- `docs/cookbook/pipelines/02-episode-graph.md`：剧集图谱。
- `docs/cookbook/pipelines/03-production-assets.md`：角色、场景、道具资产。
- `docs/cookbook/pipelines/04-screenplay.md`：剧本与语义处理。
- `docs/cookbook/pipelines/05-storyboard.md`：分镜与图像。
- `docs/cookbook/pipelines/06-audio.md`：声音与音频。
- `docs/cookbook/pipelines/07-video.md`：视频生成。
- `docs/cookbook/pipelines/08-compose-export.md`：合成与导出。

**修改：**

- `docs/cookbook/start-software.md`：保留用户草稿，增加 Wiki 导航并减少重复。
- `docs/zh/README.md`：增加 Cookbook 入口。
- `docs/zh/technical-design.md`：说明概览与 Cookbook 的边界。

## 通用专题模板

每篇管线专题顶部写所属阶段、上游、下游、相关手册和代码核对基线 `55504a0`。正文固定为「功能边界 → 核心原理 → 端到端调用链 → 关键代码索引 → 数据与产物 → 常见修改场景 → 失败与诊断 → 验证方式 → 继续追踪」。代码索引精确到文件与符号，不复制长段源码或完整参数表。

### 任务 1：建立 Cookbook 首页和链接骨架

**文件：**
- 创建：`docs/cookbook/README.md`
- 修改：`docs/cookbook/start-software.md`

- [ ] **步骤 1：确认代码基线与现有草稿**

运行：`git rev-parse --short HEAD~1 && git status --short docs/cookbook`

预期：基线为 `55504a0`，`docs/cookbook/` 是未跟踪目录。

- [ ] **步骤 2：创建首页**

写入「我要修改什么」「核心生产管线」「按代码层定位」「开发与验证」「扩展能力入口」「文档维护约定」六个分区。前四个分区链接八篇管线、系统地图、启动页和四篇横向手册。

- [ ] **步骤 3：整理启动页**

保留 `start-software.md` 中已核实的 Docker、本地开发、端口和排错命令；把本机绝对路径改为 `<repo-root>`，顶部增加首页、系统地图、现有排错和模型配置链接。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/README.md docs/cookbook/start-software.md
git commit -m "docs(cookbook): add developer wiki entry"
```

### 任务 2：编写共享系统地图

**文件：**
- 创建：`docs/cookbook/system-map.md`
- 参考：`src/novelvideo/api/__init__.py`
- 参考：`src/novelvideo/api/app.py`
- 参考：`src/novelvideo/ports/registry.py`
- 参考：`src/novelvideo/ports/local/tasks.py`
- 参考：`src/novelvideo/task_backend/registry.py`
- 参考：`src/novelvideo/task_backend/run_core.py`
- 参考：`src/novelvideo/task_state.py`
- 参考：`frontend/src/task-center/provider.tsx`

- [ ] **步骤 1：核对共享入口**

运行：

```bash
rg -n 'api_router|include_router|create_app|register_project_task_runner|enqueue_project_task|EventSource|task_id' src/novelvideo/api src/novelvideo/ports src/novelvideo/task_backend frontend/src/task-center
```

预期：定位 API 注册、应用创建、任务注册/入队和前端任务订阅。

- [ ] **步骤 2：写系统图和任务生命周期**

用 Mermaid 表达「页面 → Query → API → ProjectContext → Service/TaskBackend → Runner → 模型/FFmpeg → Store/文件 → 任务中心」。说明 `queued → running → succeeded/failed/cancelled` 与 CE `InlineTaskBackend`。

- [ ] **步骤 3：写数据目录与扩展入口**

解释 `state/`、`output/`、`runtime/`；用表格列出 Freezone、导演世界、聊天助手、模型配置和 Electron 的起始目录，不展开内部管线。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/system-map.md
git commit -m "docs(cookbook): map shared system flow"
```

### 任务 3：编写功能追踪手册

**文件：**
- 创建：`docs/cookbook/development/trace-a-feature.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/compose.lazy.tsx`
- 参考：`frontend/src/lib/queries/video.ts`
- 参考：`src/novelvideo/api/routes/generation.py`
- 参考：`src/novelvideo/task_backend/runners/video.py`

- [ ] **步骤 1：核对合成样例链**

运行：`rg -n 'useComposeEpisode|compose_video|compose_episode|run_compose_episode|useFinalVideo' frontend/src src/novelvideo`

预期：页面 Query、API、任务类型、Runner 和最终结果查询均有匹配。

- [ ] **步骤 2：写四种反查方法**

分别从页面路由、HTTP 路径、任务类型和输出文件名反查，给出使用上述真实符号的 `rg` 命令。

- [ ] **步骤 3：写影响面清单**

固定检查前端类型与 Query key、API schema、task payload、Runner 注册、Store/文件、任务中心、契约测试和中英文文案。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/development/trace-a-feature.md
git commit -m "docs(cookbook): add feature tracing guide"
```

### 任务 4：编写 API 与任务扩展手册

**文件：**
- 创建：`docs/cookbook/development/add-api-and-task.md`
- 参考：`src/novelvideo/api/__init__.py`
- 参考：`src/novelvideo/ports/tasks.py`
- 参考：`src/novelvideo/task_backend/registry.py`
- 参考：`src/novelvideo/task_backend/run_core.py`
- 参考：`src/novelvideo/task_backend/cancel.py`
- 参考：`src/novelvideo/task_backend/runners/ingest.py`
- 测试：`tests/test_task_backend_registry.py`
- 测试：`tests/test_task_run_core_registration_failure.py`

- [ ] **步骤 1：核对任务契约**

运行：`rg -n 'enqueue_project_task|register_project_task_runner|ingest_fast|cancel' src/novelvideo/api/routes/ingest.py src/novelvideo/task_backend src/novelvideo/ports`

预期：定位入队、Runner 注册、统一执行和取消入口。

- [ ] **步骤 2：写扩展顺序**

说明短请求与长任务的分流，并给出实际修改顺序：schema → route → task type/payload → Runner → 注册 → 前端 task scope → 测试。

- [ ] **步骤 3：写状态与验证规则**

说明进度、错误、取消、幂等和禁止路由自行创建线程；记录验证命令：`uv run pytest tests/test_task_backend_registry.py tests/test_task_run_core_registration_failure.py -q`，预期全部通过。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/development/add-api-and-task.md
git commit -m "docs(cookbook): document API and task extensions"
```

### 任务 5：编写存储和测试手册

**文件：**
- 创建：`docs/cookbook/development/storage-and-files.md`
- 创建：`docs/cookbook/development/testing-strategy.md`
- 参考：`src/novelvideo/project_context.py`
- 参考：`src/novelvideo/config.py`
- 参考：`src/novelvideo/sqlite_store.py`
- 参考：`src/novelvideo/api/deps.py`
- 参考：`pyproject.toml`
- 参考：`frontend/package.json`

- [ ] **步骤 1：核对路径与 Store**

运行：`rg -n 'class ProjectContext|STATE_DIR|OUTPUT_DIR|RUNTIME_DIR|class SQLiteStore|get_project' src/novelvideo/project_context.py src/novelvideo/config.py src/novelvideo/sqlite_store.py src/novelvideo/api/deps.py`

预期：定位项目作用域、三类目录和 Store 生命周期入口。

- [ ] **步骤 2：写存储边界**

说明结构化数据、用户产物、临时/日志数据的位置；记录路径越界防护、禁止从 cwd 拼路径和 Store 关闭要求。

- [ ] **步骤 3：写测试矩阵**

从 `pyproject.toml`、`frontend/package.json` 提取真实命令，按领域函数、Store、API、任务、前端 Query、页面和契约测试列出最小测试选择。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/development/storage-and-files.md docs/cookbook/development/testing-strategy.md
git commit -m "docs(cookbook): document storage and tests"
```

### 任务 6：编写小说导入管线

**文件：**
- 创建：`docs/cookbook/pipelines/01-ingest.md`
- 参考：`frontend/src/routes/_app/projects.$project/ingest.tsx`
- 参考：`frontend/src/lib/queries/ingest.ts`
- 参考：`src/novelvideo/api/routes/ingest.py`
- 参考：`src/novelvideo/task_backend/runners/ingest.py`
- 参考：`src/novelvideo/structured_ingest.py`
- 参考：`src/novelvideo/episode_sources.py`
- 测试：`tests/test_task_episode_import_runner.py`

- [ ] **步骤 1：核对调用链**

运行：`rg -n 'useUploadNovel|useStartIngest|upload_novel|start_ingest|ingest_fast|run_ingest' frontend/src/lib/queries/ingest.ts frontend/src/routes/_app/projects.\$project/ingest.tsx src/novelvideo/api/routes/ingest.py src/novelvideo/task_backend/runners/ingest.py`

预期：串起上传、格式检查、启动任务和 Runner。

- [ ] **步骤 2：写原理和时序**

区分上传、格式检查、摄取任务及章节/剧集来源写入；记录 task payload、状态回传、查询失效和数据落点。

- [ ] **步骤 3：写修改场景**

覆盖新增输入格式、改变拆章规则、增加输出字段和修改进度阶段，逐项列出前端类型、API、解析器/Runner、Store 和测试影响面。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/01-ingest.md
git commit -m "docs(cookbook): trace ingest pipeline"
```

### 任务 7：编写剧集图谱管线

**文件：**
- 创建：`docs/cookbook/pipelines/02-episode-graph.md`
- 参考：`src/novelvideo/task_backend/runners/episode_graph.py`
- 参考：`src/novelvideo/episode_graph/models.py`
- 参考：`src/novelvideo/episode_graph/grouping.py`
- 参考：`src/novelvideo/episode_graph/extractor.py`
- 参考：`src/novelvideo/episode_graph/merge.py`
- 参考：`src/novelvideo/episode_graph/writer.py`
- 参考：`src/novelvideo/episode_graph/checkpoints.py`
- 参考：`src/novelvideo/episode_graph/service.py`
- 测试：`tests/episode_graph/`
- 测试：`tests/test_task_episode_graph_runner.py`

- [ ] **步骤 1：核对阶段符号**

运行：`rg -n 'episode_graph_index|EpisodeGraphBuildService|group_episode_sources|extract_groups|merge_extractions|EpisodeGraphWriter|EpisodeGraphCheckpointStore' src/novelvideo tests/episode_graph tests/test_task_episode_graph_runner.py`

预期：定位任务、分组、抽取、合并、写入和检查点。

- [ ] **步骤 2：写领域关系**

解释 source、group、extraction、merged graph 和 checkpoint；用 Mermaid 标出分组抽取可并行，合并与写入有顺序依赖。

- [ ] **步骤 3：写修改场景**

覆盖分组策略、实体/关系属性、冲突合并、写图后端和断点恢复，并对应 `tests/episode_graph/` 的具体模块。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/02-episode-graph.md
git commit -m "docs(cookbook): trace episode graph pipeline"
```

### 任务 8：编写生产资产管线

**文件：**
- 创建：`docs/cookbook/pipelines/03-production-assets.md`
- 参考：`frontend/src/routes/_app/projects.$project/characters.lazy.tsx`
- 参考：`frontend/src/lib/queries/characters.ts`
- 参考：`frontend/src/lib/queries/scenes.ts`
- 参考：`frontend/src/lib/queries/props.ts`
- 参考：`frontend/src/lib/queries/production-assets.ts`
- 参考：`src/novelvideo/api/routes/characters.py`
- 参考：`src/novelvideo/api/routes/scenes.py`
- 参考：`src/novelvideo/api/routes/props.py`
- 参考：`src/novelvideo/api/routes/production_assets.py`
- 参考：`src/novelvideo/task_backend/runners/character_image.py`
- 参考：`src/novelvideo/task_backend/runners/scene_reference.py`
- 参考：`src/novelvideo/task_backend/runners/prop_reference.py`
- 参考：`src/novelvideo/character_visual/`
- 参考：`src/novelvideo/production_workflow/`
- 测试：`tests/character_visual/`
- 测试：`tests/production_workflow/`

- [ ] **步骤 1：核对生成与采用入口**

运行：`rg -n 'character_portrait|identity_image|scene_reference_asset|prop_reference_asset|adopt_version|register_candidate|ProductionWorkflowStore' src/novelvideo frontend/src/lib/queries tests/production_workflow`

预期：三类生成任务和候选版本采用均可定位。

- [ ] **步骤 2：写资产模型和子管线**

区分角色身份/视觉设定、场景、道具、episode-specific 资产和 canonical slot；展示三类 Runner 如何汇入 candidate → adopted 版本层。

- [ ] **步骤 3：写修改场景**

覆盖资产字段、生成提示词、来源类型、采用策略和路径迁移，列出领域、API、前端、历史兼容和测试影响。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/03-production-assets.md
git commit -m "docs(cookbook): trace production assets"
```

### 任务 9：编写剧本与语义管线

**文件：**
- 创建：`docs/cookbook/pipelines/04-screenplay.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx`
- 参考：`frontend/src/lib/queries/scripts.ts`
- 参考：`frontend/src/lib/queries/screenplay-semantics.ts`
- 参考：`src/novelvideo/api/routes/scripts.py`
- 参考：`src/novelvideo/api/routes/screenplay_semantics.py`
- 参考：`src/novelvideo/workflows/script_writing.py`
- 参考：`src/novelvideo/screenplay_semantics/`
- 参考：`src/novelvideo/task_backend/runners/screenplay_semantics.py`
- 参考：`src/novelvideo/task_backend/runners/screenplay_semantic_repair.py`
- 测试：`tests/screenplay_semantics/`
- 测试：`tests/acceptance/test_screenplay_semantic_pipeline.py`

- [ ] **步骤 1：核对入口**

运行：`rg -n 'useGenerateRewrite|generate_script|useCreateScreenplaySemantics|screenplay_semantics|screenplay_semantic_repair|ScreenplaySemanticService|ScreenplaySemanticStore' frontend/src src/novelvideo tests`

预期：剧本生成、语义任务、修复任务、服务和 revision Store 可定位。

- [ ] **步骤 2：写两层模型**

说明剧本文本/Beat 与语义 revision 的关系，以及 parse → extract → validate → repair/edit → activate 的状态流。

- [ ] **步骤 3：写修改场景**

覆盖剧本模板、Beat 字段、语义校验、编辑命令和激活规则，指出保存兼容、前端类型和下游分镜/视频影响。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/04-screenplay.md
git commit -m "docs(cookbook): trace screenplay pipeline"
```

### 任务 10：编写分镜与图像管线

**文件：**
- 创建：`docs/cookbook/pipelines/05-storyboard.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/sketches.lazy.tsx`
- 参考：`frontend/src/lib/queries/sketches.ts`
- 参考：`frontend/src/lib/queries/narrative-groups.ts`
- 参考：`src/novelvideo/api/routes/generation.py`
- 参考：`src/novelvideo/api/routes/narrative_groups.py`
- 参考：`src/novelvideo/narrative_groups/`
- 参考：`src/novelvideo/render_plan/`
- 参考：`src/novelvideo/task_backend/runners/narrative_group.py`
- 参考：`src/novelvideo/task_backend/runners/sketch.py`
- 参考：`src/novelvideo/task_backend/runners/render.py`
- 测试：`tests/test_narrative_group_service.py`

- [ ] **步骤 1：核对任务**

运行：`rg -n 'narrative_group_grid|narrative_group_split|sketch_generation|selected_regen|sketch_regen|grid_regenerate|render_plan|render_execute' src/novelvideo frontend/src/lib/queries tests`

预期：定位分组、网格、草图、渲染和重生成入口。

- [ ] **步骤 2：写模型关系**

解释 Beat、NarrativeGroup、stage revision、Grid/cell、草图/渲染候选和 pool 选择；标明逐 Beat 与分组路径的交点。

- [ ] **步骤 3：写修改场景**

覆盖重建/回滚分组、网格布局、图像模型、参考图选择和单 Beat 重生成，记录任务 scope、产物和查询失效点。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/05-storyboard.md
git commit -m "docs(cookbook): trace storyboard pipeline"
```

### 任务 11：编写音频管线

**文件：**
- 创建：`docs/cookbook/pipelines/06-audio.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/audio.lazy.tsx`
- 参考：`frontend/src/lib/queries/audio.ts`
- 参考：`src/novelvideo/api/routes/generation.py`
- 参考：`src/novelvideo/task_backend/runners/audio.py`
- 参考：`src/novelvideo/audio/indextts2_beat_audio_task.py`
- 参考：`src/novelvideo/seedance2_i2v/voice_audio_task.py`
- 参考：`src/novelvideo/seedance2_i2v/narration_audio_task.py`
- 测试：`tests/test_voice_design_runner.py`
- 测试：`tests/test_seedance2_voice_clone.py`

- [ ] **步骤 1：核对入口**

运行：`rg -n 'useGenerateAudio|useRegenerateBeatAudio|generate_audio|regenerate_beat_audio|run_indextts2_beat_audio_generation|audio_generation' frontend/src src/novelvideo tests`

预期：定位前端 mutation、API、Runner 和 IndexTTS2 执行函数。

- [ ] **步骤 2：写声音解析和产物规则**

说明对白/旁白、角色/身份声音样本优先级、前置检查、整集/选定 Beat 模式，以及音频路径和时长对下游的影响。

- [ ] **步骤 3：写修改与诊断**

覆盖新增 TTS 后端、声音选择、文件格式和重生成粒度；记录缺失声音、模型失败、任务状态和残留产物的检查位置。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/06-audio.md
git commit -m "docs(cookbook): trace audio pipeline"
```

### 任务 12：编写视频管线

**文件：**
- 创建：`docs/cookbook/pipelines/07-video.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/video.lazy.tsx`
- 参考：`frontend/src/lib/queries/video.ts`
- 参考：`frontend/src/lib/queries/narrative-groups.ts`
- 参考：`src/novelvideo/api/routes/generation.py`
- 参考：`src/novelvideo/api/routes/narrative_groups.py`
- 参考：`src/novelvideo/task_backend/runners/video.py`
- 参考：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 参考：`src/novelvideo/media_capabilities/video/`
- 参考：`src/novelvideo/generators/video_generator.py`
- 测试：`tests/media_capabilities/video/`
- 测试：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：核对三类生成路径**

运行：`rg -n 'single_video|narrative_group_video|narrative_group_video_segment|generate_single_video|generate_video_group|run_single_video' frontend/src src/novelvideo tests`

预期：单 Beat、整组和单 segment 路径可定位。

- [ ] **步骤 2：写请求准备与后端路由**

说明 backend/profile、参考图/首尾帧、语音、提示词、resolver/adapter 和候选池；区分共享层与 Seedance、Higgsfield/Minimax H3 特有层。

- [ ] **步骤 3：写修改场景**

覆盖 backend/profile、请求参数、提示词、质量判定、候选采用和重试；列出 API、前端 options、adapter、计量、Runner 和测试影响。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/07-video.md
git commit -m "docs(cookbook): trace video pipeline"
```

### 任务 13：编写合成与导出管线

**文件：**
- 创建：`docs/cookbook/pipelines/08-compose-export.md`
- 参考：`frontend/src/routes/_app/projects.$project/episodes.$episode/compose.lazy.tsx`
- 参考：`frontend/src/lib/queries/video.ts`
- 参考：`src/novelvideo/api/routes/generation.py`
- 参考：`src/novelvideo/task_backend/runners/video.py`
- 参考：`src/novelvideo/generators/video_composer.py`
- 参考：`src/novelvideo/export/episode_export.py`
- 参考：`src/novelvideo/api/routes/pipeline.py`
- 测试：`tests/contract/`

- [ ] **步骤 1：核对合成与导出**

运行：`rg -n 'useComposeEpisode|useFinalVideo|compose_video|compose_episode|run_compose_episode|export_srt|export_final_video|export_zip|build_episode_' frontend/src src/novelvideo tests`

预期：合成任务、结果查询、SRT、视频和 ZIP 导出可定位。

- [ ] **步骤 2：写门禁和 FFmpeg 数据流**

说明每个 Beat 的音视频前置、`pipeline/status`、合成参数、字幕、最终命名和重新合成行为。

- [ ] **步骤 3：写修改场景**

覆盖门禁、分辨率/帧率、字幕格式、导出包内容和最终文件名，指出页面、API 下载、composer/export helper 和测试影响。

- [ ] **步骤 4：提交**

```bash
git add docs/cookbook/pipelines/08-compose-export.md
git commit -m "docs(cookbook): trace compose and export"
```

### 任务 14：接入中文文档并全量校验

**文件：**
- 修改：`docs/zh/README.md`
- 修改：`docs/zh/technical-design.md`
- 修改：`docs/cookbook/README.md`
- 验证：`docs/cookbook/**/*.md`

- [ ] **步骤 1：增加入口并补齐双向链接**

在 `docs/zh/README.md` 新增「开发」分组；在 `docs/zh/technical-design.md` 开头链接 Cookbook。确认首页能到达每篇专题，每篇专题能回到首页并进入上游、下游或共享手册。

- [ ] **步骤 2：扫描占位符和绝对路径**

运行：`rg -n 'TODO|TBD|待定|稍后补充|/Users/|E:\\' docs/cookbook docs/zh/README.md docs/zh/technical-design.md`

预期：无匹配。

- [ ] **步骤 3：验证相对链接**

运行：

```bash
uv run python -c 'import pathlib,re,sys; root=pathlib.Path("docs/cookbook"); bad=[]; [(bad.append(f"{p}:{u}")) for p in root.rglob("*.md") for u in re.findall(r"\[[^]]+\]\(([^)#]+\.md)(?:#[^)]+)?\)", p.read_text()) if not (p.parent/u).resolve().exists()]; print("\n".join(bad)); sys.exit(bool(bad))'
```

预期：无输出，退出码为 0。

- [ ] **步骤 4：抽查关键符号并检查差异**

运行：

```bash
for symbol in ingest_fast episode_graph_index character_portrait screenplay_semantics narrative_group_grid audio_generation single_video compose_episode; do rg -q "$symbol" src/novelvideo || exit 1; done
git diff --check
git status --short
```

预期：符号检查和 `git diff --check` 无输出；状态只包含计划内文档。

- [ ] **步骤 5：提交并记录证据**

```bash
git add docs/cookbook docs/zh/README.md docs/zh/technical-design.md
git commit -m "docs: publish developer cookbook"
git log -1 --oneline
git status --short
```

预期：最新提交为 `docs: publish developer cookbook`，工作区不再显示本计划涉及的文件。
