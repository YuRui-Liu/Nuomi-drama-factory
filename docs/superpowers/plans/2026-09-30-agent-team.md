# Agent 团队实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 交付固定流程下可版本化替换创作方法的团队模板、项目覆盖和真实运行追溯。
**架构：** 岗位目录、版本存储、配置解析和现有执行器分离；提交任务时冻结配置。试跑与生产共享生成逻辑，但写入目标隔离。
**技术栈：** Python 3.11、Pydantic、SQLite、FastAPI、现有 task backend；React、TypeScript、React Query、Vitest。
**规格：** `docs/superpowers/specs/2026-09-30-agent-team-design.md`，用户已批准。

## 工作区与依赖

当前 checkout 有大量不属于本任务的修改，且 creative_studios 的部分代码尚未提交。此计划仅新增文档；正式实施前使用原生 worktree 工具建立隔离工作区，并核对依赖文件是否存在于所选基线。不能假定从 HEAD 新建的 worktree 包含当前未跟踪模块，不能擅自提交或丢弃它们。先协调依赖集成或明确复制所需依赖后执行；记录基线提交。只暂存各任务拥有的文件。

## 文件边界

新增后端包 `src/novelvideo/agent_teams/`，其中模型、目录、存储、资源、解析、服务、执行适配器、试跑及导演桥接分别独立。新增路由 `src/novelvideo/api/routes/agent_teams.py`；在现有路由聚合处注册。
新增前端目录 `frontend/src/features/studios/agent-team/`，API/types/queries 为 .ts，视图为 .tsx，每个视图配同名 .test.tsx。后端测试为 `tests/agent_teams/test_<task>.py`。
现有工作室 `studio-page.tsx`、`studio-context.ts`、`studio-api.ts` 仅调整导航接入。禁止无关重构。

## 统一契约

- 资源：id、kind(prompt/skill/reference)、revision、owner、content、content_hash、archived。
- 团队：id、revision、owner、name、roles；roles 按 role_id/subtask_id 索引 MethodConfig。
- MethodConfig：model（项目路由或明确模型引用）、prompt、skills（固定版本引用列表）、references、director_preferences。
- 项目：project_id、template_id/version、overrides、draft_revision、active_revision；覆盖键缺失表示继承，列表整体替换。
- 快照：id、project_id、role_id、subtask_id、input_revision/hash、template_revision、active_revision、resolved_method、resource_snapshots、resolved_model；不含凭据。
- 试跑：comparison_id、side(active/draft)、attempt、input_snapshot_id、method_snapshot_id、task_id、status、candidate、duration、cost。
- 错误使用 code、message、field；409 为版本冲突，422 为配置不兼容，403 为权限错误。

服务契约：resolve_fields(base, overrides)；save_draft(project_id, data, expected_revision)；activate(project_id, snapshot, expected_active_revision)；freeze(project_id, role_id, subtask_id, input_revision)；load_snapshot(id)。
API 前缀 `/api/v1/projects/{project}/agent-team`：GET 总览，PUT /draft，POST /activate、/upgrade、/restore-field，GET /versions、/diff，POST /trials，GET /trials/{id}，POST /trials/{id}/retry-side。用户作用域模板与资源 API 分别放 `/api/v1/agent-team-templates`、`/api/v1/agent-team-resources`，每个读取/写入做所有者授权，不以客户端 owner 字段判权。

## 实际调用接入清单

| 岗位 | 先检查的真实文件 | 接入判定 |
|---|---|---|
| 编剧 | script_creation/prompts.py、task_backend/runners/script_creation.py | 按文档 kind 测试 prompt/skill 注入 |
| 剧本审查 | task_backend/runners/script_creation_consistency.py、agents/episode_reviewer.py | 区分实际入口，协议不可覆盖 |
| 剧本解析 | screenplay_semantics/prompts.py、task_backend/runners/screenplay_semantics.py | 结构化结果仍过原校验 |
| 分镜导演 | director_plan/prompts.py、task_backend/runners/director_plan.py | studio_config 与统一方法同源 |
| 分镜审查 | agents/episode_reviewer.py 及导演现有校验调用 | 规则校验不得伪装成可替换 Agent |
| 资产设计/审查 | agents/asset_compiler.py、keyframe_prompt_builder.py、character_reviewer.py | 按资产子任务绑定，不修改工具 |
| 视频导演 | agents/video_prompt_builder.py、media_capabilities/video/h3_prompt_optimizer.py | 保留模型专属编译协议 |

表内后端路径均相对 `src/novelvideo/`。先用调用引用追踪确认运行路径；只存在文件不代表已接入。所有可用路径均需纳入适配器契约测试；缺口必须在目录返回尚未接入，并作为交付限制报告，不能据此宣布完整接入完成。

