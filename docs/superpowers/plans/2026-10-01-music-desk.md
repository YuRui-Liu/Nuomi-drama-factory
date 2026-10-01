# 多轨配乐台实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 逐任务执行；在当前会话内按顺序实现，使用下面的复选框跟踪进度。需要并行时先取得用户授权。

**目标：** 为已完成视频提供可选的画布配乐台，使用个人音乐库或用户点击生成的纯音乐，完成全局和自由选段的多轨编排，导出独立配乐版成片。

**架构：** 配乐台保存一个固定视频来源和独立音频计划，不改变视频剪辑。个人库、项目引用、配乐草稿、生成回执、混音快照分别持久化；FastAPI 路由复用现有权限和任务后端，生成复用 RunningHubClient，音频预览与导出共享服务端混音结果。

**技术栈：** React、TypeScript、React Flow、TanStack Query、Vitest；Python、Pydantic、FastAPI、SQLite、pytest、FFmpeg。

**规格：** `docs/superpowers/specs/2026-09-30-music-library-scoring-design.md`，包括 2026-10-01 多轨补充。

## 执行边界

- 本计划基于当前工作区现有接口；工作区存在大量其他任务的未提交修改。禁止重置、清理、全量暂存或覆盖这些修改。共享文件先读取最新内容，只提交本任务明确拥有的变更。
- 文档在当前工作区完成；实施前按 worktree 技能检查是否需要隔离。新 worktree 不自动具有当前未提交接口，不能假定与本计划一致，也不能盲目复制整个工作区。
- 每个任务先运行针对测试确认预期失败，再最小实现并运行同一测试确认通过；完成一个可工作的切片再提交。命令默认在仓库根执行，前端命令使用 `pnpm --dir frontend`。
- 不提交付费生成作为自动化验证。RunningHub 接入测试使用本地工作流契约及假客户端。远端契约无法只读确认时，库内选曲与混音仍可交付，生成明确显示配置待核对。
- 已存在多轨视频合成模型，但缺少配乐轨独奏、淡变、资源版本与引用权限；不要直接把它的数据结构当作配乐台持久化协议。

## 文件边界

| 路径（相对仓库根） | 职责 |
| --- | --- |
| `src/novelvideo/music/models.py` | 严格资源、片段、轨道、计划及请求模型 |
| `src/novelvideo/music/store.py` | SQLite 事务、版本检查、资源引用及任务回执 |
| `src/novelvideo/music/media.py` | 受控资源解析、探测、文件哈希、上传落盘 |
| `src/novelvideo/music/matching.py` | 可解释标签和关键词排序 |
| `src/novelvideo/music/prompts.py` | 剧情意图到可编辑纯音乐描述 |
| `src/novelvideo/music/mix.py` | 时间线校验、混音滤镜、预览音轨及视频封装 |
| `src/novelvideo/media_capabilities/music/runninghub_acestep.py` | 工作流契约与 nodeInfoList 编译 |
| `src/novelvideo/api/routes/music_library.py` | 个人库读写与媒体访问 |
| `src/novelvideo/api/routes/music_desk.py` | 项目计划、收藏、候选、生成与导出 |
| `src/novelvideo/task_backend/runners/music.py` | 可恢复生成与混音任务 |
| `frontend/src/features/canvas/music/` | 配乐时间线纯逻辑、面板、播放器、服务接口 |
| `frontend/src/features/canvas/nodes/MusicDeskNode.tsx` | 成片输入、配乐台入口、输出摘要 |
| `tests/music/`、`frontend/src/__tests__/features/canvas/music/` | 领域、API、任务、混音及交互回归 |

新 Python 包增加 `__init__.py`。现有接线文件仅修改 `api/__init__.py`、`task_backend/runners/__init__.py`、`task_backend/run_core.py`、`api/routes/tasks.py` 和画布注册、连接规则、节点类型、中英文翻译。

