# NuomiDrama 首次多集剧本导入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让空项目首次进入「剧本导入」时即可预检并导入多个分集文件或单个合集文件，同时保持原有单文件、追加导入和版本化提交链路不变。

**架构：** 在 `episode_sources.py` 增加确定性的独占标题行拆集函数，每个上传文件生成一个或多个独立候选；预检 API 扁平化所有候选，并把空正文候选作为不可提交错误隔离。前端复用 `EpisodeImportDialog`，显示可追溯候选名称，并在空项目头部增加独立入口。

**技术栈：** Python 3.11、FastAPI、pytest、React 19、TypeScript、TanStack Query、Vitest、Testing Library、i18next。

---

## 文件结构

- `src/novelvideo/episode_sources.py`、`tests/test_episode_sources.py`：确定性合集拆集及领域测试。
- `src/novelvideo/api/routes/episode_imports.py`、`tests/test_api_episode_import_preview.py`：候选扁平化、无效分集隔离及 API 测试。
- `frontend/src/types/episode-import.ts`、`frontend/src/components/ingest/EpisodeImportDialog.tsx`、`frontend/src/__tests__/components/ingest/episode-import-dialog.test.tsx`：可追溯显示名称及弹窗测试。
- `frontend/src/routes/_app/projects.$project/ingest.tsx`、`frontend/src/__tests__/routes/ingest-settings-save.test.tsx`：首次入口及回归测试。
- `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`、`frontend/src/__tests__/i18n/locales-json.test.ts`：中英文入口文案及键契约。

### 任务 1：用纯领域函数确定性拆分合集文件

**文件：**
- 修改：`tests/test_episode_sources.py`
- 修改：`src/novelvideo/episode_sources.py`

- [ ] **步骤 1：编写失败的领域测试**

在测试导入列表加入 `split_episode_candidates`，并添加：

```python
def test_split_episode_candidates_supports_titles_preamble_and_unique_ids():
    content = (
        "剧名：失踪广播\n制作说明：竖屏短剧\n\n"
        "# 第十二集 失踪\n周禾进入广播站。\n\n"
        "Episode 13: Return\n梁真关掉发射机。\n"
    )
    candidates = split_episode_candidates("全剧.md", content)

    assert [item.episode_number for item in candidates] == [12, 13]
    assert [item.title for item in candidates] == ["失踪", "Return"]
    assert candidates[0].content.startswith("剧名：失踪广播")
    assert candidates[0].warnings == ("首集包含合集前言",)
    assert candidates[1].content.startswith("Episode 13: Return")
    assert {item.source_filename for item in candidates} == {"全剧.md"}
    assert len({item.file_id for item in candidates}) == 2


def test_split_episode_candidates_only_uses_standalone_heading_lines():
    content = "旁白提到第 2 集的事故。\n这不是标题。\n\n第 3 集 真相\n正文\n"
    candidates = split_episode_candidates("notes.md", content)
    assert len(candidates) == 1
    assert candidates[0].content == content


def test_split_episode_candidates_keeps_order_and_marks_empty_body():
    candidates = split_episode_candidates(
        "合集.txt", "第 9 集 终局\n\n第 2 集 开端\n有效正文\n"
    )
    assert [item.episode_number for item in candidates] == [9, 2]
    assert "分集标题后缺少正文" in candidates[0].warnings
    assert "分集标题后缺少正文" not in candidates[1].warnings


@pytest.mark.parametrize(
    "content", ["没有分集标题的完整剧本", "第 1 集 唯一标题\n正文"]
)
def test_split_episode_candidates_keeps_zero_or_one_boundary_as_one_candidate(content):
    assert len(split_episode_candidates("single.md", content)) == 1
```

- [ ] **步骤 2：运行测试并确认正确失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_episode_sources.py -k "split_episode_candidates" -q
```

预期：因 `split_episode_candidates` 尚不存在而失败；不是语法或夹具错误。

- [ ] **步骤 3：实现最小拆分函数**

在 `episode_sources.py` 增加：

```python
EPISODE_PREAMBLE_WARNING = "首集包含合集前言"
EPISODE_EMPTY_BODY_WARNING = "分集标题后缺少正文"

_EPISODE_HEADING_PATTERNS = (
    re.compile(
        rf"^[ \t]*(?:#{{1,6}}[ \t]*)?第\s*({_NUMBER_TOKEN})\s*集"
        r"(?:[ \t]*[:：_\-—]?[ \t]*[^\r\n]*)?[ \t]*$"
    ),
    re.compile(
        r"^[ \t]*(?:#{1,6}[ \t]*)?episode\s*[-_:#]?\s*0*([1-9][0-9]*)"
        r"(?:[ \t]*[:：_\-—]?[ \t]*[^\r\n]*)?[ \t]*$",
        re.IGNORECASE,
    ),
)


