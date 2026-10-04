# 糯米助手路由与项目持久会话实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 在统一文本任务设置中配置糯米助手，让每个用户在每个项目持续使用同一个助手会话，只有手动清除才切换会话。

**架构：** 聊天路由解析与供应商能力校验独立；项目聊天数据库保存会话身份、代次、轮次与清除请求。会话管理器协调并发和恢复，适配器执行原生恢复或持久历史重放，前端只调用已鉴权的会话 API。

**技术栈：** Python 3.12、FastAPI、SQLite、现有 CLI/SDK 适配器、React、TypeScript、pytest、Vitest。

**规格：** `docs/superpowers/specs/2026-10-04-nuomi-assistant-project-session-design.md`，已获用户确认，提交 `3ab1699`。

---

## 工作区与执行约束

仓库：`/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。现有工作区有大量未提交的其他功能，不执行 reset、stash 或全量 git add。实施前记录涉及文件的 diff；逐任务只提交自己产生的变更。若使用隔离 worktree，必须先确认其包含依赖的现有聊天代码与已批准规格，不可从过期 HEAD 测试后直接覆盖现有工作区。

本文为实现计划，不是已实现能力。所有代码块是需要加入的契约、回归断言或关键实现步骤；先写测试并确认因目标行为缺失而失败，再接入实际实现。供应商使用可控替身完成错误/并发测试，最终只做一次必要的真实聊天验证。

## 文件职责

| 文件 | 职责 |
|---|---|
| `src/novelvideo/chat/routing.py`（新建） | `chat_agent` 配置契约、旧值回退、项目覆盖、运行时能力 |
| `src/novelvideo/chat/runtime_settings.py` | 旧设置接口代理，消除双重权威配置 |
| `src/novelvideo/api/routes/model_gateway.py` | 统一设置读写与角色级能力校验 |
| `src/novelvideo/api/routes/chat_runtime.py` | 兼容旧 API，复用统一权限与存储 |
| `src/novelvideo/chat/session_store.py`（新建） | 会话、轮次、generation、清除幂等性与租约持久化 |
| `src/novelvideo/chat/session_manager.py`（新建） | 获取/恢复、轮次串行、取消与晚到结果隔离 |
| `src/novelvideo/chat/session_migration.py`（新建） | 旧数据归属检查与迁移标记 |
| `src/novelvideo/chat/store.py` | 当前 generation 的消息/事件查询和写入 |
| `src/novelvideo/chat/dispatcher.py` | 按冻结路由选择适配器、按 scope 取消 |
| `src/novelvideo/chat/cli_agent.py` | 同一逻辑会话的 replay 历史与工具结果 |
| `src/novelvideo/chat/hermes_pool.py`、`hermes_sdk.py` | 项目 worker、供应商 ID 恢复、恢复失败不新建 |
| `src/novelvideo/chat/service.py` | 现有 CE 原生调用接入管理器，移除用户级会话依赖 |
| `src/novelvideo/api/routes/chat.py` | 授权 scope、会话查询/清除、WS 事件代次 |
| `frontend/src/lib/queries/model-gateway.ts`、`chat-runtime.ts` | 聊天专用类型与设置 API |
| `frontend/src/components/settings/text-task-routing-panel.tsx`、`chat-runtime-panel.tsx`、`settings-dialog.tsx` | 统一入口与运行时特定控件 |
| `frontend/src/features/superchat/types.ts`、`use-superchat.ts`、`superchat-panel.tsx` | 会话状态、清除入口、断线恢复和缓存隔离 |

不要直接给所有 `TextTaskRuntimeName` 添加 Hermes；聊天角色使用独立能力契约，由统一设置端点组合展示，后台结构化任务继续使用既有枚举。后台配置批量保存不得删除独立保存的聊天角色。

## 任务 1：统一糯米助手路由

**文件：** 新建 `chat/routing.py`、`tests/test_chat_routing.py`；修改上表中的设置后端与前端文件，以及 `tests/team/test_chat_runtime_settings.py`、`tests/test_api_model_gateway_task_runtime.py`。

- [ ] 编写路由回归：CE 环境旧值、TEAM 已保存旧值、新路由优先、项目覆盖、Hermes 空模型合法、后台任务拒绝 Hermes、聊天拒绝未支持的 model_api。

```python
def test_chat_capabilities_are_separate_from_background_tasks():
    from novelvideo.chat.routing import ChatRoute
    from novelvideo.text_task_runtime.models import AgentTaskRoute
    from pydantic import ValidationError
    import pytest
    assert ChatRoute(backend='hermes', model='').backend == 'hermes'
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime='hermes', model='')
    with pytest.raises(ValidationError):
        ChatRoute(backend='model_api', model='example')
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_chat_routing.py -q`，确认新契约尚不存在而失败。
- [ ] 在 `routing.py` 复用 `ChatRuntimeSettings` 的校验定义 `ChatRoute`，暴露 `resolve_chat_route(project_ctx=None)` 和 `save_chat_route(route)`。使用单独的新设置键 `chat_agent_route_v1`，未保存才读取旧配置。保留路由来源 `global/project`。统一端点将 `chat_agent` 显示为聊天角色，携带其 `supported_runtimes`；分拆请求中的聊天与后台配置后分别校验，在同一事务保存。旧聊天 API 委托新函数，禁止循环调用旧回退函数。
- [ ] 前端基于角色能力渲染聊天配置；Hermes 保留其空模型语义，其余运行时验证模型。移除设置对话框中重复的 TEAM 聊天卡片，旧组件仅作为统一行的复用组件或兼容封装。配置读取失败不能以默认值覆盖已保存值。
- [ ] 增加 CE 与 TEAM 设置测试，验证保存后刷新与真实 dispatcher 使用同一配置。运行路由、旧设置与 API 测试，预期全部通过；提交本任务专属变更。

## 任务 2：持久会话与清除事务

**文件：** 新建 `chat/session_store.py`、`tests/test_chat_session_store.py`；修改 `chat/store.py`。

- [ ] 测试按用户/project/home 隔离、进程重新构造后的 ID 稳定、清除幂等、过期 generation 冲突。

```python
def test_clear_rotates_once_and_rejects_late_writes(tmp_path):
    import pytest
    from novelvideo.chat.session_store import SessionStore, SessionConflict
    store = SessionStore(tmp_path / 'chat.db')
    old = store.get_or_create()
    assert SessionStore(tmp_path / 'chat.db').get_or_create() == old
    new = store.clear(expected_generation=old.generation, request_id='clear-1')
    assert store.clear(expected_generation=old.generation, request_id='clear-1') == new
    assert new.session_id != old.session_id
    assert new.generation == old.generation + 1
    with pytest.raises(SessionConflict):
        store.require_current(old.session_id, old.generation)
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_chat_session_store.py -q`，确认失败。
- [ ] 实现 `SessionState` 不可变数据类：`session_id, generation, backend, provider_session_id, mode, status`。`SessionStore(db_path)` 使用已有 `ChatStore` 授权后的数据库路径。公开 `get_or_create()`、`require_current(session_id,generation)`、`clear(expected_generation,request_id)` 和 `bind_provider(session_id,generation,backend,provider_session_id,mode)`；冲突统一抛 `SessionConflict`。
- [ ] 创建 `assistant_sessions`（当前记录固定主键 1）、`assistant_clear_requests`（request_id 主键、旧 generation、结果 JSON）与 `assistant_turns`（turn_id、generation、顺序、状态、lease_owner、lease_until）。所有变更用 `BEGIN IMMEDIATE`。清除先查幂等键，再 CAS generation，取消旧代次排队轮次，生成新逻辑 ID，供应商 ID 置空；此时不启动供应商。
- [ ] 消息与 UI 事件增加 generation 列，读写只针对活动代次；写入时同事务验证会话仍当前。旧记录先标记为待迁移代次，迁移确认前不得回放。测试数据库行保留但清除后当前时间线为空。
- [ ] 运行新存储测试及 `tests/test_chat_replay_strip.py`，预期通过；提交本任务变更。

## 任务 3：项目轮次协调与供应商恢复

**文件：** 新建 `chat/session_manager.py`、`tests/test_chat_session_manager.py`、`tests/test_chat_hermes_session.py`；修改 `dispatcher.py`、`cli_agent.py`、`service.py`、`hermes_pool.py`、`hermes_sdk.py`。

- [ ] 为管理器编写并发测试：两条同 scope 消息按持久顺序执行，两项目互不阻塞；clear 后旧轮次不能调度下一次工具或写入结果。以可控 asyncio Event 暂停旧轮次，避免 sleep 驱动测试。
- [ ] 在现有 CLI/Hermes 测试加入身份断言：换模型不换逻辑 ID；重建 worker 后恢复同一供应商 ID；`session/load` 失败时 `session/new` 调用数为 0。

```python
async def test_failed_resume_never_starts_another_session(tmp_path, monkeypatch):
    import pytest
    from novelvideo.chat.hermes_sdk import HermesSdkThread
    from unittest.mock import AsyncMock
    thread = HermesSdkThread(cli_path=tmp_path / 'unused-cli', cwd=tmp_path,
                             env={}, model=None, username='alice', session_id='saved-id')
    send = AsyncMock(return_value=1)
    read = AsyncMock(return_value=({'error': {'message': 'session missing'}}, []))
    monkeypatch.setattr(thread, '_send', send)
    monkeypatch.setattr(thread, '_read_until_id', read)
    with pytest.raises(RuntimeError, match='resume'):
        await thread._ensure_session()
    assert [call.args[0] for call in send.await_args_list] == ['session/load']
    assert thread.id == 'saved-id'
