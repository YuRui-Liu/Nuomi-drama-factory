# 项目制作成本监控实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现项目成本总览、每日及累计曲线、渠道和媒体拆分、可追溯的调用账本，以及版本化估算规则和独立订阅管理。

**架构：** 新增独立成本域：渠道适配器只提供事实，计价器计算费用，SQLite 账本处理幂等与修订，查询服务提供同一快照的聚合。沿用现有 FastAPI、项目授权及 React Query；不改变现有积分预占和退款行为。

**技术栈：** Python 3.11 / Decimal / SQLite / Pydantic / FastAPI / pytest；React / TypeScript / TanStack Router / React Query / Vitest；趋势图用 SVG，不添加图表依赖。

**依据：** `docs/superpowers/specs/2026-09-23-project-cost-monitoring-design.md`，用户已于当前会话批准书面规格。设计提交为 `a6c4c4e`。原型是视觉参考，不能直接将模拟数据带入产品。

---

## 工作区与执行约束

当前是普通 checkout，分支 `fix/codex-identity-qc`，包含角色语音相关未提交改动。本轮仅编写计划，不改产品代码。执行前按 using-git-worktrees 技能确认隔离方式；不能 stash、覆盖或提交现有他人改动。若使用 worktree，从包含设计提交的当前 HEAD 建立，不自动复制语音改动；如执行期间相关改动已合入，按最新接口调整。

所有路径均相对于 `/Users/liuyuxiang05/Liu/Nuomi-drama-factory`（隔离执行时相对于新工作区）。编辑使用 apply_patch；若使用 shell 修改文件，必须执行 AGENTS.md 的逐文件 before/after 记录。只提交本任务列出的文件。

执行约定：每个任务按红灯测试、实现、绿灯验证、定向提交推进。测试依赖用项目现有 uv / pnpm 环境；不发起真实付费生成来验证。单个任务如果超过一个开发检查点，按其编号步骤分别提交。

## 文件职责

新增后端：
- `src/novelvideo/costs/__init__.py`：成本域包入口。
- `src/novelvideo/costs/models.py`：调用、计价项、费用结果和修订的 Pydantic 类型。
- `src/novelvideo/costs/pricing.py`：Decimal 计价、单位换算、规则匹配。
- `src/novelvideo/costs/store.py`：独立 SQLite 表、事务、事件去重及快照查询。
- `src/novelvideo/costs/service.py`：调用生命周期、补算与恢复协调。
- `src/novelvideo/costs/context.py`：显式项目/资源上下文及异步上下文传递。
- `src/novelvideo/costs/providers.py`：三个渠道的白名单计费事实规范化。
- `src/novelvideo/costs/queries.py`：统一分舍入、趋势、分类和完整性。
- `src/novelvideo/api/routes/project_costs.py`：薄路由，复用项目权限。

已有接入点：
- `src/novelvideo/media_capabilities/runtime/executor.py`：RunningHubExecutor.step 的 submit/query 分支。
- `src/novelvideo/media_capabilities/runtime/runninghub_client.py`：规范化扣费证据；不假设未提供的字段存在。
- `src/novelvideo/media_capabilities/runtime/grsai_execution.py`：execute_grsai_generation。
- `src/novelvideo/media_capabilities/image/grsai.py`：GrsaiSnapshot 计费白名单字段。
- `src/novelvideo/media_capabilities/image/pipeline.py`：独立 submit 路径，防止漏记。
- `src/novelvideo/knowledge_runtime/codex_process.py` 与 `src/novelvideo/knowledge_runtime/codex.py`：Codex 请求边界及用量。
- `src/novelvideo/text_task_runtime/runtime.py`：文本调用归属传递。
- `src/novelvideo/api/__init__.py`：注册成本路由。

新增前端：
- `frontend/src/types/project-costs.ts`：API 契约。
- `frontend/src/lib/queries/project-costs.ts`：快照、明细、配置与补算 queries/mutations。
- `frontend/src/routes/_app/projects.$project/costs.lazy.tsx`：页面路由。
- `frontend/src/components/costs/project-cost-page.tsx`：页面组合。
- `frontend/src/components/costs/cost-summary.tsx`：卡片和完整性提示。
- `frontend/src/components/costs/cost-trend.tsx`：SVG 曲线和键盘交互。
- `frontend/src/components/costs/cost-breakdown.tsx`：交叉表及媒体构成。
- `frontend/src/components/costs/cost-entries.tsx`：分页明细及详情。
- `frontend/src/components/costs/cost-rules.tsx`：规则版本与补算预览。
- `frontend/src/components/costs/cost-subscriptions.tsx`：订阅展示与编辑。
修改 `frontend/src/components/layout/project-navigation-routes.ts`、`frontend/public/locales/{zh,en}/translation.json`；通过路由生成器更新 `frontend/src/routeTree.gen.ts`，不手改生成文件。

