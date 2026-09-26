# 资产库多图选择与视频导演台实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 实现可选择角色基础图和变体的资产库，以及支持多段、自动 H3 路由、runtime 改写和失败恢复的独立画布视频导演台。

**架构：** 资产选择提供稳定图片身份，导演台草稿独立保存，通过现有任务系统驱动优化、供应商提交及结果接收。H3 适配器声明并验证工作流能力，复用项目 H3 写作规则与格式编译，隔离旧叙事组的首帧必填和 QC 门禁。前端使用摘要节点与展开面板，现有视频节点保持兼容。

**技术栈：** Python / FastAPI / Pydantic / pytest、现有 StructuredTextRuntime 与 RunningHub 执行器；React / TypeScript / Zustand / React Flow / Vitest。

**依据：** `docs/superpowers/specs/2026-09-26-canvas-video-director-design.md`，规格提交 `f9c8286`，用户已批准书面规格。此计划中的新增接口、模块和测试名是实施目标，不是对现有实现的描述。

---

## 执行边界与顺序

本次产物仅为计划，尚未实现或运行供应商生成。当前主工作目录有大量其他任务未提交修改，计划在该目录编写；实施阶段先按 using-git-worktrees 检测隔离状态、确定工作区并记录基线，不将其他任务修改带入本功能提交。尤其不要复制整个脏工作目录或撤销他人修改。

顺序：任务 1 核对契约 → 任务 2–3 完成可独立使用的资产多图选择 → 任务 4 定义导演台数据和能力 → 任务 5 优化与编译 → 任务 6 持久化任务和 API → 任务 7–8 节点与面板 → 任务 9 集成验收。工作流依赖不阻止资产库部分先落地。

每个任务采用：编写指定用例 → 运行确认在目标行为上失败 → 最小实现 → 同一用例通过 → 审查差异 → 仅提交本任务文件。禁止将现有基线失败说成新增回归，或通过删除断言掩盖失败。

Python 命令在仓库根运行；前端命令使用 `pnpm --dir frontend`。先检查依赖是否齐全，缺失时按项目 `uv sync --group dev` / `pnpm --dir frontend install` 安装。仅计划文档检查不需要安装依赖或运行应用测试。

## 文件与职责

| 路径 | 操作与职责 |
| --- | --- |
| `src/novelvideo/freezone/asset_images.py` | 新建：将现有角色及 identity 资产投影为基础/变体图片列表 |
| `src/novelvideo/freezone/video_node.py` | 修改：资产库 upsert、保存和读取保留图片身份元数据 |
| `src/novelvideo/api/routes/freezone.py` | 修改：资产同步调用投影；不继续塞入新的导演台 API |
| `frontend/src/features/canvas/ui/AssetLibraryModal.tsx` | 修改：角色分组入口和多图选择 |
| `frontend/src/features/canvas/ui/CharacterImagePicker.tsx` | 新建：角色基础/变体图片列表 |
| `frontend/src/api/ops.ts` | 修改：资产库响应类型；旧调用者保持兼容 |
| `src/novelvideo/freezone/video_director/{__init__,models,capabilities,optimizer,h3_adapter,store,service}.py` | 新建：请求契约、能力路由、优化、供应商适配、持久化与任务编排，各自独立 |
| `src/novelvideo/api/routes/freezone_video_director.py` | 新建：能力查询、创建生成、历史、状态、重试 API |
| `src/novelvideo/api/__init__.py` | 修改：注册独立路由 |
| `src/novelvideo/task_backend/runners/freezone_video_director.py` | 新建：现有任务系统 runner 入口 |
| `src/novelvideo/task_backend/runners/__init__.py` | 修改：按现有加载方式引入 runner |
| `src/novelvideo/media_capabilities/video/h3_prompt_profile.py` | 必要时提取共享写作规则，保持现有调用者行为兼容 |
| `frontend/src/features/canvas/director/{types,defaults,reducer}.ts` | 新建：导演台草稿、默认值和纯编辑操作 |
| `frontend/src/features/canvas/director/{VideoDirectorPanel,DirectorSegmentEditor,DirectorHistory}.tsx` | 新建：展开面板、片段编辑和结果历史 |
| `frontend/src/features/canvas/nodes/VideoDirectorNode.tsx` | 新建：摘要节点及视频预览 |
| `frontend/src/api/videoDirector.ts` | 新建：导演台 API 封装 |
| `frontend/src/features/canvas/director/useVideoDirectorTask.ts` | 新建：查询、恢复、重试及版本安全回填 |
| `frontend/src/features/canvas/domain/{canvasNodes,nodeRegistry,nodeDisplay}.ts` | 修改：节点类型、菜单与显示名称 |
| `frontend/src/features/canvas/Canvas.tsx` | 修改：沿实际 nodeTypes 注册入口接入节点 |
| `frontend/src/stores/canvasStore.ts` | 必要修改：默认尺寸、持久化和结果更新兼容 |
| `frontend/public/locales/{zh,en}/translation.json` | 修改：界面文案 |

