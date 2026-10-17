# 角色声音资产与专业配音节点实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development 或 executing-plans 逐任务实现此计划。使用复选框记录实际完成状态，不能将计划中的预期测试结果记为已通过。

**目标：** 将已批准的角色声音与配音节点 PRD 接通到画布、机组、叙事组生产及时间线，完成一名 E01 角色和一组真实对白的首次验收。

**架构：** 复用 SQLite 声音候选、Qwen 任务和 Index 配音适配器；新增持久声音版本和配音 take 的引用关系。画布与 Agent 调用同一业务操作，生成、采用和时间线替换分别执行。叙事组是生产单位，台词分段留在后端执行器内。

**技术栈：** Python 3.11、FastAPI、Pydantic、SQLite、pytest；React、TypeScript、React Flow、TanStack Query、Vitest；Qwen3-TTS VoiceDesign、IndexTTS2、FFmpeg。

**批准依据：** [声音节点 PRD](../specs/2026-10-17-character-voice-nodes-design.md)，用户已批准书面规格。本计划不增加新的产品选择。

## 0. 基线与执行约束

计划依据 PRD 提交 `4a9cbaa3` 及当前工作区机组接入代码。`creation/node_execution.py`、`professional_agents.py` 等仍有未提交依赖；执行前记录 `git status --short`、`git diff --stat`，明确这些基线，不直接从远端默认分支创建缺少依赖的实现工作区。计划在当前文档工作区编写；产品实现使用包含所需依赖的专用工作区，不移动或清理他人改动。

- [ ] 读取 `AGENTS.md`、`skills/nuomi-production/SKILL.md`、PRD 和本计划；实现前使用 using-git-worktrees 确认可复用工作区与基线。
- [ ] 核实已有 Python 环境和前端依赖可用；Python 测试用 `.venv\Scripts\python.exe -m pytest`，前端用 `pnpm -C frontend exec vitest run`。不因写计划安装模型或调用供应商。
- [ ] 每个任务先加对应失败测试，运行确认失败原因，再写最小实现，运行指定检查后仅提交任务文件。依赖尚未落盘时先完成依赖，不伪造测试通过。

## 1. 文件与职责

下列新增文件是实施目标，不代表已存在。既有文件保持其职责，不另造 provider 客户端。

| 文件 | 职责 |
| --- | --- |
| `src/novelvideo/media_capabilities/tts/candidate_store.py`（修改） | 在既有人工采用事务中追加不可变声音版本及当前选择 |
| `src/novelvideo/media_capabilities/tts/voice_versions.py`（新增） | 持久声音版本查询、身份映射与兼容读取 |
| `src/novelvideo/media_capabilities/tts/performance.py`（新增） | 表演单及 provider 能力预检、实际参数预览 |
| `src/novelvideo/media_capabilities/tts/voice_prompt.py`（修改） | 复用声线编译器，保持源文本与表演分离 |
| `src/novelvideo/audio/dubbing_contracts.py`（新增） | 台词快照、take、采用与影响的业务模型 |
| `src/novelvideo/audio/dubbing_store.py`（新增） | 组任务、逐句 take、采用记录、幂等及版本冲突 |
| `src/novelvideo/audio/dubbing_service.py`（新增） | 组范围准备、提交、恢复、采用；组合已有执行能力 |
| `src/novelvideo/audio/narrative_group_dubbing.py`（修改） | 拆开旧的生成、变速和发布行为，保留已有产物读取 |
| `src/novelvideo/api/routes/dubbing.py`（新增） | 项目内组配音 API，薄路由复用服务 |
| `src/novelvideo/freezone/orchestration/adapters/voice.py`（新增） | Canvas/Agent 到声音服务的操作适配，不直接调用模型 |
| `frontend/src/features/canvas/voice/`（新增目录） | 角色声音引用、表演编辑、配音列表及影响预览 |
| `frontend/src/features/canvas/nodes/CharacterVoiceNode.tsx`（新增） | 角色声音节点，复用角色面板的候选和采用逻辑 |
| `frontend/src/features/canvas/nodes/AudioNode.tsx`、`AudioOperationsPanel.tsx`（修改） | 扩展角色配音，保留普通 speech/music |