## 任务 1：记录入口覆盖和契约

**文件：** 新建 `docs/superpowers/plans/project-cost-provider-coverage.md`、`tests/costs/test_models.py`、`src/novelvideo/costs/models.py`。

- [ ] 搜索所有提交入口，记录“入口函数→实际发送函数→是否经过公共适配器→项目 ID 来源→attempt 来源→已有用量/扣费字段”。复核 TTS 的直接调用及图片 pipeline，不仅检查通用 executor。
```bash
rg -n 'RunningHubClient|GrsaiClient|execute_grsai_generation|\.submit\(|codex' src/novelvideo/media_capabilities src/novelvideo/knowledge_runtime src/novelvideo/text_task_runtime
```
对当前不支持的渠道/媒体组合记录“不支持”，禁止凭原型添加虚构能力。现有解析器未暴露的费用字段记录“未暴露，使用估算/未计价”，不能直接将原始响应任意键当费用。

- [ ] 定义状态枚举和费用验证契约，先写以下失败测试。
```python
from pydantic import ValidationError
import pytest
from novelvideo.costs.models import CostValue

def test_unknown_cost_is_not_zero():
    value = CostValue(status="unpriced", amount_micros=None, reason="missing_rule")
    assert value.amount_micros is None
    with pytest.raises(ValidationError):
        CostValue(status="confirmed", amount_micros=None)
```
CostValue 字段固定为 status、amount_micros、reason；status 为 confirmed/estimated/unpriced/subscription_covered。confirmed/estimated 必须有非负整数微元；另外两种必须为 None。零值只允许存在明确零扣费或零价格依据时写入。

- [ ] 运行 `uv run pytest tests/costs/test_models.py -q`，确认缺少类型而失败；使用 Pydantic model_validator 实现上述约束，再运行同命令通过。
- [ ] 扩展调用模型：attempt_id、project_id、provider、account_id、model、media_type、occurred_at、task_id、resource_id、external_id、execution_status、submission_status、usage、usage_source。时间要求带时区；媒体支持 image/audio/video/text。
- [ ] 仅提交本任务文件：`git commit -m "feat(costs): define cost facts and provider coverage"`。

## 任务 2：金额与规则计价

**文件：** 新建 `src/novelvideo/costs/pricing.py`、`tests/costs/test_pricing.py`；扩展 models.py。

- [ ] 写失败测试，冻结基数、计费步长和最低量的定义。
```python
from decimal import Decimal
from novelvideo.costs.pricing import charge_micros

def test_seconds_round_up_to_billing_step():
    assert charge_micros(
        quantity=Decimal("10.1"), unit_price=Decimal("1.2"),
        basis=Decimal("1"), step=Decimal("1"), minimum=Decimal("0"),
        cny_rate=Decimal("1"),
    ) == 13_200_000
```
- [ ] 运行 `uv run pytest tests/costs/test_pricing.py -q`，预期导入失败。
- [ ] 实现计费核心，输入和配置均验证非负，basis/step/rate 大于零：
```python
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

def charge_micros(*, quantity, unit_price, basis, step, minimum, cny_rate):
    billed = (max(quantity, minimum) / step).to_integral_value(
        rounding=ROUND_CEILING
    ) * step
    return int((billed / basis * unit_price * cny_rate * Decimal("1000000"))
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```
- [ ] 添加按张/次/字符/Token/积分参数化用例，输入输出 Token 为两个计价项求和；缺少所需 quantity 或换算率返回 unpriced，不传默认零。
- [ ] 实现规则选择：渠道、账户、媒体、模型/工作流及规格逐项匹配，按具体条件数量排序；同优先级重叠返回冲突，版本按调用发生时间选择。
- [ ] 运行 `uv run pytest tests/costs/test_models.py tests/costs/test_pricing.py -q`，通过后提交 `feat(costs): add versioned decimal pricing`。

