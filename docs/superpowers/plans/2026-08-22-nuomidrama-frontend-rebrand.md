# NuomiDrama 前端品牌改造实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将所有用户可见的 DramaClaw、SuperTale 和“虾系”品牌语言替换为 NuomiDrama 专业制片工具品牌，并落地已确认的 Editorial Black 视觉系统与稳定四字导航。

**架构：** 新建一个无外部依赖的 `BrandMark` 组件作为登录页和应用顶栏的唯一字标来源；路由和内部 section key 保持不变，只替换用户可见标签并把原有两级“虾画/虾集”切换重构为直接栏目导航。全局 token 负责统一 Editorial Black 颜色、圆角、焦点与控件尺寸，登录页在保留现有认证行为的前提下改为克制的真实产品展示。

**技术栈：** React 19、TypeScript 5.8、TanStack Router、Tailwind CSS 4、CSS Modules、i18next、Vitest、Testing Library。

---

## 文件结构与职责

- 创建 `frontend/src/components/brand/brand-mark.tsx`：统一渲染可缩放的 NuomiDrama 几何 N 标志和字标。
- 创建 `frontend/src/__tests__/components/brand/brand-mark.test.tsx`：锁定字标、紧凑模式及可访问名称。
- 修改 `frontend/index.html`：替换浏览器标题、描述和社交分享元数据。
- 修改 `frontend/src/index.css`：落地 Editorial Black 全局 token、字体、圆角、焦点环和 reduced-motion 基线。
- 修改 `frontend/src/components/layout/header.tsx`：使用统一字标并承载直接栏目导航。
- 修改 `frontend/src/components/layout/project-header-navigation.tsx`：删除用户可见的“虾画/虾集”双模式与第二行菜单，改为稳定一级导航。
- 修改 `frontend/src/components/layout/project-navigation-routes.ts`：继续保留现有内部 section/route 映射，只导出面向 UI 的导航清单。
- 修改 `frontend/src/__tests__/components/layout/header.test.tsx`：验证 NuomiDrama 顶栏和项目导航语义。
- 修改 `frontend/src/__tests__/components/layout/project-navigation-routes.test.ts`：验证所有栏目仍映射到原路由，不破坏深链。
- 修改 `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`：替换全部用户可见品牌、助手、栏目、帮助和社区文案。
- 修改 `frontend/src/__tests__/i18n/locales-json.test.ts`：增加禁用旧品牌词和确认新导航词的契约测试。
- 修改 `frontend/src/components/login/login-stage.tsx`、`frontend/src/components/login/login.module.css`：将登录首屏改为 Editorial Black 产品化布局。
- 修改 `frontend/src/components/login/cinematic/*.tsx`：将后续叙事页的旧品牌和“虾系”术语改为准确产品语言。
- 创建 `frontend/src/__tests__/components/login/login-stage.test.tsx`：验证品牌、主文案、CTA 与产品预览语义。
- 修改 `frontend/src/features/freezone/capabilities/candidate_capabilities.ts`、`frontend/src/components/settings/text-runtime-panel.tsx`：替换用户可见的候选能力和 Provider 展示名，保留底层 value。
- 修改对应测试：锁定展示名变化与内部值不变。

### 任务 1：建立统一品牌组件与静态元数据

**文件：**
- 创建：`frontend/src/components/brand/brand-mark.tsx`
- 创建：`frontend/src/__tests__/components/brand/brand-mark.test.tsx`
- 修改：`frontend/index.html`

- [ ] **步骤 1：编写失败的品牌组件测试**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BrandMark } from "@/components/brand/brand-mark";

describe("BrandMark", () => {
  it("exposes the NuomiDrama name and hides decorative geometry", () => {
    render(<BrandMark />);
    expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
    expect(screen.getByText("NuomiDrama")).toBeInTheDocument();
    expect(screen.getByTestId("nuomidrama-symbol")).toHaveAttribute("aria-hidden", "true");
  });

  it("supports a compact symbol-only mode", () => {
    render(<BrandMark compact />);
    expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
    expect(screen.queryByText("NuomiDrama")).not.toBeInTheDocument();
  });
});
```

- [ ] **步骤 2：运行测试并确认因组件不存在而失败**

运行：`cd frontend; pnpm vitest run src/__tests__/components/brand/brand-mark.test.tsx`

预期：FAIL，包含 `Failed to resolve import "@/components/brand/brand-mark"`。

- [ ] **步骤 3：实现单色、可缩放的几何 N 字标**

```tsx
import { cn } from "@/lib/utils";

