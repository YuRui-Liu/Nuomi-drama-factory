# 局部素材图源动态模型目录实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让角色、场景、道具的局部“图源”下拉只展示当前已配置且可执行的生图模型，同时保持三类选择相互独立且不修改项目全局默认模型。

**架构：** 新增一个凭据安全的图片模型目录模块，从媒体能力存储中的启用 GRSAI 提供方和可解析凭据生成稳定目录；素材选择 API 与 Freezone 图片模型 API 共用该目录。局部配置保存原始图片模型 ID，读取时对失效值回退，生成入口继续将该局部模型传给现有 GRSAI 执行链路。

**技术栈：** Python 3、FastAPI、Pydantic、pytest、React 19、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构

- 创建 `src/novelvideo/media_capabilities/image/catalog.py`：构建不泄露凭据的可用图片模型目录，并提供选择校验与默认回退。
- 创建 `tests/media_capabilities/image/test_catalog.py`：覆盖启用状态、凭据可用性、稳定顺序和空目录。
- 修改 `src/novelvideo/api/routes/characters.py`：让素材图源读写接口使用动态目录，保留三类独立配置。
- 修改 `src/novelvideo/api/routes/freezone.py`：复用同一图片模型目录。
- 修改 `tests/test_api_character_image_selection.py`：覆盖动态候选项、局部保存、失效回退和全局配置不变。
- 修改 `tests/test_freezone_image_backend.py`：验证 Freezone 与素材图源目录一致。
- 修改 `frontend/src/components/assets/character-image-source-select.tsx`：为空目录提供明确禁用状态，不内置任何模型名。
- 修改 `frontend/src/__tests__/components/assets/character-image-source-select.test.tsx`：用实际动态模型名验证渲染、局部保存和空目录。
- 按测试暴露的最小范围修改角色、场景或道具生成解析代码，使原始 GRSAI 模型 ID 原样进入媒体能力执行链路。

### 任务 1：建立动态图片模型目录

**文件：**
- 创建：`src/novelvideo/media_capabilities/image/catalog.py`
- 创建：`tests/media_capabilities/image/test_catalog.py`

- [ ] **步骤 1：编写失败的目录测试**

测试建立一个启用的 `grsai-main` 提供方，凭据解析器返回密钥，并断言目录包含 `GRSAI_IMAGE_MODELS` 中的模型 ID、可读标签和 `grsai-main` 提供方 ID；再覆盖禁用提供方、缺失凭据和无提供方时返回空目录。

```python
def test_list_image_models_uses_enabled_grsai_provider_with_credential(store, credentials):
    store.save_provider(grsai_provider(model="gpt-image-2"))
    credentials.set("dramaclaw/media/grsai-main", "secret")

    catalog = list_image_models(store, CredentialResolver(keyring_reader=credentials.get))

    assert {item.id for item in catalog} == set(GRSAI_IMAGE_MODELS)
    assert all(item.provider_id == "grsai-main" for item in catalog)


def test_list_image_models_is_empty_when_provider_is_not_executable(store, credentials):
    store.save_provider(grsai_provider(enabled=False))
    assert list_image_models(store, CredentialResolver(keyring_reader=credentials.get)) == ()
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/media_capabilities/image/test_catalog.py -q
```

预期：FAIL，原因是 `novelvideo.media_capabilities.image.catalog` 尚不存在。

- [ ] **步骤 3：实现最小目录模块**

定义不可变的目录项，并只在 GRSAI 提供方启用且凭据可解析时公开支持模型：

```python
class ImageModelCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    label: str
    provider_id: str
    provider: str = "grsai"


def list_image_models(store: MediaCapabilityStore, resolver: CredentialResolver) -> tuple[ImageModelCatalogItem, ...]:
    account = store.get_provider("grsai-main")
    if account is None or account.provider_type != "grsai" or not account.enabled:
        return ()
    try:
        resolver.resolve(account.credential_ref)
    except Exception:
        return ()
    return tuple(
        ImageModelCatalogItem(id=model, label=model, provider_id=account.id)
        for model in sorted(GRSAI_IMAGE_MODELS)
    )
```

把 `account.model` 对应项稳定排在第一位，使读取失效值时回退到当前提供方默认模型；其余条目按稳定顺序排列。

- [ ] **步骤 4：运行目录测试验证绿灯**

