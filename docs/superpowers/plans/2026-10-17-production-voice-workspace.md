# 角色声音工作区与独立配音实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 或 executing-plans，逐项验证。

**目标：** 设定与试听留在声音工作区，固定声音版本连接独立配音节点。

**架构：** 复用现有 CharacterVoicePanel、声音版本 API 和画布 audio 节点；先测试固定版本适配，再迁移布局，不重复实现声音生成后端。

**技术栈：** React、TypeScript、TanStack Query、Vitest。

## 任务 1：固定版本到配音节点

文件：修改 `frontend/src/features/canvas/nodes/CharacterVoiceNode.tsx`；创建 `frontend/src/features/canvas/production/voice/voiceNodeBinding.ts`；由集成者更新 `frontend/src/features/canvas/domain/canvasNodes.ts`；新增 `frontend/src/__tests__/features/canvas/voice-node-connection.test.tsx`。

- [ ] 写失败用例：选择试听候选不能被当成已采用版绑定；连接仅建立节点/边，不调用配音；新角色音色不更换旧绑定。
- [ ] 执行 `pnpm exec vitest run src/__tests__/features/canvas/voice-node-connection.test.tsx` 确认失败。
- [ ] 复用现有 `voiceReference` 字段的 character_id、identity_id、slot、version_id、audio_sha256。适配既有 `audioNode` 的 voiceRef，读取实际类型后映射，不伪造未注册的 dubbingNode 类型。
- [ ] 连接既有目标时校验项目与角色版本，缺少绑定给出返回声音工作区的明确动作。新建/复用键至少包含源声音节点和固定版本；旧配音输出不覆盖。
- [ ] 运行新增测试与 `character-voice-binding.test.tsx`、`dubbing-panel-fingerprint.test.tsx`；通过后提交 `feat: connect adopted voice versions to dubbing nodes`。

## 任务 2：试听工作区布局

文件：创建 `frontend/src/features/canvas/production/voice/CharacterVoiceWorkspace.tsx`；修改 `frontend/src/components/assets/character-voice-panel.tsx`、`frontend/src/features/canvas/nodes/CharacterVoiceNode.tsx`；新增 `frontend/src/__tests__/features/canvas/character-voice-workspace.test.tsx`。

- [ ] 写失败测试：默认展示角色、当前绑定和候选；选择新候选不采用；确认已试听后才能采用；采用后仍需明确绑定。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/canvas/character-voice-workspace.test.tsx` 确认失败。
- [ ] 提取受控展示区域：角色选择、设计/导入/复用入口、试听台词、候选对比。保留已存在描述解析差异审阅、非言语模式、身份与历史声音能力，不因简化布局删除功能。
- [ ] 切换角色先停止当前试听，保护编辑草稿；候选生成仅在显式点击后发生。正式对白生成按钮不放入声音工作区。
- [ ] 未绑定显示“绑定此声音版本”；已绑定显示版本及“返回画布，连接配音节点”。角色 API 的采用和画布固定引用保存不是一个隐式动作。
- [ ] 验证加载失败、试听生成失败、版本已失效和跨项目异步结果；运行新增测试、任务 1 测试及 `src/__tests__/components/assets/character-voice-panel.test.tsx`，通过后提交 `feat: organize voice design and audition workspace`。