## 2. 任务一：持久声音版本与采用原子性

**文件：** 修改 `src/novelvideo/media_capabilities/tts/candidate_store.py`、`src/novelvideo/api/routes/voice_candidates.py`、`src/novelvideo/models.py`、`src/novelvideo/sqlite_store.py`；新增 `src/novelvideo/media_capabilities/tts/voice_versions.py`、`tests/media_capabilities/tts/test_voice_versions.py`；扩展 `tests/media_capabilities/tts/test_candidate_store.py`。

- [ ] 用既有 candidate_store 测试的项目数据库和角色 fixtures 增加四项测试：同候选重复采用返回同版本；新候选采用保留旧版本；角色资料或 expected_reference 变化返回冲突；重开数据库仍能读取版本。必须断言角色引用与版本写入在同一事务中失败或成功。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/media_capabilities/tts/test_voice_versions.py tests/media_capabilities/tts/test_candidate_store.py -q`，确认新增用例因版本契约缺失而失败。
- [ ] 采用数据库增量建表，模型记录角色稳定身份、身份变体、candidate、音频哈希、来源摘要和真实采用者。已有 `VoiceProfileStore` 是内存结构，不用于生产持久化。关键表约束如下，JSON 保存冻结内容，生成/采用服务不得 UPDATE 历史版本：

```sql
CREATE TABLE IF NOT EXISTS character_voice_versions (
  version_id TEXT PRIMARY KEY,
  character_key TEXT NOT NULL,
  slot TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  audio_sha256 TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  UNIQUE(character_key, slot, candidate_id, audio_sha256)
);
```

- [ ] 当前 `NovelCharacter` 和 characters 表没有独立稳定 UUID；在 `sqlite_store.py` 的初始化迁移中增补可持久化 `character_id`，旧行一次性回填 UUID、新增行生成 UUID、建立唯一索引。`models.py` 暴露字段，`rename_character` 保持 UUID，旧按名称 API 继续解析到同一行；身份变体使用已有 `identity_id`。测试重复初始化不改 UUID、改名后声音版本仍可读、删除后同名新建不继承旧声音。不能只把 name 字段换名为 ID。
- [ ] 将版本追加放入 `VoiceCandidateStore.approve` 已有事务，继续校验 actor、reason、摘要和目标引用；角色引用保留兼容路径，同时返回 `voice_version_id`。对旧路径采用记录，首次明确绑定时创建来源为 legacy 的快照，不伪造历史模型参数或审批时间。identity 选择优先于角色默认，采用一个 identity 不覆盖其他身份或年龄 slot。
- [ ] 重跑上面测试及 `.venv\Scripts\python.exe -m pytest tests/test_voice_candidates_api.py tests/test_voice_acceptance.py -q`；预期全部通过。仅提交本任务文件，消息 `feat(voice): persist adopted voice revisions`。

## 3. 任务二：表演单与实际参数预览

**文件：** 新增 `src/novelvideo/media_capabilities/tts/performance.py`、`tests/media_capabilities/tts/test_performance.py`；修改 `models.py`、`voice_prompt.py`、`runninghub_voice_design.py`、`runninghub_indextts2.py`（均位于同一 tts 目录）。

- [ ] 编写以下核心失败测试；`compile_performance` 是本任务新增函数，返回提交参数与保留的导演要求，`supported_modes` 来自实际适配器声明：

```python
import pytest
from novelvideo.media_capabilities.tts.performance import compile_performance

