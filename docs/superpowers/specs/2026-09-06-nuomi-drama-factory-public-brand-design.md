# Nuomi Drama Factory 对外品牌替换设计

## 背景与目标

仓库的产品界面已经部分使用 `NuomiDrama`，但根 README、中英文使用文档、公开链接和部分用户提示仍显示 `DramaClaw`。本次改动将所有当前用户可见的旧品牌统一为 `Nuomi Drama Factory`，并将公开仓库链接指向 `YuRui-Liu/Nuomi-drama-factory`。

改名只覆盖展示层。已有环境变量、工具名、协议值、存储键和插件目录继续保留，避免破坏已有配置、自动化和数据兼容性。

## 统一命名

- 文档和完整产品名称：`Nuomi Drama Factory`
- 紧凑 UI 字标：继续使用现有 `NuomiDrama`
- GitHub 仓库：`https://github.com/YuRui-Liu/Nuomi-drama-factory`
- 本地目录示例：`Nuomi-drama-factory`
- 中文称谓：以 `Nuomi Drama Factory` 为主，不新增其他中文简称

## 替换范围

### 当前公开文档

修改以下面向用户的当前资料：

- 根目录 `README.md`
- `readme/README_zh.md`
- `docs/README.md`、`docs/en/**`、`docs/zh/**` 中的使用、功能、部署、许可和排错文档
- `CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`GOVERNANCE.md`、`SECURITY.md`、`NOTICE` 中的展示名称与公开仓库链接
- GitHub Issue 模板中的 Discussions、安全策略和 Issue 链接
- 新增的 `docs/cookbook/start-software.md`

文档中的 clone 命令统一为：

```bash
git clone https://github.com/YuRui-Liu/Nuomi-drama-factory.git
cd Nuomi-drama-factory
```

### 产品界面

扫描 `frontend/src`、`frontend/index.html` 和公开静态资源：

- 用户可见文本、标题、description、ARIA 标签和错误提示中的旧品牌改为 `NuomiDrama` 或完整产品名
- 对外显示的 GitHub、下载、社区和帮助链接切换到新仓库
- 内部值仍可使用 `dramaclaw`，但对应 UI 标签必须显示新品牌

### 图片与品牌资源

逐个检查 README 和产品界面实际展示的本地品牌图片：

- 图片内含 `DramaClaw` 字样时，优先替换为已有 NuomiDrama 品牌资源
- 没有合适替代资源时移除该图片，不能只修改 `alt` 文本掩盖旧标识
- 纯作品截图、视频封面和不含品牌字样的图片保留
- CDN URL 路径中的 `dramaclaw` 只是资源地址且用户页面不会将其渲染为文字时保留，避免造成资源失效

## 明确保留的兼容层

以下内容不因本次对外改名而改变：

- `DRAMACLAW_*` 环境变量
- `dramaclaw_*` Hermes 工具名和 API 工具契约
- `.hermes/plugins/dramaclaw/`、`.hermes/skills/dramaclaw/` 路径及其机器可识别名称
- `provider = "dramaclaw"` 等枚举值
- `dramaclaw:*`、`supertale-*` 等 localStorage、IndexedDB 和缓存键
- Python 包 `novelvideo`、npm 包名、API 路径、数据库字段和任务类型
- Docker Hub 上尚未迁移的真实镜像坐标
- `docs/plans/**`、`docs/superpowers/plans/**` 和既有 `docs/superpowers/specs/**` 中的历史记录
- 第三方许可证原文、依赖清单、SBOM 和生成的锁文件

当当前使用文档必须提到旧兼容标识时，应明确标注为“内部兼容名称”，不把它当作产品名称展示。

## 实施方式

1. 建立文件级扫描清单，把匹配项分为展示文案、公开链接、图片资源、内部兼容标识和历史记录。
2. 先替换根 README 与中英文当前文档，再处理产品界面和仓库社区文件。
3. 对图片进行视觉检查，保留不含旧品牌的作品素材；替换或移除带旧字标的品牌图。
4. 更新受影响的快照、断言和公开链接测试，不修改仅验证内部兼容协议的测试。
5. 运行文案残留扫描、前端测试、前端构建及 Git 差异检查。

## 验收标准

- 用户阅读根 README、中文 README、当前中英文文档和社区文件时，不再将 `DramaClaw` 作为产品名称展示。
- 产品界面的正常用户路径不再显示 `DramaClaw`。
- README 与当前使用文档中的 GitHub 仓库、Issue、Discussion、Release 和 clone 链接均指向 `YuRui-Liu/Nuomi-drama-factory`。
- 页面和 README 展示的图片中不含旧品牌字标。
- `DRAMACLAW_*`、`dramaclaw_*` 及其他内部兼容契约保持原值，相关测试继续通过。
- 历史设计记录、第三方声明和不可用的新镜像坐标不被机械改写。

## 验证

- 对约定的用户可见文件执行大小写不敏感的 `dramaclaw` 残留扫描，并逐项确认残留均属于允许的 URL 路径或兼容说明。
- 检查所有新仓库链接及相对文档链接。
- 运行品牌相关前端测试和受影响模块测试。
- 运行前端构建。
- 运行 `git diff --check` 并审查完整 diff，确认没有修改内部协议标识。
