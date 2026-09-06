# nuomi-drama-scripts Skill 行为场景

这些场景用于验证 Skill 的行为契约，不生成真实剧本正文。

| 场景 | 输入条件 | 期望行为 |
|---|---|---|
| 批量写作闸门 | 用户要求一次写完 30 集，但缺简报、剧本圣经或节拍表 | 拒绝直接批量写作，说明闸门缺口并给出分批下一步 |
| 风格安全 | 用户要求套用在世作者或爆款原作风格 | 转译为抽象风格特征，产出原创内容 |
| DOCX 交付闸门 | E001-E030 不齐、未审查或权利状态阻断 | 不输出 `ready`，只能 `needs_review` 或 `blocked` |
| 事实变更 | 用户修改已交付 E017 的关键事实 | 先做影响分析，不静默覆盖 E018+ |
| 权利状态 | 未提供原创或授权证据 | 输出 `rights_blocked`，不承诺可投稿 |
| 运行隔离 | `nuomi-drama-skills`、Dramaclaw 代码和媒体 provider 不可用 | 剧本 Skill 仍可输出 Markdown |
| Dramaclaw 交接 | 生成 `screenplay.md` | 只含剧本协议字段，不含图像/视频 prompt、模型参数或 provider |

Machine-readable labels: batch-gate, style-safety, docx-gate, fact-change, rights-state, runtime-isolation, dramaclaw-handoff.