## 任务 1：固定资源与时间线协议

**文件：** 创建 `music/models.py`、`tests/music/test_models.py`；前端创建 `features/canvas/music/types.ts`。

- [ ] 定义下列协议，所有时间使用整数毫秒；未知媒体时长不能进入正式计划。Python 模型禁止多余字段、NaN 和无限值。JSON 种子使用十进制字符串，防止 JavaScript 大整数截断。

```typescript
export interface MusicClip {
  id: string;
  assetVersionId: string;
  startMs: number;
  sourceInMs: number;
  lengthMs: number;
  gainDb: number;
  fadeInMs: number;
  fadeOutMs: number;
  loop: { startMs: number; endMs: number } | null;
}
export interface MusicTrack {
  id: string;
  name: string;
  gainDb: number;
  muted: boolean;
  solo: boolean;
  clips: MusicClip[];
}
export interface MusicPlan {
  schemaVersion: 1;
  revision: number;
  source: { assetVersionId: string; sha256: string; durationMs: number };
  original: { muted: boolean; gainDb: number };
  ducking: { enabled: boolean; gainDb: number;
    attackMs: number; releaseMs: number;
    intervals: { startMs: number; endMs: number }[] };
  tracks: MusicTrack[];
}
```

- [ ] Python 对应模型使用 `Field(ge=0)` 约束时间和 `Field(gt=0)` 约束长度；音量范围 `[-60, 6]` dB；轨道与片段 ID 在计划内唯一。同轨片段不允许重叠，跨轨允许重叠。淡入加淡出不得超过片段长度，循环范围必须在源音频中。
- [ ] 测试覆盖负时间、同轨碰撞、跨轨重叠、超出成片、淡变过长、重复 ID、未知版本和种子精度；使用以下输入作为最小回归例：

```python
def test_seed_round_trip():
    seed = "18446744073709551615"
    assert str(int(seed)) == seed

def test_two_tracks_can_overlap(valid_plan, validate_plan):
    valid_plan["tracks"][1]["clips"][0]["startMs"] = 0
    validate_plan(valid_plan)  # fixture supplies two distinct tracks and valid versions
```

- [ ] 在 `tests/music/conftest.py` 提供完整的两轨、90 秒成片、120 秒音频资源 fixture；模型测试直接导入实现，不使用未定义回调 fixture，以上测试中的 `validate_plan` fixture 绑定模型验证函数。
- [ ] 执行 `uv run pytest tests/music/test_models.py -q`，先确认协议缺失导致失败，再完成模型并确认通过；提交 `feat(music): define versioned multitrack plan`。

## 任务 2：个人音乐库与项目引用

**文件：** 创建 `music/store.py`、`music/media.py`、`api/routes/music_library.py`、`tests/music/test_store.py`、`tests/music/test_library_api.py`；修改 `api/__init__.py`。

- [ ] 使用专用 SQLite 文件 `STATE_DIR/local/music.db`，文件放 `OUTPUT_DIR/.music/<owner-key>/<sha256>.<ext>`。owner-key 从已认证身份生成，禁止接受客户端提供的文件路径或 owner。开 WAL、外键、busy timeout，写入使用事务。
- [ ] 建立 `assets`、`asset_versions`、`project_refs`、`plans`、`jobs` 五表。资源 metadata 有递增 revision；文件版本不可改写；计划 `(project_id, canvas_id, node_id)` 唯一；回执 `(project_id, request_id)` 唯一。SQL 版本检查固定采用：

```sql
UPDATE plans SET body_json = ?, revision = revision + 1
WHERE project_id = ? AND canvas_id = ? AND node_id = ? AND revision = ?;
```

