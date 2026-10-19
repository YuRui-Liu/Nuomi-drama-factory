# 波次 0：接口冻结实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 冻结四份规格共用的字段名、类型、CLI 挂载点，产出可导入的空实现骨架，避免并行 agent 各自发明契约。

**架构：** 只新增类型与函数签名，不含业务逻辑；四个切片各自在自己的模块里实现。所有模型用 pydantic v2（仓库既有约定）。

**技术栈：** Python 3.11、pydantic v2、typer、pytest。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/novelvideo/creation/asset_design/__init__.py` | 导出 A 切片公共类型 |
| `src/novelvideo/creation/asset_design/models.py` | `AssetDesignBrief` / `CompileResult` / `Issue` / `AdmissionProposal` |
| `src/novelvideo/director_plan/panel_spec.py` | `PanelSpec` 模型 + `compile_panel_spec` 签名（实现抛 NotImplementedError） |
| `src/novelvideo/freezone/video_director/segment_planner.py` | `SegmentDraft` / `SegmentPlan` 模型 + `split_segments` 签名 |
| `src/novelvideo/audio/voice_design.py` | `VoiceDesignBrief` 模型 + `build_voice_design_brief` 签名 |
| `src/novelvideo/music/alignment.py` | `AudioMode` 枚举 + `GroupAudioPlan` 模型 |
| `tests/test_interface_contracts.py` | 字段名与默认值的契约测试 |

---

### 任务 1：A 切片模型

**文件：**
- 创建：`src/novelvideo/creation/asset_design/__init__.py`
- 创建：`src/novelvideo/creation/asset_design/models.py`
- 测试：`tests/test_interface_contracts.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.creation.asset_design import (
    AssetDesignBrief, CompileResult, Fact, FactSource, Issue, Wardrobe,
)

def test_brief_requires_asset_identity():
    brief = AssetDesignBrief(
        asset_kind="character", asset_id="lin-du",
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))],
        design={"silhouette": "普通偏瘦", "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红")]},
    )
    assert brief.schema_version == 1
    assert brief.variant_id is None
    assert brief.facts[0].source.episode == 1

def test_issue_severity_limited():
    issue = Issue(code="brief_source_missing", severity="error", message="quote not found")
    assert issue.severity == "error"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_interface_contracts.py -k brief -v`
预期：FAIL，`ModuleNotFoundError: novelvideo.creation.asset_design`

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/models.py
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

AssetKind = Literal["character", "scene", "prop"]
FactField = Literal[
    "age", "gender", "hair", "face", "body", "wardrobe", "carried_item", "other",
    "location", "era", "weather", "time_of_day", "light", "landmark",
    "material", "color", "scale", "state", "owner",
]

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

class FactSource(_Model):
    episode: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)

class Fact(_Model):
    field: FactField
    value: str = Field(min_length=1, max_length=400)
    source: FactSource

class Wardrobe(_Model):
    piece: str = Field(min_length=1, max_length=120)
    color: str = ""
    material: str = ""
    state: str = ""

class Consistency(_Model):
    anchors: list[str] = Field(default_factory=list, max_length=12)
    forbidden: list[str] = Field(default_factory=list, max_length=12)

class Render(_Model):
    material: str = ""
    volume: str = ""
    negative: list[str] = Field(default_factory=list, max_length=40)

class Admission(_Model):
    confirmed: bool = False
    confirmed_by: str = ""
    basis: str = ""

class PanoViewpoint(_Model):
    label: str = Field(min_length=1, max_length=40)
    yaw: float
    pitch: float = 0.0
    fov: float = 60.0

class PanoDesign(_Model):
    front_anchor: str = ""
    back_anchor: str = ""
    viewpoints: list[PanoViewpoint] = Field(default_factory=list, max_length=24)

class AssetDesignBrief(_Model):
    schema_version: Literal[1] = 1
    asset_kind: AssetKind
    asset_id: str = Field(min_length=1, max_length=128)
    variant_id: str | None = Field(default=None, max_length=128)
    facts: list[Fact] = Field(default_factory=list, max_length=200)
    design: dict[str, Any] = Field(default_factory=dict)
    consistency: Consistency = Field(default_factory=Consistency)
    render: Render = Field(default_factory=Render)
    pano: PanoDesign | None = None
    admission: Admission = Field(default_factory=Admission)

class Issue(_Model):
    code: str = Field(min_length=1, max_length=80)
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1, max_length=1000)
    field: str = ""

class Fingerprint(_Model):
    brief_hash: str = ""
    style_id: str = ""
    style_revision: str = ""
    design_schema_revision: int = 0
    compiler_version: str = "1"

class CompileResult(_Model):
    compiled: dict[str, Any] = Field(default_factory=dict)
    prompt_text: str = ""
    style_fragment: str = ""
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)
    issues: list[Issue] = Field(default_factory=list)
```

```python
# src/novelvideo/creation/asset_design/__init__.py
from .models import (
    Admission, AssetDesignBrief, AssetKind, CompileResult, Consistency, Fact,
    FactField, FactSource, Fingerprint, Issue, PanoDesign, PanoViewpoint, Render, Wardrobe,
)

__all__ = [
    "Admission", "AssetDesignBrief", "AssetKind", "CompileResult", "Consistency", "Fact",
    "FactField", "FactSource", "Fingerprint", "Issue", "PanoDesign", "PanoViewpoint",
    "Render", "Wardrobe",
]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_interface_contracts.py -k brief -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design tests/test_interface_contracts.py
git commit -m "feat(asset-design): freeze brief and compile contracts"
```

