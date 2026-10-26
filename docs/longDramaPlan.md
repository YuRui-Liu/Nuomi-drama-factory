# Toonflow 长漫剧与 Codex E1 验收实现计划

> 面向 AI 代理的执行者：使用 executing-plans 技能逐任务执行，以复选框跟踪进度。用户已批准执行；完成状态以执行记录及实际验证为准。

**目标：** Codex 从一句话创意开始，经关键阶段确认，完成 E1 真实成片、视听验收和局部返修，再以 E2 验证跨集复用。

**架构：** 延用 Toonflow 插件、MCP、工作区文件和媒体供应商机制。节点工作台与外部 Codex 共用业务操作，结果及依赖版本存入项目文件；执行任务持久化，画布只展示成果。H3 规划与 RunningHub 提交分离，独立配音先确定实际时间，再规划视频。

**技术栈：** TypeScript、Bun、Express、Vue、Zod、现有 MCP SDK、@toonflow/file、现有 FFmpeg 插件；不迁移 Nuomi Python 后端。

**规格：** [longDramaDesign.md](./longDramaDesign.md)，用户已确认，含 Codex 全流程操作和 E1 验收硬要求。

**仓库规则优先：** 不新增任何测试／自检代码、测试依赖或测试命令；使用已有类型检查、构建及手动实际验证。文档使用小驼峰名称。业务代码实施时复核最新工作区，不覆盖现有 packages/mcp/src/stdio.ts 未提交改动，不提交参考目录 OpenMontage/、runnninghub/ 的无关内容。

### 执行记录（2026-10-27）

- 用户已批准在当前目录新建功能分支，执行分支为 codex/longDrama，保留 stdio.ts 原有改动。
- 任务 1：源码核对与 RunningHub 官方查询文档已完成；当前只有临时服务的实时 MCP 能力证据，尚未验证用户云端 H3 图及付费模式，G0 不标全部通过。音色设计参考映射为节点 14=text、15=instruct；IndexTTS2 必须区分单选择器图和多分支图。
- 任务 2：已实现共享结构、不可变修订、采用冲突与依赖追踪。提前接入任务 4 的只读／写入 HTTP、MCP 入口，以便通过真实公开接口验证，不创建自检脚本。
- 手动 HTTP：新建→采用→第二修订成功；旧 expectedRevision 返回 409；第二版保存后采用仍指向第一版；工作区越界请求返回 403。临时数据目录为 /private/tmp/toonflowLongDramaData，不改用户配置。
- 实时 MCP：tools/list 可发现 productionRead／productionExecute，productionRead 返回与 HTTP 一致的第二版及第一版采用记录。没有界面连接也能读取已授权服务器工作区。
- 已有检查：nodeShared typecheck、MCP typecheck 和 server build 通过。server typecheck 仅剩原有 .hutch/devkit/api/config/ElectrobunConfig 缺失；不生成占位声明掩盖错误。
- 计划命令修正：providers 包没有 typecheck script，执行既有编译器 `bun run tsc --project packages/providers/tsconfig.json`，不新增检查命令。
- 尚未实现持久生成任务、媒体证据通路、工作台及 E1 制作，不将本阶段视为 E1 验收通过。

## 里程碑与顺序

| 关卡 | 任务 | 可交付结果 |
| --- | --- | --- |
| G0 | 1 | 能力证据与 H3 五模式接入清单，缺项有明确阻塞原因 |
| G1 | 2–4 | Codex 可读写版本化成果，任务可恢复，MCP 读取真实媒体 |
| G2 | 5–6 | 题材立意、全剧规划、E1 剧本及跨集资产可通过工具操作 |
| G3 | 7–9 | 真实音色、配音、H3 格式规划及视频生成链路 |
| G4 | 10–11 | E1 成片与 Codex 验收证据、局部修复闭环 |
| G5 | 12 | E2 复用作品资产并形成第二集成片 |

任务按顺序推进，每项拆为单个可审阅操作。G0 的供应商事实核验和 G1 的媒体证据读取不能拖到最后；否则会出现界面完成而 Codex 无法验收的问题。

## 文件职责与复用位置

