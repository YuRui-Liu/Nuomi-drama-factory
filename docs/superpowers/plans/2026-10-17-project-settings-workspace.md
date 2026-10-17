# 项目设置分栏工作区实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 或 executing-plans，按步骤执行。

**目标：** 四类配置直接嵌入设置工作区，取消入口弹窗套内容弹窗，保留实际范围和版本约束。

**架构：** 入口捕获一次项目/用户/画布实例上下文，各类别复用 Panel。共用未保存退出协议，不共用含糊的跨类别提交按钮。

**技术栈：** React、TypeScript、Base UI、TanStack Router、Vitest。

## 文件职责

- 修改 `frontend/src/features/creation-workspace/ProjectSettingsEntry.tsx`，创建同目录 `ProjectSettingsWorkspace.tsx`、`project-settings-workspace.css`。
- 复用或提取 `frontend/src/features/agent-configuration/AgentConfigurationEntry.tsx`、`frontend/src/features/direction-references/DirectionReferenceEntry.tsx`、`frontend/src/features/creative-settings/CreativeSettingsEntry.tsx`、`frontend/src/features/template-library/TemplateLibraryEntry.tsx` 的上下文捕获与 Panel 调用。
- 使用现有 `DirectionReferencePanel.tsx`、`CreativeSettingsPanel.tsx`、`TemplateLibraryPanel.tsx`，保持各自提交契约。
- 修改 `frontend/src/__tests__/features/creation-workspace/project-settings.test.tsx`，创建同目录 `project-settings-workspace.test.tsx`。

## 任务 1：安全嵌入与导航

- [ ] 写失败测试：打开后只有一个设置对话框；四类均在同一个右侧区域展示；角色/用户/项目或画布运行实例变化关闭旧上下文。
- [ ] 运行 `pnpm exec vitest run src/__tests__/features/creation-workspace/project-settings-workspace.test.tsx` 确认失败。
- [ ] 将 Entry 中的打开快照、runtime、isCurrent 逻辑提取供嵌入调用，保留已有 Entry 外壳以兼容其他入口。不能在 render 中反复生成新会话实例。
- [ ] 引入类别标识及草稿退出协议：

```ts
type SettingsCategory = 'agents' | 'direction' | 'creative' | 'templates';
interface SettingsDraftGuard {
  isDirty(): boolean;
  save(): Promise<boolean>;
  discard(): void;
}
```

- [ ] 类别切换/返回时未保存提供保存、放弃、继续编辑；保存返回 false 时仍在原类别，失败输入不清空。角色配置内的明确采用行为不能被通用 save 偷偷执行。
- [ ] 测试无运行时的不可用态，旧异步请求完成不影响新上下文；运行原 `project-settings.test.tsx` 后提交 `feat: embed project settings in a single workspace`。

## 任务 2：范围、版本与局部设置边界

- [ ] 写失败用例：默认入口范围 project；节点局部入口仍能选择局部范围；禁用本级、取消本级和清除项目配置调用保持区分。
- [ ] 使用真实目录条目和固定版本，不把原型示例写入状态。方向配置预览请求范围和将保存内容，成功反馈准确来源，失败保留选择。
- [ ] 复用现有优先级解析，不在 UI 重新猜测继承；项目变更不自动改写现有媒体、不提交生成。
- [ ] Agent 配置仍管理成员与专业配置，不发起委托；对象与约定保留现有字段/关系/校验；模板保留版本固定与应用确认。每类保存独立，不将四类网络请求串为全局保存。
- [ ] 保留模板的画布 flush 和 instance 检查，确保 UI 迁移不会绕开现有 mutation 幂等。
- [ ] 执行新增测试及 `creative-settings.test.tsx`、`template-library.test.tsx`，通过后提交 `feat: clarify project defaults and version scope`。

## 验收

- [ ] 按原型 O92O7D 检查宽屏左右布局与窄屏类别切换；长表单滚动不遮住当前类别操作。
- [ ] 键盘可切换分类和返回，弹窗关闭恢复项目设置入口焦点。保留必要的操作确认弹窗，但不重复打开类别入口弹窗。
- [ ] 实测保存失败、范围不可用、旧版本引用与未保存切换，记录实际截图和测试输出。
