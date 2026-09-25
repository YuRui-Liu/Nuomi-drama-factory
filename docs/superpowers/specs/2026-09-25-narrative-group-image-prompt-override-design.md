# 叙事组出图提示词可编辑 设计

## 背景

叙事组工作台（镜头制生产计划）里，重出图（「重新生成该组（实图）」）使用的是后端**确定性拼装**的提示词。用户无法修改它，因此重出图只是用同一份提示词再跑一次，无法表达「这格的服装不对」「某格构图要改」这类意图。

用户需求原话：「这里的重出图要允许改提示词，不然只是一味的重复旧图提示词」。

## 目标

- 允许用户**逐条编辑每个格位（镜头）的画面描述**，并在重出图时生效。
- 编辑结果**持久化**：后续每次出图都使用该描述，不必反复输入。
- 编辑**只影响出图**，不影响视频生成、连续性契约或其他下游。

## 非目标

- 不编辑提示词中的布局硬约束（`grid_rules`），它继续由后端强制。
- 不修改导演计划的镜头 `subject` / `action`。
- 不做覆盖内容的历史版本或审计。
- 不做批量编辑（把同一修改应用到同组多格）。
- 不自动清理「孤儿覆盖」（镜头 id 已不存在时的残留）。
- 不改变视频路径的任何行为。

## 已确认的现状（代码事实）

### 出图提示词的拼装

`src/novelvideo/task_backend/runners/narrative_group.py:281-332` 的 `_grid_prompt` 按顺序拼接四段：

```
image_projection  +  style_prompt  +  grid_rules  +  panels
```

其中 `panels` 来自 `payload["beats"]`，每格的描述取：

```python
beat.get("visual_description") or beat.get("shot_description") or beat.get("action")
or beat.get("content") or beat.get("description") or beat.get("title") or "continue the scene"
```

`grid_rules`（`narrative_group.py:305-326`）包含行列数、每格宽高比、禁止边框/文字、以及 `strong_sketch` 的构图锁定——它是多宫格能被正确切分的保证。

### 面板描述是拼出来的，不是存储字段

`src/novelvideo/narrative_groups/service.py:748-756` 在镜头制分支里合成 beat：

```python
beats.append({
    "id": shot.id,
    "beat_id": shot.id,
    "source_span_ids": list(shot.source_span_ids),
    "visual_description": " ".join(
        part for part in (shot.subject, shot.action,
                          f"Scene: {group.scene_anchor}; time: {group.time_anchor}.",
                          shot.cinematography.prompt_facts() if shot.cinematography else "") if part
    ),
    ...
```

即每格描述 = **镜头 subject + action + 场景/时间锚点 + 摄影事实**的组合。**不存在单一可编辑字段。**

### `generation_beats_for_group` 被出图与视频共用

`service.py:684` 的 `generation_beats_for_group(project_dir, episode, group_id, legacy_beats)` 有 6 个生产调用点：

| 调用点 | 用途 |
|---|---|
| `api/routes/narrative_groups.py:1865`（`_enqueue_group_action`） | **出图（唯一目标路径）** |
| `api/routes/narrative_groups.py:2164` | 视频参考 |
| `api/routes/narrative_groups.py:2205` | 视频帧 |
| `api/routes/narrative_groups.py:2654` | 视频计划 |
| `task_backend/runners/narrative_group_video.py:685` | 视频 |
| `task_backend/runners/narrative_group_video.py:2222` | 视频 |

**这是本设计最重要的约束**：把覆盖逻辑直接写进这个共享函数会让覆盖泄漏到视频提示词，违反「仅作用于出图」。因此覆盖必须经**可选参数**注入，只有出图调用点传值。

分支行为（`service.py:691-693`）：无活跃导演计划时返回旧节拍；有则从 `group.shots` 合成。两条分支都应支持覆盖。

### 叙事组是可变 sidecar，投影同步时保留可变字段

`NarrativeGroup` 是 **`@dataclass(frozen=True)`（`models.py:245-246`）**，因此**不能就地赋值**：变更必须走 `dataclasses.replace(group, ...)`，并用 `save_groups(...)` 落盘。群组级更新的既有范式正是如此——`service.py` 的 `update_video_settings`（`:828` → `save_groups` 于 `:867`）、`update_video_reference_settings`（`:875` → `:1036`）、`update_video_plan`（`:1044` → `:1130`）。本次新增的覆盖写入函数必须沿用同一范式。

`service.py:459-475` 的 `_materialize_active_groups` 从导演计划重建 group 时，会从 `previous` 继承这些可变字段：

```python
previous = previous_by_id.get(group.id)
base = NarrativeGroup(
    ...
    video_settings=(previous.video_settings if previous else VideoSettings()),
    video_reference_settings=(previous.video_reference_settings if previous else ...),
```

