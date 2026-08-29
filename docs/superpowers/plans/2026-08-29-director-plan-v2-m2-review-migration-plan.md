# DirectorPlan v2 M2 审核与资产迁移实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 提供新旧 DirectorPlan 对比、结构化人工编辑、可解释资产迁移、显式激活和版本回退界面。

**架构：** 后端在 draft revision 上执行命令式编辑，每次编辑产生新的 draft revision，不就地修改已保存版本。资产匹配器输出高/中/低置信度和证据；高置信度默认接受，中低置信度等待用户。前端以 revision review workspace 展示差异、迁移和激活决策。

**技术栈：** Python 3.11、FastAPI、React 19、TanStack Query、Zustand、Vitest、pytest

---

## 文件结构

- 创建 `src/novelvideo/director_plan/editing.py`：拆分、合并、移动、重排和 Shot 编辑命令。
- 创建 `src/novelvideo/director_plan/migration.py`：旧/新素材相似度和迁移报告。
- 修改 `src/novelvideo/director_plan/models.py`：编辑来源和迁移决策 DTO。
- 修改 `src/novelvideo/director_plan/service.py`：编辑派生 revision、迁移决策、激活和恢复。
- 修改 `src/novelvideo/task_backend/runners/director_plan.py`：在验证后执行迁移匹配并上报阶段。
- 修改 `src/novelvideo/api/routes/director_plans.py`：审核、编辑、迁移和恢复 API。
- 创建 `tests/director_plan/test_editing.py`。
- 创建 `tests/director_plan/test_migration.py`。
- 修改 `tests/test_api_director_plans.py`。
- 创建 `frontend/src/lib/queries/director-plans.ts`：类型、查询和 mutation。
- 创建 `frontend/src/components/episode/director-review/director-review-workbench.tsx`：审核主容器。
- 创建 `frontend/src/components/episode/director-review/revision-comparison.tsx`：新旧组对比。
- 创建 `frontend/src/components/episode/director-review/group-editor.tsx`：结构编辑操作。
- 创建 `frontend/src/components/episode/director-review/asset-migration-panel.tsx`：迁移审核。
- 创建 `frontend/src/components/episode/director-review/activation-bar.tsx`：激活、放弃、恢复。
- 修改 `frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`：接入“重新导演分镜”和审核模式。
- 创建 `frontend/src/__tests__/lib/queries/director-plans.test.tsx`。
- 创建 `frontend/src/__tests__/components/episode/director-review-workbench.test.tsx`。
- 修改 `frontend/src/__tests__/routes/episodes-workbench-integration.test.ts`。

### 任务 1：实现不可变结构编辑命令

**文件：**
- 创建：`src/novelvideo/director_plan/editing.py`
- 修改：`src/novelvideo/director_plan/models.py`
- 测试：`tests/director_plan/test_editing.py`

- [ ] **步骤 1：编写失败的拆分、合并和移动测试**

```python
def test_split_group_creates_new_revision_and_preserves_source_order():
    edited = apply_edit(
        active_plan(),
        SplitGroup(group_id="ng-02", before_shot_id="shot-5"),
    )
    assert edited.revision_id != active_plan().revision_id
    assert [g.source_span_ids for g in edited.groups] == [
        ("s1", "s2"), ("s3", "s4"), ("s5", "s6")
    ]
    assert edited.parent_revision_id == active_plan().revision_id


def test_move_shot_rejects_crossing_hard_scene_boundary():
    with pytest.raises(DirectorEditError, match="hard boundary"):
        apply_edit(active_plan(), MoveShot("shot-4", target_group_id="ng-03", index=0))
```

- [ ] **步骤 2：运行测试确认缺少编辑模块**

运行：`uv run pytest tests/director_plan/test_editing.py -q`

预期：FAIL，无法导入 `apply_edit`。

- [ ] **步骤 3：定义编辑命令和统一入口**

```python
DirectorEdit = Annotated[
    SplitGroup | MergeAdjacentGroups | MoveShot | ReorderGroups | UpdateShot,
    Field(discriminator="kind"),
]


def apply_edit(
    revision: DirectorPlanRevision,
    command: DirectorEdit,
    source_spans: Sequence[SourceSpan],
) -> DirectorPlanRevision:
    candidate = _dispatch_edit(revision, command)
    report = validate_director_plan(candidate, source_spans)
    if not report.passed:
        raise DirectorEditError(report)
    return candidate.model_copy(update={
        "revision_id": str(ULID()),
        "parent_revision_id": revision.revision_id,
        "status": "review_required",
        "validation_report": report,
    })
```

只允许合并相邻组；移动 Shot 后源 span 顺序必须保持；UpdateShot 不允许修改对白正文，只能修改对白 source ID。

- [ ] **步骤 4：运行编辑测试**

