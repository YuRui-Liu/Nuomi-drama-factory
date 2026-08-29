# 叙事组 VIP 高分辨率宫格图实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为叙事组正式宫格图提供可持久化的 1K/2K/4K 选择，向 GRSAI VIP 发送真实像素尺寸，并在切分时可靠清除宫格边框残留。

**架构：** 新增纯函数式宫格分辨率解析器，把逻辑布局、项目画幅和模型档位转换为冻结在任务 payload 中的物理像素尺寸；GRSAI 适配器只发送已经验证的尺寸。切分后处理独立为专用模块，执行固定内缩、保守自适应边框检测、目标画幅裁切和清理报告记录；前端的模型能力与分辨率联动由共享 helper 驱动。

**技术栈：** Python 3.11、FastAPI、Pydantic、Pillow、pytest；React 19、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构

**新增文件**

- `src/novelvideo/narrative_groups/image_resolution.py`：模型能力、档位校验、逻辑比例与 GRSAI 物理像素解析。
- `src/novelvideo/narrative_groups/grid_cleanup.py`：单格固定内缩、自适应边框检测、画幅裁切与报告。
- `tests/test_narrative_group_image_resolution.py`：尺寸解析的表格化单元测试。
- `tests/test_narrative_group_grid_cleanup.py`：白/灰/黑边、真实浅色内容和画幅归一化测试。
- `frontend/src/lib/narrative-image-resolution.ts`：前端模型到合法档位及默认档位的纯函数。
- `frontend/src/__tests__/lib/narrative-image-resolution.test.ts`：前端能力联动单元测试。
- `frontend/src/components/episode/narrative-workbench/narrative-render-defaults-dialog.tsx`：项目实图默认模型与分辨率设置。
- `frontend/src/__tests__/components/episode/narrative-workbench/narrative-render-defaults-dialog.test.tsx`：项目默认设置交互测试。

**修改文件**

- `src/novelvideo/api/schemas.py`：项目媒体默认请求新增正式图档位。
- `src/novelvideo/api/routes/projects.py`：读取、校验和保存项目默认档位。
- `src/novelvideo/api/routes/narrative_groups.py`：生成请求接收档位，解析并冻结物理尺寸。
- `src/novelvideo/media_capabilities/image/grsai.py`：VIP 接受合法自定义像素，不再把档位字符串静默降级。
- `src/novelvideo/task_backend/runners/narrative_group.py`：使用冻结尺寸、采集实际尺寸、集成混合清边。
- `src/novelvideo/narrative_groups/models.py`：阶段状态增加请求/实际尺寸与清边报告。
- `src/novelvideo/narrative_groups/service.py`：保存、历史化和恢复新增阶段元数据。
- `tests/media_capabilities/image/test_grsai.py`：GRSAI VIP payload 和非法尺寸回归。
- `tests/test_project_media_defaults.py`：默认档位读取、保存与旧项目兼容。
- `tests/test_api_narrative_groups.py`：API 模型/档位校验与冻结 payload。
- `tests/test_narrative_group_runner_references.py`：runner 请求尺寸、实际尺寸和清边集成。
- `frontend/src/lib/queries/media-models.ts`：项目默认值类型和更新请求。
- `frontend/src/lib/queries/narrative-groups.ts`：本次选择类型和生成 payload。
- `frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`：本次宫格分辨率选择。
- `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：传递默认档位、保存默认值并挂载实图设置入口。
- `frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`：弹窗模型/档位联动。
- `frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`：提交和保存 payload 集成。
- `frontend/public/locales/zh/translation.json`：中文标签、提示与错误文案。
- `frontend/public/locales/en/translation.json`：英文对应文案。

## 实施约束

- 当前工作区已有大量用户修改。每次提交只暂存本任务列出的文件，不得清理、还原或格式化无关文件。
- 使用 `.venv/Scripts/python.exe` 和 `frontend` 现有 pnpm 工具链，不重新安装依赖。
- 新逻辑先写失败测试，再写最少实现。
- 不在草图链路启用高分辨率，也不修改其他图片入口。

### 任务 1：建立后端宫格分辨率领域模型

**文件：**
- 创建：`src/novelvideo/narrative_groups/image_resolution.py`
- 创建：`tests/test_narrative_group_image_resolution.py`

- [ ] **步骤 1：编写模型能力和标准尺寸失败测试**

```python
import pytest

