# 金石证痕全局扩展风格实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `drama_ext.jinshi_ink_suspense` 作为第 19 项全局只读扩展风格接入 Nuomi，并提供 GPT Image 原创预览与 MiniMax H3 投影契约验证。

**架构：** 后端 `catalog.json` 继续作为唯一真源，经既有同步脚本生成前端快照；StyleService、API、画布和 StyleResolver 不新增分支。新风格只增加目录数据、来源审计、预览资产和针对性契约测试。

**技术栈：** Python 3.11、pytest、Pillow、JSON、TypeScript、Vitest、GPT Image、现有 ExtensionStyleRegistry/StyleResolver。

---

## 文件结构

- 修改 `src/novelvideo/extension_styles/catalog.json`：追加“金石证痕”目录项。
- 修改 `src/novelvideo/extension_styles/source_audit.json`：把原创派生说明由 18 项更新为 19 项。
- 修改 `frontend/src/features/canvas/extension-styles/catalog.generated.json`：由同步脚本生成后端目录的确定性镜像。
- 创建 `frontend/public/images/extension-styles/jinshi-ink-suspense.webp`：GPT Image 原创预览，归一化为 640 × 360 WebP。
- 修改 `tests/test_extension_style_catalog.py`：固定新条目的元数据、来源和 19 项目录契约。
- 修改 `tests/test_extension_style_registry.py`：更新有效目录的 19 项诊断断言。
- 修改 `tests/test_extension_style_previews.py`：更新预览资产总数断言。
- 修改 `tests/test_api_styles.py`：更新 StyleService/API 的 19 项扩展风格断言。
- 修改 `tests/test_style_resolver.py`：增加新风格的 image/video 投影与稳定 hash 回归。
- 修改 `frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts`：更新 19 项快照断言并验证新 ID。

### 任务 1：先锁定目录与投影契约

**文件：**
- 修改：`tests/test_extension_style_catalog.py`
- 修改：`tests/test_extension_style_registry.py`
- 修改：`tests/test_extension_style_previews.py`
- 修改：`tests/test_api_styles.py`
- 修改：`tests/test_style_resolver.py`
- 修改：`frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts`

- [ ] **步骤 1：在精确目录映射中加入新风格**

在 `EXPECTED_CATALOG` 末尾加入：

```python
"drama_ext.jinshi_ink_suspense": (
    "金石证痕",
    "chinese",
    (
        "illustration-art-style",
        "ink-double-exposure-poster",
        "character-design-sheet",
        "scene-storytelling",
        "history-classical-themes",
    ),
    "/images/extension-styles/jinshi-ink-suspense.webp",
),
```

把该文件中目录长度从 `18` 更新为 `19`，保留 `actual == EXPECTED_CATALOG`，从而同时保护旧条目不变。

- [ ] **步骤 2：更新运行时、API 和预览数量断言**

将扩展目录相关的精确数量改为 19：

```python
assert len(snapshot.styles) == 19
assert snapshot.diagnostics.discovered == 19
assert snapshot.diagnostics.loaded == 19
assert len(styles) == 19
assert len([style for style in styles if style["type"] == "extension"]) == 19
assert types == ["preset"] * 6 + ["extension"] * 19 + ["custom"] * 2
```

测试名同步改为 `nineteen`，但不改 last-known-good 或只读行为。

- [ ] **步骤 3：增加新风格的 StyleSnapshot 回归**

在 `tests/test_style_resolver.py` 增加：

```python
def test_jinshi_style_projects_full_image_and_h3_safe_video_contract() -> None:
    from novelvideo.services.style_service import StyleService

    first = StyleService.resolve_style_snapshot(
        "drama_ext.jinshi_ink_suspense"
    )
    second = StyleService.resolve_style_snapshot(
        "drama_ext.jinshi_ink_suspense"
    )

    assert "semi-realistic 2.5D Chinese ink-line animation" in first.projections.image
    assert "stable facial planes" in first.projections.video
    assert "stone-cyan, charcoal, and ash-white palette" in first.projections.video
    assert "restrained lens distortion" in first.projections.image
    assert "restrained lens distortion" not in first.projections.video
    assert "no non-diegetic typography" in first.projections.image
    assert "no non-diegetic typography" not in first.projections.video
    assert first.style_hash == second.style_hash
    assert first.projections == second.projections
```