## 任务 3：事务账本与幂等修订

**文件：** 新建 store.py、`tests/costs/test_store.py`；参考 media_capabilities/store.py 的连接配置。

- [ ] 写重复事件、重试独立、跨账户同名请求和估算转实际的失败测试。Store 契约为 CostStore(path)、create_attempt(facts)、record_cost(attempt_id,event_id,value)、get_cost(attempt_id)、list_revisions(attempt_id)。
```python
def test_replayed_event_does_not_duplicate_revision(tmp_path):
    from novelvideo.costs.store import CostStore
    from novelvideo.costs.models import CostValue
    store = CostStore(tmp_path / "costs.sqlite3")
    store.create_attempt(dict(attempt_id="a", project_id="p",
        provider="grsai", account_id="account", model="demo",
        media_type="image", occurred_at="2026-09-23T00:00:00Z",
        submission_status="submitted", execution_status="succeeded",
        usage={"items": "1"}, usage_source="request"))
    value = CostValue(status="confirmed", amount_micros=3_200_000)
    store.record_cost("a", "event-1", value)
    store.record_cost("a", "event-1", value)
    assert len(store.list_revisions("a")) == 1
    assert store.get_cost("a").amount_micros == 3_200_000
```
- [ ] 运行 `uv run pytest tests/costs/test_store.py -q`，预期模块缺失。
- [ ] 用 configure_sqlite_connection 建立连接；创建 attempts、current_costs、cost_revisions、price_versions、subscriptions、coverage 表。attempt 主键独立于供应商 ID；事件唯一约束为 (attempt_id,event_id)。
- [ ] 在 BEGIN IMMEDIATE 事务内去重事件、验证状态优先级、写修订及当前费用；confirmed 拒绝迟到 estimated；渠道退款以确认净额修订。不要用产品积分退款触发渠道费用变动。
- [ ] 加入事务失败回滚、并发重复回调和重新打开数据库持久化用例；运行测试通过，提交 `feat(costs): persist idempotent cost ledger`。

## 任务 4：生命周期、上下文及故障恢复

**文件：** 新建 context.py、service.py、`tests/costs/test_service.py`。

- [ ] 定义 CostContext(project_id,task_id,resource_id,media_type)；通过 ContextVar 配合 token.reset 防止协程间串项目。服务接口固定为 prepare(facts)、submitted(attempt_id,external_id)、observe(attempt_id,event_id,facts)、mark_submission_unknown(attempt_id)。
- [ ] 写两个并行项目上下文测试、prepare 写入失败时发送次数为零、提交超时保留未知状态的测试；用 AsyncMock 断言外部 submit 调用数。
```python
from contextvars import ContextVar
from contextlib import contextmanager

current_cost_context = ContextVar("current_cost_context", default=None)

@contextmanager
def cost_context(value):
    token = current_cost_context.set(value)
    try:
        yield
    finally:
        current_cost_context.reset(token)
```
- [ ] 运行 `uv run pytest tests/costs/test_service.py -q`，确认失败后实现 prepare→submit→submitted→observe 顺序；轮询只 observe。
- [ ] 对已提交但记账失败保留数据库意图；恢复仅查询已知外部 ID，不调用 submit。未知提交状态显示缺口，不自动创建重试。
- [ ] 运行服务和账本测试通过，提交 `feat(costs): coordinate request accounting and recovery`。

## 任务 5：渠道适配与入口接入

**文件：** providers.py；文件清单中的 executor、客户端、grsai_execution、image/pipeline、Codex 进程与上层上下文；`tests/costs/test_provider_capture.py`。

- [ ] 按任务 1 覆盖表逐入口编写 mock 回归：成功返回用量、无费用字段、失败扣费、重复查询、重试、多项目。每新增一个真实发送入口，增加一条覆盖用例。
- [ ] 运行 `uv run pytest tests/costs/test_provider_capture.py -q`，预期调用没有账本记录。
- [ ] RunningHub 以公共客户端 submit/query 为唯一记账入口；上层 executor 只传递既有 attempt.id、账户和项目上下文。直接 pipeline/TTS 路径通过同一客户端自动覆盖，无稳定 attempt ID 时在实际提交前生成，禁止上下层双重记账。
- [ ] Grsai 以公共客户端 submit/query 为唯一记账入口，execute_grsai_generation 和 image/pipeline 传递归属信息；query 和 download 不新建生成费。账户 ID 从 runtime configuration 注入客户端，不能由密钥生成可展示的标识。
- [ ] Codex 在实际执行边界记录调用，不按输出文件个数计次；只有明确订阅配置才设 subscription_covered，文本 usage 归 text。缺失用量不制造 Token 数。
- [ ] 适配器只保留经过当前解析器和脱敏 fixture 证明的费用/用量字段，证据不足走估算或未计价；不在这一步联网执行供应商调用。
- [ ] 重跑入口覆盖搜索，对每个匹配标明接入/非生成/未支持理由；运行成本测试与受影响现有 provider 测试，提交 `feat(costs): capture provider generation attempts`。