以下均相对仓库根目录。新文件严格小驼峰；现有 package.json、SKILL.md 等工具规定文件名沿用其契约，不另造命名替代物。

| 位置 | 责任与变更 |
| --- | --- |
| packages/nodeShared/src/production/types.ts、schemas.ts、operations.ts | 新建共享制作对象、Zod 边界与业务命令；UI、服务端适配和工具共用 |
| packages/nodeShared/src/production/h3Plan.ts | 新建模式选择、提示词格式和分段校验，独立于 HTTP 与 Vue |
| packages/nodeShared/package.json | 增加上述公开子路径 exports；服务端仅导入纯 TS 模块 |
| apps/server/src/utils/production/store.ts、operations.ts、tasks.ts | 工作区授权、原子持久化、版本冲突、任务恢复及依赖标记 |
| apps/server/src/routes/production/get.ts、execute.ts | 单文件单接口的读取和执行入口，工作台与 MCP 共用同一业务函数 |
| apps/server/src/utils.ts | 暴露 production 业务模块；路由不重复初始化 |
| apps/server/src/utils/media/tasks.ts、generation.ts、provider.ts | 复用生成链路，接入可恢复供应商任务；保留旧供应商兼容 |
| packages/providers/types.d.ts、index.ts | 声明可选持久任务生命周期并导出类型，不修改所有供应商的必选契约 |
| packages/providers/src/media/runninghub.ts | 新增 RunningHub 上传、提交、查询、结果处理与模式映射 |
| apps/server/src/utils/mcp/tools.ts、resources.ts | 暴露制作操作及受控媒体资源；复用既有 target 校验 |
| packages/mcp/skills/toonflow/SKILL.md | 补充制作操作发现、阶段确认、恢复和证据读取说明 |
| packages/nodes/storyCreationNode/src/index.vue、ideaWorkspace.vue | 题材立意成果节点和 Nuomi 式双步骤工作台 |
| packages/nodes/h3PlannerNode/src/index.vue、plannerWorkspace.vue | 简洁的片段规划与中文成果审阅工作台 |
| packages/nodes/audioGenerationNode/src/index.vue、audioNode/src/index.vue | 复用声音生成和播放器，补充音色／配音业务关联及公开操作 |
| packages/nodes/videoNode/src/index.vue、videoGenerationNode/src/index.vue | 复用视频展示／生成，绑定采用版本和实际任务状态 |
| packages/nodes/frameAnalysisNode/src/videoAnalysis.ts、aiAnalysis.ts、index.vue | 复用逐帧、场景与模型分析；补齐同等公开操作 |
| packages/tools/ffmpeg/src/index.ts | 优先复用已有 ffmpeg／ffprobe；不另造底层转码执行器 |
| packages/tools/mediaGeneration/src/index.ts、runtime.ts | 延用媒体模型查询与请求校验，返回可持久查询的任务信息 |
| packages/skills/workflow/SKILL.md 及其 references/ | 调整模型路由，增加 longDrama.md、h3Planning.md、episodeDelivery.md 阶段参考 |

新节点的包清单、tsconfig 与构建入口按现有节点包最小配置创建，新构建文件使用 viteConfig.ts 并由 package script 显式引用。不要复制已有 PascalCase 文件或新建短横线文件。新增依赖仅限已有工作区包与实际使用的 Zod 等已安装库。

## 任务 1：能力核对与接口基线（G0）

**读取：** packages/providers/types.d.ts、apps/server/src/utils/media/generation.ts、provider.ts、tasks.ts、apps/server/src/utils/mcp/resources.ts、packages/tools/ffmpeg/src/index.ts、packages/nodes/frameAnalysisNode/src/index.vue、用户指定 H3 JSON，以及 Nuomi 对应适配器。

