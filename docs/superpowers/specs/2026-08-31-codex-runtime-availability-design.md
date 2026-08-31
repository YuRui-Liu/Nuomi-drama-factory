# Codex Runtime 可用性设计

**日期：** 2026-08-31
**状态：** 已确认，待实现计划
**范围：** NuomiDrama 本机 Codex CLI 的识别、状态展示与结构化文本任务执行

## 1. 背景与问题

项目目前把两个不同概念混在一起：

- **Runtime 识别：** 本机是否存在可启动、版本兼容且已登录的 Codex CLI。
- **任务执行健康：** 某次 `codex exec` 是否在合理时间内返回有效结构化结果。

现有设置页的“检查 Codex”只检查版本和登录，但没有最低版本门槛；实际结构化任务没有总执行超时，Windows 上通过 `.cmd` 启动时还可能出现最终消息已生成、父进程未及时退出的情况。当前未提交实现又在 CLI 失败外层完整重跑最多三次，而 Codex CLI 自己已经包含流式重试和 HTTPS 回退，这会重复消耗时间和额度。

本设计参考 Multica 的分层方式：Runtime 注册只负责路径、版本与兼容性；真实任务的网络和进程健康由任务执行器负责。NuomiDrama 不引入守护进程、任务中心探针或定时模型调用。

参考：

