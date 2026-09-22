# 全项目代码审计记录（持续更新）

目标是检查漫剧创作功能正确性、可恢复性、数据边界和生产成本。上一轮针对角色质检、可选草图及 CLI 的审计不能替代本记录。本记录也不代表逐行审查全部源文件或完成真实模型端到端验收。

## 已确认问题与修复

| 编号 | 优先级 | 触发条件及用户影响 | 修复位置与回归证据 |
| --- | --- | --- | --- |
| F01 | P1 | 导入数据库提交后恢复日志清理失败，原文被回滚为旧内容，数据库与文件不一致，且上层可能回滚图数据 | `episode_source_store.py` 分离提交与清理；`test_post_commit_cleanup_failure_does_not_restore_old_novel` |
| F02 | P1 | 合并或移动得到 5 镜分组，编辑校验通过，但持久模型和布局只允许 4 镜，后续读取/生成失败 | `director_plan/editing.py` 对齐 4 镜上限；merge/move 两项失败回归 |
| F03 | P2 | 横屏项目导演规划输入固定为 9:16，与项目构图设置不一致 | `task_backend/runners/director_plan.py` 使用项目比例；横竖屏参数化回归 |
| F04 | P1 | 场景变体有可用参考图，但缺少独立基础场景记录时，显式基础名/变体信息被旧格式解析覆盖，误判为待确认 | `narrative_groups/planned_binding_service.py` 保留显式结构化标识；场景绑定 65 项测试通过 |
| F05 | P1 | 缺镜头视频或某片段 FFmpeg 失败被跳过，仍发布整集成功，实际漏镜头 | `runners/video.py` 校验预期覆盖、文件及每段输出；`test_compose_episode_completeness.py`，保留原有原子发布和旧成片 |
| F06 | P1 | 供应商接受任务但提交超时/返回不可解析，UNKNOWN 再执行会重复付费；并发执行同一任务也可重复提交 | `runtime/executor.py`、`task_store.py`、`video/pipeline.py`：持久原子提交认领，UNKNOWN 停止自动重提；runtime/video/store 304 项通过 |
| F07 | P1 | 旧任务回调先读后写可覆盖新任务 ID，回退 heartbeat/lease 或覆盖取消字段 | `task_state.py` progress/complete/fail 无 owner 路径改为 task_id + 非终态 CAS；18 项竞态回归 |
| F08 | P2 | 轮询先写完成状态、SSE 后到时不再触发刷新，生成完成仍显示旧素材 | 前端 `task-center/provider.tsx` 统一快照与流事件的完成处理；polling/hydrate 回归 |
| F09 | P2 | 场次解析完成未失效 screenplay-semantics 查询，工作台停留旧结果 | 前端任务中心补场次查询失效；解析/修复完成测试 |
| F10 | P2 | `/auth/me` 暂时 5xx 保留身份，但页面在登录和首页间反跳 | `_app.tsx` 保留门禁并定时重试；真实登出、恢复、卸载清理回归 |
| F11 | P2 | 生成历史存储绝对路径，缩略图解析将其再拼到项目目录，导致缩略图始终 missing | `freezone/history.py` 区分项目内绝对路径与媒体 URL，保留越界检查；缩略图/路径 22 项通过 |
| S01 | P1 | agent 的项目限制只检查 URL；通过 query/body 选择项目可跨项目读取/修改 | `project_context.py` 校验解析后的 canonical project ID；scope/media/context 回归 |
| S02 | P1 | 上传 HTML/SVG 后以同源活动页面打开，可执行脚本 | `api/routes/files.py` 活动文档强制下载、sandbox/nosniff，禁止 OSS 重定向绕过；图片/音视频 Range 保留 |
| S03 | P1 | 聊天 WebSocket 接受低权限 agent/viewer 并创建高权限子会话；未实现 scope 的历史路径可被使用 | `api/routes/chat.py` 校验执行权限和范围、逐消息重新鉴权；合法读取/执行保留，聊天及安全组合 110 项通过 |
| S04 | P1 | 换账号后首页聊天缓存仍显示前账号内容 | 前端会话清理覆盖 `superchat:` 与 `freezone:`；真实聊天 hook 回归 |
| F12 | P1 | 删除某 slot 版本时只检查本 slot 引用，误删其他 slot 仍引用文件；fallback 也可能覆盖其他 slot 的 canonical | `production_assets.py` 全部 slot 引用检查，冲突覆盖拒绝并回滚；5 项新增 API 回归，生产资产/工作流 85 项通过 |
| F13 | P1 | 正文保存 A 未返回时输入 B，A 完成会清掉 B 的 dirty；后续离开页面丢失 B，跨组件请求也可能乱序覆盖 | 前端 `text-pane.tsx` 按分镜及已保存值清理 dirty；`queries/scripts.ts` 串行同剧集 PATCH；失败与新分镜回归 |
| F14 | P1 | 删除当前采用版本后，直接采用最新候选，可隐式绕过 QC 失败、技术错误或人工确认规则 | `production_workflow/store.py` 回退复用 `apply_adoption` 资格检查；无合格候选则无当前版本；10 项新增组合回归，相关集合 95 项通过 |
| F15 | P1 | 公开 cancel 与 submit 返回竞争，UNKNOWN 状态拒绝迟到供应商 ID，造成已提交远端任务失联 | `executor.py`/`task_store.py` 保存迟到 ID 并协调取消；拒绝、超时仍保留 ID；runtime/TaskStore/pipeline 381 项通过 |
| F16 | P1 | 覆盖新剧本后继续复用旧 active 导演计划，或规划等待期间原文变化，批量继续生产旧剧情 | `episode_source_versions.py`、导演/语义 store 与 runner、API、batch 校验源版本；过期历史可查看但不可生产，批量先解析当前语义再规划；升级项目在授权后登记可信数据库，独立复核及相关 116 项测试通过 |
| F17 | P1 | Windows 桌面启动 API 时，生产工作流无条件导入 Unix 专属 `fcntl`，后端无法启动；原打包 smoke 未导入 API，漏掉故障 | `production_workflow/store.py` 使用已有跨平台 portalocker，保留线程重入和进程锁；桌面 stage 检查真实 API 导入。禁止 fcntl 导入、嵌套异常释放和跨进程互斥回归；相关 98 项通过 |
| F18 | P1 | Windows 不支持 fcntl 时媒体索引锁静默退化为无锁，并发图片/视频任务读改写可丢索引 | `utils/state_index_files.py` 使用跨平台进程锁；pool 相关 8 项通过，根节点再次运行项目/索引锁 4 项通过 |
| C01 | P2 | 示例配置保留 6 个已不再读取的文本 MODEL 项，用户修改后不生效；检查器漏掉 3 个真实 thinking 参数 | 示例说明统一文本模型设置，检查器识别已证明的类方法参数转发；11 项检查器测试，真实仓库 dead=0/missing=0 |
| C02 | P2 | 清单生成器按换行解析 Git 输出，中文路径被引号转义；还可能把本机旧版本许可证标给 lock 新版本 | `generate_p0b_artifacts.py` 使用 NUL 分隔，依赖 metadata 版本必须匹配；新增 2 项先失败后通过回归。缺失元数据不伪造结论 |