---

### 任务 2：PanelSpec 契约

**文件：**
- 创建：`src/novelvideo/director_plan/panel_spec.py`
- 修改：`tests/test_interface_contracts.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.director_plan.panel_spec import PanelSpec, compile_panel_spec

def test_panel_spec_has_fixed_sections():
    spec = PanelSpec(sections={"count": "画面内恰好 2 人", "shot": "近景，平视"})
    assert spec.sections["count"].startswith("画面内")
    assert spec.prompt_text == ""
    assert spec.negatives == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_interface_contracts.py -k panel_spec -v`
预期：FAIL，模块不存在

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/director_plan/panel_spec.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PanelSection = Literal[
    "count", "shot", "axis", "blocking", "lighting", "performance", "props", "continuity",
]

class PanelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: dict[str, str] = Field(default_factory=dict)
    prompt_text: str = ""
    negatives: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

def compile_panel_spec(shot, group) -> PanelSpec:
    raise NotImplementedError("B slice implements compile_panel_spec")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_interface_contracts.py -k panel_spec -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/director_plan/panel_spec.py tests/test_interface_contracts.py
git commit -m "feat(director): freeze panel spec contract"
```

---

### 任务 3：段草案契约

**文件：**
- 创建：`src/novelvideo/freezone/video_director/segment_planner.py`
- 修改：`tests/test_interface_contracts.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.freezone.video_director.segment_planner import SegmentDraft, SegmentPlan

def test_segment_defaults():
    seg = SegmentDraft(segment_id="G01-S01", source_rows=[1], duration_seconds=6.0,
                       frame_mode="first", first_frame="cell-1.png")
    plan = SegmentPlan(group_id="G01", segments=[seg])
    assert plan.segments[0].last_frame is None
    assert plan.segments[0].references == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_interface_contracts.py -k segment -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/freezone/video_director/segment_planner.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

FrameMode = Literal["first", "first_last"]

class SegmentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["character", "scene", "prop"]
    asset_id: str
    image: str

class SegmentDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker: str = ""
    text: str
    mode: Literal["voiceover", "on_camera"] = "on_camera"

class TechniqueRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int

class CaseRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    source_id: str = ""
    provenance: str = ""

class SegmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    source_rows: list[int] = Field(default_factory=list)
    duration_seconds: float = Field(gt=0)
    frame_mode: FrameMode = "first"
    first_frame: str | None = None
    last_frame: str | None = None
    references: list[SegmentReference] = Field(default_factory=list)
    dialogue: list[SegmentDialogue] = Field(default_factory=list)
    panel_spec: dict = Field(default_factory=dict)
    technique_intent: str = ""
    technique: TechniqueRef | None = None
    case_reference: CaseRef | None = None

class SegmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str
    segments: list[SegmentDraft] = Field(default_factory=list)

def split_segments(rows, group) -> SegmentPlan:
    raise NotImplementedError("C slice implements split_segments")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_interface_contracts.py -k segment -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/video_director/segment_planner.py tests/test_interface_contracts.py
git commit -m "feat(video): freeze segment draft contract"
```

---

### 任务 4：配音 brief 与音频模式

**文件：**
- 创建：`src/novelvideo/audio/voice_design.py`
- 创建：`src/novelvideo/music/alignment.py`
- 修改：`tests/test_interface_contracts.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.audio.voice_design import VoiceDesignBrief
from novelvideo.music.alignment import AudioMode, GroupAudioPlan

def test_voice_brief_fields():
    brief = VoiceDesignBrief(character_id="lin-du", instruct_zh="男性，约40岁，中低音域，干涩音色")
    assert brief.language == "Chinese"
    assert brief.negatives == []

def test_audio_mode_default():
    plan = GroupAudioPlan(group_id="G01")
    assert plan.audio_mode == AudioMode.DUB
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_interface_contracts.py -k voice -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/audio/voice_design.py
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field

class VoiceDesignBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str
    instruct_zh: str = ""
    instruct_en: str = ""
    language: str = "Chinese"
    audition_text: str = ""
    negatives: list[str] = Field(default_factory=list)

def build_voice_design_brief(*args, **kwargs) -> VoiceDesignBrief:
    raise NotImplementedError("C slice implements build_voice_design_brief")
```

```python
# src/novelvideo/music/alignment.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

class AudioMode(str, Enum):
    DUB = "dub"
    ORIGINAL = "original"
    HYBRID = "hybrid"

class GroupAudioPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str
    audio_mode: AudioMode = AudioMode.DUB
    replaced_line_ids: list[str] = Field(default_factory=list)
    music_enabled: bool = False
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_interface_contracts.py -v`
预期：全部通过

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/audio/voice_design.py src/novelvideo/music/alignment.py tests/test_interface_contracts.py
git commit -m "feat(audio): freeze voice brief and audio mode contracts"
```

---

## 自检

- 规格覆盖：本计划只覆盖四份规格的公共类型，不实现行为；每个类型都能在规格中找到出处。
- 占位符：无 TODO；空实现均为显式 `NotImplementedError`，由后续切片替换。
- 类型一致性：`Issue`、`PanelSpec`、`SegmentDraft`、`VoiceDesignBrief`、`AudioMode` 的字段名在四份计划中保持一致。
