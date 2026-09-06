# Cookbook 三线 Wiki 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有开发者 Cookbook 改造成同时服务最终用户、产品、测试和研发的三线 Wiki，并增加产品功能、创作方法、业务流程与技术流程内容。

**架构：** 保留 VitePress 和 Markdown 单一内容源。新增 `product/` 与 `creation/` 两组页面，现有 `system-map.md`、`development/`、`pipelines/` 归入技术实现；首页、导航和交叉链接把三类内容连接起来。

**技术栈：** VitePress 1.6、Markdown、Mermaid、Node.js test runner、CSS。

---

## 文件结构

- `docs/cookbook/product/*.md`：七个面向用户、产品和测试的功能域。
- `docs/cookbook/creation/*.md`：八个创作方法专题。
- `docs/cookbook/index.md`：按目标、角色和创作阶段组织的总入口。
- `docs/cookbook/.vitepress/config.mts`：三线顶部导航和分组侧边栏。
- `docs/cookbook/.vitepress/theme/custom.css`：入口卡片、流程容器、角色标签和检查清单。
- `docs/cookbook/system-map.md`、`development/*.md`、`pipelines/*.md`：增加业务摘要与产品/创作交叉链接，不删除现有技术证据。
- `tests/cookbook_site.test.mjs`：页面清单、章节契约、导航、流程图和静态产物检查。

### 任务 1：定义三线 Wiki 内容契约

**文件：**
- 修改：`tests/cookbook_site.test.mjs`

- [ ] **步骤 1：添加失败测试**

  新增产品页与创作页数组，断言文件存在；产品页包含「功能概览、用户操作、业务流程、业务规则、产品验收、技术实现」，创作页包含「适用场景、判断标准、推荐工作流、常见失败、软件入口、检查清单」。

- [ ] **步骤 2：验证红灯**

  运行：`node --test tests/cookbook_site.test.mjs`

  预期：FAIL，缺少 `docs/cookbook/product/` 与 `docs/cookbook/creation/` 页面。

- [ ] **步骤 3：增加导航与产物契约**

  断言顶部导航包含「产品功能、创作方法、技术实现」；设置 `COOKBOOK_DIST` 时断言 15 个新页面均生成 HTML。

- [ ] **步骤 4：Commit**

  ```bash
  git add tests/cookbook_site.test.mjs
  git commit -m "test(docs): define three-layer wiki contract"
  ```

### 任务 2：编写七个产品功能页

**文件：**
- 创建：`docs/cookbook/product/01-project-and-novel.md`
- 创建：`docs/cookbook/product/02-episode-planning.md`
- 创建：`docs/cookbook/product/03-production-assets.md`
- 创建：`docs/cookbook/product/04-screenplay-and-storyboard.md`
- 创建：`docs/cookbook/product/05-audio-and-video.md`
- 创建：`docs/cookbook/product/06-compose-and-export.md`
- 创建：`docs/cookbook/product/07-settings-and-tasks.md`

- [ ] **步骤 1：编写统一页面骨架**

  每页按固定章节编写：功能概览、适合谁看、用户操作、业务流程、业务规则、状态与异常、产品验收、技术实现、相关创作专题。

- [ ] **步骤 2：加入业务流程图**

  每页至少一张 Mermaid 业务图，只使用用户动作、功能状态和业务产出；图后提供等价编号步骤。

- [ ] **步骤 3：对照代码与现有管线**

  只描述已有能力；入口、状态、异常和技术链接以 `docs/cookbook/pipelines/`、`development/` 及当前代码为证据。

- [ ] **步骤 4：运行契约测试**

  运行：`node --test tests/cookbook_site.test.mjs`

  预期：产品页相关断言 PASS；创作页断言仍 FAIL。

- [ ] **步骤 5：Commit**

  ```bash
  git add docs/cookbook/product
  git commit -m "docs: add product feature guides"
  ```

### 任务 3：编写创作哲学与前半程方法

**文件：**
- 创建：`docs/cookbook/creation/01-creative-philosophy.md`
- 创建：`docs/cookbook/creation/02-end-to-end-workflow.md`
- 创建：`docs/cookbook/creation/03-episode-rhythm.md`
- 创建：`docs/cookbook/creation/04-character-consistency.md`

- [ ] **步骤 1：按创作模板编写四页**

  每页包含适用场景、核心观点、判断标准、推荐工作流、正反例、常见失败、软件入口和检查清单。

- [ ] **步骤 2：区分事实与建议**

  软件行为链接到产品页和技术页；创作建议注明适用前提，不把题材偏好写成系统约束。

- [ ] **步骤 3：加入可执行检查表**

  每页至少提供一组作者、导演或产品可逐项核对的 Markdown checklist。

