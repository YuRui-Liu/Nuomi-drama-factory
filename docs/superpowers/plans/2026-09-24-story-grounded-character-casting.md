# 剧情驱动的人物选角实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将剧情依据落实到可审查的选角方案和头像候选，用户确认后才同步激活人物设计与头像。

**架构：** 扩展现有 character visual workspace，保持事实只有一个来源。将选角快照、候选生成、检查和定角分为独立单元，复用图像提供方、任务调度、成本采集和资产版本。新流程先登记候选，定角通过可恢复提交同步更新当前 visual bible 与头像。

**技术栈：** Python、Pydantic、FastAPI、现有任务后端和项目文件存储；React、TypeScript、TanStack Query；pytest、Vitest。

**依据：** `docs/superpowers/specs/2026-09-24-story-grounded-character-casting-design.md`，2026-09-24 用户已批准。

---

## 执行基线与约束

### 执行进度

- 用户于 2026-09-24 明确改为在当前分支 `fix/codex-identity-qc` 实现，不再同步依赖到 worktree；取消此前待确认的 Weaver 批量同步操作。
- [x] 任务 1：数据模型已从隔离分支接回，提交 `8242284`、`16d8350`；22 项测试通过，规格和质量两阶段审查通过。
- [x] 任务 2：档案与三提案证据链；`a4f5e52`、`fd87df9`、`0a9a238`，69 项相关测试通过，规格和质量审查通过。重叠用户工作的 stage/extraction/builders 集成与 fixture 更新保留未提交；事实空缺的方案仍严格校验并保存拒绝原因。
- [x] 任务 3：生成快照；`4a3ef7a`、`058248b`、`628c6fe`，80 项相关测试通过，规格和质量审查通过。同 ID 方案内容变化绑定新版本，未知/创作非人物种不添加人类领口。
- [x] 任务 4：候选存储与生成，`028e6a2`、`c4a6ff3`、`ea6bcff`；72 项相关测试通过，规格与质量审查通过。候选独立存储、领取落盘、图片原子发布和 SHA256 校验；当前头像不变。已有脏文件 character_image.py 的集成保留未提交。
- [x] 任务 5：三维检查，`db4a355`、`379fb81`；97 项相关测试通过，规格和质量审查通过。参考版本固定，检查尝试去重和 CAS，失败保留候选；补齐真实 runner 成功及取消回归。
- [x] 任务 6：API 与任务，`460430e`；369 项相关测试通过，规格和质量审查通过。原文归属保守校验、阶段草案隔离、幂等提交与后台任务领取、实际模型冻结、参考范围和过期提示；采纳接口暂以 503 关闭，待任务 7 接入。characters.py / character_image.py 的重叠集成未提交。
- [x] 任务 7：可恢复定角，规格与质量两轮审查通过，相关后端回归 560 项通过。覆盖真实进程退出恢复、独立 state_dir、通用素材跨 slot 路径及身份名称碰撞、旧上传/恢复/删除/画布入口保护、同锁读取当前图与设定。重叠 characters.py / character_image.py / sqlite_store.py 改动保留未提交。
- [x] 任务 8：角色详情交互，`3c4fece`；36 项前端测试、TypeScript 检查通过，规格及质量复审通过。身份入口传递稳定 ID，同内容保存不再锁住生成；重叠角色路由及路由测试保留未提交。
- [ ] 任务 9：自动化与文档已完成（`ddc44b7`），最新后端 572 项、前端 38 项通过。真实四角色首次选角被事实/方案校验拒绝；后续诊断与提取协议修复为 `5b9cd02`、`ebee162`。桑禾单角色复查仍把台词当行为而被拒绝，停止追加调用；未生成/采用新图，四角色视觉验收保留未完成。

实际 API 聚合入口为 `src/novelvideo/api/__init__.py`。任务 7 需同时防护通用资产 adopt、旧上传/恢复/同步生成入口，并覆盖身份阶段 SQLite portrait 引用。已有用户修改始终保留；新功能修改与已有未提交内容重叠时，不以整文件提交方式误收用户工作。

任务 7 的路径审计：workflow 和 data.db 位于注册表的 `ctx.state_dir`，visual workspace 位于 `ctx.output_dir/state`，二者不可互相推导。现有项目锁为线程重入锁，持锁事务必须同步，不跨 await。现有 identity 更新忽略底层 SQL 写入失败，定角必须以最新行作受检查的字段级 SQL 更新并刷新缓存。恢复覆盖读取与旧写入入口，不仅覆盖 adopt；通用资产接口还必须防护已归属选角的整个 slot，以免采用旧的未标记版本绕过 bible。