新建测试放在 `tests/freezone/video_director/` 和 `frontend/src/__tests__/features/canvas/`；资产测试扩展 `tests/test_freezone_asset_library_backend.py`。新目录需添加项目要求的包初始化文件。实施前确认 React Flow 实际 nodeTypes 的定义位置，若由 `Canvas.tsx` 导入另一模块，则在该定义处添加注册，不复制第二份注册表。

## 任务 1：冻结工作流能力证据

**文件：** 阅读 `runninghub/MiniMax H3 导演台全能工作流-参考_api.json`、`src/novelvideo/media_capabilities/video/profiles/minimax_h3*.json`、`runtime.py`、`h3_reference_payload.py`；新建 `tests/fixtures/runninghub/canvas_director_contract.json` 和 `docs/audits/2026-09-26-canvas-director-workflows.md`。

- [ ] 记录执行分支、HEAD、现有改动；在选定隔离工作区运行资产、H3 payload 与帧数相关基线测试。

```bash
uv run pytest tests/test_freezone_asset_library_backend.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_timeline.py -q
```

- [ ] 通过现有配置解析普通 H3 和 H3 Ref 的实际工作流 ID；利用服务端已有 RunningHub 客户端的认证配置，按官方工作流读取接口获取定义。禁止打印凭据、把真实素材 URL 写入 fixture，或直接运行会付费生成的 smoke 脚本来代替读取。
- [ ] 对照节点类和链接核对：纯 r2v、i2v、fl2v、多段、Ref 混合控制、输出节点、细化节点尺寸覆盖、帧数和参考上限。保留采集时间、工作流 ID、脱敏定义摘要、接口证据；没有证据的组合标记 `verified: false`，不得默认启用。
- [ ] 契约 JSON 的每个模式使用以下字段；`supported` 仅在读取证据足够时为真，实际生成验收另列。

```json
{"mode":"ref_only","supported":true,"verified":false,"evidence_kind":"workflow_definition","required_inputs":["references","segments"],"forbidden_fallbacks":["drop_references","submit_raw_prompt"]}
```

- [ ] 在审计文档逐条记录限制与实际绑定。若在线定义不可读，记录阻塞原因并继续资产任务；不编造已确认的上下限。
- [ ] 验证脱敏 JSON 可解析，提交契约与审计文档。提交信息：`docs: capture canvas director workflow contracts`。

## 任务 2：资产同步保留基础图和变体身份

**文件：** 新建 `src/novelvideo/freezone/asset_images.py`；修改 `video_node.py`、`api/routes/freezone.py`；扩展 `tests/test_freezone_asset_library_backend.py`。

