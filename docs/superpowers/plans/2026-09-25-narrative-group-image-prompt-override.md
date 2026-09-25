# 叙事组出图提示词可编辑 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让用户能逐条编辑叙事组每个格位（镜头）的出图画面描述，持久化保存，并在重出图时生效，且不影响视频路径。

**架构：** 在叙事组可变 sidecar 的 `NarrativeGroup` 上新增 `image_prompt_overrides: dict[shot_id, str]`，沿用 `video_settings` 的投影保留机制。出图时通过 `generation_beats_for_group` 的**可选参数**替换 `visual_description`——只有出图调用点传值，视频的 5 个调用点保持逐字不变。

**技术栈：** Python 3.11 / dataclasses（`NarrativeGroup` 为 `frozen=True`，变更走 `replace`）/ FastAPI / pytest；React + TypeScript / TanStack Query / Vitest。

**规格：** `docs/superpowers/specs/2026-09-25-narrative-group-image-prompt-override-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/novelvideo/narrative_groups/models.py` | 修改 | `NarrativeGroup` 新增 `image_prompt_overrides` 字段 |
| `src/novelvideo/narrative_groups/service.py` | 修改 | 反序列化、投影保留、覆盖写入函数、可选注入参数与替换辅助函数 |
| `src/novelvideo/api/routes/narrative_groups.py` | 修改 | GET/PUT `image-prompts` 端点；出图调用点传入覆盖 |
| `frontend/src/lib/queries/narrative-groups.ts` | 修改 | 类型与两个 hook |
| `frontend/src/components/episode/narrative-workbench/group-image-prompt-dialog.tsx` | 创建 | 编辑对话框（预填、恢复默认、保存） |
| `frontend/src/components/episode/narrative-workbench/group-beat-inspector.tsx` | 修改 | 每格增加编辑入口与「已自定义」标记 |
| `tests/test_narrative_group_image_prompt_overrides.py` | 创建 | 后端：模型/持久化/注入/API |
| `frontend/src/__tests__/components/episode/narrative-workbench/group-image-prompt-dialog.test.tsx` | 创建 | 前端对话框行为 |
| `frontend/src/__tests__/components/episode/narrative-workbench/group-beat-inspector.test.tsx` | 修改 | 新入口与标记 |

---

## 任务 1：数据模型与持久化

**文件：**
- 修改：`src/novelvideo/narrative_groups/models.py:246-267`
- 修改：`src/novelvideo/narrative_groups/service.py:325-347`（反序列化）、`:459-475`（投影保留）
- 测试：`tests/test_narrative_group_image_prompt_overrides.py`（创建）

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_narrative_group_image_prompt_overrides.py`：

```python
from pathlib import Path

from novelvideo.narrative_groups.models import GridLayout, NarrativeGroup
from novelvideo.narrative_groups.service import load_groups, save_groups


def _group(group_id: str = "ng-01", **overrides) -> NarrativeGroup:
    base = dict(
        id=group_id,
        ordinal=1,
        beat_ids=("shot-01-01", "shot-01-02"),
        layout=GridLayout(rows=1, columns=2, capacity=2),
        cell_to_beat=(),
    )
    base.update(overrides)
    return NarrativeGroup(**base)


def test_group_defaults_to_no_image_prompt_overrides():
    assert _group().image_prompt_overrides == {}