from novelvideo.narrative_groups.image_resolution import (
    InvalidGridImageResolution,
    resolve_grid_image_resolution,
    supported_grid_image_sizes,
)


def test_supported_tiers_are_model_specific():
    assert supported_grid_image_sizes("gpt-image-2") == ("1K",)
    assert supported_grid_image_sizes("gpt-image-2-vip") == ("1K", "2K", "4K")


def test_vip_2k_9_16_2x2_uses_documented_pixels():
    result = resolve_grid_image_resolution(
        model="gpt-image-2-vip", tier="2K",
        cell_aspect_ratio="9:16", rows=2, columns=2,
    )
    assert result.logical_aspect_ratio == "9:16"
    assert result.provider_size == "1152x2048"
    assert result.requires_aspect_normalization is False


def test_regular_model_rejects_2k_without_downgrade():
    with pytest.raises(InvalidGridImageResolution, match="only supports 1K"):
        resolve_grid_image_resolution(
            model="gpt-image-2", tier="2K",
            cell_aspect_ratio="9:16", rows=2, columns=2,
        )
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_image_resolution.py -q
```

预期：FAIL，`ModuleNotFoundError: novelvideo.narrative_groups.image_resolution`。

- [ ] **步骤 3：实现类型、官方尺寸表和模型能力校验**

在 `image_resolution.py` 定义：

```python
from dataclasses import dataclass
from math import gcd, sqrt
from typing import Literal

ImageSizeTier = Literal["1K", "2K", "4K"]


class InvalidGridImageResolution(ValueError):
    pass


@dataclass(frozen=True)
class GridImageResolution:
    requested_tier: ImageSizeTier
    logical_aspect_ratio: str
    provider_aspect_ratio: str
    width: int
    height: int
    requires_aspect_normalization: bool
    reason: str = ""

    @property
    def provider_size(self) -> str:
        return f"{self.width}x{self.height}"


VIP_STANDARD_PIXELS = {
    "1K": {"1:1": (1024, 1024), "16:9": (1280, 720), "9:16": (720, 1280), "4:3": (1152, 864), "3:4": (864, 1152)},
    "2K": {"1:1": (2048, 2048), "16:9": (2048, 1152), "9:16": (1152, 2048), "4:3": (2304, 1728), "3:4": (1728, 2304)},
    "4K": {"1:1": (2880, 2880), "16:9": (3840, 2160), "9:16": (2160, 3840), "4:3": (3264, 2448), "3:4": (2448, 3264)},
}


def supported_grid_image_sizes(model: str) -> tuple[ImageSizeTier, ...]:
    return ("1K", "2K", "4K") if model == "gpt-image-2-vip" else ("1K",)
```

实现 `resolve_grid_image_resolution()`：先计算 `(cell_width * columns):(cell_height * rows)`；标准比例使用表；非标准比例按像素上限 `1_048_576 / 4_194_304 / 8_294_400` 和边长上限 `1280 / 2304 / 3840` 计算，两边向下取 16 的倍数。

- [ ] **步骤 4：补充所有固定布局和超过 3:1 的失败测试**

```python
@pytest.mark.parametrize("aspect,rows,columns", [
    ("9:16", 1, 1), ("9:16", 1, 2), ("9:16", 2, 2),
    ("9:16", 2, 3), ("9:16", 3, 3),
    ("16:9", 1, 1), ("16:9", 1, 2), ("16:9", 2, 2),
    ("16:9", 2, 3), ("16:9", 3, 3),
])
def test_all_narrative_layouts_resolve_without_changing_layout(aspect, rows, columns):
    result = resolve_grid_image_resolution(
        model="gpt-image-2-vip", tier="2K",
        cell_aspect_ratio=aspect, rows=rows, columns=columns,
    )
    assert result.width % 16 == result.height % 16 == 0
    assert max(result.width, result.height) <= 3840
    assert 655_360 <= result.width * result.height <= 8_294_400
    assert max(result.width / result.height, result.height / result.width) <= 3


def test_landscape_two_panel_clamps_physical_ratio_but_keeps_logical_ratio():
    result = resolve_grid_image_resolution(
        model="gpt-image-2-vip", tier="2K",
        cell_aspect_ratio="16:9", rows=1, columns=2,
    )
    assert result.logical_aspect_ratio == "32:9"
    assert result.provider_aspect_ratio == "3:1"
    assert result.requires_aspect_normalization is True
