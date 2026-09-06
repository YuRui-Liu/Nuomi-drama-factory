# Higgsfield 方法论 × MiniMax H3 连续性吸收实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变现有 H3 默认行为的前提下，引入逐逻辑镜头的连续性契约、S/I/M/C 风险审计、I2VA/FL2VA 可达性路由、可审计编译 Bundle、Manifest 证据与灰度策略，并为 H3 Ref 保留严格能力门控。

**架构：** 新建 `novelvideo.shot_continuity` 领域包，负责 Contract、revision store、DirectorPlan 投影、风险与模式决策、Bundle 编译。现有 H3 optimizer/runtime 继续负责 typed plan 和 Provider 执行；`narrative_group_video` 只负责编排 policy、构建契约、选择是否阻断或采用新 Bundle，并把完整证据写入扩展后的 H3 Manifest。一个物理 H3 segment 可引用一到两个按逻辑 `ShotPlan` 保存的 Contract。

**技术栈：** Python 3.11、Pydantic v2、FastAPI、portalocker、pytest/pytest-asyncio、React 19、TypeScript、TanStack Query、Vitest。

---

## 0. 实施边界与顺序

本计划完成规格中的 Phase 0–3 代码基础，以及 Phase 4 的 Ref 编译接口和不可用门控。它不会宣称真实 H3 Ref 混合输入可用，也不会发起付费烟测。真实 Ref workflow、上传和 Subject/Picture UI 继续受以下规格中的授权烟测关卡约束：

- `docs/superpowers/specs/2026-09-06-minimax-h3-reference-director-design.md`
- `docs/superpowers/specs/2026-09-06-higgsfield-minimax-h3-continuity-absorption-design.md`

每个任务开始前先运行 `git status --short`。当前工作区存在用户未提交修改；只暂存任务“文件”列表中的路径，不得格式化、恢复或提交其他文件。

执行实现前必须使用 `using-git-worktrees` 创建基于本计划提交的独立 worktree；不要在当前含用户改动的 `main` 工作区直接执行任务。

## 1. 文件结构

### 新建后端领域文件

- `src/novelvideo/shot_continuity/__init__.py`：公开稳定类型和入口函数。
- `src/novelvideo/shot_continuity/hashing.py`：规范 JSON 和 SHA-256。
- `src/novelvideo/shot_continuity/models.py`：Contract、风险报告、模式决策、Ref binding 和 Bundle。
- `src/novelvideo/shot_continuity/store.py`：逐 episode 的原子 revision store 与 CAS。
- `src/novelvideo/shot_continuity/builder.py`：从逻辑 `ShotPlan` 与 Director World snapshot 构建 Contract。
- `src/novelvideo/shot_continuity/risk.py`：S/I/M/C 信号、评分、reason codes 和阻断项。
- `src/novelvideo/shot_continuity/mode_selector.py`：I2VA/FL2VA/reject 决策。
- `src/novelvideo/shot_continuity/compiler.py`：连续性锁、Base/Ref Bundle 和 bundle hash。
- `src/novelvideo/shot_continuity/evaluation.py`：从冻结 Manifest 聚合基线指标。

### 修改后端集成文件

- `src/novelvideo/media_capabilities/video/h3_prompt_profile.py`：允许 static camera，升级 profile version。
- `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：把 Contract 锁作为 typed plan 输入和缓存哈希的一部分。
- `src/novelvideo/media_capabilities/video/h3_timeline.py`：扩展 Manifest 证据、attempt 和 postflight 字段。
- `src/novelvideo/media_capabilities/video/workflow_registry.py`：增加 `continuity_policy` 参数与不可用 Ref 目录项。
- `src/novelvideo/task_backend/runners/narrative_group_video.py`：接入 legacy/observe/guard/enforce 编排。
- `src/novelvideo/api/routes/narrative_groups.py`：返回连续性证据，并提供显式 postflight 接口。

### 修改前端文件

- `frontend/src/lib/queries/shot-continuity.ts`：连续性 Manifest 与 postflight API 类型，避免继续扩大通用 narrative-group query 文件。
- `frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`：显示 Contract、风险、路由和 observed 状态。
- `frontend/src/components/episode/narrative-workbench/group-video-result.tsx`：把 postflight 保存动作传给抽屉。

### 新建或修改测试与文档

- `tests/shot_continuity/test_models.py`
- `tests/shot_continuity/test_store.py`
- `tests/shot_continuity/test_builder.py`
- `tests/shot_continuity/test_risk.py`
- `tests/shot_continuity/test_mode_selector.py`
- `tests/shot_continuity/test_compiler.py`
- `tests/shot_continuity/test_evaluation.py`
- `tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- `tests/media_capabilities/video/test_h3_prompt_compiler.py`
- `tests/media_capabilities/video/test_h3_timeline.py`
- `tests/media_capabilities/video/test_workflow_registry.py`
- `tests/test_task_narrative_group_video_runner.py`
- `tests/test_api_narrative_groups.py`
- `frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`
- `docs/cookbook/pipelines/07-video.md`

## 2. 任务分解

### 任务 1：建立连续性领域模型与稳定哈希

**文件：**
- 创建：`src/novelvideo/shot_continuity/__init__.py`
- 创建：`src/novelvideo/shot_continuity/hashing.py`
- 创建：`src/novelvideo/shot_continuity/models.py`
- 创建：`tests/shot_continuity/test_models.py`

- [ ] **步骤 1：编写失败的模型与哈希测试**

```python
# tests/shot_continuity/test_models.py
from pydantic import ValidationError
import pytest

from novelvideo.shot_continuity.hashing import canonical_sha256
from novelvideo.shot_continuity.models import (
    BoundaryState,
    CameraLock,
    Evidence,
    SceneLock,
    ShotContinuityContract,
)


def _contract(revision: int = 1) -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id="shot-1",
        scene_id="scene-1",
        scene=SceneLock(scene_state="night", space_anchor="doorway"),
        camera=CameraLock(
            shot_size="medium", angle="eye_level", composition="actor screen left"
        ),
        boundary=BoundaryState(
            carry_in="right hand empty",
            planned_carry_out="right hand holds cup",
        ),
    )


def test_contract_hash_ignores_mapping_order_but_not_revision() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}
    assert canonical_sha256(left) == canonical_sha256(right)
    assert _contract(1).contract_sha256 != _contract(2).contract_sha256


def test_inferred_evidence_requires_confidence() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Evidence(source="inferred")


def test_observed_state_is_distinct_from_planned_state() -> None:
    contract = _contract()
    assert contract.boundary.observed_carry_out is None
    assert contract.boundary.planned_carry_out == "right hand holds cup"
```

- [ ] **步骤 2：运行测试并确认因模块缺失而失败**

运行：

```bash
uv run pytest tests/shot_continuity/test_models.py -q
```

预期：FAIL，包含 `ModuleNotFoundError: No module named 'novelvideo.shot_continuity'`。

- [ ] **步骤 3：实现规范哈希与冻结 Pydantic 模型**

`hashing.py` 使用 UTF-8、`sort_keys=True`、紧凑分隔符和 `ensure_ascii=False`；禁止直接哈希 `repr()`：

```python
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
```

`models.py` 实现以下稳定字段和校验；所有模型统一 `ConfigDict(frozen=True, extra="forbid")`：