运行：`uv run pytest tests/director_plan/test_editing.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/director_plan/editing.py src/novelvideo/director_plan/models.py tests/director_plan/test_editing.py
git commit -m "feat: edit director plans through immutable commands"
```

### 任务 2：实现可解释资产迁移

**文件：**
- 创建：`src/novelvideo/director_plan/migration.py`
- 修改：`src/novelvideo/director_plan/service.py`
- 修改：`src/novelvideo/task_backend/runners/director_plan.py`
- 测试：`tests/director_plan/test_migration.py`
- 修改：`tests/test_task_director_plan_runner.py`

- [ ] **步骤 1：编写失败的置信度与风格门禁测试**

```python
def test_matcher_auto_applies_only_high_confidence_same_style_assets():
    report = match_assets(
        old_plan=old_plan(),
        new_plan=new_plan(),
        assets=(asset("old-shot", style_hash="style-a"),),
    )
    item = report.items[0]
    assert item.confidence == "high"
    assert item.decision == "accepted"
    assert item.evidence.source_overlap == 1.0


def test_different_style_can_only_be_reference():
    item = match_one(old_asset(style_hash="a"), new_shot(style_hash="b"))
    assert item.reuse_mode == "reference_only"
    assert item.decision == "review"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`uv run pytest tests/director_plan/test_migration.py -q`

预期：FAIL，缺少 `match_assets`。

- [ ] **步骤 3：实现确定性评分和解释**

```python
score = (
    0.35 * source_overlap
    + 0.20 * subject_overlap
    + 0.15 * scene_match
    + 0.15 * action_similarity
    + 0.15 * shot_semantic_similarity
)
confidence = "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
```

同一旧资产只能自动分配一次；候选冲突时全部降为 `review`。`style_hash` 不同强制 `reference_only`，不得因为总分高而成为正式素材。报告记录每个分量、旧/新 ID、建议决策和人工决策。runner 在验证通过后调用 matcher，并上报 `assets_matched` 85%，随后才进入 `review_ready` 100%。

- [ ] **步骤 4：运行迁移测试**

运行：`uv run pytest tests/director_plan/test_migration.py tests/test_task_director_plan_runner.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/director_plan/migration.py src/novelvideo/director_plan/service.py src/novelvideo/task_backend/runners/director_plan.py tests/director_plan/test_migration.py tests/test_task_director_plan_runner.py
git commit -m "feat: explain and review director asset migration"
```

### 任务 3：暴露审核、编辑和回退 API

**文件：**
- 修改：`src/novelvideo/api/routes/director_plans.py`
- 修改：`tests/test_api_director_plans.py`

- [ ] **步骤 1：编写失败的 API 契约测试**

```python
def test_edit_creates_child_revision_and_restore_reactivates_old_revision(client):
    edited = client.post(
        "/api/v1/projects/p/episodes/1/director-plans/rev-2/edits",
        json={"kind": "split_group", "group_id": "ng-02", "before_shot_id": "shot-5"},
    )
    assert edited.status_code == 201
    assert edited.json()["data"]["parent_revision_id"] == "rev-2"
    restored = client.post(
        "/api/v1/projects/p/episodes/1/director-plans/rev-1/activate", json={}
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["status"] == "active"
```

- [ ] **步骤 2：运行测试确认端点不存在**

运行：`uv run pytest tests/test_api_director_plans.py -q`

预期：FAIL，编辑或迁移端点返回 404。

- [ ] **步骤 3：实现审核端点**

```text
POST /director-plans/{revision_id}/edits
GET  /director-plans/{revision_id}/comparison?base={revision_id}
GET  /director-plans/{revision_id}/migration
PUT  /director-plans/{revision_id}/migration/{item_id}
POST /director-plans/{revision_id}/activate
POST /director-plans/{revision_id}/abandon
```

`abandon` 只允许把未激活 draft/review revision 标记为 `abandoned`，不物理删除 revision 或素材；active 和 superseded revision 返回 409。迁移决策只接受 `accepted`、`rejected`、`reference_only`。

- [ ] **步骤 4：运行 API 测试**

运行：`uv run pytest tests/test_api_director_plans.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/api/routes/director_plans.py tests/test_api_director_plans.py
git commit -m "feat: expose director plan review commands"
```

### 任务 4：建立前端 query、类型和审核状态

**文件：**
- 创建：`frontend/src/lib/queries/director-plans.ts`
- 创建：`frontend/src/__tests__/lib/queries/director-plans.test.tsx`

- [ ] **步骤 1：编写失败的 query 契约测试**

```tsx
it("posts a split edit and invalidates revision and narrative queries", async () => {
  server.use(http.post("*/director-plans/rev-2/edits", () => HttpResponse.json({ data: childRevision })))
  const { result } = renderHook(() => useEditDirectorPlan("p", 1), { wrapper })
  await result.current.mutateAsync({ revisionId: "rev-2", command: { kind: "split_group", group_id: "ng-02", before_shot_id: "shot-5" } })
  expect(queryClient.getQueryState(directorPlanKeys.detail("p", 1, "rev-2"))?.isInvalidated).toBe(true)
  expect(queryClient.getQueryState(queryKeys.narrativeGroups("p", 1))?.isInvalidated).toBe(true)
})
```

- [ ] **步骤 2：运行测试确认缺少模块**

运行：`corepack pnpm --dir frontend test -- director-plans.test.tsx`

预期：FAIL，无法解析 `@/lib/queries/director-plans`。

- [ ] **步骤 3：实现稳定 TypeScript DTO 和 hooks**

定义与后端同名字段的 `DirectorPlanRevision`、`NarrativeGroupPlan`、`ShotPlan`、`MigrationItem`；实现 list/detail/create/edit/activate/abandon/migration hooks。所有成功 mutation 同时失效 director plan 和 narrative group query key。

- [ ] **步骤 4：运行 query 测试**

运行：`corepack pnpm --dir frontend test -- director-plans.test.tsx`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/lib/queries/director-plans.ts frontend/src/__tests__/lib/queries/director-plans.test.tsx
git commit -m "feat: add director plan review queries"
```

### 任务 5：实现新旧版本对比和结构编辑 UI

**文件：**
- 创建：`frontend/src/components/episode/director-review/director-review-workbench.tsx`
- 创建：`frontend/src/components/episode/director-review/revision-comparison.tsx`
- 创建：`frontend/src/components/episode/director-review/group-editor.tsx`
- 创建：`frontend/src/components/episode/director-review/asset-migration-panel.tsx`
- 创建：`frontend/src/components/episode/director-review/activation-bar.tsx`
- 修改：`frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx`
- 测试：`frontend/src/__tests__/components/episode/director-review-workbench.test.tsx`
- 修改：`frontend/src/__tests__/routes/episodes-workbench-integration.test.ts`

- [ ] **步骤 1：编写失败的用户流程测试**

```tsx
it("creates a review revision, splits a group and activates it", async () => {
  render(<EpisodeWorkbench />)
  await user.click(screen.getByRole("button", { name: "重新导演分镜" }))
  expect(await screen.findByText("等待审核")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "在镜头 5 前拆分" }))
  expect(await screen.findByText("叙事组 03")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "激活新版" }))
  expect(await screen.findByText("当前版本")).toBeVisible()
})
```

- [ ] **步骤 2：运行测试确认 UI 不存在**

运行：`corepack pnpm --dir frontend test -- director-review-workbench.test.tsx episodes-workbench-integration.test.ts`

预期：FAIL，找不到“重新导演分镜”。

- [ ] **步骤 3：实现审核工作台**

界面固定包含：左侧 revision 列表；中间旧/新叙事组对照；右侧 migration panel；底部 activation bar。结构编辑按钮直接发送单一命令，不在前端复制后端校验逻辑。任务进度显示 `source_locked`、`episode_planned`、`validated`、`assets_matched`、`review_ready` 的中文标签和真实日志。

- [ ] **步骤 4：实现资产迁移审核**

高置信度显示“已自动匹配”并允许撤销；中置信度显示“待确认”；低置信度显示“未匹配”。`reference_only` 必须显示“仅作参考，激活后仍需重新渲染”。

- [ ] **步骤 5：运行组件和路由测试**

运行：`corepack pnpm --dir frontend test -- director-review-workbench.test.tsx episodes-workbench-integration.test.ts`

预期：PASS。

- [ ] **步骤 6：运行 M2 门禁**

运行：`uv run pytest tests/director_plan/test_editing.py tests/director_plan/test_migration.py tests/test_api_director_plans.py -q`

预期：PASS。

运行：`corepack pnpm --dir frontend test -- director-plans.test.tsx director-review-workbench.test.tsx episodes-workbench-integration.test.ts`

预期：PASS。

运行：`corepack pnpm --dir frontend build`

预期：TypeScript、Vite build 和 bundle budget 均通过。

- [ ] **步骤 7：提交**

```bash
git add frontend/src/components/episode/director-review frontend/src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx frontend/src/__tests__/components/episode/director-review-workbench.test.tsx frontend/src/__tests__/routes/episodes-workbench-integration.test.ts
git commit -m "feat: review and activate director plan revisions"
```

## M2 完成条件

- 所有结构编辑都生成 child revision，不修改历史版本。
- 高置信度同风格素材可自动匹配，中低置信度必须审核。
- 不同 style hash 的资产只能作为参考。
- 用户可对比、编辑、激活、放弃和恢复 revision。
- 激活成功后现有叙事组工作台刷新为新结构。