- [ ] 读取当前 AGENTS.md 和更深目录规范、Git 状态；确认与其他工作互不覆盖。对将编辑路径按 Git 根读取 Weaver 配置；apply_patch 由 hook 记录，Bash 内容修改按规则成对采集。
- [ ] 从当前真实 MCP schema 核对项目打开、节点函数发现、模型查询、文件读取、FFprobe、截帧、音频分析和合成能力。将每项的真实工具名、输入、输出和限制写入本计划的执行记录，禁止用名称猜测接口。
- [ ] 核对 Nuomi 音色设计和 IndexTTS2 工作流真实节点及输出选择；与 H3 ID 分开配置。配置缺失时返回缺项，不借用旧环境中的凭据或盲猜工作流 ID。
- [ ] 追踪新版 H3 JSON 中导演节点的模型实际上游，核对 Ref2VA 权重与 FL2V LoRA、五种模式、合法帧数和 continuity 字段的实际节点实现。不能确认的模式记为不可提交，不能仅靠改 task_type 放行。
- [ ] 查询 RunningHub 官方提交、上传、状态及取消接口，记录能否按请求标识查询未知提交；没有该能力时，提交结果未知必须进入 needsReview，禁止自动重提。
- [ ] 核对已配置媒体分析模型能接收的图片／音频／视频方式。至少找到可让 Codex 获得视觉证据和真实对白音频分析的路径；不足则在任务 4 补公开产品能力，不能把音频路径或文字稿当作已听取。

**完成判据：** 未发起付费生成，已明确实现接口与事实边界；付费验证放在具体生成方案获批后。发现模式不兼容时给出需要修正的权重／节点配置，不降低五模式目标或伪报可用。

## 任务 2：共享制作对象与版本化操作

**文件：** 新建 nodeShared 的 production 三个基础模块、server production/store.ts 与 operations.ts，修改 nodeShared/package.json、server/utils.ts。

- [ ] 定义最小公共结构，内容按 kind 用 Zod 判别联合验证，不能以任意 JSON 绕过业务字段约束：

```ts
export type ProductionKind = "brief" | "idea" | "series" | "episode" | "script" | "asset" | "voice" | "dialogue" | "h3Plan" | "clip" | "film" | "acceptance";
export type ProductionRef = { id: string; revision: number };
export type ProductionRecord = {
  id: string;
  kind: ProductionKind;
  revision: number;
  episodeId?: string;
  dependencies: ProductionRef[];
  contentPath: string;
};
export type Adoption = { subjectId: string; selected: ProductionRef; approvalId: string };
```

- [ ] 在 schemas.ts 分别校验立意四字段、作品设定、分集剧情、对白说话人及原文、媒体相对路径、音色版本、H3 模式与参考、合成条目和验收证据。错误返回具体字段；拒绝未知版本与跨工作区引用。
- [ ] 固定文件布局：工作区 production/manifest.json 保存索引和当前采用引用；production/objects/<id>/revision<N>.json 保存不可变内容；production/tasks/<id>.json 保存任务；媒体在 assets/production/ 下。ID 使用随机 UUID 去掉分隔符并加小写语义前缀，防止用户输入进入路径。
- [ ] store.ts 复用现有工作区授权与路径锁。新增修订使用 exclusive；先写不可变修订，再 writeAtomic 更新索引；未引用的修订不算采用。每次修改要求 expectedRevision，不匹配返回 409，不覆盖用户最新改动。
- [ ] 实现 read、list、saveRevision、adopt、listAffected 五种业务操作，adopt 检查确认记录关联当前版本；按依赖版本标记受影响对象，保留旧结果。共用操作接受固定目录，跨 await 不重新读取当前项目。
- [ ] 用现有运行入口在临时工作区手动创建简报与两次修订、采用一次，再提交旧 expectedRevision；预期拒绝旧请求且原采用不变。仅记录操作结果，不创建验证程序。
- [ ] 执行 `bun run --cwd packages/nodeShared typecheck`、`bun run --cwd apps/server typecheck`；通过后只提交本任务文件。

## 任务 3：可恢复媒体任务

**文件：** server production/tasks.ts、media/tasks.ts、generation.ts、provider.ts；providers/types.d.ts、index.ts；mediaGeneration/runtime.ts。

- [ ] 采用以下持久任务状态，保留供应商与方案快照，凭据只记录配置 ID：

