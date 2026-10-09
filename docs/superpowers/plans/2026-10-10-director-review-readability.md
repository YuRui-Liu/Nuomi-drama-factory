# 导演分镜审核可读性改版实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 在当前会话逐任务实施。步骤使用复选框跟踪进度；用户若明确选择子代理执行，则改用 superpowers:subagent-driven-development。

**目标：** 把导演审核页改为镜头阅读优先，改善动作、叙事意图和素材的可读性，保留原审核与生产行为。

**架构：** 使用现有 DirectorReviewWorkbench 管理版本、栏目和 mutation。GroupEditor 展示连续镜头卡片，CreativeReviewPanel 分离摘要、问题和逐镜导演细节。页面复用现有查询，无新增后端字段或翻译服务。

**技术栈：** React 19、TypeScript、Tailwind CSS、已有 UI 组件、TanStack Query、Vitest、Testing Library。

**规格：** `docs/superpowers/specs/2026-10-10-director-review-readability-design.md`，用户已于 2026-10-10 确认。

## 工作区和文件责任

执行前再次读取 git diff，以下部分文件已有未提交修改，必须在其基础上增量修改。当前 checkout 正在运行本地软件，验收需要使用该运行环境。若选择隔离 worktree，必须先保留并迁移这些文件的当前工作区内容，不能只从 HEAD 开始而丢失现有修复。不要切换当前分支或把其他人的修改混入提交。

- 修改 `frontend/src/components/episode/director-review/director-review-workbench.tsx`：栏目切换、版本显示、整体布局。
- 修改 `frontend/src/components/episode/director-review/group-editor.tsx`：镜头卡片、素材标签、结构菜单。
- 修改 `frontend/src/components/episode/director-review/creative-review-panel.tsx`：提取可复用逐镜详情、审核摘要和完整问题视图。
- 修改 `frontend/src/components/episode/director-review/activation-bar.tsx`：禁用原因和底栏布局，保留权限条件。
- 修改 `frontend/src/components/episode/director-review/asset-migration-panel.tsx` 与 `revision-comparison.tsx`：长内容换行和小屏布局，不改业务逻辑。
- 创建 `frontend/src/components/episode/director-review/review-presentation.ts`：版本编号、素材类型标签、未解决问题计数等纯展示工具。
- 修改 `frontend/src/__tests__/components/episode/director-review-workbench.test.tsx`：栏目、原有操作、状态回归。
- 创建 `frontend/src/__tests__/components/episode/director-review-presentation.test.ts`：问题状态和稳定编号。

所有 frontend 命令在 `frontend/` 下执行。不添加依赖。

## 任务 1：建立回归基线和展示工具

- [ ] 阅读上述组件和已有测试的完整当前版本，记录目标文件 git diff。运行基线：

```bash
pnpm test src/__tests__/components/episode/director-review-workbench.test.tsx
```

预期现有测试通过；现存失败先记录并判断与本任务的关系。

- [ ] 编写问题计数、稳定版本编号的测试：

```ts
import { describe, expect, it } from "vitest";
import { countUnresolvedIssues, versionNumbers } from "@/components/episode/director-review/review-presentation";

describe("审核展示", () => {
  it("不把作者声明修改当作 QC 已解决", () => {
    expect(countUnresolvedIssues([{ status: "resolved" }, { status: "proposed_change" }, { status: "persists" }])).toBe(2);
  });
  it("版本编号按创建时间固定，而非依赖当前选择", () => {
    const numbers = versionNumbers([
      { revision_id: "new", created_at: "2026-10-10T02:00:00Z" },
      { revision_id: "old", created_at: "2026-10-10T01:00:00Z" },
    ]);
    expect(numbers.get("old")).toBe(1);
    expect(numbers.get("new")).toBe(2);
  });
});
```

- [ ] 运行新测试，确认工具尚未实现导致失败：

```bash
pnpm test src/__tests__/components/episode/director-review-presentation.test.ts
```

- [ ] 实现纯展示工具：

```ts
export const assetKindLabels = {
  character_identity: "人物造型",
  character_state: "人物状态",
  scene_base: "场景",
  scene_state: "场景状态",
  prop: "道具",
} as const;

export function countUnresolvedIssues(issues: readonly { status: string }[]) {
  return issues.filter((item) => item.status !== "resolved").length;
}

export function versionNumbers(revisions: readonly { revision_id: string; created_at: string }[]) {
  return new Map([...revisions]
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.revision_id.localeCompare(b.revision_id))
    .map((revision, index) => [revision.revision_id, index + 1]));
}
```

