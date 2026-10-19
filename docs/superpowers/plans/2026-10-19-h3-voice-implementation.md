# C 切片：H3 导演与配音师实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 从分镜行拆段、判定帧模式、逐段检索手法、写 H3 提示词并分批并行提交；配音到“音色 + 对白 + 对齐”。

**架构：** 行 → `split_segments` → 草案 → 经验检索绑定 → 优化器写提示词 → 交叉校验 → preview/prepare → ≤15s 分批提交。

**技术栈：** Python 3.11、pydantic v2、pytest。

**前置：** B 的 `panel_spec` 与 `director` 块；波次 0 的 `SegmentDraft` / `VoiceDesignBrief`。

---

### 任务 1：拆段

**文件：**
- 修改：`src/novelvideo/freezone/video_director/segment_planner.py`
- 测试：`tests/test_segment_planner.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.freezone.video_director.segment_planner import split_segments

def _rows(*durations):
    return [{"shot_no": index + 1, "duration": duration, "cell_image": f"cell-{index + 1}.png",
             "director": {"panel_spec": {}, "technique_intent": ""}}
            for index, duration in enumerate(durations)]

def test_one_cell_one_segment():
    plan = split_segments(_rows(6, 7), group={"id": "G01"})
    assert [segment.segment_id for segment in plan.segments] == ["G01-S01", "G01-S02"]

def test_merge_adjacent_when_short():
    plan = split_segments(_rows(2, 3), group={"id": "G01"}, merge_adjacent=True)
    assert len(plan.segments) == 1
    assert plan.segments[0].source_rows == [1, 2]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_segment_planner.py -v`
预期：FAIL（NotImplementedError）

- [ ] **步骤 3：编写实现**

```python
def split_segments(rows, group, *, merge_adjacent: bool = True, limit_seconds: float = 15.0):
    from .segment_planner import SegmentPlan, SegmentDraft
    segments = []
    buffer = []
    def flush():
        if not buffer:
            return
        first, last = buffer[0], buffer[-1]
        segment_id = f"{group['id']}-S{len(segments) + 1:02d}"
        segments.append(SegmentDraft(
            segment_id=segment_id,
            source_rows=[row["shot_no"] for row in buffer],
            duration_seconds=float(sum(row["duration"] for row in buffer)),
            frame_mode="first",
            first_frame=first.get("cell_image"),
            last_frame=last.get("cell_image") if len(buffer) > 1 else None,
            panel_spec=buffer[0].get("director", {}).get("panel_spec", {}),
            technique_intent=buffer[0].get("director", {}).get("technique_intent", ""),
        ))
        buffer.clear()
    for row in rows:
        candidate = sum(item["duration"] for item in buffer) + row["duration"]
        if buffer and (not merge_adjacent or candidate > limit_seconds):
            flush()
        buffer.append(row)
    flush()
    return SegmentPlan(group_id=group["id"], segments=segments)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_segment_planner.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/video_director/segment_planner.py tests/test_segment_planner.py
git commit -m "feat(h3): split storyboard rows into segments"
```

---

### 任务 2：帧模式兜底

**文件：**
- 修改：`src/novelvideo/freezone/video_director/segment_planner.py`
- 测试：`tests/test_frame_mode_fallback.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.freezone.video_director.segment_planner import apply_frame_mode_guard

def test_missing_last_frame_downgrades():
    segment = {"frame_mode": "first_last", "first_frame": "a.png", "last_frame": None}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert issues[0]["code"] == "frame_mode_fallback"

def test_never_upgrades():
    segment = {"frame_mode": "first", "first_frame": "a.png", "last_frame": "b.png"}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert issues == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_frame_mode_fallback.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
def apply_frame_mode_guard(segment: dict) -> tuple[dict, list[dict]]:
    issues: list[dict] = []
    guarded = dict(segment)
    if guarded.get("frame_mode") == "first_last":
        first, last = guarded.get("first_frame"), guarded.get("last_frame")
        if not first or not last or first == last:
            guarded["frame_mode"] = "first"
            guarded["last_frame"] = None
            issues.append({"code": "frame_mode_fallback", "severity": "warning",
                           "message": "last frame unavailable or identical; downgraded to first-frame mode"})
    return guarded, issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_frame_mode_fallback.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/video_director/segment_planner.py tests/test_frame_mode_fallback.py
git commit -m "feat(h3): guard frame mode with deterministic fallback"
```

