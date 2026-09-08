# Identity Sheet v3 无头正面身体板实现计划

> **For AI workers:** 使用 `executing-plans` 逐项执行；每项先写失败测试，再做最小实现。

**目标：** 将角色造型图升级为 `identity_sheet_v3`：左 40% 为直接无头的正面全身，中 30% 为保留后脑的背面全身，右 30% 为唯一可见脸的 3/4 肖像；伤痕、血污与服装破损只由当前角色状态决定。

**架构：** 保留现有单次生成与完整画布保存流程，只替换共享版式契约、生成提示词和视觉 QC 语义。调用方继续复用共享常量，前端按 `layout_version` 区分 v2 历史资产与 v3 新资产。

**技术栈：** Python 3.12、Pydantic、Pillow、pytest、React/TypeScript、Vitest、i18next。

---

## 任务 1：锁定 v3 后端契约与 QC

**文件：**

- 修改：`tests/character_visual/test_identity_sheet.py`
- 修改：`tests/character_visual/test_identity_sheet_qc.py`
- 修改：`src/novelvideo/character_visual/identity_sheet.py`
- 修改：`src/novelvideo/character_visual/identity_sheet_qc.py`
- 修改：`src/novelvideo/character_visual/__init__.py`

1. 将测试改为断言 `identity_sheet_v3`、`front_headless/back_fullbody/portrait_3q`、40/30/30 边界，以及提示词中的“直接无头、无创口、状态决定伤痕、右侧唯一脸”。
2. 将 QC 测试改为断言 40%/70% 边界，并把 `front_face_detected` 扩展为左侧出现头、头发、耳朵或脸即失败；背面保留后脑有效。
3. 运行以下测试，确认因旧 v2 语义失败：

   `PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py -q`

4. 最小修改共享常量、提示词构建器与 QC 提示词；保留旧函数名兼容调用方，同时导出 v3 名称。
5. 重跑同一命令，预期通过。

## 任务 2：贯通元数据、调用方与前端版本文案

**文件：**

- 修改：`tests/test_character_state_assets.py`
- 修改：`tests/test_character_image_runner.py`
- 修改：`src/novelvideo/generators/nanobanana_character.py`
- 修改：`src/novelvideo/task_backend/runners/character_image.py`
- 修改：`frontend/src/components/assets/character-state-versions.tsx`
- 修改：`frontend/src/__tests__/components/assets/character-state-versions.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`

1. 后端测试断言新生成资产写入 v3 元数据和新提示语义；前端测试同时覆盖 v2 历史说明与 v3“无头正面身体”说明。
2. 运行窄测试，确认新断言失败：

   `PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/test_character_state_assets.py tests/test_character_image_runner.py -q`

3. 调用方切换到 v3 构建器；前端按版本选择描述、面板标签和隔离提示，并更新中英文文案。
4. 运行后端窄测试与：

   `pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-state-versions.test.tsx`

## 任务 3：回归验证与交付

1. 运行四组后端回归：

   `PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/character_visual/test_identity_sheet.py tests/character_visual/test_identity_sheet_qc.py tests/test_character_state_assets.py tests/test_character_image_runner.py -q`

2. 运行前端组件测试、Python 格式/静态检查和 `git diff --check`。
3. 检查差异只包含 v3 契约所需改动，提交隔离分支并请求代码审查。