任务 6 的接入审计：直接复用 `api.deps.resolve_project_scope` 与 `ctx.effective_role` 返回编辑能力。已有角色若事实为空，优先复用匹配源版本的 extraction artifact；否则只针对角色相关原文片段提取，逐条核验文档、偏移与归属，不将简介或默认 youth 当事实。剧集 evidence 偏移属于单集源文档，不能套用 novel.txt。身份阶段必须独立保存完整草案，不能只保存 revision 而共享基础方案。任务后端在 enqueue 内分配 UUID 并立即提交，持久化请求 token 与实际运行 task_id 的绑定必须在安全边界完成，禁止依赖 enqueue 返回后补写造成竞态。

仓库根：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。下文路径均相对仓库根。计划编写基线提交 `5110d9a`，但主工作区还有人物/音色/身份相关未提交修改，尤其 `character_design_stage.py` 尚未跟踪。仅从 HEAD 创建 worktree 不会带上这些依赖。

- [ ] 执行前读取 git 状态、仓库 AGENTS、Weaver 策略及 using-git-worktrees 技能；记录工作区差异，不 stash、覆盖或提交用户已有修改。
- [ ] 若使用独立 worktree，采用包含当前工作树的隔离快照，并核对已跟踪修改与未跟踪依赖均在；不复制凭据、生成资产或 node_modules。若采用当前工作区执行，每个任务只提交本任务拥有的改动，不能整文件误收其他人的修改。
- [ ] 建立任务前基线：后端 `env PYTHONPATH=src .venv/bin/python -m pytest tests/character_visual tests/production_workflow/test_character_portraits.py tests/test_character_image_runner.py -q`；前端在 `frontend` 执行 `./node_modules/.bin/vitest run src/__tests__/components/assets/character-visual-profile.test.tsx`。记录已有失败，不能将失败冒充本功能回归。

不要新建第二套人脸/身份平台，不迁移无关音色代码。生产图像生成只能在操作真实角色的视觉验收步骤执行，自动测试全部 mock 提供方。

## 文件与职责

| 路径 | 责任 |
| --- | --- |
| `src/novelvideo/character_visual/casting_models.py`（新增） | 档案版本、造型决定、候选、检查报告、定角命令模型 |
| `src/novelvideo/character_visual/models.py` | workspace 的可选选角字段；旧数据默认兼容 |
| `src/novelvideo/character_visual/casting_brief.py`（新增） | 将已有事实构建为可引用的选角档案，不重新抽取全篇 |
| `src/novelvideo/character_visual/casting_proposals.py`（新增） | 提案证据校验与选角指令构建 |
| `src/novelvideo/character_design_stage.py` | 接入选角档案、版本化 checkpoint；保留现有未提交能力 |
| `src/novelvideo/character_visual/casting_compiler.py`（新增） | 确定性生成快照编译 |
| `src/novelvideo/character_visual/store.py` | workspace 原子更新、预期版本检查 |
| `src/novelvideo/character_visual/casting_store.py`（新增） | 不可变候选与检查快照、定角日志；不复制事实库 |
| `src/novelvideo/character_visual/casting_review.py`（新增） | 三维检查、参考版本、失败与不可判断语义 |
| `src/novelvideo/character_visual/casting_adoption.py`（新增） | 定角校验与恢复协议 |
| `src/novelvideo/task_backend/runners/character_image.py` | 复用图像调用、支持只产候选 |
| `src/novelvideo/production_workflow/character_portraits.py` | 从发布流程分离候选登记；受控定角接入 |
| `src/novelvideo/api/routes/character_casting.py`（新增） | 项目授权、选角/候选/检查/定角接口 |
| `frontend/src/types/character-casting.ts`（新增） | 与 API 同构的类型 |
| `frontend/src/lib/queries/character-casting.ts`（新增） | 请求与 query invalidation |
| `frontend/src/components/assets/character-casting-panel.tsx`（新增） | 档案、三提案、候选对照与确认交互 |
| `frontend/src/components/assets/character-visual-profile/character-visual-profile.tsx` | 接入选角面板，不继续扩大人物路由文件 |