def test_sidecar_without_the_field_still_loads(tmp_path):
    project = tmp_path / "proj"
    group = _group()
    save_groups(project, 1, [group])

    # 模拟旧版 sidecar：删掉该键后重新写盘
    import json
    from novelvideo.narrative_groups.service import sidecar_path

    path = sidecar_path(project, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["groups"]:
        item.pop("image_prompt_overrides", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_groups(project, 1)
    assert loaded[0].image_prompt_overrides == {}


def test_overrides_survive_a_sidecar_round_trip(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group(image_prompt_overrides={"shot-01-01": "改成铜甲"})])

    loaded = load_groups(project, 1)
    assert loaded[0].image_prompt_overrides == {"shot-01-01": "改成铜甲"}
```

若 `GridLayout` / `sidecar_path` 的导入名与仓库实际不符，以实际为准（先读 `src/novelvideo/narrative_groups/models.py` 与 `service.py` 确认）。

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q`
预期：FAIL，`TypeError: __init__() got an unexpected keyword argument 'image_prompt_overrides'`

- [ ] **步骤 3：编写最少实现代码**

`models.py` 的 `NarrativeGroup` 末尾新增字段：

```python
    image_prompt_overrides: dict[str, str] = field(default_factory=dict)
```

`service.py` 反序列化处（`NarrativeGroup(...)` 构造）新增：

```python
        image_prompt_overrides={
            str(key): str(value)
            for key, value in (data.get("image_prompt_overrides") or {}).items()
            if str(value).strip()
        },
```

`service.py` 的 `_materialize_active_groups` 中 `base = NarrativeGroup(...)` 新增：

```python
                image_prompt_overrides=(
                    dict(previous.image_prompt_overrides) if previous else {}
                ),
```

（`to_dict` 用 `asdict`，无需改动。）

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(narrative-groups): persist per-shot image prompt overrides" -- src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/service.py tests/test_narrative_group_image_prompt_overrides.py
```

---

## 任务 2：覆盖写入函数

**文件：**
- 修改：`src/novelvideo/narrative_groups/service.py`（新增函数，放在 `update_video_settings` 附近）
- 测试：`tests/test_narrative_group_image_prompt_overrides.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
import pytest

from novelvideo.narrative_groups.service import update_image_prompt_override


def _saved_group(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group()])
    return project


def test_sets_and_clears_an_override(tmp_path):
    project = _saved_group(tmp_path)

    update_image_prompt_override(project, 1, "ng-01", "shot-01-01", "  改成铜甲  ")
    assert load_groups(project, 1)[0].image_prompt_overrides == {"shot-01-01": "改成铜甲"}

    update_image_prompt_override(project, 1, "ng-01", "shot-01-01", "   ")
    assert load_groups(project, 1)[0].image_prompt_overrides == {}


def test_rejects_unknown_group_and_unknown_shot(tmp_path):
    project = _saved_group(tmp_path)

    with pytest.raises(KeyError):
        update_image_prompt_override(project, 1, "ng-99", "shot-01-01", "x")

    with pytest.raises(ValueError):
        update_image_prompt_override(project, 1, "ng-01", "shot-99-99", "x")


def test_rejects_overlong_prompt(tmp_path):
    project = _saved_group(tmp_path)

    with pytest.raises(ValueError):
        update_image_prompt_override(project, 1, "ng-01", "shot-01-01", "字" * 4001)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q -k "sets_and_clears or rejects"`
预期：FAIL，`ImportError: cannot import name 'update_image_prompt_override'`

- [ ] **步骤 3：编写最少实现代码**

在 `service.py` 新增（`replace` 需从 `dataclasses` 导入，文件内已有先例）：

```python
_IMAGE_PROMPT_OVERRIDE_LIMIT = 4000


def update_image_prompt_override(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    shot_id: str,
    prompt: str,
) -> NarrativeGroup:
    """设置或清除单个格位的出图提示词覆盖；返回更新后的 group。"""

    cleaned = str(prompt or "").strip()
    if len(cleaned) > _IMAGE_PROMPT_OVERRIDE_LIMIT:
        raise ValueError("image prompt override is too long")
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        target = next((item for item in groups if item.id == group_id), None)
        if target is None:
            raise KeyError(group_id)
        valid_ids = {mapping.beat_id for mapping in target.cell_to_beat} | set(target.beat_ids)
        if shot_id not in valid_ids:
            raise ValueError(f"shot_id is not part of group {group_id}: {shot_id}")
        overrides = dict(target.image_prompt_overrides)
        if cleaned:
            overrides[shot_id] = cleaned
        else:
            overrides.pop(shot_id, None)
        updated = replace(target, image_prompt_overrides=overrides)
        save_groups(
            project_dir,
            episode,
            [updated if item.id == group_id else item for item in groups],
        )
    return updated
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(narrative-groups): add the image prompt override writer" -- src/novelvideo/narrative_groups/service.py tests/test_narrative_group_image_prompt_overrides.py
```

---

## 任务 3：提示词注入（本计划最关键的安全边界）

**文件：**
- 修改：`src/novelvideo/narrative_groups/service.py:684` 的 `generation_beats_for_group`
- 修改：`src/novelvideo/api/routes/narrative_groups.py:1865`（**仅此一处**）
- 测试：`tests/test_narrative_group_image_prompt_overrides.py`

- [ ] **步骤 1：编写失败的测试**

追加：

```python
def test_not_passing_overrides_leaves_beats_byte_identical():
    """视频路径的安全边界：不传覆盖时输出必须与改动前逐字节一致。"""

    beats = [{"id": "shot-01-01", "visual_description": "原描述", "action": "起身"}]

    from novelvideo.narrative_groups.service import _apply_image_prompt_overrides

    assert _apply_image_prompt_overrides([dict(b) for b in beats], None) == beats