## 任务 6：一致性汇总与时间曲线

**文件：** queries.py、`tests/costs/test_queries.py`。

- [ ] 定义 CostQueries(store).snapshot(project_id,now) 返回 summary、trend、breakdown、subscriptions、coverage、snapshot_at；一次只读事务查询，不让前端拼接不同版本的总额。
- [ ] 写失败测试：两条 confirmed/estimated 记录合计、逐条分舍入、北京时间跨日、未计价与订阅排除、缺失日期、退款回写、跨项目隔离。
```python
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from zoneinfo import ZoneInfo

def display_cents(micros: int) -> int:
    return int((Decimal(micros) / Decimal(10000))
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def accounting_day(occurred_at: str) -> str:
    return datetime.fromisoformat(occurred_at.replace("Z", "+00:00")) \
        .astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
```
- [ ] 运行 `uv run pytest tests/costs/test_queries.py -q`，预期失败。
- [ ] 逐条转展示分后聚合；trend 点字段固定 date、confirmed_cents、estimated_cents、total_cents、unpriced_count、complete。缺失区间数值为 null；累计仅累计已知金额，complete=false 持续提示缺口。
- [ ] 断言最后累计、分类之和、summary.total_cents 一致；空项目与全部未计价分别给 empty 和 unpriced_only 状态。
- [ ] 通过后提交 `feat(costs): aggregate project totals and spending trends`。

## 任务 7：规则、订阅、补算与授权 API

**文件：** project_costs.py、api/__init__.py、service.py、`tests/test_api_project_costs.py`、`tests/costs/test_reprice.py`。

- [ ] 复用 routes/tasks.py 的 get_api_user、get_project_access、resolve_project_context 授权方式，先写未授权、跨项目 entry ID 返回拒绝、非法规则返回 422、冲突版本返回 409 测试。
- [ ] 固定项目 API：GET /projects/{project}/costs/snapshot；GET /entries（channel/media/status/cursor/limit）；GET /entries/{attempt_id}。规则/订阅管理置于 /cost-settings，沿用设置权限；不允许项目只读权限修改全局设置。
- [ ] 定义规则版本创建/列表/停用、订阅保存/列表；DTO 不接受 arbitrary Python 表达式，不返回原始响应或密钥。
- [ ] 补算 POST /projects/{project}/costs/reprice-preview 返回 preview_id、规则版本、旧新金额及影响记录；POST /reprice-apply 提交 preview_id。预览绑定项目、调用版本及规则版本，状态变化时 409 重新预览；只更新未计价记录。
- [ ] 用测试证明过期预览不能覆盖刚到达的 confirmed 费用；重复 apply 不重复修订；价格改动不重算历史。
- [ ] 运行 `uv run pytest tests/test_api_project_costs.py tests/costs/test_reprice.py -q`，由失败到通过后提交 `feat(api): expose project costs and pricing settings`。

## 任务 8：项目页面和数据查询

**文件：** types/project-costs.ts、queries/project-costs.ts、costs.lazy.tsx、project-cost-page.tsx、cost-summary.tsx、cost-breakdown.tsx、cost-entries.tsx；导航及双语资源；测试 `frontend/src/__tests__/components/costs/project-cost-page.test.tsx`。

