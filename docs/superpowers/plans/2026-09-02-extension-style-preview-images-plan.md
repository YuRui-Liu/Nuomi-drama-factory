# 扩展风格预览图实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 使用扩展风格目录中现有的风格提示词，独立生成 18 张缺失的风格预览图，并以 640×360 WebP 静态资源交付。

**架构：** `catalog.json` 继续作为风格 ID、提示词和预览路径的唯一数据源，不修改前后端接口。每个风格独立调用图像模型，采用统一中性比较场景；生成结果经过居中裁切、缩放和 WebP 编码后写入目录中既有的 `preview_asset` 路径。

**技术栈：** Codex ImageGen、Python 3.11、Pillow、pytest、Vite 静态资源目录。

---

## 文件结构

- 创建：`frontend/public/images/extension-styles/*.webp`：18 张风格预览静态资源。
- 创建：`scripts/build_extension_style_previews.py`：确定性裁切、缩放、WebP 编码和目录一致性校验。
- 创建：`tests/test_extension_style_previews.py`：验证目录覆盖、尺寸、格式、文件非空和透明边缘。

### 任务 1：建立预览资产合同测试

**文件：**
- 创建：`tests/test_extension_style_previews.py`

- [ ] **步骤 1：编写失败的覆盖与规格测试**

```python
from pathlib import Path
import json
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/novelvideo/extension_styles/catalog.json"
PUBLIC = ROOT / "frontend/public"


def test_every_extension_style_has_valid_preview_asset():
    styles = json.loads(CATALOG.read_text(encoding="utf-8"))["styles"]
    assert len(styles) == 18
    for style in styles:
        relative = style["preview_asset"].lstrip("/")
        path = PUBLIC / relative
        assert path.is_file(), style["id"]
        assert path.stat().st_size > 8_000, style["id"]
        with Image.open(path) as image:
            assert image.format == "WEBP", style["id"]
            assert image.size == (640, 360), style["id"]
            assert image.mode in {"RGB", "RGBA"}, style["id"]
```

- [ ] **步骤 2：运行测试确认资产缺失**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_extension_style_previews.py -q`

预期：FAIL，首个缺失文件为 `frontend/public/images/extension-styles/japanese-cel-animation.webp`。

- [ ] **步骤 3：仅提交合同测试**

```powershell
git add -- tests/test_extension_style_previews.py
git commit -m "Hermes: 添加扩展风格预览资产合同"
```

### 任务 2：生成 18 张独立风格源图

**文件：**
- 读取：`src/novelvideo/extension_styles/catalog.json`
- 临时创建：系统临时目录中的 18 张 ImageGen 源图

- [ ] **步骤 1：读取并锁定 18 个风格提示词**

对每个风格依次拼接 `prompt_fragment.medium`、`rendering`、`lighting`、`color`、`camera`、`constraints`，再附加统一场景：

```text
One adult fictional traveler at a layered city street corner, with clear foreground,
midground and background, architecture, one plant, visible sky and varied materials.
Create a single 16:9 comparison image. No text, title, labels, dialogue, logos,
watermark, border, split screen, collage or grid.
```

- [ ] **步骤 2：逐风格独立调用 ImageGen**

每次调用只生成一个风格，输出文件名严格取自对应 `preview_asset` 的 basename。不得复用同一生成图，也不得用一张宫格切成多个风格。

- [ ] **步骤 3：核对生成数量和命名**

预期：18 个不同源图文件，basename 与目录中 18 个 `preview_asset` 一一对应。

### 任务 3：确定性处理并写入前端静态目录

**文件：**
- 创建：`scripts/build_extension_style_previews.py`
- 创建：`frontend/public/images/extension-styles/*.webp`

- [ ] **步骤 1：实现无拉伸的居中裁切与 WebP 编码**

```python
from pathlib import Path
from PIL import Image


def convert_preview(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        target_ratio = 16 / 9
        if width / height > target_ratio:
            crop_width = round(height * target_ratio)
            left = (width - crop_width) // 2
            image = image.crop((left, 0, left + crop_width, height))
        elif width / height < target_ratio:
            crop_height = round(width / target_ratio)
            top = (height - crop_height) // 2
            image = image.crop((0, top, width, top + crop_height))
        image = image.resize((640, 360), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", quality=88, method=6)
```

- [ ] **步骤 2：为 18 个源图执行转换**

运行：`\.venv\Scripts\python.exe scripts/build_extension_style_previews.py --source-dir <ImageGen输出目录>`

预期：`frontend/public/images/extension-styles/` 下产生目录声明的全部 18 个 WebP 文件。

- [ ] **步骤 3：运行资产合同测试**

运行：`\.venv\Scripts\python.exe -m pytest tests/test_extension_style_previews.py -q`

预期：`1 passed`。

### 任务 4：视觉抽检与交付

**文件：**
- 检查：`frontend/public/images/extension-styles/*.webp`

- [ ] **步骤 1：视觉检查全部预览**

逐张确认主体未被切断、无文字/水印/边框/宫格、无白边残余，且 18 张风格表现可辨识。

- [ ] **步骤 2：检查 Git 差异完整性**

运行：`git diff --check`

预期：无输出。

- [ ] **步骤 3：提交实现资产**

```powershell
git add -- scripts/build_extension_style_previews.py tests/test_extension_style_previews.py frontend/public/images/extension-styles
git commit -m "Hermes: 补齐扩展风格预览图"
```

