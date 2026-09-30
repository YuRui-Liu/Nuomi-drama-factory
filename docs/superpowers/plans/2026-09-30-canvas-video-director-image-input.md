# 画布视频导演图片输入改版实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让视频导演节点直接接收资产库、本地和画布连线图片，默认引导 H3 Ref，且界面预检、生成快照与实际提交图片一致。

**架构：** 节点保存 Ref／首尾帧两组草稿和当前启用方式；纯函数把手动图片与实时连线输出投影成现有 `DirectorDraft`。卡片和展开面板复用图片输入动作与槽位展示，提交入口只使用投影后的冻结快照；现有后端优化和 RunningHub 适配器保持不变。

**技术栈：** React 19、TypeScript、Zustand、@xyflow/react、Vitest/Testing Library、现有 FastAPI 导演台契约。

**规格：** `docs/superpowers/specs/2026-09-30-canvas-video-director-image-input-design.md`。工作目录为 `.worktrees/canvas-video-director`；此前台依赖为指向主仓库的符号链接，使用 `frontend/node_modules/.bin/*` 运行验证。

---

## 文件结构与职责

| 文件 | 本计划中的职责 |
| --- | --- |
| `frontend/src/features/canvas/domain/canvasNodes.ts` | 为节点增加可选的当前输入方式与卡片当前片段 ID；旧画布仍可加载 |
| `frontend/src/features/canvas/domain/videoDirectorDraft.ts` | 默认 Ref、切换方式与克隆时保留两组输入 |
| `frontend/src/features/canvas/domain/videoDirectorBindings.ts`（新） | 定义连线槽位数据、校验及上游图片解析 |
| `frontend/src/features/canvas/domain/videoDirectorInputs.ts`（新） | 投影当前输入方式并计算内容指纹，供预检、提交和结果版本提示共用 |
| `frontend/src/features/canvas/domain/nodeRegistry.ts` | 视频导演只接收图片节点上游 |
| `frontend/src/stores/canvasStore.ts` | 拒绝无槽位的导演台入边，保存带槽位数据的连线并支持撤销/持久化 |
| `frontend/src/features/canvas/director/DirectorConnectionTargetDialog.tsx`（新） | 连线时让用户选择全局参考或指定片段首/尾帧 |
| `frontend/src/features/canvas/Canvas.tsx` | 收口手动连线、加号建点和其他建边路径到槽位选择 |
| `frontend/src/features/canvas/director/DirectorImageSlot.tsx`（新） | 卡片与面板共用图片缩略图、选择、上传、拖入、移除及错误外观 |
| `frontend/src/features/canvas/director/useDirectorImageActions.tsx`（新） | 复用资产库选择、直传和槽位更新，上传失败不改草稿 |
| `frontend/src/features/canvas/nodes/VideoDirectorNode.tsx` | A 布局、Ref 优先空态、当前片段快速编辑和结果预览入口 |
| `frontend/src/features/canvas/director/VideoDirectorPanel.tsx`、`DirectorSegmentEditor.tsx` | 与卡片共用输入动作、显示活动方式与未启用图片 |
| `frontend/src/features/canvas/director/useVideoDirectorTask.ts` | 提交时统一解析、预检并冻结有效草稿 |
| `frontend/public/locales/zh/translation.json`、`en/translation.json` | 新输入方式、槽位、来源和错误的双语文案 |
| `frontend/src/__tests__/features/canvas/video-director-*.test.ts(x)` | 域逻辑、连线、上传、组件和请求回归 |

## 任务 1：输入方式与有效草稿

**文件：** 修改 `frontend/src/features/canvas/domain/canvasNodes.ts:195-282`、`videoDirectorDraft.ts`；创建 `videoDirectorInputs.ts`；测试 `frontend/src/__tests__/features/canvas/video-director-draft.test.ts`、新建 `video-director-inputs.test.ts`。

- [ ] **步骤 1：写失败测试。** 先固定三条规则：新节点默认 Ref；切到首帧后保留参考图；只把当前方式的图片投影到请求。测试代码加入 `video-director-inputs.test.ts`：