```

将此测试加入 `tests/test_chat_hermes_session.py`，直接验证 RPC 边界，不调用真实供应商。

- [ ] 先运行 `.venv/bin/python -m pytest tests/test_chat_session_manager.py tests/test_chat_cli_agent.py tests/test_chat_service_user_agent_scope.py -q`，记录缺失行为的失败。
- [ ] 管理器 API 定义为 `begin_turn(scope_context, turn_id, route)`、`assert_current(turn)`、`finish_turn(turn)`、`cancel_scope(scope_context,generation)`。`scope_context` 只来自已鉴权 API，包含 username、canonical scope 和数据库路径；`turn` 保存 session ID、generation、租约 owner、冻结路由。租约用 SQLite CAS，多进程不能只依靠 asyncio.Lock。失联旧执行标记 unknown，核验后才能续跑，不能自动重发工具。
- [ ] CLI 历史路径改为逻辑 session ID 派生，去掉模型/推理参数；`_ACTIVE` 与取消句柄改按用户、scope、generation 索引。保留完整持久历史，不将 `_safe` 的展示截断当成模型历史截断；超长输入报错，不暗清空。每次工具调用前与结果回写前检查代次。
- [ ] Hermes pool 按用户和 scope 管理 worker，workspace/恢复 ID 隔离；idle/token 轮换只销毁进程，不删除持久会话。删除 `session/load` 失败后自动 `session/new` 的分支，返回明确恢复错误。供应商确认 session ID 后立即持久化。
- [ ] CE 原生 Codex/Claude 分支也使用 SessionStore，不再从用户级 `agent_sessions.json` 直接取 ID；不支持当前运行时的既有会话返回配置不兼容错误。TEAM CLI 模式如实标注 replay，不能伪造 native。
- [ ] 运行本任务测试及 `tests/team/test_chat_isolation.py`、`tests/test_chat_dispatcher.py`，预期全部通过；提交本任务变更。

## 任务 4：清除 API 与 WebSocket 代次

**文件：** 修改 `api/routes/chat.py`；新建 `tests/test_chat_session_api.py`；扩展 `tests/test_security_chat_authorization.py`、`tests/test_chat_route_prewarm.py`。

- [ ] 为 `GET /api/v1/chat/session` 与 `POST /api/v1/chat/session/clear` 编写 API 测试。GET 参数使用 `scope_kind`、`project_id`；POST body 使用现有 scope 结构、`expected_generation`、`request_id`。测试 editor、只读用户、agent_session scope 越界、重复 request_id、未知项目和 clear 后旧 WS 事件。

```json
{"scope":{"kind":"project","id":"authorized-project-id"},"expected_generation":2,"request_id":"client-generated-uuid"}
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_chat_session_api.py -q`，确认端点缺失或行为错误而失败。
- [ ] 查询端点不创建供应商进程；复用 `_require_chat_scope_access`。清除需要当前项目写权限。API 返回逻辑 ID、generation、mode、status、有效路由与待生效原因，不返回磁盘路径或敏感凭据。过期 generation 返回 409；恢复失效与迁移阻塞使用结构化错误代码。
- [ ] `scope.changed`、历史响应和每个流式事件附 session ID/generation；消息发送、重连与 cancel 都携带当前 scope/generation。后台回调不匹配则丢弃，不向新时间线发送错误内容。取消仅影响当前 scope，保留独立生产任务。
- [ ] 预热只恢复已授权 scope；迁移阻塞或配置不兼容时不得创建新会话。运行 API、安全、预热测试，预期通过；提交本任务变更。

## 任务 5：前端配置与清除交互

**文件：** 修改 `frontend/src/features/superchat/types.ts`、`use-superchat.ts`、`superchat-panel.tsx`；新建 `frontend/src/lib/queries/chat-session.ts`、`frontend/src/__tests__/features/superchat/session-clear.test.tsx`；扩展现有 use-superchat 与设置测试。

- [ ] 用测试驱动以下交互：清除确认展示项目名；请求失败保留当前聊天；成功后清空当前 scope 缓存、游标和重连引用；其他项目保留；旧 generation 的 WS delta 被忽略。

```typescript
export type AssistantSession = {
  session_id: string;
  generation: number;
  mode: "native" | "replay" | null;
  status: "ready" | "migration_required" | "resume_failed";
};

