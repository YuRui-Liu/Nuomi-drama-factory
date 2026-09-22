# 灯塔 E1 r7 真机验收

## 结论

两段新视频已生成、从供应商恢复下载，并合成完整验收预览。媒体可解码和播放，但两段及切点视觉审查均未通过，不能发布为生产合格成片。未改写失败报告，未覆盖原 `ep001_final.mp4`，未将组状态强改为成功。

预览文件：`/Users/liuyuxiang05/Liu/nuomi-drama-data/output/local/acceptance_lighthouse_20260915/videos/episodes/ep001_r7_acceptance_preview.mp4`。

本机播放地址：http://127.0.0.1:8780/api/v1/projects/01M2HPJFW5HGXYC8F9C94ZKWZS/media/videos/episodes/ep001_r7_acceptance_preview.mp4

## 生成证据

- 项目：`01M2HPJFW5HGXYC8F9C94ZKWZS`，灯塔 E1，group-1，r7；未修改 Bei。
- 应用任务：`d4d6a221-7c24-4b68-8600-0120e7253b60`。
- 两个供应商任务：`2102402253485989890`、`2102405226404466690`；各提交一次，下载失败后没有重新生成。
- 输入来源：79143，选择 ID `fdf1ed6133ffd44568a3161fb4be900bbd5462a21f0c9bdd37d7bb33f623e72c`。
- 两段实际 wire 与完整合同预检结果一致，profile=15、compiler=4。
- 正式预检：`/private/tmp/nuomi-e1-visual-acceptance-ieyvs6qp/result.json`。此前 `vwiwlnfp` 诊断未带完整合同，不作为正式验收输入。
- 旧片备份：项目内 `acceptance_archive/before_r7_20260922T140745Z/`。

## 下载问题与修复

供应商生成成功，但返回精确主机 `rh-comfyui01.tos-cn-beijing.volces.com` 不在原下载白名单，应用记录为 `DOWNLOAD_FAILED` / `transport_failed`。只把这一主机加入 `runtime/configuration.py`，没有放行共享域名后缀或任意存储桶。HTTPS、禁止重定向、大小限制和下载不携带 API 密钥的行为保持不变。

新增回归先复现失败，再完成最小修复。RunningHub 客户端及配置测试 64 项通过，Ruff 和 `git diff --check` 通过。两段原结果另存 `.recovered.mp4`；原失败 manifest 与任务状态保留。

首段 SHA256：`87d32eca66a4e83866c711d1f44faf8f8942494c89ac20ef10294981a909aa5e`。

第二段 SHA256：`dc7a8cfe7e82fc01aa4cac883eb3157384cff51d547cfeab9c8729a84e133646`。

## 媒体与视觉结果

两段均为 736×1280、24 fps、H.264 + AAC，视频时长分别约 6.583、4.458 秒。预览统一等比缩放加留边为 720×1280、24 fps、11.042 秒，保留原生音轨；没有通过拉伸、补帧或改切点隐藏问题，也没有添加独立字幕或 BGM。

FFmpeg 完整解码成功；无头浏览器实际播放至 ended=true，error=null。音轨存在及可解码不等于对白内容、口型或音质已验收，本轮没有完成听觉语义验收。

视觉证据：`/private/tmp/nuomi-r7-review-evidence/result.json`，使用 `cinematography-visual-review-v2`。首段、第二段及六帧上下文切点审查均为 failed：

1. 首段静止背景灯塔的尺度、横向位置和裁切范围变化，与固定机位要求不符。
2. 第二段由下肢裁切的较近构图变为双脚和更多环境入画，未保持指定镜内构图。
3. 切点审查同样发现上述镜内摄影变化；不能据此宣布台阶世界位置连续性已经解决。

抽帧图：`/private/tmp/nuomi-r7-segment1.png`、`/private/tmp/nuomi-r7-segment2.png`；播放截图：`/private/tmp/nuomi-r7-playback.png`。

## 尚存限制

多初态全局标签已消除，但新实际提示词仍有跨字段动作及身份同义重复。仅增加字段归属规则和精确去重不足以保证最终文本简洁。此次失败也不能单独证明提示词长度是机位漂移的因果原因，需要冻结输入后做受控对照，不能靠放宽质检得出成功结论。

任务原生状态仍为失败；恢复下载和预览合成是本次验收操作，不能称为已实现通用免重购恢复入口。原有 50 元授权金额与平台积分仍未完成账单换算，本轮仅能核实两个供应商任务、无重购，不能宣称已核实人民币总消耗。