任务中的测试文件同时创建。API 在 `src/novelvideo/api/routes/__init__.py` 聚合；新增 `src/novelvideo/task_backend/runners/character_casting.py` 并在同目录 `__init__.py` 导入，使用 `register_project_task_runner` 注册选角提案和检查任务，同时核对 `run_core.py` 的延迟导入路径能加载该模块，不能重复注册。现有 `domain.py` 与 `models.py` 有两套近似类型，本功能统一使用 Pydantic `models.py` 的 workspace，不另加第三套事实模型。

## 契约与状态

标识组合为项目、稳定角色 ID、可空身份阶段 ID。角色名称只作为现有路由参数，由服务解析为稳定 ID。

新增 `CastingDecision`：`decision_id`、`attribute`、`value`、`reason`、`basis: evidence|creative_choice`、`fact_ids: list[str]`。basis=evidence 时至少引用一个存在且适用的事实；creative_choice 不能伪造 fact_ids。外貌推断不写回原文事实。

新增 `CastingRevision`：`revision_id`、`character_id`、`identity_id`、`source_revision`、`style_revision`、`profile_hash`、`decisions`、`proposal_ids`、`selected_proposal_id`。事实正文仍存在 workspace.profile，历史生成快照保存提交时必要约束及出处用于审计。

新增 `CastingSnapshot`：以上版本标识、`proposal_id`、`prompt`、`hard_constraints`、`design_decisions`、`style`、`snapshot_hash`。规范 JSON 排序后算 SHA-256；时间戳不参与内容 hash。

新增 `CastingCandidate`：`candidate_id`、归属、`snapshot`、`task_id`、`asset_path`、`generation_status`、`review_status`、`report`。生成状态 queued/running/succeeded/failed；检查状态 not_started/running/completed/failed。过期是由当前版本比较派生，不与生成状态混用。

新增 `CastingFinding`：`dimension: facts|design|distinctiveness`、`verdict: conforms|deviation|unjudgeable`、`description`、`fact_ids`、`decision_ids`、`reference_candidate_ids`。报告保留 reviewer/model/version，不输出颜值分。

定角请求 `candidate_id`、`expected_revision`、`idempotency_key`、`acknowledged_findings`、`override_reason`。事实结构校验失败、旧版本、资产缺失不可绕过；模型偏差/不可判断/检查失败允许明确确认并记录原因。缺少警示确认时返回可解释校验错误，不能静默通过。

## 任务 1：数据模型与向后兼容

文件：新增 `casting_models.py`；修改 `models.py`；新增 `tests/character_visual/test_casting_models.py`。

- [ ] 编写失败用例：旧 workspace 无选角字段仍可解析；evidence 无引用失败；creative_choice 伪引用失败；检查失败不同于 completed；身份阶段可空。
- [ ] 运行 `env PYTHONPATH=src .venv/bin/python -m pytest tests/character_visual/test_casting_models.py -q`，确认缺少模型导致失败。
- [ ] 实现上述契约，workspace 增加 `casting_revision: CastingRevision | None = None`，模型验证器只做结构校验，跨事实引用验证留给任务 2。

```python
def test_evidence_decision_requires_fact_ids():
    import pytest
    from pydantic import ValidationError
    from novelvideo.character_visual.casting_models import CastingDecision
    with pytest.raises(ValidationError):
        CastingDecision(decision_id="d1", attribute="hair", value="束发",
                        reason="原文明确", basis="evidence", fact_ids=[])
```

- [ ] 同命令验证通过，再运行 `tests/character_visual/test_models.py`。
- [ ] 仅提交本任务文件，消息 `feat(casting): define versioned casting contracts`。

## 任务 2：档案与三提案证据链

文件：新增 `casting_brief.py`、`casting_proposals.py`；修改 `character_design_stage.py`；新增 `tests/character_visual/test_casting_brief.py`、`test_casting_proposals.py`。

- [ ] 测试覆盖明确俊美、老人、非人角色、职业不推出五官、阶段冲突、伪造出处、三套方案仅换衣服、原文过强无法产生差异。
- [ ] 运行这两个测试文件并确认失败。
- [ ] 实现 `build_casting_revision(workspace, identity_id, source_revision, style_revision)`，从可信事实提取硬约束；实现 `validate_casting_decisions(profile, decisions, identity_id) -> list[str]`，核验引用与阶段。缺乏阶段适用依据的矛盾年龄不能自动选择。
- [ ] 提案生成输入档案与项目风格，输出仍是现有三套 proposal，附 decision 引用；checkpoint key 纳入档案内容 hash 和提示词版本；不沿用仅角色事实 hash 的旧缓存。

