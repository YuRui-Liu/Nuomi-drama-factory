# 案例原提示词与视频参考创作实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 逐任务实现此计划，按复选框记录进度；用户选择委派时可使用 subagent-driven-development。

**目标：** 补全三个 GitHub 来源的公开提示词，让用户在视频分段中查看案例、选择借鉴维度、生成适配方案并确认应用。

**架构：** 先修复离线导入和详情展示，再增加独立文本提案任务，最终通过现有画布更新动作写入当前分段。案例依据、适配方案、视频生产三者分离；提案和应用均不提交视频任务。

**技术栈：** Python、Pydantic、FastAPI、现有文本任务运行时；React、TypeScript、TanStack Query；pytest、Vitest。

**批准的规格：** `docs/superpowers/specs/2026-10-04-case-reference-video-design.md`。

## 工作区与交付边界

### 内联执行记录（2026-10-04）

- 执行方式：当前会话内联，保留已有工作区修改。
- 阻塞：固定来源下载在 sandbox 与提升权限后均报 `Could not resolve host: github.com`；原始文件端点也报解析超时。本地目录搜索未找到三个来源快照。未放松 `verify_snapshot`，未猜测上游提示词格式，未改写发布目录。
- 已实现的独立基础：`CasePrompt` 及原文哈希校验、多来源合并去重、旧目录兼容、列表不携带正文、详情原文展示/复制/来源切换、媒体预览和错误降级。
- 已验证：后端案例测试 35 项通过；前端原文面板、案例库、手法库、导演手法测试共 28 项通过；`git diff --check` 通过。新增测试已先在缺失实现时失败，再在实现后通过。
- 全局类型检查未通过：报错位于本次未改动的 `CanvasRunPanel.tsx:13`（数组 `.at` 的目标库版本）和 `use-superchat.ts:672`（`scope_label` 联合类型）。本次案例文件未出现在诊断中。
- 尚未完成：真实来源适配与目录重导入（任务 1–3）；持久化参考提案、任务/API、分段应用及真实验收（任务 4–8）。当前不能宣称“参考创作”已交付，也没有提交视频生成。
- 继续条件：取得三个固定提交的源快照，优先接受现有本地克隆路径。已向用户询问可用目录。

- 项目根：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。当前工作区有其他任务的修改，执行前记录 Git 状态，不回退或打包无关改动。
- 计划文档沿用本会话的规格所在工作区。实际代码实施前检查可复用 worktree；隔离时必须包含已批准规格及所依赖的现有功能，不能无条件从远端默认分支开始。
- 两个顺序交付阶段：A 为原提示词导入、展示和复制；B 为视频分段参考方案。A 独立可用，B 依赖 A 的正文来源契约。
- GitHub 数据按适配器固定提交读取。README 数量仅作调查线索，不作为硬编码测试值；不新增持续更新定时任务。
- 原图、视频生成资产不改动；本计划不重新生成视频或风格预览。

## 文件职责

现有文件：

- `src/novelvideo/technique_library/models.py`：案例及提示词记录契约。
- `src/novelvideo/technique_library/adapters.py`：三个固定仓库格式适配。
- `src/novelvideo/technique_library/ingest.py`：合并、稳定 ID 和原子发布。
- `src/novelvideo/technique_library/catalog.py`：列表摘要投影与完整详情。
- `src/novelvideo/api/routes/technique_library.py`：现有鉴权案例 API。
- `frontend/src/api/techniqueLibrary.ts`：完整详情及列表类型。
- `frontend/src/features/technique-library/CaseBrowser.tsx`：详情组件组合。
- `frontend/src/features/technique-library/presentation.ts`、`TechniqueLibrary.tsx`：目标上下文传递。
- `frontend/src/features/canvas/director/TechniqueCardPicker.tsx`、`DirectorSegmentEditor.tsx`、`VideoDirectorPanel.tsx`：分段入口与草稿更新。
- `frontend/src/features/canvas/domain/canvasNodes.ts`、`src/novelvideo/freezone/video_director/models.py`：引用元数据跨端契约。

