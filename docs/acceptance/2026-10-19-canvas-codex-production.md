# Codex + 轻量画布实施验收

日期：2026-10-19。分支：`feat/canvas-codex-production`。

## 实施范围

- 十类核心产物投影、默认轻量画布和显式兼容视图；隐藏内容保留在完整画布数据中。
- 项目内正文服务与指纹冲突保护；文件引用节点使用同一正文，旧节点不自动绑定不存在的文件。
- 按集持久化剧本、资产、分镜审阅停点；分镜要求表和宫格；auto、stop-after 及清除停点。
- CLI 批量 patch、正文、进度、经验检索、H3 preview/prepare/submit；沿用现有执行队列。
- 过期结果保留在运行记录，不覆盖当前节点；失败保留已有媒体；成功重试清除失败状态。
- 本地案例/手法检索保留出处，检查模式、时长和首尾帧约束，不传递整篇外部提示词。
- 生产 skill 已更新，用户级路径通过现有符号链接指向仓库，无需重复安装。

## 后端验证

最终联合命令：

```bash
uv run pytest --import-mode=importlib --tb=short -q \
  tests/creation/test_core_artifacts.py \
  tests/creation/test_production_documents.py \
  tests/creation/test_production_progress.py \
  tests/test_canvas_cli.py \
  tests/freezone/orchestration \
  tests/freezone/video_director \
  tests/technique_library \
  tests/acceptance/test_codex_canvas_production.py \
  tests/acceptance/test_single_episode_production.py
```

结果：494 passed，11 条既有依赖弃用警告。使用 importlib 模式避免不同目录同名 `test_api.py` 的收集冲突。

模拟供应商验收使用真实文档、进度、RunStore、Scheduler 与投影服务，覆盖三个停点、一键推进、显式停止，以及失败重试不重做已成功上游。此结果不是付费供应商或真实成片验收。

联合运行后补充的核心产物用例单独验证：4 passed（不与上述 494 重复相加）。

## 前端与浏览器验证

最终四个聚焦套件（core-artifacts、screenplay-workspace、script-node-usability、project-navigation-routes）：51 passed。`pnpm --dir frontend build` 通过 TypeScript、Vite 和 bundle budget；仍有循环 chunk 与大 chunk 警告。

在本地运行画布中实查：默认隐藏 Agent 编排入口，添加菜单只列核心产物，旧剧本不再虚构正文路径。修复了未启用查询被误判为加载中的问题；不匹配的分集作用域显示关联提示，不展示其他作用域缓存。独立复核另跑 4 个相关回归用例通过，未发现剩余重大问题。

## H3 规范核对

核对来源为 MiniMax 官方 [Base 指南](https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing/references/base-en.txt)与 [Ref 指南](https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing/references/ref-en.txt)。本地 profile 因对白锁定校验更新为 `minimax-h3-director@27`；客户端从 preview 读取当前版本，不写死版本号。

实际适配范围为首帧 I2VA、首尾帧 FL2VA 与图片参考 Ref2VA；参考模式支持纯参考、参考加首帧、参考加首尾帧。T2VA、L2VA、视频/音频参考及单图多个独立主体没有作为已支持能力声明。仅尾帧加参考也不支持。

prepared 输入通过共享编译和校验；锁定对白必须保持说话人、原文和显式语言，使用官方 `<d>` 表达。preview 不上传或提交供应商；提交重新冻结素材并校验指纹。预览中的图片 ID 占位符在实际上传后替换，提示词与时间线使用同一编译路径。

## 独立审查修复

- 完成→失败→相同结果重新完成不能残留失败状态。
- prepared 不能利用中文语言注释绕过对白校验；中英文合法说话人语法可用，非对白描述不误判。
- 带尾帧模式不得推荐禁止尾帧的手法。
- 隐藏分组时保留布局所需的透明结构父节点，拖动子节点不破坏原图。
- 切换兼容视图不改变 documentRef 正文来源。
- 未绑定文件的旧剧本保留原有内容，不凭集数推断不存在的正文文件。
- 旧剧本查询与展示使用相同作用域校验；禁用查询不显示无限加载，切换作用域不泄露旧缓存。

## 验证界限

本次没有发起付费图片、视频、配音或配乐生成，没有把模拟 URL 当作可播放成片。真实供应商的质量、声音与最终播放仍需要在指定项目和授权生成范围内验收。

工作区在本次实施前已有大量未提交修改；实现保留这些修改，没有整批暂存或混合提交。设计与计划已有独立文档提交，本次代码仍留在工作区供审阅。
