# 剧本工艺升级实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务执行，步骤以复选框跟踪。

**目标：** 将人物驱动和短剧吸引力落实为可执行的创作、语言编辑与证据审稿流程。

**架构：** 扩展既有 Markdown 技能及项目模板，增加两个专责工艺模块；分析材料与剧本正文分离。通过三集独立候选稿检查效果，保留原稿和导出协议。

**技术栈：** Markdown、YAML、现有 Python 标准库契约检查。

**工作根目录：** `/Users/liuyuxiang05/Liu/Nuomi-drama-factory`。下列路径均相对此目录；原稿位于 `/Users/liuyuxiang05/Liu/山海拾遗`。

**规格：** `docs/superpowers/specs/2026-09-26-screenwriting-craft-design.md`，用户已批准。

## 文件结构

- 修改 `nuomi-drama-scripts/SKILL.md`、`README.md`：路由与使用方式。
- 修改 `nuomi-drama-scripts/references/{story-development,screenplay-craft,short-drama-rhythm,review-rubric}.md`：人物、场景、节奏与审稿。
- 创建 `nuomi-drama-scripts/references/{character-and-dialogue,language-editing}.md`：对白策略与语言编辑。
- 修改 `nuomi-drama-scripts/project-template/{README.md,project.yaml}` 与 `manuscript/{03_剧本圣经,04_人物表}.md`、`templates/episode-card.yaml`、`reviews/structure/结构审查.md`：新项目与旧项目兼容。
- 创建 `nuomi-drama-scripts/project-template/templates/scene-card.md`、`project-template/reviews/craft/样稿审查.md`：轻量工作材料。
- 修改 `nuomi-drama-scripts/project-template/sources/source-ledger.md`：参考吸收边界。
- 修改 `nuomi-drama-scripts/tests/fixtures/skill-scenarios.md`：可人工执行的行为场景。
- 创建 `nuomi-drama-scripts/evaluations/2026-09-26-shanhai/{README.md,review.md,candidates/E001.md,candidates/E038.md,candidates/E045.md}`：对照验证，不进入生产稿件。
- 不修改已有脏文件 `nuomi-drama-scripts/tools/build_docx.py`。

## 任务 1：基线与上下文

- [x] 检查目标路径的 Git 根、Weaver 策略、现有改动；对无交叉的 Markdown 改动使用精确文件路径，保留其他任务工作。
- [x] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 nuomi-drama-scripts/tests/skill_contract.py`，预期 `PASS: nuomi-drama-scripts skill contract`。
- [x] 阅读候选三集及相邻集 E002、E037、E039、E044、E046，核对圣经、节拍和既有审查。记录原稿 SHA-256 用于结束时只读复核。

## 任务 2：人物、对白、场景与语言工艺

- [x] 在 story-development 中加入私人欲望、自我欺骗、两难代价和关系利益；对照抽象形容词与可演事件。
- [x] 新建 character-and-dialogue：分别说明目标、策略、碰壁换招、潜台词、声音差异与直说的边界；至少提供两组原创正反例。
- [x] 修改 screenplay-craft：写清场景推进和余波/过渡场景的不同要求，加入因果检查及失败返工路径。
- [x] 新建 language-editing：逐类示范作者说理、无效否定、重复、工整宣言与假停顿的处理；保留有效否定、真实打断和必要说明。
- [x] 修改 short-drama-rhythm：明确开场失衡、集内兑现、因果钩子与下集承接；移除逐场不可逆和固定刺激配额要求。

## 任务 3：入口、模板与审稿

- [x] 修改 SKILL.md 到 0.4.0：保留原有机器契约词，加入样稿校准、分阶段模块加载、旧项目局部改稿和语言编辑路由。
- [x] 修改人物表和圣经模板：关系双方诉求、误解、代价、知识边界；不以口头禅数量验收人物。
- [x] 扩展 episode-card，新增 scene-card，分析字段仅存工作材料。
- [x] 修改 project.yaml 添加可选人工流程记录 `craft_calibration`（不声称程序强制执行）；不更改既有 schema_version 或导出器语义。
- [x] 修改 review-rubric 与结构模板，新增样稿审查模板：引用原句、指出观众影响、区分创作/连续性/格式结论，P1 返工，终集允许收束。
- [x] 更新 README、模板说明与来源台账：解释新流程、原项目兼容、仓库方法取舍、未知平台效果。
- [x] 扩展 skill-scenarios：涵盖无样稿批量写作、静观、真实打断、互换台词、旧稿改事实、终集收束、模板字段不外泄。

## 任务 4：三集候选稿与证据审查

- [x] 在 evaluations README 记录输入路径、原稿哈希、编写者自审身份、上下集约束与三集选择依据。
- [x] 写 E001 候选稿：保留雨、残简、尾缺、署名和师父空席；强化即时取舍，不虚构额外能力。
- [x] 写 E045 候选稿：保留闻照到达、岸路分流、精卫不被劝停、旁注线索；让捕鸟者利益和协商代价可见，不制造无因冲突。
- [x] 写 E038 候选稿：保留砾背死亡与不能复活，保护“不用再热水了”；以动作和关系呈现悲伤，删去向审稿者解释设定的文字。
- [x] 写 review：逐集保留项、问题、修订证据、相邻集影响与未决质量判断；未经用户读样，不把样稿标为 passed。

## 任务 5：验证与交付

- [x] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 nuomi-drama-scripts/tests/skill_contract.py`，预期 PASS。
- [x] 分别运行 `PYTHONDONTWRITEBYTECODE=1 python3 nuomi-drama-scripts/tools/test_markdown_episode_contract.py -p nuomi-drama-scripts/evaluations/2026-09-26-shanhai/candidates/E001.md -n 1`，对 E038/-n 38、E045/-n 45 同样执行，预期每项 PASS。
- [x] 只读检查技能本地引用、候选稿 frontmatter 与正文一致、未包含工作卡字段；复核原稿 SHA-256 不变。
- [x] 人工执行扩展的 skill-scenarios 并在 review 逐项记录结果，明确这不是独立模型实验或用户评审。
- [x] 对本任务文件执行 `git diff --check` 并检查 diff，按清单暂存和提交；不混入已有 build_docx.py 改动。
- [x] 向用户交付技能入口、三集对照材料、验证结果和样稿待人工校准的边界。

## 自检

规格第 1—3 节映射任务 1；第 4—5 节映射任务 2—3；第 6—7 节映射任务 3—5；第 8 节映射任务 3；第 9 节由本计划及精确路径提交满足。此次无运行时代码改动，不新增模仿实现的关键词单测，不运行全产品测试。

## 执行记录

2026-09-26：任务 1—5 的文件改动、候选稿、审查和验证已完成；用户对候选稿的审美校准仍保持 needs_review，未冒充通过。

- 在当前非主分支按精确路径原地修改 Markdown/YAML，未新建 worktree；没有改动运行时代码或已有 build_docx.py 内容。
- writing-skills 要求的独立应用测试增加到验证环节：旧版复现了静观场景被要求不可逆的缺陷；新版同题正确处理该边界。实际输出及限制已记录。
- 独立候选审查提出三个 P2，已改；新增 E043 只读核对用于澄清“被困”而非“坠落”。
- 既有 README 失效链接、入口渲染范围与固定首交清单一并修正。
- 技能契约和三集 Markdown 契约通过；附加元数据/场次/汉字统计/正文隔离检查通过；原稿及已有 build_docx.py 哈希不变；本地链接通过。
- 提交按清单隔离，本地保留修改，不推送、不发布、不替换原剧本。
