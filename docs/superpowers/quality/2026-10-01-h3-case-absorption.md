# H3 三源案例吸收验收记录

日期：2026-10-01。状态：代码、数据包及隔离浏览器验收完成；当前正式服务需在运行中的视频任务结束后重载才能加载新接口。

## 实际导入

| 来源 | 固定提交 | 解析记录 | 独立案例 |
| --- | --- | ---: | ---: |
| BeatAPI/awesome-minimax-h3-prompts | `03a0d24259341413597b3c6ff0ce0c0164007e8b` | 330 | 330 |
| SkyNotSilent/awesome-MiniMax-H3-cases | `75a80139a9118a2739d2041981260dd2c3f4ac4b` | 2112 | 2112 |
| stimQQ/stunning-minimax-h3-prompts | `a964b870e38a99eac3085d2e94a7bf928f9441fb` | 294 | 222 |
| 合计 | | 2736 | 2664 |

72 条归并是 stimQQ 总目录与单篇详情的重复。跨仓库同帖可能含不同视频，保留 336 组重复候选，未按相似标题或帖子 URL 强制合并。解析错误 0，已归并条目的来源声明冲突 0。案例包包含逐项异常和候选清单。

明确排除 BeatAPI 的重复目录索引及 stimQQ 中文展示目录。SkyNotSilent 的 2112 个媒体地址是无已验证公开域名的相对路径，因此未转成猜测的 URL；原始案例来源链接均保留。没有下载、打包第三方视频或提示词全文。

提示词出处：作者公开 1140、未公开 1449、反推 72、官方 3。上游验证声明独立保存，全部案例的本地效果状态为 `unverified`，授权状态为 `unknown`。公开可访问不代表可重新分发。

固定版本导入校验提交、已跟踪内容及完整文件集合，拒绝额外未跟踪或忽略文件。再次导入继承案例 ID 和导入时间；报告写入成功后才发布正式包。禁止报告与正式包同路径及符号／硬链接别名。字段名不得参与题材分类。

维护命令（从项目根执行，三个只读 Git 快照目录分别命名为 `beatapi`、`skynotsilent`、`stimqq`）：

```sh
PYTHONPATH=src .venv/bin/python -m novelvideo.technique_library.cli \
  --source-root /path/to/pinned-snapshots \
  --output src/novelvideo/technique_library/data/catalog.json
```

当前发布包 SHA-256：`b36f6de1d1ead6ed45c5c26a6a221698aebfa925bdae0760f7839674aa5dd2ac`。用途由来源分类字段自动映射，可能多标签，不等同于人工观看鉴定；292 个案例没有匹配用途，仍可在全部案例中检索。

## 手法提炼依据

24 张卡包含原有 8 张及新增 16 张。既有卡片全部旧字段、精确版本和内容哈希保持不变。用途与关联案例是展示字段，不进入生成投影。新增卡片来源类型为 `analysis_only`，提炼依据明确区分作者文本、反推文本与官方文本。

