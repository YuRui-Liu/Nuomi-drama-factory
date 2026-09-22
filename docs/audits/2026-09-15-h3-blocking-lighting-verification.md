# H3 站位与灯光：首批实施验证

日期：2026-09-15。结论：代码层关键链路通过相关回归；旧灯塔视频缺陷被真实视觉检查识别。**尚未生成并验收修复版 E1，不能宣称生产可用或整份方案完成。**

## 已实现

- DirectorPlan 保存带来源的站位、世界/画面坐标、动作轴线、机位侧、行动路线，以及固定/移动光源、主光、阴影、曝光和切镜意图。旧方案仍可读。
- 导演规划、编辑、参考图文字描述、连续性契约与 H3 编译传递同一组摄影字段。新增规划使用 v3 校验，空字段和缺失字段不能作为完整导演约束。
- 新 H3 视频入口强制 enforce，缺少导演数据返回明确错误；CLI 批量流程不复用被标记为 production_ready=false 的旧方案，而请求新版本。旧媒体不覆盖。
- enforce 不再额外调用一次未使用的 legacy 提示词优化。
- 连续分段不再合并轴线、机位侧或灯光约束冲突的镜头；正常硬切仍被允许。
- 参考图检查先于视频提交；实际视频抽取起、中、末帧，并检查组内相邻片段切点。passed、failed、unavailable 分开记录，检查失败不得放行。
- 视觉缓存绑定图片内容、导演事实、检查上下文、运行时与策略；缓存故障不会伪造通过。
- 前后检查共用冻结 ShotPlan，存入 manifest；重放不能改用新的活动导演方案。
- 生成后质检失败保留视频，组状态 partial_failure，manifest 片段 quality_mismatch，持久片段 partial_failure、qc_passed=false。本路径不自动重新生成。

## 回归证据

以下命令最后运行：**1,640 passed，8 条第三方弃用警告，15.07 秒，退出码 0**。

```sh
/Users/liuyuxiang05/Liu/.venv/bin/python -m pytest \
  tests/shot_continuity tests/director_plan tests/media_capabilities/video \
  tests/test_task_narrative_group_video_runner.py \
  tests/test_h3_direction_preflight.py tests/test_api_narrative_groups.py \
  tests/test_api_director_plans.py tests/test_production_batch.py \
  tests/test_production_cli.py tests/test_task_director_plan_runner.py -q --tb=short
```

新增关键测试经历失败后修复：摄影字段传递、缺失数据阻断、无多余 legacy 优化、冻结质检事实、质检失败片段状态、冲突镜头不合并。视觉检查测试覆盖正常切镜、失败/不可用区分、损坏图片、缓存与本地 FFmpeg 抽帧。

定向 Ruff 检查通过：cinematography.py、generation.py、visual_review.py、production_review.py、narrative_group_video.py；`git diff --check` 通过。这不是全仓全量测试或上线验收。

## 真实旧视频诊断

使用软件已配置的 Codex 路由（gpt-5.6-sol）检查灯塔项目既有 `group-1_r4_segment_002.mp4`，没有调用图片或视频生成供应商，没有修改 `bei` 或灯塔活动方案、旧 manifest、原视频。

因历史方案没有新摄影字段，诊断脚本在内存中按 r4 已保存提示词构造检查预期，明确标记 diagnostic_only；这不是自动补全成功的证明，也不是历史数据迁移。

实际报告 status=failed、dimension=camera：起帧为人物胸部近景，中间与末帧变成包含全身、石阶与灯塔的广角构图，与静止机位、不允许后续拉远的预期冲突。报告保留三帧观察，未将有意硬切本身定为缺陷。

本机证据：`/private/tmp/nuomi-h3-cinematography-result.json`；复现脚本 `/private/tmp/nuomi-h3-cinematography-check.py`。临时文件可能被系统清理，不应作为长期唯一审计存档。

## 未完成与发布限制

1. 修复版 E1 参考图、视频、整集合成和声画质量的真实对照尚未完成。原累计 50 元授权剩余额度无法从现有积分记录可靠核实；暂停新增付费生成，不把提交次数换算成人民币。
2. 已有视频重检已有服务和缓存，但通用 UI/CLI 的免重购重检/恢复入口尚未产品化；不要直接重跑生成来替代重检。
3. 自动切点检查目前限单组内相邻片段；跨叙事组、整集级切点验收仍需接入。
4. 道具左右手/持有者及动作阶段仍主要依赖原 ShotPlan 状态与摄影文本，尚未补足全部专门结构化字段。外部观察来源也还需更强的媒体摘要绑定，不能仅因 source_ids 非空就视为来源可信。
5. 正常硬切不过度拦截已通过模拟响应回归，但尚无新生成合格对照片的真实模型通过证据。三帧抽查不等于逐帧或完整运动质量保证。
6. 本轮没有重启现有 API 服务；代码验证与独立实测使用当前工作区，新入口部署生效仍需在确认无在途任务后加载。

工作区已有修改全部保留；没有提交或推送混合改动。