def test_unavailable_control_fails_before_submission():
    with pytest.raises(ValueError, match="emotion_mode_not_supported"):
        compile_performance(
            text="别进去。", direction={"intention": "阻止同伴"},
            mode="emotion_audio", payload="reference.wav",
            supported_modes={"neutral"},
        )

def test_spoken_text_does_not_include_direction():
    result = compile_performance(
        text="别进去。", direction={"emphasis": "别进去"},
        mode="neutral", payload=None, supported_modes={"neutral"},
    )
    assert result["effective"]["text"] == "别进去。"
    assert result["review_only"] == {"emphasis": "别进去"}
    assert "target_duration" not in result["effective"]
```

- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/media_capabilities/tts/test_performance.py -q`，预期缺少函数失败。
- [ ] 实现输入验证、互斥 EmotionRequest 和模型特定参数映射；基础规则如下。mode 非 neutral 时先由既有 EmotionRequest 校验匹配 payload，再按 adapter 编译，不能使用任意 `**kwargs` 透传界面字段：

```python
def validate_mode(mode: str, supported_modes: set[str]) -> None:
    if mode not in supported_modes:
        raise ValueError("emotion_mode_not_supported")
```

- [ ] 为 Qwen 返回 workflow 实际语言 Auto 和 text/instruct；为 Index 返回实际支持的情绪模式，其他重音、停顿保留 `review_only`。添加冲突描述、非法向量、朗读括号原文、发音替换前后文以及不支持语言控制的测试；不靠正则删除合法朗读文本。
- [ ] 运行上述测试和 `.venv\Scripts\python.exe -m pytest tests/media_capabilities/tts/test_voice_prompt.py tests/media_capabilities/tts/test_workflow_variants.py tests/media_capabilities/tts/test_runninghub_voice_design_runtime.py -q`。通过后提交 `feat(voice): compile supported performance controls`。

## 4. 任务三：台词快照、take 与局部影响

**文件：** 新增 `src/novelvideo/audio/dubbing_contracts.py`、`dubbing_store.py`、`tests/test_dubbing_store.py`、`tests/test_dubbing_impact.py`。

- [ ] 新增持久化及影响测试：文本变化仅影响选中 dialogue_id；换声音影响引用旧版本的台词；画布位置变化不影响；删除台词只标记，不删除 take；重开数据库历史可读。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/test_dubbing_store.py tests/test_dubbing_impact.py -q`，确认缺少业务契约失败。
- [ ] 新增冻结 `DialogueSnapshot`：`dialogue_id/character_key/identity_id/script_revision/group_id/text/voice_version_id/performance`；take 记录快照、任务、音频哈希、实测时长、技术状态。组任务记录用户选择范围及整个源快照，单句音频复用键只包含影响该句的输入，不能因整集修订号变化使所有音频失效。纯函数及测试核心如下：

```python
import hashlib
import json

def line_input_hash(line: dict) -> str:
    keys = ("character_key", "identity_id", "text", "voice_version_id", "performance")
    data = {key: line.get(key) for key in keys}
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def test_layout_and_source_revision_do_not_invalidate_audio():
    line = dict(character_key="c1", identity_id="adult", text="别进去。",
                voice_version_id="v1", performance={})
    assert line_input_hash(line) == line_input_hash({**line, "x": 42, "script_revision": "r2"})
    assert line_input_hash(line) != line_input_hash({**line, "text": "进去吧。"})
