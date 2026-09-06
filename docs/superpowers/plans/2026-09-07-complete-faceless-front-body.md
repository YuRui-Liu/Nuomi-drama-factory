# 身份三视图完整无面部正身实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 Identity Sheet v2 中间正面全身保留完整头顶到脚底，只隔离可识别五官，不再裁掉头部。

**架构：** 保留历史机器键 `front_headless`，在生成提示、确定性合成和 QC 三层更新其语义。合成器只拼版不遮盖；模型直接生成完整无五官头部，QC 负责拒绝可识别人脸；前端同步改为“无面部正面全身”。

**技术栈：** Python 3.12、Pillow、pytest、React、TypeScript、Vitest、i18next

---

## 文件结构

- 修改：`tests/character_visual/test_identity_sheet.py` — 锁定完整正身提示和头部像素保留行为。
- 修改：`tests/character_visual/test_identity_sheet_qc.py` — 锁定 QC 允许完整头部、只拒绝五官的语义。
- 修改：`src/novelvideo/character_visual/identity_sheet.py` — 更新提示并删除顶部 22% 遮盖。
- 修改：`src/novelvideo/character_visual/identity_sheet_qc.py` — 更新视觉质检判定说明。
- 修改：`src/novelvideo/generators/nanobanana_character.py` — 更新 Identity Sheet v2 内部文档描述。
- 修改：`frontend/src/__tests__/components/assets/character-state-versions.test.tsx` — 锁定新用户文案。
- 修改：`frontend/public/locales/zh/translation.json` — 中文“无面部正面全身”说明。
- 修改：`frontend/public/locales/en/translation.json` — 英文 “Faceless front full body” 说明。

### 任务 1：修正生成、合成与 QC 语义

**文件：**
- 修改：`tests/character_visual/test_identity_sheet.py`
- 修改：`tests/character_visual/test_identity_sheet_qc.py`
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`src/novelvideo/character_visual/identity_sheet_qc.py`
- 修改：`src/novelvideo/generators/nanobanana_character.py`

- [ ] **步骤 1：编写失败的 Prompt 与合成测试**

将 Prompt 断言更新为完整无面部正身：

```python
assert "FACELESS FRONT FULL BODY" in prompt
assert "complete head, hairstyle, neck, body, and footwear" in prompt
assert "no identifiable facial features" in prompt
assert "HEADLESS" not in prompt
assert "neck up empty" not in prompt
```

把顶部遮盖测试改为源像素保留测试：

```python
def test_compose_v2_preserves_front_head_region_without_masking(tmp_path: Path) -> None:
    candidate = Image.new("RGB", (800, 600), (128, 128, 128))
    candidate.paste((30, 210, 40), (400, 0, 600, 600))
    candidate.paste((30, 40, 210), (600, 0, 800, 600))
    candidate_path = tmp_path / "candidate.png"
    candidate.save(candidate_path)
    portrait = _solid(tmp_path / "portrait.png", (64, 64), (12, 34, 56))
    output = tmp_path / "sheet.png"

    compose_identity_sheet_v2(candidate_path, portrait, output)

    image = Image.open(output).convert("RGB")
    assert image.getpixel((900, 40)) == (30, 210, 40)
```

- [ ] **步骤 2：编写失败的 QC Prompt 测试**

更新捕获到的 QC Prompt 断言：

```python
qc_prompt = captured["prompt"].lower()
assert "complete head" in qc_prompt
assert "hair outline" in qc_prompt
assert "facial features" in qc_prompt
assert "head safety zone" not in qc_prompt
assert "any face, facial feature, head" not in qc_prompt
```

- [ ] **步骤 3：运行测试确认红灯**

运行：

```bash
uv run pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py -q
```

预期：Prompt 仍包含 `HEADLESS`，合成器顶部仍为背景灰，QC 仍要求头部区域为空，因此新增断言失败。

- [ ] **步骤 4：实现最小后端修复**

在 `build_identity_sheet_v2_prompt` 中把中间面板改为：

```text
- CENTER 25%: FACELESS FRONT FULL BODY in a neutral standing pose, fully visible from the complete top of the head to the soles of the feet. Preserve the complete head, hairstyle and hair outline, ears, neck, body proportions, outfit, and footwear without cropping. Render the facial plane as a smooth neutral surface consistent with the project style, with no identifiable facial features: no eyes, eyebrows, nose, lips, beard, or face-like markings. Do not replace the face with a mask, veil, prop, wound, hole, or horror element.
```

