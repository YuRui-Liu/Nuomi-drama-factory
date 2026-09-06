# Dramaclaw 文件交接

## 输入

通过审查的单集 Markdown、项目交付配置和 handoff 开关。

## 必须规则

- 生成 `deliverables/screenplay.md`，按 E### 数字顺序拼接连续剧本正文。
- 只保留集标题、时长、场景标题、人物行、动作、对白和必要音效。
- Dramaclaw 只读取这个文件，不通过 API、数据库、共享状态或隐式调用连接本 Skill。
- frontmatter、审查内部备注、版权台账和媒体生成字段不写入 handoff 正文。

## 交付字段

`screenplay.md`、生成时间、源文件列表、集数范围、源版本、导出状态。

## 阻断条件

单集缺稿、错序、结构契约失败；handoff 含 `image_prompt`、`video_prompt`、`provider`、模型参数或资产字段；权利状态阻断。

## 自检问题

- 文件离开本 Skill 后，Dramaclaw 是否能只凭正文导入？
- 是否能从源单集重建同一 handoff？
- 是否完全没有媒体运行时依赖？
