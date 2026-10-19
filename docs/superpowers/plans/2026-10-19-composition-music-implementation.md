# D 切片：合成与智能配乐实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 视频批次拼回时间线，按组选择音频来源，配音对齐，可选智能配乐与混音，合成前校验。

**架构：** 批次 → 时间线 → `GroupAudioPlan` → 对齐链 → MusicPlan（可选）→ 合成 → 校验。

**技术栈：** Python 3.11、pydantic v2、ffmpeg（既有 `music/mix`）、pytest。

**前置：** C 的批次与配音输出；波次 0 的 `AudioMode` / `GroupAudioPlan`。

---

### 任务 1：音频模式互斥校验

**文件：**
- 修改：`src/novelvideo/music/alignment.py`
- 测试：`tests/test_episode_audio_mode.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.music.alignment import AudioMode, GroupAudioPlan, validate_audio_plan

def test_hybrid_requires_line_ids():
    plan = GroupAudioPlan(group_id="G01", audio_mode=AudioMode.HYBRID)
    issues = validate_audio_plan(plan)
    assert issues[0].code == "audio_mode_conflict"

def test_original_blocks_dub():
    plan = GroupAudioPlan(group_id="G01", audio_mode=AudioMode.ORIGINAL, replaced_line_ids=["L1"])
    issues = validate_audio_plan(plan)
    assert any(issue.code == "audio_mode_conflict" for issue in issues)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_episode_audio_mode.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
from pydantic import BaseModel

class AudioIssue(BaseModel):
    code: str
    message: str

def validate_audio_plan(plan: GroupAudioPlan) -> list[AudioIssue]:
    issues: list[AudioIssue] = []
    if plan.audio_mode is AudioMode.HYBRID and not plan.replaced_line_ids:
        issues.append(AudioIssue(code="audio_mode_conflict", message="hybrid requires replaced_line_ids"))
    if plan.audio_mode is AudioMode.ORIGINAL and plan.replaced_line_ids:
        issues.append(AudioIssue(code="audio_mode_conflict", message="original mode cannot replace lines"))
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_episode_audio_mode.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/music/alignment.py tests/test_episode_audio_mode.py
git commit -m "feat(audio): validate per-group audio mode"
```

---

### 任务 2：对齐链 X3 → X2 → X4

**文件：**
- 创建：`src/novelvideo/audio/dubbing_alignment.py`
- 测试：`tests/test_dubbing_alignment_chain.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.audio.dubbing_alignment import align_dialogue

def test_pause_trim_first():
    result = align_dialogue(audio_seconds=6.4, segment_seconds=6.0, pause_budget=0.5)
    assert result.strategy == "trim_pauses"

def test_speed_within_five_percent():
    result = align_dialogue(audio_seconds=6.2, segment_seconds=6.0, pause_budget=0.1)
    assert result.strategy == "speed"
    assert 0.95 <= result.speed <= 1.05

def test_beyond_limit_blocks():
    result = align_dialogue(audio_seconds=7.0, segment_seconds=6.0, pause_budget=0.1)
    assert result.blocked
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_dubbing_alignment_chain.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class AlignmentResult:
    strategy: str
    speed: float = 1.0
    blocked: bool = False

def align_dialogue(*, audio_seconds: float, segment_seconds: float, pause_budget: float) -> AlignmentResult:
    overrun = audio_seconds - segment_seconds
    if overrun <= 0:
        return AlignmentResult(strategy="none")
    if overrun <= pause_budget:
        return AlignmentResult(strategy="trim_pauses")
    speed = audio_seconds / segment_seconds
    if speed <= 1.05:
        return AlignmentResult(strategy="speed", speed=round(speed, 3))
    return AlignmentResult(strategy="blocked", speed=1.0, blocked=True)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_dubbing_alignment_chain.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/audio/dubbing_alignment.py tests/test_dubbing_alignment_chain.py
git commit -m "feat(audio): align dubbing with pause-trim then speed"
```

---

### 任务 3：智能配乐（匹配 → 生成回退）

**文件：**
- 创建：`src/novelvideo/music/smart_scoring.py`
- 测试：`tests/test_smart_music.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.music.smart_scoring import plan_group_music

def test_uses_match_when_available(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda **kw: [{"id": "m1", "duration_ms": 20000}])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000, enabled=True)
    assert plan["source"] == "library"

def test_disabled_returns_none():
    assert plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000, enabled=False) is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_smart_music.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
from novelvideo.music.matching import match_assets

def plan_group_music(*, group: dict, mood: str, duration_ms: int, enabled: bool) -> dict | None:
    if not enabled:
        return None
    matches = match_assets([], query=mood, duration_ms=duration_ms, instrumental_only=True)
    if matches:
        return {"group_id": group["id"], "source": "library", "asset": matches[0], "duck": True}
    return {"group_id": group["id"], "source": "generate", "prompt": mood,
            "duration_ms": duration_ms, "duck": True}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_smart_music.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/music/smart_scoring.py tests/test_smart_music.py
git commit -m "feat(music): match then generate for optional scoring"
```

---

### 任务 4：合成前校验

**文件：**
- 创建：`src/novelvideo/music/composition_checks.py`
- 测试：`tests/test_composition_checks.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.music.composition_checks import check_timeline

def test_missing_segment_blocks():
    issues = check_timeline(segments=[{"id": "S1", "video": "a.mp4"}, {"id": "S2", "video": None}],
                            tracks=["dialogue", "music"])
    assert issues[0].code == "timeline_gap"

def test_duplicate_track_blocks():
    issues = check_timeline(segments=[{"id": "S1", "video": "a.mp4"}],
                            tracks=["dialogue", "dialogue"])
    assert issues[0].code == "duplicate_audio_track"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_composition_checks.py -v`
预期：FAIL

- [ ] **步骤 3：编写实现**

```python
from pydantic import BaseModel

class CompositionIssue(BaseModel):
    code: str
    message: str

def check_timeline(*, segments: list[dict], tracks: list[str]) -> list[CompositionIssue]:
    issues: list[CompositionIssue] = []
    for segment in segments:
        if not segment.get("video"):
            issues.append(CompositionIssue(code="timeline_gap", message=f"missing video: {segment.get('id')}"))
    if len(tracks) != len(set(tracks)):
        issues.append(CompositionIssue(code="duplicate_audio_track", message="audio track passed twice"))
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_composition_checks.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/music/composition_checks.py tests/test_composition_checks.py
git commit -m "feat(compose): check timeline before composition"
```

---

## 自检

- 规格覆盖：音频模式、对齐链、配乐、合成校验均有任务；混音调用沿用 `music/mix`。
- 占位符：无 TODO。
- 类型一致性：`AudioMode` / `GroupAudioPlan` 来自波次 0；C 的段输出字段名一致。
