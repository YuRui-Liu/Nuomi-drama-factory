# 剧本创作工作台实现计划

> **面向 AI 代理的工作者：** 用户已授权子代理分工及常规执行决策。使用 subagent-driven-development 逐任务实现，由主代理负责规格审查、代码质量审查及集成验证。

**目标：** 将用户确认的 V5 原型落地为项目级剧本创作工作台，支持持久编辑、真实 AI 创作、候选审阅和版本化制作交接。

**架构：** 创作文档作为项目数据库内的独立领域，修订不可变，当前指针使用乐观并发控制。AI 使用现有文本任务运行时；制作输入通过明确交接接入 EpisodeSourceStore，继续使用现有场次校对和导演规划。

**技术栈：** Python 3.11、FastAPI、Pydantic、现有 SQLite 项目存储、React 19、TypeScript、TanStack Router/Query、Vitest、pytest。

---

## 基线与工作区

- 用户于 2026-09-26 确认 V5。规范：docs/superpowers/specs/2026-09-26-script-creation-workspace-design.md。
- 隔离工作区：/private/tmp/nuomi-script-creation-plan，基线提交 7229a95。
- 原项目工作区存在大量进行中改动。原生 worktree 工具因会话根目录不是 Git 仓库失败，已用 git worktree 创建隔离 checkout。不要复制、提交或覆盖其他任务的改动。
- 用户已确认的原型：原项目 .superpowers/brainstorm/61360-1790428985/content/script-studio-v5.html。仅作视觉及结构参考，禁止搬用固定生成结果作为正式功能。
- 以下路径均相对于隔离工作区。接口接入前检查与原项目最新改动的兼容性，特别是导航、来源版本和文本任务运行时。
- 计划采用八个顺序任务；每个任务完成相关测试再提交。所有任务完成前不宣称整项功能交付。

## 文件职责

新增后端：
- src/novelvideo/script_creation/models.py：文档、修订、运行、提案、证据、资产关联与交接类型。
- src/novelvideo/script_creation/store.py：项目库建表、事务、版本校验与查询。
- src/novelvideo/script_creation/documents.py：文档保存、导入与恢复。
- src/novelvideo/script_creation/proposals.py：范围校验、冲突预检与原子采纳。
- src/novelvideo/script_creation/generation.py、prompts.py：阶段生成和创作模板。
- src/novelvideo/script_creation/consistency.py：版本化证据、关联检查。
- src/novelvideo/script_creation/handoff.py：采用快照、来源写入与派发恢复。
- src/novelvideo/api/routes/script_creation.py：项目授权与薄路由。
- src/novelvideo/task_backend/runners/script_creation.py：任务生命周期适配。
- src/novelvideo/script_creation/__init__.py：模块入口。

修改现有后端：
- src/novelvideo/api/__init__.py：注册创作路由。
- src/novelvideo/task_backend/registry.py：按现有加载机制纳入新 runner。
- src/novelvideo/text_task_runtime/models.py、settings.py：注册创作任务角色及默认路由。
- src/novelvideo/episode_source_store.py：交接幂等审计适配，保留原有事务恢复。
- src/novelvideo/task_backend/runners/screenplay_semantics.py：携带交接来源，禁止旧结果覆盖新版。

新增前端：
- frontend/src/routes/_app/projects.$project/creation.tsx：项目创作路由。
- frontend/src/features/script-creation/types.ts、api.ts：契约与查询。
- 同目录 workspace.tsx、document-tree.tsx、document-editor.tsx：三栏文档工作台。
- 同目录 script-setter.tsx、option-picker.tsx：融合题材、预设、搜索及手动输入。
- 同目录 assistant-panel.tsx、proposal-review.tsx、issue-list.tsx：AI 引用、差异和问题。
- 同目录 revision-history.tsx、asset-link-dialog.tsx、handoff-dialog.tsx：历史、资产和交接。
- 同目录 templates.ts：已确认的文档结构，不保存故事示例为用户数据。
- 同目录 *.test.tsx：行为测试。

修改现有前端：
- frontend/src/components/layout/project-navigation-routes.ts：新增 creation 入口。
- frontend/src/components/layout/project-header-navigation.tsx：必要的导航适配。
- frontend/src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx：返回创作来源。
- 使用现有本地化目录添加标签；由路由插件生成 routeTree，不手工拼接生成文件。

## 共同契约

文档类型：brief、outline、people、scenes、props、episode_synopsis、episode_script。
正文采用 Markdown；人物/场景/道具条目和集/场使用稳定 block_id，定位为 block_id 加段内偏移。改名不改变实体 ID；无法重定位时返回冲突。