```

- [ ] **步骤 5：运行测试并提交**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_image_resolution.py -q
```

预期：全部 PASS。

提交：

```powershell
git add src/novelvideo/narrative_groups/image_resolution.py tests/test_narrative_group_image_resolution.py
git commit -m "feat(image): resolve narrative grid VIP sizes"
```

### 任务 2：扩展项目默认值和生成 API 契约

**文件：**
- 修改：`src/novelvideo/api/schemas.py:106-113`
- 修改：`src/novelvideo/api/routes/projects.py:704-778`
- 修改：`src/novelvideo/api/routes/narrative_groups.py:48-91, 700-830`
- 修改：`tests/test_project_media_defaults.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写默认值读取与保存失败测试**

在 `tests/test_project_media_defaults.py` 把默认 payload 断言扩展为：

```python
assert _media_defaults_payload({})["narrative_render_image_size"] == "2K"

request = MediaDefaultsRequest(
    video_model="runninghub:minimax-h3",
    narrative_render_model="gpt-image-2-vip",
    narrative_render_image_size="4K",
)
assert request.narrative_render_image_size == "4K"
```

增加 PUT 后 `project_config.json` 保存 `narrative_render_image_size` 的断言。

- [ ] **步骤 2：编写生成 API 冻结尺寸失败测试**

在 `tests/test_api_narrative_groups.py` 增加：

```python
response = client.post(
    "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
    json={
        "aspect_ratio": "9:16",
        "provider_id": "grsai-main",
        "model": "gpt-image-2-vip",
        "image_size": "2K",
    },
)
assert response.status_code == 202
payload = queued_payloads[-1]
assert payload["image_size"] == "2K"
assert payload["provider_image_size"] == "1152x2048"
assert payload["provider_aspect_ratio"] == "9:16"
```

另加普通模型提交 2K 返回 422 且不入队的测试。

- [ ] **步骤 3：运行定向测试确认失败**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_project_media_defaults.py tests\test_api_narrative_groups.py -q
```

预期：字段缺失或 payload 断言失败。

- [ ] **步骤 4：实现 Pydantic 字段和配置读写**

在 `MediaDefaultsRequest` 和 `NarrativeGroupGenerationRequest` 增加：

```python
narrative_render_image_size: Literal["1K", "2K", "4K"] = "2K"
```

以及：

```python
image_size: Literal["1K", "2K", "4K"] | None = None
```

`_media_defaults_payload()` 返回旧项目默认 `2K`；`put_project_media_defaults()` 的 `optional_bindings` 增加同名字段。

- [ ] **步骤 5：在入队前解析并冻结物理尺寸**

让 `_image_binding()` 返回 `(provider_id, model, image_size)`。正式图从请求或 `narrative_render_image_size` 读取档位；草图不读取此新字段。`_enqueue_group_action()` 在已有 layout 可用后调用：

```python
resolution = resolve_grid_image_resolution(
    model=model,
    tier=image_size,
    cell_aspect_ratio=request.aspect_ratio,
    rows=group.layout.rows,
    columns=group.layout.columns,
)
payload.update({
    "image_size": resolution.requested_tier,
    "provider_image_size": resolution.provider_size,
    "provider_aspect_ratio": resolution.provider_aspect_ratio,
    "logical_grid_aspect_ratio": resolution.logical_aspect_ratio,
    "requires_aspect_normalization": resolution.requires_aspect_normalization,
})
```

捕获 `InvalidGridImageResolution` 并返回 422，detail 使用异常消息。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_project_media_defaults.py tests\test_api_narrative_groups.py -q
git add src/novelvideo/api/schemas.py src/novelvideo/api/routes/projects.py src/novelvideo/api/routes/narrative_groups.py tests/test_project_media_defaults.py tests/test_api_narrative_groups.py
git commit -m "feat(api): persist narrative render resolution"
```

预期：测试全部 PASS，提交仅包含列出的五个文件。

### 任务 3：修正 GRSAI VIP 像素请求和 runner 元数据

**文件：**
- 修改：`src/novelvideo/media_capabilities/image/grsai.py:93-129`
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py:252-345`
- 修改：`tests/media_capabilities/image/test_grsai.py`
- 修改：`tests/test_narrative_group_runner_references.py`