```ts
export type ProductionTaskStatus = "prepared" | "submitting" | "running" | "downloading" | "completed" | "failed" | "cancelRequested" | "cancelled" | "needsReview";
export type ProductionTask = {
  id: string;
  requestId: string;
  plan: ProductionRef;
  providerId: string;
  providerTaskId?: string;
  status: ProductionTaskStatus;
  outputPaths: string[];
};
```

- [ ] providers/types.d.ts 增加可选任务生命周期：提交返回供应商任务编号；查询返回 running/completed/failed/cancelled 与结果描述；取消按供应商实际能力可选。既有一次性生成接口保留，未实现恢复能力的供应商明确声明不能恢复，不伪造编号。
- [ ] 提交前保存 prepared→submitting；拿到编号后立即持久化为 running。请求 ID 与方案版本去重；相同请求回读原任务。网络失败但提交可能成功时进入 needsReview，只有查到确切结果才能推进。
- [ ] 查询完成后进入 downloading，下载失败只重试下载；实际文件保存成功才 completed。输出采用新路径，禁止覆盖旧候选；返回供应商节点对应输出，不取任意第一项。
- [ ] 为持久任务解除页面断连即停止的行为，保留旧临时任务语义以免无关回归；服务重启时读取任务索引，恢复有编号任务查询，不重新调用 submit。
- [ ] 明确取消结果：请求已发出为 cancelRequested，供应商确认后才 cancelled；不支持远端取消时仅停止本地等待，并告知远端仍可能产生费用。
- [ ] 对真实已授权的小任务进行断开／重连和服务重启操作，记录同一 providerTaskId；未获付费授权时只完成代码与类型检查，保持恢复行为验证未完成。
- [ ] 执行 server 与 providers 的已有 typecheck，提交本任务改动。

## 任务 4：公开操作与媒体证据通路（G1）

**文件：** server/routes/production/get.ts、execute.ts，utils/mcp/tools.ts、resources.ts，mcp/skills/toonflow/SKILL.md；复用 frameAnalysisNode、useAudioTranscription 与 FFmpeg 能力。

- [ ] GET 读取与 POST 执行分别放单路由文件，复用 validateFields、u.production、success/error；具体解析结果显式读取，冲突返回 HTTP 409。运行 `bun run --cwd apps/server routes` 生成注册文件。
- [ ] 在 MCP 公布 productionRead、productionExecute，命令由 task 2 共用 schema 导出，使用既有 target.directory／connectionId／canvasId 校验。工具中只做适配，不重复实现状态更新。
- [ ] 提供任务查询与有界等待操作，等待超时返回当前状态及 taskId，不重新生成。外部 Codex 直接调用；不强制使用 runAgent。
- [ ] 媒体资源返回经过工作区授权的路径、MIME、时长等元信息；图片和音频按客户端支持的 MCP 内容类型返回，视频可通过受控资源加实际截帧／音频证据读取。大文件不塞入 20 MiB Base64 文件工具；分段音频证据需完整覆盖对白区间。
- [ ] 复用已有 frameAnalysisNode 的抽帧和模型分析、audioNode 的音频转写及 FFprobe；缺少公开函数时为同一实现注册工具，返回对应来源路径、时间范围和分析结果，不复制算法到 MCP 层。
- [ ] 在实际 Codex 会话中读取一张真实图片和一段真实音频，确认返回的可视／可听或分析证据可消费；验证越界路径被拒绝、项目切换旧 target 被拒绝。无法消费时修复传输，不以资源 URL 代替验收。
- [ ] 执行 server typecheck、MCP typecheck 与 server build；提交本任务文件及生成的路由文件。

## 任务 5：立意节点及工作台（G2 前半）

**文件：** 新节点 storyCreationNode/src/index.vue、ideaWorkspace.vue 及必要包配置；共用 production 操作；workflow/references/longDrama.md。

