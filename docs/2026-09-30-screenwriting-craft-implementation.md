# 剧本创作与导演转译实施记录

日期：2026-09-30。对应已确认的 B 方案。

## 已接入

- 剧本正文生成采用小云雀示例的集号场次、场景、人物、△动作与人物对白结构；按目标集号生成，移除与正文冲突的分析标题要求，旧格式仍可读取。
- 梗概与正文方法加入人物欲望、阻力、策略、结果、信息差和回报；明确人物猜测不等于客观事实，安静场景也可以有效。方法不包含 E1 专有剧情。
- 整场改写兼容新旧场头及 CRLF 原文偏移。改写依据实际文档类型加载方法，自定义方法保持替换语义，并在任务入队时冻结方法快照。
- 导演计划中的逐镜头意图进入叙事组 beat、配对镜头上下文和视频提示词优化输入，保留镜头归属与顺序。修改意图会改变缓存输入；单组与整集优化器缓存版本分别更新为 8、5。
- OS、广播等声源保留语气信息，人物身份不混入括号标签；普通对白不继承上一行 OS。单独的语气行仍可作用于下一句。

## 实际路径与计划调整

导演意图在 `task_backend/runners/narrative_group_video.py` 的真实调用路径进入优化器 `director_context`，再交给既有编译器；没有为了符合初始文件清单而修改不需要改动的适配器或编译器。

静态生图继续使用现有开场状态与构图描述。未将自由文本意图直接追加到首帧，避免“听见声音后才转头”等后续动作提前出现在开场图中；已加入回归断言。这不等于实现了新的静态注意力转换算法。

## 验证证据

综合回归命令：

```sh
.venv/bin/python -m pytest tests/test_director_intent_projection.py tests/test_h3_director_intent.py tests/director_plan tests/screenplay_semantics tests/test_screenplay_scene_parser.py tests/script_creation tests/agent_teams/test_runtime.py tests/test_narrative_group_service.py tests/test_storyboard_start_frame_prompt.py tests/test_h3_generation_source_contract.py -q
```

结果：**533 passed，8 warnings，17.01 秒**。警告来自依赖的弃用提示。

视频提示词专项回归：

```sh
.venv/bin/python -m pytest tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_episode_pack.py tests/test_task_narrative_group_video_runner.py -q
```

结果：**237 passed，8 warnings**。与综合测试分开报告，不将两组结果当作去重覆盖率。

新增测试覆盖真实导演计划保存与激活后的意图投影、旧数据无意图兼容、成对镜头顺序、源对白不变、模拟模型输出进入真实优化器及编译器、缓存命中与意图变更失效。失败用例先复现，再修复。

E1 候选正文只读解析确认：3 个场景、27 行对白，出镜人物为岑砚与居民甲乙丙；广播不成为出镜人物，岑禾不因被提名而创建角色；源块文本与原文行一致。

## 验收边界

- 本轮采用候选 E1 解析与独立链路 fixture 的分层离线验证，尚未完成整份 E1 从模型改稿到成片的连续验收。
- 未覆盖生产 E1，未发起付费图像、视频或配音调用，未向项目写入新的角色、场景或道具。本轮没有修改画幅配置；16:9 的实际生成请求仍需在后续整集验收中核对。
- 尚未用真实供应商验证配音时长、口型与最终成片的情绪效果；测试通过证明数据与提示词链路，不证明艺术质量。
- 未改动界面布局；未将共享工作区中大量既有修改批量提交。

相关资料：[调研](2026-09-30-screenwriting-research.md)、[E1 候选正文](2026-09-30-e1-screenplay-b-candidate.md)、[创作比较](2026-09-30-e1-creative-comparison.md)。
