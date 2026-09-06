# Identity Sheet v2 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将所有新生成的 Identity 全面切换为“3/4 大肖像 + 无头正面全身 + 背面全身”的 `identity_sheet_v2`，只在质量检查通过后进入可采用资产版本。

**架构：** 新建一个小型 `identity_sheet` 领域模块，集中定义布局、风格分支、确定性拼版和质量报告；Nano Banana 与队列生成链路共享同一提示词契约。队列先把模型输出写入候选临时文件，再用已确认 Portrait 覆盖肖像格、遮罩正面头部安全区、执行视觉 QC，最后注册 Production Workflow 版本；QC 失败的版本保留可查看但不替换正式 Identity。

**技术栈：** Python 3.11、Pydantic、Pillow、现有图像生成/视觉模型网关、FastAPI、React/TypeScript、TanStack Query、pytest、Vitest

---

## 文件结构

- 创建 `src/novelvideo/character_visual/identity_sheet.py`：v2 布局常量、风格分类、共享提示词、确定性拼版及质量报告类型。
- 创建 `src/novelvideo/character_visual/identity_sheet_qc.py`：调用现有多模态模型做结构/身份/风格检查，并将响应收敛成稳定问题码。
- 修改 `src/novelvideo/generators/nanobanana_character.py`：用共享 v2 提示词替换旧 front/side/back 契约。
- 修改 `src/novelvideo/task_backend/runners/character_image.py`：强制已确认脸部母版、单次生成、拼版、QC、候选注册和元数据持久化。
- 修改 `src/novelvideo/freezone/presets.py`：在 v2 节点和下游引用元数据中声明 Portrait 格是唯一脸源。
- 修改 `frontend/src/lib/queries/production-assets.ts`：为 v2 质量报告元数据增加类型。
- 修改 `frontend/src/components/assets/character-state-versions.tsx`：显示 v2 三格职责、说明和逐项 QC 结果。
- 修改 `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`：新增中英文说明与错误文案。
- 创建 `tests/character_visual/test_identity_sheet.py`：布局、风格、提示词、遮罩和“零额外模型调用”单元测试。
- 创建 `tests/character_visual/test_identity_sheet_qc.py`：视觉 QC 解析及不同风格门槛测试。
- 修改 `tests/test_character_state_assets.py`：任务链路、失败不覆盖、版本元数据和旧资产兼容测试。
- 修改 `tests/test_newapi_image_gateway.py`：供应商请求仍只调用一次且引用图顺序稳定。
- 创建 `frontend/src/components/assets/character-state-versions.test.tsx`：v1/v2 与 QC 界面测试。

### 任务 1：建立 v2 布局、风格和质量报告契约

**文件：**
- 创建：`src/novelvideo/character_visual/identity_sheet.py`
- 创建：`tests/character_visual/test_identity_sheet.py`

- [ ] **步骤 1：编写失败测试，锁定布局版本和风格分支**

```python
from novelvideo.character_visual.identity_sheet import (
    IDENTITY_SHEET_LAYOUT_VERSION,
    IdentitySheetStyleFamily,
    classify_identity_sheet_style,
)


def test_identity_sheet_v2_contract_and_style_families():
    assert IDENTITY_SHEET_LAYOUT_VERSION == "identity_sheet_v2"
    assert classify_identity_sheet_style("anime_2d") is IdentitySheetStyleFamily.TWO_D
    assert classify_identity_sheet_style("stylized_2_5d") is IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    assert classify_identity_sheet_style("realistic_3d") is IdentitySheetStyleFamily.THREE_D_REALISTIC
    assert classify_identity_sheet_style("pixar_like_3d") is IdentitySheetStyleFamily.THREE_D_STYLIZED
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py -q`

预期：FAIL，报错 `ModuleNotFoundError: novelvideo.character_visual.identity_sheet`。

- [ ] **步骤 3：实现最小领域契约**

