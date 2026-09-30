# 跨项目手法卡片库实现计划

> **面向 AI 代理的工作者：** 使用 superpowers:subagent-driven-development 逐任务实现，先规格审查、再质量审查。用户明确要求直接修改当前分支，不新建 worktree。

**目标：** 将导演片段的下拉框和长列表替换为共享手法库弹窗，提供全局入口、自有镜头示意、搜索分类和用户收藏。

**架构：** 全局只读目录与用户收藏接口独立于项目。共用弹窗接收可选片段上下文，浏览模式无选用操作，选卡模式沿用现有兼容性和快照契约。收藏使用服务端用户身份持久化，展示资源不参与生成内容哈希。

**技术栈：** FastAPI、Python、React、TypeScript、Radix Dialog、TanStack Query、pytest、Vitest。

## 任务 1：全局目录与用户收藏

文件：新建 `src/novelvideo/api/routes/technique_library.py`、`src/novelvideo/freezone/video_director/favorites.py`、`tests/freezone/video_director/test_technique_library.py`；注册到 `src/novelvideo/api/__init__.py`。原项目目录接口保留。

- [ ] 先写 API 与存储契约测试：全局 GET 无需项目，身份缺失拒绝，两个用户收藏隔离，重复写幂等、取消收藏、重新打开存储持久化、未知 ID 拒绝、退役收藏保留、并发收藏无丢失、存储错误不返回假成功。
- [ ] 运行 `PYTHONPATH=src .venv/bin/python -m pytest tests/freezone/video_director/test_technique_library.py -q`，确认缺失接口的失败。
- [ ] 提供以下接口（返回沿用 `{ok:true,data:...}`）：
  ```text
  GET /api/v1/techniques → {catalog_version, techniques}
  GET /api/v1/techniques/favorites → {ids: string[]}
  PUT /api/v1/techniques/favorites/{card_id} → {ids: string[]}
  DELETE /api/v1/techniques/favorites/{card_id} → {ids: string[]}
  ```
  全部使用 `get_api_user`。从认证身份获取 owner，不接受客户端 owner。使用现有配置的服务端 state 目录，SQLite 事务/唯一键存 `(owner, card_id)`；连接在操作后关闭。读取不能把损坏或不可访问的数据当空集合。可删除已不存在卡 ID 的收藏。目录和生成指令不改变。
- [ ] 跑新测试及 `tests/freezone/video_director` 回归；审查差异，只提交本任务文件。

## 任务 2：弹窗、示意与两个入口

文件：新建 `frontend/src/api/techniqueLibrary.ts`、`frontend/src/features/technique-library/{TechniqueLibraryDialog,TechniqueSketch,useTechniqueLibrary,techniquePresentation}.tsx/ts`；修改 `frontend/src/api/videoDirector.ts` 类型、`frontend/src/features/canvas/director/TechniqueCardPicker.tsx`、`frontend/src/components/layout/header.tsx`；新增相关组件测试，更新 `video-director-techniques.test.tsx` 与中英文语言资源。

- [ ] 先写用户行为测试：全局打开不需 project；搜索/分类/空状态；片段默认可用过滤、查看全部的不兼容详情不可选；收藏读取与写入失败不影响选卡；选卡只调用目标回调；关闭还原焦点；导演摘要不平铺目录；八张卡有各自示意，长小数格式化。
- [ ] 使用 `./node_modules/.bin/vitest run technique-library video-director-techniques` 看到预期失败。
- [ ] API 与共享状态以当前认证用户为 key，退出/换用户不泄露收藏；写收藏等待服务器成功后更新缓存，显示失败与重试，按卡片禁用重复写。目录与收藏独立加载。
- [ ] 弹窗用 Radix Dialog，搜索栏、类目/收藏、网格和可滚动详情；小屏列表/详情可返回。详情展示 action_beats/performance/camera/ending_composition/avoid、来源和技术限制。所有交互可键盘操作。内置八种自有 SVG 镜头示意并标注“镜头示意”，无外部媒体请求。按稳定目录顺序显示，不做 AI 推荐。
- [ ] 片段模式以原 `techniqueCompatibility` 判断，缺 capabilities 时禁止选择；过滤条件无结果可重置。切片段重置选卡上下文，浏览变化不改草稿。当前选择显示摘要/换卡/清除，未知与不兼容错误仍保留。
- [ ] 顶栏增加带标签的手法库入口，使用相同弹窗的浏览模式。旧 director 目录读取可保留兼容，卡片完整类型字段可选以兼容历史记录。
- [ ] 跑新增测试、全部 director 测试、header 测试与 `tsc -p tsconfig.app.json --noEmit`，按自有变更范围提交；已有未提交 header/locale 变更必须保留且不能随意纳入提交。

## 任务 3：集成和真实界面验收

- [ ] 规格审查：逐项核对顶栏、跨项目、用户隔离、目录/收藏失败、退役、选卡适用性、版本与历史、焦点、窄屏、原创示意。
- [ ] 代码质量审查：检查状态竞争、旧请求覆盖新账号、并发收藏、嵌套弹窗事件、键盘可用性和持久化错误。
- [ ] 运行后端 `tests/freezone/video_director`、前端 `vitest run video-director technique-library`、header 相关测试、TypeScript 与 Vite build。只对新失败进行定位和修复。
- [ ] 用本地运行界面实际检查全局浏览和片段选卡、关闭回焦、收藏及缩窄布局；保留截图或准确说明未验证的部分。
- [ ] 完成后更新本计划，报告修改、验证及必要局限。用户已授权在当前分支开发，不再询问 worktree 或合并方式。