export function BrandMark({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <span aria-label="NuomiDrama" className={cn("inline-flex items-center gap-2", className)}>
      <svg data-testid="nuomidrama-symbol" aria-hidden="true" viewBox="0 0 24 24" className="size-6">
        <path d="M4 19V5h4l8 10V5h4v14h-4L8 9v10H4Z" fill="currentColor" />
        <path d="M8 5h8l-2 3H6l2-3Zm2 11h8l-2 3H8l2-3Z" fill="var(--brand-accent)" />
      </svg>
      {!compact ? <span className="text-[15px] tracking-[-0.02em]"><b>Nuomi</b>Drama</span> : null}
    </span>
  );
}
```

- [ ] **步骤 4：替换页面元数据**

将 `frontend/index.html` 的 title、description、`og:*` 和 `twitter:*` 用户可见内容统一为：

```html
<title>NuomiDrama — AI 短剧制片工作台</title>
<meta name="description" content="从剧本导入、资产管理、分镜制作到视频交付的一站式 AI 短剧制片工作台。" />
<meta property="og:site_name" content="NuomiDrama" />
<meta property="og:title" content="NuomiDrama — AI 短剧制片工作台" />
<meta name="twitter:title" content="NuomiDrama — AI 短剧制片工作台" />
```

- [ ] **步骤 5：运行测试并提交**

运行：`cd frontend; pnpm vitest run src/__tests__/components/brand/brand-mark.test.tsx`

预期：2 tests passed。

```bash
git add frontend/index.html frontend/src/components/brand/brand-mark.tsx frontend/src/__tests__/components/brand/brand-mark.test.tsx
git commit -m "feat: add NuomiDrama brand foundation"
```

### 任务 2：落地 Editorial Black 全局视觉 token

**文件：**
- 修改：`frontend/src/index.css`
- 创建：`frontend/src/__tests__/styles/editorial-black-contract.test.ts`

- [ ] **步骤 1：编写失败的 token 契约测试**

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("Editorial Black theme", () => {
  const css = readFileSync("src/index.css", "utf8");

  it("defines the approved surfaces and action color", () => {
    expect(css).toContain("--background: #0d0e10");
    expect(css).toContain("--brand-accent: #e5ff5c");
    expect(css).toContain("--surface-raised: #20242a");
  });

  it("provides visible keyboard focus and reduced-motion handling", () => {
    expect(css).toContain(":focus-visible");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });
});
```

- [ ] **步骤 2：运行测试确认旧 token 导致失败**

运行：`cd frontend; pnpm vitest run src/__tests__/styles/editorial-black-contract.test.ts`

预期：FAIL，缺少 `--brand-accent: #e5ff5c`。

- [ ] **步骤 3：最小替换暗色主题与基础尺寸**

在 `frontend/src/index.css` 的主题区建立以下变量，并将 Tailwind 语义变量映射到这些值；不要批量改动业务组件中的特殊状态色：

```css
:root,
.dark {
  --background: #0d0e10;
  --foreground: #f2f4f7;
  --card: #14161a;
  --popover: #1a1d22;
  --muted: #1a1d22;
  --muted-foreground: #9299a5;
  --border: #2a2f37;
  --input: #2a2f37;
  --primary: #e5ff5c;
  --primary-foreground: #0d0e10;
  --ring: #bfe6ff;
  --brand-accent: #e5ff5c;
  --surface-raised: #20242a;
  --radius: 0.5rem;
  color-scheme: dark;
}

:focus-visible {
  outline: 2px solid var(--ring);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; }
}
```

- [ ] **步骤 4：验证测试与构建**

运行：`cd frontend; pnpm vitest run src/__tests__/styles/editorial-black-contract.test.ts && pnpm build`

预期：token 测试通过；TypeScript 与 Vite 构建成功。

- [ ] **步骤 5：提交主题基础**

```bash
git add frontend/src/index.css frontend/src/__tests__/styles/editorial-black-contract.test.ts
git commit -m "feat: apply Editorial Black design tokens"
```

