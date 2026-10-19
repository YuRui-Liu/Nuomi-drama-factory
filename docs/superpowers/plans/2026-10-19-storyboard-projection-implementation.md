# B 切片：分镜表投影与执行导演实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 导演计划成为唯一真源；分镜表是它的确定性投影；panel 规格由编译器产出拍摄语言。

**架构：** `DirectorPlanRevision` → `project_plan_rows` → `scriptNode` 行（10 列 + `director` 块）→ 单元格编辑走 `edit_shot`；宫格与 H3 读 `panel_spec`。

**技术栈：** Python 3.11、pydantic v2、pytest；前端 React + TypeScript（B 单独验证）。

**前置：** 波次 0（`PanelSpec` 契约）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `director_plan/projection.py` | 计划 → 表行，确定性 |
| `director_plan/panel_spec.py` | 实现 `compile_panel_spec` |
| `director_plan/models.py` | 组增加 `geo_layout` |
| `narrative_groups/image_prompt.py` | `panel_description` 改用 panel_spec |
| `freezone/orchestration/director_bridge.py` | `project_draft.description` 改用 panel_spec |
| `api/schemas.py` | 行增加 `director` 块 |
| `frontend/src/features/canvas/nodes/ScriptNode.tsx` | 显示 `director`、绑定 `directorBinding` |
| `frontend/src/features/canvas/domain/canvasNodes.ts` | 行类型与绑定字段 |

---

### 任务 1：GEO 与手法意图字段

**文件：**
- 修改：`src/novelvideo/director_plan/models.py`（`NarrativeGroupPlan`、`ShotPlan`）
- 测试：`tests/test_director_plan_projection.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan

def test_group_and_shot_have_new_fields():
    assert "geo_layout" in NarrativeGroupPlan.model_fields
    assert "technique_intent" in ShotPlan.model_fields
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_director_plan_projection.py -k fields -v`
预期：FAIL，字段不存在

- [ ] **步骤 3：编写最少实现**

`NarrativeGroupPlan` 加：

```python
    geo_layout: str = Field(default="", description="Scene-level spatial map; no characters or action.")
```

`ShotPlan` 加：

```python
    technique_intent: str = Field(default="", description="Retrieval anchor for technique/case matching.")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_director_plan_projection.py -k fields -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/director_plan/models.py tests/test_director_plan_projection.py
git commit -m "feat(director): add geo layout and technique intent fields"
```

---

### 任务 2：panel_spec 编译器

**文件：**
- 修改：`src/novelvideo/director_plan/panel_spec.py`
- 测试：`tests/test_panel_spec.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.director_plan.panel_spec import compile_panel_spec

class Shot:
    id = "S01"; shot_size = "近景"; camera_angle = "平视"; composition = "左三分"
    subject = "林渡"; action = "抬手"
    visible_start_state = "手停在近前"; visible_end_state = "手未接触"
    cinematography = type("C", (), {
        "axis": "A-B", "camera_side": "左侧", "screen_direction": "左→右",
        "subjects": [type("S", (), {"subject_id": "林渡", "screen_position": "画面左", "facing": "朝右", "gaze_target": "画外右后", "world_position": [0.7, 0, 0.3]})()],
        "lights": [type("L", (), {"light_id": "L1", "source_type": "主光", "direction": "右后侧逆光", "color_temperature": "冷", "relative_intensity": "低", "motivation": "天光"})()],
        "props": [], "key_light_id": "L1", "shadow_direction": "右后", "exposure_priority": "面部",
        "prompt_facts": lambda self: "",
    })()
class Group:
    geo_layout = "PLATFORM = 圆形石台；机位始终在东侧，不越轴。"

def test_sections_are_fixed_and_coordinate_free():
    spec = compile_panel_spec(Shot(), Group())
    assert list(spec.sections) == ["count", "shot", "axis", "blocking", "lighting", "performance", "props", "continuity"]
    assert "0.7" not in spec.prompt_text
    assert "画面左" in spec.prompt_text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_panel_spec.py -v`