def _episode_heading_number(line: str) -> int | None:
    for pattern in _EPISODE_HEADING_PATTERNS:
        match = pattern.fullmatch(line)
        if match:
            return _parse_number(match.group(1))
    return None


def _split_candidate(
    filename: str,
    content: str,
    heading: str,
    episode_number: int,
    *,
    has_preamble: bool,
    has_body: bool,
) -> EpisodeCandidate:
    filename_number = _first_number(Path(filename).stem, _FILENAME_PATTERNS)
    warnings: list[str] = []
    if filename_number is not None and filename_number != episode_number:
        warnings.append(
            f"正文集号 {episode_number} 与文件名集号 {filename_number} 不一致"
        )
    if has_preamble:
        warnings.append(EPISODE_PREAMBLE_WARNING)
    if not has_body:
        warnings.append(EPISODE_EMPTY_BODY_WARNING)
    return EpisodeCandidate(
        source_filename=filename,
        content=content,
        episode_number=episode_number,
        number_source="body",
        file_id=uuid4().hex,
        title=_extract_title(heading),
        content_hash=content_sha256(content),
        warnings=tuple(warnings),
    )


def split_episode_candidates(filename: str, content: str) -> tuple[EpisodeCandidate, ...]:
    """Split only documents containing at least two standalone episode headings."""
    boundaries: list[tuple[int, int, int, str]] = []
    offset = 0
    for line in content.splitlines(keepends=True):
        heading = line.rstrip("\r\n")
        number = _episode_heading_number(heading)
        if number is not None:
            boundaries.append((offset, offset + len(line), number, heading))
        offset += len(line)
    if len(boundaries) < 2:
        return (build_episode_candidate(filename, content),)

    preamble = content[: boundaries[0][0]]
    candidates: list[EpisodeCandidate] = []
    for index, (start, heading_end, number, heading) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(content)
        segment = content[start:end]
        candidates.append(
            _split_candidate(
                filename,
                f"{preamble}{segment}" if index == 0 else segment,
                heading,
                number,
                has_preamble=index == 0 and bool(preamble.strip()),
                has_body=bool(content[heading_end:end].strip()),
            )
        )
    return tuple(candidates)
```

- [ ] **步骤 4：运行领域测试并确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_episode_sources.py -q
```

预期：全部通过，无 collection error。

- [ ] **步骤 5：提交领域拆分变更**

```powershell
git add -- src/novelvideo/episode_sources.py tests/test_episode_sources.py
git commit -m "Hermes: 增加合集剧本确定性拆集"
```

### 任务 2：让预检 API 扁平化候选并隔离无效分集

**文件：**
- 修改：`tests/test_api_episode_import_preview.py`
- 修改：`src/novelvideo/api/routes/episode_imports.py`

- [ ] **步骤 1：编写失败的 API 测试**

```python
@pytest.mark.asyncio
async def test_preview_flattens_bundle_and_preserves_source_identity(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [UploadFile(
        file=io.BytesIO("序言\n第1集 起点\n甲\nEpisode 2: Next\n乙".encode()),
        filename="第一季.md",
    )]
    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    items = response["data"]["files"]
    assert [item["episode_number"] for item in items] == [1, 2]
    assert [item["display_name"] for item in items] == [
        "第一季.md · 第 1 集", "第一季.md · 第 2 集"
    ]
    assert [item["filename"] for item in items] == ["第一季.md", "第一季.md"]
    assert len({item["file_id"] for item in items}) == 2
    assert [item.source_filename for item in store.saved["items"]] == [
        "第一季.md", "第一季.md"
    ]


@pytest.mark.asyncio
async def test_preview_keeps_valid_candidates_when_one_split_episode_is_empty(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [
        UploadFile(
            file=io.BytesIO("第1集 空集\n\n第2集 有内容\n正文".encode()),
            filename="合集.txt",
        ),
        UploadFile(file=io.BytesIO("第3集\n第三集正文".encode()), filename="E03.md"),
    ]
    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    items = response["data"]["files"]
    assert [item["status"] for item in items] == ["invalid", "conflict", "new"]
    assert items[0]["error"] == "分集标题后缺少正文"
    assert [item.episode_number for item in store.saved["items"]] == [2, 3]
```

`PreviewStore` 默认已有第 2 集，所以第二个用例的有效第 2 集应为 `conflict`。

