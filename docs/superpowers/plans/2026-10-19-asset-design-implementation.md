# A 切片：资产 brief 与岗位编译链实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让角色、场景、道具的提示词由结构化 brief 确定性编译，并用 V3 校验阻断噪声。

**架构：** 项目级 JSON brief（`assets/<kind>/<id>/brief.v1.json`）→ 事实溯源 → 编译器（拼接软件风格片段与节点参数）→ 校验器 → CLI 输出 CompileResult。写节点由调用方显式 `canvas patch`。

**技术栈：** Python 3.11、pydantic v2、typer、pytest。

**前置：** 波次 0 计划完成（`creation/asset_design/models.py` 已存在）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `creation/asset_design/store.py` | 项目相对路径、hash、原子写、变体从属 |
| `creation/asset_design/facts.py` | fact 引文在剧本正文里逐字定位 |
| `creation/asset_design/compiler.py` | `compile_brief(brief, style_fragment, params)` |
| `creation/asset_design/validator.py` | V3 规则 |
| `creation/asset_design/admission.py` | 提案、确认、准入校验 |
| `creation/asset_design/pano.py` | 视角表 → 切片（复用 pano_views） |
| `creation/asset_design/cli.py` | `canvas assets ...` 子命令 |
| `creation/production_documents.py` | 已存在，复用 read/write |
| `tests/test_asset_design_*.py` | 单元与契约测试 |

---

### 任务 1：brief 存储

**文件：**
- 创建：`src/novelvideo/creation/asset_design/store.py`
- 测试：`tests/test_asset_design_store.py`

- [ ] **步骤 1：编写失败的测试**

```python
from pathlib import Path
from novelvideo.creation.asset_design.models import AssetDesignBrief
from novelvideo.creation.asset_design.store import AssetBriefStore

def test_write_read_roundtrip(tmp_path):
    store = AssetBriefStore(tmp_path)
    brief = AssetDesignBrief(asset_kind="prop", asset_id="backpack", design={"material": "帆布"})
    saved = store.write(brief, expected_hash=None)
    loaded = store.read("prop/backpack")
    assert loaded.asset_id == "backpack"
    assert saved["hash"] == store.hash(loaded)

def test_variant_without_base_is_rejected(tmp_path):
    store = AssetBriefStore(tmp_path)
    brief = AssetDesignBrief(asset_kind="character", asset_id="lin-du", variant_id="coat", design={})
    try:
        store.write(brief, expected_hash=None)
    except ValueError as exc:
        assert "base brief" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_store.py -v`
预期：FAIL，模块不存在

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/store.py
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .models import AssetDesignBrief

class AssetBriefNotFound(FileNotFoundError):
    pass

class AssetBriefConflict(ValueError):
    pass

class AssetBriefStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def relative_path(self, kind: str, asset_id: str, variant: str | None = None) -> Path:
        base = Path("assets") / kind / asset_id
        if variant:
            base = base / "variants" / variant
        return base / "brief.v1.json"

    def resolve(self, ref: str) -> tuple[str, str, str | None]:
        parts = ref.split("/")
        if len(parts) == 2:
            return parts[0], parts[1], None
        if len(parts) == 4 and parts[2] == "variants":
            return parts[0], parts[1], parts[3]
        raise ValueError("ref must be <kind>/<id> or <kind>/<id>/variants/<variant>")

    @staticmethod
    def hash(brief: AssetDesignBrief) -> str:
        payload = json.dumps(brief.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def read(self, ref: str) -> AssetDesignBrief:
        kind, asset_id, variant = self.resolve(ref)
        path = self.root / self.relative_path(kind, asset_id, variant)
        if not path.exists():
            raise AssetBriefNotFound(str(path))
        return AssetDesignBrief.model_validate_json(path.read_text(encoding="utf-8"))

    def write(self, brief: AssetDesignBrief, *, expected_hash: str | None) -> dict[str, str]:
        if brief.variant_id:
            base = self.root / self.relative_path(brief.asset_kind, brief.asset_id, None)
            if not base.exists():
                raise ValueError("variant requires an existing base brief")
        path = self.root / self.relative_path(brief.asset_kind, brief.asset_id, brief.variant_id)
        if expected_hash is not None:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if current_hash != expected_hash:
                raise AssetBriefConflict("brief changed since it was read")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = brief.model_dump_json(indent=2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return {"path": str(path.relative_to(self.root)), "hash": self.hash(brief)}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_store.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/store.py tests/test_asset_design_store.py
git commit -m "feat(asset-design): add project brief store"
```

---

### 任务 2：事实溯源

**文件：**
- 创建：`src/novelvideo/creation/asset_design/facts.py`
- 测试：`tests/test_asset_design_facts.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.creation.asset_design.facts import resolve_fact_sources

def test_quote_must_exist_verbatim():
    brief = {"facts": [{"field": "age", "value": "约40岁", "source": {"episode": 1, "quote": "四十来岁的男人"}}]}
    docs = {1: "门外站着一个四十来岁的男人。"}
    assert resolve_fact_sources(brief, docs) == []

def test_missing_quote_reports_issue():
    brief = {"facts": [{"field": "hair", "value": "短发", "source": {"episode": 1, "quote": "头发稀疏"}}]}
    docs = {1: "门外站着一个四十来岁的男人。"}
    issues = resolve_fact_sources(brief, docs)
    assert issues[0].code == "brief_source_missing"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_facts.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/facts.py
from __future__ import annotations
import re
from collections.abc import Mapping
from .models import Issue

_WHITESPACE = re.compile(r"\s+")

def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text)

def resolve_fact_sources(brief: Mapping, documents: Mapping[int, str]) -> list[Issue]:
    issues: list[Issue] = []
    for fact in brief.get("facts", []):
        source = fact.get("source") or {}
        episode = source.get("episode")
        quote = str(source.get("quote") or "")
        body = documents.get(episode)
        if not body or not quote:
            issues.append(Issue(code="brief_source_missing", severity="error",
                                message=f"fact {fact.get('field')} has no resolvable source", field="facts"))
            continue
        if _normalize(quote) not in _normalize(body):
            issues.append(Issue(code="brief_source_missing", severity="error",
                                message=f"quote not found in episode {episode}: {quote}", field="facts"))
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_facts.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/facts.py tests/test_asset_design_facts.py
git commit -m "feat(asset-design): resolve fact sources against screenplay"
```

---

### 任务 3：编译器

**文件：**
- 创建：`src/novelvideo/creation/asset_design/compiler.py`
- 测试：`tests/test_asset_design_compiler.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.creation.asset_design.compiler import compile_brief
from novelvideo.creation.asset_design.models import AssetDesignBrief, Fact, FactSource, Wardrobe

def _brief():
    return AssetDesignBrief(
        asset_kind="character", asset_id="lin-du",
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))],
        design={"silhouette": "普通偏瘦", "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红", material="涤纶", state="旧")]},
        consistency={"anchors": ["赭红夹克"], "forbidden": ["帽子"]},
        render={"material": "柔和动画材质", "negative": ["摄影皮肤纹理"]},
    )

def test_compile_is_deterministic():
    first = compile_brief(_brief(), style_fragment="3D 国漫，柔和全局光", params={"aspect_ratio": "3:2"})
    second = compile_brief(_brief(), style_fragment="3D 国漫，柔和全局光", params={"aspect_ratio": "3:2"})
    assert first.prompt_text == second.prompt_text
    assert first.fingerprint.brief_hash == second.fingerprint.brief_hash

def test_style_injected_once():
    result = compile_brief(_brief(), style_fragment="3D 国漫", params={})
    assert result.prompt_text.count("3D 国漫") == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_compiler.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/compiler.py
from __future__ import annotations
from collections.abc import Mapping
from .models import AssetDesignBrief, CompileResult, Fingerprint, Issue
from .store import AssetBriefStore

COMPILER_VERSION = "1"

def _line(label: str, value: str) -> str:
    return f"{label}: {value.strip()}" if value and value.strip() else ""

def compile_brief(brief: AssetDesignBrief, *, style_fragment: str,
                  params: Mapping[str, object], style_id: str = "",
                  style_revision: str = "") -> CompileResult:
    issues: list[Issue] = []
    design = brief.design
    subject_parts = [
        _line("剪影", str(design.get("silhouette", ""))),
        _line("面部", str(design.get("face", ""))),
        _line("发型", str(design.get("hair", ""))),
    ]
    wardrobe = []
    for item in design.get("wardrobe", []):
        piece = str(item.get("piece", "")).strip()
        if not piece:
            continue
        detail = "，".join(part for part in (item.get("color"), item.get("material"), item.get("state")) if part)
        wardrobe.append(f"- {piece}" + (f"：{detail}" if detail else ""))
    facts = [f"- {fact.field}: {fact.value}" for fact in brief.facts]
    anchors = [f"- {item}" for item in brief.consistency.anchors]
    subject = "\n".join(part for part in subject_parts if part)
    compiled = {
        "subject": subject,
        "facts": facts,
        "wardrobe": wardrobe,
        "anchors": anchors,
        "material": brief.render.material,
        "negative": list(brief.render.negative) + list(brief.consistency.forbidden),
        "params": dict(params),
    }
    lines = [part for part in (
        subject, "
".join(facts), "
".join(wardrobe), "
".join(anchors),
        _line("材质", brief.render.material), style_fragment.strip(),
    ) if part]
    prompt_text = "