- [ ] **步骤 4：更新前端目录断言并增加新 ID 查询**

在 `extension-style-catalog.test.ts` 中把两个 `18` 改为 `19`，并增加：

```typescript
const jinshi = getExtensionStyle("drama_ext.jinshi_ink_suspense");
expect(jinshi?.name).toBe("金石证痕");
expect(jinshi?.category).toBe("chinese");
expect(jinshi?.preview_asset).toBe(
  "/images/extension-styles/jinshi-ink-suspense.webp",
);
```

- [ ] **步骤 5：运行测试并确认按预期失败**

运行：

```bash
uv run pytest tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py tests/test_style_resolver.py -q
cd frontend && pnpm vitest run src/__tests__/features/canvas/extension-style-catalog.test.ts
```

预期：FAIL。后端目录仍只有 18 项、新 ID 不存在、前端生成快照仍只有 18 项、预览资产缺失。

### 任务 2：加入目录记录、来源审计和前端快照

**文件：**
- 修改：`src/novelvideo/extension_styles/catalog.json`
- 修改：`src/novelvideo/extension_styles/source_audit.json`
- 修改：`frontend/src/features/canvas/extension-styles/catalog.generated.json`

- [ ] **步骤 1：追加完整目录项**

在 `catalog.json` 末尾追加规格中的完整 JSON 对象，关键片段必须精确为：

```json
{
  "id": "drama_ext.jinshi_ink_suspense",
  "name": "金石证痕",
  "category": "chinese",
  "summary": "半写实金石墨线与矿物色薄染结合，以克制朱砂和冷暖侧光强化悬疑氛围、材质层次与焦点辨识度。",
  "prompt_fragment": {
    "medium": ["semi-realistic 2.5D Chinese ink-line animation", "stone-rubbing texture with restrained mineral-pigment wash"],
    "rendering": ["precise engraved contours, stable facial planes, crisp material edges", "controlled layered brush texture with clean silhouettes"],
    "lighting": ["low-key directional side light", "warm practical light against cool ambient shadow"],
    "color": ["stone-cyan, charcoal, and ash-white palette", "sparse cinnabar focal accents with restrained saturation"],
    "camera": ["clean focal hierarchy, stable layered depth", "restrained lens distortion, readable close-detail framing"],
    "constraints": ["production-ready clarity, preserve identity and wardrobe/props/action/setting from base prompt", "no non-diegetic typography or watermark, no glossy plastic finish, no excessive fantasy glow"]
  },
  "use_cases": ["古风悬疑漫剧", "证物与推理特写", "雨夜低照度场景"],
  "preview_asset": "/images/extension-styles/jinshi-ink-suspense.webp",
  "source": {
    "repository": "freestylefly/awesome-gpt-image-2",
    "source_ids": ["illustration-art-style", "ink-double-exposure-poster", "character-design-sheet", "scene-storytelling", "history-classical-themes"],
    "license_review": "approved",
    "imported_revision": "3a9c63baa03e6bbe2f28c89a2654cf9845466646"
  },
  "version": "1"
}
```

- [ ] **步骤 2：更新来源审计中的目录数量**

仅把 derivation 的数量改为 19：

```json
"derivation": "DramaClaw's 19 extension styles are original, story-neutral prompt fragments curated from the named upstream template methods. They do not copy upstream community images or case prompts; source_ids record which locked templates informed each local style."
```

- [ ] **步骤 3：生成前端快照**

运行：

```bash
uv run python scripts/sync_extension_style_catalog.py
uv run python scripts/sync_extension_style_catalog.py --check
```

预期：第一次写入 19 项快照；第二次退出码为 0 且无漂移。

- [ ] **步骤 4：运行除预览资产外的聚焦测试**