- [Multica CLI 发现配置](https://github.com/multica-ai/multica/blob/main/server/internal/daemon/config.go)
- [Multica Runtime 注册与版本门槛](https://github.com/multica-ai/multica/blob/main/server/internal/daemon/daemon.go)
- [Multica Agent Runtime 安装与检测](https://github.com/multica-ai/multica/blob/main/apps/docs/content/docs/install-agent-runtime.mdx)

## 2. 目标

1. 项目能稳定识别当前 Windows 用户实际可用的 Codex CLI。
2. `CODEX_BIN` 显式配置优先于 `PATH` 自动发现。
3. 只在路径、版本、最低版本和登录状态均通过时标记 Runtime 可用。
4. 设置页重新识别不调用模型、不消耗 Codex 额度。
5. 生产结构化任务只启动一次 `codex exec`，由 Codex CLI 自己处理传输重试和 HTTPS 回退。
6. 生产任务有明确超时、取消和残留进程清理，不无限等待。
7. 最终消息已经可靠写入时，可以在短暂退出宽限期后回收结果并清理残留进程。
8. Codex 失败时明确停止并返回稳定错误码，不静默切换模型 API。

## 3. 非目标

- 不增加本机常驻 Runtime 守护进程。
- 不把 Runtime 检测接入任务中心。
- 不在启动时或定时自动调用模型。
- 不自动修改、删除或重建用户的 Codex 配置和模型缓存。
- 不探测所有可用模型，也不替用户选择文本任务路由。
- 不改变 RunningHub MiniMax H3、GRSAI、Ollama 等媒体和向量运行时。

## 4. Runtime 识别

### 4.1 路径解析

按以下顺序解析 Codex 启动器：

1. 非空的 `CODEX_BIN`；
2. 当前后端进程 `PATH` 中的 `codex`；
3. 找不到时返回 `CODEX_NOT_INSTALLED`。

Windows 下继续使用参数数组启动，不拼接可执行的 shell 字符串：

- `.cmd` / `.bat` 通过 `ComSpec /d /s /c`；
- `.ps1` 通过非交互 PowerShell 和 `-ExecutionPolicy Bypass -File`；
- `.exe` 或无脚本后缀的可执行文件直接启动。

状态响应可返回解析后的本机路径，但不得包含令牌、配置文件内容或用户凭据。

### 4.2 版本和登录门槛

识别流程依次执行：

1. `codex --version`，10 秒超时；
2. 解析语义版本；
3. 要求 Codex CLI 版本不低于 `0.100.0`；
4. `codex login status`，10 秒超时；
5. 只有命令返回成功且不是 `Not logged in` 时才算已登录。

`CodexCliStatus` 应区分：

- `installed`：路径已解析；
- `compatible`：版本可解析且满足最低版本；
- `authenticated`：登录状态通过；
- `ready`：前三项全部为真；
- `path`、`version`、`message`：供设置页诊断。

最低版本固定为项目常量，避免前端和后端各自维护不同门槛。

### 4.3 API 与前端

- `GET /knowledge-runtime/status` 使用轻量识别结果，不调用模型。
- 保留 `POST /knowledge-runtime/codex/test` 兼容现有前端，但语义改为“重新识别 Codex”。
- 设置页按钮文案改为“重新识别”。
- 状态文案明确区分“可用”“版本过低”“未登录”“未安装”“无法启动”。
- Codex 与 Ollama 仍分别显示；知识运行时总状态继续要求 Codex 可用且 Ollama 已配置。

## 5. 生产任务执行

### 5.1 单次执行

`CodexCliStructuredBackend` 和文本任务路由中的 Codex 后端共享同一套底层执行函数。一次结构化请求只启动一次 `codex exec`。

不得在项目外层对 `CODEX_EXEC_FAILED` 完整重跑三次。Codex CLI 已负责 WebSocket 重试与 HTTPS 回退；外层重跑会重复消耗任务时间和额度。结构化 JSON 校验失败后的修复提示仍可保留，因为这是一次新的、明确的输出修复请求，不属于传输重试。

### 5.2 超时与完成信号

- 环境变量 `CODEX_EXEC_TIMEOUT_SECONDS` 控制单次执行总超时；默认 600 秒。
- 配置值必须是正整数；非法值在启动执行前返回明确配置错误。
- `--output-last-message` 文件是最终结果的可信完成信号。
- 正常进程退出且返回码为 0时，优先读取最终消息文件。
- 最终消息文件非空且连续 5 秒大小与修改时间不变，但父进程仍未退出时：
  1. 读取最终消息；
  2. 只终止本次启动的进程树；
  3. 将本次执行视为成功；
  4. 记录“最终消息后残留进程已清理”诊断信息。
- 到达总超时且没有稳定最终消息时，终止本次进程树并返回 `CODEX_EXEC_TIMEOUT`。

### 5.3 取消与 Windows 进程树

- 任务取消必须终止本次 Codex 进程树并重新抛出 `CancelledError`。
- POSIX 先发终止信号，宽限后强制结束。
- Windows 使用被启动进程的精确 PID 清理子进程树，不能按进程名批量终止，也不能影响其他 Codex 会话。
- 异步子进程不可用时保留线程包装回退，但其 `pid`、`wait`、`terminate`、`kill` 行为必须满足同一执行器协议。

### 5.4 输出与错误

- stdin、stdout、stderr、Schema 文件和最终消息文件统一使用 UTF-8。
- 进程非零退出且不存在稳定最终消息时，从 stderr 与 stdout 的末尾提取可操作诊断。
- 稳定错误码至少包括：
  - `CODEX_NOT_INSTALLED`
  - `CODEX_VERSION_UNSUPPORTED`
  - `CODEX_NOT_AUTHENTICATED`
  - `CODEX_EXEC_TIMEOUT`
  - `CODEX_SCHEMA_INVALID`
  - `CODEX_EXEC_FAILED`
- 不把模型缓存警告或插件目录警告单独视为失败；是否成功以最终消息、进程结果和 Schema 校验为准。
- 不静默路由到 `model_api`。只有用户事先配置的文本任务路由可以选择 `model_api`。

## 6. 数据流

### 6.1 重新识别

```text
设置页“重新识别”
  → POST /knowledge-runtime/codex/test
  → 解析 CODEX_BIN / PATH
  → codex --version
  → 最低版本检查
  → codex login status
  → 返回 CodexCliStatus
  → 刷新设置页状态
```

### 6.2 结构化任务

```text
冻结后的文本任务路由
  → CodexCliStructuredBackend
  → 构造只读、临时、无插件的 codex exec 参数
  → 受控启动单个进程树
  → UTF-8 写入提示词
  → 等待正常退出 / 稳定最终消息 / 取消 / 总超时
  → 清理临时目录与残留进程
  → Pydantic Schema 校验
  → 成功结果或稳定错误码
```

## 7. 测试设计

### 7.1 Runtime 识别单元测试

- `CODEX_BIN` 优先于 `PATH`。
- 找不到启动器返回未安装。
- Windows `.cmd`、`.ps1` 和直接可执行文件参数保持安全。
- 版本解析覆盖 `codex-cli 0.146.0`、无法解析和低于 `0.100.0`。
- 版本命令和登录命令分别覆盖成功、非零退出和 10 秒超时。
- 登录失败不得被标为 `ready`。

### 7.2 执行器红绿测试

- 当前实现缺少总超时：先用不会结束的假进程验证测试失败。
- 增加超时后验证返回 `CODEX_EXEC_TIMEOUT` 并清理进程树。
- 假进程写入稳定最终消息但不退出：验证结果被回收且只清理自己的 PID。
- 取消任务：验证进程树被清理并传播取消异常。
- 非零退出：验证错误摘要包含 stderr/stdout 末尾的可操作信息。
- 最终消息包含中文：验证 UTF-8 无乱码。
- `CODEX_EXEC_FAILED` 不触发外层完整重跑。

### 7.3 API 与前端测试

- `codex/test` 只调用轻量识别函数，不启动 `codex exec`。
- API 返回兼容性、登录和路径状态，不泄露秘密。
- 前端按钮显示“重新识别”，点击后刷新查询。
- 状态标签覆盖可用、版本过低、未登录和未安装。
- 前端请求保持默认短超时，因为识别只执行两个快速本地命令。

### 7.4 验证命令

实现阶段至少运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_knowledge_runtime_codex.py tests\test_api_knowledge_runtime.py tests\test_text_task_runtime_routing.py -q -p no:cacheprovider --basetemp '.codex-runtime-final'
```

前端至少运行 Runtime 查询与设置组件的定向 Vitest。真实 Codex 模型调用不进入默认测试套件，避免测试自动消耗额度；人工验收通过设置页重新识别和一个真实项目文本任务完成。

## 8. 验收标准

1. 当前机器能识别 `E:\npm-global\codex.cmd` 和 `codex-cli 0.146.0`。
2. 已登录且版本兼容时，设置页显示 Codex Runtime 可用。
3. 点击“重新识别”不产生模型调用。
4. 中文结构化任务结果无乱码。
5. WebSocket 重试后由 Codex CLI 回退 HTTPS 时，项目不额外完整重跑任务。
6. 最终消息已生成但父进程残留时，结果可交付且只清理本次进程树。
7. 无最终消息的超时任务在 600 秒内停止并返回 `CODEX_EXEC_TIMEOUT`。
8. Codex 失败不会静默切换到模型 API。
9. 后端与前端 Runtime 定向测试全部通过。
