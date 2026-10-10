# 全局配音资产实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 逐任务内联执行；保留工作区其他修改，只提交本功能明确拥有的变更。

**目标：** 已通过角色配音可手动收藏、分类标注，并在其他项目试听、筛选和采用。

**架构：** 全局资产服务保存独立音频及标签，项目采用服务把音频复制为目标角色候选并复用审核流程。前端通过独立收藏表单和选择器接入已有角色面板。

**技术栈：** Python、FastAPI、SQLite、React、TanStack Query、pytest、Vitest。

**规格：** `docs/superpowers/specs/2026-10-10-global-voice-assets-design.md`。

## 执行边界

仓库已有大量未提交修改，角色面板也有既有改动；不得覆盖、还原或整体提交这些修改。本计划不执行付费声音生成，不迁移历史数据。新增文件用文件编辑工具；遵循 AGENTS.md 的 Weaver 规则。

全局库保存在 `config.STATE_DIR/global_voice_assets/`，索引 `index.sqlite3`，音频 `audio/<asset_id>.<ext>`。服务端根据认证上下文解析资产所有者，跨项目复用仍限定同一所有者；CE 单用户模式使用其既有本地身份。不得把缺失身份的远端请求回退成本地用户。来源项目必须可读，目标项目必须可编辑；受限 agent 会话沿用现有作用域检查。

## 任务 1：独立存储、去重和查询

文件：创建 `src/novelvideo/voice_assets/models.py`、`src/novelvideo/voice_assets/store.py`、`src/novelvideo/voice_assets/__init__.py`、`tests/test_global_voice_asset_store.py`。

- [ ] 编写存储测试：两个相同内容收藏返回同一 ID；两个所有者互不可见；并发收藏只有一条；重启后文件及标签仍存在；写入失败不发布索引。
- [ ] 运行 `uv run pytest tests/test_global_voice_asset_store.py -q`，确认尚无模块导致失败。
- [ ] 实现模型：名称 1–100 字，描述最多 2000 字；`tags.timbre`、`tags.identity`、`tags.personality` 各最多 20 项，每项 1–30 字，去空白与组内去重。固定资产 ID、所有者、哈希、格式、时长、时间戳及来源记录。
- [ ] 用 SQLite 唯一约束 `(owner_id, sha256)` 保证去重；文件先写临时路径并原子替换，再事务发布索引。并发竞争者读取已存在记录；失败清理仅限自己创建且未被引用的文件。
- [ ] 查询实现名称包含搜索与标签组合；绑定 SQL 参数，名称中的 `%`、`_` 作为普通字符处理。固定更新时间倒序、ID 次序，返回 `items/total/offset/limit`。

匹配语义必须等价于：

```python
def matches_tags(asset_tags, selected):
    return all(
        not values or bool(set(values) & set(asset_tags.get(group, [])))
        for group, values in selected.items()
    )
```

- [ ] 增加组合查询、名称特殊字符、标签规范化、分页及删除后项目副本不受影响的测试，运行同一测试命令至通过。
- [ ] 单独提交新增存储模块与测试：`feat: persist reusable voice assets`。

## 任务 2：收藏、编辑、试听和删除接口

文件：创建 `src/novelvideo/api/routes/global_voice_assets.py`、`tests/test_global_voice_asset_api.py`；修改 `src/novelvideo/api/__init__.py` 注册路由。

- [ ] 编写路由测试：未通过候选返回 409，错误来源返回 404，无权限拒绝，重复收藏返回已存在资产但不覆盖标签。
- [ ] 运行 `uv run pytest tests/test_global_voice_asset_api.py -q`，确认新接口不存在。
- [ ] 实现以下契约，统一沿用 `{ok, data}` / `{ok:false, error}` 响应：

```text
POST   /projects/{project}/characters/{name}/voice-candidates/{candidate_id}/favorite
       body: {name, description, tags:{timbre:[], identity:[], personality:[]}}
       data: {asset, created}
GET    /voice-assets?q=&timbre=&identity=&personality=&offset=0&limit=30
       每类标签可用重复 query 参数；limit 范围 1–100
GET    /voice-assets/tags
       data: {timbre:[], identity:[], personality:[]}
PATCH  /voice-assets/{asset_id}
       body: {name, description, tags}
GET    /voice-assets/{asset_id}/audio
DELETE /voice-assets/{asset_id}
```

- [ ] 收藏用 `resolve_voice_scope(..., read_only=True)` 和候选角色归属检查，重新读取 `status == approved`，通过 `VoiceCandidateStore.read_audio` 校验文件及哈希。不信任客户端路径、时长和通过标记。
- [ ] 音频试听走带认证的文件响应，前端采用认证请求获取 Blob；不新增匿名静态目录。来源信息只返回当前用户可访问的摘要，来源失效不影响已收藏文件。
- [ ] 文件缺失返回 409；非所有者读取、修改、删除返回 404。删除在存储层与采用读取协调，避免采用过程中被删文件；音频已读取后使用该内容完成目标副本。
- [ ] 测试包括查询标签不泄露其他所有者数据、重复提交及缺失文件；运行路由与存储测试至通过。
- [ ] 仅提交该任务文件：`feat: expose voice asset collection and search`。

## 任务 3：目标角色采用与生产前检查

文件：创建 `src/novelvideo/voice_assets/adoption.py`、`tests/test_global_voice_asset_adoption.py`；扩展全局配音路由。

