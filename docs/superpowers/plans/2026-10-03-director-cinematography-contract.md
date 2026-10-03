# 通用导演创作契约实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务执行；如用户选择委派，则使用 subagent-driven-development。步骤使用复选框跟踪，不能将计划内的预期结果报告为已经通过。

**目标：** 让焦段、景别、机位、构图、运镜、表演、光影和镜头衔接成为可审查、可修订、被图像与视频共同消费的导演决定。

**架构：** 复用 director_plan 修订与叙事组入口，增加供应商无关的创作契约、语义审查和关键帧投影。H3 编译采用的决定并报告能力冲突，不新增创作权威。兼容旧计划，不自动重生成或切换采用版本。

**技术栈：** Python 3.11、Pydantic、FastAPI、pytest；React、TypeScript、Vitest。

**依据：** `docs/superpowers/specs/2026-10-03-director-cinematography-contract-design.md`（含第 13 节），基线提交 `942fefe`。用户在规格复核后要求“下一步”，本轮交付实现计划。

## 工作区与执行约束

仓库根：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。下列文件路径均相对此根。现有工作区存在其他任务改动，禁止整体暂存、重置或复制它们。实现前按 using-git-worktrees 检查现有隔离；创建新 worktree 前按该技能确认用户偏好，基于包含本规格与计划的提交。文档编写沿用原工作区，不创建代码分支或安装依赖。

所有生产行为遵循 `AGENTS.md` 和 `skills/nuomi-production/SKILL.md`；本计划的离线检查不调用生产模型。新增付费生成不属于离线验收。执行前读实际文件，遇到并发修改保留并适配，不覆盖。

先执行基线：

```bash
uv run pytest tests/director_plan tests/test_director_intent_projection.py tests/test_narrative_group_image_prompt_overrides.py tests/shot_continuity/test_cinematography.py -q
```

记录退出码及失败用例，不能假定当前基线通过；若失败，区分既有失败与本次变化。以下每项均按失败测试 → 最小实现 → 定向回归 → 单独提交执行。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| 新建 `src/novelvideo/director_plan/creative_contract.py` | 创作版本、八项决定、场次与跨镜关系，禁止导入 H3 |
| 修改 `director_plan/models.py`、`store.py`、`editing.py`、`migration.py`（均位于 `src/novelvideo/` 下） | 接入契约、兼容历史、不可变修订和编辑路径 |
| 新建 `src/novelvideo/director_plan/creative_review.py` | 审查模型、来源校验、阻断规则与候选修订 |
| 修改 `src/novelvideo/director_plan/prompts.py`、`planner.py`、`service.py`、`validation.py` | 分阶段生成与独立审查，结构检查保持独立 |
| 新建 `src/novelvideo/director_plan/keyframes.py` | 时间锚点解析及静态字段投影 |
| 修改 `src/novelvideo/narrative_groups/service.py`、`image_prompt.py` | 采用版本与字段保真装配 |
| 新建 `src/novelvideo/director_plan/creative_dependencies.py` | 消费字段指纹与陈旧状态计算 |
| 修改 `src/novelvideo/director_plan/generation.py`、`production_state.py` | 能力拆包、时间映射、失效状态 |
| 修改 `src/novelvideo/media_capabilities/video/h3_rigid_prompt.py`、`h3_prompt_compiler.py` | 通用决定映射及旧类型兼容 |
| 修改 `src/novelvideo/api/routes/director_plans.py`、`agent_teams/director_bridge.py` | API/团队入口共用校验和采用规则 |
| 修改 `frontend/src/lib/queries/director-plans.ts`、`components/episode/director-review/director-review-workbench.tsx`、`revision-comparison.tsx` | 决策、审查、差异及采用状态展示 |

## 任务 1：冻结验收用例与历史兼容边界

文件：新建 `tests/director_plan/fixtures/creative_cases.json`、`tests/director_plan/test_creative_acceptance.py`；复用 `tests/director_plan/test_models.py` 的构造习惯。