优先级：P1 会造成错误产物、数据丢失、越权或重复成本；P2 会阻塞或误导正常操作。各测试集合有重叠，不累计为总数。

## 审查覆盖与限制

| 范围 | 本轮检查方式 | 尚未完成的验证 |
| --- | --- | --- |
| 剧集原文、导入事务、来源版本 | 存储/编排代码、事务故障注入、清理失败后再次提交及重开恢复 | 进程在每个 fsync/commit 时间点被杀的恢复矩阵 |
| 场次解析、导演规划、分镜编辑、场景绑定 | 前后端调用契约、编辑约束、绑定测试、覆盖导入后的源版本失效与升级兼容 | 真实长剧本语义质量；真实项目目录迁移 |
| 角色/场景/道具生产资产 | 采用/删除共享引用、QC、删除后的候选资格检查、工作流与 API 测试 | 真实模型通过率 |
| 图片/视频供应商和任务系统 | 远端提交不确定性、取消、并发、重启认领、回调 CAS | 在线供应商对账、跨机器共享数据库压力测试 |
| 视频合成/导出 | 期望镜头覆盖、失败传播、旧成片保护、导出兼容测试 | 真实素材声画同步、字幕、转场听感与视觉验收 |
| 前端操作和任务中心 | 登录、事件/轮询、场次结果、缓存隔离及编辑保存竞态 | 多浏览器并发编辑还没有后端内容版本 CAS；真实浏览器完整制作操作 |
| 鉴权/文件/聊天 | HTTP、底层项目解析、WebSocket、上传预览、项目引用 | 不宣称全部大型路由逐行安全覆盖；聊天取消的跨 scope 隔离还需专项检查 |
| 配置/模型网关/媒体中继 | 源码与配置测试；隔离 OS 凭据库后网关/中继/secret 88 项通过 | 本机原生 Keychain、生产凭据后端部署验收 |
| 备份、容器与部署 | 阅读 db_daily/files_sync/Dockerfile/selfhosted compose；导入/备份相关 52 项测试通过 | OSS/rclone 实际恢复演练、镜像供应链锁定、容器启动实测 |
| Windows 桌面 | 后端导入路径、打包 smoke、项目/媒体索引进程锁、运行路径测试 | 在 macOS 上做故障模拟及进程锁验证；尚未构建 Windows 安装包或实机完整制作 |
| director_world、旧生成器、知识运行时、合规构建 | 默认全量测试与定向代码筛查；陈旧契约已与实际实现核对 | 仍有 3 项合规证据/清单测试失败；不声称已审完所有实现 |

