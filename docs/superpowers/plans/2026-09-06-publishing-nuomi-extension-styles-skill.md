# Nuomi 扩展风格发布技能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在仓库内提供一个可发现、两阶段审批、确定性校验并发布全局只读扩展风格的 Codex 技能。

**架构：** `SKILL.md` 编排创意输入、用户批准、GPT Image 和项目钩子；独立 Python 脚本只负责 `prepare` 与 `apply` 的确定性契约。发布计划用目录 SHA256 连接两阶段，`apply` 在内存完成目录与预览构建后以可恢复的原子替换写入三个产品文件。

**技术栈：** Python 3.11、argparse、项目 `ExtensionStyle` schema、Pillow、pytest、Codex Skill Markdown/YAML、Vitest。

---

## 文件结构

- 创建 `skills/publishing-nuomi-extension-styles/SKILL.md`：代理决策、审批、图像生成、项目钩子、验证与提交工作流。
- 创建 `skills/publishing-nuomi-extension-styles/agents/openai.yaml`：技能 UI 元数据与默认调用提示。
- 创建 `skills/publishing-nuomi-extension-styles/references/release-manifest.md`：manifest/plan 字段、来源与提示词约束、完整示例。
- 创建 `skills/publishing-nuomi-extension-styles/scripts/publish_style.py`：`prepare`/`apply` CLI、目录校验、图像转换、原子写入与回滚。
- 创建 `skills/publishing-nuomi-extension-styles/tests/test_publish_style.py`：脚本成功路径与拒绝/回滚路径。
- 修改 `tests/test_extension_style_catalog.py`：把现有 19 项改为不可变基线子集，并增加可追加条目测试。
- 修改 `tests/test_extension_style_registry.py`：从测试目录内容推导数量。
- 修改 `tests/test_extension_style_previews.py`：遍历实际目录，不固定数量。
- 修改 `tests/test_api_styles.py`：从实际扩展目录推导 API 数量。
- 修改 `frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts`：保留唯一性与契约断言，移除固定总数。
- 修改 `src/novelvideo/extension_styles/source_audit.json`：移除 derivation 中固定的 `19` 描述。

### 任务 1：记录无技能基线并建立脚本红灯测试

**文件：**
- 创建：`skills/publishing-nuomi-extension-styles/tests/test_publish_style.py`

- [ ] **步骤 1：运行独立只读压力场景并记录基线**

给独立代理任务：快速发布 `drama_ext.celadon_shadow`，但不提供技能或预设流程；记录它查看的文件数量、建议修改的文件、审批门禁、并发保护和回滚策略。结果只保留在当前任务记录，不写入技能运行资源。

- [ ] **步骤 2：编写 prepare 的失败测试**

测试通过 `importlib.util.spec_from_file_location` 导入尚不存在的 `publish_style.py`，构造包含最小有效目录、来源审计和产品目录的临时项目。先覆盖：

```python
def test_prepare_writes_plan_without_mutating_project(tmp_path): ...
def test_prepare_rejects_duplicate_id(tmp_path): ...
def test_prepare_rejects_source_not_in_audit(tmp_path): ...
def test_prepare_rejects_noncanonical_preview_path(tmp_path): ...
```

成功断言包括 `schema_version == 1`、SHA256 正确、三个固定相对目标、规范化 `style` 和原样 `preview_prompt`；失败断言包括退出码 `2` 及产品文件字节不变。

- [ ] **步骤 3：运行测试验证正确失败**

运行：

```bash
uv run pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py -q
```

预期：收集阶段因 `publish_style.py` 不存在而失败。

- [ ] **步骤 4：提交红灯测试**

```bash
git add skills/publishing-nuomi-extension-styles/tests/test_publish_style.py
git commit -m "test(skills): define extension style publisher contract"
```

### 任务 2：实现 prepare 契约

**文件：**
- 创建：`skills/publishing-nuomi-extension-styles/scripts/publish_style.py`
- 测试：`skills/publishing-nuomi-extension-styles/tests/test_publish_style.py`

- [ ] **步骤 1：实现固定路径、错误类型与项目 schema 加载**

生产接口固定为：

