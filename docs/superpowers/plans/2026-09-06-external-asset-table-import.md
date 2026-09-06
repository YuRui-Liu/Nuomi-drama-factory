# 外部资产表导入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将剧本摄入与角色、场景、道具构建解耦，并为三个资产面板增加 `.md` / `.txt` 外部表的预览确认式导入。

**架构：** 新建共享 `asset_imports` 领域包，统一文件解码、预览状态、字段差异、审计和原子确认；角色、场景、道具适配器各自解析目标章节并产出有证据的候选。FastAPI 暴露统一的 `asset_type` 路由，React 复用一个导入对话框；现有显式自动提取/知识图谱构建接口保持不变。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、aiosqlite、pytest/pytest-asyncio、React、TypeScript、TanStack Query、Vitest、Testing Library、i18next。

---

## 文件结构

- 创建 `src/novelvideo/asset_imports/models.py`：领域枚举、候选、证据、字段差异、预览和确认结果模型。
- 创建 `src/novelvideo/asset_imports/parsing.py`：安全文本解码、换行规范化、Markdown 标题/列表/表格分段。
- 创建 `src/novelvideo/asset_imports/adapters.py`：三种资产适配器、结构规则、模型推导入口、证据校验和候选归并。
- 创建 `src/novelvideo/asset_imports/service.py`：预览生命周期、当前值比较、确认时重算和领域仓储编排。
- 创建 `src/novelvideo/asset_imports/__init__.py`：公开服务与模型。
- 创建 `src/novelvideo/api/routes/asset_imports.py`：上传预览与确认 API。
- 修改 `src/novelvideo/api/__init__.py`：注册资产导入路由。
- 修改 `src/novelvideo/sqlite_store.py`：导入预览/字段证据/审计表，以及三类资产仅补空字段的原子确认方法。
- 修改 `src/novelvideo/structured_publication.py`、`src/novelvideo/structured_ingest.py`、`src/novelvideo/structured_builders.py`：剧本摄入仅发布剧集，不发布角色或场景；显式构建函数保持原状。
- 创建 `frontend/src/types/asset-import.ts`：前端预览和确认协议类型。
- 创建 `frontend/src/lib/queries/asset-imports.ts`：上传、确认和缓存失效 hooks。
- 创建 `frontend/src/components/assets/asset-import-dialog.tsx`：三类资产复用的上传和字段差异对话框。
- 修改 `frontend/src/routes/_app/projects.$project/characters.lazy.tsx`：人物表入口与对话框。
- 修改 `frontend/src/components/assets/scenes-panel.tsx`：场景表入口与对话框。
- 修改 `frontend/src/components/assets/props-panel.tsx`：道具表入口与对话框。
- 修改 `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`：导入交互文案。
- 创建 `tests/test_asset_import_parsing.py`：分段、格式和证据测试。
- 创建 `tests/test_asset_import_service.py`：合并、并发保护、事务和作用域测试。
- 创建 `tests/test_api_asset_imports.py`：API 契约、校验、审计和幂等测试。
- 修改 `tests/test_structured_ingest_atomic.py`、`tests/test_structured_ingest.py`：剧本导入不再创建三类资产的回归测试。
- 创建 `frontend/src/__tests__/lib/queries/asset-imports.test.tsx`：请求和缓存测试。
- 创建 `frontend/src/__tests__/components/assets/asset-import-dialog.test.tsx`：对话框状态和差异呈现测试。
- 修改 `frontend/src/__tests__/routes/characters.ce.test.tsx`：三个面板入口与原显式构建入口并存测试。

## 任务 1：解除剧本摄入对正式资产的隐式发布

**文件：**
- 修改：`src/novelvideo/structured_publication.py`
- 修改：`src/novelvideo/structured_ingest.py`
- 修改：`src/novelvideo/structured_builders.py`
- 修改：`tests/test_structured_ingest_atomic.py`
- 修改：`tests/test_structured_ingest.py`

- [ ] **步骤 1：编写剧本导入不改变角色、场景、道具的失败测试**