```

- [ ] 稳定台词 ID 优先取已采用专业剧本。对于仍是旧 H3 行结构的内容，在 dubbing_store 中首次导入时保存 UUID 与 `(episode, group_id, director_revision, span_index, line_index)` 的源定位映射，后续局部编辑保留 UUID。新剧本导入无法确定对应关系时标记需要核对并展示整组影响，不用文本相似度静默认领台词，不将修订号或数组位置当永久 ID。测试前面插入台词不导致旧 take 错配，重复相同台词仍有不同 ID。
- [ ] 在提交幂等键中额外包括 provider/model/workflow/编译版本和明确重生成尝试 ID；同请求重试返回已有任务，不同输入重用请求 ID 返回冲突。take 内容不可变，采用指针单独存储并做 expected revision 比较。单句复用与组来源新鲜度分别判断。
- [ ] 重跑两份测试，新增并发采用冲突、请求 ID 冲突及成功 take 被重复请求复用的用例，预期通过后提交 `feat(dubbing): track immutable takes and local impact`。

## 5. 任务四：组配音准备、生成与采用服务

**文件：** 新增 `src/novelvideo/audio/dubbing_service.py`、`src/novelvideo/api/routes/dubbing.py`、`tests/test_dubbing_service.py`、`tests/test_dubbing_api.py`；修改 `src/novelvideo/audio/narrative_group_dubbing.py`、`src/novelvideo/task_backend/runners/narrative_group_video_compose.py`、`src/novelvideo/api/__init__.py`、`src/novelvideo/api/routes/narrative_groups.py` 及 `tests/test_narrative_group_dubbing.py`。

- [ ] 在既有 `test_preparation_clones_each_voice_and_publishes_only_after_audio_ready` 的 manifest/假生成器 fixtures 上改写新契约测试：生成结束 manifest 字节不变；9 秒目标、10 秒音频产生可试听 take 和时长差；所有 ffmpeg 调用不含 atempo；采用冲突不写 manifest。另测一个角色缺声线时所有付费调用数为零。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/test_dubbing_service.py tests/test_dubbing_api.py tests/test_narrative_group_dubbing.py -q`。当前自动发布／变速路径应使新用例失败。
- [ ] 将 `prepare_external_audio` 中“生成候选”和“发布选择”拆成业务服务，复用参考解析、缓存、技术检查和 Index 执行器。移除自动 atempo 与 atrim 对对白的裁切；环境音分离仅在替换原视频对白的混音动作需要，不应阻挡无视频的独立角色试音。原速产物保留，不能把速度变换藏在 adopted take 中。
- [ ] 新 API 采用以下相对路径（前缀 `/projects/{project}/episodes/{episode}/narrative-groups/{group_id}`）；全部按项目权限解析 context，未授权不能访问其他项目的 take：

```text
POST /dubbing/prepare   {script_revision, dialogue_ids, voice_versions, performance}
POST /dubbing/generate  {preparation_id, expected_revision, request_id}
GET  /dubbing          -> preparation, tasks, takes, adoption, impact
POST /dubbing/adopt    {take_ids, expected_revision, reason}
POST /dubbing/retry    {task_id, failed_dialogue_ids, request_id}
```

- [ ] 为已有“选择 external_tts”入口更新调用方，生成完成只返回候选，采用时才写声音来源；不保留隐式发布的后门。旧 manifest 和音频继续读取为历史结果，不重新生成。未知提交状态先 reconcile，失败重试仅处理失败项；使用保存的并发和现有 task backend，不在路由内启动后台协程。
- [ ] 重跑本任务测试和 `.venv\Scripts\python.exe -m pytest tests/media_capabilities/tts/test_batch_dubbing.py tests/test_task_audio_partial_failure.py -q`，全部通过后提交 `feat(dubbing): separate group generation from adoption`。

## 6. 任务五：画布与 Agent 共用声音操作

**文件：** 新增 `src/novelvideo/freezone/orchestration/adapters/voice.py`、`tests/creation/test_voice_node_operations.py`；修改 `adapters/__init__.py`、`adapters/requests.py`、`capabilities.py`、`models.py`、`projection.py`（位于 `freezone/orchestration/`）、`src/novelvideo/creation/professional_agents.py`、`node_execution.py`。