## 任务 1：配置契约与岗位清单

**文件：** models.py、catalog.py。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_contracts.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_override_keeps_inherited_fields():
    base = {"model": "project", "prompt": "original", "skills": ["s@1"]}
    actual = resolve_fields(base, {"prompt": "custom"})
    assert actual == {"model": "project", "prompt": "custom", "skills": ["s@1"]}
    assert resolve_fields(base, {"skills": []})["skills"] == []
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_contracts.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：定义 ResourceVersion、MethodConfig、RoleDefinition、TeamVersion、ProjectDraft、ExecutionSnapshot；配置字段固定为 model、prompt、skills、references、director_preferences。岗位包含 subtasks 与 adapter_id。目录按实际调用点登记，缺少适配器返回 connected=false。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
def resolve_fields(base: dict, overrides: dict) -> dict:
    unknown = overrides.keys() - base.keys()
    if unknown:
        raise ValueError("UNKNOWN_OVERRIDE_FIELD")
    return {key: overrides[key] if key in overrides else value
            for key, value in base.items()}
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_contracts.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 配置契约与岗位清单` 单独提交。不要使用 git add .。

## 任务 2：版本存储、模板和资源

**文件：** store.py、resources.py。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_store.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_draft_does_not_activate(store, published_team):
    before = store.get_binding("project-a")
    store.save_draft("project-a", {"template": published_team}, expected_revision=0)
    assert store.get_binding("project-a") == before
def test_stale_write_fails(store):
    store.save_draft("project-a", {}, expected_revision=0)
    with pytest.raises(RevisionConflict):
        store.save_draft("project-a", {}, expected_revision=0)
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_store.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：SQLite 事务分别维护资源版本、团队版本、项目草稿、启用指针与快照。发布版本只增不改；草稿更新需 expected_revision。模板支持复制、发布、项目另存模板；资源引用版本不可删除，可停用。跨项目模板放在现有用户配置存储作用域，项目绑定放项目 state；不得借项目权限写他人的模板。测试 fixture 使用临时目录并显式传入所有者。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
db.execute("BEGIN IMMEDIATE")
current = db.execute("SELECT revision FROM project_drafts WHERE project_id=?", (project_id,)).fetchone()
if (current[0] if current else 0) != expected_revision:
    raise RevisionConflict("DRAFT_REVISION_CONFLICT")
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_store.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 版本存储、模板和资源` 单独提交。不要使用 git add .。

## 任务 3：解析、比较、启用及 HTTP 接口

**文件：** resolver.py、service.py；src/novelvideo/api/routes/agent_teams.py。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_api.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_unconnected_role_cannot_activate(client, editor_headers):
    response = client.post("/api/v1/projects/demo/agent-team/activate",
        headers=editor_headers, json={"draft_revision": 1, "expected_active_revision": 0})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ROLE_NOT_CONNECTED"
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_api.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：解析固定模板与字段覆盖，返回每字段来源。比较旧模板、新模板、项目覆盖；升级默认保留覆盖。启用校验引用可访问性、必填变量、模型能力、适配器和接口版本，事务 CAS 切换指针。恢复继承只移除覆盖键并保存草稿。API 使用现有项目 resolve、权限和错误封装；读者不可写项目，项目编辑者不可修改非本人模板。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
effective = resolve_fields(template_method, project_overrides)
validate_adapter(effective, role)
snapshot = freeze_resources(effective)
store.activate(project_id, snapshot, expected_active_revision=expected_active_revision)
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_api.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 解析、比较、启用及 HTTP 接口` 单独提交。不要使用 git add .。

## 任务 4：真实执行接入与方法追溯

**文件：** runtime.py、adapters.py；现有任务提交入口与 runners。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_runtime.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_queued_task_keeps_snapshot(runtime_fixture):
    task = runtime_fixture.submit(prompt="method-a")
    runtime_fixture.activate(prompt="method-b")
    result = runtime_fixture.run(task)
    assert result.recorded_prompt == "method-a"
    assert result.snapshot_id == task.snapshot_id
def test_missing_team_keeps_legacy_behavior(runtime_fixture):
    result = runtime_fixture.submit_without_team_and_run()
    assert result.used_legacy_path
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_runtime.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：新增 submit 时解析并冻结的方法快照；runner 只读快照。对编剧子任务、语义解析、导演规划、资产提示词/审查、视频提示词各写适配器测试，替换创作层不替换固定协议层。新旧入口、画布入口逐一核对。没有可验证适配器的子任务禁止启用且列出缺口。结果附实际模型、资源快照、输入 hash、模板及项目版本；重试复用同一快照。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
method = resolver.freeze(project_id, role_id, subtask_id, input_revision)
payload["creative_method_snapshot_id"] = store.save_snapshot(method)
# runner: load_snapshot 必须只读，不得重新 resolve 当前项目配置。
method = store.load_snapshot(payload["creative_method_snapshot_id"])
result = adapter.run(inputs=frozen_inputs, method=method, output_target=target)
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_runtime.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 真实执行接入与方法追溯` 单独提交。不要使用 git add .。