将现有 `test_production_structured_ingest_publishes_complete_bundle_before_ready` 改为断言只发布剧集，并预置三类资产确认它们未变化：

```python
result = await ingest_source_text_structured(store, str(source_path), spine_template="drama")

assert result["published"] == {"episodes": 1}
assert store.get_episode(1).raw_content.startswith("第一集")
assert store.get_character("林默").role == "用户设定"
assert await store.get_scene("天台") is None
assert (await store.get_scene("旧场景")).environment_prompt == "用户旧场景"
assert store.get_prop("旧道具").description == "用户旧道具"
assert await store.list_entity_evidence("character", "林默") == []
```

另加一项保护显式构建 API 的测试，直接调用 `build_characters_structured` 的现有测试仍断言能提取角色。

- [ ] **步骤 2：运行定向测试确认失败**

运行：`uv run pytest tests/test_structured_ingest_atomic.py tests/test_structured_ingest.py tests/test_structured_graph_build.py -q`

预期：原摄入仍返回 `characters/scenes` 并创建场景，新的隔离断言失败；显式构建测试通过。

- [ ] **步骤 3：将 source publication 收窄为 episode-only**

删除 `build_structured_publication_from_source` 内角色抽取和场景解析，只生成没有规范资产引用的剧集输入：

```python
episode_inputs = [
    StructuredEpisodeInput(
        number=number,
        title=title,
        raw_content=content,
        summary=content[:200].strip() + ("..." if len(content) > 200 else ""),
        character_ids=(),
        scene_ids=(),
    )
    for number, _start, _end, title, content in ranges
]
return build_structured_publication(episodes=episode_inputs, characters=(), scenes=())
```

在 `publish_structured_publication` 返回值中只报告本次摄入拥有的实体：

```python
return {"episodes": len(publication.episodes)}
```

保留 `build_characters_structured`、`build_scenes_structured`、`build_props_structured` 及任务 runner，不增加隐藏触发。

- [ ] **步骤 4：运行测试验证隔离和显式构建均通过**

运行：`uv run pytest tests/test_structured_ingest_atomic.py tests/test_structured_ingest.py tests/test_structured_graph_build.py tests/test_task_run_core_text_runtime.py -q`

预期：全部通过；摄入只发布剧集，三个显式构建任务仍在 registry 中。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/structured_publication.py src/novelvideo/structured_ingest.py src/novelvideo/structured_builders.py tests/test_structured_ingest_atomic.py tests/test_structured_ingest.py
git commit -m "refactor(ingest): decouple canonical asset extraction"
```

## 任务 2：定义共享导入协议与文本结构解析器

**文件：**
- 创建：`src/novelvideo/asset_imports/__init__.py`
- 创建：`src/novelvideo/asset_imports/models.py`
- 创建：`src/novelvideo/asset_imports/parsing.py`
- 创建：`tests/test_asset_import_parsing.py`

- [ ] **步骤 1：编写格式、BOM、混合章节和角色条目边界的失败测试**

测试必须直接读取两个真实 fixture 的复制内容，避免依赖工作区外路径：

```python
def test_decode_accepts_utf8_bom_and_normalizes_newlines():
    assert decode_asset_table(b"\xef\xbb\xbf# \xe4\xba\xba\xe7\x89\xa9\xe8\xa1\xa8\r\n").text == "# 人物表\n"

def test_scene_sections_ignore_prop_table():
    doc = parse_asset_document(MIXED_SCENE_PROP_MARKDOWN)
    sections = select_sections(doc, AssetType.SCENE)
    assert "谢家碑坊" in "\n".join(section.text for section in sections)
    assert "缺角木尺" not in "\n".join(section.text for section in sections)

def test_character_entries_skip_information_boundary_mentions():
    entries = split_entries(CHARACTER_MARKDOWN, AssetType.CHARACTER)
    assert {entry.name for entry in entries} == {"谢砚秋", "苏娘", "柳阿绫"}
    assert "谢衡" not in {entry.name for entry in entries}