- [ ] **步骤 2：运行测试并确认正确失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_episode_import_preview.py -q
```

预期：合集仍只有 1 个预检项或缺少 `display_name`，新增断言失败。

- [ ] **步骤 3：实现候选扁平化和无效项隔离**

导入 `EPISODE_EMPTY_BODY_WARNING`、`split_episode_candidates`，用以下循环替换单候选构造：

```python
    candidates, response_items = [], []
    for upload in files:
        parsed = _read_upload(upload)
        if isinstance(parsed, dict):
            response_items.append(parsed)
            continue
        source_filename, content = parsed
        split_candidates = split_episode_candidates(source_filename, content)
        for candidate in split_candidates:
            is_bundle = len(split_candidates) > 1
            display_name = (
                f"{candidate.source_filename} · 第 {candidate.episode_number} 集"
                if is_bundle and candidate.episode_number is not None
                else candidate.source_filename
            )
            warnings = [w for w in candidate.warnings if w != EPISODE_EMPTY_BODY_WARNING]
            item = {
                "file_id": candidate.file_id,
                "filename": candidate.source_filename,
                "display_name": display_name,
                "title": candidate.title or None,
                "episode_number": candidate.episode_number,
                "number_source": candidate.number_source,
                "warnings": warnings,
            }
            if EPISODE_EMPTY_BODY_WARNING in candidate.warnings:
                item.update(status="invalid", error=EPISODE_EMPTY_BODY_WARNING)
                response_items.append(item)
                continue
            candidates.append(candidate)
            response_items.append(item)
```

保留后续重复集号、已有集冲突和 `save_preview` 逻辑；无效空集不进入 `candidates`。

- [ ] **步骤 4：运行 API 和提交契约测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_episode_import_preview.py tests/test_api_episode_import_commit.py tests/test_episode_source_store.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交 API 变更**

```powershell
git add -- src/novelvideo/api/routes/episode_imports.py tests/test_api_episode_import_preview.py
git commit -m "Hermes: 支持合集候选扁平预检"
```

### 任务 3：显示可追溯的同源分集名称

**文件：**
- 修改：`frontend/src/types/episode-import.ts`
- 修改：`frontend/src/components/ingest/EpisodeImportDialog.tsx`
- 修改：`frontend/src/__tests__/components/ingest/episode-import-dialog.test.tsx`

- [ ] **步骤 1：编写失败的弹窗测试**

```tsx
it("shows split candidates with traceable labels and commits unique file ids", async () => {
  preview.mutateAsync.mockResolvedValue({ ok: true, data: { ...base, files: [
    item({ file_id: "bundle-2", filename: "第一季.md", display_name: "第一季.md · 第 2 集", episode_number: 2 }),
    item({ file_id: "bundle-1", filename: "第一季.md", display_name: "第一季.md · 第 1 集", episode_number: 1 }),
  ] } });
  commit.mutateAsync.mockResolvedValue({ ok: true, task_type: "episode_import" });
  renderDialog();
  await upload([new File(["bundle"], "第一季.md")]);

  const rows = await screen.findAllByTestId("episode-import-row");
  expect(within(rows[0]).getByText("第一季.md · 第 1 集")).toBeInTheDocument();
  expect(within(rows[1]).getByText("第一季.md · 第 2 集")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(commit.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ resolutions: [
    { file_id: "bundle-1", episode_number: 1, action: "import" },
    { file_id: "bundle-2", episode_number: 2, action: "import" },
  ] }));
});
```

- [ ] **步骤 2：运行测试并确认正确失败**

```powershell
pnpm --dir frontend exec vitest run src/__tests__/components/ingest/episode-import-dialog.test.tsx
```

预期：找不到合集显示名称而失败。

- [ ] **步骤 3：增加类型和统一标签函数**

在 `EpisodeImportPreviewItem` 增加 `display_name?: string;`，并在弹窗增加：

```typescript
function previewItemLabel(item: EpisodeImportPreviewItem): string {
  return item.display_name?.trim() || item.filename;
}
```

同集号排序的名称兜底、行内主标题、手工集号标签、冲突按钮、`radiogroup` 及键盘切换查询均使用该函数。提交请求仍只发送 `file_id`、集号和动作。

- [ ] **步骤 4：运行组件测试和 TypeScript 构建**

```powershell
pnpm --dir frontend exec vitest run src/__tests__/components/ingest/episode-import-dialog.test.tsx
pnpm --dir frontend exec tsc -b --pretty false
```

预期：全部通过。

- [ ] **步骤 5：提交前端预检显示变更**

```powershell
git add -- frontend/src/types/episode-import.ts frontend/src/components/ingest/EpisodeImportDialog.tsx frontend/src/__tests__/components/ingest/episode-import-dialog.test.tsx
git commit -m "Hermes: 显示可追溯的合集分集名称"
```

### 任务 4：首次空项目展示多集导入入口

**文件：**
- 修改：`frontend/src/routes/_app/projects.$project/ingest.tsx`
- 修改：`frontend/src/__tests__/routes/ingest-settings-save.test.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`frontend/src/__tests__/i18n/locales-json.test.ts`