- [ ] **步骤 1：编写 VIP 具体像素 payload 失败测试**

```python
request = ImageGenerationRequest(
    capability=MediaCapability.IMAGE_STORYBOARD_GRID,
    prompt="two-panel storyboard",
    model="gpt-image-2-vip",
    aspect_ratio="9:16",
    image_size="1152x2048",
)
await client.submit(request)
assert submitted_json["model"] == "gpt-image-2-vip"
assert submitted_json["aspectRatio"] == "1152x2048"
```

再加 VIP 收到 `"2K"` 时抛 `ValueError` 的测试，防止未来重新引入静默降级。

- [ ] **步骤 2：运行 GRSAI 测试确认失败**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\image\test_grsai.py -q
```

预期：VIP 自定义尺寸被旧 allowlist 改写或非法档位未报错。

- [ ] **步骤 3：实现 VIP 尺寸验证**

将 `_gpt_image_size()` 改为接收 `model`。VIP 分支解析 `^(\d+)x(\d+)$`，验证 16 倍数、最大边、总像素和 3:1；普通模型保留 `1024x1024 / 1024x1536 / 1536x1024`。任何显式非法值抛 `ValueError`，只有未提供尺寸时才根据 aspect ratio 使用 1K 默认值。

- [ ] **步骤 4：编写 runner 冻结尺寸和实际尺寸失败测试**

```python
assert submitted[0].image_size == "1152x2048"
assert submitted[0].aspect_ratio == "9:16"
assert result["requested_image_size"] == "2K"
assert result["requested_pixel_size"] == "1152x2048"
assert result["actual_pixel_size"] == "1152x2048"
```

测试用 Pillow 创建 1152×2048 返回图片，禁止 mock 一个没有真实宽高的空字节响应。

- [ ] **步骤 5：runner 使用冻结参数并读取下载图片宽高**

构造 `ImageGenerationRequest` 时使用：

```python
aspect_ratio=str(payload["provider_aspect_ratio"])
image_size=str(payload["provider_image_size"])
```

写入文件后用 Pillow 读取真实宽高，并将请求档位、请求像素和实际像素加入结果。若不同，增加 `resolution_warning`，但继续进入切分。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\image\test_grsai.py tests\test_narrative_group_runner_references.py -q
git add src/novelvideo/media_capabilities/image/grsai.py src/novelvideo/task_backend/runners/narrative_group.py tests/media_capabilities/image/test_grsai.py tests/test_narrative_group_runner_references.py
git commit -m "fix(grsai): send exact VIP grid pixels"
```

### 任务 4：实现混合边框清理

**文件：**
- 创建：`src/novelvideo/narrative_groups/grid_cleanup.py`
- 创建：`tests/test_narrative_group_grid_cleanup.py`

- [ ] **步骤 1：编写白、灰、黑边清理失败测试**

```python
from PIL import Image, ImageDraw
from novelvideo.narrative_groups.grid_cleanup import clean_grid_cell


@pytest.mark.parametrize("border", [(255, 255, 255), (230, 230, 230), (0, 0, 0)])
def test_clean_grid_cell_removes_uniform_separator_border(tmp_path, border):
    path = tmp_path / "cell.png"
    image = Image.new("RGB", (720, 1280), border)
    ImageDraw.Draw(image).rectangle((12, 12, 707, 1267), fill=(30, 90, 160))
    image.save(path)
    report = clean_grid_cell(path, target_aspect_ratio="9:16")
    with Image.open(path) as cleaned:
        assert cleaned.width * 16 == cleaned.height * 9
        assert cleaned.getpixel((0, cleaned.height // 2)) != border
    assert report.adaptive is True
    assert max(report.trimmed.values()) <= int(1280 * 0.03)
```

- [ ] **步骤 2：编写防止误裁白墙的失败测试**

构造边缘为带纹理浅色墙面的图片，断言只发生固定 2px 内缩，`report.adaptive is False`。

- [ ] **步骤 3：运行测试确认失败**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_grid_cleanup.py -q
```

预期：模块不存在。

- [ ] **步骤 4：实现清理报告和保守边缘检测**

定义：

```python
@dataclass(frozen=True)
class GridCellCleanupReport:
    trimmed: dict[str, int]
    adaptive: bool
    enhanced: bool
    output_size: tuple[int, int]