```python
from enum import StrEnum
from pydantic import BaseModel, Field

IDENTITY_SHEET_LAYOUT_VERSION = "identity_sheet_v2"
IDENTITY_SHEET_PANEL_LAYOUT = ("portrait_3q", "front_headless", "back_fullbody")


class IdentitySheetStyleFamily(StrEnum):
    TWO_D = "2d"
    TWO_POINT_FIVE_D = "2.5d"
    THREE_D_REALISTIC = "3d_realistic"
    THREE_D_STYLIZED = "3d_stylized"


class IdentitySheetQualityReport(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    style_family: IdentitySheetStyleFamily


def classify_identity_sheet_style(style: str) -> IdentitySheetStyleFamily:
    value = style.lower().replace("-", "_")
    if "2_5d" in value or "2.5d" in value:
        return IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    if "anime" in value or value.endswith("2d"):
        return IdentitySheetStyleFamily.TWO_D
    if "realistic" in value and "3d" in value:
        return IdentitySheetStyleFamily.THREE_D_REALISTIC
    return IdentitySheetStyleFamily.THREE_D_STYLIZED
```

实现时优先使用 `StyleService.get_style_branch()` 返回值；字符串分类只作为自定义风格缺少明确 medium 时的兼容回退。

- [ ] **步骤 4：运行测试确认通过**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py -q`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/identity_sheet.py tests/character_visual/test_identity_sheet.py
git commit -m "feat(characters): define identity sheet v2 contract"
```

### 任务 2：用共享提示词契约全面替换旧三视图

**文件：**
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`src/novelvideo/generators/nanobanana_character.py:54-105`
- 修改：`tests/character_visual/test_identity_sheet.py`

- [ ] **步骤 1：编写失败测试，覆盖三格职责和中性展示**

```python
def test_v2_prompt_has_one_face_source_and_no_cinematic_baking():
    prompt = build_identity_sheet_v2_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance="深蓝长袍",
        project_style="anime_2d",
        style_instructions="clean ink linework and flat cel colors",
        avoid_instructions="no photorealism",
        ethnicity="Chinese",
        has_costume_reference=False,
    )
    assert "LARGE THREE-QUARTER PORTRAIT" in prompt
    assert "HEADLESS FRONT FULL BODY" in prompt
    assert "BACK FULL BODY" in prompt
    assert "the only visible face source" in prompt
    assert "no wound, blood, gore, or horror" in prompt
    assert "neutral gray background" in prompt
    assert "no film grain" in prompt and "no cinematic lens" in prompt
    assert "visible pores" not in prompt
```

再增加 2.5D、写实 3D、非写实 3D 参数化断言：2D 检查线条/色块/瞳孔高光；2.5D/写实 3D 检查自然材质、眼神光和非塑料感；非写实 3D 不被强制真人皮肤。

- [ ] **步骤 2：运行测试并确认旧提示词失败**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py -q`

预期：FAIL，旧提示词仍包含 `FRONT VIEW / SIDE VIEW / BACK VIEW`。

- [ ] **步骤 3：实现共享编译器并保留旧函数名兼容调用方**

```python
def build_identity_sheet_v2_prompt(*, character_name: str, character_tag: str,
                                   appearance: str, project_style: str,
                                   style_instructions: str, avoid_instructions: str,
                                   ethnicity: str, has_costume_reference: bool) -> str:
    family = classify_identity_sheet_style(project_style)
    quality = style_quality_instructions(family)
    return f"""Identity Sheet v2 for {character_tag} ({character_name}).
LEFT 50%: LARGE THREE-QUARTER PORTRAIT; this is the only visible face source.
CENTER 25%: HEADLESS FRONT FULL BODY, neck-up empty, no wound, blood, gore, or horror.
RIGHT 25%: BACK FULL BODY, facing fully away, no turned face or reflection.
Use one neutral gray background and flat even light. {quality}
Preserve the configured project style: {style_instructions}.
No text, labels, props, environment, action, film grain, cinematic lens, depth of field,
dramatic lighting, LUT, or scene color cast. Appearance: {appearance}.
Additional exclusions: {avoid_instructions}. Ethnicity when unspecified: {ethnicity}."""
```

让 `build_character_state_sheet_prompt()` 成为薄兼容包装并返回 v2 契约；删除旧 `CHARACTER_STATE_PANEL_LAYOUT` 的 front/side/back 语义，调用方统一导入新常量。

- [ ] **步骤 4：运行提示词及网关测试**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py tests/test_newapi_image_gateway.py -q`