### 任务 3：把项目顶栏改为稳定直接导航

**文件：**
- 修改：`frontend/src/components/layout/project-navigation-routes.ts`
- 修改：`frontend/src/components/layout/project-header-navigation.tsx`
- 修改：`frontend/src/components/layout/header.tsx`
- 修改：`frontend/src/__tests__/components/layout/project-navigation-routes.test.ts`
- 修改：`frontend/src/__tests__/components/layout/header.test.tsx`

- [ ] **步骤 1：先写路由和顶栏失败测试**

在路由测试中增加：

```ts
expect(PROJECT_NAV_ITEMS.map((item) => item.labelKey)).toEqual([
  "nav.ingest", "nav.assets", "nav.episodes", "nav.freezone",
  "nav.styles", "nav.tasks", "nav.aiAssistant",
]);
expect(PROJECT_NAV_ITEMS.map((item) => item.to)).toEqual([
  PROJECT_SECTION_ROUTES.ingest,
  PROJECT_SECTION_ROUTES.characters,
  PROJECT_SECTION_ROUTES.episodes,
  PROJECT_SECTION_ROUTES.freezone,
  PROJECT_SECTION_ROUTES.styles,
  PROJECT_SECTION_ROUTES.tasks,
  PROJECT_SECTION_ROUTES.assistant,
]);
```

在 header 测试的翻译 mock 加入七个导航标签，并增加：

```tsx
const routerState = vi.hoisted(() => ({ pathname: "/", project: undefined as string | undefined }));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: React.ComponentProps<"a">) => <a {...props}>{children}</a>,
  useNavigate: () => vi.fn(),
  useParams: () => ({ project: routerState.project }),
  useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => string }) =>
    select({ location: { pathname: routerState.pathname } }),
}));

function renderHeader({ project }: { project?: string } = {}) {
  routerState.project = project;
  routerState.pathname = project ? `/projects/${project}/ingest` : "/";
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Header />
    </QueryClientProvider>,
  );
}

it("shows the NuomiDrama brand and direct project navigation", () => {
  renderHeader({ project: "demo" });
  expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Project navigation" })).toBeInTheDocument();
  expect(screen.queryByText("虾画")).not.toBeInTheDocument();
  expect(screen.queryByText("虾集")).not.toBeInTheDocument();
});
```

- [ ] **步骤 2：运行聚焦测试确认失败**

运行：`cd frontend; pnpm vitest run src/__tests__/components/layout/project-navigation-routes.test.ts src/__tests__/components/layout/header.test.tsx`

预期：FAIL，`PROJECT_NAV_ITEMS` 尚未导出且旧字标仍为图片。

- [ ] **步骤 3：导出稳定导航清单并重写导航组件**

在 `project-navigation-routes.ts` 导出：

```ts
export const PROJECT_NAV_ITEMS = [
  { labelKey: "nav.ingest", to: PROJECT_SECTION_ROUTES.ingest },
  { labelKey: "nav.assets", to: PROJECT_SECTION_ROUTES.characters },
  { labelKey: "nav.episodes", to: PROJECT_SECTION_ROUTES.episodes },
  { labelKey: "nav.freezone", to: PROJECT_SECTION_ROUTES.freezone },
  { labelKey: "nav.styles", to: PROJECT_SECTION_ROUTES.styles },
  { labelKey: "nav.tasks", to: PROJECT_SECTION_ROUTES.tasks },
  { labelKey: "nav.aiAssistant", to: PROJECT_SECTION_ROUTES.assistant },
] as const;
```

将 `ProjectHeaderNavigation` 改为遍历该清单的单层 `<Link>`；剧集项继续使用 `rememberedEpisodeLocation`，其他栏目继续使用现有 route 常量。删除 `changeMode`、滑块、`ProjectXiajiMenu` 的渲染以及用户可见的 `xiahua/xiaji` 模式；不要删除 `projectModeFromPath` 或 store 中的旧 key，以免破坏已有持久化数据。

- [ ] **步骤 4：在 Header 中使用 BrandMark 并提升栏高**

```tsx
<Link to="/" aria-label={t("app.logoHomeTooltip")} className="flex min-w-0 shrink-0 items-center">
  <BrandMark />
</Link>
```