---

### 任务 3：手法/案例绑定

**文件：**
- 创建：`src/novelvideo/technique_library/binding.py`
- 测试：`tests/test_technique_binding.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.technique_library.binding import bind_experience

def test_only_compatible_ids(monkeypatch):
    monkeypatch.setattr("novelvideo.technique_library.binding.search_experience",
                        lambda query, mode, duration_seconds, limit=5: {
                            "results": [{"case_id": "case-1", "source_id": "src-1",
                                         "provenance": "author", "compatible_technique_ids": ["t-1"]}],
                            "techniques": [{"id": "t-1", "version": 2}, {"id": "t-2", "version": 1}]})
    ref, case = bind_experience(intent="克制的惊讶", mode="ref_plus_first", duration=6, user_technique=None)
    assert ref == {"id": "t-1", "version": 2}
    assert case["case_id"] == "case-1"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_technique_binding.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
# src/novelvideo/technique_library/binding.py
from __future__ import annotations
from novelvideo.technique_library.experience import search_experience

def bind_experience(*, intent: str, mode: str, duration: float, user_technique: dict | None):
    found = search_experience(intent, mode, duration, 5)
    cases = found.get("results", [])
    if not cases:
        if user_technique:
            raise ValueError("technique_conflict: requested technique has no compatible case")
        return None, None
    first = cases[0]
    compatible = set(first.get("compatible_technique_ids", []))
    if user_technique:
        if user_technique.get("id") not in compatible:
            raise ValueError("technique_conflict: requested technique is not compatible")
        return user_technique, {"case_id": first["case_id"], "source_id": first.get("source_id", ""),
                                "provenance": first.get("provenance", "")}
    technique = next((item for item in found.get("techniques", []) if item.get("id") in compatible), None)
    case = {"case_id": first["case_id"], "source_id": first.get("source_id", ""),
            "provenance": first.get("provenance", "")}
    return technique, case
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_technique_binding.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/technique_library/binding.py tests/test_technique_binding.py
git commit -m "feat(h3): bind compatible techniques and cases"
```

---

### 任务 4：提示词交叉校验

**文件：**
- 创建：`src/novelvideo/freezone/video_director/crosscheck.py`
- 测试：`tests/test_prompt_crosscheck.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.freezone.video_director.crosscheck import crosscheck_prompt

def test_dialogue_must_be_verbatim():
    issues = crosscheck_prompt(prompt="他说：你好。", spec={"dialogue": ["老陈……是我！"]}, duration=6)
    assert issues[0]["code"] == "prompt_spec_conflict"

def test_camera_side_contradiction():
    issues = crosscheck_prompt(prompt="从右侧拍摄", spec={"camera_side": "左侧"}, duration=6)
    assert any(issue["code"] == "prompt_spec_conflict" for issue in issues)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_prompt_crosscheck.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
from __future__ import annotations

_SIDE_PAIRS = (("左侧", "右侧"), ("left", "right"))

def crosscheck_prompt(*, prompt: str, spec: dict, duration: float) -> list[dict]:
    issues: list[dict] = []
    for line in spec.get("dialogue", []):
        if line and line not in prompt:
            issues.append({"code": "prompt_spec_conflict", "severity": "error",
                           "message": f"dialogue missing verbatim: {line}"})
    side = str(spec.get("camera_side", ""))
    for left, right in _SIDE_PAIRS:
        if left and left in side and right in prompt and left not in prompt:
            issues.append({"code": "prompt_spec_conflict", "severity": "error",
                           "message": "camera side contradicts panel spec"})
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_prompt_crosscheck.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/video_director/crosscheck.py tests/test_prompt_crosscheck.py
git commit -m "feat(h3): crosscheck prompt against panel spec"
```