未知或缺失审核状态用现有「等待独立 QC」文字表达，不能仅因问题数组为空显示通过。

- [ ] 重跑工具测试确认通过。只提交属于本任务的新增展示工具与测试；若目标文件含其他未提交内容，暂不对整文件提交。

## 任务 2：镜头卡片与导演细节

- [ ] 从 CreativeReviewPanel 提取 `ShotCreativeDetails` 导出，参数保持实际类型：

```ts
import type { ShotPlan } from "@/lib/queries/director-plans";

export function ShotCreativeDetails({ shot }: { shot: ShotPlan }) {
  return <details className="mt-4 border-t border-border pt-3">
    <summary className="cursor-pointer text-sm font-medium">导演细节</summary>
    <dl className="mt-4 grid gap-4 sm:grid-cols-2">
      {Object.entries(dimensions).map(([key, title]) => {
        const decision = shot.creative?.[key] as Record<string, unknown> | undefined;
        return <div key={key} className="min-w-0 break-words">
          <dt className="text-sm font-semibold">{title}</dt>
          <dd className="mt-1 text-sm leading-7">{typeof decision?.purpose === "string" ? decision.purpose : "缺少创作决定"}</dd>
          {decision && <dd className="mt-1 text-sm leading-6 text-muted-foreground">{describe(decision)}</dd>}
          {decision?.source != null && <dd className="mt-1 text-xs leading-6 text-muted-foreground">依据：{describe(decision.source)}</dd>}
        </div>;
      })}
    </dl>
  </details>;
}
```

`dimensions` 和 `describe` 复用同文件现有定义。旧创作契约镜头至少显示 shot_size、camera_angle、composition、camera_motion；用现有值，不补造参数。

- [ ] GroupEditor 中将 flex 包裹的小文本块改为连续 `article` 卡片，正文与按钮分离。使用以下核心展示结构，外层保留 groups/shots 原序和已有结构命令：

```tsx
<article aria-label={`镜头 ${shot.id}`} className="min-w-0 rounded-xl border border-border bg-muted/20 p-5">
  <header className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
    <strong>镜头 {shotIndex + 1}</strong>
    <span className="text-sm text-muted-foreground">{group.scene_anchor} · {group.time_anchor}</span>
    <span className="ml-auto text-xs text-muted-foreground">{shot.duration_seconds} 秒</span>
  </header>
  <p className="mt-4 text-xs font-medium text-muted-foreground">画面与动作</p>
  <p className="mt-2 max-w-[72ch] whitespace-pre-wrap break-words text-base leading-8">{shot.action}</p>
  {shot.intent?.narrative_purpose && <div className="mt-4"><p className="text-xs text-muted-foreground">叙事意图</p><p className="mt-1 text-sm leading-7">{shot.intent.narrative_purpose}</p></div>}
  {shot.intent?.audience_attention && <div className="mt-3"><p className="text-xs text-muted-foreground">观众注意</p><p className="mt-1 text-sm leading-7">{shot.intent.audience_attention}</p></div>}
  <ShotCreativeDetails shot={shot} />
</article>
```

所需素材为可换行中文标签，使用 assetKindLabels。entity_key 原值完整保留，使用 `break-words`，evidence_source_ids 放进原生 details。对白来源标识可在详情展示；当前没有独立对白正文接口，不从 action 猜测对白。原文对照若无法复用现有数据，不增加假入口。

- [ ] 保留原 reorder_groups、merge_adjacent_groups、split_group、move_shot 四种命令及 disabled 规则。组级结构按钮放到原生 details 或已有 dropdown 中，镜头操作位于卡片底部。
- [ ] 将现有操作测试改为先展开对应结构入口再点击。补充「默认正文可见、导演细节可展开、完整长动作不被截断」的行为测试。不要写仅检查 Tailwind class 的测试。
- [ ] 运行 workbench 测试，修复只由这次改版引入的失败。

## 任务 3：栏目、版本历史和审核摘要

- [ ] 添加栏目状态，默认镜头内容：

```tsx
type ReviewTab = "shots" | "issues" | "assets" | "changes";
const [tab, setTab] = useState<ReviewTab>("shots");
const tabs: { id: ReviewTab; label: string }[] = [
  { id: "shots", label: "镜头内容" },
  { id: "issues", label: "审核问题" },
  { id: "assets", label: "素材复用" },
  { id: "changes", label: "版本变化" },
];
```

用已有 Tabs 组件实现键盘切换和 tab/tabpanel 关联；保留所有 hooks 无条件调用。版本变化内容在无对比数据时显示「没有可比较的版本」，素材迁移保持原真实空状态。

