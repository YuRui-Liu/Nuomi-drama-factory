# 资产设计 brief 与专业岗位编译链设计

日期：2026-10-19。分支：`feat/canvas-codex-production`。
状态：设计已由用户逐节确认（范围 A、架构 A3、存储 B1、结构 S2、输出 C2、校验 V3、确认 D1+D3、放行 E1、引入本体论）；本文待书面审阅，尚未实施。

## 1. 背景与目标

当前画布 + skill 的漫剧生产在资产环节阻塞：角色与场景出图提示词由负责人一次性自由书写，没有经过造型师、场景师、道具师的专业整理，出现风格重复、事实与画面矛盾、禁词混入等噪声；场景 360 与切片能力已存在但未接成流程。

本设计把资产生产拆成**结构化中间产物 + 确定性编译 + 硬校验**三段：

```
剧本事实 → 岗位 brief（项目级 JSON）→ 编译器 → 结构化编译产物 → 资产图节点
```

目标是让"专业度"来自可审阅的 brief 与可测试的编译规则，而不是某一次模型自由发挥。本切片只覆盖资产链（角色、场景、道具）+ 场景 360，不覆盖分镜表、导演链、H3 视频与配音。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 切片 | A 切片：资产链打样，先立规矩再复制到分镜链与声音链 |
| 岗位能力落点 | A3 混合：schema/编译器/校验器在后端（唯一真源、可测试）；岗位方法论、检查表、追问话术在 skill（外部可调用、可迭代） |
| brief 存放 | B1 项目本地 JSON，可 diff、可 review、外部脚本可直接读写 |
| brief 结构 | S2 三层分离：`facts`（剧本出处）/ `design`（岗位创作）/ `consistency`（锚点与禁用） |
| 风格归属 | 风格是**软件设置**，不进 brief；由节点或项目风格设置解析后由编译器注入 |
| 编译产物 | C2 结构化产物：`compiled` + `prompt_text` + `style_fragment` + 指纹；节点保存提示词与结构化快照 |
| 校验门 | V3：结构 + 事实溯源 + 渲染约束；error 阻断，warning 放行并记录 |
| 新建资产/变体的确认 | D1 确认记录文件 + D3 画布资产审阅停点；确认落为本体 `definition.confirm`（user_only） |
| 错误放行 | E1 无强制放行；error 一律阻断 |
| 本体论 | 只承担身份、关系、确认、schema 版本；brief 正文仍在 JSON 文件 |
| 资产层级 | **项目级**，不是集级；集只引用项目资产，不复制 |
| 新增倾向 | 只在本集确有需要时新增资产或变体，且需二次确认；默认拒绝 |

## 3. 范围与非目标

**纳入**
- 三类资产 brief：角色、场景、道具。
- 项目级 brief 存储、编译器、V3 校验器、公开 CLI。
- skill 侧岗位手册（造型师／场景师／道具师）与资产准入手册。
- 本体对象/关系/确认的最小接入。
- A2 场景 360：scene brief → 360 生成输入（场景提示、主图、背面图）→ 定视角切片 → 切片作为资产子图。

**非目标**
- 分镜表、分镜导演、执行导演、H3 视频导演、配音（后续切片）。
- Web 端 brief 编辑器；本期 brief 只有 CLI 读写与节点内展示。
- 新画布节点：brief 不是节点，画布仍只有资产图节点。
- 自动批量重跑付费生成；批量变体探索。
- 改造现有资产目录表结构或迁移历史资产。

## 4. 数据流

```
剧本节点（唯一事实源）
  │ skill 读剧本场次 + 视觉连续性参考
  ▼
岗位手册（造型师 / 场景师 / 道具师）
  │ 产出 brief.v1.json；新资产/变体先出提案
  ▼
资产 brief（项目文档区，项目级）
  │ nuomi canvas assets validate / compile
  ▼
编译器：facts + design + consistency
        + 软件风格片段（extension_styles.compile_prompt_fragment）
        + 节点参数（画幅/分辨率/参考图顺序，只读）
  ▼
CompileResult{compiled, prompt_text, style_fragment, 指纹, issues}
  │ skill 显式 canvas patch 写入资产图节点
  ▼
资产图节点（prompt_text + 结构化快照 + 指纹）
```