顶栏高度改为 56px；导航激活态使用 2px `var(--brand-accent)` 底线，不使用发光或整块高亮。

- [ ] **步骤 5：验证深链、可访问性与提交**

运行：`cd frontend; pnpm vitest run src/__tests__/components/layout/project-navigation-routes.test.ts src/__tests__/components/layout/header.test.tsx`

预期：全部通过，剧集深链、任务中心和画布路由保持原 URL。

```bash
git add frontend/src/components/layout frontend/src/__tests__/components/layout
git commit -m "feat: replace project mode switcher with direct navigation"
```

### 任务 4：统一双语产品语言并建立旧词禁用契约

**文件：**
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 修改：`frontend/src/__tests__/i18n/locales-json.test.ts`

- [ ] **步骤 1：编写失败的品牌语言测试**

```ts
it("uses the approved NuomiDrama navigation and contains no retired visible names", () => {
  const zhText = readFileSync("public/locales/zh/translation.json", "utf8");
  const enText = readFileSync("public/locales/en/translation.json", "utf8");
  const zh = JSON.parse(zhText);

  expect(zh.nav).toMatchObject({
    ingest: "剧本导入", assets: "资产中心", episodes: "剧集制作",
    freezone: "创作画布", styles: "视觉风格", tasks: "任务中心",
    aiAssistant: "糯米助手",
  });
  expect(zh.auth.community.heading).toBe("作品广场");
  for (const retired of ["DramaClaw", "SuperTale", "虾导", "虾塘", "虾画", "虾镜", "虾料", "虾格", "虾条", "虾集"]) {
    expect(zhText).not.toContain(retired);
    expect(enText).not.toContain(retired);
  }
});
```

- [ ] **步骤 2：运行测试确认旧词被捕获**

运行：`cd frontend; pnpm vitest run src/__tests__/i18n/locales-json.test.ts`

预期：FAIL，并报告第一个仍存在的旧词。

- [ ] **步骤 3：逐语境替换中文文案**

按语义替换，不做无脑全局替换：

```text
虾料 → 剧本导入
虾塘 → 资产中心
虾镜 → 剧集制作
虾画 → 创作画布
虾格 → 视觉风格
虾条 → 任务中心
虾导 → 糯米助手
DramaClaw TV → 作品广场
DramaClaw / SuperTale（产品主语）→ NuomiDrama
```

底层服务解释中的品牌主语改成“NuomiDrama”，但 `provider` 的机器值、API 路径和环境变量名不在 locale 中改名。

- [ ] **步骤 4：同步英文文案并保持 key 集合一致**

英文导航固定为 `Script Import / Assets / Production / Canvas / Visual Style / Tasks / Nuomi Assistant`；品牌统一为 `NuomiDrama`，社区统一为 `Showcase`。不增删仅一侧 locale key。

- [ ] **步骤 5：验证并提交**

运行：`cd frontend; pnpm vitest run src/__tests__/i18n/locales-json.test.ts`

预期：所有 locale JSON、key 对齐及旧词禁用测试通过。

```bash
git add frontend/public/locales frontend/src/__tests__/i18n/locales-json.test.ts
git commit -m "feat: rename visible product language to NuomiDrama"
```

### 任务 5：重做登录首屏并清理登录叙事页旧品牌

**文件：**
- 修改：`frontend/src/components/login/login-stage.tsx`
- 修改：`frontend/src/components/login/login.module.css`
- 修改：`frontend/src/components/login/cinematic/SecondScreenVideo.tsx`
- 修改：`frontend/src/components/login/cinematic/FourthScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/FifthScreenVideo.tsx`
- 修改：`frontend/src/components/login/cinematic/SeventhPipelineScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/EighthControlScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/NinthWorkflowScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/EleventhFaqScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/TwelfthFinalScreen.tsx`
- 修改：`frontend/src/components/login/cinematic/IntroRitualScreen.tsx`
- 创建：`frontend/src/__tests__/components/login/login-stage.test.tsx`

- [ ] **步骤 1：编写登录首屏失败测试**

```tsx
it("presents NuomiDrama as a professional production workspace", () => {
  render(<LoginStageContent onStart={vi.fn()} />);
  expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "从故事到成片，一站完成" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "开始创作" })).toBeInTheDocument();
  expect(screen.getByLabelText("产品工作台预览")).toBeInTheDocument();
});
```