- [ ] 编写操作安装与授权测试：`characterVoiceNode.prepare_design/generate_candidate` 和 `audioNode.prepare_dubbing/dub` 在 catalog 和 available_operations 同时存在；无 scoped grant 拒绝；同请求幂等；新版本结果不会覆盖用户新编辑；生成 grant 不可用于采用或替换时间线。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/creation/test_voice_node_operations.py -q`，确认当前缺少操作失败。
- [ ] 按既有 NodeAdapter 特殊操作机制连接角色服务和组配音服务，返回已有任务标识。映射表为：

```python
VOICE_OPERATION_SEMANTICS = {
    ("characterVoiceNode", "prepare_design"): "voice.prepare_design",
    ("characterVoiceNode", "generate_candidate"): "voice.generate_candidate",
    ("characterVoiceNode", "adopt"): "voice.adopt",
    ("audioNode", "prepare_dubbing"): "dubbing.prepare",
    ("audioNode", "dub"): "dubbing.generate",
    ("audioNode", "adopt_dubbing"): "dubbing.adopt",
}
```

- [ ] adopted 结果投影只保存资产／take 引用与版本；禁止 Agent 自行写 URL 伪造采用。角色采用服务仍核验真实用户采用记录；添加三种专业岗位的最小操作集合，执行师不因角色名称获得采用授权。准备动作不计为媒体生成，生成数量与范围进入授权摘要。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/creation/test_voice_node_operations.py tests/creation/test_node_execution.py tests/creation/test_professional_agents.py tests/creation/test_crew_node_integration.py -q`。通过后提交 `feat(agents): wire voice and dubbing node operations`。

## 7. 任务六：角色声音画布节点

**文件：** 新增 `frontend/src/features/canvas/nodes/CharacterVoiceNode.tsx`、`frontend/src/features/canvas/voice/voiceTypes.ts`、`frontend/src/features/canvas/voice/CharacterVoicePanel.tsx`、`frontend/src/__tests__/features/canvas/character-voice-node.test.tsx`；修改 `frontend/src/features/canvas/domain/canvasNodes.ts`、`frontend/src/features/canvas/domain/nodeRegistry.ts`、`frontend/src/features/canvas/nodes/index.ts`、`frontend/src/lib/queries/characters.ts`、`frontend/src/lib/queries/voice-acceptance.ts`。

- [ ] 写组件测试：三来源模式可切换；同稿候选比较可播放；显示候选数量；未采用时输出无 version；点击生成不会调用 approve；导入可选择片段；切到复用不调用生成；关闭重开仍读数据库状态。
- [ ] 运行 `pnpm -C frontend exec vitest run src/__tests__/features/canvas/character-voice-node.test.tsx`，预期缺少节点失败。
- [ ] 新增节点类型、默认数据、渲染注册和输出端口，复用 `character-voice-design-form.tsx`、`voice-acceptance-panel.tsx` 及 `frontend/src/lib/queries/voice-acceptance.ts`。节点只保存稳定引用与编辑草稿，候选事实从 API 获取。前端引用契约为：

```typescript
export type VoiceSourceMode = 'design' | 'import' | 'reuse';
export interface AdoptedVoiceRef {
  characterKey: string;
  identityId: string;
  slot: string;
  versionId: string;
  audioSha256: string;
}
export interface CharacterVoiceDraft {
  sourceMode: VoiceSourceMode;
  adoptedVoice: AdoptedVoiceRef | null;
  auditionText: string;
  candidateCount: number;
}
```

- [ ] 表单与自由描述通过结构差异确认保持同步，提交预览展示生效参数与仅供试听参考的要求；角色资料过期和采用冲突提供刷新差异入口。使用当前节点样式与面板，不另建全屏资产系统。
- [ ] 运行上述测试及 `pnpm -C frontend exec vitest run src/__tests__/components/assets/voice-acceptance-panel.test.tsx src/__tests__/components/assets/character-voice-panel.test.tsx`，通过后提交 `feat(canvas): add reusable character voice node`。

## 8. 任务七：配音列表、局部委托与试听

