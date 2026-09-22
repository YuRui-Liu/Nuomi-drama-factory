# 漫剧生产链路审计与修复

范围：角色状态图 QC、候选采用、叙事组草图/实图、制作 CLI、批量状态衔接和合成导出。结论针对这些路径，不等同于全仓库或线上生产认证。

## 发现与处理

| 级别 | 发现与证据 | 处理 |
| --- | --- | --- |
| P1 | `identity_sheet_qc.py` 将 12 项布尔检查全部作为拒绝条件，主观“死眼/塑料感”单独出现即可让可用状态图无法采用 | 5 项观感判断降为可见 warning；身体裁切、身份不一致、额外人脸等仍阻断；持久化全部检查及分级 |
| P1 | 质检要求精确 40%/70% 边界、所有鞋底可见、任何头发遮挡均不合格，容易把正常站姿和绘画差异作为缺陷 | 改为按实际参考可用性判断，容忍轻微边界偏移和不影响识别的头发；未知不当作缺陷证据 |
| P2 | `_IdentitySheetQcChecks` 的普通 bool 会把字符串/整数转换成布尔值，与文本 JSON 的严格检查不一致 | 结构化输出启用 strict，异常结果继续标记 qc_unavailable |
| P1 | 前端对无草图生成默认放行，但 API `allow_unconstrained` 默认 False；页面先展示草图，容易形成付费必经步骤 | API 默认 True，保留显式 False；实图优先，未使用的草图折叠，提示额外费用 |
| P1 | 生成中“整组重新生成”仍可点击 | queued/running 时禁用，降低重复提交风险 |
| P1 | 原 CLI 只有导入、Cognee、服务启动与备份；没有批量视频生产协调 | 新增轻量 `nuomi`、兼容 `novelvideo production` 和项目 Skill；支持多集制作、机器校验方案自动激活、自动引用、状态复用、异常隔离与成片汇总 |
| P1 | 仅靠提交成功或任务 completed 无法断言产物可交付；完成的图也可能 QC 失败 | CLI 精确等待 task_id；QC 失败非零退出；批量再次读取实际阶段产物；合成后确认下载 URL |
| P1 | 实图重生成没有持久标记旧视频过期，断点续跑或直接合成可能继续使用旧视频 | 服务端在新实图版本创建时持久标记视频失效、保留旧媒体；清除重建阶段自己的过期标记；合成拒绝过期视频，批量自动重建依赖阶段 |
| P2 | ZIP 导出对旧逐 Beat 视频也加 group 前缀，破坏已有 archive 文件名契约 | 只给导演组命名空间加前缀，恢复旧视频文件名 |
| P2 | 8 个合成/字幕/导出测试仍 monkeypatch 已移除的 load_groups，无法覆盖现行逻辑 | 更新为 load_materialized_groups，恢复对导演组去重、混合排序和音轨缺失的验证 |

## 批量策略

默认 `render -> video -> compose`，草图显式开启。正常检查通过项自动继续，不要求逐组人审。缺少必需素材、方案校验失败、引用过期等只影响相关组，其他集继续。状态来源为服务端，已有任务按作用域和 ID 接续；已付费媒体不因重启被无条件重做。

传输失败不自动重发写请求。批次写入尝试有上限；该上限不代表供应商内部调用次数或货币成本。旧失败任务需要显式 `--retry-failed`，每阶段每批最多一次。最终合成没有来源指纹，因此重做合成以确保输出来自当前视频，不重复生成付费片段。

## 验证边界

新增测试先复现主观 QC 拒绝、默认草图阻断、结构化类型转换、生成中按钮仍可用和缺失批量入口，然后修复。HTTP 测试使用受控传输模拟服务端任务状态，覆盖多集执行、断点复用、缺失资产隔离、自动方案激活、画幅、提交上限、超时、认证与错误码。

本轮不调用付费图像或视频服务，也不修改真实项目素材。真实视觉误判率、长批次会话续期、供应商容量及实际成本仍需在部署环境验证。历史已存 QC 结果不会自动改写；新生成结果使用新策略。原有不可用 QC 服务仍不能被假定为通过。

## 实际验证结果

- 后端相关回归：451 passed，8 个现有依赖弃用警告，退出码 0。
- 前端叙事工作台及角色状态版本：18 个测试文件、162 项测试通过，退出码 0。
- `tsc --noEmit -p tsconfig.app.json`：通过。
- 本轮修改的 Python 文件 Ruff 检查、`git diff --check`：通过。
- Skill 校验与 CLI 模块入口 help / batch dry-run：通过。
- `pre-commit run gitleaks --files ...`：未能运行，环境未安装 pre-commit / gitleaks（退出码 127）；没有声称完成秘密扫描。

后端回归命令：

```bash
.venv/bin/python -m pytest \
  tests/character_visual tests/test_character_image_runner.py \
  tests/test_character_state_assets.py tests/test_api_narrative_groups.py \
  tests/test_production_cli.py tests/test_production_batch.py \
  tests/production_workflow tests/test_api_production_assets.py \
  tests/test_task_narrative_group_runners.py tests/test_task_narrative_group_runner.py \
  tests/test_compose_episode_h3_director.py tests/test_api_compose_export_contract.py \
  tests/test_episode_export_h3_director.py tests/test_cli_cognee_commands.py \
  tests/test_cli_store_lifecycle.py tests/test_narrative_group_service.py \
  tests/test_compose_episode_atomic_publish.py tests/test_compose_episode_audio_source.py -q
```

前端回归（frontend 目录）：

```bash
./node_modules/.bin/vitest run \
  src/__tests__/components/episode/narrative-workbench \
  src/__tests__/components/assets/character-state-versions.test.tsx
./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
```