- [ ] 按现有 textNode 的节点注册和包构建模式建立故事成果节点；通过公开工具发现其能力。节点只保存对象引用及显示信息，内容从共用操作读取。
- [ ] 工作台建立“题材创意／立意方向”两步，简报保存和自写方向入口按 Nuomi 当前交互实现。
- [ ] 生成三个候选，正文为 oneLine、protagonistConflict、highlights、finalChoice；生成请求固定简报修订。前端选择候选只改视图，不调用 adopt。
- [ ] 编辑与仅重做当前方向创建新修订，其他候选不变；未保存离开提供保存、放弃、继续编辑，保存失败保留草稿。
- [ ] 注册同等业务工具：保存简报、读取候选、提交候选、修改方向、采用方向。LLM 内容由 Agent/Skill 产生，业务操作只校验保存，不另建第二套隐式 Agent。
- [ ] Codex 通过公开操作完成简报→三候选→修改一项→采用，前端刷新能看到一致结果；用户对话确认由 Codex 记录版本依据，不再要求点击。
- [ ] 执行新增节点已有约定的 typecheck/build 和 nodeShared typecheck，提交。

## 任务 6：长篇规划、剧本与跨集资产

**文件：** production/schemas.ts、operations.ts；workflow/SKILL.md、references/longDrama.md；复用 textNode、imageGenerationNode、imageNode、assetOperator。

- [ ] 定义作品设定、可选篇章、分集提纲及 E1 剧本的结构：稳定角色 ID、场景 ID、镜头 ID、对白 ID、说话人 ID、原文和采用修订；对白原文不能从 H3 回译覆盖。
- [ ] Skill 从采用立意生成全剧与首批分集规划，经确认再生成 E1 剧本；无授权不继续生成后续集数。
- [ ] 使用已有文档／文本节点展示可编辑成果，提供保存修订、读取和采用工具；不新建与已有编辑器重复的通用文本界面。
- [ ] 用已有图片生成与资产工具建立共用角色／场景，记录风格和采用版本；E1 引用作品资产，不复制成独立角色身份。
- [ ] 修改 workflow 的 Seedance 入口，将明确选定 H3 的制作转到 h3Planning 规则；Seedance 用户仍沿原路径，不静默替换模型。
- [ ] 手动验证修改角色候选不会替换 E1 已用版本，采用新版本后只标记相关镜头受影响；执行受影响包的 typecheck，提交。

## 任务 7：RunningHub 音色设计与独立配音

**文件：** providers/src/media/runninghub.ts、providers/types.d.ts；production/schemas.ts、operations.ts；audioGenerationNode/src/index.vue、audioNode/src/index.vue。

- [ ] 按任务 1 核验的工作流参数加入音色设计与 IndexTTS2 配音模型配置，API Key 沿用供应商配置。凭据和带签名 URL 不写追溯文件。
- [ ] 音色设计由角色身份音色与单句表演分别输入；上传参考录音创建候选，采用固定 voiceRevision。生成成功不能自动宣称用户已试听。
- [ ] IndexTTS2 适配按实际图选择一条目标输出依赖，核对节点 ID 与音频格式。FLAC 字节不能仅改名为 WAV；需要转换时复用现有 FFmpeg。
- [ ] 配音请求绑定 dialogueId、原文修订、角色 ID、voiceRevision 和表演要求；完成后读取实际音频时长，逐句重做仅新增该句候选。
- [ ] 复用音频节点播放器和公开操作完成试听、采用；界面显示角色、试听结果、状态，技术参数只写追溯文件。
- [ ] 获批后生成一个角色音色及一段真实对白，Codex 读取声音证据与转写对照；确认采用后再批量生成 E1。记录失败项，不重做已成功的句子。
- [ ] 执行 providers、server、音频节点 typecheck 与相关构建，提交。

## 任务 8：H3 规划器与简洁工作台

**文件：** nodeShared/production/h3Plan.ts、schemas.ts；h3PlannerNode/src/index.vue、plannerWorkspace.vue；workflow/references/h3Planning.md。

- [ ] 定义规划输入与执行段，导出并复用这些类型：

```ts
export type H3Mode = "t2va" | "i2va" | "fl2va" | "l2va" | "ref2va";
export type H3Segment = {
  id: string;
  mode: H3Mode;
  sourceShots: ProductionRef[];
  dialogue: ProductionRef[];
  references: { role: "firstFrame" | "lastFrame" | "subject" | "video" | "voice"; asset: ProductionRef }[];
  requestedSeconds: number;
  effectiveFrames: number;
  fps: number;
  prompt: string;
};
```

