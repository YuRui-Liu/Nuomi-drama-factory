# 前端界面切换性能优化设计

## 目标

在不修改 API、生成工作流、项目数据与现有媒体缓存协议的前提下，降低首次进入登录页、创作画布和 3D 导演功能时的主线程阻塞与资源竞争，让常用界面切换更快出现可交互内容。

## 已确认范围

本轮采用稳妥优化方案，仅包含：

1. 登录页保留同步路由守卫，将 `LoginCinematicPage` 改为路由级懒加载，避免已登录工作台下载 GSAP 与整套登录动画。
2. `PikoInspirationStation` 仅在用户首次打开入口时动态加载；伙伴反馈与版本提醒保持同步加载，避免改变任务反馈与升级提醒行为。
3. 增加统一的 3D Director 懒加载边界，现有调用方保持相同 props；只有弹窗实际打开时才加载重型 3D 实现。
4. 素材网格首屏 eager 图片上限从 20 降为 8；无封面视频在列表中不再批量预加载 metadata，继续显示明确的视频占位状态。
5. TanStack Router 保持 `defaultPreload: "intent"`，将 `defaultPreloadStaleTime` 从 0 调整为 30 秒，使 hover/focus 预加载能被随后的点击复用。

## 架构边界

### 登录页

认证与区域选择守卫继续位于同步 `login.tsx`，视觉页面迁入对应 lazy route 模块。重定向决策不依赖懒组件，避免登录权限行为变化。

### Piko

应用外壳只保留开关状态和轻量 Suspense 边界。关闭时不创建动态 import；打开时显示轻量占位并加载游戏模块。关闭、音频与入口行为保持不变。

### 3D Director

新增专用 lazy wrapper，导出与现有 `ThreeDDirectorDialog` 一致的 props 接口。wrapper 在 `open=false` 时返回空，不触发重型模块下载；`open=true` 时通过 `React.lazy` 加载真实实现，并在 Suspense 中显示轻量加载状态。调用方只替换导入路径，不修改业务状态。

### 素材列表

图片只对最先渲染的 8 项使用 eager，其余使用浏览器 lazy loading。无服务端封面的视频卡片不挂载带真实 `src` 的 metadata video，避免切页瞬间并发 Range 请求与解码；卡片仍显示视频类型占位，可正常点击或拖拽。

## 错误与降级

- 动态 chunk 加载继续使用现有全局 chunk-load recovery。
- Suspense fallback 只占目标组件区域，不遮挡整个工作台。
- 懒模块加载失败不改变现有错误恢复策略。
- 减少媒体预加载不影响视频在详情区或播放器中的正常加载。

## 测试与验收

新增或更新性能契约测试，先红后绿，锁定：

- 登录视觉页面不再由同步登录路由静态导入。
- Piko 游戏通过动态 import 加载。
- 3D Director wrapper 在关闭时不加载实现，打开时才加载。
- Router 预加载有效期为 30 秒。
- 素材网格 eager 图片最多 8 项。
- 列表无封面视频不使用 `preload="metadata"`。

验收要求：

- 聚焦测试全部通过。
- 现有登录、导航、Freezone 与 3D Director 相关测试无新增失败。
- 构建产物中登录动画、Piko 游戏和 3D Director 不再属于工作台主加载链。
- 不提交或覆盖工作区中其他未提交改动。

## 非目标

- 本轮不实施完整列表虚拟化。
- 不修改后端 Cache-Control、ETag 或 OSS 签名策略。
- 不改媒体文件格式、缩略图生成流程或数据库结构。
- 不引入页面 keep-alive，也不改变任务中心与查询缓存语义。