两条硬边界：

- **编译器只组装、不创作**：岗位没写的字段不补写，缺字段报 issue 让 skill 回去补。
- **风格与参数不进 brief**：brief 修改不需要重设画幅；画布改画幅也不会让 brief 失效。

## 5. 组件边界

新增包 `src/novelvideo/creation/asset_design/`，模块单一职责：

| 模块 | 职责 | 不做 |
| --- | --- | --- |
| `models.py` | `AssetDesignBrief` v1、`CompileResult`、`Issue`、`AdmissionProposal` | 不解释数据、不做规则 |
| `store.py` | 项目级 brief 路径读写、hash 校验、原子写 | 不做 schema 语义判断 |
| `facts.py` | 把每条 fact 的 `{episode, quote}` 在剧本正文里逐字定位 | 不判断剧情合理性 |
| `compiler.py` | 纯函数 `compile(brief, style_fragment, params) → CompileResult` | 不补写设计、不写节点 |
| `validator.py` | V3 规则，返回 error/warning | 不调用模型 |
| `admission.py` | 新资产/变体提案、复用检索、确认校验 | 不直接改资产目录表 |
| `pano.py` | scene brief 视角表 → 切片 → 注册资产子图 | 不做 3D 渲染 |
| `cli.py` | `canvas assets ...` 子命令 | 不直连供应商 |

**复用清单（不重写）**
- `extension_styles.compile_prompt_fragment`：固定键序 medium→rendering→lighting→color→camera→constraints 的风格片段。
- `creation/production_documents.ProductionDocuments`：项目相对路径、hash 与原子写。
- `director_world/pano_views.equirectangular_to_perspective`：2:1 全景 → 透视切片。
- `freezone.scene_360`：主图 + 背面图 + 场景提示 → 360 全景。
- `creation/ontology.py`：定义版本化、对象/关系实例、user_only 确认。
- `script_creation/episode_asset_supplement`：集级候选清单（保持只读，不写目录）。

## 6. Brief schema v1

路径（项目文档区根下）：
- 基础资产：`assets/<asset_kind>/<asset_id>/brief.v1.json`
- 变体：`assets/<asset_kind>/<asset_id>/variants/<variant_id>/brief.v1.json`

`asset_kind` ∈ `character | scene | prop`。变体必须存在基础 brief，否则 `brief_variant_without_base`。

字段（角色示例，场景与道具用同构的 `design` 子块）：

```json
{
  "schema_version": 1,
  "asset_kind": "character",
  "asset_id": "lin-du",
  "variant_id": null,
  "facts": [
    {"field": "age", "value": "约40岁", "source": {"episode": 1, "quote": "四十来岁的男人"}},
    {"field": "hair", "value": "稀疏短黑发", "source": {"episode": 1, "quote": "头发稀疏"}}
  ],
  "design": {
    "silhouette": "普通偏瘦，肩略窄",
    "face": "略圆脸，颧骨平缓",
    "wardrobe": [
      {"piece": "运动夹克", "color": "褪色赭红", "material": "涤纶", "state": "旧，拉链半开"}
    ],
    "distinctive": ["深棕软布背包"]
  },
  "consistency": {"anchors": ["赭红夹克", "深棕背包"], "forbidden": ["帽子", "眼镜"]},
  "render": {"material": "柔和动画材质，无毛孔", "volume": "真正 3D 体积感", "negative": ["摄影皮肤纹理", "胡茬", "文字水印"]},
  "admission": {"confirmed": true, "confirmed_by": "user", "basis": "项目既有角色"}
}
```

