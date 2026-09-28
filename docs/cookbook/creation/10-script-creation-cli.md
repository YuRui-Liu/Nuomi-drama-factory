# 剧本创作 CLI：从空白简报到制作交接

`nuomi --project PROJECT script` 对应[剧本创作工作台](09-script-creation-workspace.md)的项目 API。安装后的 `nuomi`、`novelvideo production` 和 `.venv/bin/python -m novelvideo.production_cli` 都接受同一组 `script` 子命令。下面以 `nuomi` 为例；项目参数也可由 `NUOMI_PROJECT` 提供，API 地址由 `NUOMI_API_URL` 提供。受保护部署沿用 `NUOMI_TOKEN` 或 `NUOMI_SESSION`，不要把凭据放在 JSON 文件或命令参数里。

每个命令只发送一次对应请求。`--dry-run` 打印方法、项目内路径、正文和查询参数，不访问网络。命令输出是 API JSON；任务提交只表示入队，取返回的 `task_id` 或 `queued_task_id` 后运行 `nuomi --project PROJECT wait TASK_ID`。超时或 `submission_unknown` 时，先用 `status` 和相应 `get/list` 查原任务与 mutation ID；不要立即换 ID 重发。409 冲突会原样显示服务端 `detail` 与 `current_revision_id`，需要读取最新修订并人工决定下一次请求。

所有有正文的命令接受 `--json '{...}'` 或 `--body-file path.json`，两者不能并用；文件必须是 UTF-8 JSON 对象。文档和任务写入的 `client_mutation_id` 由调用方提供：同一意图重试沿用同一个 ID，不同意图使用新的 ID。CLI 不自动取最新版、改写版本号、审阅候选、采纳提案或确认交接。路径中的 ID 用原始名称，CLI 负责 URL 编码；不要提供 `/`、`?`、`#`、`%` 或预编码片段。

## 1. 建立简报并生成首集

先创建真正的空白简报，并从结果的 `data.id` 与 `data.current_revision_id` 记录 `BRIEF_ID`、`REV_BRIEF_1`：

```bash
nuomi --project PROJECT script documents create --json '{"kind":"brief","title":"创作简报","markdown":"","client_mutation_id":"brief-create-1"}'
nuomi --project PROJECT script documents get BRIEF_ID
nuomi --project PROJECT script documents revisions BRIEF_ID
```

将下例保存为 `brief-settings.json`，用刚读到的当前修订替换占位符。`nuomi-script-settings` 注释是工作台识别的创作设置；自定义题材、受众、风格和故事想法均是普通文本，不必局限于预设选项。`mode` 必须是 `single` 或 `series`，生成请求的 `script_mode` 和 `episode_count` 必须与这里一致。

```json
{
  "base_revision_id": "REV_BRIEF_1",
  "markdown": "<!-- nuomi-script-settings\n{\"mode\":\"series\",\"episodeCount\":3,\"genrePrimary\":\"城市奇幻\",\"genreSecondary\":\"不融合\",\"audience\":[\"成年读者\"],\"roles\":[],\"era\":[],\"hooks\":[],\"style\":[\"克制的悬念\"],\"structure\":[],\"durationSeconds\":90,\"idea\":\"一位夜班司机发现乘客都来自明天。\"}\n-->\n\n# 创作简报\n\n## 故事想法\n一位夜班司机发现乘客都来自明天。\n\n## 创作边界\n先写第一集，不提前揭示终局。",
  "client_mutation_id": "brief-settings-1"
}
```

```bash
nuomi --project PROJECT script documents save BRIEF_ID --body-file brief-settings.json
nuomi --project PROJECT script generations start --json '{"mode":"bootstrap","brief_id":"BRIEF_ID","script_mode":"series","episode_count":3,"episode_number":1,"instruction":"先建立故事框架，再写首集","client_mutation_id":"bootstrap-1"}'
nuomi --project PROJECT wait TASK_ID
nuomi --project PROJECT script generations get RUN_ID
nuomi --project PROJECT script documents list
```

`bootstrap` 依次生成大纲、连续剧分集梗概、人物、场景、道具和**仅第一集**。从空白开始时，这些步骤的 `output.kind` 是 `document`，正文已直接保存；从 `steps[].output.document_id` 读取文档并审阅，不需要运行候选采纳命令。若目标文档已有非空正文，步骤的 `output.kind` 才是 `candidate`，原正文保持不变。继续第 2 集时，需先保存有实质内容的目标集梗概和前一集剧本，再显式提交 `mode: "continue"`、`episode_number: 2`；不能跳集。失败或暂停的原运行用 `generations retry RUN_ID`，设定或基线变化导致需重新定基时用 `generations rebase RUN_ID --json '{"client_mutation_id":"rebase-2"}'`。二者都由调用方明确决定。