- [ ] 更新影响行数为零返回 409；写入项目引用时解析为不可变资源版本及所需元数据快照。归档仅阻止新选用；旧项目引用保持可播放。用户改标签不改变已采用版本。
- [ ] 上传先写任务临时文件，ffprobe 验证存在可解码音轨、实际时长与格式，再计算哈希及原子归档；失败清理本次临时文件。存储读取权限从调用者及项目引用判断，不因知道哈希而授权。
- [ ] 增加 `/music-library/assets` GET/POST、`/music-library/assets/{id}` PATCH、`/music-library/versions/{id}/audio` GET。PATCH 包含 expected_revision 和 archived；音频支持浏览器 Range。个人库仅所有者访问，项目作用域 agent 不可枚举整个个人库。
- [ ] 测试：两个用户互不可见、上传伪造扩展名失败、路径穿越拒绝、Range 返回正确字节、两个项目复用同版本、归档后旧引用可读、新引用拒绝、并发 CAS 仅一个成功。
- [ ] 执行 `uv run pytest tests/music/test_store.py tests/music/test_library_api.py -q`；提交 `feat(music): add private library and immutable project references`。

## 任务 3：项目配乐 API、选曲和提示词

**文件：** 创建 `music/matching.py`、`music/prompts.py`、`api/routes/music_desk.py`、`tests/music/test_matching.py`、`tests/music/test_desk_api.py`；修改 `api/__init__.py`。

- [ ] 路由前缀 `/projects/{project}/music`；`GET/PUT /plans/{canvas}/{node}`、`GET/POST /favorites`、`POST /matches`、`POST /prompt-preview`。读取要求 viewer，写入要求 editor，生成/导出还要求 tasks:submit。复用 `resolve_project_scope`，不得直接拼项目路径。
- [ ] 计划保存时校验源视频可访问、资源版本有效、真实时长、轨道碰撞和循环区间；响应返回 revision。源成片哈希变化返回 source_changed，保留草稿且阻止旧计划直接导出。
- [ ] 首版按显式标签和文本词匹配：用途 4、情绪 3、主题 3、强度 2、乐器 1、时长满足 1；分数仅用于排序，界面不展示伪百分比。未知人声属性不满足“已确认无人声”的硬筛选。返回匹配标签、不足和可用范围。
- [ ] 提示词首版确定性模板，将用户可选用途映射为音乐词汇；中文自由意图保留为备注，用户可编辑最终英文 tags。不要声称自动理解视频。初始示例：

```python
PURPOSES = {
    "clue": "restrained cinematic suspense, sparse felt piano, low strings",
    "conflict": "rising tension, low percussion, controlled rhythmic pulse",
    "reflection": "gentle reflective underscore, soft piano, warm pads",
    "bed": "subtle atmospheric underscore, sparse arrangement",
}

def prompt_for(purpose: str) -> str:
    return f"Instrumental, {PURPOSES[purpose]}, leave space for dialogue, no vocals"
```

- [ ] 测试无 RunningHub 配置仍可检索与保存；查询不会调用 submit；源版本变化被拒绝；版本冲突保留客户端修改；项目成员不能借收藏访问未授权个人资产。
- [ ] 执行 `uv run pytest tests/music/test_matching.py tests/music/test_desk_api.py -q`；提交 `feat(music): add project plans and explainable matching`。

## 任务 4：纯音乐工作流与可恢复生成

**文件：** 创建 `media_capabilities/music/runninghub_acestep.py`、`music/generation.py`、`task_backend/runners/music.py`、`tests/music/test_acestep.py`、`tests/music/test_generation.py`、`tests/fixtures/runninghub/acestep_music_contract.json`；修改项目音乐路由与任务注册文件。

- [ ] 从用户 `_api.json` 提取不含凭证的最小节点契约 fixture：94 的类型和输入，203/205/109 primitive 类型及到 94/98 的连接，107 输出。核对远端提供的公开只读工作流接口后再接契约检查；没有可用接口时要求服务配置绑定已核对的部署契约，禁止虚构远端验证成功。
- [ ] 配置复用现有 RunningHub credential resolver，工作流 ID 默认字符串 `2059090557116440578`；种子缺省用 `secrets.randbits(63)` 并冻结；非法负种子不原样发出。编译结果核心为：