```python
class Evidence(BaseModel):
    model_config = MODEL_CONFIG
    source: Literal["explicit", "director_world", "inferred"] = "explicit"
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_confidence(self) -> "Evidence":
        if self.source == "inferred" and self.confidence is None:
            raise ValueError("inferred evidence requires confidence")
        return self


class AssetEvidence(BaseModel):
    model_config = MODEL_CONFIG
    asset_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SceneLock(BaseModel):
    model_config = MODEL_CONFIG
    scene_state: str = ""
    space_anchor: str = ""
    landmarks: tuple[str, ...] = ()
    axis: str = ""
    camera_side: str = ""
    screen_direction: str = ""
    assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class SubjectLock(BaseModel):
    model_config = MODEL_CONFIG
    subject_id: str = Field(min_length=1)
    state: str = ""
    screen_position: str = ""
    facing: str = ""
    gaze_target: str = ""
    visible: bool = True
    identity_assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class PropLock(BaseModel):
    model_config = MODEL_CONFIG
    prop_id: str = Field(min_length=1)
    state: str = ""
    owner_subject_id: str = ""
    held_in_hand: Literal["", "left", "right", "both"] = ""
    contact: str = ""
    critical: bool = False
    assets: tuple[AssetEvidence, ...] = ()
    evidence: Evidence = Field(default_factory=Evidence)


class CameraLock(BaseModel):
    model_config = MODEL_CONFIG
    shot_size: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    composition: str = ""
    motion: str = "static"
    axis: str = ""
    screen_direction: str = ""
    control_frames: tuple[AssetEvidence, ...] = ()


class LightingLock(BaseModel):
    model_config = MODEL_CONFIG
    key_source: str = ""
    direction: str = ""
    shadow_direction: str = ""
    exposure_priority: str = ""
    color_temperature: str = ""


class BoundaryState(BaseModel):
    model_config = MODEL_CONFIG
    carry_in: str = Field(min_length=1)
    planned_carry_out: str = Field(min_length=1)
    observed_carry_out: str | None = Field(default=None, min_length=1)
    deviation_accepted: bool = False
    deviation_reason: str = ""

    @model_validator(mode="after")
    def validate_deviation(self) -> "BoundaryState":
        if self.deviation_accepted and not self.deviation_reason.strip():
            raise ValueError("accepted deviation requires a reason")
        return self


class DirectorWorldBinding(BaseModel):
    model_config = MODEL_CONFIG
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    control_frame: AssetEvidence | None = None


class ShotContinuityContract(BaseModel):
    model_config = MODEL_CONFIG
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0)
    shot_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    predecessor_shot_id: str | None = Field(default=None, min_length=1)
    predecessor_revision: int | None = Field(default=None, gt=0)
    scene: SceneLock
    subjects: tuple[SubjectLock, ...] = ()
    props: tuple[PropLock, ...] = ()
    camera: CameraLock
    lighting: LightingLock = Field(default_factory=LightingLock)
    boundary: BoundaryState
    director_world: DirectorWorldBinding | None = None

    @property
    def contract_sha256(self) -> str:
        return canonical_sha256(self)

    @model_validator(mode="after")
    def validate_predecessor(self) -> "ShotContinuityContract":
        if (self.predecessor_shot_id is None) != (self.predecessor_revision is None):
            raise ValueError("predecessor id and revision must be supplied together")
        return self


class ContractRef(BaseModel):
    model_config = MODEL_CONFIG
    shot_id: str = Field(min_length=1)
    revision: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RiskDimensionScore(BaseModel):
    model_config = MODEL_CONFIG
    dimension: Literal["spatial", "identity", "motion", "continuity"]
    level: Literal[0, 1, 2]
    reasons: tuple[str, ...] = ()


class ShotRiskReport(BaseModel):
    model_config = MODEL_CONFIG
    spatial: RiskDimensionScore
    identity: RiskDimensionScore
    motion: RiskDimensionScore
    continuity: RiskDimensionScore
    blockers: tuple[str, ...] = ()


class H3ModeDecision(BaseModel):
    model_config = MODEL_CONFIG
    requested: Literal["auto", "i2va", "fl2va"]
    mode: Literal["i2va", "fl2va"] | None
    reason_codes: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


class FrameEvidence(BaseModel):
    model_config = MODEL_CONFIG
    asset_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class H3ReferenceBinding(BaseModel):
    model_config = MODEL_CONFIG
    reference_id: str = Field(min_length=1)
    source_kind: Literal["character_identity", "scene_base", "prop"]
    subject_index: int = Field(gt=0)
    picture_index: int = Field(gt=0)
    label: str = Field(min_length=1)
    asset: FrameEvidence


class CompiledShotBundle(BaseModel):
    model_config = MODEL_CONFIG
    schema_version: Literal[1] = 1
    segment_id: str = Field(min_length=1)
    source_shot_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    contracts: tuple[ContractRef, ...] = Field(min_length=1, max_length=2)
    compiler_id: Literal["minimax-h3-shot-compiler"] = "minimax-h3-shot-compiler"
    compiler_version: int = Field(gt=0)
    adapter: Literal["base-h3", "h3-ref"]
    mode: Literal["i2va", "fl2va"]
    prompt: str = Field(min_length=1)
    first_frame: FrameEvidence
    last_frame: FrameEvidence | None = None
    control_frames: tuple[FrameEvidence, ...] = ()
    references: tuple[H3ReferenceBinding, ...] = ()
    risk_report: ShotRiskReport
    mode_decision: H3ModeDecision
    diagnostics: tuple[str, ...] = ()
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_contract_order(self) -> "CompiledShotBundle":
        if tuple(item.shot_id for item in self.contracts) != self.source_shot_ids:
            raise ValueError("contract order must match source_shot_ids")
        if self.mode == "fl2va" and self.last_frame is None:
            raise ValueError("fl2va bundle requires last_frame")
        if self.adapter == "h3-ref" and not self.references:
            raise ValueError("h3-ref bundle requires reference bindings")
        return self
```

`CompiledShotBundle` 使用 `segment_id`、有序 `source_shot_ids` 和有序 `contracts`，不能使用单个 `contract_revision`。

- [ ] **步骤 4：导出公开接口并运行测试**

在 `__init__.py` 显式导出上述公开类型和 `canonical_sha256`，然后运行：

```bash
uv run pytest tests/shot_continuity/test_models.py -q
uv run ruff check src/novelvideo/shot_continuity tests/shot_continuity/test_models.py
```

预期：全部 PASS，ruff 无错误。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/shot_continuity tests/shot_continuity/test_models.py
git commit -m "feat: add shot continuity domain contracts"
```

### 任务 2：实现 Contract revision store 与 CAS

**文件：**
- 创建：`src/novelvideo/shot_continuity/store.py`
- 创建：`tests/shot_continuity/test_store.py`
- 修改：`src/novelvideo/shot_continuity/__init__.py`

- [ ] **步骤 1：编写原子保存、语义去重和冲突测试**

```python
def test_store_assigns_revision_and_deduplicates_equal_payload(tmp_path):
    store = ShotContinuityStore(tmp_path)
    first = store.put(1, contract(revision=0), expected_revision=0)
    duplicate = store.put(1, contract(revision=0), expected_revision=1)
    assert first.revision == 1
    assert duplicate == first
    assert store.load_active(1, "shot-1") == first


def test_store_rejects_stale_expected_revision(tmp_path):
    store = ShotContinuityStore(tmp_path)
    store.put(1, contract(revision=0), expected_revision=0)
    with pytest.raises(ContinuityRevisionConflict, match="expected 0, found 1"):
        store.put(1, changed_contract(), expected_revision=0)


def test_store_keeps_all_revisions(tmp_path):
    store = ShotContinuityStore(tmp_path)
    store.put(1, contract(revision=0), expected_revision=0)
    store.put(1, changed_contract(), expected_revision=1)
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2]


def test_store_reports_only_dependents_bound_to_an_old_predecessor_revision(tmp_path):
    store = ShotContinuityStore(tmp_path)
    first = store.put(1, contract(revision=0), expected_revision=0)
    successor = successor_contract(
        predecessor_shot_id=first.shot_id,
        predecessor_revision=first.revision,
    )
    store.put(1, successor, expected_revision=0)
    store.put(1, changed_contract(), expected_revision=1)
    assert [item.shot_id for item in store.stale_dependents(1, "shot-1")] == [
        "shot-2"
    ]
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_store.py -q
```

预期：FAIL，`ShotContinuityStore` 尚未定义。

- [ ] **步骤 3：实现逐 episode JSON store**

使用路径 `.shot_continuity/ep001.json`，格式固定为：

```json
{
  "schema_version": 1,
  "episode": 1,
  "shots": {
    "shot-1": {"active_revision": 2, "revisions": [{}, {}]}
  }
}
```

实现接口：

```python
class ContinuityRevisionConflict(RuntimeError):
    """The expected active contract revision is stale."""


class ShotContinuityStore:
    def __init__(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir).absolute().resolve()

    def path_for(self, episode: int) -> Path:
        if episode <= 0:
            raise ValueError("episode must be positive")
        return self._project_dir / ".shot_continuity" / f"ep{episode:03d}.json"

    def load_active(
        self, episode: int, shot_id: str
    ) -> ShotContinuityContract | None:
        revisions = self.list_revisions(episode, shot_id)
        return revisions[-1] if revisions else None

    def list_revisions(
        self, episode: int, shot_id: str
    ) -> tuple[ShotContinuityContract, ...]:
        _validate_shot_id(shot_id)
        document = _read_document(self.path_for(episode), episode)
        history = (document.get("shots") or {}).get(shot_id) or {}
        return tuple(
            ShotContinuityContract.model_validate(item)
            for item in history.get("revisions") or ()
        )

    def stale_dependents(
        self, episode: int, predecessor_shot_id: str
    ) -> tuple[ShotContinuityContract, ...]:
        predecessor = self.load_active(episode, predecessor_shot_id)
        if predecessor is None:
            return ()
        document = _read_document(self.path_for(episode), episode)
        active_contracts = [
            ShotContinuityContract.model_validate(history["revisions"][-1])
            for history in (document.get("shots") or {}).values()
            if history.get("revisions")
        ]
        return tuple(
            contract
            for contract in active_contracts
            if contract.predecessor_shot_id == predecessor_shot_id
            and contract.predecessor_revision != predecessor.revision
        )

    def put(
        self,
        episode: int,
        candidate: ShotContinuityContract,
        *,
        expected_revision: int,
    ) -> ShotContinuityContract:
        _validate_shot_id(candidate.shot_id)
        target = self.path_for(episode)
        with _store_guard(target):
            document = _read_document(target, episode)
            shots = dict(document.get("shots") or {})
            history = dict(shots.get(candidate.shot_id) or {})
            revisions = list(history.get("revisions") or [])
            active_revision = int(history.get("active_revision") or 0)
            if active_revision != expected_revision:
                raise ContinuityRevisionConflict(
                    f"expected {expected_revision}, found {active_revision}"
                )
            if revisions:
                active = ShotContinuityContract.model_validate(revisions[-1])
                if _semantic_hash(active) == _semantic_hash(candidate):
                    return active
            saved = candidate.model_copy(update={"revision": active_revision + 1})
            revisions.append(saved.model_dump(mode="json"))
            shots[candidate.shot_id] = {
                "active_revision": saved.revision,
                "revisions": revisions,
            }
            _atomic_write_document(
                target,
                {"schema_version": 1, "episode": episode, "shots": shots},
            )
            return saved