```python
from typing import Literal
from pydantic import BaseModel, Field

class RevisionRef(BaseModel):
    document_id: str
    revision_id: str

class TextEdit(BaseModel):
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    before: str
    after: str

class SaveDocumentRequest(BaseModel):
    base_revision_id: str
    markdown: str
    client_mutation_id: str

class GenerationRequest(BaseModel):
    operation: Literal["bootstrap", "continue", "rewrite", "check"]
    context: list[RevisionRef]
    instruction: str
    episode: int | None = None
```

所有请求绑定授权项目。ID 不允许用作任意文件路径；所有文档与引用必须属于当前项目。
保存冲突返回 409 和 current_revision_id；不存在返回 404；非法范围和缺少要求返回 422。
成功响应沿用现有 API 包装。所有改变状态的操作持久保存 client_mutation_id，重试返回第一次结果。
编辑器以 Unicode code point 计算选段，与 Python 一致；测试含 emoji 和中文，不能直接用 JS UTF-16 偏移。

## 任务 1：修订存储与文档 API

执行状态：已完成。提交 148c6b8、478702f、ba057a9；规格与质量审查通过，16 项针对性测试通过。

**文件：** models.py、store.py、documents.py、新路由与 API 注册；tests/script_creation/test_documents.py、tests/test_api_script_creation.py。

- [ ] 建立测试：保存产生新修订；旧基线写入冲突；相同 mutation 重放只产生一条修订；恢复历史生成新版本；跨项目读写拒绝。
- [ ] 运行 `uv run pytest tests/script_creation/test_documents.py tests/test_api_script_creation.py -q`，确认功能缺失导致失败。
- [ ] 在项目 SQLite 增加 creative_documents、creative_revisions、creative_mutations 表，修订表只插入。事务内先检查当前指针，再插入修订和更新指针；唯一键约束 mutation_id。
- [ ] 实现 GET/POST documents、GET revisions、PUT documents/{id}、POST restore，统一前缀 /projects/{project}/script-creation。
- [ ] 导入已有 EpisodeSource 时复制正文到创作首版本并保存来源 revision/hash，不改写原来源，不调用 AI。重复导入相同来源返回已有文档。
- [ ] 运行上述测试，增加真实临时数据库的并发保存测试，断言只有一个竞争写入成功。
- [ ] 仅提交本任务文件：`git commit -m "feat(script-creation): persist revisioned creative documents"`。

核心不变量测试：
```python
assert restored.parent_revision_id == previous_head.revision_id
assert restored.markdown == historical.markdown
assert adopted_snapshot.revision_id == originally_adopted_revision
```

## 任务 2：V5 工作台与可选可填设定

执行状态：已完成。提交 5d2a02b、049ad06、36cb668；规格与质量审查通过，70 项定向前端测试、类型检查和构建通过。实际浏览器验证可选可填设定持久化、中文及 emoji 编辑保存刷新、人物弧光锚点与集场目录。

**文件：** creation.tsx、workspace/tree/editor/setter/picker/templates/types/api；导航两文件；对应 *.test.tsx。

- [ ] 用 MSW 编写行为测试：时代背景搜索无结果后手动添加；选项移除；确认重开保留；单集隐藏多集目录；编辑失败文本保留且交接禁用。
- [ ] 运行 `pnpm --dir frontend test src/features/script-creation`，确认失败。
- [ ] 实现三栏布局，复用现有 theme token、按钮和对话框。按 V5 布局实现预设侧栏、双题材、六类设置；预设不标称真实热榜。
- [ ] 文档目录实现大纲章节、人物/弧光、场景、道具、集/场定位。弧光在人物记忆点后，允许平稳和负向弧光。世界设定位于大纲，不重复维护一份人物世界正文。
- [ ] 编辑器使用 Markdown 预览与块内文本编辑，保留稳定 block_id。800ms 防抖保存，文档切换前保留本地未保存缓冲；新请求序列号防止旧响应覆盖新编辑。
- [ ] 展示保存中、已保存、失败重试、409 对照处理；保存未完成时禁止交接和使用未保存文字发起生成。
- [ ] 测试通过并执行 `pnpm --dir frontend exec tsc -b`，浏览器对照 V5 验证深色配色和目录滚动。
- [ ] 提交本任务文件，提交说明 `feat(script-creation): add document-first workspace and flexible settings`。

## 任务 3：真实创作任务与逐集续写