**文件：** 新增 `frontend/src/features/canvas/voice/DubbingPanel.tsx`、`dubbingApi.ts`、`dubbingImpact.ts`、`frontend/src/__tests__/features/canvas/dubbing-panel.test.tsx`；修改 `AudioNode.tsx`、`AudioOperationsPanel.tsx`、`useAudioGeneration.ts`（位于 `frontend/src/features/canvas/nodes/`）及 `domain/canvasNodes.ts`。

- [ ] 测试三配音模式、角色声音映射、缺失角色提示、选择台词提交范围、同组顺序试听、失败位置可见、自由试音不进入正式列表；普通 speech/music 保持可用。
- [ ] 运行 `pnpm -C frontend exec vitest run src/__tests__/features/canvas/dubbing-panel.test.tsx`，预期缺少新交互失败。
- [ ] UI 从组 API 读取固定快照与 takes，生成按钮展示范围及缺失依赖。选中台词委托传递 `group_id/dialogue_ids/expected_revision`，不能靠 node 位置或说话人名称猜顺序。mode 采用如下独立值，不覆盖旧 speech/music discriminator：

```typescript
export type DubbingMode = 'script' | 'performance' | 'audition';
export function orderedSelection(order: string[], selected: Set<string>): string[] {
  return order.filter(id => selected.has(id));
}
```

- [ ] 新稿到达只展示影响，不自动提交；播放列表按顺序遇到缺失项显示缺口，不能把部分结果标记为整组成功。支持旧／新 take 对比、当前采用标志和原文／朗读映射检查。
- [ ] 重跑组件测试，增加“更新一个台词只有对应条目标记过期”和“改变节点位置无 API 生成请求”断言，通过后提交 `feat(canvas): support scoped character dubbing`。

## 9. 任务八：时间线采用与不变速交付

**文件：** 修改 `frontend/src/features/canvas/compose/timelineModel.ts`、`frontend/src/features/canvas/compose/VideoComposeModal.tsx`、`frontend/src/features/canvas/nodes/VideoComposeNode.tsx`；新增 `frontend/src/features/canvas/voice/placeDubbingTake.ts`、`frontend/src/__tests__/features/canvas/place-dubbing-take.test.ts`、`tests/test_dubbing_timeline_adoption.py`；修改 `src/novelvideo/audio/dubbing_service.py` 的时间线投影。

- [ ] 测试未采用 take 无法放入正式轨道；采用不自动替换旧片段；替换需匹配时间线修订与明确范围；超长保留完整音频并显示差值；不调用 compose/export。
- [ ] 运行 `pnpm -C frontend exec vitest run src/__tests__/features/canvas/place-dubbing-take.test.ts` 与 `.venv\Scripts\python.exe -m pytest tests/test_dubbing_timeline_adoption.py -q`，确认新增契约失败。
- [ ] 音频片段引用 `take_id/voice_version_id/source_revision`；放入素材与替换现有轨道分开。只计算差值并提供编辑处理，不写速度或截掉尾部。核心时间比较如下：

```typescript
export function dubbingTiming(actualMs: number, targetMs: number) {
  if (!Number.isFinite(actualMs) || !Number.isFinite(targetMs) || actualMs <= 0 || targetMs <= 0) {
    throw new Error('invalid_audio_duration');
  }
  return { actualMs, targetMs, deltaMs: actualMs - targetMs, needsDecision: actualMs > targetMs };
}
```

- [ ] `VideoComposeNode` 当前要求两条视频才能打开。改为有有效时间线或一条可放置的已采用音视频即可编辑和试听；正式视频导出仍检查可用视频轨。`VideoComposeModal.buildInitialTimeline` 不能把所有上游 audioUrl 都当作已采用配音，旧通用音频保持兼容，新配音要求 take 元数据与采用标识。
- [ ] 更新旧 external_tts UI，收到候选只展示试听／采用；出现超长提供调整剪辑、重新表演、返回剧本三个入口。保留已有原声及旧媒体引用，改稿走原有采用流程，不篡改原文解决时长。
- [ ] 重跑本任务及 `tests/test_compose_episode_audio_source.py`；前端执行 `pnpm -C frontend exec tsc -b --pretty false`。通过后提交 `feat(dubbing): adopt takes into versioned timelines`。