def clean_grid_cell(path: Path, *, target_aspect_ratio: str) -> GridCellCleanupReport:
    ...
```

实现顺序固定为：2px 内缩；扫描最多 3% 的外围行/列；只有当通道标准差低、连续长度足够且与内侧样本存在明显颜色差时才认定分隔带；中心安全裁切到目标画幅；检测到高置信度残留时最多再清理一次。使用等比裁切，不调用非等比 resize。

- [ ] **步骤 5：补充透明边、1px 抗锯齿边和输出尺寸测试**

透明 PNG 先与中性背景合成用于检测，但保存为 RGB PNG；分别断言 9:16 和 16:9 的交叉乘积严格相等。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_grid_cleanup.py -q
git add src/novelvideo/narrative_groups/grid_cleanup.py tests/test_narrative_group_grid_cleanup.py
git commit -m "feat(image): clean narrative grid borders"
```

### 任务 5：集成清边并持久化生成证据

**文件：**
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py:348-470`
- 修改：`src/novelvideo/narrative_groups/models.py:67-93`
- 修改：`src/novelvideo/narrative_groups/service.py:250-292, 700-820`
- 修改：`tests/test_narrative_group_runner_references.py`
- 修改：`tests/test_narrative_group_service.py`

- [ ] **步骤 1：编写切分集成失败测试**

生成带 8px 白色分隔线的 2×2 测试宫格，调用 `_split_existing_grid()`，逐个打开 `cell_assets`，断言目标画幅、边缘无纯白分隔带，并断言：

```python
assert len(result["cleanup_reports"]) == 4
assert all(item["output_size"] for item in result["cleanup_reports"])
```

- [ ] **步骤 2：编写阶段状态 round-trip 失败测试**

```python
record_stage_result(
    project_dir, 1, "ng-01", "render", expected_revision=1,
    status="completed",
    requested_image_size="2K",
    requested_pixel_size="1152x2048",
    actual_pixel_size="1152x2048",
    cleanup_reports=[{"cell": 0, "trimmed": {"left": 2, "right": 2, "top": 2, "bottom": 2}}],
)
state = load_groups(project_dir, 1)[0].stages["render"]
assert state.requested_image_size == "2K"
assert state.actual_pixel_size == "1152x2048"
```

- [ ] **步骤 3：运行测试确认失败**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_runner_references.py tests\test_narrative_group_service.py -q
```

- [ ] **步骤 4：集成 `clean_grid_cell()` 并重建宫格**

在 `_split_existing_grid()` 中替换 `_normalize_image_aspect()` 调用。对每个 cell 运行 `clean_grid_cell()`，将报告转成 JSON-safe dict；完成后继续调用 `_rebuild_normalized_grid()`，确保前端宫格预览来自清理后的单格。

- [ ] **步骤 5：扩展阶段状态和历史字段**

在 `GroupStageState` 增加：

```python
requested_image_size: str = ""
requested_pixel_size: str = ""
actual_pixel_size: str = ""
resolution_warning: str = ""
cleanup_reports: tuple[dict[str, Any], ...] = ()
```

同步更新 `_group_from_dict()`、`record_stage_result()`、`stage_history()` 和序列化路径。旧 sidecar 缺字段时使用空默认值。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_runner_references.py tests\test_narrative_group_service.py -q
git add src/novelvideo/task_backend/runners/narrative_group.py src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/service.py tests/test_narrative_group_runner_references.py tests/test_narrative_group_service.py
git commit -m "feat(narrative): persist grid resolution evidence"
```

### 任务 6：实现前端模型能力和请求字段

**文件：**
- 创建：`frontend/src/lib/narrative-image-resolution.ts`
- 创建：`frontend/src/__tests__/lib/narrative-image-resolution.test.ts`
- 修改：`frontend/src/lib/queries/media-models.ts:32-114`
- 修改：`frontend/src/lib/queries/narrative-groups.ts:48-58, 274-327`

- [ ] **步骤 1：编写前端能力 helper 失败测试**

```typescript
import { describe, expect, it } from "vitest";
import { allowedNarrativeRenderSizes, coerceNarrativeRenderSize } from "@/lib/narrative-image-resolution";

