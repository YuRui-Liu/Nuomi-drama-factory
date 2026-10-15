# 画布视频处理

选中已有视频节点，打开“延长 / 模型增强 / 片段重拍”。结果作为候选节点写入提交时的画布，原片保留；来源变化或节点删除后，产物仍可从任务中心找回。

## 配置

在处理面板的“配置处理模型”中填写实际供应商模型 ID 和能力。网关地址及密钥沿用现有 NewAPI 模型设置，不在项目配置中复制密钥。模型配置保存到项目的 `freezone/video-processing.json`；每次请求冻结其非敏感配置，队列执行不会受后续配置修改影响。

- 延长：模型必须支持 `video_extend`。按供应商契约选择返回整段或仅续段、接收新增时长或总时长。界面始终填写新增秒数，自动继承原画幅。
- 模型超分：模型必须支持 `video_upscale`，配置允许的目标分辨率。
- 智能插帧：模型必须支持 `video_frame_rate`，配置允许的目标帧率。超分、插帧使用独立阶段 ID。
- 重拍：模型必须支持 `video_edit`。向模型提交 `[start, end)` 的选区片段，输出作为候选；采纳后拼回整片并保留原音轨。此模式不承诺跨切点视觉无缝，需预览后采纳。当前不传额外角色图片或选区外上下文给供应商。

无模型配置、未启用或缺少网关凭据时，前后端均拒绝模型操作。配置声明不是供应商验证结果；当前实现未进行付费供应商实测。

## HTTP 契约与恢复

适配器沿用 NewAPI 传输：`POST /video/generations`，`GET /videos/{id}`，成功后下载返回视频。请求使用数字 `duration` 与 `metadata.reference_videos`。延长/重拍分别设置 `omni_reference_task_type=extend/edit`；超分与插帧由专用模型 ID 区分，分别传 `resolution_tier` 与 `target_fps/smart_interpolation`，不把普通模型生成为视频处理。

请求 ID、阶段 ID 与供应商任务 ID 分别持久化。已完成阶段复用结果；已获供应商任务 ID 的中断会继续轮询。POST 已开始但未取得任务 ID 属于提交状态未知，禁止自动重复 POST，需要按显示的操作标识向供应商核查。不会假设所有 NewAPI 网关都兑现幂等 Header。

结果通过 ffprobe 检查实际时长、画幅、目标分辨率和帧率。重拍时长不匹配会拒绝采纳，不自动拉伸。延长仅在供应商返回续段时拼接；拼接统一画幅、时间基和音轨。新音视频分离任务输出 MP3，历史 M4A 保持可读。

契约依据：DramaClaw [PR #662](https://github.com/dramaclaw/dramaclaw/pull/662)、[PR #716](https://github.com/dramaclaw/dramaclaw/pull/716) 的公开请求与测试；本地适配复用现有传输，没有合并上游实现。

## 本地验证

`pytest tests/test_video_processing_contract.py tests/test_video_processing_newapi.py tests/test_video_processing_persistence.py tests/test_freezone_audio_separate_runner.py`

覆盖真实 FFmpeg 片头/片中/片尾采纳、续段拼接、原音轨保留、模型参数拒绝、MP3/旧 M4A、阶段失败恢复、提交未知保护、HTTP 请求/轮询/下载、请求重放和画布持久投影。HTTP 使用确定性替身，不产生付费调用。