- [ ] 定义六组有预期结论的离线用例：三人训练室（托盘守恒、背景可读）；坐姿正反打（焦躁转移、反应与余波）；历史音频只作上下文；门口跨光区与反打；仅改视线的局部修复；缺少道具揭示的补镜候选。
- [ ] 每组保存来源文本、场次事实、正确方案、单项破坏方案、预期问题代码和具体字段路径。正确方案由明确创作决定组成，不能把官方示例中矛盾的托盘持有状态作为真值。
- [ ] 补充静止、二维、无人物、多光源、有意越轴、未知画幅及超过 15 秒/4 镜案例。未知画幅不强制换算毫米与角度，有意越轴记录叙事理由。
- [ ] 在旧数据用例中确认缺项保留缺失，采用版本不变；禁止将默认中景等值认定为已完成设计。
- [ ] 运行 `uv run pytest tests/director_plan/test_models.py tests/director_plan/test_creative_acceptance.py -q`。新能力断言应先失败；数据构造失败不算预期红灯。

用例结果统一使用下列字段，后续审查任务直接消费：

```json
{"case_id":"tray_ownership","variant":"duplicate","expected_code":"prop_state_conflict","evidence_path":"groups.0.shots.0.action","expected_status":"needs_revision"}
```

## 任务 2：建立通用创作契约

文件：`creative_contract.py`、`models.py`、`store.py`、`migration.py`；新建 `tests/director_plan/test_creative_contract.py`。

- [ ] 写失败测试：新版本缺必需决定时失败、旧版本照常读取、旧版不自动升级、来源与不适用原因可追溯，未填写决定不能用默认值通过。
- [ ] 建立 `CreativeContract`、`SceneCreativeFacts`、`GroupCreativeIntent`、`ShotCreativeDecisions`、`ShotHandoff`。契约固定版本为 `1`；历史修订的 `creative_contract` 为 `None`，状态投影为 `legacy_unreviewed`。新版本生成入口显式请求契约，不能靠 prompt_version 猜测。
- [ ] 八项决定沿用现有 ID。光学表达 focal_length_mm + sensor_basis 或 fov_degrees + fov_axis，并含距离、景深、焦点；机位有地标、高度、轴侧；运镜有触发、路径、结束；表演有目标、阻力、策略、可见行为；光源引用场次；衔接包含两镜 ID 与交接事实。每项含目的、来源、适用性与原因。
- [ ] 场次事实保存世界地理；镜头保存屏幕投影。角色表演母本以 ID/版本引用已有资料，并记录缺失，不建设新资产系统。叙事组关联源节拍、信息揭示与覆盖镜头。
- [ ] 将原始模型的 4 镜/15 秒约束迁至按契约版本检查的兼容逻辑：新版本集合非空、时长有限且大于零，旧版保留原限制；所有 draft、edit、store 路径共用规则。
- [ ] 运行 `uv run pytest tests/director_plan/test_creative_contract.py tests/director_plan/test_models.py tests/director_plan/test_store.py tests/director_plan/test_migration.py -q`；仅提交本任务路径，消息 `feat(director): add versioned creative contract`。

接口草图（新增类型均在本任务定义）：

```python
class DecisionSource(BaseModel):
    kind: Literal["screenplay", "asset", "director", "observation"]
    ids: tuple[str, ...]

class DecisionBasis(BaseModel):
    purpose: str = Field(min_length=1)
    source: DecisionSource
    applicable: bool = True
    reason: str = ""
```

物理参数不适用时必须提供原因；字符串必需字段统一 strip 后非空，不仅依靠 min_length。

## 任务 3：生成与独立创作审查

文件：`creative_review.py`、`prompts.py`、`planner.py`、`service.py`、`validation.py`；新建 `tests/director_plan/test_creative_review.py`，修改 `test_start_state_policy.py`、`test_validation.py`。

- [ ] 写失败测试：结构通过但光学/构图矛盾仍需修改；生成者自填通过无效；审查失败或证据不足不自动放行；无动作变化的保持镜头可合法。
- [ ] 新版生成顺序为场次共享事实 → 叙事组目标与覆盖 → 单镜决定 → 跨组衔接审查。生成与修复共用规则；不要求每镜必须有大动作或必须从动作开始前进入。
- [ ] 审查报告记录 plan_revision_id、rules_version、status、findings；每个 finding 含 code、severity、dimension、shot_ids、evidence_paths、impact、suggestion。status 为 unreviewed / needs_revision / passed / needs_human。校验所有证据路径和镜号确实属于被审查版本。
- [ ] 复用现有文本运行时执行一次独立审查；异常保持未通过状态，不自动付费循环。确定性检查处理 ID、来源、缺项；语义审查处理摄影联动、时间可执行性、反应覆盖、世界光源和情绪衔接。
- [ ] 结构正确且无阻断问题才可自动放行；审美争议可人工注明理由采用，非法来源不能豁免。修复只产生带改变项/保留项的候选，不覆盖已采用版。
- [ ] 运行 `uv run pytest tests/director_plan/test_creative_review.py tests/director_plan/test_validation.py tests/director_plan/test_start_state_policy.py tests/director_plan/test_planner.py -q`；提交 `feat(director): review creative decisions independently`。