按资产类型的 `design` 子字段：
- 角色：`silhouette`、`face`、`hair`、`wardrobe[]`、`distinctive[]`。
- 场景：`space`（空间布局）、`scale`（尺度）、`entrances[]`（出入口与通路）、`light_sources[]`（光源身份与位置）、`states[]`（稳定环境状态）。
- 道具：`scale`、`material`、`grip`（持握方式）、`states[]`。

约束：
- `facts[].field` 取固定枚举，避免自由命名导致校验不可判定：
  - 角色：`age`、`gender`、`hair`、`face`、`body`、`wardrobe`、`carried_item`、`other`。
  - 场景：`location`、`era`、`weather`、`time_of_day`、`light`、`landmark`、`other`。
  - 道具：`material`、`color`、`scale`、`state`、`owner`、`other`。
- `render` 不含 `style`；风格由软件设置提供。
- `facts` 每条必须有可逐字定位的 `source`；无出处的创作信息只能进 `design`。
- 变体 brief 不得改动基础 `consistency.anchors`，只能追加。
- `admission.confirmed` 只是文件旁证，硬凭证在本体确认记录；两者缺一即拒绝编译。

### 6.1 定版图与参考图规则（外部依据）

来源：Higgsfield Hell Grind 生产手册、LibTV prompt 铁律。只吸收结构性结论。

- **角色定版图默认三面板**：脸部特写、正面全身、背面全身；全图**只有一处可读的脸**。可选变体：正面全身去掉头部，避免远景从模糊小脸取脸。
- **定版图保持“无聊”**：中性灰底、平光、不烘焙电影感；质感由软件风格与镜头提示词承担，否则角色会把某种光感带进每一场。
- **点改不整图二次过模型**：衣服、伤、血等局部变化在定版图上做点改后**用蒙版叠回原图**；整图二次生成会毁纹理并让表情变“塑料”。
- **场景定版用 3/4 视角**，留一个锚点物（柱、灯、沙发），全场只允许一套光逻辑（一个光源、一个投影方向）。
- **每张参考图声明角色**：`reference_roles[]` 写明“取什么、不取什么”，例如“只取空间与材质，不继承构图、角度与调色”。
- **描述符逐字复用**：编译出的资产描述符在每次生成中原样使用，不缩写、不换词。

## 7. 本体论边界

本体只承担四件事，不存设计正文（定义字段仅简单类型、最多 50 个、不可含嵌套与 `_id` 后缀）。

| 用途 | 定义 | 说明 |
| --- | --- | --- |
| 设计身份 | 对象 `character_design` / `scene_design` / `prop_design` | 字段 `asset_ref`、`variant_ref`、`brief_path`、`brief_hash`、`schema_version`、`status` |
| 变体从属 | 关系 `design_variant_of`（custom → character/scene） | 数据层禁止无基础变体 |
| 剧本依赖 | 关系 `design_depends_on_script`（custom → `story_fragment`） | 剧本块变更可反查失效 brief |
| 二次确认 | `definition.confirm`（user_only）+ 对象创建 | `basis` 记录复用检索结果与证据引文 |

分工：**brief 文件是内容真源；本体对象是身份、关系、确认与 schema 版本真源**。编译器同时读两者，缺一拒绝。

## 8. 编译器契约

```
compile(brief, style_fragment, params) -> CompileResult
```

输出（C2）：

```json
{
  "compiled": {"subject": "...", "design": ["..."], "material": "...", "negative": ["..."], "params": {...}},
  "prompt_text": "...",
  "style_fragment": "...",
  "fingerprint": {"brief_hash": "...", "style_id": "...", "style_revision": "...", "design_schema_revision": 1, "compiler_version": "1"},
  "issues": []
}
```