_SAFE_SHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _validate_shot_id(shot_id: str) -> None:
    if _SAFE_SHOT_ID.fullmatch(shot_id) is None or shot_id in {".", ".."}:
        raise ValueError("shot_id must be a safe stable identifier")


def _semantic_hash(contract: ShotContinuityContract) -> str:
    return canonical_sha256(contract.model_copy(update={"revision": 0}))


def _read_document(path: Path, episode: int) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "episode": episode, "shots": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("episode") != episode:
        raise ValueError("continuity store identity mismatch")
    return payload


@contextmanager
def _store_guard(target: Path) -> Iterator[None]:
    key = str(target.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise ValueError("continuity store directory must not be a symlink")
        with portalocker.Lock(str(target.with_suffix(".json.lock")), mode="a+", timeout=60):
            yield


def _atomic_write_document(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
```

实现要求：

- 使用 `portalocker` 加进程锁和模块级 `threading.RLock`；
- 临时文件必须与目标文件同目录，`flush` + `fsync` 后 `os.replace`；
- 拒绝 episode ≤ 0、空 shot ID、路径分隔符、`.`、`..` 和 symlink/reparse 路径；
- 比较语义相同时，把 `revision` 临时归零后哈希；相同则返回 active，不追加 revision；
- 不修改调用者传入的冻结模型。

- [ ] **步骤 4：验证 store 行为**

```bash
uv run pytest tests/shot_continuity/test_store.py -q
uv run ruff check src/novelvideo/shot_continuity/store.py tests/shot_continuity/test_store.py
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/shot_continuity/store.py src/novelvideo/shot_continuity/__init__.py tests/shot_continuity/test_store.py
git commit -m "feat: persist shot continuity revisions"
```

### 任务 3：从 DirectorPlan 构建逐逻辑镜头 Contract

**文件：**
- 创建：`src/novelvideo/shot_continuity/builder.py`
- 创建：`tests/shot_continuity/test_builder.py`
- 修改：`src/novelvideo/shot_continuity/__init__.py`

- [ ] **步骤 1：编写 DirectorPlan 投影测试**

```python
def test_builder_projects_explicit_director_facts_without_inventing_fields():
    shot = ShotPlan(
        id="shot-1",
        source_span_ids=("beat-1",),
        subject="沈璃",
        action="沈璃用右手拿起杯子",
        space_anchor="table screen right",
        visible_start_state="沈璃站在桌左侧，右手空",
        visible_end_state="沈璃站在桌左侧，右手持杯",
        shot_size="medium",
        camera_angle="eye_level",
        composition="沈璃 screen left, table screen right",
        camera_motion="static",
        duration_seconds=5,
        asset_requirements=(
            AssetRequirement(kind="character_identity", entity_key="char-shenli"),
            AssetRequirement(kind="prop", entity_key="prop-cup"),
        ),
    )
    result = build_shot_continuity_contract(
        shot,
        scene_id="scene-room",
        scene_state="night",
        predecessor=None,
        director_world=None,
        asset_evidence_by_entity={},
    )
    assert result.shot_id == "shot-1"
    assert result.subjects[0].subject_id == "char-shenli"
    assert result.props[0].prop_id == "prop-cup"
    assert result.camera.motion == "static"
    assert result.boundary.observed_carry_out is None
    assert result.lighting.direction == ""


def test_builder_uses_confirmed_predecessor_observed_state_as_carry_in():
    result = build_shot_continuity_contract(
        next_shot(), scene_id="scene-room", scene_state="night",
        predecessor=predecessor_with_observed("cup at lips"), director_world=None,
        asset_evidence_by_entity={},
    )
    assert result.boundary.carry_in == "cup at lips"
    assert result.predecessor_shot_id == "shot-1"
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_builder.py -q
```

预期：FAIL，builder 入口不存在。

- [ ] **步骤 3：实现无猜测的 Builder**

```python
def build_shot_continuity_contract(
    shot: ShotPlan,
    *,
    scene_id: str,
    scene_state: str,
    predecessor: ShotContinuityContract | None,
    director_world: Mapping[str, Any] | None,
    asset_evidence_by_entity: Mapping[str, AssetEvidence],
) -> ShotContinuityContract:
    character_ids = tuple(
        item.entity_key
        for item in shot.asset_requirements
        if item.kind in {"character_identity", "character_state"}
    ) or (shot.subject.strip(),)
    prop_requirements = tuple(
        item for item in shot.asset_requirements if item.kind == "prop"
    )
    observed = predecessor.boundary.observed_carry_out if predecessor else None
    carry_in = observed or shot.visible_start_state
    return ShotContinuityContract(
        revision=0,
        shot_id=shot.id,
        scene_id=scene_id,
        predecessor_shot_id=predecessor.shot_id if predecessor else None,
        predecessor_revision=predecessor.revision if predecessor else None,
        scene=SceneLock(
            scene_state=scene_state,
            space_anchor=shot.space_anchor,
            assets=(asset_evidence_by_entity[scene_id],)
            if scene_id in asset_evidence_by_entity
            else (),
            evidence=Evidence(source="explicit"),
        ),
        subjects=tuple(
            SubjectLock(
                subject_id=value,
                state=shot.visible_start_state,
                identity_assets=(asset_evidence_by_entity[value],)
                if value in asset_evidence_by_entity
                else (),
            )
            for value in dict.fromkeys(character_ids)
        ),
        props=tuple(
            PropLock(
                prop_id=item.entity_key,
                state=item.visible_change,
                critical=item.required and bool(item.visible_change.strip()),
                assets=(asset_evidence_by_entity[item.entity_key],)
                if item.entity_key in asset_evidence_by_entity
                else (),
            )
            for item in {
                requirement.entity_key: requirement
                for requirement in prop_requirements
            }.values()
        ),
        camera=CameraLock(
            shot_size=shot.shot_size,
            angle=shot.camera_angle,
            composition=shot.composition,
            motion=shot.camera_motion,
        ),
        boundary=BoundaryState(
            carry_in=carry_in,
            planned_carry_out=shot.visible_end_state,
        ),
        director_world=director_world_binding(director_world),
    )


class ContinuityContractUnavailable(LookupError):
    """A physical segment cannot be mapped to active logical shots."""


def director_world_binding(
    snapshot: Mapping[str, Any] | None,
) -> DirectorWorldBinding | None:
    if not snapshot:
        return None
    raw_control = snapshot.get("control_frame")
    control_frame = (
        AssetEvidence.model_validate(raw_control)
        if isinstance(raw_control, Mapping)
        else None
    )
    return DirectorWorldBinding(
        snapshot_sha256=canonical_sha256(snapshot),
        control_frame=control_frame,
    )


def contracts_for_segment(
    plan: DirectorPlanRevision,
    segment_id: str,
    *,
    predecessors: Mapping[str, ShotContinuityContract],
    director_world_by_shot: Mapping[str, Mapping[str, Any]],
    asset_evidence_by_entity: Mapping[str, AssetEvidence],
) -> tuple[ShotContinuityContract, ...]:
    requested_ids = tuple(segment_id.split("--"))
    shot_index = {
        shot.id: (group, shot)
        for group in plan.groups
        for shot in group.shots
    }
    missing = [shot_id for shot_id in requested_ids if shot_id not in shot_index]
    if missing:
        raise ContinuityContractUnavailable(
            f"contract_unavailable_legacy: {','.join(missing)}"
        )
    result = []
    for shot_id in requested_ids:
        group, shot = shot_index[shot_id]
        result.append(
            build_shot_continuity_contract(
                shot,
                scene_id=group.scene_anchor,
                scene_state=group.time_anchor,
                predecessor=predecessors.get(shot_id),
                director_world=director_world_by_shot.get(shot_id),
                asset_evidence_by_entity=asset_evidence_by_entity,
            )
        )
    return tuple(result)
```

`director_world_binding()` 只读取传入 snapshot 的规范 JSON 哈希和已存在控制帧的稳定 asset/hash；不从自然语言猜 camera、actor 或 prop 值。`contracts_for_segment()` 按 `segment_id.split("--")` 的顺序返回 1–2 个 Contract，找不到逻辑 shot 时返回明确的 `contract_unavailable_legacy` 诊断，而不是伪造 Contract。

- [ ] **步骤 4：运行 builder 测试**

```bash
uv run pytest tests/shot_continuity/test_builder.py -q
uv run ruff check src/novelvideo/shot_continuity/builder.py tests/shot_continuity/test_builder.py
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/shot_continuity/builder.py src/novelvideo/shot_continuity/__init__.py tests/shot_continuity/test_builder.py
git commit -m "feat: build continuity contracts from director plans"
```

### 任务 4：实现 S/I/M/C 风险审计与模式选择

**文件：**
- 创建：`src/novelvideo/shot_continuity/risk.py`
- 创建：`src/novelvideo/shot_continuity/mode_selector.py`
- 创建：`tests/shot_continuity/test_risk.py`
- 创建：`tests/shot_continuity/test_mode_selector.py`
- 修改：`src/novelvideo/shot_continuity/__init__.py`

- [ ] **步骤 1：编写四维风险测试**

```python
@pytest.mark.parametrize(
    ("signals", "scores", "blocker"),
    [
        (ShotRiskSignals(subject_count=1), (0, 0, 0, 0), None),
        (ShotRiskSignals(over_shoulder=True, exact_axis=True), (2, 0, 0, 0), "director_world_required"),
        (ShotRiskSignals(subject_count=3, strong_occlusion=True), (1, 2, 0, 0), "reference_capability_required"),
        (ShotRiskSignals(action_beats=4, complex_camera=True), (0, 0, 2, 0), "shot_rewrite_required"),
        (ShotRiskSignals(exact_boundary=True, critical_prop_handoff=True), (0, 0, 0, 2), None),
    ],
)
def test_risk_dimensions_remain_independent(signals, scores, blocker):
    report = audit_h3_shot(signals, ref_available=False, director_world_available=False)
    assert (report.spatial.level, report.identity.level, report.motion.level, report.continuity.level) == scores
    assert blocker in report.blockers if blocker else not report.blockers
```

- [ ] **步骤 2：编写模式选择测试**

```python
def test_auto_uses_i2va_without_exact_terminal_state():
    decision = select_h3_mode(
        requested="auto", has_first_frame=True, has_last_frame=False,
        exact_terminal_state=False, endpoint_reachable=True, motion_level=1,
    )
    assert decision.mode == "i2va"
    assert decision.blockers == ()


def test_auto_uses_fl2va_only_for_reachable_exact_terminal_state():
    decision = select_h3_mode(
        requested="auto", has_first_frame=True, has_last_frame=True,
        exact_terminal_state=True, endpoint_reachable=True, motion_level=1,
    )
    assert decision.mode == "fl2va"


def test_unreachable_endpoint_is_rejected_before_transport():
    decision = select_h3_mode(
        requested="fl2va", has_first_frame=True, has_last_frame=True,
        exact_terminal_state=True, endpoint_reachable=False, motion_level=2,
    )
    assert decision.mode is None
    assert "unreachable_motion" in decision.blockers
```

- [ ] **步骤 3：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_risk.py tests/shot_continuity/test_mode_selector.py -q
```

预期：FAIL，风险与模式函数不存在。

- [ ] **步骤 4：实现确定性规则**

`risk.py` 公开 `ShotRiskSignals` 和 `audit_h3_shot()`。所有文本启发式只允许集中在 `signals_for_shot()`，并为中英文关键词写参数化测试；评分函数只接收 typed signals：

```python
@dataclass(frozen=True, slots=True)
class ShotRiskSignals:
    subject_count: int = 1
    over_shoulder: bool = False
    exact_axis: bool = False
    topology_change: bool = False
    strong_occlusion: bool = False
    action_beats: int = 1
    direction_changes: int = 0
    complex_camera: bool = False
    has_predecessor: bool = False
    exact_boundary: bool = False
    critical_prop_handoff: bool = False


_OVER_SHOULDER = ("over shoulder", "over-the-shoulder", "过肩", "反打")
_OCCLUSION = ("occluded", "occlusion", "遮挡", "遮住")
_TOPOLOGY_CHANGE = ("new location", "change location", "换场", "穿越空间")
_COMPLEX_CAMERA = ("orbit", "crane", "drone", "handheld", "环绕", "升降", "航拍", "手持")
_DIRECTION_WORDS = ("left", "right", "forward", "backward", "左", "右", "前", "后")


def signals_for_shot(
    shot: ShotPlan,
    contract: ShotContinuityContract,
) -> ShotRiskSignals:
    action_text = shot.action.casefold()
    text = " ".join(
        (
            shot.action,
            shot.space_anchor,
            shot.camera_angle,
            shot.composition,
            shot.camera_motion,
        )
    ).casefold()
    clauses = [
        value.strip()
        for value in re.split(r"[,;，；]|\bthen\b|随后|然后|同时", shot.action)
        if value.strip()
    ]
    direction_hits = [word for word in _DIRECTION_WORDS if word in action_text]
    return ShotRiskSignals(
        subject_count=max(1, len(contract.subjects)),
        over_shoulder=any(token in text for token in _OVER_SHOULDER),
        exact_axis=bool(contract.scene.axis or contract.camera.axis),
        topology_change=any(token in text for token in _TOPOLOGY_CHANGE),
        strong_occlusion=any(token in text for token in _OCCLUSION),
        action_beats=max(1, len(clauses)),
        direction_changes=max(0, len(direction_hits) - 1),
        complex_camera=any(token in text for token in _COMPLEX_CAMERA),
        has_predecessor=contract.predecessor_shot_id is not None,
        exact_boundary=shot.continuous_with_next,
        critical_prop_handoff=any(item.critical for item in contract.props),
    )


def _dimension(
    name: Literal["spatial", "identity", "motion", "continuity"],
    level: Literal[0, 1, 2],
    reasons: list[str],
) -> RiskDimensionScore:
    return RiskDimensionScore(dimension=name, level=level, reasons=tuple(reasons))


def spatial_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    reasons = []
    if signals.over_shoulder:
        reasons.append("over_shoulder")
    if signals.exact_axis:
        reasons.append("exact_axis")
    if signals.topology_change:
        reasons.append("topology_change")
    if reasons:
        return _dimension("spatial", 2, reasons)
    if signals.subject_count >= 2:
        return _dimension("spatial", 1, ["multiple_subjects"])
    return _dimension("spatial", 0, [])


def identity_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    if signals.subject_count >= 3 or signals.strong_occlusion:
        reasons = ["three_or_more_subjects"] if signals.subject_count >= 3 else []
        if signals.strong_occlusion:
            reasons.append("strong_occlusion")
        return _dimension("identity", 2, reasons)
    if signals.subject_count == 2:
        return _dimension("identity", 1, ["two_subjects"])
    return _dimension("identity", 0, [])


def motion_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    reasons = []
    if signals.action_beats > 3:
        reasons.append("too_many_action_beats")
    if signals.direction_changes > 1:
        reasons.append("repeated_direction_change")
    if signals.complex_camera and signals.action_beats > 2:
        reasons.append("complex_camera_competes_with_action")
    if reasons:
        return _dimension("motion", 2, reasons)
    if signals.action_beats >= 2 or signals.direction_changes == 1:
        return _dimension("motion", 1, ["multi_beat_motion"])
    return _dimension("motion", 0, [])


def continuity_score(signals: ShotRiskSignals) -> RiskDimensionScore:
    reasons = []
    if signals.exact_boundary:
        reasons.append("exact_boundary")
    if signals.critical_prop_handoff:
        reasons.append("critical_prop_handoff")
    if reasons:
        return _dimension("continuity", 2, reasons)
    if signals.has_predecessor:
        return _dimension("continuity", 1, ["has_predecessor"])
    return _dimension("continuity", 0, [])


def audit_h3_shot(
    signals: ShotRiskSignals,
    *,
    ref_available: bool,
    director_world_available: bool,
) -> ShotRiskReport:
    spatial = spatial_score(signals)
    identity = identity_score(signals)
    motion = motion_score(signals)
    continuity = continuity_score(signals)
    blockers = []
    if motion.level == 2:
        blockers.append("shot_rewrite_required")
    if spatial.level == 2 and not director_world_available:
        blockers.append("director_world_required")
    if identity.level == 2 and not ref_available:
        blockers.append("reference_capability_required")
    return ShotRiskReport(
        spatial=spatial,
        identity=identity,
        motion=motion,
        continuity=continuity,
        blockers=tuple(blockers),
    )
```

`mode_selector.py` 的优先级固定为：缺首帧 → M2/不可达 → 显式模式输入校验 → C2/精确末态选择 FL2VA → 其余 I2VA。显式 `fl2va` 也不能绕过不可达检查：

```python
def select_h3_mode(
    *,
    requested: Literal["auto", "i2va", "fl2va"],
    has_first_frame: bool,
    has_last_frame: bool,
    exact_terminal_state: bool,
    endpoint_reachable: bool,
    motion_level: Literal[0, 1, 2],
) -> H3ModeDecision:
    blockers = []
    if not has_first_frame:
        blockers.append("first_frame_required")
    if motion_level == 2 or not endpoint_reachable:
        blockers.append("unreachable_motion")
    if requested == "fl2va" and not has_last_frame:
        blockers.append("last_frame_required")
    if requested == "i2va" and exact_terminal_state:
        blockers.append("exact_terminal_requires_fl2va")
    if blockers:
        return H3ModeDecision(
            requested=requested,
            mode=None,
            blockers=tuple(dict.fromkeys(blockers)),
        )
    if requested == "i2va":
        return H3ModeDecision(requested=requested, mode="i2va", reason_codes=("explicit_i2va",))
    if requested == "fl2va":
        return H3ModeDecision(requested=requested, mode="fl2va", reason_codes=("explicit_fl2va",))
    if exact_terminal_state:
        if not has_last_frame:
            return H3ModeDecision(
                requested=requested,
                mode=None,
                blockers=("last_frame_required",),
            )
        return H3ModeDecision(
            requested=requested,
            mode="fl2va",
            reason_codes=("reachable_exact_terminal",),
        )
    return H3ModeDecision(
        requested=requested,
        mode="i2va",
        reason_codes=("soft_terminal",),
    )
```

- [ ] **步骤 5：验证风险与模式矩阵**

```bash
uv run pytest tests/shot_continuity/test_risk.py tests/shot_continuity/test_mode_selector.py -q
uv run ruff check src/novelvideo/shot_continuity tests/shot_continuity
```

预期：全部 PASS。

- [ ] **步骤 6：Commit**

```bash
git add src/novelvideo/shot_continuity tests/shot_continuity
git commit -m "feat: audit H3 shot risk and endpoint reachability"
```

### 任务 5：接入连续性锁、允许 static camera，并编译 Bundle

**文件：**
- 创建：`src/novelvideo/shot_continuity/compiler.py`
- 创建：`tests/shot_continuity/test_compiler.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_profile.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_compiler.py`

- [ ] **步骤 1：编写 static camera 与锁合并回归测试**

```python
def test_profile_allows_static_camera_as_an_explicit_director_choice():
    assert "static or moving" in H3_DIRECTOR_SYSTEM_PROMPT
    assert "Specify the camera movement" not in H3_DIRECTOR_SYSTEM_PROMPT


def test_optimizer_hash_changes_when_contract_locks_change(segment, context):
    left = context.model_copy(update={"continuity_locks": ("cup in right hand",)})
    right = context.model_copy(update={"continuity_locks": ("cup in left hand",)})
    assert _input_hash(segment, left, H3Mode.I2VA) != _input_hash(segment, right, H3Mode.I2VA)


def test_compile_and_gate_merges_contract_locks_before_wire_compile(segment, context, plan):
    context = context.model_copy(update={"continuity_locks": ("cup stays in right hand",)})
    result = compile_and_gate_h3_plan(
        plan, segment=segment, context=context, mode=H3Mode.I2VA, input_hash="a" * 64
    )
    assert "cup stays in right hand" in result.plan.continuity_locks
    assert "cup stays in right hand" in result.prompt
```

- [ ] **步骤 2：编写 Base/Ref Bundle 测试**

```python
def test_base_bundle_freezes_ordered_contract_refs_and_hashes(optimization_result):
    bundle = compile_shot_bundle(
        segment_id="shot-1--shot-2",
        source_shot_ids=("shot-1", "shot-2"),
        contracts=(contract_one(), contract_two()),
        optimization=optimization_result,
        decision=fl2va_decision(),
        risk_report=low_risk_report(),
        first_frame=frame("first", "1" * 64),
        last_frame=frame("last", "2" * 64),
        adapter="base-h3",
    )
    assert [item.shot_id for item in bundle.contracts] == ["shot-1", "shot-2"]
    assert bundle.bundle_sha256 == canonical_sha256(bundle.model_dump(exclude={"bundle_sha256"}))


def test_ref_bundle_requires_bindings_and_never_replaces_frames(optimization_result):
    arguments = {
        "segment_id": "shot-1",
        "source_shot_ids": ("shot-1",),
        "contracts": (contract_one(),),
        "optimization": optimization_result,
        "decision": i2va_decision(),
        "risk_report": low_risk_report(),
        "first_frame": frame("first", "1" * 64),
    }
    with pytest.raises(ValueError, match="reference bindings"):
        compile_shot_bundle(**arguments, adapter="h3-ref", references=())
    bundle = compile_shot_bundle(
        **arguments, adapter="h3-ref", references=(binding(),)
    )
    assert bundle.first_frame is not None
    assert bundle.references[0].picture_index == 1
```

- [ ] **步骤 3：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_compiler.py -q
```

预期：FAIL，缺少 Bundle compiler、context 字段和新 profile 文案。

- [ ] **步骤 4：实现最小编译改动**

对 `H3PromptContext` 增加：

```python
continuity_locks: tuple[str, ...] = ()
continuity_contracts_json: str = ""
risk_report_json: str = ""
```

`_build_task()` 把三项放在 Director-stage constraints 之后；`_input_hash()` 已序列化整个 context，无需另加旁路哈希。

在 `compile_and_gate_h3_plan()` 中先稳定去重合并锁：

```python
merged_locks = tuple(dict.fromkeys((*plan.continuity_locks, *context.continuity_locks)))
plan = plan.model_copy(update={"continuity_locks": merged_locks})
```

把 `H3_PROMPT_PROFILE_VERSION` 从 4 升为 5，并将系统规则改为：

```text
Specify whether the camera is static or moving. For movement, state direction, amplitude, speed, and ending composition; never add movement without a narrative purpose.
```

`compiler.py` 实现 `continuity_locks_for()` 与 `compile_shot_bundle()`；锁顺序固定为 scene → subject → prop → camera → lighting → boundary，只输出非空事实：

```python
H3_SHOT_COMPILER_VERSION = 1


def continuity_locks_for(
    contracts: tuple[ShotContinuityContract, ...],
) -> tuple[str, ...]:
    values = []
    for contract in contracts:
        scene = contract.scene
        if scene.scene_state:
            values.append(f"scene state stays {scene.scene_state}")
        if scene.space_anchor:
            values.append(f"space anchor stays {scene.space_anchor}")
        if scene.axis:
            values.append(f"action axis stays {scene.axis}")
        for subject in contract.subjects:
            values.append(f"subject {subject.subject_id} keeps identity and {subject.state}")
            if subject.screen_position:
                values.append(
                    f"subject {subject.subject_id} stays {subject.screen_position}"
                )
        for prop in contract.props:
            prop_state = ", ".join(
                value
                for value in (
                    prop.state,
                    f"owner {prop.owner_subject_id}" if prop.owner_subject_id else "",
                    f"held in {prop.held_in_hand} hand" if prop.held_in_hand else "",
                    prop.contact,
                )
                if value
            )
            if prop_state:
                values.append(f"prop {prop.prop_id}: {prop_state}")
        camera = contract.camera
        values.append(
            f"camera {camera.shot_size}, {camera.angle}, {camera.motion}; "
            f"composition {camera.composition or 'unchanged'}"
        )
        lighting = contract.lighting
        if lighting.direction:
            values.append(f"key light direction stays {lighting.direction}")
        values.append(f"frame 0 state: {contract.boundary.carry_in}")
        values.append(
            f"planned terminal state: {contract.boundary.planned_carry_out}"
        )
    return tuple(dict.fromkeys(values))


def compile_shot_bundle(
    *,
    segment_id: str,
    source_shot_ids: tuple[str, ...],
    contracts: tuple[ShotContinuityContract, ...],
    optimization: H3PromptOptimizationResult,
    decision: H3ModeDecision,
    risk_report: ShotRiskReport,
    first_frame: FrameEvidence,
    adapter: Literal["base-h3", "h3-ref"],
    last_frame: FrameEvidence | None = None,
    control_frames: tuple[FrameEvidence, ...] = (),
    references: tuple[H3ReferenceBinding, ...] = (),
    diagnostics: tuple[str, ...] = (),
) -> CompiledShotBundle:
    if decision.mode is None:
        raise ValueError("blocked mode decision cannot compile a bundle")
    contract_refs = tuple(
        ContractRef(
            shot_id=contract.shot_id,
            revision=contract.revision,
            sha256=contract.contract_sha256,
        )
        for contract in contracts
    )
    payload = {
        "schema_version": 1,
        "segment_id": segment_id,
        "source_shot_ids": source_shot_ids,
        "contracts": contract_refs,
        "compiler_version": H3_SHOT_COMPILER_VERSION,
        "adapter": adapter,
        "mode": decision.mode,
        "prompt": optimization.prompt,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "control_frames": control_frames,
        "references": references,
        "risk_report": risk_report,
        "mode_decision": decision,
        "diagnostics": diagnostics,
    }
    return CompiledShotBundle.model_validate(
        {**payload, "bundle_sha256": canonical_sha256(payload)}
    )
```

Bundle 在所有字段完成后计算 `bundle_sha256`，禁止把自身哈希纳入哈希输入。

- [ ] **步骤 5：更新所有 version fixture 并验证**

```bash
uv run pytest tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_quality.py -q
uv run ruff check src/novelvideo/shot_continuity/compiler.py src/novelvideo/media_capabilities/video/h3_prompt_profile.py src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py
```

预期：全部 PASS；static camera 用例保持通过，动态 camera 仍要求 direction/amplitude/speed。

- [ ] **步骤 6：Commit**

```bash
git add src/novelvideo/shot_continuity/compiler.py src/novelvideo/media_capabilities/video/h3_prompt_profile.py src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_compiler.py
git commit -m "feat: compile continuity-aware H3 bundles"
```

### 任务 6：扩展 Manifest、attempt 证据与 replay

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`tests/media_capabilities/video/test_h3_timeline.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：编写 Manifest v2 兼容性测试**

```python
def test_v1_manifest_still_loads_without_continuity_fields(tmp_path):
    path = write_manifest(tmp_path, format_version=1)
    loaded = load_h3_director_manifest(path)
    assert loaded.entries[0].compiled_bundle is None
    assert loaded.entries[0].attempts == ()


def test_manifest_freezes_bundle_risk_and_attempts(tmp_path):
    entry = base_entry().model_copy(update={
        "continuity_contracts": [contract.model_dump(mode="json")],
        "risk_report": risk.model_dump(mode="json"),
        "compiled_bundle": bundle.model_dump(mode="json"),
        "attempts": ({
            "attempt": 1,
            "status": "submitted",
            "provider_task_id": "task-1",
            "error_code": None,
        },),
    })
    saved = round_trip(tmp_path, manifest(entries=(entry,), format_version=2))
    assert saved.entries[0].compiled_bundle["bundle_sha256"] == bundle.bundle_sha256
    assert saved.entries[0].attempts[0].provider_task_id == "task-1"
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/media_capabilities/video/test_h3_timeline.py -q
```

预期：FAIL，Manifest entry 不接受新字段。

- [ ] **步骤 3：增加版本化证据模型**

在 `h3_timeline.py` 定义：

```python
class H3GenerationAttemptEvidence(BaseModel):
    model_config = _MODEL_CONFIG
    attempt: int = Field(gt=0)
    status: Literal["submitted", "completed", "transport_failed", "quality_rejected"]
    provider_task_id: str | None = None
    error_code: str | None = None


class H3ObservedBoundary(BaseModel):
    model_config = _MODEL_CONFIG
    value: str = Field(min_length=1)
    source_contract_revision: int = Field(gt=0)
    result_contract_revision: int = Field(gt=0)
    accepted: bool = False
    deviation_reason: str = ""
    lock_violations: tuple[
        Literal["identity", "spatial", "prop", "camera", "lighting"], ...
    ] = ()
```

给 `H3TimelineEntry` 增加：

```python
continuity_contracts: tuple[dict[str, Any], ...] = ()
risk_report: dict[str, Any] | None = None
mode_decision: dict[str, Any] | None = None
compiled_bundle: dict[str, Any] | None = None
attempts: tuple[H3GenerationAttemptEvidence, ...] = ()
observed_carry_out: H3ObservedBoundary | None = None
```

Manifest 顶层 `format_version` 默认升为 2，但 loader 必须接受现有 v1 文件的缺省字段。对以上 dict 使用 `deepcopy` validator，防止调用者后续修改 Manifest 快照。

- [ ] **步骤 4：让 runner 追加 attempt 而非覆盖证据**

新增纯函数 `_append_attempt(manifest, segment_id, evidence)`，并在 provider submitted、completed、transport failure 三个边界调用。每个 segment 的 attempt 从 1 连续递增；已有 Contract/Bundle 字段必须保留。

- [ ] **步骤 5：验证失败路径与 replay 证据**

```bash
uv run pytest tests/media_capabilities/video/test_h3_timeline.py tests/test_task_narrative_group_video_runner.py -q
uv run ruff check src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/task_backend/runners/narrative_group_video.py
```

预期：全部 PASS；现有 `quality_rejected`、`transport_failed`、`partial_failure` 测试仍通过。

- [ ] **步骤 6：Commit**

```bash
git add src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_h3_timeline.py tests/test_task_narrative_group_video_runner.py
git commit -m "feat: freeze H3 continuity evidence in manifests"
```

### 任务 7：接入 legacy / observe / guard / enforce 运行策略

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/workflow_registry.py`
- 修改：`tests/media_capabilities/video/test_workflow_registry.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：编写 workflow policy 测试**

```python
def test_h3_registry_exposes_continuity_policy_with_legacy_default(store, resolver):
    workflow = build_video_workflow_registry(store, resolver).list("narrative_group")[0]
    policy = next(item for item in workflow.parameters if item.key == "continuity_policy")
    assert policy.default == "legacy"
    assert [item.value for item in policy.options] == ["legacy", "observe", "guard", "enforce"]
```

- [ ] **步骤 2：编写四种 runner 行为测试**

```python
@pytest.mark.parametrize(
    ("policy", "has_blocker", "transport_calls", "uses_bundle_prompt"),
    [
        ("legacy", True, 1, False),
        ("observe", True, 1, False),
        ("guard", True, 0, False),
        ("enforce", False, 1, True),
    ],
)
def test_continuity_policy_controls_blocking_and_prompt_use(
    policy, has_blocker, transport_calls, uses_bundle_prompt, seeded_group, monkeypatch
):
    result, captured, manifest = run_with_policy(
        seeded_group, monkeypatch, policy=policy, has_blocker=has_blocker
    )
    assert len(captured.transport) == transport_calls
    assert captured.used_bundle_prompt is uses_bundle_prompt
    if policy == "legacy":
        assert manifest.entries[0].risk_report is None
    else:
        assert manifest.entries[0].risk_report is not None
```

- [ ] **步骤 3：运行测试验证失败**

```bash
uv run pytest tests/media_capabilities/video/test_workflow_registry.py tests/test_task_narrative_group_video_runner.py -q
```

预期：FAIL，policy 参数与编排尚不存在。

- [ ] **步骤 4：实现 runner 编排 helper**

先把 Base 与 Ref 共用的参数定义提取为 `_h3_parameters()`，避免两个 registry 项复制后漂移：

```python
def _h3_parameters() -> tuple[VideoWorkflowParameterDefinition, ...]:
    return (
        VideoWorkflowParameterDefinition(
            key="resolution",
            label="分辨率",
            default="720p",
            scope="narrative_group",
            options=(
                VideoWorkflowParameterOption(
                    value="720p", label="标准", relative_cost="standard"
                ),
                VideoWorkflowParameterOption(
                    value="1080p",
                    label="高清",
                    description="画质更高，预计耗时和额度增加。",
                    relative_cost="higher",
                ),
            ),
        ),
        VideoWorkflowParameterDefinition(
            key="continuity_policy",
            label="连续性策略",
            default="legacy",
            scope="narrative_group",
            options=tuple(
                VideoWorkflowParameterOption(value=value, label=label)
                for value, label in (
                    ("legacy", "旧流程"),
                    ("observe", "只观察"),
                    ("guard", "阻断确定性错误"),
                    ("enforce", "启用新编译"),
                )
            ),
        ),
    )
```

把逻辑放入新 helper `_prepare_continuity_evidence()`，避免继续扩大 `_execute()`：

```python
@dataclass(frozen=True)
class PreparedContinuity:
    provider_segment: H3DirectorSegment
    contracts: tuple[ShotContinuityContract, ...]
    risk_report: ShotRiskReport
    mode_decision: H3ModeDecision
    bundle: CompiledShotBundle | None


def continuity_policy(parameters: Mapping[str, str]) -> str:
    value = str(parameters.get("continuity_policy") or "legacy")
    if value not in {"legacy", "observe", "guard", "enforce"}:
        raise ValueError("unsupported continuity_policy")
    return value
```

执行顺序固定为：

1. `legacy` 直接走当前路径；
2. 其余 policy 从 active DirectorPlan 映射逻辑 shot；从 render cell、场景/角色/道具引用记录解析稳定 asset ID 与 SHA-256，required 资产缺失时产生 `required_asset_evidence_missing`；随后构建并 `store.put()` Contract；
3. 读取现有 `_optimizer_director_context()` 作为 Director World snapshot；
4. 运行 risk + mode decision；
5. `observe` 保存诊断；当建议模式与当前模式一致时保存 shadow Bundle，否则保存 `shadow_mode_replan_required`，Provider 始终使用当前 segment/mode/prompt；
6. `guard` 遇 blocker 时在上传前生成 `quality_rejected` Manifest 并停止；无 blocker 仍使用旧 prompt；
7. `enforce` 遇 blocker 时停止，无 blocker 时按 mode decision 调整尾帧并使用 Bundle prompt；
8. S2 且没有 snapshot/control-frame evidence 时使用 `director_world_required`，不能调用 Provider；
9. I2 且 H3 Ref capability 不可用时使用 `reference_capability_required`，不能静默走 Base；
10. M2 始终使用 `shot_rewrite_required`，Director World 或 Ref 不能清除该 blocker。
11. C2 镜头若 predecessor 尚无 `observed_carry_out`，使用 `predecessor_observation_required`；若 `predecessor_revision` 已过期，使用 `predecessor_revision_stale`。C0/C1 镜头不因此串行化。

在 `H3WorkflowAdapter` 调用前从 `workflow_parameters` 剥离 `continuity_policy`，只把 Provider 参数传给实际 H3 runtime；完整产品参数仍保存在 Manifest。

- [ ] **步骤 5：验证旧路径字节级行为与新路径阻断时机**

```bash
uv run pytest tests/media_capabilities/video/test_workflow_registry.py tests/test_task_narrative_group_video_runner.py tests/test_api_runninghub_h3_backend.py -q
```

预期：全部 PASS；legacy fixture 不变；guard/enforce blocker 用例断言 transport mock 为 0 次。

- [ ] **步骤 6：Commit**

```bash
git add src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_workflow_registry.py tests/test_task_narrative_group_video_runner.py
git commit -m "feat: add staged H3 continuity policies"
```

### 任务 8：增加 H3 Ref 编译接口与不可用能力门控

**文件：**
- 修改：`src/novelvideo/shot_continuity/models.py`
- 修改：`src/novelvideo/shot_continuity/compiler.py`
- 修改：`tests/shot_continuity/test_compiler.py`
- 修改：`src/novelvideo/media_capabilities/video/workflow_registry.py`
- 修改：`tests/media_capabilities/video/test_workflow_registry.py`
- 创建：`tests/fixtures/runninghub/minimax_h3_ref_compiler_contract.json`

- [ ] **步骤 1：编写稳定 Subject/Picture 映射测试**

```python
def test_ref_bindings_compile_in_picture_order_without_replacing_frames():
    bindings = (
        H3ReferenceBinding(
            reference_id="character:shen-li",
            source_kind="character_identity",
            subject_index=1,
            picture_index=1,
            label="沈璃",
            asset=frame("char", "1" * 64),
        ),
        H3ReferenceBinding(
            reference_id="scene:room",
            source_kind="scene_base",
            subject_index=2,
            picture_index=2,
            label="室内",
            asset=frame("scene", "2" * 64),
        ),
    )
    compiled = compile_reference_definitions(bindings)
    assert compiled.splitlines() == [
        "<Subject 1> is Shen Li from <Picture 1>; preserve identity, hair, and wardrobe.",
        "<Subject 2> is the room from <Picture 2>; preserve architecture and set dressing.",
    ]
```

- [ ] **步骤 2：编写 registry 不可用关卡测试**

```python
def test_ref_workflow_is_listed_but_not_resolvable_before_hybrid_verification(store, resolver):
    registry = build_video_workflow_registry(store, resolver)
    ref = next(item for item in registry.list("narrative_group") if item.id == "runninghub:minimax-h3-ref")
    assert ref.available is False
    assert ref.unavailable_reason == "hybrid_input_unverified"
    with pytest.raises(VideoWorkflowUnavailable, match="hybrid_input_unverified"):
        registry.resolve(ref.id, "narrative_group")
```

- [ ] **步骤 3：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_workflow_registry.py -q
```

预期：FAIL，Ref definitions 和目录项不存在。

- [ ] **步骤 4：实现 Ref-only 编译接口，不接真实 transport**

`compile_reference_definitions()` 必须校验：

- `subject_index` 和 `picture_index` 都从 1 连续递增；
- reference ID、asset ID 和 SHA-256 唯一；
- 类型只允许 `character_identity`、`scene_base`、`prop`；
- 定义按 Picture 顺序生成；
- Bundle 仍要求首帧，FL2VA 仍要求尾帧；
- Ref 数量、顺序、描述或图片哈希变化会改变 bundle hash。

实现固定模板和连续编号校验：

```python
_REF_SUFFIX = {
    "character_identity": "preserve identity, hair, and wardrobe",
    "scene_base": "preserve architecture and set dressing",
    "prop": "preserve shape, material, and visible state",
}


def compile_reference_definitions(
    bindings: tuple[H3ReferenceBinding, ...],
) -> str:
    if not bindings:
        raise ValueError("reference bindings are required")
    ordered = tuple(sorted(bindings, key=lambda item: item.picture_index))
    expected = tuple(range(1, len(ordered) + 1))
    if tuple(item.picture_index for item in ordered) != expected:
        raise ValueError("picture_index values must be continuous from 1")
    if tuple(item.subject_index for item in ordered) != expected:
        raise ValueError("subject_index values must be continuous from 1")
    for values, label in (
        ((item.reference_id for item in ordered), "reference_id"),
        ((item.asset.asset_id for item in ordered), "asset_id"),
        ((item.asset.sha256 for item in ordered), "asset sha256"),
    ):
        values = tuple(values)
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {label}")
    return "\n".join(
        f"<Subject {item.subject_index}> is {item.label} from "
        f"<Picture {item.picture_index}>; {_REF_SUFFIX[item.source_kind]}."
        for item in ordered
    )
```

在 registry 中加入不可用定义：

```python
VideoWorkflowDefinition(
    id="runninghub:minimax-h3-ref",
    label="RunningHub MiniMax H3 · Ref",
    provider="runninghub",
    adapter_key="minimax-h3-ref",
    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
    supported_modes=("auto", "i2va", "fl2va"),
    parameters=_h3_parameters(),
    available=False,
    unavailable_reason="hybrid_input_unverified",
)
```

此任务不注册可执行 `minimax-h3-ref` transport adapter，也不读取未验证 workflow ID。Fixture 只冻结本地编译语义，不冒充 RunningHub 真实 payload fixture。

- [ ] **步骤 5：验证能力门控**

```bash
uv run pytest tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_workflow_registry.py -q
uv run ruff check src/novelvideo/shot_continuity src/novelvideo/media_capabilities/video/workflow_registry.py
```

预期：全部 PASS；Ref 出现在列表但无法 resolve；Base 仍是第一个可用默认项。

- [ ] **步骤 6：Commit**

```bash
git add src/novelvideo/shot_continuity/models.py src/novelvideo/shot_continuity/compiler.py src/novelvideo/media_capabilities/video/workflow_registry.py tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_workflow_registry.py tests/fixtures/runninghub/minimax_h3_ref_compiler_contract.json
git commit -m "feat: gate H3 Ref continuity compilation"
```

### 任务 9：提供显式 Postflight API 并在 Prompt Drawer 展示证据

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`tests/test_api_narrative_groups.py`
- 创建：`frontend/src/lib/queries/shot-continuity.ts`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-result.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`

- [ ] **步骤 1：编写 API 测试，确保 observed state 不会静默改写计划**

```python
def test_put_observed_boundary_requires_manifest_and_contract_revision(client, project):
    response = client.put(
        f"/api/v1/projects/{project}/episodes/1/narrative-groups/ng-01/video/segments/seg-1/continuity",
        json={
            "contract_revision": 2,
            "observed_carry_out": "cup in left hand",
            "accept_deviation": True,
            "deviation_reason": "导演接受换手，并将调整下一镜头",
        },
    )
    assert response.status_code == 200
    entry = response.json()["data"]["units"][0]
    assert entry["observed_carry_out"]["value"] == "cup in left hand"
    assert entry["observed_carry_out"]["source_contract_revision"] == 2
    assert entry["observed_carry_out"]["result_contract_revision"] == 3
    assert entry["continuity_contracts"][0]["boundary"]["planned_carry_out"] == "cup in right hand"
    assert response.json()["data"]["stale_dependent_shot_ids"] == ["shot-2"]


def test_put_observed_boundary_rejects_unexplained_deviation(client, project):
    response = client.put(
        f"/api/v1/projects/{project}/episodes/1/narrative-groups/ng-01/video/segments/seg-1/continuity",
        json={
            "contract_revision": 2,
            "observed_carry_out": "cup in left hand",
            "accept_deviation": True,
            "deviation_reason": "",
        },
    )
    assert response.status_code == 422
```

- [ ] **步骤 2：编写前端证据显示测试**

```tsx
it("shows independent S/I/M/C scores and planned versus observed boundary", async () => {
  renderDrawer({
    risk_report: {
      spatial: { level: 2, reasons: ["over_shoulder"] },
      identity: { level: 1, reasons: [] },
      motion: { level: 0, reasons: [] },
      continuity: { level: 2, reasons: ["critical_prop_handoff"] },
      blockers: ["director_world_required"],
    },
    continuity_contracts: [{
      revision: 2,
      boundary: { planned_carry_out: "右手持杯", observed_carry_out: null },
    }],
  });
  expect(await screen.findByText("空间风险 S2")).toBeInTheDocument();
  expect(screen.getByText("计划末态：右手持杯")).toBeInTheDocument();
  expect(screen.getByText("尚未验收实际末态")).toBeInTheDocument();
});
```

- [ ] **步骤 3：运行后端与前端测试验证失败**

```bash
uv run pytest tests/test_api_narrative_groups.py -q
pnpm --dir frontend test -- group-video-prompt-drawer.test.tsx
```

预期：后端 404/405，前端缺少新字段显示。

- [ ] **步骤 4：实现 Postflight API**

增加 request：

```python
class NarrativeGroupObservedBoundaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_revision: int = Field(ge=1)
    observed_carry_out: str = Field(min_length=1, max_length=2000)
    accept_deviation: bool = False
    deviation_reason: str = Field(default="", max_length=2000)
    lock_violations: tuple[
        Literal["identity", "spatial", "prop", "camera", "lighting"], ...
    ] = ()
```

路由只允许编辑当前 stage 指向的 Manifest；校验项目内路径、segment ID、Contract revision 和非运行状态。它更新 Manifest entry 的 `observed_carry_out`，并通过 `ShotContinuityStore.put()` 生成新 Contract revision；`H3ObservedBoundary.source_contract_revision` 保存生成时使用的 revision，`result_contract_revision` 保存带 observed 状态的新 revision。Manifest 中冻结的 `continuity_contracts` 不得改写。响应通过 `store.stale_dependents()` 返回 `stale_dependent_shot_ids`，前端仅标记这些直接后继镜头需要重新确认。若值不同于 planned 且 `accept_deviation=False`，返回 409；若接受偏差但原因为空，返回 422。禁止直接覆盖旧 revision。

- [ ] **步骤 5：实现最小前端审计面板**

在新建的 `shot-continuity.ts` 中隔离 mutation：

```tsx
export type ContinuityLockViolation =
  | "identity" | "spatial" | "prop" | "camera" | "lighting";

export interface ObservedBoundaryInput {
  groupId: string;
  segmentId: string;
  contractRevision: number;
  observedCarryOut: string;
  acceptDeviation: boolean;
  deviationReason: string;
  lockViolations: ContinuityLockViolation[];
}

export function useRecordObservedBoundary(project: string, episode: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ObservedBoundaryInput) => api.put(
      p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${input.groupId}/video/segments/${input.segmentId}/continuity`,
      { json: {
        contract_revision: input.contractRevision,
        observed_carry_out: input.observedCarryOut,
        accept_deviation: input.acceptDeviation,
        deviation_reason: input.deviationReason,
        lock_violations: input.lockViolations,
      } },
    ).json<ApiResponse<NarrativeGroupVideoPromptManifest>>(),
    onSuccess: (_data, input) => Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      queryClient.invalidateQueries({
        queryKey: [
          ...queryKeys.narrativeGroups(project, episode),
          input.groupId,
          "video",
          "prompts",
        ],
      }),
    ]),
  });
}
```

在现有 Prompt Drawer 内增加四个只读区块：

- `Contract revisions`；
- `S/I/M/C` 独立风险；
- mode/adapter/reason codes；
- planned 与 observed carry_out。

提供“记录实际末态”文本框、“接受偏差”复选框和身份/空间/道具/摄影机/灯光违规多选项；值不同于 planned 时显示原因输入。保存成功后 invalidate 当前 narrative group 与 prompt manifest query。历史 v1 Manifest 不显示空白错误，只显示“该历史任务未记录连续性证据”。

- [ ] **步骤 6：验证 API、UI 与类型检查**

```bash
uv run pytest tests/test_api_narrative_groups.py -q
pnpm --dir frontend test -- group-video-prompt-drawer.test.tsx
pnpm --dir frontend build
```

预期：全部 PASS，TypeScript 构建无错误。

- [ ] **步骤 7：Commit**

```bash
git add src/novelvideo/api/routes/narrative_groups.py tests/test_api_narrative_groups.py frontend/src/lib/queries/shot-continuity.ts frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx frontend/src/components/episode/narrative-workbench/group-video-result.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx
git commit -m "feat: review H3 continuity evidence postflight"
```

### 任务 10：建立基线评测、回归矩阵与发布文档

**文件：**
- 创建：`src/novelvideo/shot_continuity/evaluation.py`
- 创建：`tests/shot_continuity/test_evaluation.py`
- 修改：`docs/cookbook/pipelines/07-video.md`

- [ ] **步骤 1：编写 Manifest 聚合指标测试**

```python
def test_evaluation_keeps_lock_categories_separate():
    report = evaluate_manifests([
        accepted_manifest(first_pass=True, attempts=1, boundary_match=True),
        accepted_manifest(
            first_pass=False,
            attempts=2,
            boundary_match=False,
            violations=("spatial", "prop"),
        ),
    ])
    assert report.first_pass_usable_rate == 0.5
    assert report.attempts_per_accepted_shot == 1.5
    assert report.boundary_match_rate == 0.5
    assert report.lock_violation_rates["identity"] == 0.0
    assert report.lock_violation_rates["spatial"] == 0.5
    assert report.lock_violation_rates["prop"] == 0.5