`_same_projection_structure`（`service.py:412-424`）以 `director_revision_id / beat_ids / source_span_ids / shot_ids / layout / cell_to_beat` 判定结构是否变化。

序列化：`models.py:58-61` 的 `to_dict` 用 `asdict(self)`；反序列化在 `service.py:325-347`，全部用 `data.get(...)` 加默认值。

## 关键决策

### 决策 1：编辑粒度是「逐条镜头描述」

用户明确选择了逐条（而非组级附加指令或完整覆盖）。逐条最贴合真实诉求（某一格不对就改那一格），且不会让用户接触到 `grid_rules`。

### 决策 2：持久化覆盖，且只作用于出图

新增独立覆盖字段，而非改写镜头 `subject` / `action`。理由：`subject` / `action` 被视频提示词与连续性契约使用，改写会波及下游；覆盖字段的作用面可精确限定在出图。

### 决策 3：覆盖存放在叙事组 sidecar

而非导演计划或独立存储。理由：

- 导演计划是带版本的存储（`revision_id` / `status` / 激活流程），编辑它需要产生新 revision 并牵连审核链路，而本需求只要影响出图。
- 独立存储多一个存储面，且与 sidecar 可变状态的既有模式不一致。
- sidecar 的可变字段本来就在投影同步时保留，新增字段沿用同一机制即可。

### 决策 4：覆盖替换整段 `visual_description`，但编辑框预填拼装结果

`visual_description` 里含 `Scene: …; time: …` 与摄影事实。如果给用户一个空编辑框，会**静默丢掉**这些信息。因此编辑框预填当前拼装出的完整文本，用户在其上修改；并提供「恢复默认」清除覆盖。

### 决策 5：`grid_rules` 不进入编辑范围

它保证多宫格被正确切分。允许用户覆盖整条提示词会导致切分错乱，因此 `grid_rules` 始终由后端强制。

## 数据模型与持久化

文件：`src/novelvideo/narrative_groups/models.py`

```python
@dataclass
class NarrativeGroup:
    id: str
    ordinal: int
    beat_ids: tuple[str, ...]
    layout: GridLayout
    cell_to_beat: tuple[CellMapping, ...]
    # ... 既有字段 ...
    image_prompt_overrides: dict[str, str] = field(default_factory=dict)
```

键为 `shot_id`（镜头制）或 beat 的 id（旧节拍制），值为覆盖文本。

序列化：`to_dict` 的 `asdict(self)` 自动包含该字段，无需改动。

反序列化（`service.py:325-347` 的 `NarrativeGroup(...)` 构造处）新增：

```python
image_prompt_overrides={
    str(key): str(value)
    for key, value in (data.get("image_prompt_overrides") or {}).items()
    if str(value).strip()
},
```

旧 sidecar 不含该键 → 得到 `{}`，向后兼容。

投影保留（`service.py:459-475` 的 `base = NarrativeGroup(...)`）新增：

```python
image_prompt_overrides=(
    dict(previous.image_prompt_overrides) if previous else {}
),
```

与 `video_settings` 同款。若导演计划重新生成导致 `shot_id` 变化，旧覆盖会成为孤儿键——不报错、不自动清理（见「非目标」）。

### 覆盖写入的落盘路径

在 `service.py` 新增函数，与 `update_video_settings` / `update_video_plan` 同构：