```python
def test_unknown_fact_reference_is_reported():
    from novelvideo.character_visual.models import CharacterNarrativeProfile
    from novelvideo.character_visual.casting_models import CastingDecision
    from novelvideo.character_visual.casting_proposals import validate_casting_decisions
    profile = CharacterNarrativeProfile(character_id="c1", name="甲")
    decision = CastingDecision(decision_id="d1", attribute="hair", value="短发",
                               reason="引用缺失", basis="evidence", fact_ids=["missing"])
    assert "unknown_fact:missing" in validate_casting_decisions(profile, [decision], None)
```

- [ ] 运行新增测试与 `tests/character_visual/test_proposals.py tests/test_character_build_stages.py`，确认原有三提案与非人约束保留。
- [ ] 提交消息 `feat(casting): ground casting proposals in narrative evidence`。

## 任务 3：不可变编译快照

文件：新增 `casting_compiler.py`、`tests/character_visual/test_casting_compiler.py`。

- [ ] 编写输入排序不改变 hash、风格改变导致 hash 改变、显式美貌保留、肤质不默认磨皮、头像不注入场景服装、动物不注入人类妆容的测试。
- [ ] 运行新增测试确认失败。
- [ ] 实现 `compile_casting_snapshot(revision, proposal, profile, style) -> CastingSnapshot`；硬事实、设计解释、自由选择分节编译，先验证版本与引用，再生成文本。复用现有头像构图契约，编译中不调用模型。

```python
def test_snapshot_hash_is_stable_for_key_order():
    from novelvideo.character_visual.casting_compiler import snapshot_digest
    assert snapshot_digest({"age": "60", "hair": "白发"}) == snapshot_digest(
        {"hair": "白发", "age": "60"})
```

- [ ] 实现 `snapshot_digest(payload)` 使用 `json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 的 UTF-8 字节与 SHA-256；不可使用 Python hash。
- [ ] 运行新增测试与现有 `tests/character_visual/test_compiler.py`，提交 `feat(casting): compile immutable story-grounded portrait snapshots`。

## 任务 4：候选存储与只生成不替换

文件：新增 `casting_store.py`；修改 `store.py`、`task_backend/runners/character_image.py`、`production_workflow/character_portraits.py`；新增 `tests/character_visual/test_casting_store.py`、`tests/test_character_casting_generation.py`。

- [ ] 用临时项目和 mock 提供方测试：生成成功当前头像字节不变；未创建 current 的新角色仍不激活；重启重读保留候选；重复任务只有一个候选；越界路径拒绝；原候选图片不可覆盖。
- [ ] 运行新增测试确认失败。
- [ ] `CastingCandidateStore(project_dir)` 在项目 state 下持久化候选元数据；`create_pending(candidate)` 以 candidate_id 去重，`complete_generation(candidate_id, asset_path)` 只接受项目候选目录中的有效图片。workspace 更新提供 expected_revision 的 CAS 语义。
- [ ] 将 `_generate_character_portrait` 中提供方生成与 `commit_character_portrait_current` 分离；新增选角候选路径只登记 immutable version，不调用当前头像发布。现有旧路径保留兼容，已启用选角的角色入口必须统一进入候选路径，不能被同步头像接口绕过。

```python
def test_candidate_store_survives_restart(tmp_path):
    from novelvideo.character_visual.casting_store import CastingCandidateStore
    store = CastingCandidateStore(tmp_path)
    assert store.list_candidates(character_id="c1", identity_id=None) == []
    assert CastingCandidateStore(tmp_path).list_candidates(
        character_id="c1", identity_id=None) == []