核心状态规则：

```python
def can_auto_adopt(structure_passed, review, revision_id):
    return (structure_passed and review.plan_revision_id == revision_id
            and review.status == "passed"
            and not any(f.severity == "blocking" for f in review.findings))
```

## 任务 4：关键帧与提示词保真

文件：`keyframes.py`、`narrative_groups/service.py`、`image_prompt.py`；新建 `tests/director_plan/test_keyframe_projection.py`，修改 `tests/test_narrative_group_image_prompt_overrides.py`、`test_director_intent_projection.py`。

- [ ] 写失败测试：静态投影保留当时朝向、视线、焦点、曝光和道具状态；未采用的未来动作不出现；末帧必须显式指定；手工 override 不能绕过新版契约；旧 override 保持兼容。
- [ ] 定义关键帧锚点为 start / end / event_id，默认 start；没有已定义状态的中间事件返回明确错误，不自动插值臆造。返回内容与消费字段列表，便于任务 5 建立指纹。
- [ ] `panel_description` 按契约版本分流；新版先解析关键帧，再装配必需锚点。override 作为候选表达接受一致性检查，冲突时返回问题，不能直接 early return。
- [ ] 参考资产明确身份/造型/空间/风格/构图角色；既有描述常量按采用版本复用，行为按场改写。仅装配当前可见/可闻对象；历史音频只作背景时不能生成重复声音或新人物。
- [ ] 预算不足显式报错，不截断关键决定；分辨率、供应商引用编码由对应适配处理。不得以更短提示词为理由删掉身份或空间锚点。
- [ ] 运行 `uv run pytest tests/director_plan/test_keyframe_projection.py tests/test_narrative_group_image_prompt_overrides.py tests/test_director_intent_projection.py tests/director_plan/test_legacy_downstream_projection.py -q`；提交 `fix(storyboard): preserve adopted decisions in keyframes`。

投影返回协议：

```python
class KeyframeProjection(BaseModel):
    shot_id: str
    revision_id: str
    anchor: str
    facts: dict[str, object]
    consumed_paths: tuple[str, ...]
```

## 任务 5：修订与精准失效

文件：`creative_dependencies.py`、`editing.py`、`production_state.py`、`narrative_groups/service.py`；新建 `tests/director_plan/test_creative_dependencies.py`。

- [ ] 写失败测试：改起始视线使首帧和视频陈旧；仅改后半动作不影响未消费它的首帧；场次光源改变影响所有相关镜头；仅 H3 编码变化不影响图片；组图一格变化使共享图片陈旧。
- [ ] 根据实际消费字段计算稳定 JSON/hash；将 revision_id、asset_hash、consumed_paths 与结果保存。场次共享事实纳入依赖，不能只比较单镜文本。
- [ ] 修订保留历史结果与原采用版本，拆并镜记录替代映射；陈旧状态不自动重生成。媒体观察冲突保存预期与实测，接受观察需创建新导演修订。
- [ ] 所有手工编辑、复制、修复、组拆并入口执行一致校验；只改了说明文字且不影响消费字段时，不使媒体无差别失效。
- [ ] 运行 `uv run pytest tests/director_plan/test_creative_dependencies.py tests/director_plan/test_editing.py tests/director_plan/test_production_state.py tests/test_narrative_group_image_revision.py -q`；提交 `feat(director): track creative dependencies by consumed fields`。

## 任务 6：H3 映射与生产能力拆包

文件：`h3_rigid_prompt.py`、`h3_prompt_compiler.py`、`director_plan/generation.py`；新建 `tests/media_capabilities/video/test_h3_creative_mapping.py`，修改 `tests/director_plan/test_generation.py`。