预期：PASS；已有 Portrait、Costume 引用顺序测试继续通过。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/identity_sheet.py src/novelvideo/generators/nanobanana_character.py tests/character_visual/test_identity_sheet.py tests/test_newapi_image_gateway.py
git commit -m "feat(characters): replace identity prompt with v2 layout"
```

### 任务 3：实现确定性裁切、遮罩和拼版

**文件：**
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`tests/character_visual/test_identity_sheet.py`

- [ ] **步骤 1：编写像素级失败测试**

```python
def test_compose_v2_uses_confirmed_portrait_and_gray_head_safe_zone(tmp_path):
    candidate = make_three_color_candidate(tmp_path / "candidate.png")
    portrait = make_solid_image(tmp_path / "portrait.png", (12, 34, 56))
    output = tmp_path / "sheet.png"
    result = compose_identity_sheet_v2(candidate, portrait, output)
    image = Image.open(output).convert("RGB")
    assert image.size == (1536, 1024)
    assert image.getpixel((384, 512)) == (12, 34, 56)
    assert image.getpixel((900, 40)) == result.neutral_gray
    assert image.getpixel((1300, 512)) != result.neutral_gray
```

另加测试确认输入文件未被改写、输出不含 EXIF 场景信息、函数签名不接收生成器或网络客户端。

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py -q`

预期：FAIL，报错 `compose_identity_sheet_v2` 尚未定义。

- [ ] **步骤 3：用 Pillow 实现固定模板**

```python
@dataclass(frozen=True, slots=True)
class IdentitySheetComposition:
    output_path: Path
    neutral_gray: tuple[int, int, int]
    panel_bounds: dict[str, tuple[int, int, int, int]]


def compose_identity_sheet_v2(candidate_path: Path, portrait_path: Path,
                              output_path: Path) -> IdentitySheetComposition:
    canvas = Image.new("RGB", (1536, 1024), (128, 128, 128))
    bounds = {
        "portrait_3q": (0, 0, 768, 1024),
        "front_headless": (768, 0, 1152, 1024),
        "back_fullbody": (1152, 0, 1536, 1024),
    }
    # 候选图按等宽三格读取身体格；Portrait 用已确认母版直接覆盖。
    # front_headless 顶部 22% 使用从候选四角取中位数得到的中性灰填充。
    # 所有缩放均用 Image.Resampling.LANCZOS，最终只保存一次 PNG。
```

实现 `fit_crop()`、`sample_neutral_gray()` 两个纯函数；不引入修复、磨皮、生成式补绘或任何第二次图像生成调用。

- [ ] **步骤 4：运行像素级测试**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py -q`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/identity_sheet.py tests/character_visual/test_identity_sheet.py
git commit -m "feat(characters): compose identity sheet v2 deterministically"
```

### 任务 4：增加风格感知视觉 QC

**文件：**
- 创建：`src/novelvideo/character_visual/identity_sheet_qc.py`
- 创建：`tests/character_visual/test_identity_sheet_qc.py`

- [ ] **步骤 1：编写失败测试，锁定问题码和风格差异**

```python
@pytest.mark.asyncio
async def test_2d_qc_does_not_require_skin_pores(monkeypatch, tmp_path):
    monkeypatch.setattr(identity_sheet_qc, "call_freezone_vision_model", fake_json_response({
        "portrait_large": True, "front_headless": True, "back_face_hidden": True,
        "state_consistent": True, "neutral_presentation": True,
        "style_matches": True, "eyes_alive": True, "skin_texture_natural": False,
    }))
    report = await inspect_identity_sheet(tmp_path / "sheet.png", "anime_2d")
    assert report.passed is True
    assert "skin_texture_natural" not in report.checks
```