- [ ] 新增一个角色基础图和两个 identity 变体的测试夹具，重复同步两次；断言三张图片身份稳定、元数据不丢、没有重复。复用现有测试的 `tmp_path`、角色 store mock、合法项目内 PNG，不用文件名猜测角色身份。

```python
def test_character_images_keep_identity_metadata():
    from novelvideo.freezone.asset_images import merge_character_images
    images = [
        {"image_id": "c1:base", "character_id": "c1", "kind": "base", "url": "/base.png"},
        {"image_id": "c1:i1:portrait", "character_id": "c1", "kind": "variant", "variant_id": "i1", "url": "/v1.png"},
    ]
    assert merge_character_images(images, images) == images
```

- [ ] 运行 `uv run pytest tests/test_freezone_asset_library_backend.py -q`，确认新增函数缺失或元数据断言失败。
- [ ] 实现 `merge_character_images(existing, incoming)`：按稳定 `image_id` 合并，后值更新、保留首次顺序。从 `character.identities` 及现有 canonical path helpers 枚举 identity、costume、portrait 图片；跳过不存在的文件。基础图沿用 canonical portrait，现有 character ID 缺失时才使用项目范围内稳定名称标识。

```python
def merge_character_images(existing, incoming):
    by_id = {item["image_id"]: dict(item) for item in existing}
    for item in incoming:
        by_id[item["image_id"]] = dict(item)
    return list(by_id.values())
```

- [ ] `_upsert_library_item`、save/load 和 API 序列化全链路保留 `character_id/image_id/kind/variant_id/variant_label`。保留已有顶层角色条目，增加图片子集合以避免角色卡片一变多；旧 `url/image_urls` 仍可读。删除/失效资产不能以旧 URL 留在可选子集合中。
- [ ] 补充基础图缺失但变体存在、同名角色、旧库记录、重新同步更新 URL、其他媒体不受影响用例，运行同一测试命令通过。
- [ ] 仅暂存上述文件并提交：`feat: preserve character image variants in asset library`。

## 任务 3：资产库二级多选

**文件：** 修改 `AssetLibraryModal.tsx`、`frontend/src/api/ops.ts`；新建 `CharacterImagePicker.tsx`、`frontend/src/__tests__/features/canvas/asset-library-character-images.test.tsx`。

- [ ] 测试以真实 Modal 为入口，mock API 返回一个角色三张图片；执行点击角色、选择基础图和变体、返回、再次进入、确认。

```tsx
await user.click(screen.getByRole('button', { name: '查看步知遥的图片' }));
await user.click(screen.getByRole('checkbox', { name: '步知遥 基础图' }));
await user.click(screen.getByRole('checkbox', { name: '步知遥 夜行装' }));
await user.click(screen.getByRole('button', { name: '返回资产库' }));
expect(screen.getByText('已选 2')).toBeInTheDocument();
await user.click(screen.getByRole('button', { name: '确定' }));
expect(onConfirm.mock.calls[0][0].map((x: { imageId: string }) => x.imageId))
  .toEqual(['c1:base', 'c1:night']);
```

- [ ] 运行 `pnpm --dir frontend test src/__tests__/features/canvas/asset-library-character-images.test.tsx`，确认行为失败。
- [ ] `AssetLibrarySelection` 增加兼容旧调用者的可选 `assetId/imageId/characterId/variantId/variantLabel`；Modal 状态以图片 ID 维护选择，返回上级仅改变浏览位置。共享入口多选、首尾帧入口单选；标签与 checkbox 使用可访问名称。

```ts
const next = new Map(selectedImages);
if (next.has(image.imageId)) next.delete(image.imageId);
else if (next.size < maxSelectable) next.set(image.imageId, image);
setSelectedImages(next);
```

- [ ] 覆盖单选替换、跨角色累积、达到上限、上传图片兼容、无图空态、重复打开恢复已选图片；每张图只占一个名额。
- [ ] 同一命令通过，提交：`feat: select base and variant images in asset library`。

