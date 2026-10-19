# 分镜表与执行导演 panel 规格设计（B 切片）

日期：2026-10-19。分支：`feat/canvas-codex-production`。
状态：设计已由用户逐节确认（F1 计划为唯一真源、G2 摘要列 + 结构化块、I1 影视拍摄语言、H2 确定性编译器、K2 软校验、L3 双轨升格、B/C 边界收口）；本文待书面审阅，尚未实施。

## 1. 背景与目标

画布已有两条互不相干的分镜路径：`scriptNode` 表由一次性通用 LLM 生成（`FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT`），导演计划由 `DirectorPlanner` 从剧本正文生成（`director_bridge.direct_canvas`），两者各说各话。截图里的分镜表甚至是文档形态，完全绕过了 `scriptNode`。

执行侧同样断裂：`generation_beats_for_group` 已把 `cinematography`、`creative` 注入 beat，但 `panel_description` 把它们 `json.dumps` 成世界坐标系（`"world_position": [0.7, 0, 0.3]`），模型收到的不是拍摄语言。

本切片把两者接成一条链：**导演计划是唯一真源，分镜表是它的投影与编辑面，panel 规格由确定性编译器产出拍摄语言**。

## 2. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 表的权威来源 | F1：`DirectorPlanRevision` 为唯一真源，`scriptNode` 行由计划投影；编辑回写计划 |
| 表的形态 | G2：约 10 个人读摘要列 + 每行 `director` 结构化块；向后兼容旧行 |
| “实拍生图”含义 | I1：仍是项目风格（3D 国漫），但按真实拍摄逻辑组织画面；不产出写实参考图 |
| panel 规格生成 | H2：新增确定性编译器 `panel_spec.py`，禁止把世界坐标写进成品文本 |
| 时长与分组 | K2：分组由计划决定；组时长超限/帧对齐不整除只给 warning，不阻断审阅 |
| 旧表迁移 | L3：旧表只读可用；显式“生成导演计划”后旧表转历史快照，新表接替；不在旧行上伪造结构化字段 |
| B/C 边界 | B 只保证“格位可寻址、组时长可见、panel_spec 可复用”；帧模式判定、ref 选择、H3 提示词、并行提交归 C |

用户约束（写入接口）：一个叙事组 = 一次导演台提交 = 若干段视频生成，总量建议 ≤ 15s；段模式（Ref2VA / 首帧 / 首尾帧）由规划器判断；C 侧可利用并行出视频。

## 3. 范围与非目标

**纳入**
- 计划 → 表投影（确定性、可单测）。
- 表格新增 `director` 块与 10 个摘要列；行携带 `group_id`、`segment_index`。
- 单元格编辑 → 计划编辑操作的映射与冲突处理。
- `panel_spec` 编译器；宫格与 H3 共用。
- 旧表双轨与显式升格。
- 失效传播与软校验提示。

**非目标**
- 首帧/首尾帧判定、Ref2VA 引用选择、引用数量上限。
- H3 提示词契约编译、提交、并行调度与重试。
- 配音、音乐、合成。
- 新增画布节点；不把每个段落变成独立节点。
- 自动重跑付费生成。

## 4. 数据流

```
screenplayNode（剧本正文，唯一事实源）
  │ directorWorkspaceNode prepare（现有 DirectorPlanner + schema + 校验）
  ▼
DirectorPlanRevision（groups / shots / cinematography / creative / static_panel / asset_requirements）
  │ project_plan_rows(revision)：确定性投影
  ▼
scriptNode 分镜表（10 列 + director 块 + group_id/segment_index + panel_spec）
  │ 单元格编辑 → edit_shot / edit_structure（canvas run 可调）
  ▼
新计划修订 → 重新投影（表不漂移）
  │
  ├─ 宫格：panel_spec.prompt_text 作为每格拍摄语言
  └─ H3：同一 panel_spec 作为段提示词结构输入
```

## 5. 组件边界

| 位置 | 职责 | 不做 |
|---|---|---|
| `director_plan/projection.py`（新） | `project_plan_rows(revision) -> rows`；计划 → 表行；固定字段顺序 | 不读写画布、不生成内容 |
| `director_plan/panel_spec.py`（新） | `compile_panel_spec(shot, group) -> PanelSpec` | 不补写缺失字段 |
| `FreezoneStoryScriptRow`（改） | 增加可选 `director` 对象；10 个摘要列保留 | 不破坏旧行反序列化 |
| `scriptNode`（改） | 新增 `directorBinding{episode, revisionId, projectionHash}`；有绑定走投影，无绑定走旧生成 | 不双写两套行 |
| `image_prompt.panel_description`（改） | 用 `PanelSpec.prompt_text` 替换 JSON dump | 不再拼 `world_position` |
| `director_bridge.project_draft`（改） | `description` 改用 panel_spec | 不再 `model_dump_json()` |
| 编辑回写 | 复用 `directorWorkspaceNode edit_shot` / `edit_structure` | 不新增节点操作 |