运行同一步骤 2，预期全部 PASS。

- [ ] **步骤 5：提交目录模块**

```bash
git add src/novelvideo/media_capabilities/image/catalog.py tests/media_capabilities/image/test_catalog.py
git commit -m "feat: catalog available image models"
```

### 任务 2：素材图源接口改用动态目录

**文件：**
- 修改：`src/novelvideo/api/routes/characters.py`
- 修改：`tests/test_api_character_image_selection.py`

- [ ] **步骤 1：编写失败的 API 测试**

为 `GET/PATCH /projects/{project}/image-source-selection/{asset_kind}` 注入媒体能力存储和凭据存储，验证：

```python
def test_asset_image_source_uses_runtime_catalog_and_keeps_kind_local(client, project_config):
    response = client.get("/api/v1/projects/demo/image-source-selection/character")
    assert response.json()["data"]["options"]["gpt-image-2"] == "gpt-image-2"
    assert "newapi_gpt_image2" not in response.json()["data"]["options"]

    updated = client.patch(
        "/api/v1/projects/demo/image-source-selection/character",
        json={"image_source_selection": "gpt-image-2-vip"},
    )
    assert updated.status_code == 200
    assert project_config()["character_image_selection"] == "gpt-image-2-vip"
    assert project_config()["render_image_selection"] == "gpt-image-2"
```

另加测试：场景与道具键互不影响；失效保存值回退到目录第一项；空目录返回空选择/空 options；PATCH 拒绝目录外模型。

- [ ] **步骤 2：运行 API 测试验证红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_api_character_image_selection.py -q
```

预期：FAIL，响应仍包含 `newapi_gpt_image2` / LingShan 标签，且 PATCH 不接受 `gpt-image-2-vip`。

- [ ] **步骤 3：注入目录依赖并实现读取、回退与保存校验**

给素材图源 GET/PATCH 路由增加 `MediaCapabilityStore` 与凭据存储依赖，使用 `CredentialResolver` 构建目录。将目录映射为 `{item.id: item.label}`；读取时保留仍有效的局部值，否则选择目录第一项；目录为空时选择为空。PATCH 只接受目录中的 ID，且只写入 `ASSET_IMAGE_SELECTION_CONFIG_KEYS[asset_kind]`。

保留旧 `/character-image-selection` 合约用于兼容，但让它调用相同的角色局部选择逻辑，避免两条角色路径分叉。

- [ ] **步骤 4：运行 API 测试验证绿灯**

运行同一步骤 2，预期全部 PASS。

- [ ] **步骤 5：提交素材 API 改动**

```bash
git add src/novelvideo/api/routes/characters.py tests/test_api_character_image_selection.py
git commit -m "fix: source asset models from runtime catalog"
```

### 任务 3：让 Freezone 复用同一目录

**文件：**
- 修改：`src/novelvideo/api/routes/freezone.py`
- 修改：`tests/test_freezone_image_backend.py`

- [ ] **步骤 1：编写失败的一致性测试**

注入与任务 2 相同的媒体存储和凭据，断言 `/freezone/image/models` 返回同一组 ID、label、provider/providerId/apiModel 字段；当凭据缺失时返回空数组，不回退到 LingShan 常量。

- [ ] **步骤 2：运行测试验证红灯**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_freezone_image_backend.py -q -k "image_models"
```

预期：FAIL，旧实现仍从 `image_generation_selection_options()` 构建 LingShan 列表。

- [ ] **步骤 3：替换为共享目录构建器**

路由通过依赖获得 store/credential store，调用 `list_image_models()`，只在 Freezone 响应适配现有 camelCase 与 snake_case 字段，不复制可用性判断。

- [ ] **步骤 4：运行一致性测试验证绿灯**

运行同一步骤 2，预期全部 PASS。

- [ ] **步骤 5：提交 Freezone 复用改动**

```bash
git add src/novelvideo/api/routes/freezone.py tests/test_freezone_image_backend.py
git commit -m "refactor: share available image model catalog"
```

### 任务 4：验证局部模型进入三类生成链路

**文件：**
- 测试：`tests/test_api_character_identity_costume.py`
- 测试：`tests/test_scene_reference_runner.py`
- 测试：`tests/test_prop_reference_runner.py`
- 按红测定位后最小修改对应生成解析文件。