## 任务 4：导演台契约与能力路由

**文件：** 新建 `src/novelvideo/freezone/video_director/models.py`、`capabilities.py`、`tests/freezone/video_director/test_capabilities.py`。

- [ ] 定义共享类型：`DirectorImage`（image_id、asset_id、可选 character_id/variant_id、url、sha256）；`DirectorSegment`（id、prompt、duration_seconds、first_frame、last_frame）；`DirectorDraft`（schema_version=1、revision、model_id、aspect_ratio、resolution、references、segments）；`DirectorAttempt`（id、revision、snapshot、stage、optimized_segments、provider_task_id、result_url、error）。
- [ ] 使用 Pydantic 禁止非有限时长、空片段列表、重复片段 ID；模型/参数限制从能力服务解析。能力接口 `resolve_route(reference_count: int) -> str` 只处理路由，素材合法性由独立请求校验负责。

```python
@pytest.mark.parametrize("count,expected", [(0, "h3"), (1, "h3_ref"), (3, "h3_ref")])
def test_route_uses_subject_images_only(count, expected):
    from novelvideo.freezone.video_director.capabilities import resolve_route
    assert resolve_route(count) == expected
```

- [ ] 运行 `uv run pytest tests/freezone/video_director/test_capabilities.py -q` 确认失败后实现：

```python
def resolve_route(reference_count: int) -> str:
    if reference_count < 0:
        raise ValueError("reference_count must be nonnegative")
    return "h3_ref" if reference_count else "h3"
```

- [ ] 增加六种规格输入组合测试：首帧、首尾帧、纯 Ref、Ref 混合、无图、仅尾帧。用任务 1 的能力证据控制混合模式；纯 Ref 不得要求首帧；未知模型拒绝且不清空输入。时长使用现有 `frames_for_duration`，显示换算值；尺寸复用 `resolve_h3_size_setting`。
- [ ] 运行通过后提交：`feat: define canvas director inputs and model capabilities`。

## 任务 5：无 QC 的 H3 改写和多段编译

**文件：** 新建 `optimizer.py`、`h3_adapter.py`、`tests/freezone/video_director/test_optimizer.py`、`test_h3_adapter.py`；必要时从 `h3_prompt_profile.py` 提取可复用写作规则。

- [ ] 优化器接口确定为 `async optimize(runtime, draft, system_prompt) -> OptimizedDirector`；`OptimizedDirector` 和 `OptimizedSegment` 在 `models.py` 定义，后者含 `segment_id` 与编译所需的 H3 wire 结构。生成后输出片段 ID 必须与输入有序一致。
- [ ] 用 fake `StructuredTextRuntime` 记录 prompt、system_prompt、images；分别断言 H3 规则存在、两个输入片段保留、同角色两图共用角色身份。fake runtime 抛连接异常时不得产出原始 prompt fallback。

```python
async def test_optimizer_failure_never_returns_raw_prompt():
    from novelvideo.freezone.video_director.optimizer import optimize
    runtime = AsyncMock()
    runtime.run_structured.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        await optimize(runtime, draft, system_prompt="H3 writing rules")
    runtime.run_structured.assert_awaited_once()
```

`draft` 使用本文件 fixture 构造任务 4 的 `DirectorDraft`，包含一段有效首帧输入。测试导入 `AsyncMock` 自 `unittest.mock`。

- [ ] 运行 `uv run pytest tests/freezone/video_director/test_optimizer.py tests/freezone/video_director/test_h3_adapter.py -q`，确认失败。
- [ ] 通过已有 runtime 路由构造文本后端，调用已存在的真实接口，而不是传不受支持的 skill ID：

```python
result = await runtime.run_structured(
    prompt=serialized_input,
    system_prompt=system_prompt,
    output_type=OptimizedDirector,
    images=structured_images,
)
```