## 6. 分镜表结构

**场景级 GEO 空间图（新增）**：每个叙事组所属场景带一份 `geo_layout`，只含地标、左右、机位侧、不可越过的轴线，**不含人物与动作**；逐镜原样编译进 panel_spec，不得改写。

**摘要列（10）**：镜号、时长、画面（起点→动作→终点）、景别、机位、轴线、主光、表演、对白、声音。

**行内 `director` 块**：

```json
{
  "group_id": "G01",
  "segment_index": 1,
  "technique_intent": "克制的惊讶反应",
  "framing": {"shot_size": "近景", "angle": "平视", "composition": "左三分构图"},
  "blocking": {
    "axis": "A-B", "camera_side": "左侧", "screen_direction": "左→右",
    "subjects": [{"id": "lin-du", "screen_position": "画面左", "facing": "朝右", "gaze": "望向画外右后", "path": "原地不动"}]
  },
  "lights": [{"id": "L1", "type": "主光", "direction": "右后侧逆光", "temperature": "冷", "intensity": "低", "motivation": "天光"}],
  "performance": [{"id": "lin-du", "expression": "紧张但克制", "action": "右手护住喉咙", "intensity": "中"}],
  "props": [{"id": "backpack", "contact": "林渡背包带自然垂下", "state": "旧"}],
  "continuity": {"start_state": "手停在近前", "end_state": "手未接触", "handoff": "下一格转向右侧"},
  "panel_spec": {"sections": {"shot": "...", "axis": "...", "blocking": "...", "lighting": "...", "performance": "...", "props": "...", "continuity": "..."}, "prompt_text": "...", "negatives": ["..."]}
}
```

约束：
- `technique_intent` 只作为 C 侧手法/案例检索的查询锚点，**不进入** `panel_spec.prompt_text`；检索、适配校验、引用绑定与出处记录全部归 C 规格。
- 计划由现有 `directorWorkspaceNode prepare`（或 CLI 等价命令）生成；表只引用其 `revisionId`，不要求画布常驻导演工作台节点。
- `panel_spec` 是编译产物，投影时生成并随行保存；编辑后重新投影即刷新。
- 旧行没有 `director` 时，表格回退到 10 个字符串列，不报错。
- `group_id` 与 `segment_index` 必须与计划中的组/镜顺序一致。

## 7. panel_spec 契约

```
compile_panel_spec(shot, group) -> {sections, prompt_text, negatives, issues}
```

固定段落顺序：
1. `shot`：景别、机位、构图。
2. `axis`：轴线、机位侧、屏幕运动方向。
3. `blocking`：站位、朝向、视线、前景/背景关系。
4. `lighting`：光源身份、方向、色温、强弱、动机。
5. `performance`：表情、动作、强度。
6. `props`：道具位置、持握/接触状态。
7. `continuity`：起始状态、结束状态、与相邻格的交接。

规则：
- **人数头**：panel_spec 首行写明本格可见人数与计数物件（如“画面内恰好 2 人”），计数物件用正向锁。
- **屏幕方向只从机位说**：禁用“角色左边/右边”，只用“画面左/画面右”，位置以地标 + 米数表达。
- **禁止世界坐标数字**：坐标只用于推导“左/右/前景/背景”，成品文本不得出现 `[0.7, 0, 0.3]` 这类值；出现即 `panel_coordinate_leak`。
- **只组装**：字段缺失记 issue，不补写。
- **确定性**：同输入同输出，字段顺序固定，可 golden 单测。
- **负向独立**：负向词与正向段落分开存放，不混进 `prompt_text`。

## 8. 编辑与回写

| 列 | 计划字段 | 操作 |
|---|---|---|
| 画面（三段） | `visible_start_state` / `action` / `visible_end_state` | `edit_shot`；表格显示合并文本，编辑时按三段展开 |
| 景别/机位/轴线 | `shot_size` / `camera_angle` / `cinematography.axis` | `edit_shot` |
| 主光 | `cinematography.lights` | `edit_shot` |
| 表演 | `creative.performance.characters` | `edit_shot` |
| 时长 | `duration_seconds` | `edit_shot` |
| 镜号/分组 | 组与镜顺序 | `edit_structure`（显式重排） |
| 对白/声音 | 剧本来源 | 只读（改剧本回上游） |

