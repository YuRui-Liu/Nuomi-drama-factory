# 合成与导出

## 功能概览

Compose 页解析当前可用视频来源，重建音轨并通过 FFmpeg 生成固定成片；用户还可以独立导出 SRT、视频文件或 ZIP 素材包。

## 适合谁看

适合导演、制片、测试和研发确认合成门禁、来源回退、旧成片保留和导出内容。

## 用户操作

1. 在 Compose 页检查 Beat 状态和可合成提示。
2. 选择分辨率并启动合成；可填写字幕或 BGM 选项。
3. 任务完成后播放或下载固定成片。
4. 按需导出 SRT 或 ZIP 素材包。

## 业务流程

```mermaid
flowchart LR
    A[检查可用 Beat/视频] --> B[启动 compose_episode]
    B --> C[解析 Director 或逐 Beat 来源]
    C --> D[FFmpeg 拼接并重建音轨]
    D --> E{候选文件有效?}
    E -->|是| F[原子替换固定成片]
    E -->|否/失败/取消| G[删除候选并保留旧成片]
    F --> H[播放或导出视频/SRT/ZIP]
```

1. 用户检查页面门禁并提交合成选项。
2. Runner 优先使用已完成 Director span，再用逐 Beat MP4 补空位。
3. FFmpeg 写隐藏候选，成功且非空后原子替换固定文件。
4. 播放和导出读取固定文件；SRT、ZIP 为独立路径。

## 业务规则

- 页面门禁、`pipeline/status` 和 Runner 校验是三套不同判断，不能互相替代。
- Runner 只要求至少一个来源片段成功；缺失或单片失败可能生成部分成片。
- 非法分辨率当前回退 `720x1280`；页面提供竖屏和横屏合法值。
- 字幕和 BGM 参数当前不会烧入或混入成片，只在请求结果中回显相关信息。
- `GET /final` 只检查固定文件存在，不验证 MP4 可解码性或新鲜度。
- ZIP 只收集当前 Beats、manifest 解析到的媒体和生成的 SRT，不是历史快照。

## 状态与异常

合成任务按 `compose_episode` 进入 queued/running/completed/failed/cancelled。来源为空会失败；单来源 FFmpeg 错误可被记录并继续。候选失败、超时或取消时旧成片保持可播放。Director 的 `external_tts` 缺 ambience stem 会使来源解析失败。

## 产品验收

- 有至少一个可用来源时能生成并播放固定成片。
- 合成失败或取消后旧成片仍可访问，临时候选不被误当成成片。
- SRT 时间轴按音频或 manifest 时序生成；无可用时长按现有回退规则处理。
- 视频、SRT、ZIP 下载接口分别返回正确的成功或 404/失败提示。

## 技术实现

来源解析、音轨回退、原子发布和导出路径见[合成与导出管线](../pipelines/08-compose-export.md)；FFmpeg 子进程、任务取消和项目目录见[共享系统地图](../system-map.md)。

## 相关创作专题

- [音频与视频](05-audio-and-video.md)
- [设置与任务](07-settings-and-tasks.md)
- [合成与导出管线](../pipelines/08-compose-export.md)