预期：FAIL（当前抛 NotImplementedError）

- [ ] **步骤 3：编写实现**

```python
# src/novelvideo/director_plan/panel_spec.py（替换 compile_panel_spec）
def compile_panel_spec(shot, group) -> PanelSpec:
    cinematography = getattr(shot, "cinematography", None)
    subjects = list(getattr(cinematography, "subjects", []) or [])
    lights = list(getattr(cinematography, "lights", []) or [])
    count = f"画面内恰好 {len(subjects)} 人" if subjects else "画面内无人物"
    shot_text = "；".join(part for part in (
        getattr(shot, "shot_size", ""), getattr(shot, "camera_angle", ""), getattr(shot, "composition", ""),
    ) if part)
    axis_text = "；".join(part for part in (
        f"轴线 {cinematography.axis}" if getattr(cinematography, "axis", "") else "",
        f"机位在 {cinematography.camera_side}" if getattr(cinematography, "camera_side", "") else "",
        f"屏幕运动 {cinematography.screen_direction}" if getattr(cinematography, "screen_direction", "") else "",
        "屏幕方向只从机位描述",
    ) if part)
    blocking = "；".join(
        f"{subject.subject_id} 在{subject.screen_position}，{subject.facing}，视线{subject.gaze_target}"
        for subject in subjects
    ) or "无站位要求"
    lighting = "；".join(
        f"{light.source_type} {light.light_id} 来自{light.direction}，{light.color_temperature}，强度{light.relative_intensity}，动机{light.motivation}"
        for light in lights
    ) or "沿用场景光逻辑"
    performance = "；".join(part for part in (
        f"表演：{getattr(shot, 'action', '')}" if getattr(shot, "action", "") else "",
        f"起始：{getattr(shot, 'visible_start_state', '')}" if getattr(shot, "visible_start_state", "") else "",
        f"结束：{getattr(shot, 'visible_end_state', '')}" if getattr(shot, "visible_end_state", "") else "",
    ) if part)
    props = "无额外道具"
    continuity = "；".join(part for part in (
        f"场景空间图：{group.geo_layout}" if group.geo_layout else "",
        "动作只写正向，不写禁止",
    ) if part)
    sections = {
        "count": count, "shot": shot_text, "axis": axis_text, "blocking": blocking,
        "lighting": lighting, "performance": performance, "props": props, "continuity": continuity,
    }
    prompt_text = "\n".join(f"{name}: {value}" for name, value in sections.items() if value)
    return PanelSpec(sections=sections, prompt_text=prompt_text, negatives=[])
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_panel_spec.py -v`
预期：PASS，且断言无 `0.7`

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/director_plan/panel_spec.py tests/test_panel_spec.py
git commit -m "feat(director): compile panel spec in film language"
```

---

### 任务 3：计划 → 表行投影

**文件：**
- 创建：`src/novelvideo/director_plan/projection.py`
- 测试：`tests/test_director_plan_projection.py`

- [ ] **步骤 1：编写失败的测试**

```python
from types import SimpleNamespace
from novelvideo.director_plan.projection import project_plan_rows

def _revision():
    shot = SimpleNamespace(
        id="S01", shot_size="近景", camera_angle="平视", composition="左三分",
        subject="林渡", action="抬手", visible_start_state="手停在近前",
        visible_end_state="手未接触", duration_seconds=6.0, dialogue_source_ids=[],
        creative=None, technique_intent="克制的惊讶",
        cinematography=SimpleNamespace(
            axis="A-B", camera_side="左侧", screen_direction="左→右",
            subjects=[SimpleNamespace(subject_id="林渡", screen_position="画面左",
                                      facing="朝右", gaze_target="画外右后",
                                      world_position=[0.7, 0, 0.3])],
            lights=[SimpleNamespace(light_id="L1", source_type="主光", direction="右后侧逆光",
                                    color_temperature="冷", relative_intensity="低",
                                    motivation="天光")],
        ),
    )
    group = SimpleNamespace(id="G01", geo_layout="PLATFORM = 圆形石台；机位在东侧", shots=[shot])
    return SimpleNamespace(groups=[group])

