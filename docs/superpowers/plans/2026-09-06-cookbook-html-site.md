# 开发者 Cookbook HTML 文档站实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为现有 `docs/cookbook/**/*.md` 增加独立的 VitePress HTML 阅读入口，同时保持 Markdown 为唯一内容源。

**架构：** `docs/cookbook/` 同时作为 VitePress 内容根和独立 Node 包；`.vitepress/` 只保存站点配置与轻量主题扩展。仓库根脚本转发到文档包，生产构建输出到忽略目录，不接入业务前端或线上发布流程。

**技术栈：** VitePress、Mermaid、vitepress-plugin-mermaid、Node.js 内置测试运行器、pnpm

---

## 文件结构

- 创建 `tests/cookbook_site.test.mjs`：验证文档站入口、脚本、导航目标和忽略规则。
- 创建 `docs/cookbook/package.json`：隔离文档站依赖与命令。
- 创建 `docs/cookbook/pnpm-lock.yaml`：锁定文档站依赖。
- 创建 `docs/cookbook/.vitepress/config.mts`：站点元数据、导航、侧边栏、搜索和 Mermaid 配置。
- 创建 `docs/cookbook/.vitepress/theme/index.ts`：扩展 VitePress 默认主题。
- 创建 `docs/cookbook/.vitepress/theme/custom.css`：中文排版与 Cookbook 阅读样式。
- 修改 `package.json`：增加 `docs:dev`、`docs:build`、`docs:preview` 转发命令。
- 修改 `.gitignore`：忽略文档站缓存和静态产物。
- 修改 `docs/cookbook/README.md`：说明 HTML 入口使用方式。

### 任务 1：建立文档站配置契约

**文件：**
- 创建：`tests/cookbook_site.test.mjs`
- 修改：`package.json`
- 修改：`.gitignore`

- [ ] **步骤 1：编写失败的配置测试**

创建使用 Node 内置 `node:test`、`assert`、`fs` 的测试，覆盖以下行为：

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(path, "utf8");

test("root scripts expose cookbook development, build and preview", () => {
  const pkg = JSON.parse(read("package.json"));
  assert.equal(pkg.scripts["docs:dev"], "pnpm --dir docs/cookbook dev");
  assert.equal(pkg.scripts["docs:build"], "pnpm --dir docs/cookbook build");
  assert.equal(pkg.scripts["docs:preview"], "pnpm --dir docs/cookbook preview");
});

