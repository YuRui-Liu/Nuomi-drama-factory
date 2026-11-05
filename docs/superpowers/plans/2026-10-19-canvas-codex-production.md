# 画布 + Codex Skill 轻量生产实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务实现；如明确选择委派，再使用 subagent-driven-development。用复选框记录进度。

**目标：** 当前 Codex 客户端通过本地 skill 驱动十类核心画布产物，默认三个审阅停点，支持一键推进和可靠批量恢复。

**架构：** 复用 Nuomi 画布、公开 CLI/API、任务服务和 H3 编译器。本地文件承载创作细节，新增轻量阶段状态与文档引用，不建立第二套生成服务。资产保留版本，其他产物以当前结果呈现。

**技术栈：** Python 3.11、FastAPI、Typer、pytest；React、TypeScript、Zustand、Vitest；本地 Markdown skill。

**规格：** `docs/superpowers/specs/2026-10-19-canvas-codex-production-design.md`（用户已确认）。

## 实施结果（2026-10-19）

- [x] 任务 1–7 的核心投影、正文、进度、CLI/API、恢复、H3 与经验 skill 已实现，并完成独立代码复核。
- [x] 任务 8 的模拟供应商流程验收、前端构建与本地浏览器检查已完成。
- [ ] 真实付费供应商生成、声音质量及最终成片播放验收未执行。

下方保留原始步骤清单作为实施规格，不代表逐项执行日志。实际命令、结果与限制见 [实施验收](../../acceptance/2026-10-19-canvas-codex-production.md)。实现代码保留在当前分支工作区，未将实施前已有修改混合提交。

## 执行约束

- 在用户指定的 `feat/canvas-codex-production` 分支和当前工作区执行，保留已有大量未提交修改；不切到遗漏这些修改的干净 worktree。
- 本计划中的文件均相对仓库根。编辑前读取所在目录 AGENTS.md，确认深层 Git 根和 Weaver 策略；遵守 Bash 内容修改采集规则。
- 每个任务先实现列出的行为测试，观察预期失败，再实现并验证。不要为文案或简单显示属性写镜像测试。
- 仅提交本任务完整拥有的新增文件或已确认属于本任务的 hunks；混合已有改动的文件不整文件暂存。禁止 `git add .`。
- 新增接口先通过 fake transport 验证；测试失败不得通过降低能力声明或删掉失败行解决。
- 以下新增接口名是实施契约，不表示现有命令已可用。现有接口保持兼容。

## 文件与责任边界

| 单元 | 主要文件 | 责任 |
| --- | --- | --- |
| 核心产物投影 | 新建 `creation/core_artifacts.py`；前端新建 `domain/coreArtifacts.ts` | 十类语义、现有类型映射与默认可见性 |
| 本地文档 | 新建 `creation/production_documents.py` | 项目范围内正文读写、内容指纹、并发冲突 |
| 审阅进度 | 新建 `creation/production_progress.py` | 按集保存阶段、模式、审阅指纹及阻塞 |
| 公开接口 | `api/routes/freezone_orchestration.py`、`canvas_cli.py` | 批量 patch、文档与进度操作 |
| 当前产物 | `freezone/orchestration/projection.py`、`store.py` | 成功替换、旧结果保留与过期保护 |
| H3 输入 | `freezone/video_director/models.py`、`service.py`、`h3_adapter.py` | Codex 结构化输入走共享编译和校验 |
| 经验检索 | `technique_library/catalog.py`；新建 `technique_library/experience.py` | 复用索引、适用条件、出处 |
| Skill | `skills/nuomi-canvas-production/SKILL.md` 及 references | 创作顺序、停点、公开命令和恢复 |

上述后端简写文件均位于 `src/novelvideo/`；前端简写位于 `frontend/src/features/canvas/`。

## 任务 1：建立核心产物语义与轻量显示