`serialized_input` 由草稿及合法帧数生成；`structured_images` 使用项目现有 `StructuredImage` 及图像加载方法；片段 ID、参考编号、身份字段作为数据，与系统指令分离。系统提示复用 H3 写作规则，排除旧“hard quality failure”驱动的审稿语义，不加载旧 15 节质量评分链作为新输入前置要求。

- [ ] `h3_adapter.py` 用 H3 wire/compiler 生成真实格式，再按已核实契约构建 timeline。纯 Ref 的 `first_frame=None` 不得经过旧构建器的首帧必填分支；不伪造首帧，也不拿第一张主体参考当首帧。为多段 prompt、顺序、Picture/Subject 映射、音轨、refine 尺寸编写 payload 断言。
- [ ] 在集成边界给 QC 调用安装抛错 spy，证明新路径不调用评分/审核/自动质量修复；格式无法解析仍失败。断言对白不被翻译或改写、普通 H3 锚点保持、同角色图片不增殖角色。
- [ ] 运行上述测试和 `tests/media_capabilities/video/test_h3_prompt_compiler.py`，提交：`feat: compile director prompts through runtime without QC gates`。

## 任务 6：可恢复生成任务与 API

**文件：** 新建 `store.py`、`service.py`、`api/routes/freezone_video_director.py`、`task_backend/runners/freezone_video_director.py`；修改路由/runner 加载入口；新建 `test_store.py`、`test_service.py`、`test_api.py`。

- [ ] 按现有项目任务 store 扩展持久化生成尝试；快照和生成状态分开，快照一旦开始不能变。唯一请求键由项目、canvas ID、node ID、客户端 request ID 组成；相同键返回同一尝试，防双击和重连重复创建。
- [ ] 使用 pytest fake runtime/provider/store 验证优化失败 `provider.submit` 调用次数为 0，已有 provider task ID 时只查询。再覆盖响应丢失、下载失败和进程重启恢复。

```python
async def test_resume_queries_existing_provider_task(service, provider, stored_attempt):
    stored_attempt.provider_task_id = "rh-123"
    await service.resume(stored_attempt.id)
    provider.submit.assert_not_awaited()
    provider.query.assert_awaited_once_with("rh-123")
```

这些 fixture 在 `tests/freezone/video_director/conftest.py` 定义：service 为 `DirectorService`，provider 为注入的 `AsyncMock`，stored_attempt 通过实际 store 插入，避免只改内存副本而未持久化。

- [ ] 运行 `uv run pytest tests/freezone/video_director/test_store.py tests/freezone/video_director/test_service.py tests/freezone/video_director/test_api.py -q` 确认失败。
- [ ] 编排固定阶段：`optimizing → submitting → queued → generating → completed`，失败附 `failed_stage`；另设 `submission_unknown` 禁止自动重发。持久化 optimized output 后再提交，拿到 provider ID 立即持久化。调用现有并发协调器，不能自行降低已配置并发。

```python
if attempt.provider_task_id:
    return await self.provider.query(attempt.provider_task_id)
if attempt.stage == "submission_unknown":
    raise RuntimeError("submission status must be resolved before retry")
```

- [ ] 注册路由前缀 `/projects/{project}/freezone/video-director`：`GET /capabilities`、`POST /attempts`、`GET /attempts?canvas_id=&node_id=`、`GET /attempts/{id}`、`POST /attempts/{id}/retry`。所有路由复用项目权限检查；创建返回现有任务 ID 与 attempt ID，业务 runner 使用项目的文本任务路由角色。
- [ ] 服务定义 `resume(attempt_id)` 与 `retry(attempt_id)`；优化失败重试优化，明确提交失败复用成功优化，已有任务继续查询，下载失败复用产物地址，执行失败创建关联原记录的新 attempt。前端不能传任意工作流 ID 或服务器路径。
- [ ] API 测试跨项目访问失败、未知 attempt 404、参数错误 422、重复 request ID 幂等、历史顺序稳定；上面的恢复场景均通过后提交：`feat: persist and recover canvas director generation tasks`。