```ts
const ref = { imageId: 'ref', url: '/ref.png' };
const first = { imageId: 'first', url: '/first.png' };
const draft = updateSegment(updateDraft(createDirectorDraft('s1'), { references: [ref] }), 's1', { firstFrame: first });
expect(resolveDirectorInputMode({ draft, activeInputMode: 'ref' })).toBe('ref');
expect(projectDirectorDraft(draft, 'ref').segments[0].firstFrame).toBeNull();
expect(projectDirectorDraft(draft, 'frames').references).toEqual([]);
expect(projectDirectorDraft(draft, 'frames').segments[0].firstFrame).toEqual(first);
expect(draft.references).toEqual([ref]);
```

- [ ] **步骤 2：验证失败。** 在 `frontend` 运行 `./node_modules/.bin/vitest run src/__tests__/features/canvas/video-director-inputs.test.ts`；预期缺少 `resolveDirectorInputMode` 和 `projectDirectorDraft` 导出。
- [ ] **步骤 3：实现最小域逻辑。** `VideoDirectorNodeData` 增加 `activeInputMode?: 'ref' | 'frames'` 与 `visibleSegmentId?: string | null`。旧节点缺字段时，先由已有参考图推断 Ref，再由已有首帧推断 frames，其余默认 Ref；创建新节点明确设为 Ref。`videoDirectorInputs.ts` 的核心实现：

```ts
export type DirectorInputMode = 'ref' | 'frames';
export function resolveDirectorInputMode(data: Pick<VideoDirectorNodeData, 'draft' | 'activeInputMode'>): DirectorInputMode {
  if (data.activeInputMode) return data.activeInputMode;
  if (data.draft.references.length) return 'ref';
  return data.draft.segments.some((s) => s.firstFrame) ? 'frames' : 'ref';
}
export function projectDirectorDraft(draft: DirectorDraft, mode: DirectorInputMode): DirectorDraft {
  return mode === 'ref'
    ? { ...draft, segments: draft.segments.map((s) => ({ ...s, firstFrame: null, lastFrame: null })) }
    : { ...draft, references: [] };
}
export function directorSubmissionFingerprint(draft: DirectorDraft): string {
  return JSON.stringify({ modelId: draft.modelId, aspectRatio: draft.aspectRatio,
    resolution: draft.resolution, references: draft.references,
    segments: draft.segments.map(({ id, prompt, durationSeconds, firstFrame, lastFrame }) =>
      ({ id, prompt, durationSeconds, firstFrame, lastFrame })) });
}
export function setDirectorInputMode(data: VideoDirectorNodeData, mode: DirectorInputMode): VideoDirectorNodeData {
  if (resolveDirectorInputMode(data) === mode) return data;
  return { ...data, activeInputMode: mode,
    draft: { ...data.draft, revision: data.draft.revision + 1 } };
}
```

模式切换只更新 `activeInputMode` 并递增 `draft.revision`；移除最后一张参考图且存在首帧时切到 frames。`cloneVideoDirectorData` 复制新增字段及两组输入，仍清除活动任务和结果。
- [ ] **步骤 4：验证通过。** 运行上述 Vitest，加跑 `video-director-draft.test.ts`；预期全部通过。使用 `git diff --check`。
- [ ] **步骤 5：只提交本任务文件。** `git add` 所列文件并提交 `feat(canvas): preserve director input modes`。

## 任务 2：图片连线槽位与实时解析

**文件：** 创建 `frontend/src/features/canvas/domain/videoDirectorBindings.ts`；修改 `nodeRegistry.ts:680-790`、`frontend/src/stores/canvasStore.ts:1379-1418,1977-2028`；测试新建 `video-director-bindings.test.ts`，扩展 `video-director-draft.test.ts`。

- [ ] **步骤 1：写失败测试。** 用两张图片节点分别连接参考图与片段首帧，验证来源更新、失效和断线；断线后原手动图片仍在草稿中。关键断言：

```ts
const edge = { id: 'e1', source: 'image-1', target: 'director', data: {
  edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's1' },
} };
expect(readDirectorBinding(edge)?.slot).toEqual({ kind: 'firstFrame', segmentId: 's1' });
expect(resolveDirectorBindings(draft, [imageNode('/new.png')], [edge], 'frames').draft.segments[0].firstFrame?.url).toBe('/new.png');
expect(resolveDirectorBindings(draft, [], [edge], 'frames').errors['segments[0].first_frame']).toMatch(/上游图片/);
expect(resolveDirectorBindings(draft, [], [edge], 'ref').errors).toEqual({});
expect(resolveDirectorBindings(draft, [], [], 'frames').draft.segments[0].firstFrame).toEqual(draft.segments[0].firstFrame);
```

