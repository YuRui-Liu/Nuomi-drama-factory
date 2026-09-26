# nuomi-drama-scripts

> 独立漫剧剧本 Skill：面向红果漫剧交付与后续扩写的剧本创作工作台。

## 定位

nuomi-drama-scripts 是单独安装、调用和维护的“编剧创作层”，只负责：

- 选题、立意、三轴设计与项目简报
- 故事大纲、剧本圣经、人物/场景/道具资料
- 分卷节拍表、逐集剧本与连续性管理
- 结构审查、合规预检、来源与版权台账
- 首交前 30 集 DOCX，以及后续按目标集数增量扩写

明确不纳入本 Skill：文生图、角色定妆、分镜出图、视频生成、配音、剪辑、平台账号登录、自动投稿和任何平台接口调用。它不依赖、不修改、不注册到 `nuomi-drama-skills`，也不依赖 Nuomi 漫剧工厂 代码、API 或数据库。

需要交给 Nuomi 漫剧工厂 时，只导出 `deliverables/screenplay.md`；这是纯文件交接，不共享运行状态。

## 文档

1. [技能入口](SKILL.md)：阶段路由、创作标准和交付协议。
2. [项目模板](project-template/README.md)：项目文件结构、样稿与审查记录。
3. [来源台账](project-template/sources/source-ledger.md)：平台资料、创作参考及适用边界。

## 当前结论

- 默认首交规模：30 集；“最终 80 集”只作为可配置目标，不视为红果通用规则。
- 首交格式：DOCX 为主，建议同时保留 Markdown 单一来源和 PDF 复核件。
- 单集字数：项目内部质量底线先设为正文不少于 300 个中文字符；这不是红果公开统一标准。
- 参考排版：以用户指定的《68岁的婚礼策划师_前30集剧本.docx》为样本建立模板画像，不修改原文件。
- 研究日期：2026-08-02。平台规则、征集活动和漫剧入口会变化，正式投稿前必须重新核验。

## 0.4 创作流程

默认追求人物可信和短剧吸引力同时成立。先用私人欲望、关系利益和选择代价建立戏，再安排集内回报与后续期待；不设固定反转、金句或打脸配额。

1. 简报、圣经和节拍通过后，先写开篇、关系冲突、情绪回报样稿。
2. 按人物与场景工艺完成初稿，再单独做语言编辑。破折号按真实功能保留，避免作者解释和假悬念。
3. 审稿引用正文证据，用户读样校准后再批量生成。创作、连续性、格式和交付分别记录。
4. 旧项目先读相邻集与审查，做影响分析，用独立候选稿验证，不默认重写已确认故事。

新增 [人物与对白](references/character-and-dialogue.md)、[语言编辑](references/language-editing.md)；修订 [场景工艺](references/screenplay-craft.md)、[短剧节奏](references/short-drama-rhythm.md) 与 [审稿量规](references/review-rubric.md)。安静余波无需每场不可逆，终集无需强开新悬念。

`project.yaml` 的可选 `craft_calibration` 只记录人工流程，不是导出器自动门禁；旧项目配置仍适用。创作工作卡不进入交付剧本。效果验证见 [三集候选稿与审查](evaluations/2026-09-26-shanhai/README.md)，不能据此宣称已获专业认证或平台留存提升。

## 使用路径

- 只要 Markdown：完成简报、圣经、节拍、样稿校准及审查后，输出指定集数或 `deliverables/screenplay.md`。
- 要前 30 集 DOCX：E001-E030 齐全，并通过结构、连续性、版权、合规和渲染 QA 后再编译。
- 要续写/改稿：读取现稿和 reviews，先输出影响分析，再写受影响集数。

工具入口（Python 标准库实现，跨平台，不需要 PowerShell）：

```bash
python3 tests/skill_contract.py                                            # Skill 契约自检
python3 tools/test_markdown_episode_contract.py -p manuscript/episodes/E001.md -n 1
python3 tools/export_screenplay.py -m manuscript -o deliverables/screenplay.md
python3 tools/build_docx.py -o deliverables/剧本.docx -m manuscript --episode-count 30
python3 tools/build_docx.py -o deliverables/E017.docx -m manuscript --single-episode --episode-number 17
python3 tools/build_docx.py -o deliverables/剧本.docx -m manuscript --require-delivery-gate
python3 tools/audit_docx.py -p deliverables/剧本.docx --expected-episodes 30
```

`tools/nuomi_common.py` 是共用库；同名 `.ps1` 为 Windows PowerShell 等价实现，保留以便双平台使用。

## Nuomi 漫剧工厂 交接

`screenplay.md` 只包含集标题、时长、场景、人物、动作、对白和必要音效；禁止图像 prompt、视频 prompt、模型参数和 provider 字段。

仓库模板不附带一部可直接投稿的 30 集成品。调用本 Skill 时按“选题 → 大纲 → 剧本圣经 → 节拍表 → 样稿校准 → 指定集数创作与语言编辑 → 审查 → 可选 DOCX”推进。