```

- [ ] **步骤 2：运行测试验证失败**

```bash
uv run pytest tests/shot_continuity/test_evaluation.py -q
```

预期：FAIL，评测模块不存在。

- [ ] **步骤 3：实现纯离线评测函数**

```python
class H3ContinuityEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_entries: int
    accepted_shots: int
    scored_entries: int
    unscored_entries: int
    first_pass_usable_rate: float
    attempts_per_accepted_shot: float
    boundary_match_rate: float
    lock_violation_rates: dict[str, float]


LOCK_KINDS = ("identity", "spatial", "prop", "camera", "lighting")


def _planned_carry_out(entry: H3TimelineEntry) -> str | None:
    if not entry.continuity_contracts:
        return None
    boundary = entry.continuity_contracts[-1].get("boundary") or {}
    value = str(boundary.get("planned_carry_out") or "").strip()
    return value or None


def evaluate_manifests(
    manifests: Iterable[H3DirectorOutputManifest],
) -> H3ContinuityEvaluation:
    entries = [entry for manifest in manifests for entry in manifest.entries]
    accepted = [entry for entry in entries if entry.status == "completed"]
    scored = [
        entry
        for entry in accepted
        if entry.observed_carry_out is not None
        and _planned_carry_out(entry) is not None
    ]
    total = len(entries)
    accepted_count = len(accepted)
    scored_count = len(scored)
    first_pass = sum(len(entry.attempts) == 1 for entry in accepted)
    attempt_count = sum(len(entry.attempts) for entry in accepted)
    boundary_matches = sum(
        entry.observed_carry_out.value == _planned_carry_out(entry)
        for entry in scored
        if entry.observed_carry_out is not None
    )
    violation_counts = {
        key: sum(
            key in entry.observed_carry_out.lock_violations
            for entry in scored
            if entry.observed_carry_out is not None
        )
        for key in LOCK_KINDS
    }
    return H3ContinuityEvaluation(
        total_entries=total,
        accepted_shots=accepted_count,
        scored_entries=scored_count,
        unscored_entries=total - scored_count,
        first_pass_usable_rate=first_pass / total if total else 0.0,
        attempts_per_accepted_shot=(
            attempt_count / accepted_count if accepted_count else 0.0
        ),
        boundary_match_rate=(
            boundary_matches / scored_count if scored_count else 0.0
        ),
        lock_violation_rates={
            key: count / scored_count if scored_count else 0.0
            for key, count in violation_counts.items()
        },
    )