- [ ] 使用 MSW 给快照和明细提供 fixture，先写总额、项目切换、交叉表点击后明细筛选、全部未计价不显示零的测试。
- [ ] 运行 `pnpm --dir frontend test src/__tests__/components/costs/project-cost-page.test.tsx`，确认缺少页面失败。
- [ ] 查询 key 包含 project ID 和明细筛选；snapshot 每 15 秒轮询且 refetchIntervalInBackground=false。页面不可见时禁用轮询；配置修改使 snapshot 和 entries 失效。
- [ ] 页面以快照状态展示卡片，交叉表只设置明细筛选，不改快照 query key。使用整数分格式化显示：
```ts
export function formatCny(cents: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency", currency: "CNY",
  }).format(cents / 100);
}
```
- [ ] 加入成本导航、项目切换记忆及中英文词条；执行 `pnpm --dir frontend build:ce` 生成并验证路由，不手改 routeTree.gen.ts。
- [ ] 测试通过后提交 `feat(ui): add project cost overview and ledger`。

## 任务 9：可访问趋势图

**文件：** cost-trend.tsx；`frontend/src/__tests__/components/costs/cost-trend.test.tsx`。

- [ ] 写三日 fixture（48、96、132 元），断言每日末点 132、累计末点 276；未计价数量提示、缺口不连线、键盘聚焦读数和日/累计按钮切换。
- [ ] 运行 `pnpm --dir frontend test src/__tests__/components/costs/cost-trend.test.tsx`，确认失败。
- [ ] 使用 viewBox 的 SVG；实线总额及已确认、虚线估算。将 null 分割为多个 polyline，禁止跨缺口连线。累计读数来自服务端口径，不把未知自动补成零。
- [ ] 总额数据点提供 tabIndex=0、日期与金额 aria-label，onFocus/onMouseEnter/onClick 更新详情；按钮使用 aria-pressed。曲线容器可横向滚动，长周期每点保留焦点，横轴只减少标签。
- [ ] 无数据、全部订阅、全部未计价均显示与总览一致的文字；不能只留空白坐标轴。
- [ ] 测试通过后提交 `feat(ui): visualize daily and cumulative project spending`。

## 任务 10：计价配置与订阅交互

**文件：** cost-rules.tsx、cost-subscriptions.tsx、queries/project-costs.ts；`frontend/src/__tests__/components/costs/cost-settings.test.tsx`。

- [ ] 写创建价格版本、非法数值、规则冲突、订阅金额缺失、补算预览再应用、过期预览重新加载的交互测试。
- [ ] 运行 `pnpm --dir frontend test src/__tests__/components/costs/cost-settings.test.tsx`，确认失败。
- [ ] 表单按计价单位展示必填字段，不填默认为未知而不是零。保存显示规则生效时间，历史版本只读；订阅区显示账户周期和本项目用量，绝不加入项目总额。
- [ ] 补算按钮先请求预览，展示具体影响及预计金额；用户点击应用后提交 preview_id，成功刷新账本与快照。409 留在预览界面并说明已变化。
- [ ] 测试通过后提交 `feat(ui): manage pricing versions and shared subscriptions`。

## 任务 11：回填、故障场景和整体验收

**文件：** 新增 `src/novelvideo/costs/backfill.py`、`tests/costs/test_backfill.py`；更新覆盖文档与用户说明。

- [ ] 编写已有稳定请求/用量记录回填测试，同一记录重跑不增加费用；只有最终产物而无调用证据不建立虚构账本。
- [ ] 运行 `uv run pytest tests/costs/test_backfill.py -q`，确认失败；实现保守回填，仅接受已证明的记录结构，以稳定来源 ID 去重，记录 coverage 起点和未知历史缺口。
- [ ] 对接入表逐行核查 provider fixture，禁止剩余真实发送路径未分类；验证并发、重启恢复、失败扣费、退款修订、不完整历史与敏感字段过滤。
- [ ] 执行最终后端和前端测试：
```bash
uv run pytest tests/costs tests/test_api_project_costs.py tests/test_api_model_credit_cost.py -q
pnpm --dir frontend test src/__tests__/components/costs
pnpm --dir frontend build:ce
git diff --check
```
预期：新增成本用例及原积分报价回归全通过，构建退出码为零；失败必须区分基线问题与本次变更，不宣称全部通过。
- [ ] 本地浏览器检查真实页面（使用测试数据库）：项目切换、筛选与详情、每日/累计、键盘焦点、窄屏、配置保存、补算及刷新失败。截图包含数据完整性提示，检查总额对账；不调用供应商实际生成。
- [ ] 逐项核对设计规格第 10 节九条验收要求，记录每条对应测试/截图。提交 `test(costs): verify accounting coverage and recovery`。
- [ ] 最终报告实现路径、验证命令结果、实际扣费已支持的字段范围及估算回退范围，明确历史覆盖限制。