执行状态：实现与两阶段审查完成（def126a、840a5bb、4e3d50d）。后端 45 项、前端 30 项通过；真实六阶段已完成并人工读样，未默认生成后续集；结构读样发现的问题已补提示词，补充复验进行中。证据位于 /private/tmp/nuomi-generation-corrected。

**文件：** generation.py、prompts.py、runner、runtime 模型/设置、registry；tests/script_creation/test_generation.py、tests/test_task_script_creation_runner.py。

- [ ] 假运行时测试阶段输出、暂停/失败保留、重试跳过已完成步骤、引用变化中止、已有正文不被续写覆盖。
- [ ] 运行 `uv run pytest tests/script_creation/test_generation.py tests/test_task_script_creation_runner.py -q` 验证先失败。
- [ ] bootstrap 阶段依次生成大纲与梗概、人物、场景、道具、首集。使用 run_structured 和固定路由快照，输出校验失败记录原因，不落入成功状态。
- [ ] 每阶段持久化 context revision、task_id、状态和产出。提交前再次验证依赖指针；不匹配返回 needs_rebase，不覆盖用户修改。
- [ ] continue 要求已保存样稿、当前大纲、目标集梗概及前文事实，存在该集正文则只创建候选。single 不创建分集梗概。
- [ ] runner 按现有取消监听与任务进度模式注册；前端显示可读阶段、暂停和失败重试。不得用 setTimeout 模拟模型完成。
- [ ] 针对未配置模型路由显示可操作错误。生成候选中分开正文事实与设计建议，未写的集数只标计划。
- [ ] 运行测试并提交 `feat(script-creation): generate resumable story drafts through text runtime`。

## 任务 4：候选审阅与版本历史

执行状态：已完成至 8f5badc，规格与质量审查均通过。后端 62 项、前端 41 项及类型检查通过；实际浏览器验证真实选段改稿、切换面板恢复、精确单处采纳、历史恢复新修订。候选采纳只更新当前草稿，制作采用指针仅由任务 7 交接更新。跨块或多段场次继续调整保持原始授权范围，不因整文补丁存储形式扩围。

**文件：** proposals.py、proposal-review.tsx、revision-history.tsx；tests/script_creation/test_proposals.py；前端对应测试。

- [ ] 测试重复文字精确定位、emoji 偏移、基线变化、重叠候选、批量冲突全部回滚、重复采纳幂等。
- [ ] 运行 `uv run pytest tests/script_creation/test_proposals.py -q`，确认失败。
- [ ] 提案记录 base revision、block_id、原文范围、before/after、理由和依赖。采纳事务先验证整个集合再应用，按偏移从后向前替换；跨文档禁止隐式批量采纳。
- [ ] 范围或版本失效返回 409，前端保留提案并提供重新生成。继续调整创建新候选，不采纳旧候选。
- [ ] 接入对白自然、潜台词、冲突、压缩、自定义操作；右侧显示当前引用。手动恢复历史生成新修订并保留制作采用指针。
- [ ] 运行服务和前端测试，提交 `feat(script-creation): review changes with conflict-safe adoption`。

原子性断言：
```python
assert head_after_failed_batch == head_before_batch
assert all(item.status == "pending" for item in rejected_batch)
```

## 任务 5：关联检查、证据与有意安排

执行状态：进行中，由独立子代理负责真实检查任务、证据校验、目标选择与关联候选 UI。

**文件：** consistency.py、issue-list.tsx；tests/script_creation/test_consistency.py；前端对应测试。

- [ ] 测试人物弧光变化影响后集、场景布局变化影响出入路径、道具持有变化影响对白；未选文档不生成提案。
- [ ] 运行 `uv run pytest tests/script_creation/test_consistency.py -q` 验证先失败。
- [ ] 问题保存 evidence revision、block、quote、理由和目标引用。后端校验 quote 属于对应修订，拒绝无证据的精确定位。
- [ ] 用户选择 targets 后才生成关联候选；采纳前标“若采纳将影响”，采纳后重查实际版本。
- [ ] “有意安排”必须保存原因和证据版本；相关正文变化将问题标 stale，不能永久静音。
- [ ] UI 按可定位事实冲突与创作建议区分，不将审美建议变成硬门禁；显示检查失败和引用过期。
- [ ] 服务与 UI 测试通过，提交 `feat(script-creation): surface evidence-bound continuity suggestions`。

## 任务 6：人物、场景和道具资产关联

**文件：** models/store、asset-link-dialog.tsx、新路由关联接口；tests/script_creation/test_asset_links.py。