def test_passing_overrides_replaces_only_the_description():
    from novelvideo.narrative_groups.service import _apply_image_prompt_overrides

    beats = [
        {"id": "shot-01-01", "visual_description": "原描述", "action": "起身"},
        {"id": "shot-01-02", "visual_description": "另一格", "action": "递物"},
    ]

    applied = _apply_image_prompt_overrides(beats, {"shot-01-01": "改成铜甲"})

    assert applied[0]["visual_description"] == "改成铜甲"
    assert applied[0]["action"] == "起身"          # 其他字段不动
    assert applied[1] == beats[1]                  # 未命中的格位逐字不变
    assert beats[0]["visual_description"] == "原描述"  # 不改动入参
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q -k overrides`
预期：FAIL，`ImportError: cannot import name '_apply_image_prompt_overrides'`

- [ ] **步骤 3：编写最少实现代码**

`service.py` 新增辅助函数：

```python
def _apply_image_prompt_overrides(
    beats: list[dict[str, Any]],
    overrides: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    """只用出图路径传入 overrides；视频调用点不传，行为保持不变。"""

    if not overrides:
        return beats
    applied: list[dict[str, Any]] = []
    for beat in beats:
        override = str(overrides.get(str(beat.get("id") or "")) or "").strip()
        applied.append({**beat, "visual_description": override} if override else beat)
    return applied
```

`generation_beats_for_group` 增加**关键字参数**并在两条返回分支上施加：

```python
def generation_beats_for_group(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    legacy_beats: Iterable[Mapping[str, Any]],
    *,
    image_prompt_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
```

- 无活跃导演计划的分支：`return _apply_image_prompt_overrides([dict(beat) for beat in legacy_beats], image_prompt_overrides)`
- 镜头制分支：在函数返回处对最终 `beats` 列表施加同样处理

**绝对不要**改动其余 5 个调用点（`narrative_groups.py:2164`、`:2205`、`:2654`，`narrative_group_video.py:685`、`:2222`）。

`api/routes/narrative_groups.py:1865` 传入覆盖：

```python
        selected_beats = generation_beats_for_group(
            resolved.project_dir,
            episode,
            group_id,
            selected_beats,
            image_prompt_overrides=source_group.image_prompt_overrides,
        )
```

若 `source_group` 在此时尚未取到，使用该函数中已有的 group 变量（`_group_beats` 的第一个返回值）。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q`
预期：全部 PASS

**并证明视频调用点未被改动**：

运行：`git show --stat HEAD -- src/novelvideo/task_backend/runners/narrative_group_video.py`
预期：**空输出**（该文件不得出现在本任务的提交中）

再运行：`git show --stat HEAD -- src/novelvideo/api/routes/narrative_groups.py`
预期：只出现 `:1865` 那一处新增参数

同时跑视频相关回归：
`.venv/bin/python -m pytest tests/ -q -k "narrative_group_video or video_plan"`

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(narrative-groups): apply image prompt overrides on the grid path only" -- src/novelvideo/narrative_groups/service.py src/novelvideo/api/routes/narrative_groups.py tests/test_narrative_group_image_prompt_overrides.py
```

---

## 任务 4：API 端点

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 测试：`tests/test_narrative_group_image_prompt_overrides.py`

- [ ] **步骤 1：编写失败的测试**

先读同文件既有测试（如 `tests/` 下与 narrative groups 相关的 API 测试）确认 client 与鉴权 fixture 的真实写法，**不要臆造 fixture**。然后追加覆盖以下行为的测试：

- `GET …/narrative-groups/{group_id}/image-prompts` 返回每格的 `cell` / `shot_id` / `synthesized` / `override`
  - 有覆盖时 `override` 为该文本、`synthesized` 仍为拼装结果
  - 无覆盖时 `override` 为空串
- `PUT` 写入后，同 group 的 GET 能读回；`prompt` 为空串则清除
- 未知 `group_id` → 404
- 不属于该组的 `shot_id` → 422
- 超过 4000 字符 → 422

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q -k image_prompt`
预期：FAIL（404 或路由不存在）

- [ ] **步骤 3：编写最少实现代码**

新增两个端点，保持路由薄（校验 + 状态码映射，逻辑交给 `service.py`）：

```python
@router.get("/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/image-prompts")
async def get_group_image_prompts(project: str, episode: int, group_id: str, user=...):
    ...
    # synthesized 用 generation_beats_for_group(..., image_prompt_overrides=None) 计算
    # 按 cell_to_beat 的顺序输出 cells
```

```python
@router.put("/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/image-prompts")
async def put_group_image_prompt(project: str, episode: int, group_id: str, body: ..., user=...):
    try:
        group = update_image_prompt_override(resolved.project_dir, episode, group_id, body.shot_id, body.prompt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "data": {"image_prompt_overrides": group.image_prompt_overrides}}
```

请求体模型（`extra="forbid"`）：`shot_id: str`、`prompt: str`。

鉴权与项目解析请复用同文件既有端点的写法。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q`
预期：全部 PASS

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(api): expose per-shot image prompt overrides" -- src/novelvideo/api/routes/narrative_groups.py tests/test_narrative_group_image_prompt_overrides.py
```

---

## 任务 5：前端查询层

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`

- [ ] **步骤 1：编写失败的测试**

在任务 6 的对话框测试文件里先写一个类型层断言（该文件在任务 6 扩充）：

```tsx
import { expect, it } from "vitest";

import type { NarrativeGroupImagePromptCell } from "@/lib/queries/narrative-groups";

it("exposes an image prompt cell type", () => {
  const cell: NarrativeGroupImagePromptCell = {
    cell: 0,
    shot_id: "shot-01-01",
    synthesized: "拼装结果",
    override: "",
  };
  expect(cell.shot_id).toBe("shot-01-01");
});
```

（用 `import type`，这样该断言才会被 `tsc` 检查；若类型名写错或字段缺失，`tsc` 会报错。）

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && pnpm vitest run src/__tests__/components/episode/narrative-workbench/group-image-prompt-dialog.test.tsx`
预期：FAIL（类型不存在或模块导入失败）

- [ ] **步骤 3：编写最少实现代码**

在 `narrative-groups.ts` 新增：

```ts
export interface NarrativeGroupImagePromptCell {
  cell: number;
  shot_id: string;
  synthesized: string;
  override: string;
}

export function narrativeGroupImagePromptsPath(project: string, episode: number, groupId: string) {
  return `api/v1/projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/image-prompts`;
}

export function useNarrativeGroupImagePrompts(project: string, episode: number, groupId: string, enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.narrativeGroups(project, episode), groupId, "image-prompts"],
    queryFn: ({ signal }) =>
      api.get(narrativeGroupImagePromptsPath(project, episode, groupId), { signal })
        .json<OkResponse<{ cells: NarrativeGroupImagePromptCell[] }>>(),
    enabled,
  });
}

export function useUpdateNarrativeGroupImagePrompt(project: string, episode: number, groupId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ shotId, prompt }: { shotId: string; prompt: string }) =>
      api.put(narrativeGroupImagePromptsPath(project, episode, groupId), {
        json: { shot_id: shotId, prompt },
      }).json<OkResponse<{ image_prompt_overrides: Record<string, string> }>>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) });
    },
  });
}
```

路径前缀与 `OkResponse` 的导入请对齐该文件既有写法（同文件的 `narrativeGroupVideoPromptsPath` 是最好的参照）。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd frontend && pnpm tsc --noEmit -p tsconfig.app.json`
预期：exit 0

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(web): query and mutate per-shot image prompt overrides" -- frontend/src/lib/queries/narrative-groups.ts
```

---

## 任务 6：编辑对话框与格位入口

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/group-image-prompt-dialog.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-beat-inspector.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-pipeline.tsx`、`narrative-group-workbench.tsx`（透传 project/episode）
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/group-image-prompt-dialog.test.tsx`（创建）、`group-beat-inspector.test.tsx`（修改）

- [ ] **步骤 1：编写失败的测试**

对话框测试至少覆盖：

- 无覆盖时 `Textarea` 初值等于 `synthesized`
- 有覆盖时初值等于 `override`，且渲染「已自定义」标记
- 点「保存」调用 mutation，参数为 `{ shotId, prompt }`
- 无覆盖时「恢复默认」禁用；有覆盖时点击提交 `prompt: ""`
- GET 失败时显示错误态

`group-beat-inspector.test.tsx` 追加：每格存在「编辑出图提示词」按钮；点击回调收到对应 `shot_id`。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && pnpm vitest run src/__tests__/components/episode/narrative-workbench/`
预期：FAIL（组件不存在）

- [ ] **步骤 3：编写最少实现代码**

`group-image-prompt-dialog.tsx` 使用既有 `Dialog` / `Textarea` / `Button`（参照 `group-video-prompt-drawer.tsx` 的结构与错误态写法），要点：

- 初值：`useState(cell.override || cell.synthesized)`，并在 `cell` 变化时同步（`useEffect`）。
- 「保存」：`update.mutateAsync({ shotId: cell.shot_id, prompt: draft })` → `toast.success` → 关闭。
- 「恢复默认」：`disabled={!cell.override}`，点击提交 `prompt: ""`。
- 错误态：`role="alert"` 文案，与既有抽屉一致。

`group-beat-inspector.tsx` 每格增加按钮「编辑出图提示词」，点击时把该格的 `{cell, shot_id}` 交给上层打开对话框；`cell.override` 非空时渲染「已自定义」标记。新增 props 需向下透传 `project` / `episode`（对话框需要），若链路太长，可改为由上层持有对话框状态、inspector 只回调 `onEditImagePrompt(shotId)`。

**优先选择**：inspector 只回调 `onEditImagePrompt(cell)`，对话框由 `narrative-group-workbench.tsx` 持有并渲染——避免把 project/episode 一路透传进 inspector。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd frontend && pnpm vitest run src/__tests__/components/episode/narrative-workbench/
cd frontend && pnpm tsc --noEmit -p tsconfig.app.json
```

预期：测试全绿，`tsc` exit 0

- [ ] **步骤 5：Commit**

```bash
git commit -m "feat(web): edit per-shot image prompts from the group workbench" -- frontend/src/components/episode/narrative-workbench/ frontend/src/__tests__/components/episode/narrative-workbench/
```

---

## 任务 7：验收

- [ ] **步骤 1：后端相关测试**

```bash
.venv/bin/python -m pytest tests/test_narrative_group_image_prompt_overrides.py -q
.venv/bin/python -m pytest tests/test_api_narrative_groups.py tests/test_narrative_group_service.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：视频路径零回归（本计划的核心风险）**

```bash
git diff 9b7b066 --stat -- src/novelvideo/task_backend/runners/narrative_group_video.py
.venv/bin/python -m pytest tests/ -q -k "video"
```

预期：`narrative_group_video.py` **零改动**；视频测试无新增失败。

- [ ] **步骤 3：前端测试与类型检查**

```bash
cd frontend && pnpm vitest run src/__tests__/components/episode/narrative-workbench/
cd frontend && pnpm tsc --noEmit -p tsconfig.app.json
```

预期：全绿，`tsc` exit 0。

- [ ] **步骤 4：需求覆盖核对**

逐条对照规格 `docs/superpowers/specs/2026-09-25-narrative-group-image-prompt-override-design.md`：字段与持久化、投影保留、可选参数注入、仅出图路径、GET/PUT 端点、预填编辑框、恢复默认、已自定义标记、`grid_rules` 不可编辑、5 个视频调用点未改动。逐项指出实现位置与测试名，列出未覆盖项。

- [ ] **步骤 5：端到端确认（真实前端）**

重建 Web 产物并刷新页面，在一个真实的镜头制叙事组上：

1. 打开某格「编辑出图提示词」，确认**初值为拼装出的完整描述**（含 `Scene: …` 与摄影事实）。
2. 修改并保存，确认该格出现「已自定义」标记。
3. 点击「重新生成该组（实图）」，在生成参数或结果中确认**使用的是修改后的描述**。
4. 点「恢复默认」后再重出图，确认回到原描述。

若环境无法完成真实生成，明确报告「未执行」及原因，不要伪造。

- [ ] **步骤 6：保留工作区既有改动**

提交前运行 `git status --porcelain`，确认未把与本计划无关的既有修改一并提交（本仓库长期存在他人进行中的改动）。