测试中 mock `useTranslation`、`useGithubStars`、`CommunityShowcase` 和动画组件，使测试只关注首屏语义。

- [ ] **步骤 2：运行测试确认新文案与预览不存在**

运行：`cd frontend; pnpm vitest run src/__tests__/components/login/login-stage.test.tsx`

预期：FAIL，找不到新标题或产品预览。

- [ ] **步骤 3：实现 Editorial Black 首屏**

用 `BrandMark` 替换旧 PNG；主内容改为左侧标题、说明和双 CTA，右侧使用纯 HTML/CSS 构成“时间轴 + 9:16 预览 + 资产轨”产品预览。保留 `onStart`、商务联系、GitHub 和产品手册行为，不引入新图片依赖。

```tsx
<section className={styles.hero}>
  <div className={styles.heroCopy}>
    <h1>从故事到成片，一站完成</h1>
    <p>统一管理剧本、角色资产、分镜与视频任务，让每一集都可追踪、可复用。</p>
    <div className={styles.heroActions}>
      <button type="button" className={styles.heroPrimary} onClick={onStart}>开始创作</button>
      <a className={styles.heroSecondary} href={PRODUCT_MANUAL_URL}>查看工作流</a>
    </div>
  </div>
  <div className={styles.productPreview} aria-label="产品工作台预览">
    <div className={styles.previewSidebar}>资产中心</div>
    <div className={styles.previewCanvas}><span>9:16</span></div>
    <div className={styles.previewTimeline}><span /><span /><span /></div>
  </div>
</section>
```

CSS 必须使用已建立 token，标题 56/64/600，正文宽度不超过 520px，主按钮高 40px，卡片圆角 8px；移动端变成单列并保持所有交互目标至少 40px。

- [ ] **步骤 4：逐文件清理登录叙事页术语**

将 FAQ 和流程文案改为明确流程名，例如：“先在剧本导入建立文本，再到资产中心确认角色/场景/道具，在剧集制作推进分镜，复杂镜头进入创作画布。”同时把所有 `aria-label` 中的旧品牌改为 NuomiDrama 或准确中文功能名。

- [ ] **步骤 5：运行登录测试和旧词扫描**

运行：`cd frontend; pnpm vitest run src/__tests__/components/login/login-stage.test.tsx && rg -n "DramaClaw|SuperTale|虾导|虾塘|虾画|虾镜|虾料|虾格|虾条|虾集" src/components/login`

预期：测试通过；`rg` 无输出并以状态 1 结束。

- [ ] **步骤 6：提交登录体验**

```bash
git add frontend/src/components/login frontend/src/__tests__/components/login/login-stage.test.tsx
git commit -m "feat: redesign the NuomiDrama login experience"
```

### 任务 6：清理其余可见品牌并保留内部兼容标识

**文件：**
- 修改：`frontend/src/features/freezone/capabilities/candidate_capabilities.ts`
- 修改：`frontend/src/components/settings/text-runtime-panel.tsx`
- 修改：`frontend/src/__tests__/components/settings/text-runtime-panel.test.tsx`
- 创建：`frontend/src/__tests__/features/freezone/candidate-capabilities-brand.test.ts`
- 修改：扫描识别出的其他用户可见 TSX 文件；不得修改纯注释、API 协议、storage key、provider value、包名或测试夹具中的后端错误原文。

- [ ] **步骤 1：编写展示名与兼容值测试**

```tsx
it("shows the NuomiDrama gateway name while preserving the backend value", async () => {
  render(<TextRuntimePanel />);
  await user.click(screen.getByRole("combobox"));
  expect(screen.getByText("NuomiDrama API")).toBeInTheDocument();
  await user.click(screen.getByText("NuomiDrama API"));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ provider: "dramaclaw" }));
});
```

```ts
it("uses a NuomiDrama production label without changing the preset value", () => {
  expect(STYLE_OPTIONS).toContainEqual({ value: "supertale_production", label: "NuomiDrama 生产风格" });
});
```

- [ ] **步骤 2：运行聚焦测试确认展示名仍旧**

运行：`cd frontend; pnpm vitest run src/__tests__/components/settings/text-runtime-panel.test.tsx src/__tests__/features/freezone/candidate-capabilities-brand.test.ts`