```python
SCHEMA_VERSION = 1
CATALOG_PATH = Path("src/novelvideo/extension_styles/catalog.json")
AUDIT_PATH = Path("src/novelvideo/extension_styles/source_audit.json")
SNAPSHOT_PATH = Path("frontend/src/features/canvas/extension-styles/catalog.generated.json")
PREVIEW_DIR = Path("frontend/public/images/extension-styles")

class PublishError(Exception):
    def __init__(self, message: str, exit_code: int = 2): ...

def prepare_release(project_root: Path, manifest_path: Path) -> dict[str, object]: ...
```

脚本将自身仓库的 `src` 加入 `sys.path` 并导入 `ExtensionStyle`、`load_catalog`；manifest 顶层必须精确包含 `style` 和非空 `preview_prompt`。

- [ ] **步骤 2：实现来源、冲突和预览命名校验**

`prepare_release` 用 `ExtensionStyle.from_dict()` 获得规范化投影，要求：

```python
expected_preview = (
    "/images/extension-styles/"
    + style.id.removeprefix("drama_ext.").replace("_", "-")
    + ".webp"
)
```

目录中不得已有该 ID 或预览引用，目标预览文件不得存在。`source.repository`、`source.imported_revision` 必须等于 audit，`license_review` 必须 approved，非空 `source_ids` 必须全部存在于 audit templates。

- [ ] **步骤 3：实现只读计划生成和 CLI 错误码**

计划包含：

```python
{
    "schema_version": 1,
    "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
    "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
    "style": style.projection_input(),
    "preview_prompt": preview_prompt,
    "targets": {
        "catalog": CATALOG_PATH.as_posix(),
        "snapshot": SNAPSHOT_PATH.as_posix(),
        "preview": preview_relative_path.as_posix(),
    },
}
```

`main()` 支持 `prepare --manifest --plan [--project-root]`，先完成全部校验，再以 UTF-8、两空格 JSON 写计划。`PublishError` 输出一行 stderr 并返回其退出码，不输出 traceback。

- [ ] **步骤 4：运行 prepare 测试验证通过**

运行：

```bash
uv run pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py -q
```

预期：prepare 测试 PASS；apply 测试尚未加入。

- [ ] **步骤 5：提交 prepare 实现**

```bash
git add skills/publishing-nuomi-extension-styles/scripts/publish_style.py
git commit -m "feat(skills): prepare extension style releases"
```

### 任务 3：用 TDD 实现 apply、图片规范化与回滚

**文件：**
- 修改：`skills/publishing-nuomi-extension-styles/tests/test_publish_style.py`
- 修改：`skills/publishing-nuomi-extension-styles/scripts/publish_style.py`

- [ ] **步骤 1：编写 apply 失败测试**

增加：

```python
def test_apply_updates_exactly_three_products_and_normalizes_preview(tmp_path): ...
def test_apply_rejects_catalog_drift_without_writes(tmp_path): ...
def test_apply_rejects_invalid_image_without_writes(tmp_path): ...
def test_apply_rolls_back_every_target_when_replace_fails(tmp_path, monkeypatch): ...
```

成功测试创建 800×800 RGBA PNG，断言输出为 640×360 RGB WebP、后端新增对象与前端快照完全相等，并且 audit 未变化。漂移测试期望退出码 `3`。回滚测试在第二次产品替换处注入 `OSError`，断言三个目标恢复到调用前状态。

- [ ] **步骤 2：运行新增测试验证正确失败**

运行：

```bash
uv run pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py -q
```

预期：因 `apply_release` 或 `apply` 子命令不存在而 FAIL。

- [ ] **步骤 3：实现纯内存渲染**

实现并分别测试：

```python
def _append_catalog_entry(original: bytes, style: dict[str, object]) -> bytes: ...
def _render_snapshot(catalog: list[dict[str, object]]) -> bytes: ...
def _render_preview(source: Path) -> bytes: ...
```

