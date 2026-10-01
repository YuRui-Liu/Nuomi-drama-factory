# Nuomi Drama Factory 项目长期笔记

## 前端测试陷阱：base-ui Select 不能在同一测试文件里反复 render
- 现象：多个 `it()` 各自 `render()` 同一含 `Select` 的组件时，只有第一个用例的下拉能打开；后续用例点 trigger 后 `aria-expanded` 仍为 false，`getByRole('option', ...)` 找不到。单独跑（`-t` 过滤）每个都能过。
- 原因：base-ui 的 Select 内部 id 在重复 render 间复用，跨 render 状态错乱。`cleanup()` 甚至 `document.body.innerHTML = ''` 都救不回来。
- 对策：把相关断言合并到**单个 `it()` 里做一次 render 的完整流程**（项目里 `workbuddy-routing.test.tsx` 就是这个写法）。
- 另：`vi.mock('@/lib/queries/model-gateway', ...)` 时若组件用到了具名常量导出（如 `TASK_REASONING_EFFORTS`），mock 必须一并补上，否则整个文件所有用例崩溃。

## 文本任务路由（text_task_runtime）要点
- 三个运行时：`codex` / `model_api` / `workbuddy`，路由在任务入队时冻结。
- 推理强度（reasoning_effort）各运行时支持面不同：`max` 只有 WorkBuddy CLI 有；model_api 走 `_normalize_openai_compat_reasoning_effort` 归一，非 OpenAI 兼容值会被丢弃。改动枚举时记得同步前端 `model-gateway.ts` 与面板选项。
- WorkBuddy 不支持图片输入，视觉检查必须走 Codex 或 model_api。
- 模型名校验已放宽：允许内部空格、首尾 strip；`TEXT_TASK_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/\- ]{0,127}$"`；走 `field_validator("model")` 调 `validate_text_task_model_name`，shell 元字符（`&|%`、换行）仍被拒。
- 模型候选清单来源：`src/novelvideo/text_task_runtime/models_catalog.py::get_runtime_model_catalog()`。workbuddy 读 `NUOMI_PROJECT_ROOT/.codebuddy/models.json` > cwd 同路径 > `~/.codebuddy/models.json` 的 `availableModels`（权威）或 `models[].id`（兜底合并），都没读到回退内置 `[Hy4 preview, Hy3, Deepseek-V4, Deepseek-V4.1-Flash, default-model]`（与 WorkBuddy 桌面端模型选择器对齐）；codex 默认 `[gpt-5.6-sol, gpt-6-astra]`；model_api 返回 `[]`。可用 `TEXT_TASK_DEFAULT_MODELS_<RUNTIME>` 环境变量覆盖内置默认。接口 `GET /api/v1/model-gateway/task-runtime/models`。

## 前端 ComboBox 反模式：不要用 `<input list>` + `<datalist>` 做多选下拉
- `<datalist>` 会按当前 input 值**过滤**候选——一旦选过某选项再点开下拉，只会看到与已选值匹配的条目，其他候选被浏览器隐藏。表现就是"workbuddy 下拉只显示自己"。
- 正确做法：用 base-ui `Popover` + 自渲染 `<button>` 列表（或用 base-ui 自带的 `Combobox`，但需自行处理 `inputValue`/`selectedValue` 的语义）。本项目已抽出 `frontend/src/components/settings/model-combobox.tsx`：触发器仿输入框样式，弹层内含可编辑 `<Input>` + 候选 `<li role="option">` 列表 + 「使用自定义值"xxx"」行。
- 模型名校验：放行内部空格、首尾 strip、空白拒绝；shell 元字符（`&|%\n`）仍由 `validate_text_task_model_name` 拦截，跨平台 subprocess list args 仍安全。
- 运行时模型候选清单：`text_task_runtime/models_catalog.py:get_runtime_model_catalog()` 按运行时返回建议下拉。workbuddy 读 `NUOMI_PROJECT_ROOT/.codebuddy/models.json` → cwd 同路径 → `~/.codebuddy/models.json`，有 `availableModels` 时为权威，无则合并 `models[].id`，都没读到回退内置默认；codex / model_api 默认内置或环境变量 `TEXT_TASK_DEFAULT_MODELS_<RUNTIME>` 覆盖。本机 CLI 都拿不到模型枚举（无 `codex models`/`workbuddy models` 命令），清单必须由 models.json 或后端常量维护。
- 前端"模型"字段是 `<input list=...>` + `<datalist>`，下拉选项按当前 `route.runtime` 切换；同时允许手输任意 ID。

## 剧本线：nuomi-drama-scripts skill（与 nuomi-production 生产线完全独立）
- 位置 `nuomi-drama-scripts/`，纯 Python 标准库工具，不需要 PowerShell。项目实例放 `output/nuomi-scripts/<剧名>/`（结构：project.yaml + manuscript/ + sources/ + reviews/ + templates/ + deliverables/）。
- **单集交付协议硬格式**：`# 第N集 · 集名`（禁止 `第N集：集名`）；`时长：整数s` 单独一行；场景标题 `### N-M 地点 时段 内/外`，时段只能取 日/夜/清晨/早晨/上午/中午/下午/傍晚/黄昏/深夜；每个场景标题下一行必须 `人物：甲 乙`（**≥2 个角色**，否则被 `build_docx.py` 降级成蓝色对白）。
- `audit_docx.py` 期望 EpisodeTitle = `--expected-episodes + 1`（多一个第零集）。`test_markdown_episode_contract.py` 的场景正则用 `\S+` 匹配地点，自带校验脚本要写 `(.+)` 才与 `test_single_episode_contract.py`（用 `.+`）对齐。
- `export_screenplay.py` / `build_docx.py` **输出已存在就 fail**，重跑必须先删产物。全篇需至少 1 行 `【音效】`（`^【`）供 audit 通过。
- **已改** `tools/build_docx.py`：封面元数据改为从 project.yaml 读 `genre_label` / `aspect_ratio` / `orientation_label` / `version_label` / `delivery_status_label`，缺省回退旧文案（原来硬编码"A4 · 9:16 竖屏"）。横屏项目靠这个才不会被贴错封面。`tests/skill_contract.py` 仍 PASS。
- **本机没有 Word/LibreOffice**（`soffice` 不存在）→ DOCX 的 PDF/PNG 逐页渲染 QA 做不了，按 skill 规则交付状态只能是 `delivery_blocked`，不能标 `ready`。

