# NuomiDrama 版本更新弹窗品牌替换设计

## 背景

版本更新弹窗的头图来自远程视频 `version-update-2026-06-22.mp4`。旧 `DramClaw` 标识已经烧录在视频像素中，因此仅修改页面文案或全局 Logo 无法消除品牌遗留；同时，首次显示还依赖 CDN 媒体加载。

## 目标

- 完全移除版本弹窗中的旧 `DramClaw` 视觉资产和媒体请求。
- 沿用现有 NuomiDrama 品牌标识与 Editorial Black 设计令牌。
- 保持弹窗尺寸、2:1 头图比例、版本内容和交互行为不变。
- 头图随 React 首屏同步渲染，不等待远程图片或视频。

## 已批准方案：内建品牌场景

在 `VersionUpdateDialog` 内使用 HTML、现有 `BrandMark` 和 Tailwind 样式绘制头图：

- 深色背景使用 `--editorial-background` 对应的 `#0D0E10`。
- 品牌强调色使用 `--brand-accent` 对应的 `#E5FF5C`。
- 中央复用 `BrandMark`，显示 NuomiDrama N 字剪辑切口与字标。
- 使用纯装饰网格、黄绿光晕和斜向剪辑线强化品牌识别。
- 装饰层全部标记为不可访问；头图整体提供 `NuomiDrama 版本更新` 的可访问名称。
- 不使用 `<video>`、远程图片或新增二进制资产。

## 边界

- 不修改版本通知 API、已读逻辑、弹窗自动打开逻辑或本地存储键。
- 不重构通用 Dialog、Button 或 BrandMark。
- 不为这次修复新增视频、图片、字体或网络依赖。
- 不修改其他页面的历史 DramClaw 技术兼容键。

## 验证

新增定向回归测试，确认：

1. 弹窗显示带有可访问名称的 NuomiDrama 内建头图。
2. 弹窗 DOM 不包含 `<video>`，从行为层阻止旧 CDN 头图回归。
3. 现有自动打开与手动打开测试继续通过。