- [ ] 写测试：同名不自动关联、外项目资产拒绝、改名关联保留、改动机不产生媒体任务、删除资产后可见缺失状态。
- [ ] 运行 `uv run pytest tests/script_creation/test_asset_links.py -q`，确认失败。
- [ ] 存储 entity_id、asset_type、asset_id、selected_revision。通过现有资产查询让用户选择，不能按名称猜测。
- [ ] 首次制作可创建资产文字档案，但调用生图/音色等仍使用现有显式入口。关联动作只记录关系，不触发生成。
- [ ] 设计条目首次出场和关键场次区分 planned 与 written 引用；道具持有与场景关键物件引用同一实体 ID。
- [ ] 运行测试并提交 `feat(script-creation): link narrative designs to production assets`。

## 任务 7：采用快照与幂等制作交接

**文件：** handoff.py、episode_source_store.py、screenplay runner、handoff-dialog.tsx、单集 script 路由；tests/script_creation/test_handoff.py、tests/test_task_screenplay_semantics_runner.py。

- [ ] 测试快照不可变、重复点击、来源版本冲突、派发失败重试、保存失败禁交接、旧任务完成不成为新版当前结果。
- [ ] 运行 `uv run pytest tests/script_creation/test_handoff.py tests/test_task_screenplay_semantics_runner.py -q` 验证先失败。
- [ ] 冻结正文、相关设计修订、资产关联、检查确认及用户更新范围。快照唯一键绑定 project/episode/revision/ref hash/action。
- [ ] 交接状态：prepared → source_written → dispatched → completed；另含 failed 与 needs_rebase。记录每步结果，重试从最近已完成步骤继续。
- [ ] 通过 EpisodeSourceStore.upsert_sources 的 expected_revision 更新来源；在来源事务内记录 handoff_id，解决“来源写成功、交接状态写失败”的重放窗口，不能先写文件再猜测成功。
- [ ] 采用事务后以 outbox 派发既有场次校对，任务 envelope 带 snapshot_id/source_revision。旧任务可以保留归档结果，但激活必须校验当前来源版本。
- [ ] 新旧差异展示确定变化与推测影响；无法匹配的场次要求重新校对，未更新受影响媒体标 stale。仅更新文字不调用媒体生成。
- [ ] 测试覆盖故障注入在来源提交后、派发前、派发后；重试不创建重复来源或任务。
- [ ] 测试通过后提交 `feat(script-creation): hand off immutable screenplay snapshots`。

## 任务 8：集成验收与交付

**文件：** tests/acceptance/test_script_creation_workflow.py；前端工作台集成测试；docs/cookbook/creation/ 下新增创作使用说明。

- [ ] 临时项目跑三集路径：自定义设定 → 首稿 → 人物弧光修改 → 关联范围 → 采纳 → 下一集 → 场景道具关联 → 制作交接。
- [ ] 临时项目跑 single 和已有剧本接入；确认不会默认创作整部，不会静默覆盖现有制作来源。
- [ ] 使用确定性文本运行时验证完整流程；另在已配置路由下做一次人工阅读的真实生成验收，保留上下文和产出证据，不用 AI 自评分替代。
- [ ] 执行 `uv run pytest tests/script_creation tests/test_api_script_creation.py tests/test_task_script_creation_runner.py tests/acceptance/test_script_creation_workflow.py -q`。
- [ ] 执行 `pnpm --dir frontend test src/features/script-creation` 与 `pnpm --dir frontend build:ce`。
- [ ] CUA 检查实际 UI：目录、设定搜索/手填、选段候选、保存错误、历史、交接差异；保存关键截图。额外检查中文输入法期间不抢焦点，切文档不丢未保存缓冲。
- [ ] 查看限定文件 diff，确认无固定样例输出、无自动付费任务、无跨项目读写；提交验收与说明。

## 覆盖与执行方式

规格 1–5 → 任务 1–3；规格 6 → 任务 2、4；规格 7 → 任务 5；
规格 8/8.1 → 任务 2、6；规格 9 → 任务 7；规格 10–11 → 全部契约与任务 8；
V5 大纲/小传/弧光/分集剧本 → 任务 2、3、5。

当前会话使用子代理分工执行，检查点放在“可编辑持久文档”“真实生成与审阅”“制作交接与整体验收”，由主代理完成常规审查，无需逐项向用户确认。
计划文件本身不是实现结果。开发前读取 executing-plans 和 test-driven-development；实现完成后读取 verification-before-completion。