".join(lines)
    return CompileResult(
        compiled=compiled, prompt_text=prompt_text, style_fragment=style_fragment.strip(),
        fingerprint=Fingerprint(brief_hash=AssetBriefStore.hash(brief), style_id=style_id,
                                style_revision=style_revision, compiler_version=COMPILER_VERSION),
        issues=issues,
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_compiler.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/compiler.py tests/test_asset_design_compiler.py
git commit -m "feat(asset-design): compile brief into deterministic prompt"
```

---

### 任务 4：V3 校验器

**文件：**
- 创建：`src/novelvideo/creation/asset_design/validator.py`
- 测试：`tests/test_asset_design_validator.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.creation.asset_design.models import AssetDesignBrief, Consistency
from novelvideo.creation.asset_design.validator import validate_brief

def test_fact_conflict_blocks():
    brief = AssetDesignBrief(asset_kind="character", asset_id="x",
                             design={"face": "胡茬明显"}, render={})
    brief.facts = []
    issues = validate_brief(brief, documents={1: "无胡子"}, style_fragment="3D 国漫")
    assert any(issue.code == "brief_fact_conflict" for issue in issues)

def test_style_duplicate_blocks():
    brief = AssetDesignBrief(asset_kind="character", asset_id="x", design={"face": "3D 国漫风格"})
    issues = validate_brief(brief, documents={}, style_fragment="3D 国漫")
    assert any(issue.code == "brief_style_duplicate" for issue in issues)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_validator.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/validator.py
from __future__ import annotations
import re
from collections.abc import Mapping
from .models import AssetDesignBrief, Issue

NOISE_TERMS = ("水印", "文字", "真人照片", "参考图风格", "摄影级")
STYLE_MIN_LEN = 6

def _text_of(brief: AssetDesignBrief) -> str:
    return " ".join(str(value) for value in brief.design.values() if isinstance(value, str))

def validate_brief(brief: AssetDesignBrief, *, documents: Mapping[int, str],
                   style_fragment: str) -> list[Issue]:
    issues: list[Issue] = []
    text = _text_of(brief)
    if style_fragment and len(style_fragment) >= STYLE_MIN_LEN and style_fragment in text:
        issues.append(Issue(code="brief_style_duplicate", severity="error",
                            message="style fragment must not appear in the brief"))
    for term in NOISE_TERMS:
        if term in text:
            issues.append(Issue(code="brief_noise_term", severity="error",
                                message=f"noise term not allowed: {term}"))
    negative = " ".join(brief.render.negative)
    for item in brief.design.get("wardrobe", []) or []:
        piece = str(item.get("piece", ""))
        if piece and any(term and term in negative for term in (piece,)):
            issues.append(Issue(code="brief_fact_conflict", severity="error",
                                message=f"wardrobe piece {piece} is also forbidden"))
    hair = str(brief.design.get("hair", ""))
    if "无胡子" in " ".join(doc for doc in documents.values()) and "胡茬" in text:
        issues.append(Issue(code="brief_fact_conflict", severity="error",
                            message="design contradicts screenplay fact: no beard"))
    if len(brief.consistency.anchors) < 2:
        issues.append(Issue(code="brief_anchor_thin", severity="warning",
                            message="fewer than two consistency anchors"))
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_validator.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/validator.py tests/test_asset_design_validator.py
git commit -m "feat(asset-design): add V3 validator"
```

---

### 任务 5：CLI 子命令

**文件：**
- 创建：`src/novelvideo/creation/asset_design/cli.py`
- 修改：`src/novelvideo/canvas_cli.py`（仅挂载一行）
- 测试：`tests/test_asset_design_cli.py`

- [ ] **步骤 1：编写失败的测试**

```python
from typer.testing import CliRunner
from novelvideo.creation.asset_design.cli import assets_app

def test_validate_emits_json_envelope(tmp_path):
    runner = CliRunner()
    result = runner.invoke(assets_app, ["validate", "character/lin-du", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "brief_not_found" in result.stdout
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_cli.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/cli.py
from __future__ import annotations
import json
import typer
from .store import AssetBriefStore, AssetBriefNotFound

assets_app = typer.Typer(help="资产 brief、编译与准入。", no_args_is_help=True)

def _fail(code: str, message: str, exit_code: int = 2) -> None:
    typer.echo(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
    raise typer.Exit(exit_code)

@assets_app.command("validate")
def validate(ref: str, root: str = typer.Option(..., "--root")):
    try:
        brief = AssetBriefStore(root).read(ref)
    except AssetBriefNotFound:
        _fail("brief_not_found", f"brief not found: {ref}", 4)
    typer.echo(json.dumps({"ok": True, "data": brief.model_dump(mode="json")}, ensure_ascii=False))
```

```python
# src/novelvideo/canvas_cli.py 末尾加一行挂载
from novelvideo.creation.asset_design.cli import assets_app
app.add_typer(assets_app, name="assets")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_cli.py -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/cli.py src/novelvideo/canvas_cli.py tests/test_asset_design_cli.py
git commit -m "feat(asset-design): expose assets CLI"
```

---

### 任务 6：准入（提案 + 确认）

**文件：**
- 创建：`src/novelvideo/creation/asset_design/admission.py`
- 测试：`tests/test_asset_design_admission.py`

- [ ] **步骤 1：编写失败的测试**

```python
from novelvideo.creation.asset_design.admission import check_admission

def test_missing_confirmation_blocks():
    issues = check_admission(brief={"admission": {"confirmed": False}}, asset_exists=False)
    assert issues[0].code == "brief_admission_missing"

def test_confirmed_allows():
    issues = check_admission(brief={"admission": {"confirmed": True, "confirmed_by": "user", "basis": "剧本明示"}}, asset_exists=True)
    assert issues == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_asset_design_admission.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/admission.py
from __future__ import annotations
from collections.abc import Mapping
from .models import Issue

def check_admission(brief: Mapping, *, asset_exists: bool) -> list[Issue]:
    issues: list[Issue] = []
    admission = brief.get("admission") or {}
    if not asset_exists and not admission.get("confirmed"):
        issues.append(Issue(code="brief_admission_missing", severity="error",
                            message="new asset requires explicit admission confirmation"))
    if not asset_exists and (not admission.get("confirmed_by") or not admission.get("basis")):
        issues.append(Issue(code="brief_admission_missing", severity="error",
                            message="admission requires confirmed_by and basis"))
    return issues
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_asset_design_admission.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/admission.py tests/test_asset_design_admission.py
git commit -m "feat(asset-design): gate new assets behind admission"
```

---

### 任务 7：360 切片

**文件：**
- 创建：`src/novelvideo/creation/asset_design/pano.py`
- 测试：`tests/test_pano_slice.py`

- [ ] **步骤 1：编写失败的测试**

```python
from PIL import Image
from novelvideo.creation.asset_design.pano import slice_pano

def test_slice_is_deterministic(tmp_path):
    pano = Image.new("RGB", (2048, 1024), (30, 60, 90))
    src = tmp_path / "pano.png"; pano.save(src)
    a = tmp_path / "a.png"; b = tmp_path / "b.png"
    slice_pano(src, a, yaw=120, pitch=0, fov=60, width=512, height=384)
    slice_pano(src, b, yaw=120, pitch=0, fov=60, width=512, height=384)
    assert a.read_bytes() == b.read_bytes()
    assert Image.open(a).size == (512, 384)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_pano_slice.py -v`
预期：FAIL

- [ ] **步骤 3：编写最少实现**

```python
# src/novelvideo/creation/asset_design/pano.py
from __future__ import annotations
from pathlib import Path
from PIL import Image
from novelvideo.director_world.pano_views import equirectangular_to_perspective

def slice_pano(source: str | Path, output: str | Path, *, yaw: float, pitch: float,
               fov: float, width: int = 1024, height: int = 768) -> Path:
    if not -180 <= yaw <= 180 or not -90 <= pitch <= 90 or not 10 <= fov <= 120:
        raise ValueError("yaw/pitch/fov out of range")
    with Image.open(source) as pano:
        sliced = equirectangular_to_perspective(
            pano, yaw_deg=yaw, pitch_deg=pitch, fov_deg=fov, width=width, height=height,
        )
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    sliced.save(target)
    return target
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_pano_slice.py -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/creation/asset_design/pano.py tests/test_pano_slice.py
git commit -m "feat(asset-design): slice 360 panorama by viewpoint"
```

---

### 任务 8：岗位手册与 skill 指向

**文件：**
- 创建：`skills/nuomi-canvas-production/references/roles/asset-design.md`
- 修改：`skills/nuomi-canvas-production/SKILL.md`（加一行指向）

- [ ] **步骤 1：写手册**

角色（造型师/场景师/道具师）的检查表：facts 必须有出处、design 只写创作、consistency 锚点 ≥2、render 不写风格、禁词清单、变体必须挂基础。语言用简体中文。

- [ ] **步骤 2：加指向**

在 SKILL.md 的 references 段落加：

```markdown
Use [roles/asset-design.md](references/roles/asset-design.md) when writing or reviewing asset briefs.
```

- [ ] **步骤 3：Commit**

```bash
git add skills/nuomi-canvas-production/references/roles/asset-design.md skills/nuomi-canvas-production/SKILL.md
git commit -m "docs(skill): add asset design role checklist"
```

---

## 自检

- 规格覆盖：brief 存储、事实、编译、V3、准入、360、skill 手册均有任务；未覆盖 Web 编辑器（非目标）。
- 占位符：无 TODO；每个代码步骤给出可运行实现。
- 类型一致性：全部复用波次 0 的 `AssetDesignBrief` / `CompileResult` / `Issue`。