- [ ] **步骤 1：编写失败的首次入口测试**

在路由测试翻译 mock 加入 `multiEpisode: "Multi-episode import"`，再添加：

```tsx
it("opens multi-episode import before the first script is imported", async () => {
  const user = userEvent.setup();
  render(<Wrapper><IngestPageContent project="demo" /></Wrapper>);

  expect(screen.getByRole("button", { name: "Multi-episode import" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Append episode" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Multi-episode import" }));
  expect(screen.getByRole("dialog", { name: "episode import dialog" })).toHaveAttribute(
    "data-existing-episodes", ""
  );
});
```

在 `locales-json.test.ts` 的 `episodeImport` 契约加入：

```typescript
multiEpisode: expect.any(String),
```

- [ ] **步骤 2：运行测试并确认正确失败**

```powershell
pnpm --dir frontend exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/i18n/locales-json.test.ts
```

预期：首次入口不存在且翻译键缺失。

- [ ] **步骤 3：实现入口和中英文文案**

在中英文 `ingest.episodeImport` 下分别增加 `"multiEpisode": "多集导入"` 和 `"multiEpisode": "Multi-episode import"`。将头部动作改为：

```tsx
<div className="flex shrink-0 items-center gap-2">
  {hasImportedContent ? (
    <>
      <Button type="button" variant="outline" onClick={() => setEpisodeImportOpen(true)}>
        <Plus className="size-4" />
        {t("ingest.episodeImport.append")}
      </Button>
      <Button type="button" onClick={() => setEpisodeImportOpen(true)}>
        {t("ingest.episodeImport.batch")}
      </Button>
    </>
  ) : (
    <Button type="button" variant="outline" onClick={() => setEpisodeImportOpen(true)}>
      <Plus className="size-4" />
      {t("ingest.episodeImport.multiEpisode")}
    </Button>
  )}
</div>
```

不移动单文件上传、粘贴入口或弹窗，也不在空项目启用 episode imports 查询。

- [ ] **步骤 4：运行前端目标回归**

```powershell
pnpm --dir frontend exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/components/ingest/episode-import-dialog.test.tsx src/__tests__/i18n/locales-json.test.ts
pnpm --dir frontend exec tsc -b --pretty false
```

预期：全部通过。

- [ ] **步骤 5：提交首次入口变更**

```powershell
git add -- 'frontend/src/routes/_app/projects.$project/ingest.tsx' frontend/src/__tests__/routes/ingest-settings-save.test.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json frontend/src/__tests__/i18n/locales-json.test.ts
git commit -m "Hermes: 首次剧本导入支持多集入口"
```

### 任务 5：集中回归、构建与真机预检

**文件：**
- 不新增生产文件；如发现回归，先在对应测试文件补失败测试，再修改最小实现文件。

- [ ] **步骤 1：运行后端集中测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_episode_sources.py tests/test_api_episode_import_preview.py tests/test_api_episode_import_commit.py tests/test_episode_source_store.py tests/test_episode_import_transaction.py tests/test_episode_import_records.py -q
```

预期：全部通过。

- [ ] **步骤 2：运行前端集中测试与生产构建**

```powershell
pnpm --dir frontend exec vitest run src/__tests__/routes/ingest-settings-save.test.tsx src/__tests__/components/ingest/episode-import-dialog.test.tsx src/__tests__/lib/queries/episode-imports.test.tsx src/__tests__/i18n/locales-json.test.ts
pnpm --dir frontend run build
```

预期：目标测试、`tsc -b`、Vite 构建和 bundle budget 检查全部成功。

- [ ] **步骤 3：检查变更边界**

```powershell
git diff --check
git status --short
```

预期：`git diff --check` 无输出，本功能文件均已按任务提交。

- [ ] **步骤 4：重启前后端并执行隔离项目真机预检**

1. 首次进入「剧本导入」，确认显示「多集导入」，不显示「追加单集」。
2. 上传含前言、`第 1 集`、`Episode 2` 和一个空正文分集的 `.md` 合集。
3. 确认有效分集按集号显示为「原文件名 · 第 N 集」，前言警告可见，空正文分集单独失败。
4. 不点击确认，刷新页面，确认未自动提交。
5. 上传两个独立分集文件，确认仍返回两个候选且冲突动作正确。
6. 记录前端 URL、后端健康状态、预检响应摘要和截图。

- [ ] **步骤 5：闭环验证中发现的回归**

若步骤 1–4 失败，回到归属任务的失败测试步骤：先补充能稳定复现该回归的测试，确认红灯，再修改该任务列出的最小实现文件。重新运行任务 5 的步骤 1–3；修复与归属任务使用同一组精确文件并入该任务提交，不创建通用或空提交。