test("generated cookbook site files are ignored", () => {
  const ignore = read(".gitignore");
  assert.match(ignore, /\/docs\/cookbook\/\.vitepress\/dist\//);
  assert.match(ignore, /\/docs\/cookbook\/\.vitepress\/cache\//);
});
```

- [ ] **步骤 2：运行测试并确认正确失败**

运行：

```bash
node --test tests/cookbook_site.test.mjs
```

预期：两个测试因根脚本和忽略规则尚不存在而失败；测试文件本身没有语法或加载错误。

- [ ] **步骤 3：添加最小根脚本与忽略规则**

在根 `package.json` 的 `scripts` 中添加：

```json
"docs:dev": "pnpm --dir docs/cookbook dev",
"docs:build": "pnpm --dir docs/cookbook build",
"docs:preview": "pnpm --dir docs/cookbook preview"
```

在 `.gitignore` 添加：

```gitignore
/docs/cookbook/.vitepress/cache/
/docs/cookbook/.vitepress/dist/
```

- [ ] **步骤 4：运行测试并确认第一组契约通过**

运行：`node --test tests/cookbook_site.test.mjs`

预期：`2 passed, 0 failed`。

- [ ] **步骤 5：提交任务 1**

```bash
git add package.json .gitignore tests/cookbook_site.test.mjs
git commit --only package.json .gitignore tests/cookbook_site.test.mjs -m "test(docs): define cookbook site contract"
```

### 任务 2：增加独立 VitePress 包与导航配置

**文件：**
- 修改：`tests/cookbook_site.test.mjs`
- 创建：`docs/cookbook/package.json`
- 创建：`docs/cookbook/pnpm-lock.yaml`
- 创建：`docs/cookbook/.vitepress/config.mts`

- [ ] **步骤 1：先扩展失败测试**

增加测试，读取文档包和配置文件并断言：

```js
const sitePkg = JSON.parse(read("docs/cookbook/package.json"));
assert.equal(sitePkg.scripts.dev, "vitepress .");
assert.equal(sitePkg.scripts.build, "vitepress build .");
assert.equal(sitePkg.scripts.preview, "vitepress preview .");
assert.ok(sitePkg.devDependencies.vitepress);
assert.ok(sitePkg.devDependencies.mermaid);
assert.ok(sitePkg.devDependencies["vitepress-plugin-mermaid"]);

const config = read("docs/cookbook/.vitepress/config.mts");
for (const route of [
  "/", "/start-software", "/system-map",
  "/development/trace-a-feature", "/development/add-api-and-task",
  "/development/storage-and-files", "/development/testing-strategy",
  "/pipelines/01-ingest", "/pipelines/02-episode-graph",
  "/pipelines/03-production-assets", "/pipelines/04-screenplay",
  "/pipelines/05-storyboard", "/pipelines/06-audio",
  "/pipelines/07-video", "/pipelines/08-compose-export"
]) assert.match(config, new RegExp(JSON.stringify(route)));
assert.match(config, /provider:\s*["']local["']/);
assert.match(config, /withMermaid/);
```

- [ ] **步骤 2：运行测试并确认因站点包缺失而失败**

运行：`node --test tests/cookbook_site.test.mjs`

预期：新增测试在读取 `docs/cookbook/package.json` 时失败，证明站点配置尚未实现。

- [ ] **步骤 3：初始化文档站包并安装依赖**

创建最小 `docs/cookbook/package.json`：

```json
{
  "name": "nuomi-cookbook-site",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vitepress .",
    "build": "vitepress build .",
    "preview": "vitepress preview ."
  },
  "devDependencies": {}
}
```

运行：

```bash
pnpm --dir docs/cookbook add -D vitepress vitepress-plugin-mermaid mermaid
```

预期：更新该目录的 `package.json` 并生成 `pnpm-lock.yaml`；不修改根依赖或 `frontend/package.json`。

- [ ] **步骤 4：添加站点配置**

`config.mts` 使用 `defineConfig` 与 `withMermaid`，配置：

- `lang: "zh-CN"`、站点标题和说明；
- 顶部入口：首页、系统地图、启动与调试、指向 GitHub `docs/zh/README.md` 的仓库文档链接；
- 三组侧边栏及全部 15 个页面；
- `themeConfig.search.provider = "local"`；
- `outline.level = [2, 3]`、中文目录标题和上一篇/下一篇文案；
- `ignoreDeadLinks: false`；
- Mermaid 浅色和深色主题配置。

- [ ] **步骤 5：运行配置测试**

运行：`node --test tests/cookbook_site.test.mjs`

预期：全部通过。

- [ ] **步骤 6：提交任务 2**

```bash
git add tests/cookbook_site.test.mjs docs/cookbook/package.json docs/cookbook/pnpm-lock.yaml docs/cookbook/.vitepress/config.mts
git commit --only tests/cookbook_site.test.mjs docs/cookbook/package.json docs/cookbook/pnpm-lock.yaml docs/cookbook/.vitepress/config.mts -m "feat(docs): add VitePress cookbook site"
```

### 任务 3：增加轻量主题与 HTML 入口说明

**文件：**
- 修改：`tests/cookbook_site.test.mjs`
- 创建：`docs/cookbook/.vitepress/theme/index.ts`
- 创建：`docs/cookbook/.vitepress/theme/custom.css`
- 修改：`docs/cookbook/README.md`

- [ ] **步骤 1：先扩展失败测试**

增加测试断言主题入口导入默认主题与样式、首页包含三条文档站命令：

```js
const theme = read("docs/cookbook/.vitepress/theme/index.ts");
assert.match(theme, /vitepress\/theme/);
assert.match(theme, /custom\.css/);

const home = read("docs/cookbook/README.md");
for (const command of ["pnpm docs:dev", "pnpm docs:build", "pnpm docs:preview"]) {
  assert.match(home, new RegExp(command.replace(":", "\\:")));
}
```

- [ ] **步骤 2：运行测试并确认主题入口缺失**

运行：`node --test tests/cookbook_site.test.mjs`

预期：新增测试因 `theme/index.ts` 尚不存在而失败。

- [ ] **步骤 3：实现最小主题扩展**

`theme/index.ts`：

```ts
import DefaultTheme from "vitepress/theme";
import "./custom.css";

export default DefaultTheme;
```

`custom.css` 只覆盖默认主题变量和内容容器：

- 中英文系统字体栈；
- 桌面正文最大宽度；
- H2/H3 分隔与间距；
- 表格横向滚动；
- 行内代码和 `<details>` 的边框、背景、间距；
- 小屏幕恢复默认正文宽度。

- [ ] **步骤 4：在首页增加 HTML 阅读入口**

在 `docs/cookbook/README.md` 首段后增加「HTML 阅读入口」，说明：

```bash
pnpm docs:dev
pnpm docs:build
pnpm docs:preview
```

注明命令从仓库根目录运行，开发服务器输出实际本地 URL，Markdown 仍是内容源。

- [ ] **步骤 5：运行配置测试和 Markdown 链接检查**

运行：

```bash
node --test tests/cookbook_site.test.mjs
python3 scripts/check_docs_links.py
```

如果仓库不存在 `scripts/check_docs_links.py`，使用现有 Cookbook 链接检查命令，不为一次检查引入新的 Python 工具。

预期：主题与入口测试全部通过；Markdown 相对链接、锚点和 `<details>` 配对无错误。

- [ ] **步骤 6：提交任务 3**

```bash
git add tests/cookbook_site.test.mjs docs/cookbook/.vitepress/theme/index.ts docs/cookbook/.vitepress/theme/custom.css docs/cookbook/README.md
git commit --only tests/cookbook_site.test.mjs docs/cookbook/.vitepress/theme/index.ts docs/cookbook/.vitepress/theme/custom.css docs/cookbook/README.md -m "feat(docs): style cookbook reading experience"
```

### 任务 4：构建并验证静态 HTML

**文件：**
- 修改：`tests/cookbook_site.test.mjs`
- 按构建结果仅修正任务 1–3 已列出的配置或主题文件

- [ ] **步骤 1：增加构建产物验证测试**

测试接受输出根目录参数，检查以下文件存在且包含 HTML：

```js
const builtPages = [
  "index.html",
  "system-map.html",
  "pipelines/01-ingest.html",
  "pipelines/02-episode-graph.html",
  "pipelines/03-production-assets.html",
  "pipelines/04-screenplay.html",
  "pipelines/05-storyboard.html",
  "pipelines/06-audio.html",
  "pipelines/07-video.html",
  "pipelines/08-compose-export.html"
];
```

把产物检查放在仅当 `COOKBOOK_DIST` 环境变量存在时运行的独立测试中，避免普通配置测试依赖已有构建目录。测试还需检查输出资源中存在本地搜索索引和 Mermaid 相关 bundle 标记。

- [ ] **步骤 2：构建前运行产物测试并确认失败**

运行：

```bash
COOKBOOK_DIST=docs/cookbook/.vitepress/dist node --test tests/cookbook_site.test.mjs
```

预期：因静态输出尚不存在而失败。

- [ ] **步骤 3：执行生产构建**

运行：`pnpm docs:build`

预期：VitePress 构建退出码为 0，没有 dead link 或 Mermaid 配置错误。

- [ ] **步骤 4：验证产物和工作区**

运行：

```bash
COOKBOOK_DIST=docs/cookbook/.vitepress/dist node --test tests/cookbook_site.test.mjs
git status --short docs/cookbook/.vitepress/dist docs/cookbook/.vitepress/cache
git diff --check
```

预期：配置与产物测试全部通过；构建目录未出现在 Git 状态中；无空白错误。

- [ ] **步骤 5：浏览器冒烟验证**

运行 `pnpm docs:preview`，在本地浏览器验证：

1. 首页「我要修改什么」能跳到对应 `#常见修改`。
2. 左侧三组导航及上一篇/下一篇可用。
3. `system-map` 和至少一条生产管线的 Mermaid 显示为 SVG。
4. 本地搜索能用「任务」「分镜」找到相关页面。
5. 深色模式与窄屏菜单可用，正文表格不会撑破页面。

- [ ] **步骤 6：提交任务 4 的测试调整**

```bash
git add tests/cookbook_site.test.mjs
git commit --only tests/cookbook_site.test.mjs -m "test(docs): verify cookbook static output"
```

构建产物保持未跟踪且被忽略，不加入提交。

## 最终验证

- [ ] 运行 `node --test tests/cookbook_site.test.mjs`。
- [ ] 清理构建目录后，带 `COOKBOOK_DIST` 运行测试并确认红灯，再运行 `pnpm docs:build` 和同一测试确认绿灯。
- [ ] 运行 Cookbook Markdown 链接、锚点、代码围栏和 `<details>` 检查。
- [ ] 运行 `git diff --check`。
- [ ] 确认所有提交只包含计划列出的文档站文件，没有混入当前工作区的并行改动。
- [ ] 请求最终代码与文档审阅，修复 Critical 和 Important 问题后再交付。