```python
def node(node_id: str, field: str, value: object) -> dict[str, str]:
    return {"nodeId": node_id, "fieldName": field, "fieldValue": str(value)}

def compile_fields(tags: str, bpm: int, seconds: float, seed: int):
    return [node("94", "tags", tags), node("94", "lyrics", "[Instrumental]"),
            node("203", "value", bpm), node("205", "value", f"{seconds:g}"),
            node("109", "value", seed)]
```

- [ ] 编译前校验 bpm/duration/key/time-signature 符合已核对节点约束；时长仅写共享 205，不覆盖 94 或 98 的连接。保存 schema hash、实际参数、账号引用、请求摘要与种子。客户端不得改写加载模型或采样器。
- [ ] `POST /generation-jobs` 接受 request_id、冻结选区与可审阅参数；默认一个候选。插入 submitting 回执后调用 RunningHubClient.submit，获得 remote_task_id 立即持久化，后续重启只 query。相同 request_id 不同摘要返回 409。
- [ ] 提交超时且没有 remote_task_id 时标记 submission_unknown，不能假装可查询未知 ID或自动重提；显示人工核对入口。失败明确且可重试时由用户点击产生新请求。取消不能声称撤销远端费用。
- [ ] 成功后下载、解码、测量并保存候选版本；不写正式计划，不自动收藏。新增候选列表、采用引用及收藏入口；任务标签包括 music_generate，费用沿用成本捕获上下文。
- [ ] 测试节点共享时长、纯音乐标记、大种子、契约不符拒绝、重复请求只提交一次、未知提交不重试、重启恢复 query、候选不修改计划、已完成文件恢复。
- [ ] 执行 `uv run pytest tests/music/test_acestep.py tests/music/test_generation.py -q`；提交 `feat(music): add guarded RunningHub generation`。

## 任务 5：多轨混音、预览与导出

**文件：** 创建 `music/mix.py`、`tests/music/test_mix.py`、`tests/music/test_render_jobs.py`；扩展 `task_backend/runners/music.py`、项目音乐路由。

- [ ] 提供纯函数 active_tracks、compile_mix 和任务函数 render_music；所有文件由资产 ID 在服务端解析，FFmpeg 用 argv 调用，不用 shell。静音优先于独奏，原声独立：

```python
def active_tracks(tracks):
    has_solo = any(t.solo for t in tracks)
    return [t for t in tracks if not t.muted and (not has_solo or t.solo)]

def linear_gain(track_db: float, clip_db: float) -> float:
    return 10 ** ((track_db + clip_db) / 20)
```

- [ ] 每片段滤镜顺序为裁剪源区间、重置 PTS、有效循环区间展开、裁到目标长度、固定采样率/声道、片段及轨道增益、淡入淡出、延迟到时间线。长片段循环前验证循环范围，禁止默认拉伸。
- [ ] 按可靠对白区间创建含 attack/release 的配乐增益包络；缺少对白区间时要求手工区间或整体调低，不通过原声能量冒充对白检测。原声按独立增益进入输出。
- [ ] 用完整成片长度的静音基础轨保证无原声视频、空配乐计划和结尾静音均有正确时长。amix 显式 normalize=0，最后 limiter ceiling 0.891251（−1 dBFS）并补偿其延迟；输出精确裁到成片长度。
- [ ] `POST /preview-jobs` 与 `POST /render-jobs` 冻结同一 mix spec；预览产出整集混音音轨，视频播放器静音并同步播放该音轨。编辑后标记预览过期，主动更新试听，不播放旧音轨冒充新配置。导出复用同 hash 预览缓存，封装源视频与混音音轨；视频兼容时 copy，必要转码仅改变封装兼容性，不重新剪辑。
- [ ] jobs 表保存冻结计划、源视频哈希、任务 ID、输出及错误；任务后端运行 ffmpeg 队列。输出到新 job 路径，成功后才发布结果引用。取消终止本地子进程，临时结果不覆盖已发布版本。
- [ ] 用 FFmpeg lavfi 临时生成 440Hz/880Hz 短音频与无声视频：检查两轨重叠频谱均存在、静音/独奏一致、淡变能量、对白区间压低、末尾长度、峰值上限及源文件哈希不变。不存在 ffmpeg 的环境明确 skip 媒体验证，交付验收环境必须具备 ffmpeg。
- [ ] 执行 `uv run pytest tests/music/test_mix.py tests/music/test_render_jobs.py -q`；提交 `feat(music): render immutable multitrack soundtracks`。