增加失败样例：正面有脸映射 `front_face_detected`、背面回头映射 `back_face_visible`、场景化光照映射 `non_neutral_presentation`、风格错误映射 `style_mismatch`；2.5D/写实 3D 才纳入 `skin_texture_natural` 与 `non_plastic_material`。

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/character_visual/test_identity_sheet_qc.py -q`

预期：FAIL，模块尚不存在。

- [ ] **步骤 3：实现结构化视觉检查**

```python
async def inspect_identity_sheet(image_path: Path, project_style: str) -> IdentitySheetQualityReport:
    family = classify_identity_sheet_style(project_style)
    _, raw = await call_freezone_vision_model(
        system_prompt=qc_system_prompt(family),
        user_prompt="Return one JSON object only.",
        image_paths=[image_path],
    )
    checks = normalize_qc_json(raw, family)
    issues = issue_codes_for_failed_checks(checks)
    return IdentitySheetQualityReport(
        passed=not issues, checks=checks, issues=issues, style_family=family
    )
```

QC 是只读视觉分析，不调用图像生成接口。JSON 缺字段、解析失败或视觉网关失败均返回 `passed=False` 和 `qc_unavailable`，防止未经检查的候选进入正式槽。

- [ ] **步骤 4：运行 QC 测试**

运行：`uv run pytest tests/character_visual/test_identity_sheet_qc.py -q`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/character_visual/identity_sheet_qc.py tests/character_visual/test_identity_sheet_qc.py
git commit -m "feat(characters): add style-aware identity sheet qc"
```

### 任务 5：接入任务生成、候选版本和正式槽保护

**文件：**
- 修改：`src/novelvideo/task_backend/runners/character_image.py:13-30,220-290,403-560`
- 修改：`tests/test_character_state_assets.py`
- 修改：`tests/test_newapi_image_gateway.py`

- [ ] **步骤 1：编写失败集成测试**

```python
@pytest.mark.asyncio
async def test_failed_v2_qc_registers_viewable_candidate_without_replacing_canonical(...):
    canonical.write_bytes(b"old-v1")
    fake_generate.calls = 0
    monkeypatch.setattr(character_image, "inspect_identity_sheet", failing_qc)
    result = await character_image._run_character_image(envelope, update)
    assert canonical.read_bytes() == b"old-v1"
    assert fake_generate.calls == 1
    assert result["layout_version"] == "identity_sheet_v2"
    assert result["qc_passed"] is False
    assert result["adoption_status"] == "candidate"
```

增加通过用例：普通身份必须使用角色 Portrait；年龄变体缺 Identity Portrait 时明确失败；通过 QC 后仍先注册候选，由现有 `auto_provisional` 策略决定是否成为 current；元数据包含 `layout_version`、三格布局、`face_source`、`quality_report` 和原始候选路径。

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_character_state_assets.py tests/test_newapi_image_gateway.py -q`

预期：FAIL，当前任务会在生成后直接 `_replace_canonical_asset()`，且布局元数据仍是 `front/side/back`。

- [ ] **步骤 3：重排生成事务，确保旧正式资产不被提前改写**

```python
@dataclass(frozen=True, slots=True)
class CharacterStateGeneration:
    output_path: Path
    canonical_path: Path
    raw_candidate_path: Path
    reference_paths: tuple[Path, ...]
    prompt: str
    state_id: str
    layout_version: str
    quality_report: IdentitySheetQualityReport
