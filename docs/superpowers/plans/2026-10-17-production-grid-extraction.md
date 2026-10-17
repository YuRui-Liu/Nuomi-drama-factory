# 导演到宫格出图与拆帧实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 或 executing-plans，勾选步骤并保留回归证据。

**目标：** 从组分镜连接真实出图节点，支持默认裁切与可选模型高清提取。

**架构：** 显式来源绑定 + 出图节点宫格模式 + 拆帧协调器。纯裁切复用现有 ImageSplitGateway，高清处理复用现有生成提交及任务查询。

**技术栈：** React、TypeScript、React Flow、Vitest，现有图片命令与生成 API。

## 文件职责

- 修改 `frontend/src/features/canvas/nodes/DirectorWorkspaceNode.tsx`、`NarrativeGroupStoryboardNode.tsx`：审阅与连接入口，移除页面内直接制作操作。
- 创建 `frontend/src/features/canvas/production/workspace/DirectorWorkspace.tsx`：组导航、图板/表/预演与按需详情。
- 修改 `frontend/src/features/canvas/nodes/ImageGenNode.tsx`，创建 `frontend/src/features/canvas/production/grid/GridExtractionPanel.tsx`、`gridBinding.ts`、`useGridExtraction.ts`：出图宫格与拆帧，不继续把业务塞进巨型节点文件。
- 集成者修改 `frontend/src/features/canvas/domain/canvasNodes.ts` 与来源结果所需的存储序列化，确保重载不丢字段。
- 复用 `frontend/src/features/canvas/application/toolProcessor.ts`、`infrastructure/webImageSplitGateway.ts`、`ui/tool-editors/SplitStoryboardToolEditor.tsx` 和 `frontend/src/commands/image.ts`。
- 新增测试 `frontend/src/__tests__/features/canvas/grid-binding.test.ts`、`grid-extraction.test.tsx`、`director-grid-workspace.test.tsx`。

## 任务 1：来源绑定和节点复用

- [ ] 为纯函数写失败测试：同项目/集/组/导演版返回同键，不同版本不同键，镜头格序稳定且空格不绑定镜头。

```ts
export type GroupGridBinding = {
  project: string; episode: number; groupId: string;
  directorRevisionId: string; shotIds: string[];
};
export const gridBindingKey = (b: GroupGridBinding) =>
  JSON.stringify([b.project, b.episode, b.groupId, b.directorRevisionId]);
```

- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/grid-binding.test.ts` 确认失败，再实现类型、键和映射校验；禁止重复 shot ID 或容量少于镜头数。
- [ ] 打开组分镜或出图节点时以绑定键查找已有节点，仅无匹配时通过 store 建立节点和上下文连接；不提交生成请求。
- [ ] 写组件断言：连续两次打开只有一个对应节点；更换导演版本不会覆盖旧节点；失效异步响应不回写当前画布。
- [ ] 运行新增测试与 `director-workspace-group-storyboard.test.tsx`、`director-preview-revision.test.tsx` 通过后提交 `feat: bind director groups to grid image nodes`。

## 任务 2：导演工作区与宫格生成

- [ ] 写失败用例：导演/组分镜内没有开始制作按钮；仍可审阅编辑采用；点击出图入口只接线。原有 `DirectorProductionPanel` 不从其他不相关业务全局删除。
- [ ] 执行 `pnpm exec vitest run src/__tests__/features/canvas/director-grid-workspace.test.tsx`，确认失败。
- [ ] 迁入共享壳：场景/组导航、选中镜头详情、调度关联、来源审阅和版本切换。保留图板、镜头表、已有素材预演功能及固定版本过滤。
- [ ] 出图节点新增明确模式字段，不复用 image gallery 标记。输入镜头有序描述与引用素材，按模型能力设置宫格布局及参数；多结果画册仍保持原语义。
- [ ] 生成提交复用 ImageGenNode 当前 `submitFreezoneGen` 路径和任务生命周期；若属于集生产，先读 `skills/nuomi-production/SKILL.md`，通过受支持生产服务保持叙事组、并发与失败恢复语义。不得以逐镜头 provider 循环替代。
- [ ] 结果携带来源绑定及结果版本，导演图板按准确绑定回显；上游变更仅标记过期，不自动重生成。
- [ ] 用 mock 任务测试生成成功/失败/重试与结果过期；运行任务 1 测试和 `director-animatic.test.tsx` 后提交 `feat: move grid generation to image nodes`。

## 任务 3：直接裁切与单帧节点

- [ ] 写失败测试：默认 `hd=false`，调用 ImageSplitGateway，不调用生成提交；只输出选择格；输出保存来源键/结果版本/镜头/格位置/裁切范围。

```ts
export type GridExtractionMode = 'crop' | 'generative-hd';
export type FrameOrigin = {
  gridNodeId: string; gridResultId: string; binding: GroupGridBinding;
  cellIndex: number; shotId: string;
  crop: { x: number; y: number; width: number; height: number };
};
```

- [ ] 执行 `pnpm exec vitest run src/__tests__/features/canvas/grid-extraction.test.tsx` 确认失败。
- [ ] 拆帧面板复用格线与分隔线计算，明确像素坐标；裁切前校验源图尺寸和有效矩形。保留原图、不生成替代图。
- [ ] 通过现有 splitGateway 与持久化路径保存单帧，再创建可连接的独立图片结果节点，重复同来源同裁切定位复用，新源结果保持新版本。保持 row-major 镜头映射，空白格不可错误成为镜头。
- [ ] 测试不均分尺寸、分隔线、部分选择、重载来源元数据和连续重复点击；通过后提交 `feat: extract grid cells into linked image nodes`。

## 任务 4：模型高清剪裁

- [ ] 写失败测试：开启后无有效模型不可执行；按已选帧提交显式生成任务，成功帧不在失败重试中重复提交；原始裁切始终保留。
- [ ] 通过现有图片生成提交器适配参考图输入，任务输入固定源裁切及宫格上下文、镜头描述和模型参数。不能把已有“高清放大”名称当作等价实现，必须满足用户确认的生成式提取。
- [ ] 开关默认关闭。开启后展示可用模型、该模型支持的尺寸与参数、细节可能变化和模型用量说明；确认按钮明确为高清提取，不静默后台启动。
- [ ] 每帧保留原始裁切、候选结果和任务 ID，显示 queued/running/completed/failed，取消后不丢已有结果。采用或连接后续节点是显式操作。
- [ ] 重试只包含失败帧，复用现有并发设置，不发明更低限额；浏览器重载恢复任务状态。作用域改变后结果仅更新原来源。
- [ ] 同一测试文件补足上述行为；必要 API 变更须补契约测试，不绕开授权与任务存储。通过后提交 `feat: add generative HD frame extraction`。