删除：

```python
FRONT_HEAD_SAFE_ZONE_RATIO = 0.22
mask_bottom = int(IDENTITY_SHEET_SIZE[1] * FRONT_HEAD_SAFE_ZONE_RATIO)
canvas.paste(neutral_gray, (front_bounds[0], 0, front_bounds[2], mask_bottom))
```

在 QC Prompt 中明确允许完整头部，只在出现五官或可识别人脸时设置 `front_face_detected=true`。同步更新 `generate_identity_with_reference` 的 docstring 为“完整无面部正面全身”。

- [ ] **步骤 5：运行后端测试确认绿灯**

运行：

```bash
uv run pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交后端修复**

```bash
git add tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py src/novelvideo/character_visual/identity_sheet.py src/novelvideo/character_visual/identity_sheet_qc.py src/novelvideo/generators/nanobanana_character.py
git commit -m "fix: preserve complete faceless front body"
```

### 任务 2：更新前端可见语义

**文件：**
- 修改：`frontend/src/__tests__/components/assets/character-state-versions.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

- [ ] **步骤 1：编写失败的前端文案测试**

直接导入正式中英文资源，并增加资源契约测试，确保旧文案先失败：

```tsx
import enTranslation from "../../../../public/locales/en/translation.json";
import zhTranslation from "../../../../public/locales/zh/translation.json";

it("describes the complete faceless front body in both locales", () => {
  expect(zhTranslation.characters.stateVersions.panels.headlessFront).toBe("无面部正面全身");
  expect(zhTranslation.characters.stateVersions.isolationHint).toContain("保留完整头部轮廓");
  expect(enTranslation.characters.stateVersions.panels.headlessFront).toBe("Faceless front full body");
  expect(enTranslation.characters.stateVersions.isolationHint).toContain("complete head outline");
});
```

同时将组件测试翻译 fixture 和断言改为：

```tsx
"characters.stateVersions.v2Description": "3/4 脸部母版锁定身份；无面部正面全身保留完整头身并表达体型与服装正面；背面全身表达背部轮廓与服装结构。",
"characters.stateVersions.isolationHint": "正面全身保留完整头部轮廓，仅隔离可识别五官，并非裁切或图片缺损。",
"characters.stateVersions.panels.headlessFront": "无面部正面全身",
```

断言组件显示以上三段新文本，且不再显示“无头正面全身”。

- [ ] **步骤 2：运行测试确认红灯**

运行：

```bash
cd frontend
./node_modules/.bin/vitest run src/__tests__/components/assets/character-state-versions.test.tsx
```

预期：正式翻译资源仍使用旧“无头 / headless”语义，新增资源契约测试失败。

- [ ] **步骤 3：更新中英文资源**

中文使用：

```json
"v2Description": "3/4 脸部母版锁定身份；无面部正面全身保留完整头身并表达体型与服装正面；背面全身表达背部轮廓与服装结构。",
"isolationHint": "正面全身保留完整头部轮廓，仅隔离可识别五官，并非裁切或图片缺损。",
"headlessFront": "无面部正面全身"
```

英文使用：

```json
"v2Description": "The 3/4 face master anchors identity; the faceless front full body preserves the complete head-to-toe figure while defining body shape and front outfit details; the back full body defines the rear silhouette and outfit structure.",
"isolationHint": "The front full-body view keeps the complete head outline and isolates only identifiable facial features; it is not cropped or damaged.",
"headlessFront": "Faceless front full body"
```

- [ ] **步骤 4：运行前端测试确认绿灯**

运行：

```bash
cd frontend
./node_modules/.bin/vitest run src/__tests__/components/assets/character-state-versions.test.tsx
```

预期：全部通过。

- [ ] **步骤 5：运行最终验证**

运行：

```bash
uv run pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py -q
cd frontend
./node_modules/.bin/vitest run src/__tests__/components/assets/character-state-versions.test.tsx
./node_modules/.bin/tsc -b --pretty false
cd ..
git diff --check
```

预期：Python、Vitest、TypeScript 和差异检查均通过。

- [ ] **步骤 6：提交前端文案**

```bash
git add frontend/src/__tests__/components/assets/character-state-versions.test.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json
git commit -m "fix: clarify faceless front body labels"
```