- [ ] **步骤 4：Commit**

  ```bash
  git add docs/cookbook/creation/01-creative-philosophy.md docs/cookbook/creation/02-end-to-end-workflow.md docs/cookbook/creation/03-episode-rhythm.md docs/cookbook/creation/04-character-consistency.md
  git commit -m "docs: add core creation methodology"
  ```

### 任务 4：编写后半程创作方法与复盘模板

**文件：**
- 创建：`docs/cookbook/creation/05-shot-language.md`
- 创建：`docs/cookbook/creation/06-audiovisual-continuity.md`
- 创建：`docs/cookbook/creation/07-quality-gates.md`
- 创建：`docs/cookbook/creation/08-retrospective-template.md`

- [ ] **步骤 1：编写镜头与连续性专题**

  覆盖镜头目的、景别与调度、动作/角色/空间/声音连续性及返工信号，并连接分镜、视频和合成产品页。

- [ ] **步骤 2：编写阶段质量门禁**

  按导入、拆集、资产、剧本、分镜、声音/视频、合成七阶段列出进入条件、通过标准和退回条件。

- [ ] **步骤 3：编写案例复盘模板**

  提供目标、约束、关键决策、版本对比、问题证据、返工成本、可复用规则和后续行动字段；不编造案例数据。

- [ ] **步骤 4：Commit**

  ```bash
  git add docs/cookbook/creation/05-shot-language.md docs/cookbook/creation/06-audiovisual-continuity.md docs/cookbook/creation/07-quality-gates.md docs/cookbook/creation/08-retrospective-template.md
  git commit -m "docs: add creation quality playbooks"
  ```

### 任务 5：重做首页、导航和阅读样式

**文件：**
- 修改：`docs/cookbook/index.md`
- 修改：`docs/cookbook/.vitepress/config.mts`
- 修改：`docs/cookbook/.vitepress/theme/custom.css`

- [ ] **步骤 1：重做首页**

  第一屏提供「第一次创作、了解产品功能、学习创作方法、修改技术实现」四个入口；随后展示创作旅程、三线知识地图和角色入口。

- [ ] **步骤 2：更新导航**

  顶部导航加入产品功能、创作方法、技术实现；侧边栏按产品、创作、技术分组并包含全部新页面。

- [ ] **步骤 3：增加语义样式**

  为 `.wiki-grid`、`.wiki-card`、`.audience-tags`、`.business-flow`、`.technical-flow`、`.checklist-block` 增加桌面和移动端样式；颜色不作为唯一编码。

- [ ] **步骤 4：验证配置测试**

  运行：`node --test tests/cookbook_site.test.mjs`

  预期：页面、章节与配置测试全部 PASS；静态产物测试按环境跳过。

- [ ] **步骤 5：Commit**

  ```bash
  git add docs/cookbook/index.md docs/cookbook/.vitepress/config.mts docs/cookbook/.vitepress/theme/custom.css
  git commit -m "feat(docs): add three-layer wiki navigation"
  ```

### 任务 6：连接现有技术文档

**文件：**
- 修改：`docs/cookbook/system-map.md`
- 修改：`docs/cookbook/development/*.md`
- 修改：`docs/cookbook/pipelines/*.md`

- [ ] **步骤 1：增加技术页摘要**

  每页前部补充「业务目标、对应产品功能、相关创作专题、技术调用链」摘要；不删除现有代码证据和验证命令。

- [ ] **步骤 2：统一交叉链接**

  产品页链接技术页，技术页返回产品页；涉及质量判断时连接创作专题。

- [ ] **步骤 3：验证内部链接**

  运行：`pnpm docs:build`

  预期：构建成功且无内部死链。

- [ ] **步骤 4：Commit**

  ```bash
  git add docs/cookbook/system-map.md docs/cookbook/development docs/cookbook/pipelines
  git commit -m "docs: connect product creation and technical guides"
  ```

### 任务 7：静态产物与最终验收

**文件：**
- 修改：`tests/cookbook_site.test.mjs`

- [ ] **步骤 1：构建到临时目录**

  ```bash
  pnpm --dir docs/cookbook exec vitepress build . --outDir /private/tmp/nuomi-cookbook-three-layer
  ```

- [ ] **步骤 2：运行产物测试**

  ```bash
  COOKBOOK_DIST=/private/tmp/nuomi-cookbook-three-layer node --test tests/cookbook_site.test.mjs
  ```

  预期：全部测试 PASS，首页、七个产品页、八个创作页和现有技术页均有 HTML。

- [ ] **步骤 3：检查格式与状态**

  ```bash
  git diff --check
  git status --short -- docs/cookbook tests/cookbook_site.test.mjs
  ```

  预期：无空白错误；目标文件无未提交修改。

- [ ] **步骤 4：Commit**

  ```bash
  git add tests/cookbook_site.test.mjs
  git commit -m "test(docs): verify three-layer wiki output"
  ```
