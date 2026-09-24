# 成本监控验收记录

工作分支：`feat/project-cost-monitoring`，独立 worktree。真实供应商调用未用于验收。

## 已执行回归

2026-09-24，在本 worktree 使用主仓库 Python 虚拟环境与 `PYTHONPATH=src`：

- `pytest tests/costs tests/test_api_project_costs.py tests/test_api_model_credit_cost.py -q`：最终候选版本 `9564e2e` 为 200 项通过，8 条既有依赖弃用警告（含 22 项保守历史回填测试）。
- `pytest tests/media_capabilities/runtime tests/media_capabilities/image tests/media_capabilities/tts tests/media_capabilities/video/test_pipeline.py tests/media_capabilities/video/test_runninghub_h3.py tests/test_grsai_image_transport.py tests/test_runninghub_h3_video_generator.py tests/test_knowledge_runtime_codex.py tests/test_codex_process.py -q`：371 项通过，9 条既有依赖弃用警告。

前端最终候选版本 `9186959`：

- `vitest run src/features/costs src/__tests__/components/layout/project-navigation-routes.test.ts src/__tests__/performance/navigation-resource-loading.test.tsx src/__tests__/i18n/locales-json.test.ts`：63 项通过。
- `tsc -b`：退出码 0。
- `vite build --mode ce`：退出码 0；保留既有 baseui/tanstack 循环分包与大分包警告，成本页面单独懒加载。

## 浏览器验收环境

- 数据根目录：`/private/tmp/project-cost-monitoring-demo`，不连接生产数据。
- 本机后端端口 8787，前端端口 5177。
- 测试项目：`cost_monitor_demo`，ID `01M37DRJN8BV5XC287Q528T7KX`。
- 数据为合成夹具：已知费用 ¥294.00（确认 ¥67.20，估算 ¥226.80），待核算 1 笔；共享订阅 ¥150.00，不计入总额。

已检查首屏的金额与完整性提示、点击 RunningHub 视频分类后仅列出 7 笔对应调用、详情抽屉的金额与审计来源。390px 窄屏的根元素宽度与 scrollWidth 均为 390px，无页面整体横向溢出。

曲线已在浏览器确认每日/累计切换，累计最终 ¥294.00 与总览一致。键盘 Enter 选择 9 月 23 日读取已确认 ¥16.80、估算 ¥56.70、总额 ¥73.50、待核算 1 笔以及不完整提示。另创建空项目 `cost_empty_demo` 和全部未计价项目 `cost_unknown_demo`，分别显示“暂无费用记录”和“费用待核算，暂无可绘制金额”；没有虚构零费用曲线。

浏览器新增 `demo-image-price/1` 规则（每张 ¥1.25），预览明确影响 1 笔 `demo-unpriced`，点击应用后总额变为 ¥295.25、估算 ¥228.05、待核算 0，明细同步显示 ¥1.25。累计最终 ¥295.25 与总览一致。原共享订阅 ¥150.00 不变；另保存空金额订阅显示“未登记”，不改变总额。

临时关闭独立后端后点击刷新：显示“刷新失败，当前显示上次成功获取的数据”，仍保留 ¥295.25。恢复服务并重新进入项目后正常显示，无费用错误提示。390px 规则对话框可纵向滚动，标签与输入均可见；验收后恢复正常视口。

## 设计验收对应

| 要求 | 证据 |
| --- | --- |
| 渠道和媒体覆盖 | `test_provider_capture.py` 与 371 项渠道回归；覆盖范围详见 provider coverage 文档，未虚构实际金额字段 |
| 计量、步长、最低量及舍入 | `test_pricing.py`、`test_models.py`；十进制与有理数精确换算，缺少依据保持未计价 |
| 幂等、重试、失败扣费 | `test_store.py`、`test_service.py`、`test_provider_capture.py` |
| 实际替换估算、版本冻结、补算审计 | `test_reprice.py`，包含补算后再次观察及过期预览原子拒绝；浏览器补算闭环 |
| 项目、账户及外部 ID 隔离 | store/service/provider capture/API 契约测试；其他项目详情不可越权读取 |
| 汇总、分类、每日/累计一致 | `test_queries.py` 的逐笔分舍入、跨日、缺口、退款回写测试；浏览器末点 ¥295.25 对账 |
| 订阅隔离及额外费用 | service/query 测试；浏览器 ¥150.00 与未登记订阅独立显示 |
| 页面交互 | 24 项 costs 前端测试与上文浏览器记录；包括项目切换、筛选、曲线、键盘、详情、空态、错误及窄屏 |
| 恢复、未知提交、权限与安全字段 | provider capture/API/backfill 契约测试；不重发生成，不复制历史提示词或完整响应 |

限制仍然保留：历史直接调用无法全部重建，直接 UUID 调用在接受后的账本写入失败且进程丢失时可能无法自动关联；账本与界面通过待确认/未计价/覆盖缺口表达这些情况。

最终独立集成审查覆盖 `a6c4c4e..9564e2e`，未发现阻塞性正确性、权限或集成缺陷；保留独立分支与 worktree 供后续集成。