## 10. 任务九：CLI 支持与 E01 真机验收

**文件：** 修改 `src/novelvideo/production_cli.py`、`tests/test_production_voice_cli.py`；新增 `tests/test_production_dubbing_cli.py`、`docs/superpowers/reports/2026-10-17-character-voice-e01-acceptance.md`（实际执行后创建）。

- [ ] 增加 CLI mock 测试：组 prepare/generate/status/adopt/retry 指向任务四组 API；不使用旧 shot/span endpoint；不覆写并发；未知状态先查任务；采用不自动 compose。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest tests/test_production_dubbing_cli.py tests/test_production_voice_cli.py -q`，预期新命令缺失失败。
- [ ] 为生产 CLI 增加 `dubbing` 子命令组，复用现有 Connection/request/wait 与 JSONL；请求文件含组、输入版本与范围，不含凭据。执行顺序固定为：

```text
nuomi --project PROJECT policy
nuomi --project PROJECT groups --episode 1
nuomi --project PROJECT voice preflight-episode 1
nuomi --project PROJECT dubbing prepare --episode 1 --group GROUP --body-file preparation.json
nuomi --project PROJECT dubbing generate --episode 1 --group GROUP --body-file generation.json
nuomi --project PROJECT dubbing status --episode 1 --group GROUP
nuomi --project PROJECT dubbing adopt --episode 1 --group GROUP --body-file adoption.json
```

这里的 dubbing 命令为本任务需要实现的命令；PROJECT/GROUP 必须来自真实项目和 groups 结果，不按示例字面提交。音色设计／采用使用已经存在的 `voice design/candidates/approve` 命令，按 `--help` 生成参数。

- [ ] 单元测试通过后，先确认真实 E01 已采用剧本、有效组及角色；不存在时将其作为全链路前置任务推进，不用虚构台词宣称验收。使用 UI 完成一名角色设计与试听采用，再生成一组真实对白，验证 Agent 任务记录、声音一致性、原文完整、实测长度、时间线播放。只在授权范围生成；生产读取保存并发、只处理失败项、不额外 ASR 或重复付费质检。
- [ ] 报告记录项目/角色/组/剧本/声音/take/任务版本、实际播放结果与阻塞。媒体不提交 Git。首组通过后整集生产沿既定计划继续，显式导出后才能报告 E01 成片完成。提交 CLI 和测试 `feat(production): expose group dubbing workflow`；真实验收报告单独提交，不能先写 PASS。

## 11. 顺序、覆盖与完成条件

后端依赖顺序为 1 → 2/3 → 4 → 5；前端 6 可在任务一接口固定后并行，7 依赖 4/5/6，8 依赖 4/7，9 在整条链路通过后执行。共享 NodeAdapter、注册表及模型文件由集成者顺序合并，不让两个 worker 同时编辑。

| PRD 范围 | 实现任务 |
| --- | --- |
| 声音身份、三来源、候选采用 | 1、6 |
| 三配音模式与专业提示词 | 2、4、7 |
| 专业机组和真实可执行授权 | 5、7 |
| 不可变版本、局部影响、失败恢复 | 1、3、4 |
| 试听、时长差异、时间线与导出边界 | 4、7、8 |
| 现有能力复用与 CLI 生产约束 | 1、4、5、9 |
| 一角色一组 E01 验收及整集衔接 | 9 |

完成定义：指定测试实际通过、前端类型检查通过、真实调用链能定位版本和产物、首组试听与时间线播放通过。文档完成、任务已排队、伪造 provider 的测试均不代表实际生成成功。