- [ ] 阅读 `VoiceCandidateStore.create/save_audio/save_report/approve` 以及 `voice_candidates.voice_readiness`，复用现有质量规则、目标角色快照、槽位预期引用检查。
- [ ] 编写 A 项目收藏、B 项目采用的测试：目标文件位于 B，采用记录绑定 B 的角色资料，生产前检查认可目标角色记录，来源删除后仍可用。
- [ ] 运行 `uv run pytest tests/test_global_voice_asset_adoption.py -q` 确认失败。
- [ ] 实现契约：

```text
POST /projects/{project}/characters/{name}/voice-assets/{asset_id}/adopt
body: {slot, expected_reference:{path,sha256}, request_id}
data: {candidate, asset_id}
```

- [ ] 校验目标编辑权限、槽位和当前角色适用性。根据目标角色当前资料创建候选，保存独立音频、运行本地质量检查，再通过现有 `approve` 显式采用。记录资产 ID、源哈希、采用时名称与标签、操作者。不得直接拷贝来源角色的 `profile_digest` 或绕过质量拒绝。
- [ ] `request_id` 与目标、资产、槽位绑定，同一请求重试返回原结果；参数不同则 409。槽位冲突返回 409，旧声音保持有效；失败候选不得显示为已采用，清理本次未引用文件。
- [ ] 增加默认与年龄槽位、角色资料不兼容、低质量阻止采用、重复请求、并发槽位变化及全局资产删除测试。测试不用模型调用。
- [ ] 运行前三组新增测试及既有角色配音候选测试至通过，单独提交：`feat: adopt global voices into project characters`。

## 任务 4：前端查询与收藏表单

文件：创建 `frontend/src/lib/queries/global-voice-assets.ts`、`frontend/src/components/assets/voice-asset-favorite-dialog.tsx`、`frontend/src/components/assets/voice-asset-tag-input.tsx`、`frontend/src/__tests__/components/assets/voice-asset-favorite-dialog.test.tsx`；修改角色面板及实际使用的翻译资源。

- [ ] 先写用户行为测试：通过候选有收藏入口；未通过没有；提交名称与分类标签；重复收藏显示已有资产内容；错误后保留草稿。
- [ ] 运行 `cd frontend && pnpm test src/__tests__/components/assets/voice-asset-favorite-dialog.test.tsx`，确认失败。
- [ ] 查询层定义一致的 `VoiceAsset`、`VoiceAssetTags`、分页与收藏结果类型；实现前述 API hooks，编辑收藏成功使列表与标签索引缓存失效。
- [ ] 收藏对话框提供名称、描述与三个标签组；支持输入新增、候选选择、删除标签。仅从明确的结构化资料预填建议，显示为可编辑内容；不猜测身份和性格，不发起模型调用。
- [ ] 在现有候选操作区域接入对话框，区分“收藏”和重复收藏后的“编辑全局资产”。使用现有 UI 组件和翻译机制。
- [ ] 运行新对话框测试及 `src/__tests__/components/assets/character-voice-panel.test.tsx`，确认既有试听和采用行为未回归；仅提交本次改动的文件或片段。

## 任务 5：检索、试听与采用选择器

文件：创建 `frontend/src/components/assets/global-voice-picker.tsx`、`frontend/src/__tests__/components/assets/global-voice-picker.test.tsx`；扩展查询层和角色面板。

- [ ] 写用户流程测试：名称搜索、同类多选、跨类组合、空结果、分页、试听切换、采用成功刷新、409 冲突提示。
- [ ] 运行 `cd frontend && pnpm test src/__tests__/components/assets/global-voice-picker.test.tsx` 确认失败。
- [ ] 每个声音槽位提供“从全局配音库选择”；列表展示名称、标签、时长、来源摘要、试听和采用。检索变动重置分页；正在播放时切换资产先停止旧音频并释放 Blob URL。
- [ ] 采用操作展示目标角色和槽位，携带最新 `expected_reference` 与请求 ID。重复点击被阻止；重试复用请求 ID；重新选择资产产生新 ID。成功刷新角色、声音槽位、候选及 readiness 查询；冲突保留选择并提示刷新。
- [ ] 全局资产支持编辑名称/标签与删除，删除提示说明项目已采用副本不受影响。
- [ ] 运行收藏表单、选择器和角色面板测试，单独提交本次前端集成改动。

## 任务 6：综合验收与交付

- [ ] 运行 `uv run pytest tests/test_global_voice_asset_store.py tests/test_global_voice_asset_api.py tests/test_global_voice_asset_adoption.py -q`，预期全部通过。
- [ ] 运行 `cd frontend && pnpm test src/__tests__/components/assets/voice-asset-favorite-dialog.test.tsx src/__tests__/components/assets/global-voice-picker.test.tsx src/__tests__/components/assets/character-voice-panel.test.tsx`，预期全部通过。
- [ ] 运行 `cd frontend && pnpm exec tsc --noEmit -p tsconfig.app.json`；先确认仓库实际 TypeScript 配置名，若不同使用 package.json 中 build 的 `tsc -b`。区分本任务引入错误和既有工作区错误。
- [ ] 使用本地测试项目已有通过配音手动走完收藏→三类标签检索→试听→另一项目采用→刷新。截图记录入口与结果；不用付费生成验证。
- [ ] 核对网络响应与文件归属，确认独立保存及生产前检查通过。删除隔离测试来源后再试听目标副本，不操作用户真实项目删除。
- [ ] `git diff --check`，检查提交范围，报告测试、手动验收及未解决问题。不把未执行的浏览器或模型验证写成通过。

## 自检

规格映射：收藏与通过校验→任务 2；独立文件、去重、标签语义→任务 1；目标角色审核与副本→任务 3；名称和分类标签编辑→任务 4；检索试听复用→任务 5；失败、权限及端到端验证→任务 1–6。未包含付费生成、批量迁移、社区共享或其他画布升级。