```python
def update_image_prompt_override(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    shot_id: str,
    prompt: str,
) -> NarrativeGroup:
    """设置或清除单个格位的出图提示词覆盖；返回更新后的 group。"""

    cleaned = str(prompt or "").strip()
    with _sidecar_guard(project_dir, episode):
        groups = load_groups(project_dir, episode)
        target = next((item for item in groups if item.id == group_id), None)
        if target is None:
            raise KeyError(group_id)
        valid_ids = {mapping.beat_id for mapping in target.cell_to_beat} | set(target.beat_ids)
        if shot_id not in valid_ids:
            raise ValueError(f"shot_id is not part of group {group_id}: {shot_id}")
        if len(cleaned) > _IMAGE_PROMPT_OVERRIDE_LIMIT:
            raise ValueError("image prompt override is too long")
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

`_IMAGE_PROMPT_OVERRIDE_LIMIT = 4000`。API 路由只做参数校验与状态码映射，其余交给该函数。

## 提示词注入

文件：`src/novelvideo/narrative_groups/service.py`

`generation_beats_for_group` 新增**可选**参数：

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

在镜头制分支合成 beat 之后、以及旧节拍制分支返回之前，统一施加替换：

```python
def _apply_image_prompt_overrides(
    beats: list[dict[str, Any]],
    overrides: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    """只在出图路径传入 overrides；视频调用点不传，行为保持不变。"""

    if not overrides:
        return beats
    applied = []
    for beat in beats:
        override = str(overrides.get(str(beat.get("id") or "")) or "").strip()
        if override:
            beat = {**beat, "visual_description": override}
        applied.append(beat)
    return applied
```

`subject` / `action` / `visible_start_state` 等字段保持原样，只有 `visual_description` 被替换。

仅出图调用点传值（`api/routes/narrative_groups.py:1865`）：

```python
selected_beats = generation_beats_for_group(
    resolved.project_dir,
    episode,
    group_id,
    selected_beats,
    image_prompt_overrides=source_group.image_prompt_overrides,
)
```

其余 5 个调用点**不得改动**。

## API 契约

文件：`src/novelvideo/api/routes/narrative_groups.py`

### GET `/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/image-prompts`

返回每个格位的拼装结果与覆盖，供前端预填：

```json
{
  "ok": true,
  "data": {
    "cells": [
      {"cell": 0, "shot_id": "shot-07-01", "synthesized": "……", "override": ""}
    ]
  }
}
```

`synthesized` 由服务端用 `generation_beats_for_group(..., image_prompt_overrides=None)` 计算，取该 `shot_id` 对应的 `visual_description`。

### PUT `/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/image-prompts`

请求体：

```json
{"shot_id": "shot-07-01", "prompt": "……"}
```

- `prompt` 去首尾空白后为空串 → **清除**该 `shot_id` 的覆盖
- 非空 → 写入覆盖

响应返回更新后的完整映射：

```json
{"ok": true, "data": {"image_prompt_overrides": {"shot-07-01": "……"}}}
```

错误处理：

| 情况 | 状态码 |
|---|---|
| group 不存在 | 404 |
| `shot_id` 不属于该组 | 422 |
| `prompt` 超过 4000 个字符（按 `len(str)` 计，非字节数） | 422 |

「属于该组」的判定：`shot_id` 必须出现在该组的 `cell_to_beat` 的 `beat_id` 集合中，**或**出现在该组的 `beat_ids` 中（后者覆盖旧节拍制分支）。两个集合取并集。

## 前端交互

文件：`frontend/src/components/episode/narrative-workbench/group-beat-inspector.tsx` 及新增对话框组件。

- 每个格位在既有按钮旁增加「编辑出图提示词」入口。
- 对话框内容：
  - `Textarea`，初值为 `override || synthesized`（来自 GET）。
  - 「恢复默认」按钮：提交空串以清除覆盖；无覆盖时禁用。
  - 「保存」：`PUT` 后 `toast` 提示成功，并 `invalidateQueries` 失效 `narrativeGroups`。
- 该格存在覆盖时，在格位标题处显示一个「已自定义」标记，避免用户忘记自己改过。
- GET 失败时对话框显示错误态且不阻塞其他功能。

查询与变更放 `frontend/src/lib/queries/narrative-groups.ts`，遵循该文件既有 hook 风格。

## 测试

后端：

- **视频路径逐字不变**：不传 `image_prompt_overrides` 时，`generation_beats_for_group` 的输出与改动前逐字节一致（这是本设计最关键的安全回归）。
- 传覆盖时，命中 `shot_id` 的 beat 的 `visual_description` 等于覆盖文本；未命中的 beat 不变；`subject` / `action` 等字段不变。
- 旧节拍制分支（无活跃导演计划）也支持覆盖。
- 覆盖随投影同步保留：`_materialize_active_groups` 在结构不变时保留 `image_prompt_overrides`。
- 旧 sidecar（无该字段）可正常加载并得到 `{}`。
- API：GET 返回 `synthesized` 与 `override`；PUT 设置与清除；未知 `shot_id` → 422；未知 group → 404；超长 → 422。

前端：

- 无覆盖时对话框预填 `synthesized`；有覆盖时预填 `override` 并显示「已自定义」标记。
- 保存调用 PUT 且提交正确 `shot_id` / `prompt`。
- 「恢复默认」在无覆盖时禁用；有覆盖时提交空串。

## 边界与不做的事

- `grid_rules` 不可编辑，始终由后端强制。
- 不改 `subject` / `action`，视频、连续性契约与其他下游零影响。
- 不做覆盖的历史版本或审计。
- 不自动清理孤儿覆盖。
- 不做批量编辑。
- 视频的 5 个 `generation_beats_for_group` 调用点不做改动。

## 风险

1. **覆盖泄漏到视频路径**：这是最主要的风险。缓解手段是把替换限定在可选参数之后，并用「不传参数时输出逐字节不变」的测试锁死。
2. **孤儿覆盖**：导演计划重新生成后 `shot_id` 变化会让旧覆盖失效。当前选择不清理、不报错，用户可在新格位上重新设置。
3. **用户删掉场景/时间信息**：`visual_description` 含场景与时间锚点，用户可能整段改写导致连续性漂移。缓解手段是编辑框预填完整拼装结果（用户能看到并保留），以及提示词中 `grid_rules` 仍强制要求连续性。此为可接受的产品取舍。
