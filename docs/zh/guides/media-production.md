# 媒体生产中心

媒体生产中心把 GRSAI 出图、RunningHub 视频与 TTS 组织成可预览、可恢复的 Production DAG。默认仍走旧管线，必须按项目显式开启生产管线。

## 本地启动

在项目虚拟环境中启动后端：

```powershell
.\.venv\Scripts\novelvideo.exe api --port 8780
pnpm --dir frontend dev
```

前端默认在 `http://localhost:5173`，并把 `/api/v1` 代理到后端。供应商密钥通过设置页或凭据存储配置，不写入工作流 JSON、日志或项目配置。

## 供应商与工作流

- GRSAI：单图、多宫格、超分和切割。切割后的子图与原始宫格图分别登记，便于复用和追踪。
- RunningHub MiniMax H3 导演台（`runninghub:minimax-h3`）仍为默认模型：使用工作流 `2089723723468328961`，不使用全局 Ref，只绑定 `12.timeline_data`，从节点 `7` 下载成片；一次任务可生成多镜。仅首帧为 i2v，首尾帧为 fl2v，不开放仅尾帧。
- MiniMax H3 Ref 导演台（`runninghub:minimax-h3-ref`）是显式选择的独立模型，使用工作流 `2096502793044582401`。它把叙事组内有序的角色、场景、关键道具或临时上传 Ref 与每个视频单元的首帧、可选尾帧一起提交。全局 Ref 默认上限为 5，可在设置中调整为 1–10；首尾帧不计入该上限。
- RunningHub TTS：支持 Qwen3 音色设计和 IndexTTS2 音色克隆、情绪变体与批量对白合并。

导入 RunningHub API JSON 后，先建立 profile，明确 workflow ID、版本、能力和允许绑定的节点字段。不要允许任意节点或任意字段透传。

## 并行与生产运行

可把 RunningHub 供应商并发设为 `5`。并发限制由共享协调器统一管理，视频和 TTS 可同时提交；轮询不占提交槽，等待中的任务不会串行阻塞其他可运行分支。

生产中心操作顺序：

1. 选择 `strict`、`balanced` 或 `auto` 审核策略并生成 preview。
2. 检查 create、reuse、invalidate、skip、预计成本和缺失资产。
3. 使用 preview 返回的 `snapshot_token` 确认启动。配置变化后旧 token 失效。
4. 创建接口立即返回 HTTP 202 和 `run_id`，后台继续执行。
5. 可暂停、恢复、取消运行；仅失败、质检失败或已取消节点可重试。

`auto` 仍会阻止 `quality_failed` 和高风险资产进入下游合成。

## 功能开关与迁移

项目配置默认值：

```json
{
  "image_pipeline": "legacy",
  "video_pipeline": "legacy",
  "tts_pipeline": "legacy",
  "production_scheduler": false
}
```

切换时先只开启一种生产管线，再开启调度器。旧资产迁移必须先 dry-run，核对“将登记、跳过、冲突”清单后再 apply。迁移只登记 SHA-256 和关联信息，不移动或删除源文件；未知 provider/workflow 保持 `null`。

回滚时暂停新运行，将四个开关恢复为上述默认值，再处理已在远端提交的任务；不要删除 Production 数据库或旧资产。

## 产物与烟测

真实产物路径以运行详情中 artifact 的 `local_path` 为准；内容寻址文件保存在配置的媒体 artifact 根目录，图像管线的阶段文件保存在该批次 `output_dir`。测试使用 MockTransport，不会生成真实视频。因此只有显式启用真实烟测、配置有效凭据并实际提交 RunningHub 后，才会出现真实 MP4。

真实烟测建议只提交一个低成本镜头，确认远端 task ID、下载文件、SHA-256、质检结果和重启恢复后，再扩大到并发 5。

H3 烟测脚本 `scripts/smoke_runninghub_h3.py` 只提交一个原导演台 timeline，并输出 task ID。提示词使用中文，含对白时必须为可辨识台词以支持对口型。H3 原生环境声/音效会保留；对白默认使用 `external_tts`，可逐 span 改为 `h3_native`，该改动只重新合成。

H3 Ref 必须至少选择 1 张全局 Ref 并提供首帧，FL2V 还必须提供尾帧。系统在上传前冻结所选顺序、Subject 描述、素材字节与 Ref revision；快照只保留摘要和元数据，不保留本地路径或图片内容。素材缺失、被修改、无法解码、超限或逃逸项目目录都会阻止提交。失败时不会切回 `runninghub:minimax-h3`，也不会删除 Ref、删除尾帧或截断列表后重试。

Ref 兼容性烟测是人工发布关卡，不属于自动测试。它只尝试一次可能产生费用的 RunningHub 提交；操作者必须明确提供绝对路径和分辨率，并在确认费用后设置 `RUNNINGHUB_REAL_SMOKE=1`：

```bash
RUNNINGHUB_REAL_SMOKE=1 uv run python scripts/smoke_runninghub_h3_ref.py \
  --reference /absolute/path/to/reference.png \
  --first-frame /absolute/path/to/first.png \
  --last-frame /absolute/path/to/last.png \
  --resolution 720p \
  --output /absolute/path/to/h3-ref-smoke.mp4
```

命令输出工作流 `2096502793044582401`、输入摘要、远端 task ID、最终状态和本地结果路径。CI 与普通本地验证不得设置该开关；远端拒绝时只报告一次失败，不降级、不重试。