```

在 `_generate_identity_image()` 中：

1. 非年龄身份要求 `assets/characters/<name>/portrait.png` 存在；年龄变体要求对应 Identity Portrait 存在，不再 prompt-only 降级。
2. 只调用一次 `_generate_grsai_image(..., aspect_ratio="3:2")` 写入 `versions/<id>.raw.png`。
3. 调用 `compose_identity_sheet_v2(raw, portrait, output)`，再调用只读 `inspect_identity_sheet(output, style)`。
4. 不调用 `_replace_canonical_asset()`；把候选和报告交给 `_register_character_state_candidate()`。
5. 注册元数据：

```python
generation_metadata={
    "layout_version": generation.layout_version,
    "panel_layout": list(IDENTITY_SHEET_PANEL_LAYOUT),
    "face_source": reference_sources[0],
    "face_source_panel": "portrait_3q",
    "quality_report": generation.quality_report.model_dump(mode="json"),
    "raw_candidate_path": raw_candidate_path,
    "canonical_path": canonical_path,
}
```

`qc_passed` 取报告值，`soft_issues` 取具体问题码。只有 Production Workflow 把通过版本设为 current 时才复制到 canonical；失败版本始终保留在版本列表但不可采用。拼版或 QC 异常也注册 `technical_error`，并保持旧 canonical 原样。

- [ ] **步骤 4：运行后端聚焦测试**

运行：`uv run pytest tests/test_character_state_assets.py tests/test_newapi_image_gateway.py tests/contract/test_m04_route_contracts.py -q`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/task_backend/runners/character_image.py tests/test_character_state_assets.py tests/test_newapi_image_gateway.py tests/contract/test_m04_route_contracts.py
git commit -m "feat(characters): publish identity v2 through qc candidates"
```

### 任务 6：更新 Freezone 和下游唯一脸源语义

**文件：**
- 修改：`src/novelvideo/freezone/presets.py:560-610,4760-4900`
- 修改：`tests/test_freezone_asset_library_backend.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_v2_identity_reference_marks_portrait_panel_as_only_face_source(...):
    refs = build_refs_for_identity(version_metadata={"layout_version": "identity_sheet_v2"})
    identity_ref = next(ref for ref in refs if ref.role == "character_identity")
    assert identity_ref.meta["layout_version"] == "identity_sheet_v2"
    assert identity_ref.meta["face_source_panel"] == "portrait_3q"
    assert identity_ref.meta["front_panel_role"] == "body_and_outfit_only"
```

再加 v1 用例：缺少 `layout_version` 时继续输出旧兼容引用，不猜测图片内容。

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_freezone_asset_library_backend.py -q`

预期：FAIL，当前引用元数据只有 character/identity/source_role。

- [ ] **步骤 3：透传版本化引用说明**

```python
if layout_version == "identity_sheet_v2":
    meta.update({
        "layout_version": layout_version,
        "face_source_panel": "portrait_3q",
        "front_panel_role": "body_and_outfit_only",
        "back_panel_role": "silhouette_and_outfit_back_only",
    })
```

Identity 工作流节点改用 `3:2`，提示词直接调用共享 v2 编译器；禁止自由区自动提交未带 QC 报告的 v2 候选。v1 节点保持现有行为。

- [ ] **步骤 4：运行 Freezone 测试**

运行：`uv run pytest tests/test_freezone_asset_library_backend.py -q`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/novelvideo/freezone/presets.py tests/test_freezone_asset_library_backend.py
git commit -m "feat(freezone): consume identity v2 as single-face reference"
```

### 任务 7：更新 Identity 版本界面和中英文文案