仅当 `output.kind` 为 `candidate` 时，才读取候选、转成提案、检查差异，再按当前正文修订显式采纳：

```bash
nuomi --project PROJECT script generations candidate CANDIDATE_ID
nuomi --project PROJECT script generations review CANDIDATE_ID
nuomi --project PROJECT script proposals list DOCUMENT_ID
nuomi --project PROJECT script documents get DOCUMENT_ID
nuomi --project PROJECT script proposals accept --json '{"proposal_ids":["PROPOSAL_ID"],"base_revision_id":"CURRENT_REVISION_ID","client_mutation_id":"accept-1"}'
```

`review` 只创建提案，`accept` 才改正文。多个 `proposal_ids` 可作为一次原子采纳；不需要的提案用 `proposals discard PROPOSAL_ID`。现有制作剧本若要先在工作台打磨，使用 `documents import --json '{"episode_number":1}'`，它复制来源而不会自动回写制作来源。手工创建其他文档时 `kind` 可用 `outline`、`people`、`scenes`、`props`、`episode_synopsis`、`episode_script`；单集剧本需附 `episode_number`。`documents save` 必须传 `base_revision_id`、`markdown`、`client_mutation_id`，可用 `blocks: [{"id":"...","markdown":"..."}]` 保存有稳定块 ID 的条目。`documents revisions ID` 后可用 `documents restore ID --json '{"revision_id":"OLD_REV","base_revision_id":"CURRENT_REV","client_mutation_id":"restore-1"}'` 显式恢复；恢复产生新修订。

## 2. 改写与关联检查

改写请求必须锚定当前修订。`start/end` 是 Python Unicode **码点**偏移，不是 UTF-8 字节数或浏览器 UTF-16 单位；先读取正文并确认所选范围，正文变化后重新计算。`scope` 为 `selection`、`scene` 或 `episode`；`mode` 为 `dialogue`、`subtext`、`conflict`、`compress` 或 `custom`。`context_revisions` 按引用文档 ID 固定版本，`reference_proposal_id` 可指向待审提案。

```json
{
  "document_id": "EPISODE_DOCUMENT_ID",
  "base_revision_id": "EPISODE_REVISION_ID",
  "start": 0,
  "end": 24,
  "scope": "selection",
  "mode": "custom",
  "instruction": "保留人物动机，让对白更克制",
  "preserve": "不要改变谁先发现线索",
  "context_revisions": {"PEOPLE_DOCUMENT_ID": "PEOPLE_REVISION_ID"},
  "client_mutation_id": "rewrite-1"
}
```

将此对象保存为 `rewrite.json` 后运行 `script rewrites create --body-file rewrite.json`，用返回的 `queued_task_id` 执行 `wait`，再用 `rewrites get JOB_ID`、`rewrites list EPISODE_DOCUMENT_ID` 和 `proposals list EPISODE_DOCUMENT_ID` 查看结果。改写不会自动覆盖正文。

实际版本的关联检查示例；`context_revisions` 应包括单集文档及参与检查的参考文档的当前修订。若传 `proposal_id`，结果是“假设采纳”的检查，采纳后仍应重查实际版本。

```bash
nuomi --project PROJECT script checks start --json '{"episode_document_id":"EPISODE_DOCUMENT_ID","context_revisions":{"EPISODE_DOCUMENT_ID":"EPISODE_REVISION_ID","PEOPLE_DOCUMENT_ID":"PEOPLE_REVISION_ID"},"client_mutation_id":"check-1"}'
nuomi --project PROJECT wait TASK_ID
nuomi --project PROJECT script checks get RUN_ID
nuomi --project PROJECT script checks list --episode-document-id EPISODE_DOCUMENT_ID
```

事实问题若确为有意安排，可用 `checks intentional ISSUE_ID --json '{"reason":"第二集揭示的伏笔"}'` 记录原因。需要改写时，从 `checks get RUN_ID` 的问题证据与当前文档中明确选出目标，再运行 `checks rewrite-targets ISSUE_ID --json '{"target_document_ids":["TARGET_DOCUMENT_ID"]}'`。`checks targets ISSUE_ID` 读取**已选过**的目标文档 ID，用于恢复进度，并不是可选目标目录。返回任务仍需 `wait` 和审阅提案；其他文档不会被自动改写。