规则：
- **确定性**：同一 `brief + style_fragment + params` 必得同一输出；字段顺序固定。
- **只组装**：不新增设计内容，不加形容词，不改写 facts。
- **风格只注入一次**：`style_fragment` 处于 `prompt_text` 的固定段位；brief 中出现风格片段即 `brief_style_duplicate`。
- **参数只读**：`params` 仅来自节点当前设置（画幅、分辨率、参考图顺序）；缺失则由调用方显式传入，不在 brief 中写死。
- **只读副作用**：`compile` 不写节点、不触发生成；写节点由调用方显式 `canvas patch`。

## 9. 校验规则（V3）

| 码 | 级别 | 触发 | 修复 |
| --- | --- | --- | --- |
| `brief_schema_invalid` | error | 字段缺失/类型错/未知字段 | 改 brief |
| `brief_source_missing` | error | fact 引文在剧本正文找不到逐字片段 | 改出处或删 fact |
| `brief_fact_conflict` | error | design 与 facts 矛盾（如 facts「无胡子」vs design「胡茬」） | 改 design 或补 fact |
| `brief_variant_without_base` | error | 变体缺基础 brief 或本体关系 | 先建基础 |
| `brief_admission_missing` | error | 未经 D1+D3 确认 | 补 admission |
| `brief_asset_unknown` | error | 资产 ID 不在项目目录 | 走新建流程 |
| `brief_style_missing` | error | 节点风格未设或已下架 | 设风格 |
| `brief_style_duplicate` | error | brief 重复出现风格片段 | 删掉 |
| `brief_noise_term` | error | 禁词：水印、文字、真人照片、参考图风格等 | 删 |
| `brief_stale_script` | error | 依赖剧本块已变更 | 重核 facts 后重编译 |
| `brief_anchor_thin` | warning | 锚点少于 2 个 | 建议补 |
| `brief_material_missing` | warning | 缺材质/版型 | 建议补 |

分级原则：**事实矛盾、缺出处、未确认、风格冲突一律阻断**；风格化建议只记录。

## 10. CLI 契约

```bash
nuomi --project P canvas assets brief read   <kind>/<asset_id>[/variants/<variant_id>]
nuomi --project P canvas assets brief write  <kind>/<asset_id>[/variants/<variant_id>] --file brief.json --expected-hash H
nuomi --project P canvas assets validate     <ref>
nuomi --project P canvas assets compile      <ref> --node NODE_ID
nuomi --project P canvas assets compile      <ref> --style STYLE_ID --aspect 3:2 --resolution 1K
nuomi --project P canvas assets propose      <kind> --name <名称> --body-file proposal.json
nuomi --project P canvas assets admit        <kind>/<asset_id>[/variants/<variant_id>] --decision accept|reject
nuomi --project P canvas assets pano slice   <kind>/<asset_id> --yaw 120 --pitch 0 --fov 60 --out slice.png
```

约定：
- 读命令只读；`compile` 只输出 JSON，不写画布。
- 退出码：`0` 通过（可含 warning）、`2` 存在 error、`3` 冲突（hash 或 revision）、`4` 输入非法。
- 所有输出为单一 JSON 信封 `{ok, data|error}`，skill 直接解析。
- 参数只来自节点或显式选项；不读环境变量推断设计。

## 11. 场景 360（A2）

1. scene brief 的 `space/entrances/light_sources` 编译出三段输入：场景提示、主图提示、背面图提示。
2. 通过现有 `freezone.scene_360` 生成 2:1 全景（三输入必填，沿用前端既有必填覆盖）。
3. 全景挂到 `pano360ViewerNode`；brief 的 `viewpoints[]`（`label/yaw/pitch/fov`）作为切片清单。
4. `canvas assets pano slice` 复用 `equirectangular_to_perspective` 输出定视角切片，注册为该场景的资产子图，供分镜引用。
5. 一致性地基：切片必须来自同一张全景；不允许不同切片来自不同生成批次（指纹校验）。

## 12. 错误处理、冲突与失效传播