## 测试基线与测试自身缺陷

首次全量后端收集因引用已删除的 reference resolver helper 失败，已迁移到当前 planned snapshot 测试接口。源代码 Ruff 发现重复导入并已删除。

首轮全量后端执行为 5522 passed / 121 failed / 16 skipped / 2 deselected。该进程运行期间修复仍在进行，结果不是最终版本验收。随后确认其中一部分来自：

- 网关测试未隔离 OS 凭据库，可能读写开发者实际凭据；`tests/conftest.py` 对网关消费者注入 test-local memory store，底层平台后端测试仍独立测试。
- `test_m01_auth.py` 删除 `sys.modules` 内的 API 模块，使提前导入的 FastAPI 依赖与重建后的依赖不是同一函数；20 项并发设置测试单跑通过、组合失败。删除模块清理并更新结构化导入项目字段契约后恢复。
- 第一轮前端全量 2393 passed / 7 failed，7 个失败来自新增 hook 缺 mock；已修复并补功能回归，最终全量见下文。

未调用付费模型，未修改用户项目媒体或线上数据，未提交 Git。gitleaks/pre-commit 本地不可用；不能报告已通过密钥扫描。

后续独立复核补获并修正了两项边界：导入清理失败后须重新启用恢复，防止下一次提交跨过遗留日志版本；本地缩略图文件名中的 `#`/`?` 不应作为 URL 片段截断。

前端最终全量结果：350 文件、2415 用例通过；TypeScript 检查通过。打包产物测试先因 uv 缓存沙箱权限失败，获准在沙箱外重跑后 1 项通过。

桌面 Node 测试 8 项通过，包括实际 API 导入 smoke 契约与注入 Windows path 的盘符、空格及 PATH 分隔符测试。此处不是 Windows 安装包实机验收。

最后一轮后端全量：`5732 passed / 3 failed / 16 skipped / 2 deselected`（169.29 秒）。3 个失败全部位于 `test_p0b_compliance_generator.py`：两项精确版本许可证证据缺失、一项 Git 索引许可证清单缺失。全量收集后补入的 `test_state_index_portable_lock.py` 另行运行通过；项目锁与索引锁合计 4 项复验通过。源代码 Ruff、TypeScript、`git diff --check` 复验通过。不能将此结果表述为全量全绿。

## 仍需验证或处理

- **桌面功能缺口（已确认、未修）**：从继承 `ST_LOCAL_API_TOKEN` 或非 loopback `NOVELVIDEO_PUBLIC_HOST` 的环境启动时，`desktop/runtime-paths.cjs` 保留这些变量，`main.cjs` 又不建立认证 cookie；公开健康检查可通过，但受保护操作被拒绝。合成环境下已验证认证拒绝。需要补桌面专用认证传递与环境隔离设计，不能靠移除认证检查解决。
- **桌面退出风险（未实机复现）**：`desktop/main.cjs` 只 kill 后端且不等待退出或处理子树；Windows 存在活动媒体子进程残留的可能，需要实机进程树验证。
- 依赖许可证清单尚不能生成：按 lock 与本机 metadata 版本比对，14 个条目缺少可用的精确版本证据，包括 antlr4-python3-runtime、cloudpickle、demucs 两个版本、dora-search、julius、lameenc、omegaconf、openunmix、retrying、sphn、submitit、torchaudio、treetable。未联网推断或填写许可证。
- 已提交的 `license-inventory.csv` 与当前 Git 索引不一致，修正中文路径解析后仍缺 836 项。需要核对新增桌面资源、第三方代码和文档的来源后再更新清单，不能全部套用项目许可证来通过测试。
- 无供应商 ID 的崩溃遗留提交 claim 不会重提，但单次 step 仍可能显示 PREPARING；视频流程默认 1800 秒轮询预算，CLI 单任务/批任务默认 600/900 秒。上传及并发租约等待不在严格的墙钟时限内。
- 已绑定源数据库的项目移动物理目录需要重绑定规范路径；这是新增持久身份契约，当前不会自动信任指向另一目录的定位文件。无源数据库的旧项目继续兼容。
- 真人画面评估、长集声画同步、真实供应商对账、跨浏览器并发编辑、备份恢复及分发合规仍不能由本轮离线测试替代。