```

缺少新证据的 legacy Manifest 计入 `unscored`，不能当作通过；报告额外返回 `scored_entries` 和 `unscored_entries`。基线文件由调用者显式传入，不扫描整个项目目录。

- [ ] **步骤 4：更新视频流水线文档**

在 `07-video.md` 增加：

- Contract → risk → mode → Bundle → Manifest 数据流；
- 四种 `continuity_policy` 的行为表；
- S2/I2/M2/C2 的不同处置；
- Base 与 Ref 的能力差异；
- Postflight planned/observed 规则；
- 离线基线评测调用示例；
- Ref `hybrid_input_unverified` 的限制和付费烟测必须单独授权。

- [ ] **步骤 5：运行聚焦测试与文档检查**

```bash
uv run pytest tests/shot_continuity tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_quality.py tests/media_capabilities/video/test_h3_timeline.py tests/media_capabilities/video/test_workflow_registry.py tests/test_task_narrative_group_video_runner.py tests/test_api_narrative_groups.py -q
uv run ruff check src/novelvideo/shot_continuity src/novelvideo/media_capabilities/video/h3_prompt_profile.py src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/task_backend/runners/narrative_group_video.py src/novelvideo/api/routes/narrative_groups.py
pnpm --dir frontend test -- group-video-prompt-drawer.test.tsx
pnpm --dir frontend build
git diff --check
```

预期：全部命令 exit 0。

- [ ] **步骤 6：运行后端 H3 回归套件**

```bash
uv run pytest tests/media_capabilities/video tests/test_runninghub_h3_video_generator.py tests/test_api_runninghub_h3_backend.py tests/test_compose_episode_h3_director.py tests/test_episode_export_h3_director.py -q
```

预期：全部 PASS；不调用真实 RunningHub。

- [ ] **步骤 7：Commit**

```bash
git add src/novelvideo/shot_continuity/evaluation.py tests/shot_continuity/test_evaluation.py docs/cookbook/pipelines/07-video.md
git commit -m "docs: add H3 continuity evaluation and rollout guide"
```

## 3. 规格覆盖矩阵

| 规格主题 | 实现任务 |
|---|---|
| Contract 类型、事实来源、revision | 任务 1–3 |
| DirectorPlan 与 Director World 投影 | 任务 3、7 |
| S/I/M/C 独立风险与不同处置 | 任务 4、7 |
| I2VA / FL2VA / reject | 任务 4、7 |
| Base/Ref 共享 Compiler 与 Bundle | 任务 5、8 |
| Prompt 密度、动作顺序、static camera | 任务 5 |
| planned/observed state、局部依赖链 | 任务 2、7、9 |
| 错误分类、阻断与 attempt 证据 | 任务 6、7 |
| Manifest v2、v1 兼容与 replay | 任务 6 |
| Ref capability gate | 任务 8 |
| Postflight 人工验收 | 任务 9 |
| 基线、指标与灰度发布文档 | 任务 7、10 |

## 4. 最终验收清单

- [ ] 默认 `continuity_policy=legacy` 时，现有 Base H3 输入、Prompt 和 Provider 行为保持不变。
- [ ] `observe` 写入 Contract、S/I/M/C、模式建议和 shadow Bundle，但不改变付费 payload。
- [ ] `guard` 对确定性 blocker 在上传前停止，Manifest 标记 `transport_called=false`。
- [ ] `enforce` 只在无 blocker 时使用新模式与 Bundle prompt。
- [ ] Contract 按逻辑 shot 版本化；双镜头 segment 冻结两个有序 Contract refs。
- [ ] `planned_carry_out` 与 `observed_carry_out` 始终分开，接受偏差会产生新 revision。
- [ ] S2 只要求/使用 Director World 证据；I2 只要求 Ref；M2 只要求拆镜/简化；C2 只提高边界约束。
- [ ] static camera 是合法选择，动态 camera 仍要求完整运动参数。
- [ ] H3 Ref 在未完成真实混合输入验证前只能编译和展示为不可用，不能 resolve 或静默回退 Base。
- [ ] Manifest v1 可读，Manifest v2 冻结 Contract、risk、decision、Bundle、attempt 与 postflight。
- [ ] 所有自动测试不产生付费任务；真实烟测需要用户另行授权。
- [ ] 工作区其他用户改动未被暂存、格式化、恢复或提交。

## 5. 实施时的提交序列

预期形成以下独立提交，任何一步失败都可以单独回退：

1. `feat: add shot continuity domain contracts`
2. `feat: persist shot continuity revisions`
3. `feat: build continuity contracts from director plans`
4. `feat: audit H3 shot risk and endpoint reachability`
5. `feat: compile continuity-aware H3 bundles`
6. `feat: freeze H3 continuity evidence in manifests`
7. `feat: add staged H3 continuity policies`
8. `feat: gate H3 Ref continuity compilation`
9. `feat: review H3 continuity evidence postflight`
10. `docs: add H3 continuity evaluation and rollout guide`