新增文件：

- `src/novelvideo/technique_library/prompt_records.py`：正文校验、来源记录和去重辅助函数。
- `src/novelvideo/case_reference/models.py`、`store.py`、`service.py`、`prompts.py`、`__init__.py`：提案模型、持久化、编排和文本模型指令。
- `src/novelvideo/api/routes/case_reference.py`：项目范围提案 API。
- `src/novelvideo/task_backend/runners/case_reference.py`：可取消文本任务，沿用 `director_plan` 文本路由。
- `frontend/src/api/caseReference.ts`：提案 API 类型和请求。
- `frontend/src/features/technique-library/CasePromptPanel.tsx`、`CaseMedia.tsx`、`CaseReferenceReview.tsx`、`useCaseReference.ts`：原文、媒体、审阅和任务状态。
- `frontend/src/features/canvas/director/applyCaseReference.ts`：校验基准并产出更新草稿的纯函数。

## 任务 1：核对固定来源与建立真实格式夹具

- [ ] 读取 `adapters.py` 的 `SOURCES`，定位或获取三个固定提交的只读快照；使用现有 `verify_snapshot` 验证。不得运行上游脚本。
- [ ] 检查 BeatAPI `prompts/*.json`、SkyNotSilent `data/cases.json`、stimQQ `prompts/**/*.md`。分别记录正文、语言、公开/反推标记、媒体地址的真实字段或段落边界，不从字段名猜测。
- [ ] 在 `tests/technique_library/fixtures/prompts/` 添加最小原创正文夹具，保持上游结构；覆盖作者公开、反推、未公开、只有外链、Markdown 多段落及多来源。
- [ ] 在 `tests/technique_library/test_ingest.py` 增加回归：导入后正文必须等于夹具原文，未公开记录不产生正文。先运行 `.venv/bin/python -m pytest tests/technique_library/test_ingest.py -q`，确认新断言失败。

测试核心断言：

```python
assert case.prompts[0].text == "镜头缓慢前推。\n人物停步回望。"
assert case.prompts[0].provenance == "author"
assert unpublished_case.prompts == []
assert reconstructed_case.prompts[0].provenance == "reconstructed"
```

## 任务 2：提示词契约与三个导入器

- [ ] 定义 `CasePrompt`，字段为 `text`、`language`、`provenance`、`repository`、`revision`、`path`、`upstream_id`、`source_url`、`content_sha256`；`Case.prompts` 默认空数组兼容旧目录。语言不明使用 `und`，不猜测翻译文本。
- [ ] 依据任务 1 的真实结构逐个适配。删除“永不保留提示词正文”的旧限制；保留现有快照验证。正文为空、仅 URL 或明确未公开占位时不产生记录；公开状态不明的实际正文可保留，来源标注 unknown。
- [ ] 对原文 UTF-8 内容计算哈希，保留段落和语言，不将原文替换为摘要。摘要仍用于列表，不向原文混入分类信息。
- [ ] 修改 `merge_cases`：按来源与正文哈希合并提示词记录，保留不同版本和不同来源；沿用既有案例 ID 继承规则。禁止同媒体下最后一条提示词覆盖前一条。
- [ ] 增加两来源同媒体、重新导入 ID 稳定、旧目录默认空正文测试；运行 `.venv/bin/python -m pytest tests/technique_library -q`。同步修改旧“禁止正文”测试为“正文只保存在显式来源字段”的新契约。
- [ ] 审查仅上述文件差异，提交 `feat(cases): preserve source prompt records`。

正文身份实现原则：

```python
digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
key = (repository, revision, path, upstream_id, digest)
```

## 任务 3：补全目录与原文详情