**文件：**
- 修改：`frontend/src/lib/queries/production-assets.ts`
- 修改：`frontend/src/components/assets/character-state-versions.tsx`
- 创建：`frontend/src/components/assets/character-state-versions.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

- [ ] **步骤 1：编写失败组件测试**

```tsx
it("explains v2 headless panel and renders concrete qc failures", async () => {
  renderVersions({
    layout_version: "identity_sheet_v2",
    panel_layout: ["portrait_3q", "front_headless", "back_fullbody"],
    quality_report: { passed: false, checks: {}, issues: ["front_face_detected"] },
  });
  expect(screen.getByText("无头正面是模型身份隔离策略，并非图片缺损。")).toBeVisible();
  expect(screen.getByText("正面身体仍检测到脸部。")) .toBeVisible();
  expect(screen.getByRole("button", { name: "采用此版本" })).toBeDisabled();
});
```

增加 v1 用例，仍显示正面/侧面/背面且不显示 v2 说明。

- [ ] **步骤 2：运行测试并确认失败**

运行：`npm --prefix frontend run test -- character-state-versions.test.tsx`

预期：FAIL，当前组件没有 v2 标签和质量报告文案映射。

- [ ] **步骤 3：实现类型、标签和失败原因映射**

```ts
type IdentitySheetQualityReport = {
  passed: boolean;
  checks: Record<string, boolean>;
  issues: string[];
  style_family: "2d" | "2.5d" | "3d_realistic" | "3d_stylized";
};

const V2_PANEL_LABELS = {
  portrait_3q: t("characters.identities.sheetV2.portrait"),
  front_headless: t("characters.identities.sheetV2.frontHeadless"),
  back_fullbody: t("characters.identities.sheetV2.back"),
};
```

从 `generation_metadata.layout_version` 分流 v1/v2；把六个规范问题码映射为已批准的具体提示，未知问题码显示原值供排障。QC 失败按钮保持禁用，候选图片仍可查看。

- [ ] **步骤 4：运行组件测试和类型检查**

运行：`npm --prefix frontend run test -- character-state-versions.test.tsx`

预期：PASS。

运行：`npm --prefix frontend run build:ce`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/lib/queries/production-assets.ts frontend/src/components/assets/character-state-versions.tsx frontend/src/components/assets/character-state-versions.test.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json
git commit -m "feat(ui): explain identity sheet v2 quality results"
```

### 任务 8：聚焦回归与验收

**文件：**
- 修改：仅修复本计划测试暴露的问题，不扩展功能范围

- [ ] **步骤 1：运行 Identity v2 后端测试集合**

运行：`uv run pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py tests/test_character_state_assets.py tests/test_newapi_image_gateway.py tests/contract/test_m04_route_contracts.py tests/test_freezone_asset_library_backend.py -q`

预期：全部 PASS。

- [ ] **步骤 2：运行前端聚焦验证**

运行：`npm --prefix frontend run test -- character-state-versions.test.tsx`

预期：PASS。

运行：`npm --prefix frontend run build:ce`

预期：PASS。

- [ ] **步骤 3：执行一次 dry-run 提示词验收**

```python
prompt = build_identity_sheet_v2_prompt(
    character_name="验收角色", character_tag="[QA]", appearance="黑色长外套",
    project_style="anime_2d", style_instructions="2D ink and flat color",
    avoid_instructions="no photorealism", ethnicity="Chinese",
    has_costume_reference=False,
)
assert "LARGE THREE-QUARTER PORTRAIT" in prompt
assert "HEADLESS FRONT FULL BODY" in prompt
assert "BACK FULL BODY" in prompt
```

运行：`uv run python -c 'from novelvideo.character_visual.identity_sheet import build_identity_sheet_v2_prompt as b; p=b(character_name="验收角色", character_tag="[QA]", appearance="黑色长外套", project_style="anime_2d", style_instructions="2D ink and flat color", avoid_instructions="no photorealism", ethnicity="Chinese", has_costume_reference=False); assert all(x in p for x in ("LARGE THREE-QUARTER PORTRAIT", "HEADLESS FRONT FULL BODY", "BACK FULL BODY"))'`

预期：退出码 0，无输出。

- [ ] **步骤 4：检查差异卫生**

运行：`git diff --check`

预期：退出码 0，无空白错误。

- [ ] **步骤 5：Commit 验收修复（仅当步骤 1-4 产生修复时）**

```bash
git add <本任务实际修复的文件>
git commit -m "fix(characters): close identity sheet v2 regressions"
```

若没有产生修复，不创建空提交。