`_append_catalog_entry` 解析并验证原目录，但保留原有条目文本，只在最后 `]` 前追加缩进后的新对象。`_render_snapshot` 使用 `ensure_ascii=False, indent=2`。`_render_preview` 用 Pillow `ImageOps.fit(..., (640, 360), method=Image.Resampling.LANCZOS)`、转换 RGB，并保存 WebP `quality=88, method=6` 到 `BytesIO`。

- [ ] **步骤 4：实现原子写入、回滚与 apply CLI**

接口：

```python
def apply_release(project_root: Path, plan_path: Path, preview_path: Path) -> dict[str, object]: ...
def _replace_bytes(path: Path, payload: bytes) -> None: ...
def _commit_payloads(payloads: dict[Path, bytes]) -> None: ...
```

`apply_release` 拒绝未知 schema version、非固定 targets、计划 SHA256 与批准值不符、计划 style 失效、重复 ID、来源失配和后端/前端哈希漂移。全部 payload 构建且完整新目录通过 `load_catalog` 后才调用 `_commit_payloads`。写入失败时按反序恢复原有字节或删除本次新文件；恢复异常合并进最终错误。`apply` 成功向 stdout 输出包含 ID 和三个目标的 JSON 摘要。

- [ ] **步骤 5：运行完整脚本测试验证通过**

运行：

```bash
uv run pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py -q
```

预期：全部 PASS，stderr 无警告。

- [ ] **步骤 6：提交 apply 实现**

```bash
git add skills/publishing-nuomi-extension-styles/scripts/publish_style.py skills/publishing-nuomi-extension-styles/tests/test_publish_style.py
git commit -m "feat(skills): apply extension style releases atomically"
```

### 任务 4：泛化产品测试的目录数量契约

**文件：**
- 修改：`tests/test_extension_style_catalog.py`
- 修改：`tests/test_extension_style_registry.py`
- 修改：`tests/test_extension_style_previews.py`
- 修改：`tests/test_api_styles.py`
- 修改：`frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts`
- 修改：`src/novelvideo/extension_styles/source_audit.json`

- [ ] **步骤 1：先增加第 20 项兼容性红灯测试**

在 `tests/test_extension_style_catalog.py` 增加临时目录测试：复制实际目录、追加 `valid_style()` 的唯一变体，并断言基线验证辅助函数接受新增项。先将现有精确比较抽为仍执行 `actual == EXPECTED_CATALOG` 的 `_assert_required_baseline`，运行新增测试确认因额外条目 FAIL。

- [ ] **步骤 2：将精确全集改为精确基线子集**

重命名 `EXPECTED_CATALOG` 为 `REQUIRED_BASELINE_CATALOG`，实现：

```python
def _assert_required_baseline(actual):
    assert set(REQUIRED_BASELINE_CATALOG) <= set(actual)
    assert {
        style_id: actual[style_id]
        for style_id in REQUIRED_BASELINE_CATALOG
    } == REQUIRED_BASELINE_CATALOG
```

实际目录测试调用该函数并继续验证预览唯一性，不再断言总数为 19。

- [ ] **步骤 3：让 Python 运行时测试从目录推导数量**

Registry fixture 增加 `catalog_size`，由 `len(json.loads(catalog_path.read_text()))` 得出；初始 snapshot、discovered、loaded 和 schema failure 断言使用该值。API 测试从 `ExtensionStyleRegistry` 的真实 snapshot 或 `StyleService` 返回集合推导 expected count，仍单独断言 preset 为 6。

- [ ] **步骤 4：泛化预览、前端和审计文案**

删除预览测试的 `len(styles) == 19`。前端测试断言目录非空、ID/preview 唯一且保留已知基线风格字段，不固定总数。将 audit derivation 改为 `Nuomi Drama Factory's extension styles ...`，不包含数字。

- [ ] **步骤 5：运行聚焦回归**

运行：

```bash
uv run pytest tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py -q
npm --prefix frontend test -- --run frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts
```

预期：Python 与前端测试全部 PASS，模拟新增条目测试证明无需修改固定数量。

- [ ] **步骤 6：提交测试契约调整**

```bash
git add tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_api_styles.py frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts src/novelvideo/extension_styles/source_audit.json
git commit -m "test(styles): allow contract-safe catalog additions"
```