---

### 任务 5：≤15s 分批

**文件：**
- 创建：`src/novelvideo/freezone/video_director/batching.py`
- 测试：`tests/test_h3_batching.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.freezone.video_director.batching import batch_segments

def test_batches_under_limit():
    segments = [{"segment_id": f"S{i}", "duration_seconds": 6} for i in range(4)]
    batches = batch_segments(segments, limit_seconds=15)
    assert [sum(s["duration_seconds"] for s in batch) for batch in batches] == [12, 12]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_h3_batching.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
def batch_segments(segments: list[dict], *, limit_seconds: float = 15.0) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    total = 0.0
    for segment in segments:
        duration = float(segment["duration_seconds"])
        if current and total + duration > limit_seconds:
            batches.append(current)
            current, total = [], 0.0
        current.append(segment)
        total += duration
    if current:
        batches.append(current)
    for batch in batches:
        if sum(float(item["duration_seconds"]) for item in batch) > limit_seconds:
            raise ValueError("batch_over_limit")
    return batches
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_h3_batching.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/video_director/batching.py tests/test_h3_batching.py
git commit -m "feat(h3): batch segments under attempt limit"
```

---

### 任务 6：音色 brief 与克隆分层

**文件：**
- 修改：`src/novelvideo/audio/voice_design.py`
- 测试：`tests/test_voice_design_brief.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.audio.voice_design import build_voice_design_brief, clone_performance

def test_brief_prompt_contains_required_fields():
    brief = build_voice_design_brief(
        character_id="lin-du", gender="男性", age=40, register="中低音域",
        texture="干涩", pace="偏慢", accent="北方口音", baseline="紧张但克制",
    )
    assert "40岁" in brief.instruct_zh
    assert "中低音域" in brief.instruct_zh

def test_clone_performance_has_no_emotion_weight():
    params = clone_performance(speaking_rate=1.05, pause_seconds=0.1)
    assert params["speakingRate"] == 1.05
    assert "emotion" not in params
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_voice_design_brief.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
def build_voice_design_brief(*, character_id: str, gender: str, age: int, register: str,
                             texture: str = "", resonance: str = "", articulation: str = "",
                             pace: str = "", accent: str = "", baseline: str = "",
                             state: str = "", negatives: list[str] | None = None) -> VoiceDesignBrief:
    parts = [gender, f"{age}岁", register, texture, resonance, articulation, pace, accent]
    zh = "，".join(part for part in parts if part)
    tail = []
    if baseline:
        tail.append(f"情绪基线：{baseline}")
    if state:
        tail.append(f"本剧状态：{state}")
    if negatives:
        tail.append("避免：" + "、".join(negatives))
    instruct = zh + ("；" + "；".join(tail) if tail else "")
    return VoiceDesignBrief(character_id=character_id, instruct_zh=instruct,
                            negatives=list(negatives or []))

def clone_performance(*, speaking_rate: float, pause_seconds: float) -> dict:
    if not 0.9 <= speaking_rate <= 1.1:
        raise ValueError("speaking rate must stay within 5%")
    return {"speakingRate": speaking_rate, "maxInternalPauseDuration": pause_seconds}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_voice_design_brief.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/audio/voice_design.py tests/test_voice_design_brief.py
git commit -m "feat(voice): build qwen3 brief and clone performance split"
```

---

## 自检

- 规格覆盖：拆段、帧模式、检索、交叉校验、分批、音色/克隆均有任务；搜索调用以现有 `experience.search` 为准，签名冲突时以实际代码为准。
- 占位符：无 TODO。
- 类型一致性：`SegmentDraft` 复用波次 0；`panel_spec` 来自 B。