- **brief 并发写**：沿用 `expected_hash`；冲突读回再合并，不盲写。
- **节点 revision**：写节点用 `canvas patch` 带当前 revision；409 重读，不自动重放旧操作。
- **陈旧结果**：`fingerprint` 变化后节点保留旧图但标 `stale`，不自动重跑付费生成，必须显式重跑。
- **剧本改版**：依赖的 `story_fragment` 变更 → `brief_stale_script`，阻断直到 facts 复核。
- **风格下架/资产删除**：编译直接 error，不静默替换。
- **无强制放行**：error 无 `--force`；只有修复后才能继续。

## 13. skill 侧交付

- `skills/nuomi-canvas-production/references/roles/character-designer.md`
- `.../scene-designer.md`、`.../prop-designer.md`
- `.../asset-admission.md`（何时允许新增资产/变体、二次确认话术、拒绝默认）
- 更新 `SKILL.md` 与 `references/cli.md`，把资产环节指向上述手册与新命令。

岗位手册只写方法论、检查表、追问话术与产出格式；不写死具体资产内容，不替代编译器校验。

## 14. 测试与验收

单元/契约（默认 `uv run pytest`，零成本）：

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_asset_design_brief.py` | schema 必填、枚举、变体格式、未知字段拒绝 |
| `tests/test_asset_design_facts.py` | 逐字命中/未命中、跨集引用、剧本改版失效 |
| `tests/test_asset_design_compiler.py` | 确定性 golden 快照、不补写字段、风格只注入一次 |
| `tests/test_asset_design_validator.py` | V3 每个 error/warning 各一例 |
| `tests/test_asset_design_cli.py` | JSON 信封、退出码、compile 只读、hash 冲突 409 |
| `tests/test_asset_design_ontology.py` | agent 无法 confirm、关系端点校验、schema_revision 不匹配拒绝 |
| `tests/test_pano_slice.py` | 同参数字节一致、越界拒绝、尺寸正确 |

golden fixture：`tests/fixtures/asset_design/lin_du.brief.v1.json`。

端到端验收（一次，需用户授权付费）：
1. 建林渡 brief → validate 无 error。
2. compile 输出提示词，人工对比：固有特征与风格分离、无重复风格、无禁词。
3. patch 节点 → 生成 1 张图 → 检查 3D 国漫风格与身份稳定。
4. 负向：删 fact 出处 → 阻断；只留变体 → 阻断；agent 直接 confirm → 失败。
5. 360：场景 brief → 生成全景 → 两视角切片 → 空间关系一致。

不做：ASR、多轮付费质量对比、自动批量重跑。

## 15. 本地依据

- `src/novelvideo/creation/professional_roles.py`、`professional_agents.py`（岗位契约与能力声明，当前为 proposal-only）
- `src/novelvideo/character_visual/domain.py`、`models.py`（`CharacterDesignBrief`、`CharacterVisualBible` 既有先例）
- `src/novelvideo/script_creation/episode_asset_supplement.py`（集级资产补充的保守规则与证据引文）
- `src/novelvideo/creation/ontology.py`（定义版本化、user_only 确认）
- `src/novelvideo/extension_styles/schema.py`（`compile_prompt_fragment`）
- `src/novelvideo/creation/production_documents.py`（hash 与原子写）
- `src/novelvideo/director_world/pano_views.py`（`equirectangular_to_perspective`）
- `src/novelvideo/freezone/skill_registry.py`（`freezone.scene_360` 输入契约）
- `skills/nuomi-canvas-production/SKILL.md`、`references/cli.md`
- `docs/superpowers/specs/2026-10-19-canvas-codex-production-design.md`
- `docs/research/2026-10-19-professional-agent-external-references.md`（外部专业参考）

自检：范围限定在资产链；brief 内容与本体身份已分离；风格与参数不进 brief；错误无放行通道；未把集级候选清单写成资产目录写入口。