## 任务 5：隔离试跑及单侧重试

**文件：** trials.py；src/novelvideo/task_backend/runners/agent_team_trial.py。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_trials.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_trial_never_publishes(trial_fixture):
    before = trial_fixture.production_digest()
    trial = trial_fixture.compare_same_input()
    assert trial.left.input_hash == trial.right.input_hash
    assert trial_fixture.production_digest() == before
def test_retry_only_failed_side(trial_fixture):
    trial = trial_fixture.fail_right()
    trial_fixture.retry_right(trial.id)
    assert trial_fixture.left_calls == 1
    assert trial_fixture.right_calls == 2
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_trials.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：复用 task backend 注册试跑任务，左右侧各保存输入快照与方法快照。适配器把输出目标显式区分 candidate/production；无法隔离副作用的适配器不开放试跑。候选保存独立 namespace，不调用启用、资产绑定或后续生产任务。幂等键包含比较 ID 与侧；失败重试增加 attempt 而不重跑成功侧。展示耗时、实际费用/未提供及取消结果。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
for side in ("active", "draft"):
    enqueue_trial(comparison_id=comparison_id, side=side,
                  input_snapshot_id=input_snapshot_id,
                  method_snapshot_id=method_ids[side],
                  output_target="candidate")
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_trials.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 隔离试跑及单侧重试` 单独提交。不要使用 git add .。

## 任务 6：导演方法桥接

**文件：** director_bridge.py；frontend/src/features/studios/director-studio.tsx；src/novelvideo/api/routes/studio_director.py。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** `tests/agent_teams/test_director_bridge.py`。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```python
def test_import_does_not_activate(bridge_fixture):
    before = bridge_fixture.active_version()
    bridge_fixture.import_legacy_director()
    assert bridge_fixture.active_version() == before
def test_both_editors_share_draft(bridge_fixture):
    bridge_fixture.save_from_director(pace="slow")
    assert bridge_fixture.read_from_team()["director_preferences"]["pace"] == "slow"
```

- [ ] 执行 `uv run pytest tests/agent_teams/test_director_bridge.py -v`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：把旧导演配置导入为候选；用户选择后桥接同一岗位草稿及启用指针。导演入口发起 AI 任务时走相同快照解析；无团队项目保留旧行为。单镜头运镜构图修改仍是方案编辑，不修改团队方法。两个入口共享 query invalidation 与 expected_revision。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```python
if project_has_team(project_id):
    return team_service.get_method(project_id, "director", "plan")
return legacy_director_store.get(document_id)
```

- [ ] 再执行 `uv run pytest tests/agent_teams/test_director_bridge.py -v`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 导演方法桥接` 单独提交。不要使用 git add .。

## 任务 7：前端总览与岗位编辑

**文件：** frontend/src/features/studios/agent-team/{api,types,queries,team-studio,role-overview,method-editor,version-panel}.tsx/ts。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** 对应组件同名 .test.tsx。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```tsx
it("saving a draft does not label it active", async () => {
  render(<TeamStudio project="demo" />);
  await user.click(await screen.findByRole("button", { name: "保存草稿" }));
  expect(await screen.findByText("草稿已保存")).toBeInTheDocument();
  expect(screen.getByText("当前启用 v2")).toBeInTheDocument();
});
it("disables activation for an unconnected role", async () => {
  render(<TeamStudio project="demo" />);
  await user.click(await screen.findByRole("button", { name: "尚未接入岗位" }));
  expect(screen.getByRole("button", { name: "启用" })).toBeDisabled();
});
```

- [ ] 执行 `cd frontend && ./node_modules/.bin/vitest run src/features/studios/agent-team`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：使用现有 React Query、Button、错误封装和未保存保护。新增团队页签但分离工作室导航类型与 StudioDocument 存储种类，不能把 team 误送旧文档 API。顶部模板栏，左侧分阶段岗位，右侧三个页签；宽编辑/窄屏返回保持选择及草稿。每字段来源与恢复继承，多子任务下拉，协议只读。保存冲突保留文本，启用显示差异与范围。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```tsx
const saveDraft = useMutation({
  mutationFn: api.saveDraft,
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-team", project, "draft"] }),
});
// saveDraft 不刷新为启用版；启用使用独立 mutation 与确认面板。
```