describe("narrative image resolution", () => {
  it("allows all VIP tiers", () => {
    expect(allowedNarrativeRenderSizes("gpt-image-2-vip")).toEqual(["1K", "2K", "4K"]);
  });
  it("coerces regular GPT images to 1K", () => {
    expect(coerceNarrativeRenderSize("gpt-image-2", "4K")).toBe("1K");
  });
  it("defaults VIP to 2K", () => {
    expect(coerceNarrativeRenderSize("gpt-image-2-vip", undefined)).toBe("2K");
  });
});
```

- [ ] **步骤 2：运行测试确认失败**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/lib/narrative-image-resolution.test.ts
```

预期：模块不存在。

- [ ] **步骤 3：实现 helper 和共享类型**

```typescript
export type NarrativeRenderImageSize = "1K" | "2K" | "4K";

export function allowedNarrativeRenderSizes(model?: string): NarrativeRenderImageSize[] {
  return model === "gpt-image-2-vip" ? ["1K", "2K", "4K"] : ["1K"];
}

export function coerceNarrativeRenderSize(model: string | undefined, value: string | undefined): NarrativeRenderImageSize {
  const allowed = allowedNarrativeRenderSizes(model);
  if (allowed.includes(value as NarrativeRenderImageSize)) return value as NarrativeRenderImageSize;
  return model === "gpt-image-2-vip" ? "2K" : "1K";
}
```

`MediaDefaults` 增加 `narrative_render_image_size`；`useUpdateMediaDefaults()` 参数增加 `narrativeRenderImageSize` 并发送 snake_case 字段。`NarrativeGroupGenerationSelection` 增加 `imageSize`，`narrativeGroupActionPayload()` 发送 `image_size`。

- [ ] **步骤 4：运行测试并提交**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/lib/narrative-image-resolution.test.ts
git add frontend/src/lib/narrative-image-resolution.ts frontend/src/__tests__/lib/narrative-image-resolution.test.ts frontend/src/lib/queries/media-models.ts frontend/src/lib/queries/narrative-groups.ts
git commit -m "feat(frontend): add narrative render size contract"
```

### 任务 7：在生成前弹窗加入分辨率选择

**文件：**
- 修改：`frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx:20-230`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx:45-205`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

- [ ] **步骤 1：编写弹窗联动失败测试**

覆盖以下行为：默认 VIP 为 2K；VIP 显示 1K/2K/4K；切换普通模型后值变为 1K；草图阶段不显示实图分辨率；提交 selection 包含 `imageSize`。

```typescript
expect(screen.getByLabelText("宫格画布分辨率")).toHaveValue("2K");
await user.selectOptions(screen.getByLabelText("本次真实模型"), "gpt-image-2");
expect(screen.getByLabelText("宫格画布分辨率")).toHaveValue("1K");
```

- [ ] **步骤 2：运行组件测试确认失败**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
```

- [ ] **步骤 3：实现弹窗选择和说明文案**

`GroupReferenceDialogProps` 增加 `defaultImageSize?: NarrativeRenderImageSize`。仅 `stage === "render"` 渲染下拉框；模型变更通过 `coerceNarrativeRenderSize()` 原子更新 model 和 imageSize。保存 checkbox 文案改成“保存模型与分辨率为项目默认值”。

- [ ] **步骤 4：接入项目默认读取与保存**

`NarrativeGroupWorkbench` 向弹窗传入 `mediaDefaults?.narrative_render_image_size`。确认提交时把 `selection.imageSize` 传给 action；勾选保存时把它传给 `updateDefaults.mutateAsync({ narrativeRenderImageSize: ... })`。其他更新默认值的调用都必须透传当前分辨率，防止切换视频模型时丢失。

- [ ] **步骤 5：运行测试并提交**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git add frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-reference-dialog.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json
git commit -m "feat(ui): select narrative grid resolution"
```

### 任务 8：增加项目实图默认设置入口

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/narrative-render-defaults-dialog.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-render-defaults-dialog.test.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

- [ ] **步骤 1：编写项目设置失败测试**

测试点击“实图设置”后显示当前 provider、model、resolution；切换 VIP + 4K 并保存时：

```typescript
expect(updateDefaults).toHaveBeenCalledWith(expect.objectContaining({
  narrativeRenderProvider: "grsai-main",
  narrativeRenderModel: "gpt-image-2-vip",
  narrativeRenderImageSize: "4K",
}));
```