- [ ] `query_cases` 的列表投影排除正文，提供 `prompt_count`、`has_public_prompt`；详情返回全部 `prompts`。在 `tests/technique_library/test_api.py` 验证正文不会通过每页列表重复传输，详情保留全部段落和来源。
- [ ] 扩展 `TechniqueCase` 及列表摘要类型，加入媒体、时长、模式、提示词记录。创建 `CasePromptPanel`：多来源切换、来源类别、原文展开和复制成功/失败反馈；纯文本渲染原文，不执行上游 HTML。
- [ ] 创建 `CaseMedia`：受支持图片/视频正常展示；加载失败保留来源链接和错误状态，不把外链网页塞进视频标签。不要静默下载或代理任意外部 URL。
- [ ] 在 `frontend/src/__tests__/features/canvas/technique-cases.test.tsx` 验证公开/反推区别、多来源切换、完整复制、媒体失败、未公开状态及旧目录兼容。
- [ ] 先向临时文件重导入：`.venv/bin/python -m novelvideo.technique_library.cli --source-root /private/tmp/nuomi-case-sources --output /private/tmp/nuomi-case-catalog.json --report /private/tmp/nuomi-case-import-report.json`。来源根必须由任务 1 准备成适配器要求的布局。
- [ ] 比较旧新案例 ID 集合及手法引用，核验每源解析数、正文数量和异常；任何旧案例丢失或正文类别混淆都先修复再发布。通过现有导入器原子发布到 `src/novelvideo/technique_library/data/catalog.json`，遵守 Bash 编辑记录规则。
- [ ] 运行后端案例测试和 `pnpm exec vitest run src/__tests__/features/canvas/technique-cases.test.tsx`，真实页面打开三个来源各一个案例并复制原文校对；提交 `feat(cases): expose original prompts and media`。

## 任务 4：提案持久化与输入输出契约

- [ ] 创建 `case_reference` 包和 `tests/case_reference/test_store.py`、`test_models.py`。定义借鉴维度枚举 `camera | rhythm | performance | ending`，至少一个且去重；提示词及用户补充描述限制长度，不接受空剧情。
- [ ] 输入保存项目、画布、节点、分段 ID、草稿 revision、模型能力快照、完整导演草稿、案例 ID、选中提示词哈希、借鉴维度、用户补充描述及客户端 request ID。服务端从目录读取正文和来源，忽略客户端提供的作者/已分析标记。
- [ ] 输出保存完整建议提示词、每项建议及依据、兼容性、输入指纹、来源正文快照和文本路由快照。状态为 queued/running/succeeded/failed/cancelled，取消后禁止成功结果覆盖状态。
- [ ] `store.py` 使用项目 runtime 下独立 SQLite 文件，唯一键为拥有者和 request ID；相同 ID 不同输入返回冲突。事务比较状态后更新，解决取消与完成竞态。
- [ ] 测试同请求只创建一次、输入变更冲突、重启可读取、取消后迟到结果不生效、跨拥有者读取失败。运行 `.venv/bin/python -m pytest tests/case_reference/test_store.py tests/case_reference/test_models.py -q`；提交 `feat(cases): persist reference proposals`。

## 任务 5：参考方案文本任务与 API

- [ ] 在 `tests/case_reference/test_service.py` 使用伪文本运行时测试：公开正文无媒体可成功；通用摘要无正文被拒绝；用户具体描述可作为独立依据；原提示词中的外部命令仅作为数据。
- [ ] `prompts.py` 指定保留当前角色、地点、剧情结果、模型、时长和首尾帧，只改写勾选的表现维度；生成结构化输出，不允许视频工具调用。来源原文与目标剧情分开标记。
- [ ] `service.py` 验证选择的正文哈希确实属于该案例；解析输出并复用导演兼容性校验。检查结果包含所选维度的依据，缺失或不兼容则返回明确错误；不自行更改图片/模型/时长。
- [ ] 注册 `case_reference` 文本任务，沿用现有 `register_project_task_runner`、`director_plan` 路由及取消监视器，不调用 `DirectorService.resume` 或视频 provider。
- [ ] 增加已批准规格中的创建、查询、取消 API，并接入现有路由加载入口；项目编辑权限用于创建/取消，读取校验拥有者。API 只返回当前项目提案。
- [ ] 在 `tests/case_reference/test_api.py` 与 `tests/test_task_case_reference.py` 测试权限、重复点击、失败重试、取消和文本任务状态；给视频提交 mock 配置 `assert_not_called()`。运行 `.venv/bin/python -m pytest tests/case_reference tests/test_task_case_reference.py -q`；提交 `feat(cases): generate contextual video reference proposals`。