export function belongsToSession(
  current: AssistantSession,
  event: { session_id: string; generation: number },
) {
  return current.session_id === event.session_id
    && current.generation === event.generation;
}
```

- [ ] 在 frontend 运行 `pnpm exec vitest run src/__tests__/features/superchat/session-clear.test.tsx src/__tests__/features/superchat/use-superchat.test.ts`，先确认失败。
- [ ] `chat-session.ts` 使用项目现有请求封装访问两个会话端点。助手菜单接入真实 `clearSession`，替代 no-op sessionControl。确认文案明确只清上下文；请求 ID 每次用户操作生成一次，网络重试复用。清除成功后再更新 UI，不乐观删除数据。
- [ ] hook 在切换 scope 时读取持久身份，在流式事件入口执行 `belongsToSession`；清除旧 pending promise/缓存引用，但不删除项目数据。通过事件或当前查询刷新同步其他标签页，已过期标签页发送被服务端 409 拒绝后重新拉取。
- [ ] 设置行显示当前有效与“清除后生效”的路由差异。native/replay 使用用户可理解的说明，不将恢复错误显示为原始 SDK 堆栈。
- [ ] 运行本任务 Vitest 与 `src/__tests__/components/settings/chat-runtime-panel.test.tsx`、`src/__tests__/lib/queries/chat-runtime.test.tsx`，并运行 `pnpm exec tsc -b`。通过后提交任务变更。

## 任务 6：旧数据迁移

**文件：** 新建 `chat/session_migration.py`、`tests/test_chat_session_migration.py`；接入 `session_manager.py` 的第一次读取。

- [ ] 用 tmp_path 创建三种旧数据：可证明项目归属的单一历史、用户级跨项目 thread、多模型产生的多份项目历史。断言仅第一种自动迁入；后两种保留旧文件并返回 migration_required，且供应商启动数为 0。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_chat_session_migration.py -q`，确认失败。
- [ ] 暴露 `inspect_legacy(scope_context)` 返回 `none/unique/ambiguous` 与仅服务端使用的来源描述。唯一归属须来自已存 scope 元数据，不以当前页面、目录枚举顺序、最近更新时间猜测。迁移用事务标记完成，重复启动不重复导入。供应商 ID 与 replay 历史保持原值。
- [ ] ambiguous 状态通过手动 clear 接口确认后释放，不删除旧原件，不把旧聊天重新注入新代次。项目未曾有历史且不存在归属冲突时允许正常首次创建；其他项目的明确旧历史不阻止这个项目。
- [ ] 运行迁移与存储/API 测试，确认 clear 之后迁移检查不会再次阻塞；提交任务变更。