预期：FAIL，当前展示名为 `DramaClawAPI` 或 `SuperTale 生产风格`。

- [ ] **步骤 3：仅修改 label 与用户提示**

```tsx
<SelectItem value="dramaclaw">NuomiDrama API</SelectItem>
```

```ts
{ value: "supertale_production", label: "NuomiDrama 生产风格" }
```

生成提示词中的内部 provenance 不属于 UI，但如果会直接展示给用户则改为中性描述 `production-ready asset candidate`，不要改 API 字段或模型 preset value。

- [ ] **步骤 4：执行分层旧词审计并人工分类**

运行：

```powershell
rg -n -S "DramaClaw|SuperTale|虾导|虾塘|虾画|虾镜|虾料|虾格|虾条|虾集" frontend/src frontend/public frontend/index.html
```

预期仅允许以下类型残留：源码注释、API 兼容说明、`dramaclaw` provider value、`supertale_production` preset value、包名 `dramaclaw-spec-render`、后端错误原文测试夹具、下载文件名兼容逻辑。任何 JSX 文本、ARIA、locale、option label 或用户提示均不得残留。

- [ ] **步骤 5：运行聚焦测试并提交**

运行：`cd frontend; pnpm vitest run src/__tests__/components/settings/text-runtime-panel.test.tsx src/__tests__/features/freezone/candidate-capabilities-brand.test.ts`

预期：全部通过且底层 values 断言不变。

```bash
git add frontend/src/features/freezone/capabilities/candidate_capabilities.ts frontend/src/components/settings/text-runtime-panel.tsx frontend/src/__tests__/components/settings/text-runtime-panel.test.tsx frontend/src/__tests__/features/freezone/candidate-capabilities-brand.test.ts
git commit -m "fix: remove remaining legacy product labels"
```

### 任务 7：全量回归与视觉验收

**文件：**
- 修改：仅限回归测试暴露出的本功能文件。

- [ ] **步骤 1：运行完整前端测试**

运行：`cd frontend; pnpm test`

预期：全部测试通过；若存在与本分支无关的既有失败，记录完整测试名和错误，不得声称全绿。

- [ ] **步骤 2：运行类型检查与生产构建**

运行：`cd frontend; pnpm build`

预期：`tsc -b` 和 `vite build` 均以状态 0 结束。

- [ ] **步骤 3：启动本地页面进行三视口视觉验收**

运行：`cd frontend; pnpm dev --host 127.0.0.1`

在浏览器检查登录页、项目中心、剧集制作三个页面，视口至少覆盖 1440×900、1024×768、390×844。验收：无横向溢出；品牌在 24px 高度清晰；焦点环可见；一级导航可用；缩略图仍是页面视觉主体；酸柠黄仅用于主 CTA、激活线和进度。

- [ ] **步骤 4：验证用户可见旧词为零**

运行：

```powershell
rg -n -S '"[^"\r\n]*(DramaClaw|SuperTale|虾导|虾塘|虾画|虾镜|虾料|虾格|虾条|虾集)[^"\r\n]*"' frontend/src frontend/public frontend/index.html
```

逐条确认残留只属于内部兼容或测试原文；浏览器可见字符串不得残留。

- [ ] **步骤 5：检查 diff 并确认任务提交完整**

运行：`git diff --check; git status --short`

预期：`git diff --check` 无错误；仅出现用户原有未提交变更，任务 1–6 的修改均已进入对应提交。若回归阶段产生修正，回到引发问题的任务，使用该任务列出的精确 `git add` 文件清单补充提交，禁止整目录暂存。

## 完成定义

- 登录页、顶栏、导航、社区、设置、帮助、候选能力中不再显示 DramaClaw、SuperTale 或任何“虾系”术语。
- 品牌统一显示 `NuomiDrama`，助手统一显示“糯米助手”，导入入口统一显示“剧本导入”。
- 导航为稳定直达栏目，不再显示“虾画/虾集”模式切换；所有原路由和深链继续工作。
- Editorial Black token、焦点态、reduced motion 和移动端布局通过测试与人工视觉验收。
- 内部 `dramaclaw` provider value、`supertale_production` preset value、API/storage key、包名和后端错误原文保持不变。
- 完整测试、构建和 `git diff --check` 均有当次运行证据。