| 新卡 | 已阅读的文本依据 | 提炼边界 |
| --- | --- | --- |
| 产品环绕揭示 | BeatAPI `luxury-perfume-commercial` | 单次环绕与停留，不移植品牌或粒子效果 |
| 俯拍分步组装 | BeatAPI `top-down-stop-motion-style-video-of-four-hands-assembli-679353` | 仅使用用户已有物料与步骤 |
| 光源触发揭示 | BeatAPI `16-9-15s-hyper-realistic-korean-noir-crime-teaser-a-447606` | 已有光源揭示已有主体 |
| 低机位后退跟随 | BeatAPI `low-angle-fashion-tracking-film-062019` | 保持主体尺度与行进方向 |
| 同位动作匹配转场 | BeatAPI `strawberry-seasonal-match-cut-food-commercial-117943` | 仅切换用户指定场景 |
| 使用动作插入特写 | BeatAPI `image-as-the-exact-character-reference-preserve-her-fac-150697` | 操作与细节关系，不添加产品功效 |
| 投影光表演 | BeatAPI `mv-one-scene-long-take-low-angle-shot-background-floor-516749` | 需要已有投影或变化光源，不增加音频依赖 |
| 接触质感微距 | stimQQ `macro-beauty-routine-commercial`，反推 | 抽取接触前后质感，不限定美妆 |
| 居中行进衔接 | stimQQ `seamless-wardrobe-morph-journey`，反推 | 仅衔接已给定造型及背景 |
| 表演弧线靠近 | stimQQ `intense-live-rock-performance`，反推 | 弧线靠近表演重点，不新增歌词 |
| 动作落点切换 | stimQQ `dynamic-urban-parkour-flow`，反推 | 单个已设定动作的准备、发生与落点 |
| 倾听反应对切 | stimQQ `echoes-of-a-midnight-call`，反推 | 需要已有人物关系，不新增通话或对白 |
| 双人动静反差 | stimQQ `anime-passenger-live-action-driver`，反推 | 抽取表演节奏，不移植混合画风 |
| 事件余波停留 | SkyNotSilent `official-t2va-starship`，官方 | 既有事件结束后的反应，不新增爆炸 |
| 前后景移焦 | SkyNotSilent `official-fl2va-ramen`，官方 | 需要已有前后景，不新增主体 |
| 台词后收束 | SkyNotSilent `official-ref2va-lamb`，官方 | 仅抽取台词结束后的表演；不提供原案例的视频编辑或音频复用能力 |

以上依据来自实际阅读固定快照的文本，没有观看或复测案例视频。新卡保守支持当前图生／参考图模式；没有直接沿用外部案例时长作为模型能力。前置素材条件写入卡片指导及避免事项。

## 验证与待办

- 导入器已通过独立规格及质量审查；发现的版本完整性、ID 稳定性、报告发布顺序和分类问题均已修复并添加回归。
- 任务 1、2、3 均通过独立规格与质量审查，最终跨模块审查通过。
- 根代理独立运行后端 `pytest --import-mode=importlib tests/technique_library tests/freezone/video_director -q`：156 passed。该选项避免两个目录同名测试模块冲突。
- 前端 `vitest run video-director technique-library technique-cases header`：19 文件、179 passed；`tsc -p tsconfig.app.json --noEmit` 退出 0。最初独立检查发现新增测试使用不支持的 `.at` 和 `exact` 参数，修复后重新通过。
- Vite 生产构建与画布 bundle budget 检查退出 0；仍有共享 chunk 循环及大 chunk 提示。没有修改构建预算掩盖提示。
- 最终案例包临时副本重导入后字节完全一致。新卡上限与当前帧对齐一致，覆盖 15 秒及自身上限回归。
- 实际浏览器采用独立 5174/8781 验收端口：新目录使用真实路由与发布包，其余读取转接正式 API，避免中断正式 8780 上正在渲染的视频。未启动第二套任务执行器。
- 键盘路径验证实际 2664 案例、翻到第 2 页、官方过滤后回第 1 页并显示 3 例、案例跳到对应手法、24 张卡、音乐用途 4 张卡。390×844 窄屏详情与返回正常；关闭后焦点回到手法库入口。
- 导演台中筛选并应用“事件余波停留”，片段显示选卡结果，随后清除并恢复原来的无卡状态。该验证仅改页面草稿，隔离代理不转发画布保存／生成写请求；真实持久化与付费生成不在本轮浏览器验证范围。精确版本应用由组件和后端测试覆盖。
- 自动化工具的鼠标点击未可靠打开入口，键盘路径可完成全部操作；新增显式鼠标按下／松开组件回归通过，未据此盲改事件处理或宣称完成实际鼠标验收。
- 本地截图：`/private/tmp/nuomi-h3-case-library-verified.png`（案例关联到手法）、`/private/tmp/nuomi-h3-segment-applied.png`（片段应用）。
- 未触发付费生成，不能据此声称视频成片质量已提升。
