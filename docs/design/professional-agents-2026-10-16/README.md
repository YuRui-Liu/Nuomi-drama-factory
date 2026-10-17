# 专业 Agent 与漫剧全链路原型

更新：最新统一工作空间设计见[U01—U09 新稿](../agent-node-workspace-2026-10-16/README.md)。本目录的 P01—P09 保留为历史参考。

## 2026-10-16 当前实现交互重绘

按用户“根据当前软件实际交互重绘制 pen 图”的要求，同一 `E:/Proj/nuomi.pen` 已新增 R01—R09，置于原稿下方，按三列排列。原 P01—P09 和 U01 保留供对照。本轮依据当前组件源码重绘顶部导航、左上画布切换、右侧 Agent 浮层、配置弹窗与左下制作面板，不将规划中的专业团队目录和全链路交付页当作现有界面。

| 新画板 | 节点 ID / 预览 | 源码依据 |
| --- | --- | --- |
| R01 画布工作区 | [ZF3PX](../current-interactions-2026-10-16/ZF3PX.png) | CreationWorkspace、CanvasWorkspaceSwitcher、FreezoneShell、project-header-navigation |
| R02 项目 Agent 配置 | [u5SpYZ](../current-interactions-2026-10-16/u5SpYZ.png) | AgentConfigurationEntry、AgentConfigurationPanel |
| R03 引用节点到对话 | [IeOgn](../current-interactions-2026-10-16/IeOgn.png) | CanvasRunPanel、FreezoneChatDock |
| R04 个人 Agent 编辑与预览 | [xe8FH](../current-interactions-2026-10-16/xe8FH.png) | AgentConfigurationPanel、AgentEditor |
| R05 画布保存冲突 | [v0Xkaf](../current-interactions-2026-10-16/v0Xkaf.png) | FreezoneShell / CanvasConflictOverlay |
| R06 制作失败与重试 | [KKxmq](../current-interactions-2026-10-16/KKxmq.png) | CanvasRunPanel |
| R07 创作模板固定版本 | [hbDQi](../current-interactions-2026-10-16/hbDQi.png) | TemplateLibraryEntry、TemplateLibraryPanel |
| R08 制作中与停止 | [oJkxi](../current-interactions-2026-10-16/oJkxi.png) | CanvasRunPanel |
| R09 视频节点播放与截帧 | [DxBXD](../current-interactions-2026-10-16/DxBXD.png) | VideoNode |

验证边界：这是 1600 × 1000 的可编辑交互状态示意，布局与入口经源码核对，控件细节及内容有简化；并非运行页面的逐像素导入。本机无运行中的前端且未安装前端依赖，本轮未进行实机回放。示例项目、个人配置、聊天、任务数量与视频时长均为示例数据；播放区为明确标注的占位。未建立可点击页面跳转，不表示业务端到端验收通过。已查看九页截图，最终九页结构检查均无裁切问题，PNG 已导出。

交互核对：配置采用不启动任务；引用节点前保存画布；执行失败显示“重试失败节点”，等待处理或提交结果待核实显示“恢复检查”；运行中支持停止。现有输入与状态位置保留，未引入独立制作阶段页面。

## 原规划稿记录

状态：本轮 brainstorming 前的九页草稿，尚未按最新确认的统一布局重画。产品依据为[主 PRD 第 15 节](../../superpowers/specs/2026-10-13-agent-canvas-prd.md)；下列 PDF／PNG／Pen 不视为本轮已经批准的视觉稿。本次仅更新说明。

日期：2026-10-16。可编辑设计源：[nuomi.pen](E:/Proj/nuomi.pen)。通过用户已配置的 Pen MCP 服务创建，沿用 Nuomi Editorial Black 配色。

[查看九页 PDF](export.pdf) · [PRD](../../superpowers/specs/2026-10-16-professional-agents-script-handoff-prd.md) · [专业岗位与交接契约](../../superpowers/specs/2026-10-16-manga-production-agent-contracts.md) · [交互说明](../../superpowers/specs/2026-10-16-professional-agents-interaction-design.md)

| 画板 | 节点 ID | 预览 |
| --- | --- | --- |
| P01 专业 Agent 目录 | `Gd4VE` | [PNG](Gd4VE.png) |
| P02 详情与局部委托 | `oSie6` | [PNG](oSie6.png) |
| P03 剧本交接预览 | `XJlF3` | [PNG](XJlF3.png) |
| P04 画布与主创接手 | `aVPqA` | [PNG](aVPqA.png) |
| P05 重交接与编辑冲突 | `G0qpp` | [PNG](G0qpp.png) |
| P06 部分完成与任务恢复 | `o2dYYr` | [PNG](o2dYYr.png) |
| P07 漫剧专业团队 · 18 岗位 | `Jup0N` | [PNG](Jup0N.png) |
| P08 完整制作任务与专业方案 | `xDtlw` | [PNG](xDtlw.png) |
| P09 成片交付与修改回路 | `htUaM` | [PNG](htUaM.png) |

每页为 1440 × 960，可编辑节点保存在 `.pen` 内；PNG 与 PDF 为查看副本。已逐页查看 MCP 截图。人物、剧情、任务和能力状态均为原型示例，P09 播放区域明确标为占位，不含真实影片。

当前交付是可编辑交互状态稿，页面底部说明和按钮目标记录转移规格；未建立或验证可点击页面跳转，未连接真实业务执行。付费授权卡、空状态和部分异常路径的完整规则见交互说明，不把文字规格视为已实现功能。

连接记录：当前会话旧工具为 `get_editor_state` / `batch_design`，但本地已配置服务实际提供 `get_app_state` / `execute`。通过 MCP 标准握手建立新连接后可正常访问设计，未修改用户配置。原有画板 `bi8Au` 与按钮组件 `GNzPH` 均保留。
