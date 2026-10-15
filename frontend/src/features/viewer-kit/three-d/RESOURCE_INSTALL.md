# 预演动作资源

动作模型由 Quaternius 制作，Standard 版本为 CC0 1.0。各目录保留原作者 `License.txt`。不要使用付费 Source 包替代。

运行 `python3 scripts/install_previsualization_assets.py`，安装到已 gitignore 的 `frontend/public/viewer-kit/quaternius/`。脚本验证 SHA-256，失败不覆盖已有资源。没有资源时导演台使用明确标记的静态替身，物体路径及机位仍可预演；骨骼动作需要完整资源。

| 文件 | 来源 | SHA-256 |
| --- | --- | --- |
| `ual1/UAL1_Standard.glb` | [公开 CC0 镜像](https://github.com/richardanaya/metaverse-avatar/tree/master/anims)，[作者及许可](https://quaternius.itch.io/universal-animation-library) | `d867292451e432b735e2a910c2db6640fbea97b205d85a2e8ffed26da87972cf` |
| `ual2/UAL2_Standard.glb` | [作者在 OpenGameArt 发布的 CC0 Standard 包](https://opengameart.org/content/universal-animation-library-2) | `0815dd05531cae9bc313fc9c0ba81330bc72f8e19ec45f73738e74ddc5796a43` |

UAL1 镜像仓库的其他角色模型采用不同许可；安装脚本仅下载以上单个 CC0 动作文件。UAL2 仅提取 `Unreal-Godot/UAL2_Standard.glb`，不提取其它内容。SHA 固定文件内容；远端升级后校验失败需人工审核来源和许可后更新哈希。

## 2026-10-11 本机验收

使用独立 Chromium + SwiftShader、真实 PlayCanvas 与上述两份 GLB。默认 gstack macOS headless 禁用 GPU，不能用于此项 WebGL 验收。

- 轨迹从 x=-1 到 x=1、时长 2 秒，t=0.5 的实际导出位置为 `[-0.5, 0, 0]`。
- 比较 11 个腿、手臂及脊柱骨骼的局部四元数：t=0.1 与 t=0.35 不同，回跳 t=0.1 与首次完全一致，排除物体整体平移造成的假阳性。
- MediaRecorder 在软件 GL 下曾把 2 秒录成 1.822 秒；现已探测成片时长，超过一帧误差自动重新逐帧编码。
- 最终文件 ffprobe：H.264、1280×900、30/1 fps、60 帧、2.000000 秒；浏览器 `video.play()` 成功。
- 本机证据：`/private/tmp/nuomi-previz-browser.mp4`、`/private/tmp/nuomi-previz-browser.png`。它们是临时验收输出，不随 Git 发布。
- 未在此验收中调用收费模型；未把浏览器引擎 harness 当作完整画布上传流程实测。画布回写路径沿用 generic video upload、referenceOnly 视频节点及带快照的来源连线。

录制时禁用机位输入和 gizmo，并遮挡编辑入口；关闭或切换场景导致时间轴面板卸载时会 abort，停止媒体轨道或终止独立 FFmpeg worker。浏览器隐藏会终止实时录制并提示重试。逐帧编码使用浏览器内 FFmpeg.wasm，长场景需要较多时间和内存。
