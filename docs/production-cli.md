# 批量漫剧 CLI

本机启动、Skill 调用和常用命令速查：[CLI + Skill 快速使用指南](production-skill-quickstart.md)。

从已导入剧本和项目素材连续制作多集视频。默认自动复用媒体、选择已规划引用、跳过草图；机器校验通过的导演方案自动激活。

覆盖导入新剧本后，过期导演方案不会被复用；需要新方案时，批处理先解析并激活当前剧本语义，再生成并激活新导演方案。历史版本保留供查看。源版本冲突返回 409，不能通过强行激活旧版本绕过。

## 运行

使用现有项目虚拟环境无需重新安装：

```bash
.venv/bin/python -m novelvideo.production_cli --project PROJECT_ID batch --episodes 1-10
```

安装或更新项目后，等价命令为 `nuomi --project PROJECT_ID batch --episodes 1-10`；也支持 `novelvideo production`。独立入口不导入 Cognee。

通过环境配置 `NUOMI_API_URL`（默认 `http://127.0.0.1:8780/api/v1`）、`NUOMI_PROJECT`、`NUOMI_TOKEN`（短期 agent session）或 `NUOMI_SESSION`。使用 API 服务端已配置的模型和项目权限，不在本地绕过数据库或门禁。

CE 本机默认无需登录，也无需设置 `NUOMI_TOKEN` 或 `NUOMI_SESSION`。CLI 无凭据时不发送认证头或 cookie，由服务端判断访问权限；启用认证的服务仍会拒绝未认证请求。供应商密钥继续由服务端设置管理，不需要传给 CLI。

| 参数 | 行为 |
| --- | --- |
| `--episodes 1-5,8` | 多集范围，去重、按输入顺序执行 |
| `--through render/video/compose` | 执行到指定阶段，默认 compose |
| `--with-sketch` | 额外生成草图，默认关闭 |
| `--max-submissions 30` | 本次 API 写入尝试上限，包括规划与合成；不是费用报价 |
| `--retry-failed` | 每个已有失败阶段在本批最多重试一次 |
| `--aspect-ratio 16:9` | 显式覆盖画幅；缺省按项目横竖方向生成 16:9 或 9:16 |
| `--task-timeout 900` | 单任务等待上限，默认 900 秒；超时不取消服务端任务 |

全局 `--dry-run` 放在子命令前。批量 dry-run 不联网，输出集号与阶段，不估算模型费用或验证引用。

## 断点与交付

### 显式分镜来源与看图策略

以下操作不生成图片或视频。先查组状态和来源列表，再携带查到的完整 ID 选择来源；不要按 revision 猜版本，也不要覆盖公共 `beat_XX.png`。

```bash
nuomi --project PROJECT_ID groups --episode 1
nuomi --project PROJECT_ID request episodes/1/narrative-groups/GROUP_ID/storyboard-sources
nuomi --project PROJECT_ID request episodes/1/narrative-groups/GROUP_ID/storyboard-sources/selection \
  --method PUT --json '{"source_id":"TARGET_SOURCE_ID","expected_selected_id":"CURRENT_SELECTION_ID"}'
nuomi --project PROJECT_ID request episodes/1/narrative-groups/GROUP_ID/storyboard-contract \
  --method PUT --json '{"version":1,"expected_version":0,"expected_selected_id":"CURRENT_SELECTION_ID"}'
```

`source_id` 与选中 ID 为完整的 64 位十六进制字符串；没有选择时 `expected_selected_id` 为 `""`。启用成功后 `expected_version` 为 1，不提供通过降级到 0 绕过视觉校验的入口。组查询返回实际 `storyboard_contract_version`，不要盲目重复启用。

启用时拒绝活动阶段、过期预期版本/选择以及损坏或不完整的已有来源。尚无来源的组可以提前启用，但后续视频仍须有完整有效来源；历史公共 frames 不会被自动认定为有来源的素材。来源选择令旧视频过期，不会自动重购。409 时先刷新并核对；422 时修正来源，不自动提交生成。

旧任务保留入队语义，历史成片不因此获得视觉验收标记。目前仅灯塔验收组实际启用；这不是对所有项目的默认设置变更。

### 批处理恢复行为

- 完成且未过期的媒体复用；排队/运行中的任务按集号、scope 和 task_id 接续。
- 缺少必需素材或硬性检查失败只阻断相应组；其他组与剧集继续。
- 自动修复仅切分失败，不重新生成已有图片。
- 提交响应丢失时不自动重复 POST，避免重复付费。
- 合成 API 暂无输入指纹。为防止拿到旧成片，每次完成批次都会从当前视频重新合成，复用已付费视频片段。
- 批量是串行协调，默认每项目一个协调进程；没有跨进程的额外幂等锁。
- 默认自动执行通过项，不逐图、逐组要求人审。缺少可用资产仍要先补齐，不能靠跳过引用来伪造完成。

批量输出 JSONL，最后一行为包含 `ok`、`submissions`、`episodes` 的汇总。单步命令输出 JSON。退出码 0 为成功，1 为 API/任务/批次失败或超时，参数错误通常为 2。任务执行完成但 `qc_passed: false` 也以失败退出。

其他命令：`import-preview`、`plan`、`groups`、`generate`、`compose`、`status`、`wait`、`request`。`request` 支持项目内 GET/POST/PUT/PATCH/DELETE 和 `--json` / `--body-file`。单步图片生成需携带 reference_resolution；视频生成需携带当前 revision 和 plan_revision。`batch` 自动读取这些数据。

项目 Skill 位于 [skills/nuomi-production/SKILL.md](../skills/nuomi-production/SKILL.md)。可直接让代理按该文件执行，或将整个文件夹安装到代理的 skills 目录。

## 身份图原图重检

`qc_unavailable` 表示质检通道不可用，不代表需要重画。身份图质检有独立任务路由 `identity_sheet_qc`，默认使用已登录的 Codex，模型与运行时可在设置的任务路由中配置。不会在失败后偷偷切换供应商。

从生产资产版本响应取得真实版本 ID 后，调用：

```bash
.venv/bin/python -m novelvideo.production_cli --project PROJECT_ID request characters/林舟/identities/林舟_青年时期/versions/VERSION_ID/recheck --method POST --json '{}'
.venv/bin/python -m novelvideo.production_cli --project PROJECT_ID wait TASK_ID
```

请求只审查已有图片。响应带 `task_id` 时等待该任务；`reused: true` 且含 `data` 时已复用通过报告，不再调用模型。同版本、图片、质检策略和路由的进行中任务也会复用。质检失败或不可用后，修正原因再用 `--json '{"retry":true}'` 显式重检；重检不会触发图像生成。检查历史记录在版本元数据 `qc_history`，包括图片摘要、路由与时间。

道具库由外部导入，规划只链接已有道具与分镜。空库规划零关联不意味着应该自动建库；不要为缺失引用自动创建道具，避免资产膨胀。