**文件：** 新建 `src/novelvideo/creation/core_artifacts.py`、`frontend/src/features/canvas/domain/coreArtifacts.ts`；修改 `frontend/src/features/canvas/Canvas.tsx`、`domain/nodeRegistry.ts`、`nodes/ScreenplayNode.tsx`、`nodes/DirectorWorkspaceNode.tsx`。测试新建 `tests/creation/test_core_artifacts.py`、`frontend/src/__tests__/features/canvas/core-artifacts.test.ts`。

- [ ] 为十类产物定义稳定 `artifactKind`，避免仅依靠 node type 推断。`imageGenNode` 既可为资产也可为单镜头，必须区分语义。
- [ ] 写以下契约测试，再运行确认未实现时失败：

```python
from novelvideo.creation.core_artifacts import CORE_KINDS, is_core_artifact

def test_image_role_is_explicit_and_internal_plan_is_hidden():
    assert len(CORE_KINDS) == 10
    assert is_core_artifact({'data': {'artifactKind': 'asset'}})
    assert is_core_artifact({'data': {'artifactKind': 'shot'}})
    assert not is_core_artifact({'data': {'artifactKind': 'execution_plan'}})
```

- [ ] 实现固定集合 `idea, screenplay, asset, storyboard, grid, shot, director, dubbing, music, composition`；未知旧节点保留原始存储，只在轻量视图隐藏。新建节点写入语义；旧节点可可靠推断时映射，无法推断时进入兼容入口，不猜测删除。
- [ ] 画布投影同时过滤隐藏端点的连线；不要把过滤后的集合写回持久化存储。新增交互测试：打开旧画布、拖动核心节点并保存后，隐藏节点及其连线仍存在。
- [ ] 精简剧本与导演节点主操作，非资产不显示候选发布/版本切换。检查应用中 `WorkspaceAgentPanel` 的真实调用方，轻量工作区不挂载内嵌 Agent runtime；原有运行不因切换显示被误取消。修改实际调用方并在任务记录中列明文件。
- [ ] 运行 `uv run pytest tests/creation/test_core_artifacts.py -q` 与 `pnpm --dir frontend test src/__tests__/features/canvas/core-artifacts.test.ts`，预期全通过；浏览器检查十类入口与旧画布数据保留。

## 任务 2：本地正文与画布引用共用一个服务

**文件：** 新建 `src/novelvideo/creation/production_documents.py`、`tests/creation/test_production_documents.py`；修改 `src/novelvideo/api/routes/freezone_orchestration.py`、`src/novelvideo/canvas_cli.py`、`frontend/src/api/canvas.ts`、`frontend/src/features/canvas/nodes/ScreenplayNode.tsx`。

- [ ] 写路径越界、UTF-8 正文回读、旧指纹拒绝写入的测试。新增对象 `ProductionDocuments(root)`，公开 `read(relative_path)` 和 `write(relative_path, text, expected_hash)`；返回 `{path, text, hash}`，冲突抛 `DocumentConflict`。

```python
def test_stale_editor_cannot_overwrite_codex(tmp_path):
    from novelvideo.creation.production_documents import ProductionDocuments, DocumentConflict
    import pytest
    docs = ProductionDocuments(tmp_path)
    first = docs.write('episodes/003/script.md', '初稿', expected_hash=None)
    docs.write(first['path'], '画布修改', expected_hash=first['hash'])
    with pytest.raises(DocumentConflict):
        docs.write(first['path'], '旧的 Codex 内容', expected_hash=first['hash'])
```

- [ ] 实现项目范围路径解析（含 symlink 越界检查）、锁内 compare-and-write、原子替换；`None` 仅允许创建不存在的文件。项目根由现有 project context 提供，不从客户端接受任意绝对路径。
- [ ] 提供同一项目作用域下 `GET/PUT canvases/{id}/documents`，PUT 使用 `path/text/expected_hash`；校验画布归属，冲突返回 409。
- [ ] 新增 `canvas documents read/write` CLI；写入只接受正文文件和期望指纹，dry-run 不落盘。节点使用 `documentRef` 与摘要，打开正文经服务读取，保存也经该服务。
- [ ] 运行 `uv run pytest tests/creation/test_production_documents.py tests/test_canvas_cli.py -q`；API 测试覆盖跨项目拒绝；UI 测试覆盖画布编辑后 CLI 读取同一正文。