- [ ] 用 versionNumbers 生成短版本名，完整 ID 在详情中显示，并用 Clipboard API 提供复制按钮；复制失败使用已有 toast 告知，不显示成功状态。选择版本的事件仍传 revision_id。
- [ ] 在镜头栏目放 GroupEditor 和右侧审核摘要，使用 `grid-cols-1 xl:grid-cols-[minmax(0,1fr)_300px]`；正文最大宽度约 850px。右侧提供当前状态、问题数量、最多三条完整可换行问题标题以及「查看全部问题」按钮。
- [ ] CreativeReviewPanel 增加 `showShotDetails?: boolean`，默认保留旧行为，审核问题栏目传 false，避免重复显示全部逐镜参数。QC 历史改为 details，完整原文保持，复查任务持久化状态和错误展示继续复用当前 workbench 代码。
- [ ] QC 复查按钮在摘要和审核栏目复用同一动作，继续使用 pending/reviewRunning 禁用规则，避免重复提交。
- [ ] 为栏目行为写测试：默认动作可见；切换审核问题可看到验收条件；切换素材栏目可调用 migrate；版本变化可看到比较信息；旧版无创作反馈时不误显示通过。

```tsx
fireEvent.click(screen.getByRole("tab", { name: /素材复用/ }));
fireEvent.click(screen.getByRole("button", { name: /接受/ }));
expect(mocks.migrate).toHaveBeenCalled();
```

测试使用已有迁移 fixture 的实际按钮名称。旧测试中依赖素材和比较默认显示的断言移到相应栏目后执行。

- [ ] 运行工具与 workbench 测试，确认仍保留独立 QC 失败重开显示、在运行状态下防重复提交等已有测试。

## 任务 4：激活说明和小屏可读性

- [ ] ActivationBar 保留原 disabled 判定，新增原因按以下优先级计算，不增加规则：

```ts
const blockedReason = disabled
  ? "操作处理中，请稍候"
  : !revision.validation_report.passed
    ? "结构校验尚未通过"
    : revision.creative_contract_version === 1 && revision.creative_review_status !== "passed"
      ? "独立 QC 尚未通过"
      : null;
```

只在当前状态存在相应激活按钮时显示原因。已激活状态保留进入分镜生图入口。恢复按钮使用短版本名或放入恢复菜单，但保持原 aria-label 与 revision ID 的关联。

- [ ] 版本侧栏在窄屏改为顶部可换行的版本选择或可折叠版本区，不挤占正文。主滚动区 `min-w-0`，长值 `break-words`，必要的原始字段使用 `overflow-wrap:anywhere`。底栏 flex-wrap，底部留白不少于实际底栏高度。
- [ ] AssetMigrationPanel 长 ID 移进详情，保持 score、confidence、decision 真实显示；结构比较区域补齐长内容换行，不改变差异含义。
- [ ] 添加激活行为回归：结构失败和 QC 未通过均禁用，状态 active 可进入生图，旧契约沿用原条件。
- [ ] 在 frontend 执行：

```bash
pnpm test src/__tests__/components/episode/director-review-workbench.test.tsx src/__tests__/components/episode/director-review-presentation.test.ts
pnpm exec tsc -b --pretty false
```

目标测试应通过，TypeScript 无本次引入的新错误。若 tsc 修改被 gitignore 的构建缓存，无需上报；若写入已跟踪生成文件，应记录并检查差异，不擅自丢弃。

## 任务 5：真实浏览器验收与交付

- [ ] 打开正在运行的项目第1集导演审核，分别检查 1440px、1024px 和 390px 视口；缺少某种审核状态时使用已有测试 fixture 验证，不修改真实导演数据来造状态。
- [ ] 核验动作完整可读、叙事顺序正确、正文默认不被导演参数淹没、长英文不溢出、底栏不遮挡、Tab 键可访问栏目和结构操作。
- [ ] 保存改版截图到忽略目录 `.superpowers/brainstorm/`。浏览器检查避免点击真实激活、放弃、重新规划等有生产副作用的按钮；这些行为由组件测试验证。
- [ ] 汇总 git diff，确认无 API、模型、剧本或素材数据变更，未覆盖已有修改。只对可明确归属于本任务的改动提交；交付结果链接、截图、测试结果和实际限制。

## 自检

规格中的正文阅读、审核分区、参数折叠、中文素材标签、版本编号、窄屏、激活约束和错误状态均有对应任务。ShotPlan、DirectorPlanRevision、mutation 参数沿用现有类型。明确排除未支持的独立对白生成、自动 QC 翻译和推测性问题定位。计划没有占位任务。