测试辅助 `imageNode(url)` 返回 `CanvasNode`，类型为 `CANVAS_NODE_TYPES.upload`，ID 为 `image-1`，`data.imageUrl` 为传入 URL。
- [ ] **步骤 2：验证失败。** 运行 `./node_modules/.bin/vitest run src/__tests__/features/canvas/video-director-bindings.test.ts`；预期缺少绑定解析函数。
- [ ] **步骤 3：实现绑定契约。** `readDirectorBinding` 只校验边数据结构；存储层额外核对目标片段和源节点类型。`resolveDirectorBindings(draft,nodes,edges,mode)` 用现有 `extractCanvasAssets(nodes).image` 按 `nodeId` 解析当前图片，复制草稿后覆盖绑定槽位；仅为当前 `mode` 中失效的绑定返回字段错误。参考图按 `imageId` 去重，手动参考图在前，连线参考图按边顺序追加；帧槽位最多一条绑定，断线后不修改原手动草稿。`nodeRegistry` 把视频导演的上游白名单限定为 `upload/imageEdit/imageGen/exportImage`。`canvasStore.onConnect` 拒绝没有槽位数据的普通入边；`addEdgeWithData` 在目标为导演台时只接收有效绑定、存在的片段与空闲的帧槽位。绑定类型和同槽判断：

```ts
export type DirectorBinding = { edgeKind: 'videoDirectorImage'; slot:
  { kind: 'reference' } | { kind: 'firstFrame' | 'lastFrame'; segmentId: string } };
export function readDirectorBinding(edge: Pick<CanvasEdge, 'data'>): DirectorBinding | null {
  const value = edge.data as Partial<DirectorBinding> | undefined;
  if (value?.edgeKind !== 'videoDirectorImage') return null;
  const slot = value.slot;
  if (slot?.kind === 'reference') return { edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } };
  if ((slot?.kind === 'firstFrame' || slot?.kind === 'lastFrame') &&
      typeof slot.segmentId === 'string' && slot.segmentId) return {
        edgeKind: 'videoDirectorImage', slot: { kind: slot.kind, segmentId: slot.segmentId },
      };
  return null;
}
export function sameDirectorFrameSlot(a: CanvasEdge, b: CanvasEdge): boolean {
  const left = readDirectorBinding(a)?.slot;
  const right = readDirectorBinding(b)?.slot;
  if (!left || !right || left.kind === 'reference' || right.kind === 'reference') return false;
  return a.target === b.target && left.kind === right.kind && left.segmentId === right.segmentId;
}
```

```ts
if (targetNode?.type === CANVAS_NODE_TYPES.videoDirector && !readDirectorBinding(newEdge)) return null;
if (targetNode?.type === CANVAS_NODE_TYPES.videoDirector &&
    state.edges.some((edge) => sameDirectorFrameSlot(edge, newEdge))) return null;
```

- [ ] **步骤 4：验证通过。** 运行绑定测试、`video-director-draft.test.ts` 与现有连接规则测试；预期通过。检查旧画布的非导演台连接不受影响。
- [ ] **步骤 5：提交。** 仅暂存本任务域文件和测试，提交 `feat(canvas): bind director inputs to image nodes`。

## 任务 3：画布连线目标选择

**文件：** 创建 `frontend/src/features/canvas/director/DirectorConnectionTargetDialog.tsx`；修改 `frontend/src/features/canvas/Canvas.tsx:1722-1765` 及相关渲染区；测试新建 `video-director-connection-dialog.test.tsx`。

- [ ] **步骤 1：写失败测试。** 从图片节点连向视频导演后，断言先出现「主体参考图／第 1 段首帧／第 1 段尾帧」选择，而不是立即产生无槽位边；选择首帧后边数据包含稳定片段 ID，取消后无边。

```tsx
fireEvent.click(screen.getByRole('button', { name: '第 1 段首帧' }));
expect(useCanvasStore.getState().edges[0].data).toEqual({
  edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's1' },
});
```