- [ ] **步骤 1：编写三条失败的传递测试**

分别选择 `gpt-image-2-vip`，断言角色、场景、道具生成最终提交给 GRSAI 的请求模型仍为 `gpt-image-2-vip`，且不会被 `normalize_image_generation_selection()` 改回 legacy LingShan 选择键。

- [ ] **步骤 2：运行测试验证红灯或记录现有绿灯**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_api_character_identity_costume.py tests/test_scene_reference_runner.py tests/test_prop_reference_runner.py -q
```

预期：若某链路仍规范化为 legacy 选择键则对应测试 FAIL；已正确传递的链路保持 PASS，不改生产代码。

- [ ] **步骤 3：只修复实际红灯的模型解析点**

模型解析规则统一为：目录中的原始 GRSAI 模型 ID原样传递；仅对仍受支持的历史选择键执行兼容映射。不得修改提示词、尺寸、队列或资产落盘逻辑。

- [ ] **步骤 4：运行三类生成测试验证绿灯**

运行同一步骤 2，预期全部 PASS。

- [ ] **步骤 5：提交生成传递改动**

```bash
git add tests/test_api_character_identity_costume.py tests/test_scene_reference_runner.py tests/test_prop_reference_runner.py src/novelvideo/api/routes/characters.py src/novelvideo/generators/image_generator.py src/novelvideo/generators/scene_reference_images.py src/novelvideo/task_backend/runners/prop_reference.py
git commit -m "fix: preserve local asset image model selection"
```

只添加本任务实际修改的文件，未修改的候选文件不得加入提交。

### 任务 5：前端动态选项与空目录状态

**文件：**
- 修改：`frontend/src/components/assets/character-image-source-select.tsx`
- 修改：`frontend/src/__tests__/components/assets/character-image-source-select.test.tsx`

- [ ] **步骤 1：编写失败的前端测试**

将 mock 候选项改成 `gpt-image-2` 与 `nano-banana-2`，验证控件原样显示后端名称并 PATCH 原始 ID；新增 `options: {}` 测试，断言下拉禁用且不出现 LingShan 文本。

- [ ] **步骤 2：运行测试验证红灯**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-image-source-select.test.tsx
```

预期：动态模型用例通过现有行为；空目录状态用例 FAIL，因为当前控件没有明确的无可用模型展示。

- [ ] **步骤 3：实现最小空目录状态**

在禁用条件中加入 `!selectionQuery.isLoading && optionEntries.length === 0`，并让 `SelectValue` 在空目录时显示翻译键 `characters.imageSource.unavailable`。在中英文翻译文件中加入“暂无可用生图模型”/“No image models available”。

- [ ] **步骤 4：运行前端测试验证绿灯**

运行同一步骤 2，预期全部 PASS。

- [ ] **步骤 5：提交前端改动**

```bash
git add frontend/src/components/assets/character-image-source-select.tsx frontend/src/__tests__/components/assets/character-image-source-select.test.tsx frontend/public/locales/en/translation.json frontend/public/locales/zh/translation.json
git commit -m "fix: show runtime models in asset source picker"
```

### 任务 6：综合验证

**文件：**
- 不新增文件。

- [ ] **步骤 1：运行聚焦后端测试**

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/media_capabilities/image/test_catalog.py tests/test_api_character_image_selection.py tests/test_freezone_image_backend.py tests/test_api_character_identity_costume.py tests/test_scene_reference_runner.py tests/test_prop_reference_runner.py -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行聚焦前端测试与类型检查**

```bash
pnpm --dir frontend exec vitest run src/__tests__/components/assets/character-image-source-select.test.tsx src/__tests__/lib/queries/character-image-selection.test.tsx
pnpm --dir frontend exec tsc -b
```

预期：全部 PASS，TypeScript 退出码为 0。

- [ ] **步骤 3：运行静态差异检查**

```bash
git diff --check
git status --short
```

预期：`git diff --check` 无输出；状态只包含本计划明确产生的提交或计划文档。

- [ ] **步骤 4：提交计划执行记录（如有）**

若勾选了计划步骤，则只提交本计划文件：

```bash
git add -f docs/superpowers/plans/2026-09-08-local-asset-image-model-catalog.md
git commit -m "docs: record local asset image model implementation"
```