- [ ] Skill 依据已确认镜头和实际对白时间生成模式建议与中文方案；首尾帧模式要求对应图片，全参考必须声明素材用途。自动拆段不删改对白，不改变事件顺序，不强迫所有接点连续。
- [ ] 按官方基础三字段／全参考六字段格式输出，检查参考标签均有绑定、说话人映射一致、切镜时间递增且落在片段范围内。首尾帧对齐时间使用实际有效时长。
- [ ] 依据任务 1 核验的帧格选择合法帧数，先保证输入有效再计算 `effectiveFrames / fps <= 15`；批次单次提交的全部段同时计总时长。不能为了合法帧格向上取整超过 15 秒。
- [ ] 冲突返回具体问题：缺首帧、尾帧不兼容、对白超长、模型不可用或参考超限。不得截断对白、隐式换模型或以默认样例内容填充。
- [ ] 工作台只显示片段、中文描述、素材、时长和状态，按模式改变素材槽位；不显示原始提示词、模型链、帧数、日志，也不加高级详情入口。
- [ ] 公开 prepare/read/revise 操作与界面共用实现；保存完整执行文件，Codex 可读取实际 prompt 和绑定结果。前段未采用时返回明确依赖，不生成虚构尾帧。
- [ ] 通过公开入口手动提交五种模式的合法与缺素材请求、超过 15 秒请求，确认输出或明确拒绝；不调用付费生成。执行相关 typecheck/build，提交。

## 任务 9：H3 RunningHub 执行（G3）

**文件：** providers/src/media/runninghub.ts、server media/provider.ts、production/tasks.ts；mediaGeneration/runtime.ts；videoGenerationNode/src/index.vue。

- [ ] 按任务 1 的有效模型链完成各模式工作流映射；新版 JSON 作为模板资料，移除示例人物、素材路径和旧时间轴，明确覆写当前任务全部相关字段，不把预览缓存当输入。
- [ ] 使用真实上传回执构建 timeline_data，视频输出定位 SaveVideo 节点，运行报告另存；全局与段级参考顺序一致。
- [ ] 连接任务 3 的生命周期接口，提交前再次核对授权方案版本、实际时长、素材存在性和模式可用性。取消、未知提交和下载失败走已有明确状态。
- [ ] 获批后按模式分别进行最小真实生成，读取实际时长、画面与音频证据；模式在实际验证通过前不能标为可用。若云端需要额外模型／图配置，记录具体修正，不自动切换到其他模式冒充通过。
- [ ] E1 有尾帧依赖的后段从采用视频提取真实边界帧，再生成下一段；无依赖段可在批准批次范围内执行。
- [ ] 断开界面并重连验证状态与任务编号不变；下载失败重试不新增供应商生成编号。执行相关类型检查和构建，提交。

## 任务 10：成片与视听证据（G4 前半）

**文件：** production/schemas.ts、operations.ts；复用 tools/ffmpeg、frameAnalysisNode、audioNode、videoNode；workflow/references/episodeDelivery.md。

- [ ] 定义合成清单，按剧本顺序引用采用视频与对白，记录视频入出点、对白起止、字幕及基础音轨。时长使用 FFprobe 结果，不使用计划时长冒充输出时长。
- [ ] 独立配音路径明确每个片段的环境声来源。若原生混音含对白，先使用实际可用的分离能力获取环境声并核验；缺失时停止并报告，不能把原始人声再次混入或静默丢掉必需环境声。
- [ ] 通过已有 FFmpeg 能力执行顺序剪接、统一输出规格、对白与基础音轨混合、字幕合成；生成新成片路径，不覆盖旧采用结果。字幕内容来自原对白，时间来自实际采用音频。
- [ ] 公开 composeEpisode、readComposition、inspectEpisode 业务操作，Codex 能发起、查询、读取结果及证据；inspectEpisode 是用户可用的产品媒体检查能力，不是测试入口或内置断言脚本。
- [ ] 证据覆盖每个镜头及接点的图像、完整对白区间的音频、逐句转写对照及媒体元信息；报告注明模型分析与直接观察的来源，转写通过不能替代声音质量判断。
- [ ] 复用视频节点播放最终成片，画布显示成果和采用状态；执行受影响 typecheck/build，提交。