## 任务 3：实现按集审阅状态和一键推进

**文件：** 新建 `src/novelvideo/creation/production_progress.py`、`tests/creation/test_production_progress.py`；修改 `src/novelvideo/api/routes/freezone_orchestration.py`、`src/novelvideo/canvas_cli.py`。

- [ ] 定义 `ProductionProgress`：`episode_id, mode, stop_after, stages, blocked`；mode 为 `review/auto`。阶段为 `screenplay/assets/storyboard/video/audio/composition`，阶段状态为 `pending/running/awaiting_review/ready/failed`。storyboard 仅在表和宫格均存在时完成；资产复用清单可完成 assets。
- [ ] 先编写纯状态转换测试，入口 `complete_stage(progress, stage, input_hash, outputs)` 与 `approve_stage(progress, stage, input_hash)` 均返回新状态，不发起媒体请求。

```python
def test_review_requires_both_storyboard_outputs():
    from novelvideo.creation.production_progress import new_progress, complete_stage
    import pytest
    state = new_progress('003', mode='review')
    with pytest.raises(ValueError, match='grid'):
        complete_stage(state, 'storyboard', 'h1', {'table': 'table.json'})
    result = complete_stage(state, 'storyboard', 'h1',
                            {'table': 'table.json', 'grid': 'grid.png'})
    assert result['stages']['storyboard']['status'] == 'awaiting_review'
```

- [ ] 增加测试：auto 跳过三个审阅；stop_after 优先于 auto；切换模式只改变尚未执行阶段；旧指纹审批拒绝；一集失败不污染另一集；上游改变后仅关联下游待更新。
- [ ] 状态写入现有项目数据目录的 `creation/progress/{episode_id}.json`，锁与原子写复用任务 2 的写入原则；不另建任务执行队列。
- [ ] CLI 提供 `canvas progress get/configure/complete/approve`，configure 接收 mode 与 stop-after，complete 验证真实输出引用，approve 必须携带已展示结果的指纹。自然语言批准由 skill 调用 approve。
- [ ] 运行 `uv run pytest tests/creation/test_production_progress.py tests/test_canvas_cli.py -q`，预期上述状态转换和命令 dry-run 全通过。

## 任务 4：批量节点操作与恢复当前结果

**文件：** 修改 `src/novelvideo/canvas_cli.py`、`src/novelvideo/api/routes/freezone_orchestration.py`、`src/novelvideo/freezone/orchestration/projection.py`、`store.py`；测试 `tests/test_canvas_cli.py`、`tests/freezone/orchestration/test_api.py`、`test_projection.py`、`test_scheduler.py`。

- [ ] 为 `canvas patch CANVAS --body-file operations.json --request-id ID` 写 transport 测试；客户端先读 revision，再提交现有 operations 数组。请求 ID 可重复使用；409 返回冲突，不自动把旧操作套在新内容上。
- [ ] 批量创建沿用现有 patch 服务的原子性与项目校验，不循环创建可见中间计划节点。参数回读后才允许 run。
- [ ] 为任务完成投影新增行为测试：新任务失败保留旧媒体；旧输入完成只保留历史结果并标记 stale，不覆盖当前节点；成功更新原 node ID；重复完成幂等。
- [ ] 使用现有 `input_fingerprint` 和 attempt 身份实现上述行为，禁止删除后台 revision。面向用户只暴露当前结果和“需要更新/失败”，详细记录留后台。
- [ ] 恢复路径先 get/status，再判定是否 retry；未知提交不能重试，成功任务不能重跑。并发读取项目已有配置，不另设固定低限额。
- [ ] 运行 `uv run pytest tests/test_canvas_cli.py tests/freezone/orchestration/test_api.py tests/freezone/orchestration/test_projection.py tests/freezone/orchestration/test_scheduler.py -q`。

