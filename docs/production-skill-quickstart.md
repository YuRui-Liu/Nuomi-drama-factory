# 漫剧批量生产：CLI + Skill 使用速查

适用于本机 CE。Skill 负责理解制作要求、准备素材和处理异常，CLI 负责批量执行、等待任务、续跑和输出成片地址。

当前 `batch` 的起点是**已导入剧本和可用资产**，不是从一句创意直接无人值守完成全部制作。前置导入和资产准备由代理结合项目 API 完成。

## 快速查找

- [启动服务](#1-启动服务)
- [直接调用 Skill](#2-直接调用-skill无需安装)
- [安装后用名称调用](#3-可选安装后用名称调用)
- [直接运行 CLI](#4-直接运行-cli)
- [导入和资产规则](#5-导入和资产规则)
- [续跑与异常处理](#6-续跑与异常处理)
- [预算与验收边界](#7-预算与验收边界)

## 1. 启动服务

后端必须运行；仅使用 CLI 时不需要启动前端。服务已启动时，不要重复启动。

终端 1：后端 API，使用当前机器实际虚拟环境和数据目录。

```bash
cd /Users/liuyuxiang05/Liu/Nuomi-drama-factory
ST_EDITION=ce \
NOVELVIDEO_DATA_ROOT=/Users/liuyuxiang05/Liu/nuomi-drama-data \
NOVELVIDEO_STATE_DIR=/Users/liuyuxiang05/Liu/nuomi-drama-data/state \
/Users/liuyuxiang05/Liu/.venv/bin/novelvideo api --host 127.0.0.1 --port 8780
```

终端 2：前端，可选，用于设置和查看产物。

```bash
cd /Users/liuyuxiang05/Liu/Nuomi-drama-factory/frontend
pnpm dev --host 127.0.0.1
```

CE 本地默认无需登录，CLI 复用软件「设置」中的供应商配置，不需要把密钥放进命令或聊天。Codex 运行时的登录状态与 CE 登录是两回事；使用 Codex 文本或视觉任务路由时，该运行时仍需可用。

## 2. 直接调用 Skill（无需安装）

在以本仓库为工作目录的 Codex 会话中发送以下内容。将项目、集数和预算替换为本批实际要求；示例并不表示相关剧集已存在。

```text
请读取并执行 skills/nuomi-production/SKILL.md。

项目 ID：填写实际项目 ID
制作范围：E1–E5
交付目标：逐集完整视频、下载地址和失败原因汇总

本机 Python：/Users/liuyuxiang05/Liu/.venv/bin/python
API：http://127.0.0.1:8780/api/v1
使用软件设置中的供应商配置，默认不生成草图。
剧本已导入；场景和道具从外部库导入并复用，场景允许必要变体。
机器检查通过后自动继续，不逐张要求人审，也不绕过硬性质量检查。
优先复用已有产物，接续正在运行的任务，不重复购买生成。
本批付费授权：累计人民币 50 元以内；无法确认剩余预算时先暂停付费调用。
遇到缺失素材、无法自动解决的质量问题或预算风险，集中报告。
```

Skill 文件：[nuomi-production/SKILL.md](../skills/nuomi-production/SKILL.md)。它是供代理读取的操作说明，不是独立后台服务，也不是 shell 命令。

## 3. 可选：安装后用名称调用

在仓库根目录执行一次，将仓库内 Skill 链接到 Codex 的项目技能目录：

```bash
cd /Users/liuyuxiang05/Liu/Nuomi-drama-factory
mkdir -p .agents/skills
ln -s ../../skills/nuomi-production .agents/skills/nuomi-production
```

如果目标已存在，先检查其指向，不要强制覆盖。安装只影响技能发现，不会启动生产任务。

随后在 Codex CLI / IDE 中用 `/skills` 选择，或在消息中显式调用：

```text
$nuomi-production
为项目 <项目 ID> 制作 E1–E5，沿用本批已确认的素材规则和费用授权。
使用 /Users/liuyuxiang05/Liu/.venv/bin/python 执行 CLI。
```

`$nuomi-production` 是发给 Codex 的消息，不是在终端执行。未发现技能时，重新开启会话或重启客户端。目录发现、符号链接及显式调用规则参考 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)。

## 4. 直接运行 CLI

在新的终端中设置项目。**这里必须是项目 ID，不是显示名称。** 可从项目页面 URL 或 API 响应获取。

```bash
cd /Users/liuyuxiang05/Liu/Nuomi-drama-factory
export NUOMI_PROJECT="替换为实际项目ID"
export NUOMI_API_URL="http://127.0.0.1:8780/api/v1"

# 当前终端的便捷命令；新终端需重新定义
alias nuomi-local='/Users/liuyuxiang05/Liu/.venv/bin/python -m novelvideo.production_cli'
```

先做不联网预览：

```bash
nuomi-local --dry-run batch --episodes 1-5 --through compose
```

预览只列出集数和阶段，**不检查素材、不估算费用、不生成媒体**。确认项目、素材和付费授权后再执行：

```bash
nuomi-local batch --episodes 1-5 --through compose --task-timeout 1800
```

| 要做什么 | 命令 |
| --- | --- |
| 查看任务、任务 ID 和结果 | `nuomi-local status` |
| 查看 E1 分组及版本 | `nuomi-local groups --episode 1` |
| 等待一个已有任务 | `nuomi-local wait TASK_ID` |
| 制作连续集和离散集 | `nuomi-local batch --episodes 1-5,8` |
| 只做到实图 | `nuomi-local batch --episodes 1-5 --through render` |
| 只做到视频片段 | `nuomi-local batch --episodes 1-5 --through video` |
| 显式生成草图 | `nuomi-local batch --episodes 1 --with-sketch` |
| 显式指定竖屏 | `nuomi-local batch --episodes 1 --aspect-ratio 9:16` |
| 限制 API 写入尝试次数 | `nuomi-local batch --episodes 1-5 --max-submissions 30` |
| 用现有片段提交 E1 合成 | `nuomi-local compose --episode 1` |
| 查看完整参数 | `nuomi-local batch --help` |

`batch` 默认做到 `compose`，跳过草图，画幅跟随项目设置。单步 `compose` 是提交任务；拿到任务 ID 后仍需 `wait` 检查完成结果。

灯塔验收项目的实际 ID 为 `01M2HPJFW5HGXYC8F9C94ZKWZS`，显示名称为 `acceptance_lighthouse_20260915`。安全预览示例：

```bash
nuomi-local --project 01M2HPJFW5HGXYC8F9C94ZKWZS --dry-run batch --episodes 1
```

## 5. 导入和资产规则

制作前需要完成剧本导入、角色资产准备，以及外部场景和道具关联；`batch` 不会自动包办这些前置步骤。

```bash
# 只预览剧本导入，不提交覆盖
nuomi-local import-preview /绝对路径/剧本.txt

# 查看已有角色
nuomi-local request characters
```

导入提交需根据预览返回的 `preview_id`、`base_revision` 和文件冲突信息构造请求，按 Skill 中的导入流程执行，不直接覆盖已有集。`request` 的路径相对当前项目，不带开头 `/`。

- 场景从外部场景表导入，复用基础场景，允许必要变体，不为每个镜头临时扩库。
- 道具从外部导入，规划负责将已有道具关联到分镜；缺少必需道具时报告，不自动创建。
- 角色身份图允许无血腥、平整的无头正面颈部截面，面部细节由脸部特写组合；仍需检查多余人脸、明显伤口和参考可用性。
- 身份图质检通道故障时优先恢复通道并重检原图，不因此反复生成新图。

具体重检命令见 [CLI 文档：身份图原图重检](production-cli.md#身份图原图重检)。

## 6. 续跑与异常处理

中断后先 `status` 查看任务，再对同一范围运行 `batch`。完成且未过期的媒体会复用，排队或运行中的任务会接续；最终合成可能重新执行，不代表重新购买视频。

| 情况 | 处理方式 |
| --- | --- |
| CLI 等待超时 | 服务端任务未必停止；先查状态并等待，不立即重复生成 |
| `submission_unknown` | 提交结果未知；先查任务，避免重复付费 |
| `missing_required_assets` | 补齐或关联已有资产，不绕过必需引用 |
| `qc_unavailable` | 修复视觉通道，重检已有图，不直接重画 |
| `plan_invalid` / 版本冲突 | 查校验报告，按当前剧本和版本修复，不强行激活旧方案 |
| 已有视频但后处理失败 | 优先恢复已有产物或单独合成，不盲目重试视频生成 |
| 确认需要重试失败生成 | 修复原因、核对费用后，才加 `--retry-failed` |

每个项目同时只运行一个批量协调进程；当前没有跨客户端的完整幂等锁。

`batch` 输出 JSONL，最后一行是汇总。检查 `ok`、各集状态、错误及 `video_url`。退出码 `0` 表示所选阶段成功，`1` 表示存在失败或超时，参数错误通常为 `2`。提交成功不等于成片完成，做到 `render` 成功也不等于完成视频。

## 7. 预算与验收边界

- `--max-submissions` 限制的是 API 写入尝试次数，包含规划与合成，**不是人民币预算硬上限**，也不等于供应商计费次数。
- 平台积分不能直接视为人民币。每批明确授权；若无法可靠估算剩余费用，应先暂停付费调用，不能宣称 CLI 能强制守住 50 元。
- 常规通过项自动继续，缺素材、硬性质量失败、授权范围变化等异常集中处理；减少人审不等于取消质量门禁。
- 通用视频后处理免重购恢复、原生音轨与外部 TTS 的自动选择，以及 ASR / 口型质量验收仍有边界，不能把现有 Skill 视为全流程无人值守生产保证。
- 灯塔 E1 已有成片，但验收包含恢复操作，不代表任意剧集已通过完全无人值守验收。详见 [真机验收记录](audits/2026-09-15-live-acceptance.md)。

完整参数和协议补充：[批量漫剧 CLI](production-cli.md)。