def test_projection_is_deterministic():
    revision = _revision()
    first = project_plan_rows(revision)
    second = project_plan_rows(revision)
    assert first == second
    assert first[0]["group_id"] == "G01"
    assert first[0]["segment_index"] == 1
    assert "director" in first[0]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_director_plan_projection.py -k projection -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
# src/novelvideo/director_plan/projection.py
from __future__ import annotations
from novelvideo.director_plan.panel_spec import compile_panel_spec

def project_plan_rows(revision) -> list[dict]:
    rows: list[dict] = []
    for group in revision.groups:
        for index, shot in enumerate(group.shots, start=1):
            spec = compile_panel_spec(shot, group)
            cinematography = shot.cinematography
            creative = getattr(shot, "creative", None)
            performance = [
                {"id": item.subject_id, "expression": item.visible_behavior,
                 "action": item.start_behavior, "gaze": item.gaze, "intensity": ""}
                for item in (getattr(creative, "performance", None).characters
                             if creative and getattr(creative, "performance", None) else [])
            ]
            rows.append({
                "shot_no": len(rows) + 1,
                "group_id": group.id,
                "segment_index": index,
                "duration": int(round(shot.duration_seconds)),
                "visual_description": " → ".join(part for part in (
                    shot.visible_start_state, shot.action, shot.visible_end_state) if part),
                "shot": shot.shot_size,
                "camera_angle": shot.camera_angle,
                "axis": getattr(cinematography, "axis", ""),
                "key_light": getattr(cinematography, "key_light_id", ""),
                "performance": "；".join(item["expression"] for item in performance),
                "dialogue": " / ".join(shot.dialogue_source_ids),
                "sound": "",
                "director": {
                    "group_id": group.id,
                    "segment_index": index,
                    "technique_intent": getattr(shot, "technique_intent", ""),
                    "framing": {"shot_size": shot.shot_size, "angle": shot.camera_angle,
                                "composition": shot.composition},
                    "blocking": {
                        "axis": getattr(cinematography, "axis", ""),
                        "camera_side": getattr(cinematography, "camera_side", ""),
                        "screen_direction": getattr(cinematography, "screen_direction", ""),
                        "subjects": [
                            {"id": s.subject_id, "screen_position": s.screen_position,
                             "facing": s.facing, "gaze": s.gaze_target}
                            for s in getattr(cinematography, "subjects", [])
                        ],
                    },
                    "lights": [
                        {"id": l.light_id, "type": l.source_type, "direction": l.direction,
                         "temperature": l.color_temperature, "intensity": l.relative_intensity,
                         "motivation": l.motivation}
                        for l in getattr(cinematography, "lights", [])
                    ],
                    "performance": performance,
                    "props": [],
                    "continuity": {
                        "start_state": shot.visible_start_state,
                        "end_state": shot.visible_end_state,
                        "handoff": "",
                    },
                    "panel_spec": spec.model_dump(mode="json"),
                },
            })
    return rows
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_director_plan_projection.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/director_plan/projection.py tests/test_director_plan_projection.py
git commit -m "feat(director): project plan into storyboard rows"
```

---

### 任务 4：行模型增加 director 块

**文件：**
- 修改：`src/novelvideo/api/schemas.py`
- 测试：`tests/test_storyboard_table_binding.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.api.schemas import FreezoneStoryScriptRow

def test_row_accepts_director_block():
    row = FreezoneStoryScriptRow(shot_no=1, duration=6, visual_description="x",
                                 director={"group_id": "G01", "segment_index": 1})
    assert row.director["group_id"] == "G01"

def test_legacy_row_without_director():
    row = FreezoneStoryScriptRow(shot_no=1, duration=6, visual_description="x")
    assert row.director is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_storyboard_table_binding.py -k director -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

在 `FreezoneStoryScriptRow` 加：