另测切换普通模型后 4K 自动修正为 1K。

- [ ] **步骤 2：运行测试确认失败**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench/narrative-render-defaults-dialog.test.tsx
```

- [ ] **步骤 3：实现项目级“实图设置”弹窗**

该组件接收当前 `MediaDefaults` 和 `onSave`；只编辑 `narrative_render_provider/model/image_size`，保存时由 workbench 透传当前视频、草图字段，防止覆盖其他默认值。入口放在叙事组工作台页头的画幅选择旁，避免把项目配置塞入没有 project 上下文的全局 SettingsDialog。

- [ ] **步骤 4：运行测试并提交**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench/narrative-render-defaults-dialog.test.tsx
git add frontend/src/components/episode/narrative-workbench/narrative-render-defaults-dialog.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-render-defaults-dialog.test.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json
git commit -m "feat(ui): edit project narrative render defaults"
```

### 任务 9：回归、证据展示与真机验收

**文件：**
- 修改：`frontend/src/components/episode/narrative-workbench/group-pipeline.tsx`（若阶段卡片尚未展示元数据）
- 修改：对应现有 `group-pipeline` 测试文件
- 修改：`tests/test_narrative_group_service.py`

- [ ] **步骤 1：增加阶段证据展示测试**

当 render stage 包含 `requested_image_size="2K"`、`requested_pixel_size="1152x2048"`、`actual_pixel_size="1152x2048"` 时，阶段卡或版本详情应显示 `gpt-image-2-vip · 2K · 1152×2048`。存在 `resolution_warning` 时显示警告而非成功徽标。

- [ ] **步骤 2：实现最小展示并运行前端定向测试**

```powershell
corepack pnpm --dir frontend vitest run src/__tests__/components/episode/narrative-workbench
```

预期：全部 PASS。

- [ ] **步骤 3：运行后端聚焦回归**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_image_resolution.py tests\test_narrative_group_grid_cleanup.py tests\media_capabilities\image\test_grsai.py tests\test_project_media_defaults.py tests\test_api_narrative_groups.py tests\test_narrative_group_runner_references.py tests\test_narrative_group_service.py -q
```

预期：全部 PASS，无跳过本功能测试。

- [ ] **步骤 4：运行静态检查和差异检查**

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check src\novelvideo\narrative_groups src\novelvideo\api\routes\narrative_groups.py src\novelvideo\media_capabilities\image\grsai.py src\novelvideo\task_backend\runners\narrative_group.py tests\test_narrative_group_image_resolution.py tests\test_narrative_group_grid_cleanup.py
corepack pnpm --dir frontend exec tsc --noEmit
git diff --check
```

预期：三个命令均以 0 退出。

- [ ] **步骤 5：真机执行一次 VIP 2K 正式宫格任务**

启动当前工作区后端与前端，从叙事组工作台选择一个 2×2 正式宫格，选择 `grsai-main / gpt-image-2-vip / 2K` 并生成。验收证据必须同时满足：

```text
任务请求档位 = 2K
GRSAI 请求 aspectRatio = 1152x2048（9:16 的 2×2）或 2048x1152（16:9 的 2×2）
任务结果 actual_pixel_size 与下载文件一致
4 个 cell 均存在且画幅正确
cell 四边无白/灰/黑分隔线残余
cell_to_beat 顺序与生成前一致
```

该步骤调用真实付费供应商，不得用 mock 结果替代；若供应商返回策略违规或网络错误，保存 provider task id 和响应，修复链路问题后重试一次。

- [ ] **步骤 6：最终提交**

```powershell
git add frontend/src/components/episode/narrative-workbench/group-pipeline.tsx frontend/src/__tests__/components/episode/narrative-workbench tests/test_narrative_group_service.py
git commit -m "test: verify VIP narrative grid workflow"
```

提交前执行 `git status --short`，确认没有把原工作区中的无关修改纳入提交。

## 计划自检结果

- 规格中的临时选择、项目默认、VIP 真实像素、叙事组优先、混合清边、证据记录和真机验收均有对应任务。
- 所有新增类型在首次使用前已经定义，前后端字段统一为 API `image_size` / 配置 `narrative_render_image_size` / TypeScript `imageSize`。
- 没有把草图、角色图、场景图或自由画布纳入范围。
- 没有依赖清理或覆盖当前脏工作区。