```

以上空库测试之外必须以任务 1 契约构造真实 pending 候选，断言第二次打开仍读出相同 snapshot_hash；仅空库通过不算完成。

- [ ] 运行新增测试及 `tests/test_character_image_runner.py tests/production_workflow/test_character_portraits.py`。
- [ ] 提交 `feat(casting): persist portrait candidates without adopting them`。

## 任务 5：三维检查与失败语义

文件：新增 `casting_review.py`、`tests/character_visual/test_casting_review.py`；复用现有身份 QC 模型调用封装。

- [ ] 测试明确年龄不符、五官被美化、无参考人物、亲缘相似、图片看不见的标记、模型超时、无效 JSON、重复检查。
- [ ] 运行新增测试确认失败。
- [ ] 实现 `build_review_input(candidate, references)` 与 `validate_review_report(report, snapshot)`，仅传递受支持的图片路径和可溯源约束；同项目已确认人物参考固定版本，无参考则 distinctiveness=unjudgeable。
- [ ] 检查完成写 report；超时与解析失败写 failed 及错误摘要，保留图像。重试检查沿用 candidate_id，不触发生成任务；不把模型未知结论变成符合。

```python
def test_failed_review_cannot_have_a_pass_report():
    from novelvideo.character_visual.casting_review import failed_review
    result = failed_review("timeout")
    assert result["review_status"] == "failed"
    assert result["report"] is None