## 任务 11：Codex E1 全流程验收（G4）

**文件：** 不新增仓库测试文件。制作成果及 acceptance 记录写入用户选定的真实制作工作区，沿用业务操作。

- [ ] Codex 调用 getAppState，定位实际项目并发现制作操作；读取已批准的创意、集长、画幅、风格、预算及模型配置。缺少创作输入在对话中逐项确认，不生成固定示例作品替代用户项目。
- [ ] 完成三个立意候选、采用、全剧规划、E1 剧本；将用户确认绑定具体修订，验证全程无需界面点击。
- [ ] 完成角色／场景资产及角色音色；取得试听采用依据，生成逐句独立配音，读取实际媒体。
- [ ] 规划 H3 分段、展示中文导演方案和生成清单，经授权后生成；处理至少一次实际失败或中断恢复时只查询原任务，禁止为演示而浪费付费任务。
- [ ] 完成 E1 合成，Codex 读取全片元信息、逐镜头／接点图像、完整对白音频证据和字幕。逐项记录是否满足：真实可播放、对白无漏句截断、无重复人声、字幕对齐、采用版本正确、H3 每次提交不超过 15 秒。
- [ ] 对实际发现的一处问题执行局部修复；若没有问题，使用用户批准的一句台词修改验证返修范围，重新合成并确认无关素材未重做。不得为了验收未经批准改写剧情。
- [ ] 保存验收记录及证据引用。存在未验证项或能力缺口时状态为 needsReview，不标通过；仅技术检查通过不得替代视听检查。
- [ ] 交付 E1 成片与验收报告，明确用户最终采用决定。用户审批之外，不允许依赖其手工搬素材、跑命令或剪接来补链路。

## 任务 12：E2 跨集复用与最终交付（G5）

- [ ] 从已采用的全剧规划生成 E2 剧本，引用 E1 的作品角色与音色版本，新增资产仅限本集需要。
- [ ] 按任务 7–10 的公开能力生成 E2 配音、H3 片段与成片，所有生成仍受已批准范围和单次 15 秒约束。
- [ ] Codex 核对跨集角色外观、音色与剧情延续，并读取真实媒体证据；不只比较 ID 或文件名。
- [ ] 保留 E1 原成片和采用版本，交付 E2 成片及跨集复用记录。分别报告编译、构建、实际生成与视听验收结果。

## 提交、验证与范围控制

每个实现任务只提交自己的文件，提交前运行 `git diff --check`；具体包使用已有 `bun run --cwd <包目录> typecheck`、`build`，不新增 check 脚本。路由变更先执行 routes；只有新改动、失败或尚未解决的问题才扩大验证范围。

工作区 JSON 快照显式原子保存；外部输入使用 Zod；插件内容不进入 nodeScaffold；界面与自有组件按仓库小驼峰及 template→script→style 规范。普通生成方案批准不等于全部模式付费验证已获授权；具体数量与重试范围须清晰。

## 规格覆盖自检

| 规格要求 | 对应任务 |
| --- | --- |
| Agent/Skill 核心、公开操作与同一状态 | 2、4、5、6 |
| 立意 Nuomi 交互、关键阶段确认 | 5、6、11 |
| 长篇层级、角色／音色跨集复用 | 2、6、7、12 |
| H3 五模式、格式转换、15 秒限制 | 1、8、9 |
| 独立配音、字幕、基础音轨与成片 | 7、10 |
| 无技术详情展开、文件追溯 | 2、5、8、10 |
| 恢复、局部返修、重复提交保护 | 2、3、9、11 |
| Codex 实际视听验收 E1 | 4、10、11 |
| 连续两集、E2 复用 | 12 |
| 不新增测试模块、真实验证如实报告 | 所有任务 |

仅按执行记录声明已经完成并验证的范围；未完成任务继续保留。