```python
    director: dict | None = Field(default=None, description="计划投影：framing/blocking/lights/performance/props/continuity/panel_spec")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_storyboard_table_binding.py -k director -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/api/schemas.py tests/test_storyboard_table_binding.py
git commit -m "feat(storyboard): add director block to rows"
```

---

### 任务 5：宫格与 bridge 改用 panel_spec

**文件：**
- 修改：`src/novelvideo/narrative_groups/image_prompt.py`
- 修改：`src/novelvideo/freezone/orchestration/director_bridge.py`
- 测试：`tests/test_panel_description.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.narrative_groups.image_prompt import panel_description

def test_panel_description_has_no_world_coordinates():
    beat = {"visible_start_state": "手停在近前", "cinematography": {"subjects": [
        {"subject_id": "林渡", "screen_position": "画面左", "world_position": [0.7, 0, 0.3]}]}}
    text = panel_description(beat)
    assert "0.7" not in text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_panel_description.py -v`
预期：FAIL（当前 JSON dump 含 0.7）

- [ ] **步骤 3：编写实现**

在 `panel_description` 的分支里，把 `json.dumps(facts)` 替换为调用共享编译器生成的自然语言；最小改动：

```python
from novelvideo.director_plan.panel_spec import compile_panel_spec

def _spec_text(beat):
    shot = type("S", (), {})()
    for key in ("shot_size", "camera_angle", "composition", "action",
                "visible_start_state", "visible_end_state"):
        setattr(shot, key, beat.get(key, ""))
    cinematography = beat.get("cinematography")
    shot.cinematography = type("C", (), cinematography)() if isinstance(cinematography, dict) else None
    group = type("G", (), {"geo_layout": beat.get("geo_layout", "")})()
    return compile_panel_spec(shot, group).prompt_text
```

并在原 `json.dumps(facts)` 处改为 `_spec_text(beat)`；`director_bridge.project_draft` 的 `description` 拼接同样替换。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_panel_description.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/narrative_groups/image_prompt.py src/novelvideo/freezone/orchestration/director_bridge.py tests/test_panel_description.py
git commit -m "feat(storyboard): compile panel descriptions in film language"
```

---

### 任务 6：前端绑定与展示

**文件：**
- 修改：`frontend/src/features/canvas/domain/canvasNodes.ts`
- 修改：`frontend/src/features/canvas/nodes/ScriptNode.tsx`
- 测试：`frontend/src/__tests__/features/canvas/storyboard-director.test.tsx`

- [ ] **步骤 1：类型**

```typescript
export interface StoryboardDirectorBlock {
  group_id: string; segment_index: number; technique_intent?: string;
  framing?: Record<string, string>; blocking?: Record<string, unknown>;
  lights?: Array<Record<string, string>>; performance?: Array<Record<string, string>>;
  continuity?: Record<string, string>; panel_spec?: { prompt_text?: string };
}
```

并在行类型加 `director?: StoryboardDirectorBlock`，节点数据加 `directorBinding?: { episode: number; revisionId: string; projectionHash: string }`。

- [ ] **步骤 2：测试**

渲染一行，断言显示景别与主光；行无 `director` 时回退到字符串列（无崩溃）。

- [ ] **步骤 3：实现**

有 `directorBinding` 时，表格列从 `row.director` 取；无绑定时维持现有列。编辑提交到既有 `edit_shot` 调用（不新增操作）。

- [ ] **步骤 4：运行**

```bash
pnpm --dir frontend test -- storyboard-director
```
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/features/canvas/domain/canvasNodes.ts frontend/src/features/canvas/nodes/ScriptNode.tsx frontend/src/__tests__/features/canvas/storyboard-director.test.tsx
git commit -m "feat(canvas): render director block in storyboard table"
```

---

## 自检

- 规格覆盖：GEO、panel_spec、投影、director 块、宫格替换、前端展示均有任务；编辑回写复用现有操作。
- 占位符：无 TODO；`compile_panel_spec` 给出完整实现，坐标只用于推导文本。
- 类型一致性：`director` 块字段与 A 计划、C 计划的 `PanelSpec` / `SegmentDraft` 命名一致。