## 计划自检与执行顺序

### 执行记录

- 任务 1 已完成：费用与调用模型、渠道入口覆盖表；33 项测试，规格与质量审查通过。提交 39ce557、03721d3。
- 任务 2 已完成：版本化计价规则、缺失依据回退、精确舍入；模型与价格合计 73 项测试，规格与质量审查通过。提交 1bea38a、40fa108、d9b9747。
- 任务 3 已完成：事务账本、幂等事件、不可变归属、规则与订阅持久化、带用量快照的费用修订；13 项存储测试，规格与质量审查通过。提交 b1c416a、2900868、f0f8175、19984ea。
- 任务 4 已完成：隔离调用上下文、提交生命周期、原币证据、原子用量观察及迟到事件保护；成本域合计 102 项测试，规格与质量审查通过。提交 24d00ce、19b2da8、5ca9096。
- 任务 5 已完成：三个渠道公共调用边界记账、具体媒体用量、测试账本隔离；成本及相关渠道回归合计 495 项通过，规格与质量审查通过。提交 c64442d。直接调用在记账失败且进程丢失后可能留下无法自动匹配的 pending 意图，查询页必须提示不完整；不猜测关联或自动重发。
- 任务 6 已完成：统一舍入汇总、北京时间每日与累计曲线、覆盖缺口、分页及原子审计详情；成本域 134 项测试通过，规格与质量审查通过。提交 808a01f、84e7990、4024cee。
- 任务 7 已完成：项目授权 API、管理配置审计、持久化补算预览与原子应用；API 与成本 152 项测试通过，规格与质量审查通过。提交 b506252、f3dd4ae。补算规则显式替换活动快照，后续轮询不退回旧规则；分类新增 priced_count 区分确认零与未提交。
- 任务 8 已完成：实际项目页、分类与明细、订阅概览、审计抽屉、导航与中英文、同步刷新；10 项页面测试与 5 项导航测试通过，TypeScript 与 CE 构建通过，规格与质量审查通过。提交 889fe82、f33418c、06782f1。浏览器已验证合成项目数据、分类筛选、详情、390px 无整体横向溢出。
- 任务 9 已完成：每日与累计 SVG 曲线、未知区间断线、逐日键盘和触摸读数、长周期横向查看；成本前端 17 项测试通过，TypeScript 与规格/质量审查通过。提交 17977c1。浏览器验证每日/累计切换，另验证空项目与全部待核算项目不显示零费用曲线。
- 任务 10 已完成：按权限加载价格与订阅表单、可读版本/周期、多计费项、补算预览与显式应用、冲突留存；成本前端 24 项测试与 TypeScript 通过，规格/质量审查通过。提交 018c8c6、9186959。浏览器完成规则新增及 ¥1.25 补算，总额从 ¥294.00 到 ¥295.25，订阅 ¥150.00 不变；另保存空金额订阅显示未登记。
- 任务 11 已完成：两类 H3 源库只读保守回填、按证据标记覆盖起点、去重及隔离、操作说明；22 项回填测试、成本/API/积分合计 200 项通过，另 371 项渠道回归通过；前端 63 项相关测试、TypeScript 与 CE 构建通过。提交 528eeab、9564e2e。规格、质量及最终集成审查均通过；浏览器验收详见 docs/operations/project-cost-verification.md。
- 隔离工作区已建立：feat/project-cost-monitoring。后端基线 26 项、渠道基线 92 项、前端基线 2 项与 TypeScript 检查通过。
- 本机 pnpm 会自动检查依赖并尝试重装共享软链接；执行时使用现有 node_modules/.bin/vitest、tsc、vite，避免改变共享依赖。

依赖顺序：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11。先账本后页面，不以演示图替代真实采集。

规格覆盖：页面对应 8–10；趋势及一致性对应 6、9；定价与订阅对应 2、7、10；数据边界和幂等对应 1、3–5；故障及权限对应 4、7、11；历史回填与验收对应 11。

实施时各任务新增的函数和 DTO 以本计划名称为边界，已有渠道接口保留兼容。若任务 1 发现新增发送路径，先把实际文件/函数和对应测试追加到覆盖表，再接入；不能把覆盖表当作免接入清单。实际完成情况以上述执行记录及测试证据为准。