## 3. 关联文字资产并交接

`entities assets --asset-type character` 可查询现有角色；类型还有 `scene`、`prop`。实体来自 `people`、`scenes`、`props` 文档中已保存的块。先用 `documents get` 取 `block_id` 和当前修订，再显式关联现有 `asset_id` 或以 `create_text` 新建文字档案，不能两者同时提供：

```json
{
  "document_id": "PEOPLE_DOCUMENT_ID",
  "base_revision_id": "PEOPLE_REVISION_ID",
  "block_id": "CHARACTER_BLOCK_ID",
  "name": "林默",
  "client_mutation_id": "entity-1",
  "create_text": {"name":"林默","description":"夜班司机"},
  "appearances": [{"kind":"first_appearance","status":"planned","episode_number":1}]
}
```

保存为 `entity.json`，运行 `script entities put --body-file entity.json` 和 `script entities list --document-id PEOPLE_DOCUMENT_ID`。`planned` 仅代表计划出场；确实写进当前剧本后才能改为 `written`，并提供单集 `document_id`、`revision_id`。后续更新既有实体需传 `entity_id`。关联文字资产不会启动生图、配音或视频任务。

交接前用 `documents get` 核实本集与参考文档修订，用 `checks get` 核实同一组 `context_revisions`。`reference_revisions` 只列参考文档，本集文档由 `document_id/revision_id` 隐含。`selected_entity_ids` 只放已核实且未过期的实体。下面的 `checked` 必须指向针对这些相同修订的已完成实际检查；如明确跳过检查，应使用 `{"mode":"unchecked","reason":"具体原因"}`，不能留空。

```json
{
  "document_id": "EPISODE_DOCUMENT_ID",
  "revision_id": "EPISODE_REVISION_ID",
  "reference_revisions": {"PEOPLE_DOCUMENT_ID":"PEOPLE_REVISION_ID"},
  "selected_entity_ids": ["ENTITY_ID"],
  "update_scope": {"mode":"all","scene_ids":[]},
  "fact_acknowledgement": {"mode":"checked","run_id":"CHECK_RUN_ID","issue_reasons":{}},
  "client_mutation_id": "handoff-prepare-1"
}
```

保存为 `handoff-prepare.json` 后执行：

```bash
nuomi --project PROJECT script handoffs prepare --body-file handoff-prepare.json
nuomi --project PROJECT script handoffs get HANDOFF_ID
nuomi --project PROJECT script handoffs confirm HANDOFF_ID --json '{"expected_source_project_revision":0,"client_mutation_id":"handoff-confirm-1"}'
nuomi --project PROJECT script handoffs get HANDOFF_ID
```

`prepare` 只生成差异预览与冻结快照；确认前核对 `diff`、`update_scope`、`expected_source_project_revision`，将示例中的 `0` 换成**本次 prepare 返回的值**。范围可为 `none`、`all`、`selected`；`selected` 必须携带预览中有效且非空的 `scene_ids`。`confirm` 才改制作来源并可能派发场次解析任务。若返回任务 ID，使用 `wait`，再 `handoffs get` 看最终状态。派发失败时在**原 `HANDOFF_ID`** 上用 `handoffs retry HANDOFF_ID`；不要新建交接猜测是否成功。`handoffs list --episode-number 1` 可找回记录。文字交接不会自动生成画面、音频或视频；后续制作遵循 [Nuomi 生产流程](../../../skills/nuomi-production/SKILL.md)。

## 命令索引

| 分组 | 子命令 |
| --- | --- |
| `documents` | `list`, `create`, `get ID`, `save ID`, `revisions ID`, `restore ID`, `import` |
| `generations` | `list`, `start`, `get RUN_ID`, `retry RUN_ID`, `rebase RUN_ID`, `candidate CANDIDATE_ID`, `review CANDIDATE_ID` |
| `rewrites` | `create`, `get JOB_ID`, `list DOCUMENT_ID` |
| `proposals` | `list DOCUMENT_ID`, `accept`, `discard PROPOSAL_ID` |
| `checks` | `list [--episode-document-id ID]`, `start`, `get RUN_ID`, `intentional ISSUE_ID`, `targets ISSUE_ID`, `rewrite-targets ISSUE_ID` |
| `entities` | `list [--document-id ID]`, `put`, `assets --asset-type character/scene/prop` |
| `handoffs` | `list [--episode-number N]`, `get ID`, `prepare`, `confirm ID`, `retry ID` |