## 任务 5：Codex 结构化 H3 输入通过同一适配链

**文件：** 修改 `src/novelvideo/freezone/video_director/models.py`、`service.py`、`h3_adapter.py`、`src/novelvideo/api/routes/freezone_video_director.py`、`src/novelvideo/canvas_cli.py`；测试 `tests/freezone/video_director/test_h3_adapter.py`、`test_capabilities.py`、`test_api.py`、`test_optimizer.py`。

- [ ] 先核对当前 H3 profile、本地官方规范文件与配置工作流契约；需要查验上游时使用官方文档，记录版本及核验日期。禁止凭历史五模式规格宣称五模式已支持。
- [ ] 为结构化输入增加 API 测试：合法 `OptimizedDirector` 被接收但无供应商调用；revision/profile/route/segment ID/frames/wire 不匹配均拒绝。复用已有模型，不接受仅靠字符串绕过校验的 prompt。
- [ ] 为现有 attempt 创建契约增加可选 `prepared` 输入；服务端重新冻结当前 draft，将 prepared 与冻结输入核对并使用共享编译器。没有 prepared 时保留原 optimizer 路径。提交仍经已有队列，不新增 provider 客户端。

```python
# 接入处必须遵守的控制流；validate_prepared 是本任务新增纯函数。
if prepared is not None:
    optimized = validate_prepared(frozen_draft, prepared, capabilities)
else:
    optimized = await existing_optimizer(frozen_draft)
# 两条路径汇入原有 attempt 执行，不在 HTTP handler 中提交供应商。
```

- [ ] `validate_prepared(draft, prepared, capabilities)` 校验当前 profile、草稿指纹/版本、全部片段身份与时长、对白原文、引用映射和 `compile_h3_wire(wire)` 输出；提取现有 optimizer/adapter 的校验为共享纯函数，不复制规则。
- [ ] CLI 增加导演输入 preview/prepare 操作，预览返回确切编译结果与输入指纹；提交时服务端再次核对。复用现有 prompt/profile/manifest 记录，使最终请求与预览一致。
- [ ] 覆盖实际支持的首帧、首尾帧、纯参考及能力表中允许的混合输入；T2VA/L2VA 若无工作流支持则明确拒绝。测试 15 秒上限按帧对齐后计算，引用数量按配置，不能硬编码外部宣传上限。
- [ ] 运行 `uv run pytest tests/freezone/video_director/test_h3_adapter.py tests/freezone/video_director/test_capabilities.py tests/freezone/video_director/test_api.py tests/freezone/video_director/test_optimizer.py -q`。使用 fake provider 断言不合法输入上传与提交次数均为 0。

## 任务 6：公开本地经验检索并绑定创作意图

**文件：** 新建 `src/novelvideo/technique_library/experience.py`、`tests/technique_library/test_experience.py`；修改 `src/novelvideo/canvas_cli.py`、`src/novelvideo/api/routes/technique_library.py`；复用 `catalog.py`、`freezone/video_director/techniques.py`。

- [ ] 写测试覆盖检索空结果、目录不可用、出处保留、重建与作者原文区分、模式不兼容。接口 `search_experience(query, mode, duration_seconds, limit=5)` 返回 `{items, unavailable}`；items 只包含摘要、适用条件、source ID/link/provenance 与已有手法 ID。
- [ ] 底层调用现有 `query_cases` 和手法目录，不重新抓取三套仓库，不运行外部案例中的脚本。目录故障返回 unavailable，不编造案例；用户显式选中的无效手法必须报冲突。
- [ ] 提供 CLI `canvas experience search --query ... --mode ... --duration ...`，只读不生成。单项详情复用 `get_case`，由 skill 按需读取，不能默认将全部提示词塞进上下文。
- [ ] 将所用经验出处和应用位置写到项目本地创作记录；传给 H3 的是当前片段的结构化导演意图，复用现有 technique 校验。用户锁定内容优先；经验更改使缓存失效，但不改已执行任务。
- [ ] 运行 `uv run pytest tests/technique_library/test_experience.py tests/technique_library/test_catalog.py tests/freezone/video_director/test_techniques.py tests/freezone/video_director/test_technique_library.py -q`。