```

- [ ] **步骤 2：运行测试确认模块不存在**

运行：`uv run pytest tests/test_asset_import_parsing.py -q`

预期：收集失败，报 `ModuleNotFoundError: novelvideo.asset_imports`。

- [ ] **步骤 3：实现领域模型**

在 `models.py` 定义稳定协议；所有后续任务复用这些名称：

```python
class AssetType(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"

class FieldEvidence(BaseModel):
    field: str
    value: Any
    source_start: int
    source_end: int
    quote: str

class AssetCandidate(BaseModel):
    name: str
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[FieldEvidence] = Field(default_factory=list)
    source_code: str = ""

class FieldChange(BaseModel):
    field: str
    current: Any = None
    proposed: Any = None
    disposition: Literal["fill", "preserve", "conflict"]

class AssetDiff(BaseModel):
    name: str
    disposition: Literal["create", "supplement", "skip", "conflict"]
    changes: list[FieldChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

同时定义 `DecodedAssetTable`、`DocumentSection`、`SourceEntry`、`AssetImportPreview`、`AssetImportResult`，字段包括 `import_id`、`asset_type`、文件元数据、diffs、ignored_sections、warnings 和计数。

- [ ] **步骤 4：实现确定性文本解析**

在 `parsing.py` 实现：

```python
def decode_asset_table(filename: str, payload: bytes) -> DecodedAssetTable:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise ValueError("仅支持 .md 和 .txt 人物、场景或道具表")
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("导入文件为空")
    return DecodedAssetTable(filename=Path(filename).name, text=text, sha256=sha256(payload).hexdigest())
```

标题分类使用领域关键词集合；Markdown 表格第一列作为名称/编号列，只有目标表头命中时才产出条目。说明性章节如“人物信息边界”“连续性”“使用红线”作为附属规则，不能单独创建资产。

- [ ] **步骤 5：运行解析测试验证通过**

运行：`uv run pytest tests/test_asset_import_parsing.py -q`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add src/novelvideo/asset_imports tests/test_asset_import_parsing.py
git commit -m "feat(asset-imports): add shared parsing protocol"
```

## 任务 3：实现角色、场景、道具领域适配器

**文件：**
- 创建：`src/novelvideo/asset_imports/adapters.py`
- 修改：`src/novelvideo/asset_imports/__init__.py`
- 修改：`tests/test_asset_import_parsing.py`
- 测试：`tests/test_structured_extraction.py`

- [ ] **步骤 1：编写三类候选字段映射和证据拒绝测试**

```python
@pytest.mark.asyncio
async def test_character_adapter_maps_profile_without_name_guessing(fake_gateway):
    result = await CharacterSheetAdapter(gateway=fake_gateway).extract(CHARACTER_MARKDOWN)
    xie = next(item for item in result.candidates if item.name == "谢砚秋")
    assert xie.fields["description"]
    assert xie.fields["role"]
    assert "gender" not in xie.fields  # 人物块未明确性别
    assert all(ev.quote in CHARACTER_MARKDOWN for ev in xie.evidence)

@pytest.mark.asyncio
async def test_scene_adapter_maps_only_base_scene_fields(fake_gateway):
    scene = (await SceneSheetAdapter(gateway=fake_gateway).extract(MIXED_MARKDOWN)).candidates[0]
    assert scene.fields["scene_type"] == "interior"
    assert scene.fields["time_of_day"] == "夜／日"
    assert "base_scene_id" not in scene.fields
    assert "variant_id" not in scene.fields

@pytest.mark.asyncio
async def test_prop_adapter_defaults_uncertain_type_to_object(fake_gateway):
    prop = (await PropSheetAdapter(gateway=fake_gateway).extract(MIXED_MARKDOWN)).candidates[0]
    assert prop.fields["prop_type"] == "object"
    assert prop.fields["owner"] == "谢砚秋"
```

增加模型返回 quote 不在条目原文时丢弃该字段并生成 warning 的测试，以及重复同名字段矛盾时标记 conflict 的测试。

- [ ] **步骤 2：运行适配器测试确认失败**

运行：`uv run pytest tests/test_asset_import_parsing.py -q`

预期：失败，三个适配器尚未定义。

- [ ] **步骤 3：实现适配器注册表与字段白名单**

```python
ADAPTERS: dict[AssetType, type[AssetSheetAdapter]] = {
    AssetType.CHARACTER: CharacterSheetAdapter,
    AssetType.SCENE: SceneSheetAdapter,
    AssetType.PROP: PropSheetAdapter,
}

FIELD_ALLOWLIST = {
    AssetType.CHARACTER: {"aliases", "role", "is_main", "gender", "age_group", "body_type", "description", "face_prompt", "appearance_details"},
    AssetType.SCENE: {"aliases", "scene_type", "time_of_day", "environment_prompt", "description", "notes"},
    AssetType.PROP: {"aliases", "prop_type", "visual_prompt", "description", "owner", "notes"},
}
```

角色的 biography/occupation/social_identity/relationships/personality/dramatic_function 进入 `CharacterNarrativeProfile`；同时将 biography 回填空的 `NovelCharacter.description`、occupation 回填空的 `role`。场景和道具不创建变体/状态分身。

- [ ] **步骤 4：实现结构值优先和模型兜底**

复用项目现有文本模型网关模式，要求输出 `AssetCandidate`。每个字段通过以下校验后才保留：字段在白名单内、quote 在对应 `SourceEntry.text` 中、value 非空、没有与同名候选的已验证字段冲突。结构表格已明确的值优先于模型值。

```python
def verified_fields(candidate: AssetCandidate, entry: SourceEntry) -> dict[str, Any]:
    allowed = FIELD_ALLOWLIST[entry.asset_type]
    return {
        ev.field: ev.value
        for ev in candidate.evidence
        if ev.field in allowed and ev.quote and ev.quote in entry.text and _non_empty(ev.value)
    }
```

- [ ] **步骤 5：运行适配器与现有角色提取测试**

运行：`uv run pytest tests/test_asset_import_parsing.py tests/test_structured_extraction.py -q`

预期：全部通过，现有显式角色抽取无回归。

- [ ] **步骤 6：提交**

```bash
git add src/novelvideo/asset_imports/adapters.py src/novelvideo/asset_imports/__init__.py tests/test_asset_import_parsing.py
git commit -m "feat(asset-imports): infer domain-specific candidates"
```

## 任务 4：增加预览、审计和字段证据持久化

**文件：**
- 修改：`src/novelvideo/sqlite_store.py`
- 创建：`tests/test_asset_import_service.py`

- [ ] **步骤 1：编写预览作用域、过期和一次性状态测试**

```python
@pytest.mark.asyncio
async def test_import_preview_is_scoped_and_single_use(store):
    await store.save_asset_import_preview(PREVIEW, user_id="alice", expires_at="2099-01-01T00:00:00Z")
    assert await store.get_asset_import_preview(PREVIEW.import_id, user_id="alice", asset_type="scene")
    assert await store.get_asset_import_preview(PREVIEW.import_id, user_id="bob", asset_type="scene") is None
    await store.mark_asset_import_confirmed(PREVIEW.import_id, user_id="alice")
    assert await store.get_asset_import_preview(PREVIEW.import_id, user_id="alice", asset_type="scene") is None
```

另测过期预览不可读取，完整上传正文不出现在审计 metadata。

- [ ] **步骤 2：运行测试确认数据库 API 不存在**

运行：`uv run pytest tests/test_asset_import_service.py -q`

预期：失败，`save_asset_import_preview` 未定义。

- [ ] **步骤 3：增加项目数据库表**

在 `SQLITE_SCHEMA_SQL` 增加：

```sql
CREATE TABLE IF NOT EXISTS asset_import_previews (
    import_id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    source_text TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    confirmed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS asset_import_field_evidence (
    import_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    field_name TEXT NOT NULL,
    source_start INTEGER NOT NULL,
    source_end INTEGER NOT NULL,
    evidence_text TEXT NOT NULL,
    PRIMARY KEY (import_id, asset_type, asset_name, field_name, source_start)
);
```

实现保存、作用域读取和成功后标记确认的方法；`source_text` 只存在项目 DB，不进入日志或公共审计 metadata。

- [ ] **步骤 4：运行存储测试验证通过**

运行：`uv run pytest tests/test_asset_import_service.py -q`

预期：预览生命周期测试通过。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/sqlite_store.py tests/test_asset_import_service.py
git commit -m "feat(asset-imports): persist scoped import previews"
```

## 任务 5：实现仅补空字段的确认事务

**文件：**
- 创建：`src/novelvideo/asset_imports/service.py`
- 修改：`src/novelvideo/sqlite_store.py`
- 修改：`src/novelvideo/asset_imports/__init__.py`
- 修改：`tests/test_asset_import_service.py`

- [ ] **步骤 1：编写三类资产合并和并发保护的失败测试**

```python
@pytest.mark.asyncio
async def test_confirm_fills_empty_fields_and_preserves_non_empty(store, service):
    await store.add_character(NovelCharacter(name="谢砚秋", role="用户角色", description=""))
    preview = await service.preview(AssetType.CHARACTER, "04_人物表.md", CHARACTER_BYTES, "alice")
    await store.update_character("谢砚秋", description="用户在预览后填写")
    result = await service.confirm(preview.import_id, AssetType.CHARACTER, "alice")
    current = store.get_character("谢砚秋")
    assert current.role == "用户角色"
    assert current.description == "用户在预览后填写"
    assert "description" in result.skipped_fields["谢砚秋"]
```

场景测试保护 `spatial_layout_image`、`base_scene_id`、`variant_id`、`variant_prompt` 和 stale reference；道具测试保护已有引用图相关文件/资产槽。增加 `prop_type="object"` 可由有证据的具体分类补全、非默认值不可覆盖的测试。增加 SQL trigger 制造中途失败并断言整批回滚、预览仍为 pending 的测试。

- [ ] **步骤 2：运行服务测试确认失败**

运行：`uv run pytest tests/test_asset_import_service.py -q`

预期：失败，预览/确认服务和补空事务尚未实现。

- [ ] **步骤 3：实现空值策略和差异计算**

```python
TECHNICAL_DEFAULTS = {
    AssetType.CHARACTER: {"age_group": "youth"},
    AssetType.SCENE: {"scene_type": "interior"},
    AssetType.PROP: {"prop_type": "object"},
}

def is_fillable(asset_type: AssetType, field: str, current: Any) -> bool:
    if current is None or current == "" or current == [] or current == {}:
        return True
    return TECHNICAL_DEFAULTS.get(asset_type, {}).get(field) == current
```

只有候选提供相同字段且证据有效时，技术默认值才可替换。对 aliases 等数组，已有非空数组整体保留，不做隐式 union。

- [ ] **步骤 4：实现单事务确认**

在 `SQLiteStore.confirm_asset_import_atomic(...)` 中使用 `BEGIN IMMEDIATE`，事务内重新读取每条资产、计算可填字段、执行参数化 UPDATE/INSERT、写 field evidence，最后将 preview 标记 `confirmed`。异常时 `rollback()`，成功后刷新内存态。

角色丰富资料通过 `CharacterVisualWorkspaceStore` 写入文件，需要和 SQLite 组成一个显式 unit of work。将该过程封装为 `CharacterProfilePublication`：先备份旧 workspace 并写同目录临时文件；开启 `BEGIN IMMEDIATE` 并完成数据库更新；在数据库提交前用原子 rename 发布 workspace；rename 失败则回滚数据库并恢复备份；数据库 commit 失败也恢复备份。只有两侧都成功才将 preview 标记 confirmed，禁止直接零散调用 `save()`。测试分别注入 rename 和 commit 失败，断言数据库与 workspace 都恢复旧值。

- [ ] **步骤 5：运行合并、并发和回滚测试**

运行：`uv run pytest tests/test_asset_import_service.py tests/character_visual/test_store.py -q`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add src/novelvideo/asset_imports/service.py src/novelvideo/asset_imports/__init__.py src/novelvideo/sqlite_store.py tests/test_asset_import_service.py
git commit -m "feat(asset-imports): confirm fill-only imports atomically"
```

## 任务 6：暴露预览与确认 API

**文件：**
- 创建：`src/novelvideo/api/routes/asset_imports.py`
- 修改：`src/novelvideo/api/__init__.py`
- 创建：`tests/test_api_asset_imports.py`

- [ ] **步骤 1：编写 API 契约和错误测试**

```python
def test_preview_and_confirm_contract(client):
    preview = client.post(
        "/api/v1/projects/demo/asset-imports/scene/preview",
        files={"file": ("05_场景与道具.md", MIXED_BYTES, "text/markdown")},
    )
    assert preview.status_code == 200
    payload = preview.json()["data"]
    assert payload["asset_type"] == "scene"
    assert all(item["name"] != "缺角木尺" for item in payload["diffs"])

    confirmed = client.post(
        f"/api/v1/projects/demo/asset-imports/scene/{payload['import_id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["created_count"] > 0
```

参数化测试非法 `asset_type`、错误扩展名、空文件、超限文件、跨用户/跨项目确认、过期和重复确认；期望分别为 422、400/413、404 或 409，并验证错误正文不含完整上传内容。

- [ ] **步骤 2：运行 API 测试确认路由不存在**

运行：`uv run pytest tests/test_api_asset_imports.py -q`

预期：请求返回 404。

- [ ] **步骤 3：实现 FastAPI 路由**

```python
router = APIRouter(prefix="/projects/{project}/asset-imports")

@router.post("/{asset_type}/preview")
async def preview_asset_import(project: str, asset_type: AssetType, file: UploadFile, user=Depends(get_api_user)):
    resolved = resolve_project_scope(user, project)
    payload = await read_upload_with_limit(file, MAX_ASSET_TABLE_BYTES)
    async with get_cognee_store(resolved) as store:
        preview = await AssetImportService(store).preview(asset_type, file.filename or "", payload, resolved.ctx.requester_user_id)
    await emit_project_audit(action="asset_import.preview", ctx=resolved.ctx, metadata=preview.audit_metadata())
    return {"ok": True, "data": preview.model_dump(mode="json")}
```

确认接口使用同一 `resolve_project_scope` 和用户 ID，映射领域异常为稳定 HTTP 状态。审计 metadata 仅含 asset_type、文件名、哈希、计数、import_id，不含 `source_text`。

- [ ] **步骤 4：注册路由并运行契约测试**

在 `src/novelvideo/api/__init__.py` 导入 `asset_imports` 并 `include_router(asset_imports.router, tags=["asset-imports"])`。

运行：`uv run pytest tests/test_api_asset_imports.py tests/contract/test_m06_route_contracts.py -q`

预期：全部通过。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/api/routes/asset_imports.py src/novelvideo/api/__init__.py tests/test_api_asset_imports.py
git commit -m "feat(api): add external asset table import endpoints"
```

## 任务 7：增加前端请求层和共享导入对话框

**文件：**
- 创建：`frontend/src/types/asset-import.ts`
- 创建：`frontend/src/lib/queries/asset-imports.ts`
- 创建：`frontend/src/components/assets/asset-import-dialog.tsx`
- 创建：`frontend/src/__tests__/lib/queries/asset-imports.test.tsx`
- 创建：`frontend/src/__tests__/components/assets/asset-import-dialog.test.tsx`

- [ ] **步骤 1：编写请求和对话框失败测试**

```tsx
it("uploads the selected file and confirms only by import id", async () => {
  render(<AssetImportDialog project="demo" assetType="scene" open onOpenChange={vi.fn()} />);
  await userEvent.upload(screen.getByLabelText("导入文件"), new File(["# 场景表"], "scenes.md"));
  await userEvent.click(screen.getByRole("button", { name: "解析预览" }));
  expect(await screen.findByText("新增 1 个场景")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(confirmBody).toEqual(undefined);
  expect(confirmUrl).toContain("/asset-imports/scene/import-1/confirm");
});
```

增加 `.txt` 可选、`.pdf` 前端拒绝、预览呈现 create/fill/preserve/conflict、过期错误回到上传态、取消不确认的测试。

- [ ] **步骤 2：运行前端测试确认模块不存在**

运行：`cd frontend && npm test -- --run src/__tests__/lib/queries/asset-imports.test.tsx src/__tests__/components/assets/asset-import-dialog.test.tsx`

预期：测试因导入模块不存在而失败。

- [ ] **步骤 3：实现协议类型与 mutations**

```ts
export type AssetImportType = "character" | "scene" | "prop";
export interface AssetImportPreview {
  import_id: string;
  asset_type: AssetImportType;
  filename: string;
  diffs: AssetImportDiff[];
  ignored_sections: string[];
  warnings: string[];
  created_count: number;
  supplemented_count: number;
  skipped_count: number;
}
```

`usePreviewAssetImport` 构建 `FormData`；`useConfirmAssetImport` 发送无客户端候选正文的 POST。确认成功后按 asset type 失效 `queryKeys.characters/scenes/props(project)`。

- [ ] **步骤 4：实现共享对话框状态机**

对话框状态限定为 `selecting | parsing | preview | confirming | success | error`。预览按资产分组并显示字段当前值、候选值和 disposition；`preserve` 明确显示不会覆盖。关闭对话框时清空本地文件和 import id，不发送确认。

- [ ] **步骤 5：运行共享前端测试**

运行：`cd frontend && npm test -- --run src/__tests__/lib/queries/asset-imports.test.tsx src/__tests__/components/assets/asset-import-dialog.test.tsx`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add frontend/src/types/asset-import.ts frontend/src/lib/queries/asset-imports.ts frontend/src/components/assets/asset-import-dialog.tsx frontend/src/__tests__/lib/queries/asset-imports.test.tsx frontend/src/__tests__/components/assets/asset-import-dialog.test.tsx
git commit -m "feat(frontend): add shared asset import preview dialog"
```

## 任务 8：接入三个资产面板并保留原显式构建入口

**文件：**
- 修改：`frontend/src/routes/_app/projects.$project/characters.lazy.tsx`
- 修改：`frontend/src/components/assets/scenes-panel.tsx`
- 修改：`frontend/src/components/assets/props-panel.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`frontend/src/__tests__/routes/characters.ce.test.tsx`

- [ ] **步骤 1：编写三个入口和原按钮并存的失败测试**

```tsx
expect(screen.getByRole("button", { name: "导入人物表" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: /自动提取/ })).toBeInTheDocument();

await userEvent.click(screen.getByRole("tab", { name: "场景" }));
expect(screen.getByRole("button", { name: "导入场景表" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: /从知识图谱构建/ })).toBeInTheDocument();

await userEvent.click(screen.getByRole("tab", { name: "道具" }));
expect(screen.getByRole("button", { name: "导入道具表" })).toBeInTheDocument();
```

点击每个入口后断言 `AssetImportDialog` 收到对应 `assetType`，不触发 build mutation。

- [ ] **步骤 2：运行路由测试确认入口不存在**

运行：`cd frontend && npm test -- --run src/__tests__/routes/characters.ce.test.tsx`

预期：找不到三个导入按钮。

- [ ] **步骤 3：将对话框接入角色页**

在 `CharactersPageContent` 增加 `characterImportOpen`；把 `onImport` 传入 `CharactersPageHeader`，保留现有 `onRebuild`：

```tsx
<AssetImportDialog
  project={project}
  assetType="character"
  open={characterImportOpen}
  onOpenChange={setCharacterImportOpen}
/>
```

- [ ] **步骤 4：接入场景和道具面板**

两个面板各自维护独立 open state，按钮放在现有创建/构建动作区域，不改变 `useBuildScenes` 或已有道具构建动作：

```tsx
<AssetImportDialog project={project} assetType="scene" open={importOpen} onOpenChange={setImportOpen} />
```

道具对应 `assetType="prop"`。同一文件必须经两个对话框产生两个 import id。

- [ ] **步骤 5：补齐中英文文案并运行测试**

文案键统一置于 `assets.imports`，至少包含三个按钮标题、格式提示、解析/确认动作、四种 disposition、过期、无候选和结果计数。

运行：`cd frontend && npm test -- --run src/__tests__/routes/characters.ce.test.tsx src/__tests__/components/assets/asset-import-dialog.test.tsx src/__tests__/i18n/locales-json.test.ts`

预期：全部通过。

- [ ] **步骤 6：提交**

```bash
git add frontend/src/routes/_app/projects.\$project/characters.lazy.tsx frontend/src/components/assets/scenes-panel.tsx frontend/src/components/assets/props-panel.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json frontend/src/__tests__/routes/characters.ce.test.tsx
git commit -m "feat(assets): expose external table imports in asset panels"
```

## 任务 9：真实样例、全链路回归与文档收口

**文件：**
- 创建：`tests/fixtures/asset_imports/04_人物表.md`
- 创建：`tests/fixtures/asset_imports/05_场景与道具.md`
- 修改：`tests/test_asset_import_parsing.py`
- 修改：`tests/test_api_asset_imports.py`
- 修改：`docs/superpowers/specs/2026-09-06-external-asset-table-import-design.md`（仅在实现暴露准确性差异时同步修正）

- [ ] **步骤 1：复制脱敏后的真实结构 fixture 并写端到端断言**

fixture 保留标题层级、字段列表和表格结构。测试从 API 分别执行三次 preview/confirm：

```python
assert character_names == {"谢砚秋", "陆无咎", "顾闻章", "石九", "苏娘", "何元簿", "赵班头", "柳阿绫", "周循", "秦山长", "程砚生", "宋讼师", "阿芷"}
assert scene_names == {"谢家碑坊", "县衙档房", "无名废寺", "旧河堤", "修志局", "砚川县衙公堂", "柳家旧村界", "澄州书院讲堂"}
assert prop_names == {"五块无名碑", "双层拓片", "缺角木尺", "黑玉镇纸", "谢衡旧护指", "首碑工匠计数牌", "官府伪旧拓", "无字诉状"}
```

再次导入相同文件，断言全部 skip 且没有覆盖第一次确认后手工修改的字段。

- [ ] **步骤 2：运行后端全套定向回归**

运行：`uv run pytest tests/test_asset_import_parsing.py tests/test_asset_import_service.py tests/test_api_asset_imports.py tests/test_structured_ingest.py tests/test_structured_ingest_atomic.py tests/test_structured_graph_build.py tests/test_api_assets.py -q`

预期：全部通过。

- [ ] **步骤 3：运行前端定向回归和类型检查**

运行：`cd frontend && npm test -- --run src/__tests__/lib/queries/asset-imports.test.tsx src/__tests__/components/assets/asset-import-dialog.test.tsx src/__tests__/routes/characters.ce.test.tsx src/__tests__/i18n/locales-json.test.ts`

预期：全部通过。

运行：`cd frontend && npm run build`

预期：TypeScript 检查和生产构建成功。

- [ ] **步骤 4：运行仓库质量检查**

运行：`git diff --check`

预期：无输出，退出码 0。

运行：`uv run pytest tests/test_task_backend_registry.py tests/test_task_run_core_text_runtime.py -q`

预期：显式 `build_characters`、`build_scenes`、`build_props` 仍注册且测试通过。

- [ ] **步骤 5：提交 fixture 和最后修正**

```bash
git add tests/fixtures/asset_imports tests/test_asset_import_parsing.py tests/test_api_asset_imports.py docs/superpowers/specs/2026-09-06-external-asset-table-import-design.md
git commit -m "test(asset-imports): cover external table workflow end to end"
```

若规格文件未发生变化，提交命令中去掉该路径；不得制造无意义文档改动。
