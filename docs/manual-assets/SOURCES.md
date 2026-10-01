# 产品手册图文来源

整理日期：2026-09-26。图片按原文件复制，没有修图、拼接或重新生成；复制前已逐张查看。

| 文件 | 来源 | 用法与边界 |
| --- | --- | --- |
| 01-workbench.png | `/private/tmp/nuomi-storyboard-workbench.png` | 真实历史界面截图；灯塔示例项目，不是山海拾遗 |
| 02-character-identity.png | `nuomi-drama-data/output/local/shanhai_shiyi/assets/characters/步知遥/identities/E1雨夜常服.png` | 既有身份素材；不宣称新版选角流程已验证 |
| 03-scene-panorama.png | `nuomi-drama-data/output/local/shanhai_shiyi/director_worlds/水镇堤道与高仓/v1/pano_360.png` | 既有全景素材；不是三维模型截图 |
| 04-storyboard-grid.png | `nuomi-drama-data/output/local/shanhai_shiyi/grids/ep001/narrative_groups/group-7417225a59f14131c45a_batch-e3b0c44298fc1c149afb_render_r3_9263_20260925085354147966.png` | 既有分镜产物；顺序讲解不代替生产记录 |
| 05-video-contact.png | `/private/tmp/e1_h3_ref_contact.png` | 既有视频抽帧对照；不声称与图 4 为同一任务 |

`nuomi-drama-data` 位于仓库上一级。手册图片已复制至本文所在目录，阅读时不依赖数据目录或临时目录。

## 功能依据

- 产品与环境：`README.md`。
- 选角：`docs/operations/story-grounded-character-casting.md`。
- 资产：`docs/cookbook/product/03-production-assets.md`。
- 剧本、声音与视频：`docs/cookbook/product/04-screenplay-and-storyboard.md`、`05-audio-and-video.md`。
- H3 与 H3 Ref：`docs/zh/guides/media-production.md`。
- 画布节点：`frontend/src/features/canvas/domain/canvasNodes.ts`。
- 创作能力：`frontend/src/features/freezone/capabilities/capabilityRegistry.ts`、`candidate_capabilities.ts`。
- 画布提交：`frontend/src/features/freezone/commit/CommitDialog.tsx`。
- 合成与导出：`docs/cookbook/product/06-compose-and-export.md`、`src/novelvideo/task_backend/runners/video.py`。
- 成本：`docs/operations/project-cost-monitoring.md`。

## 外部文档状态

参考文档：`https://neo-flying.feishu.cn/docx/T2UgdVA4Fo1A5KxCh0vckDz3nTg`。

本次浏览器读取超时，直接网络请求返回飞书登录页面，未获取正文与图文目录。没有借用该文档内容，也没有声称其结构已经被核对。

目标文档：`https://my.feishu.cn/docx/O4z0d5gd1oaHVCx6CzCcFdmGnSp`。本次交付为本地 Markdown 和图片，尚未同步。
