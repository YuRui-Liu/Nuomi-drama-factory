# 按需画布导航与选区委托

源设计：`E:/Proj/nuomi.pen`。使用 pen.dev / Pencil MCP 打开编辑。

| 状态 | 预览 |
| --- | --- |
| B01 默认画布 | [aCi4S.png](aCi4S.png) |
| B02 画布切换 | [fl9B7.png](fl9B7.png) |
| B03 新建画布 | [pGOWD.png](pGOWD.png) |
| B04 单选委托 | [dg2MC.png](dg2MC.png) |
| B05 多选委托 | [n76tX.png](n76tX.png) |
| B06 失效选区 | [z3R2r.png](z3R2r.png) |
| B07 项目设置 | [Ciu8N.png](Ciu8N.png) |

[PDF 导出](export.pdf)。这些是设计稿，不是运行中软件的截图。

已将按需导航、创建弹窗、项目设置入口、选区委托与草稿隔离接入前端。实际浏览器检查覆盖 1024 窄屏首次开启侧栏避让、1280 多选与创建取消焦点恢复、1600 单选委托，以及选区变化时保留原范围。完整实施及验证记录见 [执行计划](../../superpowers/plans/2026-10-17-canvas-navigation-selection-delegation.md)。