## 任务 6：分段引用信息与安全应用

- [ ] 在前后端 `DirectorSegment` 添加可选 `caseReference`，记录提案/案例 ID、维度、来源依据快照、应用时间、已应用提示词指纹。后端遵循现有 camelCase alias 规则，旧画布不需要迁移。
- [ ] 实现纯函数 `applyCaseReference(draft, proposal, editedPrompt)`：比对基准 revision、目标分段 ID、输入指纹；不符时返回过期错误，不能覆盖。成功时仅替换目标提示词和引用元数据、提升 revision、使旧优化结果失效。
- [ ] 在 `frontend/src/__tests__/features/canvas/case-reference-apply.test.ts` 测试目标精确更新，其余分段深相等；时长/图片/模型/手法不变；过期与目标删除拒绝；编辑建议可应用。
- [ ] 通过当前画布已有更新与撤销动作接入，禁止直接覆盖 canvas JSON。测试撤销和刷新往返，确认引用信息不被序列化丢弃；导演 attempt 快照保存最终提示词和引用来源。
- [ ] 运行上述前端测试及 `.venv/bin/python -m pytest tests/freezone/video_director -q`；提交 `feat(canvas): apply reviewed case references safely`。

## 任务 7：审阅与上下文交互

- [ ] 扩展 `TechniqueContext`，携带目标身份、草稿快照和应用回调；从 `VideoDirectorPanel` 经分段编辑器和手法入口传入。全局入口仅浏览并提示从分段进入。
- [ ] `CaseReferenceReview` 提供四维度勾选、依据选择/补充、生成按钮、任务进度、取消、前后方案对比、建议编辑和确认应用。原文复制不触发提案或视频。
- [ ] `useCaseReference` 使用包含 project/canvas/node/segment/proposal 的 query key；切换目标后旧响应不能改变新目标。关闭弹窗不自动取消任务，重新打开可查询同一提案；显式取消结果永久失效。
- [ ] 在 `frontend/src/__tests__/features/canvas/case-reference-review.test.tsx` 覆盖成功、无依据、无剧情、无权限、目标过期、能力不兼容、取消和 API 错误。应用失败保留建议和原方案，保存失败不可显示已保存。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/technique-cases.test.tsx src/__tests__/features/canvas/case-reference-review.test.tsx src/__tests__/features/canvas/case-reference-apply.test.ts`；提交 `feat(cases): review and apply video reference proposals`。

## 任务 8：真实验收与收尾

- [ ] 运行 `.venv/bin/python -m pytest tests/technique_library tests/case_reference tests/freezone/video_director tests/test_task_case_reference.py -q`；前端运行三个案例测试及现有 `technique-library.test.tsx`、`video-director-techniques.test.tsx`。
- [ ] 执行 `git diff --check`，核对只包含本计划相关文件，记录原文统计及来源提交，不声称所有外链均可播放。
- [ ] 通过浏览器打开每个来源一个案例，验证原文、来源类别、复制和媒体降级。选择真实视频分段，以一个公开提示词生成一次真实文本提案，审阅编辑后应用并刷新确认保存。
- [ ] 修改目标剧情制造过期提案，确认应用被阻止；确认取消和回退不丢当前内容。检查任务记录中没有新视频提交。
- [ ] 保存页面验收截图，汇报导入正文数量、来源分类、文本方案结果、测试输出及实际限制。浏览器受阻时明确区分 API 验证与 UI 验证，不以单元测试冒充真实操作。

## 自检覆盖

公开原文/来源分类/目录稳定性：任务 1–3；媒体与无依据降级：任务 3、5、7；提案路由、权限、幂等、取消：任务 4–5；版本冲突、撤销、保存和 attempt 快照：任务 6；审阅及不自动生成视频：任务 5、7–8。实现期间遇到上游格式差异，以固定快照实证更新夹具和适配器，不编造正文。
