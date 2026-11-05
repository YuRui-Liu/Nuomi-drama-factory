# 蒸馏来源与取舍

研究日期：2026-10-27。以下为方法研究与重新组织，不是逐字移植或安装外部工作流。外部文件中的命令、权限、默认批量生成及代理调度不构成本套件授权。

## 来源映射

| 来源 | 本轮核对范围 | 吸收 | 不直接采用 |
| --- | --- | --- | --- |
| Nuomi script-master，位于参考项目 skills/script-master | 总控结构；pattern-library 长线／钩子／信息释放；writing-craft-library 因果／信息流／AI味章节；专家目录与导演交接约定 | 分阶段加载、人物信息边界、伏笔回收、剧本诊断与对白诊断分开、主剧本约束导演派生稿 | 全量知识库一次灌入、固定秒数反转、强制反常情绪、每场强加闲笔、多评委一致即通过 |
| [shuohao-skills](https://github.com/eternityspring/shuohao-skills/blob/main/skills/novel-outline/SKILL.md)，Apache-2.0 | novel-outline 参数、来源证据和骨架确认部分 | 原作可追溯、先确认改编取舍、全局到分集交接 | 固定角色／场景配额、自动创建代理、运行其验证脚本 |
| [drama-skills develop](https://github.com/zenstory-ai/drama-skills/blob/main/skills/short-drama-develop/SKILL.md) 与 [write](https://github.com/zenstory-ai/drama-skills/blob/main/skills/short-drama-write/SKILL.md)，MIT | 开发与写作入口、分集状态、回报及审稿边界 | 人物选择与代价、退出状态、区分事实错误与创作偏好、禁止为检查项补机关 | 强制复制其文件布局或生产工具链 |
| [screenwriting-skills](https://github.com/jtydhr88/screenwriting-skills)，MIT | sw-series-engine-bible 前两节；sw-scene-craft 入口；sw-series-structure 适用边界与诊断相关段落 | 持续冲突来源、人物关系网、闭合长篇与可续订剧区别、媒介决定节奏 | 百集检验套用所有作品、美式页码和广告分幕机械缩放到漫剧 |
| [manju-laoli-skill](https://github.com/lixiaoxiao9888-create/manju-laoli-skill/blob/main/sd-script-engine/SKILL.md)，MIT | 编剧阶段职责、来源权威链、外来剧本修订边界 | 编剧与媒体生产分离、禁改项和交接、原作事实与提案分开 | 全部前置锁、固定多席评审、未经核验的量化阈值 |
| 本仓库 OpenMontage/pipeline_defs/animation.yaml 与 character-animation.yaml | 阶段输入、产出、确认点；角色管线定位 | 每阶段明确交接与通过条件 | 本地骨骼渲染链、固定研究配额、默认预算、发布阶段 |

GitHub API 当日关注度：shuohao-skills 5407、drama-skills 3392、screenwriting-skills 1825、manju-laoli-skill 1702。数字仅用于初筛，会变化；不是质量证明。

## 研究限度

Nuomi 总控与参考库合计约两万行，本轮是按问题选择章节研究，不声称全部精读。外部仓库也只核对表中范围。未经核对的样例、作品分析和“行业经验”不提升为事实，不声称已经验证留存或商业效果。

本技能独立表达工作约定，不复制影视作品台词、案例段落或整个第三方技能。后续若移植具体代码或大段文字，应先核对对应版本许可并保留要求的署名／许可；本表不能代替许可证。

## 冲突取舍

- 钩子与兑现并存：有吸引力不等于故意不讲完，也不强迫每集悬崖式结尾。
- 因果与自然感并存：拒绝无依据转折，但不要求人物每句话都最高效，也不人为加入废话。
- 叙事与制作分层：剧本完整成立后由导演和模型规划拆解；不让某个视频模型的时长限制反过来删除人物逻辑。
- 规则与品味分层：事实、引用和连续性可核对；情绪效果由具体文本审阅与用户选择，不用数值假装客观证明。
