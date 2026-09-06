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

1. [调研报告](./01_调研报告.md)：红果公开规则、监管要求、GitHub 项目与本地 Nuomi 资产。
2. [产品设计规格](./docs/superpowers/specs/2026-08-02-nuomi-script-design.md)：边界、数据结构、工作流、质量闸门与 DOCX 适配方案。
3. [Skill 设计](./03_Skill设计.md)：可复用的 nuomi-drama-scripts Skill/提示词骨架。

## 当前结论

- 默认首交规模：30 集；“最终 80 集”只作为可配置目标，不视为红果通用规则。
- 首交格式：DOCX 为主，建议同时保留 Markdown 单一来源和 PDF 复核件。
- 单集字数：项目内部质量底线先设为正文不少于 300 个中文字符；这不是红果公开统一标准。
- 参考排版：以用户指定的《68岁的婚礼策划师_前30集剧本.docx》为样本建立模板画像，不修改原文件。
- 研究日期：2026-08-02。平台规则、征集活动和漫剧入口会变化，正式投稿前必须重新核验。

## 下一阶段

## 使用路径

- 只要 Markdown：完成简报、圣经、节拍和审查闸门后，输出指定集数或 `deliverables/screenplay.md`。
- 要前 30 集 DOCX：E001-E030 齐全，并通过结构、连续性、版权、合规和渲染 QA 后再编译。
- 要续写/改稿：读取现稿和 reviews，先输出影响分析，再写受影响集数。

工具入口：`tools/export_screenplay.ps1` 生成 Nuomi 漫剧工厂 handoff，`tools/test_markdown_episode_contract.ps1` 检查单集 Markdown，`tools/build_docx.ps1 -RequireDeliveryGate` 在完整交付门槛通过后编译 DOCX。

## Nuomi 漫剧工厂 交接

`screenplay.md` 只包含集标题、时长、场景、人物、动作、对白和必要音效；禁止图像 prompt、视频 prompt、模型参数和 provider 字段。

本 Skill 不负责生成真实 30 集内容；创作仍按“选题 → 大纲 → 剧本圣经 → 节拍表 → 前 30 集 → 审查 → 可选 DOCX”推进。