## 任务 7：节点注册、草稿和视频连通

**文件：** 新建前端 `director/types.ts`、`defaults.ts`、`reducer.ts`、`nodes/VideoDirectorNode.tsx`；修改节点注册/显示/持久化入口；新建 `video-director-draft.test.ts` 并扩展 `node-registry.test.ts`。

- [ ] TypeScript 数据结构与任务 4 同构，使用 API 层统一 snake/camel 转换。新增节点类型 `videoDirectorNode`，数据包含 `draft`、当前 attempt ID、当前结果 URL、结果版本及节点显示数据。
- [ ] 新增 `createDirectorDraft()`，默认一个空片段、MiniMax、9:16、720p；空草稿能保存但不能生成。`copySegment(draft, segmentId, newId)` 要求新 ID 唯一，深拷贝片段，不共享后续可变对象。

```ts
const original = createDirectorDraft();
const next = copySegment(original, original.segments[0].id, 'copy-1');
expect(next.segments).toHaveLength(2);
expect(next.segments[1].id).toBe('copy-1');
expect(original.segments).toHaveLength(1);
```

- [ ] 运行 `pnpm --dir frontend test src/__tests__/features/canvas/video-director-draft.test.ts src/__tests__/features/canvas/node-registry.test.ts` 确认失败后实现纯编辑函数及节点注册。
- [ ] 对照现有 video 节点的序列化、菜单、默认尺寸和 `videoUrl` 下游识别方式，接入新节点结果；测试保存恢复、复制节点使用新身份、生成视频可供下游合成引用，不能只在菜单注册就视作完成。
- [ ] 添加中英文节点名称、预览、片段数与总时长。节点只负责打开面板、触发生成和展示结果，不复制大型 `VideoNode.tsx` 的全部内部逻辑。
- [ ] 上述测试通过后提交：`feat: register persistent video director canvas node`。

## 任务 8：展开面板与状态恢复

**文件：** 新建 `VideoDirectorPanel.tsx`、`DirectorSegmentEditor.tsx`、`DirectorHistory.tsx`、`useVideoDirectorTask.ts`、`frontend/src/api/videoDirector.ts`；新建 `video-director-panel.test.tsx`、`video-director-task.test.tsx`。

- [ ] 使用 MSW 模拟任务 6 的 API，覆盖模型、画幅、分辨率、共享参考和逐段编辑，以及关闭再打开面板恢复内容。

```tsx
await user.click(screen.getByRole('button', { name: '添加片段' }));
expect(screen.getAllByRole('textbox', { name: '片段提示词' })).toHaveLength(2);
await user.click(screen.getByRole('button', { name: '生成视频' }));
expect(await screen.findByText('优化中')).toBeInTheDocument();
expect(screen.queryByRole('button', { name: '批准提示词' })).not.toBeInTheDocument();
```

生成测试夹具预先填入有效首帧或共享参考及两段非空提示词，避免把输入校验错误误当优化状态测试。

- [ ] 运行 `pnpm --dir frontend test src/__tests__/features/canvas/video-director-panel.test.tsx src/__tests__/features/canvas/video-director-task.test.tsx` 确认失败。
- [ ] 面板顶部读取后端能力显示模型/参数；共享区域使用资产多选；片段首尾槽使用单选。拖动/上移下移排序只改变顺序。提示词优化是按钮触发后的自动阶段，没有审批步骤。
- [ ] 对纯 Ref、不支持混合、超过参考数量、无图和仅尾帧显示与能力一致的错误位置。时长使用后端确认值显示，不宣称未验证组合可用。
- [ ] hook 按 attempt ID 订阅/轮询现有任务；卸载时释放轮询，重新打开用保存的 ID 查询；旧任务回调按 ID 拒绝覆盖新任务。

