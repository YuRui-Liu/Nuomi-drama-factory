---
name: nuomi-canvas-production
description: Use when 在 Nuomi 创作画布上从完整剧本自动建立制作节点与连线、执行叙事组图像和视频、合成完整一集，或恢复已有画布制作运行。
---

# 创作画布整集制作

使用 `nuomi --project PROJECT canvas`，网页与 CLI 读取同一份画布和服务端运行。命令未安装时，在仓库根用 `.venv/bin/python -m novelvideo.production_cli` 替代 `nuomi`。API 默认 `http://127.0.0.1:8780/api/v1`，可用 `--base-url`；认证沿用 `NUOMI_TOKEN` / `NUOMI_SESSION`。

先读[执行手册](references/cli.md)。已有 run_id 时读[恢复说明](references/recovery.md)，直接核对并恢复原运行，避免另起一套重复生成。

1. 查询 `canvas capabilities`。这是节点/operation 的实现清单；`available` 不等于真机通过。保存的 runtime、模型和并发配置由后端读取。必要能力不可用时修复公开入口或报告明确阻塞。
2. 确认有完整剧本和场次标题。默认使用用户剧本，缺失正文不能自行冒充；测试时可明确使用[完整四场示例](fixtures/this-time-no-blanks.md)。末尾须有“【本集完】”。
3. 新建独立画布，调用 `plan`，等待返回的 run_id 成功，`validate --plan` 检查原文覆盖与时长，再以当前 revision `apply`。这些步骤自动建立原文、计划、参考资产、叙事组分镜、组视频及合成节点。依照执行手册的参数表，用 `nodes configure` / `nodes update` 设置并回读每个节点的画幅、模型、尺寸、风格及参考图；不得只把参数写进提示词。规划默认 1K 不代表用户要求的最终交付质量。
4. 先执行[风格与人物设计检查](references/visual-continuity.md)。规划完成不代表参考图已合格：先按资产节点逐项运行并目视核验，统一风格、锁定人物设计与参考图版本后，才 `run --target final` 并用 `wait` 跟踪。以叙事组为制作单位；保持整组分镜到整组视频的依赖。不要改成逐镜头付费循环，也不要用主线 batch 做完成品再回填画布。
5. 失败时读节点 error 和原尝试。提交结果不明先对账；只有明确失败且原因已修正才 `retry --failed`。公共入口有问题时修入口，不直接调用供应商或改画布 JSON。
6. 成功后 `export`，在同一画布核对输入、输出与来源，播放成片。核验场次顺序、关键动作/对白、结尾、声音和实际时长。默认 90 秒验收范围 85–95 秒，不能用缺剧情或非预期黑场凑时长。

交付画布链接、run_id、可播放成片及仍未通过项。接口接受、自动测试通过或单个媒体任务完成都不能替代整集验收。若任务要求全节点验收，按 capabilities 中的类型和 operation（skillNode 另按 skill ID）逐项保存真机证据，保留 blocked/failed 行。