- [ ] 再执行 `cd frontend && ./node_modules/.bin/vitest run src/features/studios/agent-team`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 前端总览与岗位编辑` 单独提交。不要使用 git add .。

## 任务 8：模板、资源库、试跑和结果入口

**文件：** frontend/src/features/studios/agent-team/{template-panel,resource-library,trial-panel,method-trace}.tsx。未加前缀的后端文件位于 `src/novelvideo/agent_teams/`。
**测试：** 对应组件同名 .test.tsx。测试中的 store/client/runtime_fixture/trial_fixture/bridge_fixture 为该测试模块或 `tests/agent_teams/conftest.py` 显式创建的临时存储与假模型依赖；不得调用付费模型。前端使用 Testing Library userEvent、vi.mock API 和 React Query 测试 provider。

- [ ] 写行为回归测试；以下断言作为最低要求，并为本任务描述中的失败路径增加用例：

```tsx
it("keeps project overrides selected during template upgrade", async () => {
  render(<TemplatePanel project="demo" />);
  await user.click(await screen.findByRole("button", { name: "比较模板更新" }));
  expect(screen.getByRole("checkbox", { name: "保留项目 Prompt" })).toBeChecked();
});
it("can retry the failed side without rerunning the successful side", async () => {
  render(<TrialPanel project="demo" />);
  await user.click(await screen.findByRole("button", { name: "重试草稿侧" }));
  expect(api.retryTrialSide).toHaveBeenCalledWith(expect.objectContaining({ side: "draft" }));
});
```

- [ ] 执行 `cd frontend && ./node_modules/.bin/vitest run src/features/studios/agent-team`，确认失败来自尚未实现的行为，而非 fixture 或导入环境错误。
- [ ] 实现本任务：模板复制发布/另存模板，资源内容与版本编辑/引用位置，试跑输入版本选择/两次调用提示/左右结果/耗时费用/失败侧重试。费用缺失显示未提供，不能显示 0。任务结果添加本次创作方法入口；由真实 task snapshot 加载，不查询当前岗位配置冒充历史版本。
- [ ] 按以下关键逻辑连接本任务的真实数据边界（代码片段为接口示意，实际实现须包含上述事务、校验和错误分支）：

```tsx
const costText = result.cost == null ? "未提供" : formatCost(result.cost);
const retryableSides = results.filter(side => side.status === "failed");
// 试跑界面没有写入正式产物按钮；用户启用方法后通过原生产入口生成。
```

- [ ] 再执行 `cd frontend && ./node_modules/.bin/vitest run src/features/studios/agent-team`，期望所有新增用例通过；对涉及的旧模块运行其现有测试，验证未选择团队的路径行为不变。
- [ ] 检查 `git diff --check`；只 add 本任务文件，以 `feat(agent-team): 模板、资源库、试跑和结果入口` 单独提交。不要使用 git add .。

## 任务 9：完整验收与交付

- [ ] 后端运行 `uv run pytest tests/agent_teams tests/test_studio_director.py -v`；前端运行 `./node_modules/.bin/vitest run src/features/studios` 和 `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json --incremental false`，全部通过才进入 UI 验证。
- [ ] 用浏览器在测试项目执行：选择模板 → 单字段覆盖 → 保存 → 固定输入试跑 → 比较启用 → 创建后续任务 → 查看任务方法快照 → 模板升级保留覆盖 → 回退。使用本地假模型验证副作用和快照，真实付费试跑不属于默认验收。
- [ ] 检查窄屏/展开编辑/键盘导航/未保存离开/冲突/无模型/无素材/单侧失败。保存总览、字段来源、双侧比较及历史追溯截图。
- [ ] 在前后两个任务快照中核对版本，证明排队任务保持旧版本，新任务使用启用版；确认正式产物摘要在试跑前后不变。
- [ ] 更新规格的实现状态和岗位接入清单，逐项列真实入口与测试证据；任何未接入岗位明确报告，不能把只读目录算作完整接入。
- [ ] 只提交本功能变更，报告验证结果、已接入子任务、实际限制及截图。无需自动部署或生成付费媒体。

## 规格覆盖与计划自检

| 规格要求 | 任务 |
|---|---|
| 模板/资源版本、所有权、项目覆盖 | 1–3 |
| 原型总览、字段来源、宽窄编辑 | 7 |
| 真实任务注入、固定快照、无团队兼容 | 4 |
| 试跑隔离、计费、幂等、失败侧重试 | 5、8 |
| 导演同源配置与迁移 | 6 |
| 模板比较、版本启用回退、资源引用 | 2、3、8 |
| 可访问性、冲突、权限、集成验收 | 3、7、9 |

本计划保留已有执行工具与接口，不新增任意代码 Skill 执行。各阶段为同一功能的依赖任务，不将未接入状态作为替代真实运行接入的交付捷径。