```ts
if (activeAttemptId !== update.attemptId) return;
if (update.stage === 'completed') {
  applyResult({ attemptId: update.attemptId, videoUrl: update.resultUrl });
}
```

`applyResult` 是 hook 注入的节点更新回调，参数在 `types.ts` 定义；历史更新不受当前 attempt 判断影响，所有已完成结果仍存服务端。

- [ ] 测试旧任务晚到、刷新恢复、重复点击、未知受理状态、优化失败重试、下载重试；显示原文与优化文但不写回原始提示词。状态轮询不得自动创建新 attempt。
- [ ] 同一测试命令通过后提交：`feat: add expanded multi-segment director panel`。

## 任务 9：集成回归与供应商验收

**文件：** 新建 `tests/freezone/video_director/test_end_to_end_contract.py`、`frontend/src/__tests__/features/canvas/video-director-flow.test.tsx`；更新任务 1 的审计文档。

- [ ] 假供应商集成覆盖：资产选择 → 两片段草稿 → runtime → timeline → 异步结果 → 节点/历史。断言原 prompt 未直接提交、没有 QC、两个变体仍是一个角色、片段顺序/时长正确。
- [ ] 运行聚焦后端测试：

```bash
uv run pytest tests/freezone/video_director tests/test_freezone_asset_library_backend.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_timeline.py -q
```

- [ ] 运行新增前端测试、现有节点注册回归及类型检查：

```bash
pnpm --dir frontend test src/__tests__/features/canvas/asset-library-character-images.test.tsx src/__tests__/features/canvas/video-director-draft.test.ts src/__tests__/features/canvas/video-director-panel.test.tsx src/__tests__/features/canvas/video-director-task.test.tsx src/__tests__/features/canvas/video-director-flow.test.tsx src/__tests__/features/canvas/node-registry.test.ts
pnpm --dir frontend exec tsc -b
```

- [ ] 浏览器走一次真实 UI，检查窄窗口、键盘操作、图片预览、面板关闭恢复和生成状态；截图记录资产二级选择和多段面板。进行项目构建，若出现既有无关问题记录基线差异，不擅自扩大改造范围。
- [ ] 在得到实际生成的执行授权并明确项目素材后，通过新支持入口分别验收普通 H3 首尾帧、纯 Ref 多段、Ref 混合控制；记录 provider ID、工作流版本、输入摘要、输出像素、时长和播放证据。若项目生产入口规则适用，先读取 `skills/nuomi-production/SKILL.md`，不得绕过支持入口直调付费脚本。
- [ ] 混合模式不受支持时保留明确不可用状态，审计写明实际原因；纯 Ref 或普通 H3 核心用例失败则不能宣布需求完成。视频验收限必要播放和文件可读性，不加入 ASR、审美评分或额外付费 QC。
- [ ] 检查变更仅属于本功能，提交测试与证据：`test: verify director generation and recovery contracts`。

## 规格覆盖自检

| 规格要求 | 对应任务 |
| --- | --- |
| 基础/变体图片、跨角色多选与稳定身份 | 2、3 |
| 独立节点、多段编辑、展开面板与草稿恢复 | 7、8 |
| 视频模型扩展、H3 自动路由、纯 Ref | 1、4、5 |
| 参数、合法帧数、分辨率和细化尺寸 | 1、4、5、9 |
| 项目 H3 规则、runtime 改写、不做 QC | 5、6、9 |
| 优化失败暂停、提交幂等、下载恢复 | 6、8、9 |
| 不可变快照、历史与旧结果不覆盖新任务 | 6、7、8 |
| 工作流能力证据和实际生成验收 | 1、9 |

完成定义：聚焦回归通过、UI 可操作、供应商核心用例有证据；限制如实记录。任何未执行的实测保持未勾选，不能用计划文档或 mock 测试替代。此计划不包含修改主线 QC 政策或新增其他视频模型实现。