编辑成功后重新投影；写回带当前 revision，冲突返回 `409`，重读后再改，不自动重放。

## 9. 错误、失效与提示

| 码 | 级别 | 含义 |
|---|---|---|
| `table_projection_stale` | error | 计划已改，表未重投影 |
| `table_edit_unmapped` | error | 目标单元格没有对应计划字段 |
| `plan_group_mismatch` | error | 行的组/序与计划不一致 |
| `panel_spec_missing_field` | error | 编译缺关键字段（如多人无轴线） |
| `panel_coordinate_leak` | error | 成品文本出现世界坐标数字 |
| `group_exceeds_attempt_limit` | warning | 组时长 > 15s，提交时会被拆分或建议回计划拆组 |
| `group_duration_not_aligned` | warning | 组时长可能无法被 H3 帧对齐整除 |
| `legacy_table_not_projected` | warning | 旧表尚未升格为计划投影 |

失效传播：剧本改 → 计划 stale → 表 `table_projection_stale` → 宫格待更新 → H3 片段待更新。只标记，不自动重跑。

## 10. 与 C 切片的接口

- **格位寻址**：每行提供 `group_id` + `segment_index` + 对应宫格格位与图片引用，C 的规划器据此把组拆成段。
- **拍摄语言**：`panel_spec.sections` 是 C 生成 H3 提示词的结构输入；B 不写 H3 提示词。
- **时长**：组时长与 `group_exceeds_attempt_limit` / `group_duration_not_aligned` 提示随行提供。
- **模式判定**：首帧 / 首尾帧 / Ref2VA 的选择、引用上限、并行提交全部由 C 决定；B 只保证输入齐全。

## 11. 迁移（L3）

- 旧 `scriptNode` 表原样可读、可继续旧链，标 `legacy_table_not_projected`。
- 用户或 skill 显式触发“生成导演计划”：从剧本重新规划，不读旧行当事实。
- 计划生成后：旧表转历史快照（只读），新表绑定 `directorBinding` 接替。
- 不做自动迁移，不在旧行上伪造 `director` 字段。

## 12. 测试与验收

| 文件 | 覆盖 |
|---|---|
| `tests/test_director_plan_projection.py` | 投影确定性；组/镜映射；行顺序；旧行无 `director` 退化 |
| `tests/test_panel_spec.py` | 段落顺序；无坐标数字；缺字段 issue；golden 快照 |
| `tests/test_storyboard_table_binding.py` | 编辑回写；剧本改动后 stale；L3 升格后旧表只读 |
| `tests/test_panel_description.py`（改） | 宫格 prompt 含段落、不含 `world_position` |
| `tests/test_storyboard_duration_hints.py` | 15s 与帧对齐两类 warning |

验收（一次，不重复付费）：
1. E01 剧本 → 计划 → 表显示 7 组 14 镜，组头与合计正确。
2. 改一格表演 → 计划同步 → 表重投影，宫格标待更新。
3. 宫格 prompt 为拍摄语言：有景别/轴线/主光/表演/道具，无坐标数字。
4. 旧项目表仍可读，升格后只读且新表接替。
5. 组时长 13s 无提示；20s 触达 `group_exceeds_attempt_limit`。

## 13. 本地依据

- `src/novelvideo/director_plan/`（cinematography、creative_contract、validation、store）
- `src/novelvideo/freezone/orchestration/director_bridge.py`（`direct_canvas`、`project_draft`）
- `src/novelvideo/narrative_groups/image_prompt.py`（`panel_description`）
- `src/novelvideo/freezone/text_node.py`（现有分镜表生成 prompt）
- `frontend/src/features/canvas/nodes/ScriptNode.tsx`、`ScreenplayNode.tsx`
- `frontend/src/features/canvas/production/lightweightEpisodeChain.ts`
- `docs/superpowers/specs/2026-10-19-asset-design-brief-professional-pipeline-design.md`（A 规格）
- `skills/nuomi-canvas-production/references/storyboard-direction.md`
- `docs/research/2026-10-19-professional-agent-external-references.md`（外部专业参考）

自检：范围限定在“计划 → 表 → panel 规格”；未把 H3 判定写进 B；未在旧行上造结构；风格仍由软件设置提供；错误无强制放行。