## 任务 6：前端纯时间线编辑与撤销

**文件：** 创建 `frontend/src/features/canvas/music/timeline.ts`、`history.ts`、`api.ts`、`frontend/src/__tests__/features/canvas/music/timeline.test.ts`。

- [ ] timeline 提供纯函数 addTrack、renameTrack、removeTrack、insertClip、moveClip、trimClip、splitClip、duplicateClip、replaceRange。id 通过参数生成器注入，操作不改原对象。无效操作返回明确错误而非静默夹取内容。
- [ ] replaceRange 仅作用于目标轨，右侧保留片段必须移动源入点。实现骨架如下，`newId` 为调用方传入函数；循环片段的 sourceInMs 用循环区间取模处理。

```typescript
function keepOutside(clip: MusicClip, a: number, b: number, newId: () => string): MusicClip[] {
  const end = clip.startMs + clip.lengthMs;
  if (end <= a || clip.startMs >= b) return [clip];
  const out: MusicClip[] = [];
  if (clip.startMs < a) out.push({...clip, lengthMs: a - clip.startMs, fadeOutMs: 0});
  if (end > b) out.push({...clip, id: newId(), startMs: b,
    sourceInMs: clip.sourceInMs + b - clip.startMs,
    lengthMs: end - b, fadeInMs: 0});
  return out;
}
```

- [ ] 最终实现对保留边缘的淡变长度做合法性处理；被切断处取消原整片淡变，原外侧淡变保留且限制在剩余长度；循环偏移按有效区间取模。split、trim 使用同一源偏移规则。
- [ ] history 维护 past/present/future，完成一次拖动为一个撤销单位；保存成功更新服务端 revision，撤销不回退服务端 revision。冲突保留本地历史并提示重新载入或另存节点。
- [ ] 表驱动测试：90 秒铺底替换 18–36 秒后左段 0–18、右段 36–90 且 sourceIn=36 秒；不同轨原样保留；拆分/循环/复制独立 ID、删除撤销及边界阻止。
- [ ] 执行 `pnpm --dir frontend test src/__tests__/features/canvas/music/timeline.test.ts`；提交 `feat(music): add reversible multitrack editing`。

## 任务 7：画布节点与配乐工作台

**文件：** 创建 `MusicDeskNode.tsx` 和 `music/MusicDeskModal.tsx`、`MusicLibraryPanel.tsx`、`MusicTimeline.tsx`、`MusicInspector.tsx`、`MusicGenerationDialog.tsx`、`MusicPreview.tsx`；修改 `domain/canvasNodes.ts`、`domain/nodeRegistry.ts`、`nodes/index.ts`、`Canvas.tsx`、中英文 translation.json；新增 `music/node.test.tsx`、`music/workbench.test.tsx`。