- [ ] **步骤 2：验证失败。** 运行新测试，预期找不到目标选择按钮。
- [ ] **步骤 3：接入所有画布建边入口。** 在 `connectGraphNodes` 中检测目标为视频导演，缓存 `Connection` 并展示 `DirectorConnectionTargetDialog`；其他类型仍走原 skill/普通连接路径。确认时用 `useCanvasStore.getState().addEdgeWithData` 保存槽位数据，帧槽位选择切到 frames、参考槽位选择切到 ref，保留另一组草稿，再调用 `scheduleCanvasPersist(0)`。取消只清除待连接状态。这样手动拖线和现有加号建点调用同一入口；不能只改 `handleConnect`。

```ts
if (nodes.find((node) => node.id === connection.target)?.type === CANVAS_NODE_TYPES.videoDirector) {
  setPendingDirectorConnection(connection);
  return;
}
```

目标对话框从目标节点 `draft.segments` 生成片段选项；当参考数量达到 `effectiveReferenceLimit` 或帧槽已被另一边占用时禁用相应项并说明原因。实际 `addEdgeWithData` 返回 `null` 时显示失败并保留对话框，不假报连接成功。

```tsx
const directorTarget = nodes.find((node) => node.id === pendingDirectorConnection?.target);
<DirectorConnectionTargetDialog
  open={pendingDirectorConnection !== null}
  segments={directorTarget?.data.draft.segments ?? []}
  onCancel={() => setPendingDirectorConnection(null)}
  onSelect={(slot) => {
    if (!pendingDirectorConnection) return;
    const id = useCanvasStore.getState().addEdgeWithData(
      pendingDirectorConnection.source, pendingDirectorConnection.target,
      { edgeKind: 'videoDirectorImage', slot }, { id: crypto.randomUUID() });
    if (!id) return setConnectionError('无法连接到该图片槽位');
    setPendingDirectorConnection(null);
    scheduleCanvasPersist(0);
  }}
/>
```
- [ ] **步骤 4：验证通过。** 运行新测试与现有 `skill-connection-edges.test.ts`，预期通过；手动检查图片节点右侧加号到导演台也弹出目标选择。
- [ ] **步骤 5：提交。** 提交本任务组件、Canvas 改动和测试，信息为 `feat(canvas): choose director image connection target`。

## 任务 4：共用图片槽位、资产库选择和直传

**文件：** 创建 `frontend/src/features/canvas/director/DirectorImageSlot.tsx`、`useDirectorImageActions.tsx`；修改 `VideoDirectorPanel.tsx:1-150`、`DirectorSegmentEditor.tsx:1-75`；测试新建 `video-director-image-actions.test.tsx`。

- [ ] **步骤 1：写失败测试。** 模拟 `uploadFreezoneImage` 返回 `/static/projects/demo/frame.png`，向首帧槽拖入 `image/png` 文件；上传完成前草稿不变，成功后保存持久 URL，失败时保留旧图并在首帧槽显示错误。资产库选中的角色变体需保留 `imageId/characterId/variantId`。

```tsx
fireEvent.drop(screen.getByRole('button', { name: '第 1 段首帧' }), {
  dataTransfer: { files: [new File(['png'], 'frame.png', { type: 'image/png' })] },
});
await waitFor(() => expect(nodeData().draft.segments[0].firstFrame?.url)
  .toBe('/static/projects/demo/frame.png'));
```

- [ ] **步骤 2：验证失败。** 运行新测试，预期首帧槽还不能直接处理文件拖入。
- [ ] **步骤 3：实现共用动作。** `DirectorImageSlot` 是展示组件，收到 `label/image/error/onPick/onUpload/onRemove`，负责 `dragover.preventDefault()`、drop 的图片类型检查和缩略图；不在组件内调用 API。`useDirectorImageActions(nodeId)` 管理当前槽位、资产库弹窗、上传中及槽位错误；`pick` 对全局参考图使用多选，对单帧使用单选；`upload` 调用：

```ts
const uploaded = await uploadFreezoneImage(project, file, file.name);
const image: DirectorImage = { imageId: uploaded.url, url: uploaded.url };
commitDirectorSlot(nodeId, target, image);
```