### 任务 5：编写技能说明、引用与 UI 元数据

**文件：**
- 创建：`skills/publishing-nuomi-extension-styles/SKILL.md`
- 创建：`skills/publishing-nuomi-extension-styles/references/release-manifest.md`
- 创建：`skills/publishing-nuomi-extension-styles/agents/openai.yaml`

- [ ] **步骤 1：编写精简 SKILL.md**

frontmatter 固定为：

```yaml
---
name: publishing-nuomi-extension-styles
description: Use when adding, publishing, validating, or repairing a drama_ext.* global read-only extension style in Nuomi Drama Factory.
---
```

正文按顺序要求代理：读取项目 `AGENTS.md`；仅收集缺失创意输入；在临时目录写 manifest；运行 prepare；向用户展示计划并等待明确批准；批准后调用 GPT Image；按项目规则对三个目标执行钩子并运行 apply；执行固定测试；审阅并精确提交。任何失败都停止，不自行放宽契约。

- [ ] **步骤 2：编写 manifest 引用**

文档列出 `style` 的完整 JSON 示例、六片段设计准则、来源字段获取方式、规范预览命名、preview prompt 准则、prepare/apply 命令和退出码 `2`/`3`。

- [ ] **步骤 3：编写 agents/openai.yaml**

```yaml
interface:
  display_name: "发布 Nuomi 扩展风格"
  short_description: "两阶段校验并发布全局只读视觉风格"
  default_prompt: "Use $publishing-nuomi-extension-styles to prepare and publish a contract-compliant global read-only extension style."
policy:
  allow_implicit_invocation: true
```

- [ ] **步骤 4：运行技能结构校验**

运行：

```bash
python /Users/liuyuxiang05/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/publishing-nuomi-extension-styles
```

预期：输出 `Skill is valid!`。

- [ ] **步骤 5：提交技能资源**

```bash
git add skills/publishing-nuomi-extension-styles/SKILL.md skills/publishing-nuomi-extension-styles/references/release-manifest.md skills/publishing-nuomi-extension-styles/agents/openai.yaml
git commit -m "docs(skills): add extension style publishing workflow"
```

### 任务 6：成品压力测试与全量验证

**文件：**
- 修改：仅在测试发现缺陷时修改上述技能文件；每个缺陷先增加失败测试。

- [ ] **步骤 1：运行有技能压力场景**

向独立代理提供 `skills/publishing-nuomi-extension-styles/SKILL.md` 和与基线相同的 `drama_ext.celadon_shadow` 场景，要求只读报告。通过标准：明确先 prepare、展示计划、等待批准、再 GPT Image/apply；能列出三个产品目标、SHA256 漂移保护、项目钩子和固定验证；不重新探索十余个契约文件。

- [ ] **步骤 2：根据压力测试按 TDD 修补漏洞**

若代理误解或绕过门禁，先把误解转化为脚本测试或技能压力断言，再最小化修改 `SKILL.md` 或脚本。重复同一压力测试直至符合标准。

- [ ] **步骤 3：运行最终验证**

```bash
uv run pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py tests/test_extension_style_catalog.py tests/test_extension_style_registry.py tests/test_extension_style_previews.py tests/test_sync_extension_style_catalog.py tests/test_api_styles.py -q
python scripts/sync_extension_style_catalog.py --check
npm --prefix frontend test -- --run frontend/src/__tests__/features/canvas/extension-style-catalog.test.ts
python /Users/liuyuxiang05/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/publishing-nuomi-extension-styles
git diff --check
```

预期：所有测试与检查通过；`git status --short` 仅保留用户原有无关修改或本任务尚未提交的精确文件。

- [ ] **步骤 4：审阅提交边界并完成提交**

```bash
git diff --cached --name-only
git status --short
```

确认没有暂存 `src/novelvideo/api/deps.py`、`src/novelvideo/media_capabilities/runtime/credential_store.py` 或 `tests/media_capabilities/runtime/test_credential_store.py` 等既有无关修改。若压力测试产生修补，精确暂存并提交：

```bash
git add skills/publishing-nuomi-extension-styles
git commit -m "test(skills): harden extension style publishing workflow"
```