运行：

```bash
uv run pytest tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_api_styles.py tests/test_style_resolver.py tests/test_sync_extension_style_catalog.py -q
cd frontend && pnpm vitest run src/__tests__/features/canvas/extension-style-catalog.test.ts
```

预期：PASS。

- [ ] **步骤 5：提交目录与契约变更**

```bash
git add src/novelvideo/extension_styles/catalog.json src/novelvideo/extension_styles/source_audit.json frontend/src/features/canvas/extension-styles/catalog.generated.json tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py tests/test_style_resolver.py frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts
git commit -m "feat(styles): add jinshi ink suspense extension"
```

### 任务 3：生成并验收 GPT Image 预览资产

**文件：**
- 创建：`frontend/public/images/extension-styles/jinshi-ink-suspense.webp`

- [ ] **步骤 1：使用 GPT Image 生成原创横版底图**

生成提示词：

```text
Create an original 16:9 visual-style preview for a Chinese mystery animation template. Semi-realistic 2.5D Chinese ink-line animation, stone-rubbing texture with restrained mineral-pigment wash, precise engraved contours, stable facial planes, crisp stone and paper material edges, controlled layered brush texture with clean silhouettes. Low-key directional side light, a small warm practical light against cool ambient shadow. Stone-cyan, charcoal, and ash-white palette with one sparse cinnabar focal accent. Show an anonymous non-specific investigator silhouette studying an abstract carved stone rubbing; no identifiable historical figure, no specific dynasty, no copyrighted character, no artist imitation. Clean focal hierarchy, stable layered depth, restrained lens distortion. No readable text, no logo, no watermark, no glossy plastic 3D finish, no anime big eyes, no neon, no excessive fantasy glow. Production-quality concept frame.
```

- [ ] **步骤 2：归一化输出资产**

将 GPT Image 输出居中裁切到 16:9，使用 Pillow LANCZOS 缩放到 640 × 360，转换为 RGB，以 quality 88、method 6 保存为目标 WebP。只提交最终 WebP，不提交原始生成文件。

- [ ] **步骤 3：运行预览资产契约测试**

运行：

```bash
uv run pytest tests/test_extension_style_previews.py -q
```

预期：PASS；新文件为 WebP、640 × 360、RGB/RGBA 且大于 8 KB。

- [ ] **步骤 4：人工查看最终 WebP**

检查石青/墨黑/灰白为主、朱砂仅为焦点；人物和石刻边缘清晰；无可读文字、Logo、水印、仙侠粒子、霓虹或塑料 3D 感。

- [ ] **步骤 5：提交预览资产**

```bash
git add frontend/public/images/extension-styles/jinshi-ink-suspense.webp
git commit -m "feat(styles): add jinshi ink suspense preview"
```

### 任务 4：完成同步与回归验证

**文件：**
- 验证：上述全部修改文件

- [ ] **步骤 1：验证后端目录、API、投影和预览**

```bash
uv run pytest tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py tests/test_style_resolver.py tests/test_sync_extension_style_catalog.py -q
```

预期：PASS。

- [ ] **步骤 2：验证前端目录消费**

```bash
cd frontend && pnpm vitest run src/__tests__/features/canvas/extension-style-catalog.test.ts src/__tests__/features/canvas/extension-style-compose.test.ts src/__tests__/features/canvas/extension-style-drawer.test.tsx
```

预期：PASS。

- [ ] **步骤 3：验证格式与工作区边界**

```bash
git diff --check
git status --short
```

预期：无空白错误；状态中不包含原始 GPT Image 文件，且不误纳入用户既有的无关修改。

- [ ] **步骤 4：检查提交范围**

```bash
git show --stat --oneline HEAD~1..HEAD
git diff HEAD~2 -- src/novelvideo/extension_styles frontend/src/features/canvas/extension-styles frontend/public/images/extension-styles tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py tests/test_style_resolver.py frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts
```

预期：只有本计划列出的目录、测试和预览资产发生变化；不宣称已执行付费 MiniMax H3 画质冒烟。