## 任务 7：整体验收与交付

- [ ] 运行有针对性的后端套件：

```bash
.venv/bin/python -m pytest tests/test_chat_routing.py tests/test_chat_session_store.py tests/test_chat_session_manager.py tests/test_chat_hermes_session.py tests/test_chat_session_api.py tests/test_chat_session_migration.py tests/test_chat_cli_agent.py tests/test_chat_dispatcher.py tests/test_chat_service_user_agent_scope.py tests/test_chat_route_prewarm.py tests/test_security_chat_authorization.py tests/team/test_chat_runtime_settings.py tests/team/test_chat_isolation.py tests/test_api_model_gateway_task_runtime.py -q
```

- [ ] 运行前端相关 Vitest 和 `pnpm exec tsc -b`。测试失败先区分本任务回归与工作区已有改动，记录确切命令和结果；不为通过而修改不相关断言。
- [ ] 使用隔离测试用户/项目验证 A 发消息→B 发消息→返回 A→服务重启→返回 A，断言各自逻辑 ID 与 native 模式供应商 ID 稳定。测试“清除 A”时保留 A 文档/资产/任务及 B 会话。不清除用户真实项目的旧会话来充当测试。
- [ ] App 检查糯米助手配置可见且保存有效、清除确认文案和迁移提示可操作；一次真实多轮聊天验证正确执行路由与上下文延续。保存截图和不含凭据的结果记录。
- [ ] 审查只涉及本任务的 diff，确认没有后台作者/QC 会话合并，没有 Hermes 被放入普通后台任务，没有 resume 失败暗新建、模型改变暗换历史、跨项目 cancel 或旧代次写回。
- [ ] 在本计划勾选已完成步骤，交付代码、测试结果及任何真实限制。未完成项不得以“测试通过”替代。

## 规格覆盖映射

| 规格要求 | 实施任务 |
|---|---|
| 统一入口、旧配置兼容、项目覆盖 | 1、5 |
| 持久身份、原生/重放区分、重启恢复 | 2、3 |
| 串行、scope 隔离、晚到结果和工具保护 | 2、3、4 |
| 手动清除、幂等、保留项目数据 | 2、4、5 |
| 旧数据归属与恢复失败 | 3、6 |
| 前后端与真实 App 验收 | 7 |