- [ ] 写失败测试：新版焦段/表演/光源来自采用契约；旧 H3 推测不倒灌；超过上限可保存导演计划；必须改切点才能执行时返回 capability_conflict。
- [ ] 提取旧 H3 类型中的创作语义映射到通用类型；保留旧数据兼容分支。新版编译只做表达、参考槽位、时长/帧化和能力检查，报告舍入误差。
- [ ] 生产包保存 group_id、shot_id、导演时间区间及执行区间；只在能保留动作、切点和连续性时拆包。长镜头不能静默变成剪辑镜头；当前后端无法实现则停止受影响包并给出导演修订候选。
- [ ] 所有生产仍以叙事组进入现有流程；不增加独立逐镜生成脚本，不把执行包写回叙事分组。
- [ ] 运行 `uv run pytest tests/media_capabilities/video/test_h3_creative_mapping.py tests/media_capabilities/video/test_h3_director_plan.py tests/director_plan/test_generation.py tests/director_plan/test_m3_production_integration.py -q`；提交 `refactor(h3): consume generic director decisions`。

## 任务 7：API 与工作台

文件：文件职责表中 API、bridge、前端文件；修改 `tests/test_api_director_plans.py`、`tests/agent_teams/test_director_bridge.py`、`frontend/src/__tests__/components/episode/director-review-workbench.test.tsx`。

- [ ] 写失败测试：旧计划展示未按新标准审查；新版逐镜显示八项决定、来源和跨镜问题；未解决阻断问题禁止自动采用；候选修订与当前采用版区别明确。
- [ ] API 暴露创作契约版本、review、依赖状态和采用理由；复制、手动编辑、团队入口调用同一服务校验，不能由前端自行决定合格。
- [ ] 工作台展示可理解的“焦段与焦点”“人物反应”“前后镜衔接”等字段，问题定位镜号与证据；候选变化展示改变项/保留项。资产证据缺失按影响分级，不能整集一律阻断。
- [ ] 剪辑反馈沿用候选修订入口，保存缺少反应镜/道具插入/空间交代的节拍与镜头对；不新增剪辑器。实现局部组件后对实际界面截图核对，再报告 UI 完成。
- [ ] 运行后端 `uv run pytest tests/test_api_director_plans.py tests/agent_teams/test_director_bridge.py -q`；在 frontend 运行 `npm test -- src/__tests__/components/episode/director-review-workbench.test.tsx src/__tests__/lib/queries/director-plans.test.tsx`。提交 `feat(director-ui): expose creative review and revision state`。

## 任务 8：跨层验收与交付

- [ ] 运行任务 1 的全部正反例；mock 只证明接口与阻断行为，真实语义质量另记人工判定，不能用 mock 通过宣称 AI 审查有效。
- [ ] 对同一方案检查通用契约 → 首帧提示词 → H3 编译保真，保存前后差异。核对静止、有意越轴、多光源等合法案例没有被机械拒绝。
- [ ] 运行全部 `tests/director_plan` 及前述受影响测试一次；有新增失败才扩大排查，不启动整套付费生成。
- [ ] 选择当前项目已有采用分镜作只读审查，记录项目、集数、修订 ID、源 hash，覆盖八项与全部组间衔接；若无可用样本明确报告“实际作品未验收”，不要制造样本项目身份。
- [ ] 最终报告分别列出契约/传递测试、语义审查证据、实际媒体证据和未验收项。保留所有人工采用历史，不能以一次测试通过命名为“生产级通过”。

## 规格覆盖与检查点

| 规格 | 实现任务 |
| --- | --- |
| 1–4：边界、权威、八项决定 | 1、2 |
| 5：生成、审查、采用 | 3、7 |
| 6：关键帧与出图 | 4 |
| 7：H3 与生产拆包 | 6 |
| 8–9：失效、历史兼容、各入口 | 2、5、7 |
| 10–11：验收与模块顺序 | 1、8 |
| 12：本轮仅文档 | 本计划交付边界 |
| 13：资产就绪、表演母本、覆盖、定点修订、剪辑反馈 | 1–8 的对应场景和接口 |

检查点 A（任务 1–3）：契约与审查成立，尚不代表下游采用。检查点 B（任务 4–6）：跨层保真与能力边界成立。检查点 C（任务 7–8）：用户能看到并采用修订，报告真实验收范围。

本轮未执行上述测试，未修改实现。代码执行阶段应依据当时最新源文件细化函数补丁；这里的接口草图不替代完整实现和回归证据。