```

- [ ] 核验 provider/model、角色/项目、任务归属传入现有成本上下文；未定价保持未定价，不添加自定义零费用规则。
- [ ] 新增测试与 `tests/character_visual/test_identity_sheet_qc.py` 通过后提交 `feat(casting): review factual fit design fidelity and distinctiveness`。

## 任务 6：服务与 API

文件：新增 `src/novelvideo/api/routes/character_casting.py`、`src/novelvideo/task_backend/runners/character_casting.py`、`tests/test_api_character_casting.py`；修改 `src/novelvideo/api/routes/__init__.py`、`src/novelvideo/task_backend/runners/__init__.py`、`src/novelvideo/task_backend/run_core.py` 的模块加载入口。

路由基址 `/projects/{project}/characters/{name}/casting`：

| 方法与后缀 | 行为 |
| --- | --- |
| GET 基址 | 当前档案、提案、当前定角信息 |
| POST `/recast` | 显式启动新档案/提案任务，202 |
| PATCH 基址 | expected_revision 下修订造型解释与选择，200 |
| GET `/candidates` | 候选、检查、派生过期标记 |
| POST `/candidates` | 固定版本生成一个候选，202，接受幂等键 |
| POST `/candidates/{id}/review` | 单独检查或重试，202 |
| POST `/candidates/{id}/adopt` | 调用任务 7 定角服务，200 |

- [ ] 编写 API 测试：viewer 写请求 403、跨项目/角色候选 404、版本冲突 409、结构校验 422、任务提交重复返回原任务。
- [ ] 运行新增测试确认失败。
- [ ] 路由复用 `_resolve_character_project` 对应的现有项目授权机制；将必须共享的解析函数提取为小型公共依赖，不能从新路由反向导入整个 characters 路由造成循环。
- [ ] 注册选角提案和候选检查任务，持久化 request id 后再提交；任务执行再次核对归属。记录任务提交失败，重试复用 id，前端超时不等于任务未创建。
- [ ] adopt 路由先测试 mock 服务；任务 7 完成后改为真实服务集成测试。此中间提交不对用户宣称功能可用。
- [ ] 运行新增 API 测试及现有人物 API 相关测试，提交 `feat(casting): expose authorized versioned casting workflow`。

## 任务 7：可靠定角与崩溃恢复

文件：新增 `casting_adoption.py`、`tests/character_visual/test_casting_adoption.py`；扩展 `store.py` 和 `production_workflow/character_portraits.py` 的受锁协调接口。

- [ ] 编写故障注入测试：在日志准备后、头像发布后、bible 保存后分别中断；恢复后头像与 bible 同版本。重复幂等键不二次发布，旧版本/缺图/不同归属直接拒绝。
- [ ] 运行新增测试确认失败。
- [ ] 实现 `adopt_candidate(project_dir, candidate_id, expected_revision, idempotency_key, actor, acknowledged_findings, override_reason)`；先校验候选、版本、警示确认、图片与预期 bible，再执行写入。
- [ ] 采用项目级锁与持久化 adoption journal：prepared 保存旧头像/current workflow/workspace 快照及新目标；依次写不可变图片、current、bible；全部成功后 committed。异常回滚旧快照；进程崩溃后首次读取/写入前恢复 prepared 事务，再允许服务 current。明确锁顺序为 project workflow → visual store → candidate store，避免嵌套重复持锁。
- [ ] 现有发布函数内部已获取 workflow 锁，提取受锁 helper 供定角协调调用，不能再次进入非重入锁。journal 写入和状态切换使用原子替换与 fsync，不以 try/except 代替崩溃恢复。
- [ ] 前端确认模型警示时要求原因；结构性硬约束不得 override。当前头像、bible、操作人和采用来源一起进入审计。
- [ ] 运行新增测试、API adopt 真集成和现有 portrait workflow 测试，提交 `feat(casting): adopt portraits and design revisions recoverably`。

## 任务 8：角色详情交互

文件：新增 `frontend/src/types/character-casting.ts`、`frontend/src/lib/queries/character-casting.ts`、`frontend/src/components/assets/character-casting-panel.tsx`、`frontend/src/__tests__/components/assets/character-casting-panel.test.tsx`；修改现有 visual profile 组件。

- [ ] 使用 Testing Library 与现有 MSW 模式测试：三方案的事实/解释/自由选择标签；当前图与候选区别；过期禁用采用；检查失败不显示通过；警示采用要求原因；viewer 不可写；刷新可恢复候选。
- [ ] 在 frontend 运行 `./node_modules/.bin/vitest run src/__tests__/components/assets/character-casting-panel.test.tsx`，确认失败。
- [ ] 类型复刻契约；query key 包含项目、角色、身份阶段。确认定角后失效 casting、人物、visual workspace 和资产历史 queries。生成只更新候选，不乐观替换当前头像。
- [ ] 面板按“依据 → 三提案 → 候选/检查 → 定角”排列；原文定位可展开，版本和失败显示在对应候选旁。默认不发起 recast 或生成；按钮明确区分“生成候选”和“确认定角”。
- [ ] 测试具体交互断言：

```tsx
expect(screen.getByRole('button', { name: '确认定角' })).toBeDisabled();
expect(screen.getByText('基于旧方案生成')).toBeVisible();
```

该断言在 MSW 返回旧版候选、当前 revision 已更新的用例中执行；并单独测试当前版候选可确认。

- [ ] 运行新增测试、现有 visual profile 测试及 `./node_modules/.bin/tsc -b`；提交 `feat(casting): add story-grounded casting review panel`。

## 任务 9：回归、视觉验收与文档

文件：新增 `tests/test_character_casting_flow.py`、`docs/operations/story-grounded-character-casting.md`。

- [ ] mock 端到端测试覆盖新角色从档案到采用、旧角色主动 recast、检查独立重试、生成期间版本变化、服务重启恢复；未操作旧角色字节与引用保持不变。
- [ ] 后端运行 `env PYTHONPATH=src .venv/bin/python -m pytest tests/character_visual tests/production_workflow/test_character_portraits.py tests/test_character_image_runner.py tests/test_character_casting_generation.py tests/test_api_character_casting.py tests/test_character_casting_flow.py -q`；预期 exit 0。
- [ ] 前端运行 `./node_modules/.bin/vitest run src/__tests__/components/assets/character-casting-panel.test.tsx src/__tests__/components/assets/character-visual-profile.test.tsx src/__tests__/routes/characters.ce.test.tsx`、`./node_modules/.bin/tsc -b` 和 `./node_modules/.bin/vite build --mode ce`；预期 exit 0，记录已有 bundle warning。
- [ ] 连接真实项目后端 8780，禁止使用成本演示库 8787。挑选至少四位有原文依据、年龄身份不同的角色，记录原图、原文、选择方案、提供方、快照版本与费用；需要原文明示俊美类别时可补充标记为测试的剧本角色。
- [ ] 首轮每角色只生成一个所选方案候选，不自动采用、不自动扩增重绘。用获准视觉伴侣展示同一角色新旧图与跨角色对照，收集用户具体偏差反馈。自动测试通过不能替代用户视觉验收。
- [ ] 写操作文档说明旧角色保护、费用、过期候选、检查语义、定角恢复和生成入口。记录实际命令、输出、候选 ID 与视觉验收结论，不能写预期成功为已完成。
- [ ] 提交 `test(casting): verify casting lifecycle and document acceptance`；代码审查后按用户选择执行集成，不自动 push 或合并。

## 自检与覆盖映射

规格 1–3 由任务 1–2 覆盖；规格 4–5 由任务 3–4 覆盖；规格 6 由任务 5、7 覆盖；规格 7–8 由任务 4、6–8 覆盖；规格 9 由任务 9 覆盖。主要风险为现有工作区依赖、当前头像自动发布、跨存储定角一致性，分别有基线核对、候选分离和故障注入恢复验证。

实施前不运行付费生成，不改用户现有头像。本计划的完成代表实施步骤已整理，不代表代码实现或视觉验收完成。