`commitDirectorSlot` 只改指定槽位并递增草稿版本，参考图按 `imageId` 去重且遵循能力上限；成功后更新活动方式。上传失败只更新本地错误，不修改节点草稿。把现有面板的 `AssetLibraryModal` 选择转换提取给动作层，卡片与面板都调用同一套动作。使用项目已有图片上传 API，不新增独立上传端点。
- [ ] **步骤 4：验证通过。** 运行新测试、`video-director-panel.test.tsx` 与 `asset-library-character-images.test.tsx`，预期通过。
- [ ] **步骤 5：提交。** 提交输入组件、动作层、面板及测试，信息为 `feat(canvas): allow direct director image intake`。

## 任务 5：Ref 优先卡片和多片段快速编辑

**文件：** 修改 `frontend/src/features/canvas/nodes/VideoDirectorNode.tsx:1-78`、`frontend/src/features/canvas/director/VideoDirectorPanel.tsx`、`frontend/src/features/canvas/domain/nodeRegistry.ts:501-520`、`frontend/public/locales/zh/translation.json` 与 `en/translation.json`；测试扩展 `video-director-flow.test.tsx`、`video-director-i18n.test.tsx`。

- [ ] **步骤 1：写失败测试。** 新建节点应显示主体参考图主入口，首帧与尾帧入口、当前路线和第一段提示词；点击首帧后无参考图则切普通 H3；切回 Ref 时原首帧可恢复；多段切换只改变卡片显示片段。

```tsx
expect(screen.getByRole('button', { name: /主体参考图/ })).toBeVisible();
expect(screen.getByText(/Ref 引导，待输入/)).toBeVisible();
fireEvent.change(screen.getByRole('textbox', { name: '第 1 段提示词' }), {
  target: { value: '缓慢推近人物' },
});
expect(nodeData().draft.segments[0].prompt).toBe('缓慢推近人物');
```

- [ ] **步骤 2：验证失败。** 运行上述两项 Vitest，预期当前卡片没有图片槽和提示词输入。
- [ ] **步骤 3：实现 A 布局。** 节点顶部显示 `MiniMax H3 Ref`／`MiniMax H3`，无有效输入时显示「Ref 引导，待输入」；以 `visibleSegmentId` 找当前片段，缺失时回落首段。卡片直接渲染共用 `DirectorImageSlot` 和提示词输入，交互区加 `nodrag` 以免拖动画布。卡片空白接图按当前方式处理：Ref 增加参考图；frames 在首帧为空时填首帧，否则打开目标选择。已有视频保留可播放结果和明显的「返回输入」入口；把当前有效草稿与活动 attempt 的快照分别传入 `directorSubmissionFingerprint` 比较，图片连线变化即使未改变草稿 revision，也显示「输入已修改」。面板继续提供完整编排，并标记未启用图片「已保留，当前不参与生成」。所有新文案写入中英文 locale，不用硬编码中文。

```tsx
const segment = data.draft.segments.find((s) => s.id === data.visibleSegmentId) ?? data.draft.segments[0];
<div className="nodrag" onClick={(event) => event.stopPropagation()}>
  <DirectorImageSlot label={t('node.videoDirector.editor.references')} images={resolved.references}
    error={errors.references} onPick={() => actions.pick({ kind: 'references' })}
    onUpload={(file) => actions.upload({ kind: 'references' }, file)} onRemove={actions.removeReference} />
  <DirectorImageSlot label={t('node.videoDirector.editor.firstFrame')}
    images={segment.firstFrame ? [segment.firstFrame] : []}
    error={errors.firstFrame} onPick={() => actions.pick({ kind: 'frame', segmentId: segment.id, field: 'firstFrame' })}
    onUpload={(file) => actions.upload({ kind: 'frame', segmentId: segment.id, field: 'firstFrame' }, file)}
    onRemove={() => actions.removeFrame(segment.id, 'firstFrame')} />
  <textarea aria-label={t('node.videoDirector.editor.prompt')} value={segment.prompt}
    onChange={(event) => actions.patchSegment(segment.id, { prompt: event.target.value })} />
</div>
```
- [ ] **步骤 4：验证通过。** 运行两项组件测试、`video-director-panel.test.tsx` 和 `./node_modules/.bin/tsc -b`；预期通过。
- [ ] **步骤 5：提交。** 提交卡片、面板、文案及测试，信息为 `feat(canvas): expose ref-first director inputs on node`。

## 任务 6：提交快照、错误和整体验收