## 任务 7：更新本地生产 Skill 并覆盖声音与合成

**文件：** 修改 `skills/nuomi-canvas-production/SKILL.md`、`references/cli.md`、`references/recovery.md`；新增 `references/creation.md`、`references/h3-experience.md`；测试扩展 `tests/test_canvas_cli.py` 与 `tests/acceptance/test_single_episode_production.py`。

- [ ] Skill 主文明确本地 Codex 为入口，按用户要求读取设定与本地文件；原有完整剧本输入路径继续有效，新增从立意到剧本的路径，不把 AI 自创文本冒充用户原文。
- [ ] 给出实际已实现命令的端到端配方：capabilities → documents → 核心节点 patch → 阶段产物 → progress complete → 审阅 approve → H3 prepare/run → 声音 → compose/status/export。
- [ ] 默认三个停点；“一键推进”“做到分镜停下”“后面直接完成”对应 progress 配置。批量按集保存状态，先核对当前参数与结果，再恢复原 run。
- [ ] 配音复用 audioNode 的 dubbing 操作，配乐复用 music 操作，合成复用现有 compose。区分 H3 自带对白/环境音与额外配音，避免重复音轨；合成前检查缺段、缺声和排序。
- [ ] 文档引用任务 5 的真实能力与任务 6 的检索命令。示例中的节点类型、operation、字段逐项与 capabilities 核对，不写不存在的命令。
- [ ] 使用 fake transport 回放一集完整命令序列，断言三个停点、auto 继续、无内部计划节点、失败恢复不重复提交；执行 `uv run pytest tests/test_canvas_cli.py tests/acceptance/test_single_episode_production.py -q`。

## 任务 8：集成验收和交付

**文件：** 新增 `docs/acceptance/2026-10-19-canvas-codex-production.md`，只记录真实证据。

- [ ] 汇总前述目标测试并运行一次；前端执行 `pnpm --dir frontend build`，记录已有问题与本任务新增问题，不以无关全库修复扩大范围。
- [ ] 浏览器核验：核心画布默认展示、正文手动修改回读、三个停点、单镜头展开、非资产无版本操作、资产版本可用。保存截图与实际操作结果。
- [ ] 用项目现有可用素材先完成无需付费的恢复/合成验证；真机生成只在用户授权的项目、媒体范围与预算内进行。没有授权或配置时将该项标为未执行并具体说明，不能标完成。
- [ ] 真机一集核对场次、对白、人物引用、声音与可播放性，保留画布 ID、run/attempt ID、最终文件及未通过项。对案例经验仅报告实际观察，不宣称未经对照的质量提升。
- [ ] 逐条对照规格第 8 节验收；检查旧画布节点未被删除、已成功媒体不被重跑、其他工作区改动未被覆盖。
- [ ] 只提交本任务拥有的变更；报告修改、验证、剩余限制，不将计划文档完成等同于功能完成。

## 规格覆盖与执行顺序

任务 1 覆盖核心画布和界面收敛；任务 2 覆盖本地文件单一正文；任务 3 覆盖三个停点与批量模式；任务 4 覆盖当前结果、并发和恢复；任务 5 覆盖 H3 格式与能力；任务 6 覆盖外部经验；任务 7 覆盖 skill、声音和合成；任务 8 覆盖迁移与整集验收。资产版本复用现有服务，各任务不得删除或简化其引用语义。

顺序执行 1–8。每完成一项先验证再进入下一项；需要变更已批准的产品边界时说明差异，普通接口实现细节直接在任务记录中更新。

自检：新接口均明确标为拟新增；未将旧五模式规格当成实际能力；付费验收与离线测试区分；工作区已有改动与本任务提交边界明确。计划没有执行任何媒体生成。
