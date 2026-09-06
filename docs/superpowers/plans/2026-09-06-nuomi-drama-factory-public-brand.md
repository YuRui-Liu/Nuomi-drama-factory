# Nuomi Drama Factory 对外品牌替换实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 README、当前使用文档、产品界面和公开仓库链接中的 DramaClaw 品牌替换为 Nuomi Drama Factory，同时保持所有内部兼容标识可用。

**架构：** 把改名分为公开文档、产品界面、图片资源和兼容边界四层。每层先建立残留基线，再只修改用户可见值；最后用定向测试、构建和允许列表扫描证明内部 `DRAMACLAW_*`、`dramaclaw_*` 与历史记录未被误改。

**技术栈：** Markdown、React/TypeScript、Vite、Vitest、GitHub Actions/YAML、Docker Compose 文档示例

---

## 文件结构

- `README.md`、`readme/README_zh.md`：英文和中文项目首页、安装命令及公开链接。
- `docs/README.md`、`docs/en/**`、`docs/zh/**`、`docs/cookbook/start-software.md`：当前用户文档；排除 `docs/plans/**` 和既有 `docs/superpowers/**` 历史记录。
- `CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`GOVERNANCE.md`、`SECURITY.md`、`NOTICE`：当前社区和法律入口中的展示名称、联系及仓库链接。
- `.github/ISSUE_TEMPLATE/config.yml`：Issue、Discussion 和安全策略链接。
- `frontend/src/**`、`frontend/index.html`：运行时可见品牌文案、ARIA 标签、社区链接及其测试。
- `assets/hero.png`、`assets/pipeline.png`、`assets/architecture.png` 和 README 引用的远端图片：视觉审计对象；仅在图片内确有旧字标时替换或移除。
- `scripts/check_public_brand.py`：用户可见文件残留扫描器，明确允许内部兼容值和不渲染的 CDN 路径。
- `tests/test_public_brand.py`：扫描器契约测试，证明公开品牌残留会失败、内部兼容标识会保留。

### 任务 1：建立品牌残留门禁

**文件：**
- 创建：`scripts/check_public_brand.py`
- 创建：`tests/test_public_brand.py`

- [ ] **步骤 1：编写失败测试**

测试临时文件中的 `DramaClaw` 展示文本会被报告，同时 `DRAMACLAW_API_URL`、`dramaclaw_get` 和 `https://nfg-web-assets.cdnfg.com/dramaclaw/...` 不会被误报。扫描范围固定为当前文档、社区文件和前端用户界面，显式排除历史计划、锁文件、第三方许可证与内部 Hermes 插件。

- [ ] **步骤 2：运行测试并确认失败**

运行：`uv run pytest tests/test_public_brand.py -q`

预期：FAIL，原因是 `scripts.check_public_brand` 尚不存在。

- [ ] **步骤 3：实现最小扫描器**

实现 `scan_text(path, text) -> list[str]` 和仓库扫描 CLI。逐行跳过以下允许项：全大写 `DRAMACLAW_*`、小写工具/机器标识 `dramaclaw_*`、CDN 资源路径，以及明确写作“内部兼容名称”的说明；其他大小写组合的 `DramaClaw` 均返回 `path:line`。

- [ ] **步骤 4：验证扫描器测试通过且当前仓库门禁失败**

运行：`uv run pytest tests/test_public_brand.py -q`

预期：PASS。

运行：`uv run python scripts/check_public_brand.py`

预期：非零退出，并列出尚未替换的公开文案。

- [ ] **步骤 5：提交门禁**

```bash
git add scripts/check_public_brand.py tests/test_public_brand.py
git commit -m "test: guard Nuomi public branding"
```

### 任务 2：替换项目首页与社区入口

**文件：**
- 修改：`README.md`
- 修改：`readme/README_zh.md`
- 修改：`docs/README.md`
- 修改：`CONTRIBUTING.md`
- 修改：`CODE_OF_CONDUCT.md`
- 修改：`GOVERNANCE.md`
- 修改：`SECURITY.md`
- 修改：`NOTICE`
- 修改：`.github/ISSUE_TEMPLATE/config.yml`

- [ ] **步骤 1：替换名称和仓库链接**

完整名称使用 `Nuomi Drama Factory`；README 紧凑字标可使用 `NuomiDrama`。将 clone、Issue、Discussion、Security、Release、Star 和 raw 文件地址统一切换到 `YuRui-Liu/Nuomi-drama-factory`，目录示例统一为 `Nuomi-drama-factory`。

- [ ] **步骤 2：处理失效或不可迁移的公开链接**

删除无法指向新仓库的旧 Star History 图表；官网或旧品牌邮箱若没有已确认的新地址，则改为新仓库的 Discussions 或 Security 页面，不虚构新域名和邮箱。

- [ ] **步骤 3：检查首页残留与链接格式**

运行：

```bash
rg -n -i 'dramaclaw' README.md readme/README_zh.md docs/README.md CONTRIBUTING.md CODE_OF_CONDUCT.md GOVERNANCE.md SECURITY.md NOTICE .github/ISSUE_TEMPLATE/config.yml
```

预期：只剩不向用户显示的 CDN 路径或明确的内部兼容说明。

- [ ] **步骤 4：提交首页与社区文件**

```bash
git add README.md readme/README_zh.md docs/README.md CONTRIBUTING.md CODE_OF_CONDUCT.md GOVERNANCE.md SECURITY.md NOTICE .github/ISSUE_TEMPLATE/config.yml
git commit -m "docs: rebrand public project pages to Nuomi"
```

### 任务 3：替换当前中英文用户文档

**文件：**
- 修改：`docs/en/README.md`
- 修改：`docs/en/concepts/architecture.md`
- 修改：`docs/en/concepts/features.md`
- 修改：`docs/en/getting-started/configuring-models.md`
- 修改：`docs/en/getting-started/installation.md`
- 修改：`docs/en/getting-started/quickstart.md`
- 修改：`docs/en/guides/ffmpeg.md`
- 修改：`docs/en/guides/self-hosting.md`
- 修改：`docs/en/guides/telemetry.md`
- 修改：`docs/en/guides/troubleshooting.md`
- 修改：`docs/en/license.md`
- 修改：`docs/zh/README.md`
- 修改：`docs/zh/concepts/architecture.md`
- 修改：`docs/zh/concepts/features.md`
- 修改：`docs/zh/getting-started/configuring-models.md`
- 修改：`docs/zh/getting-started/installation.md`
- 修改：`docs/zh/getting-started/quickstart.md`
- 修改：`docs/zh/guides/ffmpeg.md`
- 修改：`docs/zh/guides/self-hosting.md`
- 修改：`docs/zh/guides/telemetry.md`
- 修改：`docs/zh/guides/troubleshooting.md`
- 修改：`docs/zh/license.md`
- 修改：`docs/zh/technical-design.md`
- 修改：`docs/cookbook/start-software.md`

- [ ] **步骤 1：替换产品称谓**

正文产品名统一改为 `Nuomi Drama Factory`；描述紧凑 UI 标签时使用 `NuomiDrama`。不改模型名、环境变量、runtime token、数据卷等真实机器值。

- [ ] **步骤 2：更新所有安装示例**

把 GitHub 地址和 `cd dramaclaw` 改为新仓库及 `cd Nuomi-drama-factory`。备份命令中的真实 Compose 数据卷名不在未验证的情况下改名，并在需要时标注它是兼容名称。

- [ ] **步骤 3：检查中英文文档残留**

运行：`uv run python scripts/check_public_brand.py`

预期：如果仍失败，输出只能来自后续前端任务；本任务列出的文档不再包含未允许的旧品牌展示。

- [ ] **步骤 4：提交当前文档**

```bash
git add docs/en docs/zh docs/cookbook/start-software.md
git commit -m "docs: rename user guides to Nuomi Drama Factory"
```

### 任务 4：替换产品界面与公开链接

**文件：**
- 修改：`frontend/src/components/login/cinematic/media.ts`
- 修改：`frontend/src/components/login/login-stage.tsx`
- 修改：`frontend/src/components/settings/text-runtime-panel.tsx`
- 修改：`frontend/src/features/canvas/nodes/Pano360ViewerNode.tsx`
- 修改：`frontend/src/features/superchat/message.ts`
- 修改：`frontend/src/features/superchat/spec-extract.ts`
- 修改：`frontend/src/features/superchat/superchat-panel.tsx`
- 修改：`frontend/src/hooks/use-github-stars.ts`
- 修改：`frontend/src/lib/desktop-download.ts`
- 修改：`frontend/src/lib/login-community.ts`
- 修改：`frontend/src/lib/queries/model-gateway.ts`
- 修改：`frontend/src/lib/release-notification-state.ts`
- 修改：`frontend/src/main.tsx`
- 修改：对应 `frontend/src/__tests__/**` 测试文件

- [ ] **步骤 1：先更新用户可见断言**

将测试中针对标题、ARIA、提示、公开链接和显示标签的预期值改为 `NuomiDrama` 或新仓库地址。安装包解析、provider 值、API 字段和历史 release fixture 仍断言旧机器名称时保持不变。

- [ ] **步骤 2：运行定向测试并确认失败**

运行：

```bash
pnpm --dir frontend test
```

预期：至少一个已更新的用户可见品牌断言失败。

- [ ] **步骤 3：修改最小界面实现**

只替换渲染给用户的字符串、ARIA 文本、浏览器元数据和仓库链接。保留 `value="dramaclaw"`、下载资产兼容匹配、内部事件名及 API 请求字段。

- [ ] **步骤 4：验证前端测试通过**

运行：`pnpm --dir frontend test`

预期：全部 PASS。

- [ ] **步骤 5：提交界面替换**

```bash
git add frontend/src frontend/index.html
git commit -m "feat: finish Nuomi user-facing branding"
```

### 任务 5：审计并处理品牌图片

**文件：**
- 检查：`assets/hero.png`
- 检查：`assets/pipeline.png`
- 检查：`assets/architecture.png`
- 检查：README 和前端引用的品牌图片及视频封面
- 修改：仅限检查后确认含旧字标的资源及其引用文件

- [ ] **步骤 1：渲染和查看本地图片**

逐张查看 `hero.png`、`pipeline.png`、`architecture.png`，记录是否出现旧品牌字样；不能依据文件名或 `alt` 文本推断图片内容。

- [ ] **步骤 2：检查远端图片的实际画面**

对 README 直接展示的远端品牌图进行预览。作品封面只要没有旧品牌字标即可保留；URL 路径中的 `dramaclaw` 不构成可见品牌残留。

- [ ] **步骤 3：替换或移除旧品牌图片**

优先复用仓库已有 NuomiDrama Logo 和中性产品截图；没有等价资源时移除图片与对应说明，不生成虚构产品截图。

- [ ] **步骤 4：提交图片调整**

```bash
git add assets frontend/public README.md readme/README_zh.md docs/en docs/zh
git commit -m "docs: remove legacy branding from public images"
```

若没有图片需要修改，则跳过本次提交并在验证记录中注明检查结果。

### 任务 6：全量验证与兼容性复核

**文件：**
- 修改：仅修复验证发现的遗漏文件

- [ ] **步骤 1：运行公开品牌门禁**

运行：`uv run python scripts/check_public_brand.py`

预期：退出码 0，无未允许的公开 `DramaClaw` 残留。

- [ ] **步骤 2：运行 Python 定向测试**

运行：

```bash
uv run pytest tests/test_public_brand.py tests/test_hermes_dramaclaw_plugin.py tests/test_hermes_workspace.py -q
```

预期：全部 PASS，证明展示层改名没有破坏 Hermes 兼容契约。

- [ ] **步骤 3：运行前端测试和构建**

运行：

```bash
pnpm --dir frontend test
pnpm --dir frontend build
```

预期：测试全部 PASS，构建退出码 0。

- [ ] **步骤 4：检查链接和差异**

运行：

```bash
rg -n 'github.com/dramaclaw/dramaclaw|raw.githubusercontent.com/dramaclaw/dramaclaw' README.md readme docs/en docs/zh CONTRIBUTING.md SECURITY.md .github/ISSUE_TEMPLATE
git diff --check
git status --short
```

预期：旧仓库链接无匹配；`git diff --check` 退出码 0；状态只包含本计划的预期修改。

- [ ] **步骤 5：复核内部标识未迁移**

运行：

```bash
git grep -n 'DRAMACLAW_API_URL' .hermes src tests
git grep -n 'dramaclaw_get' .hermes src tests
```

预期：兼容环境变量和工具名仍有匹配，相关文件未因品牌替换被批量重命名。

- [ ] **步骤 6：提交验证修正**

如果验证修改了公开品牌扫描器，则运行：

```bash
git add scripts/check_public_brand.py tests/test_public_brand.py
git commit -m "chore: complete Nuomi public brand migration"
```

如果验证只修改了某个前述任务中的文件，将该文件追加到对应任务的提交；如果验证没有产生修正，则不创建空提交。
