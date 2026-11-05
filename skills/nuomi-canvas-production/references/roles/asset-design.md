# 资产设计岗位手册（造型师 / 场景师 / 道具师）

写或审资产 brief 时按本手册逐条检查。三个岗位共用同一份 brief 结构（`AssetDesignBrief`），区别只在 `asset_kind`：角色 `character`、场景 `scene`、道具 `prop`。造型师、场景师、道具师各自只对本人岗位负责的字段签字，不能互相代签。

## 一、facts：每条都要有出处

- 每条 fact 必须带 `source.episode` 和 `source.quote`，且 quote 是剧本正文里**逐字**存在的片段（比较时只忽略空白字符，不忽略标点或同义词）。
- quote 写"原文那半句"，不要写概括：要 `"四十来岁的男人"`，不要 `"大概四十岁"`。
- `field` 从枚举里选（角色：age/gender/hair/face/body/wardrobe/carried_item/other；场景：location/era/weather/time_of_day/light/landmark/material/color/scale；道具：material/color/scale/state/owner/other）。一个字段对应一条 fact，不合并。
- 剧本没写的就是没有出处：要么删掉这条 fact，要么把它降级成 design 里的创作补充，绝不能伪造 quote。
- 现有工具：`creation.asset_design.facts.resolve_fact_sources(brief, documents)` 会把定位不到的 quote 报成 `brief_source_missing`。

## 二、design：只写创作，不写别人的活

design 是**本岗位的创作决定**，只写"长什么样"。

- 该写：剪影、脸型、发型、服装件与配色、材质、做旧状态、地标、尺度、道具持有关系。
- 不该写：风格词（"3D 国漫""赛博朋克""水墨"）、渲染参数（采样步数、比例、模型名）、镜头语言（景别、机位、运镜）、画布节点 ID、文件名、URL。风格由软件的 `style_fragment` 统一注入，写进 design 会重复注入并互相打架，校验器报 `brief_style_duplicate`。
- 不写"参考图风格""和某某图一样"这类无法执行的描述；要复用的图走节点绑定，不写进文字。
- 一份 brief 只描述一个资产。角色的每个变体单独建 variant brief，不把日常装和战损装塞进同一份 design。

## 三、consistency：锚点 ≥2

- `consistency.anchors` 至少两条，写**跨镜头可核对**的识别特征，例如"褪色赭红运动夹克""左眉一道旧疤"，让下游一眼判断"还是不是这个人/这个地方"。少于两条只报警告（`brief_anchor_thin`），但审稿时应视为不合格。
- `consistency.forbidden` 写明确不能出现的东西，例如"帽子""眼镜""血迹"。写进 forbidden 的东西同时会进 negative，编译器会主动避让。
- anchors 与 forbidden 不要互相否定：不要把锚点里的夹克又写进 forbidden（校验器按 wardrobe 与 negative 交叉比对报 `brief_fact_conflict`）。

## 四、render：材质与负面，不写风格

- `render.material` 写材质倾向（"柔和动画材质""粗糙水泥"），`render.volume` 写体积/厚度一类的形体要求。
- `render.negative` 写要压掉的画面问题；风格片段不写在这里，也不写进 `render.material`。
- negative 与 forbidden 会合并成最终负面词，重复写没有额外收益，挑最关键的几条即可。

## 五、禁词清单

设计稿正文（design 的字符串字段）出现下列词直接阻断（`brief_noise_term`）：

- 水印
- 文字
- 真人照片
- 参考图风格
- 摄影级

要表达"干净画面"就写具体内容（"纯色背景""无标识"），不要用这些会让模型加重纹理的词。

## 六、变体必须挂基础

- 建变体前先有基础 brief（`assets/<kind>/<id>/brief.v1.json`）；变体落在 `assets/<kind>/<id>/variants/<variant>/brief.v1.json`，缺基础直接报错（"variant requires an existing base brief"）。
- 变体只写与基础不同的部分，基础里已定的脸型、体型不重复；变体的 `asset_id` 与基础一致，只填 `variant_id`。
- 改基础前先确认没有变体依赖它的独有细节；改完要回看受影响变体。

## 七、360 场景补充（场景师）

- 生成前先读 [360 场景的一次提交与球面验收](../scene-360.md)。必须先完成覆盖整球的空间/投影准备，不能靠连续付费试图补齐背面；失败时停止自动重抽。
- `pano` 只给需要 360 的场景：写 `front_anchor`、`back_anchor`，以及 `viewpoints`（`label` 唯一、可读，`yaw` 在 -180~180、`pitch` 在 -90~90、`fov` 在 10~120）。
- 视角切片用 `creation.asset_design.pano.slice_pano`，同参数同字节；视角 label 要能对应到分镜里的具体机位，不要写"视角1/视角2"。

## 八、准入（新建资产才需要）

- 新建资产必须先有人确认：`admission.confirmed = true`，并填 `confirmed_by`（谁确认的）与 `basis`（依据：剧本明示 / 用户指定 / 美术补充）。缺任一项报 `brief_admission_missing`。
- 已有资产修改不重复走准入，但设计变更仍需在回复里说明改了什么、为什么。

## 九、提交前自检清单

- [ ] 每条 fact 的 quote 能在对应集正文里逐字搜到。
- [ ] design 里没有风格词、渲染参数、镜头语言。
- [ ] consistency.anchors ≥ 2，且与 forbidden 不互相矛盾。
- [ ] render 里没有风格片段；negative 与 forbidden 不重复堆叠。
- [ ] 设计稿正文没有出现禁词清单里的词。
- [ ] 变体已挂在基础 brief 上；变体只写差异。
- [ ] 新建资产已填 confirmed / confirmed_by / basis。
- [ ] 360 场景的 viewpoints 范围合法、label 可读。

## 十、校验入口

- 函数：`creation.asset_design.validator.validate_brief(brief, documents=..., style_fragment=...)`，错误码 `brief_style_duplicate`、`brief_noise_term`、`brief_fact_conflict` 阻断，`brief_anchor_thin` 仅警告。
- 编译：`creation.asset_design.compiler.compile_brief(brief, style_fragment=..., params=...)`，同输入必得同 `prompt_text` 与指纹；改了 brief 就重新编译，不复用旧提示词。
- CLI：`nuomi --project PROJECT canvas assets validate <kind>/<id> --canvas CANVAS --style-fragment "..."`；写入用 `brief write`，编译用 `compile`。输出是 JSON 信封，`ok: false` 时按 `error.code` 定位。

审稿结论写法：先列阻断项（错误码 + 具体字段 + 改法），再列建议项，最后给"通过 / 需修改"。不写"看起来还行"这类无法复核的结论。