- [ ] 注册 `musicDesk: 'musicDeskNode'`；NodeData 仅保存 planId/revision/sourceVersionId/resultVideoUrl/status 等轻量引用。加载完整计划走 API，禁止把音频 base64 存进画布。
- [ ] 添加菜单、拖线创建、默认尺寸、节点渲染和连接规则。输入接受已完成 videoCompose 或 video 节点中的一个视频；多个输入显示选择冲突，不静默取首个。视频导演的多个候选不能当成单一成片；通过明确成片节点导入。
- [ ] 空节点展示“接入成片”；接入后服务端固定版本及真实时长。节点打开大尺寸工作台，源视频轨不可移动剪辑；原声独立控制。输出完成后可预览配乐版并保留旧源引用。
- [ ] 工作台左侧音乐库/收藏/候选，中间视频预览，右侧选区或片段属性，底部多轨时间线。提供新增/重命名/删除轨、每轨增益/M/S，片段移动/裁剪/拆分/复制/删除、淡变与撤销/重做。
- [ ] 全局选择设置 `[0, durationMs]`，自由拖选与时间输入同步；当前轨显式高亮。选曲按当前轨插入，碰撞弹出“替换选区 / 新轨叠加 / 取消”。生成弹窗冻结选区但结果仅进入候选；之后用户可以采用到不同范围，重新校验时长。
- [ ] 拖动时做轻量局部视图更新；鼠标松开提交单次领域操作。键盘可选择片段、输入时间和撤销。复用 `compose/audioPeaks.ts`、`compose/filmstrip.ts`，不改动原视频剪辑器行为。
- [ ] MusicPreview 播放视频与服务端混音轨，seek/pause/rate同步，检测漂移纠正；预览生成中可继续编辑，但过期结果不冒充当前版本。没有配乐时直接播放原视频。
- [ ] 测试节点入口、无源/多源状态、拖选、冲突三选项、独奏、删除撤销、生成不自动采用、保存刷新恢复、旧预览标记、输出新版本；使用 MSW 假接口。
- [ ] 执行 `pnpm --dir frontend test src/__tests__/features/canvas/music` 和 `pnpm --dir frontend exec tsc --noEmit`；提交 `feat(canvas): add multitrack music desk`。

## 任务 8：集成验收与交付

**文件：** 创建 `tests/music/test_end_to_end.py`、`docs/superpowers/specs/2026-10-01-music-desk-verification.md`。

- [ ] 集成用临时项目、用户 A/B 和本地短媒体：A 上传入库→项目收藏→接入成片→全局铺底→新轨局部音乐→试听→导出。核对文件解码、时长、混音、权限和原片不变。
- [ ] 假 RunningHub：点击生成→重载轮询恢复→候选试听→采用→收藏跨项目复用；确认不点击生成时 submit 计数为零；模拟提交超时、任务失败、重复点击和并发保存。
- [ ] 执行 `uv run pytest tests/music tests/test_task_backend_registry.py -q`；执行 `pnpm --dir frontend test src/__tests__/features/canvas/music src/__tests__/features/canvas/timelineModel.test.ts`；执行前端类型检查。已有无关失败单列，不把整个工作区宣称全绿。
- [ ] 浏览器验证 1440px 和较窄桌面布局，截图保存工作台、重叠轨、冲突处理、导出结果；真实短媒体核对同步播放与 seek。测试不更改用户现有成片或花费生成额度。
- [ ] 验收文档记录实际命令、结果、截图、远端契约核对状态与未验证项。完成前审查资源权限、任务恢复和混音一致性；只提交本功能文件。

## 交付检查

| 规格 | 实现任务 |
| --- | --- |
| 音乐库优先、项目收藏、版本与权限 | 2、3 |
| 默认纯音乐、手动 RunningHub 生成、候选独立 | 3、4 |
| 成片后可选节点、全局/自由选段 | 1、6、7 |
| 多轨、多片段、增益、独奏、静音、撤销 | 1、5、6、7 |
| 淡变、循环、对白压低、预览导出一致 | 5、7、8 |
| 不覆盖原成片、版本冲突与在途冻结 | 2、3、4、5、8 |

本计划完成不代表功能已实现。后续按任务 1 到任务 8 顺序执行，每个任务验证后更新复选框。