**文件：** 修改 `frontend/src/features/canvas/director/useVideoDirectorTask.ts:145-175`、`directorValidation.ts`、`VideoDirectorNode.tsx`、`VideoDirectorPanel.tsx`；测试扩展 `video-director-task.test.tsx`、`video-director-flow.test.tsx`、`video-director-contract.test.ts`。

- [ ] **步骤 1：写失败测试。** 草稿同时保留参考图和首帧，当前方式为 Ref 时请求只含参考图；切 frames 后请求只含首帧。上游图片从 `/old.png` 更新为 `/new.png` 后，本次提交快照固定 `/new.png`，提交后再次变动不修改快照；活动槽位来源失效时不创建 attempt。

```ts
expect(postedDraft.references.map((image: { image_id: string }) => image.image_id)).toEqual(['ref']);
expect(postedDraft.segments[0].first_frame).toBeNull();
expect(nodeData().pendingSubmission?.frozenDraftSnapshot.references[0].url).toBe('/new.png');
```

- [ ] **步骤 2：验证失败。** 运行任务和契约测试；预期旧 `generate(draft)` 仍直接冻结未经模式投影的原草稿，导致断言失败。
- [ ] **步骤 3：统一提交入口。** 将 `useVideoDirectorTask.generate` 改为无参方法，点击时从 `useCanvasStore.getState()` 读取最新节点、边、上游节点，通过 `resolveDirectorBindings` 和 `projectDirectorDraft` 生成唯一有效草稿，先返回槽位错误，再运行现有 `validateDirectorDraft`，最后 `structuredClone` 写入提交 journal。卡片和面板只调用 `task.generate()`，不传局部旧草稿。模式非活动图片不参与校验；活动连线失效或超限则不创建 attempt。保留现有请求 ID、未知提交状态恢复和旧结果不得覆盖新结果的逻辑。

```ts
const state = useCanvasStore.getState();
const current = currentData(nodeId);
if (!current || !capabilities) return;
const mode = resolveDirectorInputMode(current);
const resolved = resolveDirectorBindings(current.draft, state.nodes, state.edges, mode);
const effective = projectDirectorDraft(resolved.draft, mode);
const errors = { ...resolved.errors, ...validateDirectorDraft(effective, capabilities) };
if (Object.keys(errors).length) { setFieldErrors(errors); return; }
const frozenDraftSnapshot = structuredClone(effective);
```

服务端契约保持不变，继续拒绝 Ref 与首尾帧同时有效。UI 不添加 QC、原文回退或优化二次审批。
- [ ] **步骤 4：验证聚焦套件。** 在 `frontend` 运行 `./node_modules/.bin/vitest run src/__tests__/features/canvas/video-director-*.test.ts src/__tests__/features/canvas/video-director-*.test.tsx src/__tests__/features/canvas/asset-library-character-images.test.tsx`，然后 `./node_modules/.bin/tsc -b` 和 `./node_modules/.bin/vite build`；预期通过。若主仓库已有测试失败，记录并区分是否与本分支相关。
- [ ] **步骤 4a：验证服务端契约未回退。** 在 worktree 根运行 `PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/freezone/video_director -q`；预期画布 H3/H3 Ref 现有测试通过。
- [ ] **步骤 5：做浏览器验收。** 使用本地画布逐一验证 Ref 空态、三种图片入口、连线目标选择、上游图片变化、模式切换、生成前槽位提示、刷新恢复和结果预览；测试用节点完成后撤销，不留下用户数据。不要点击真实付费生成，除非该轮目标明确要求出片验收。检查 `git diff --check`。
- [ ] **步骤 6：提交。** 只暂存本任务文件并提交 `feat(canvas): freeze resolved director image inputs`。

## 最终核对

- [ ] 将规格第 1–8 节逐条映射到上述任务：图片入口与 Ref 优先由任务 4–5，模式保留和路线由任务 1、6，连线实时绑定由任务 2–3、6，错误和快照由任务 4、6 覆盖。
- [ ] 复核旧画布数据：缺少 `activeInputMode` 的既有导演节点仍按已有参考图或首帧正确推断，复制节点后不会复用旧 attempt 或旧结果。
- [ ] 复核 `video-director` 全套 Vitest、TypeScript 与 Vite 构建的真实输出，再报告完成情况；不以原型或单元测试宣称 RunningHub 实际出片成功。
