# 角色交付台实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前会话逐任务实现；如选择子代理执行，使用 subagent-driven-development。使用复选框跟踪进度。

**目标：** 在现有资产中心中交付角色基础形象与变体，完成三案、四区生成、上下文修复、头像裁切及成套采用。

**架构：** 复用 character_visual 的事实、提案、候选及采用服务，扩展四区资产组和有额度的修复链。角色路由仅组装现有页面壳与新的内容组件，后台继续使用持久化任务；不引入新 MCP 或公司级调度平台。

**技术栈：** Python/FastAPI/Pydantic、现有图像提供方和任务运行时、React/TypeScript/TanStack Query、现有 react-image-crop、pytest/Vitest。

**设计依据：** `docs/superpowers/specs/2026-10-04-character-agent-workbench-design.md`。最终原型只作为交互参考，不复制其示意数据、DOM 拼接、全局状态或模拟任务代码。

## 工作基线与文件职责

当前分支存在大量与导演和剧本相关的未提交改动。实施前检查 `git status --short` 与目标文件 diff，保留他人工作，不整体 stash、reset 或提交。按用户要求在当前工作区原地实现。

## 实施结果（2026-10-04）

以下记录最终实现；后面的逐步清单保留为原始计划，不代表最终文件拆分。

- [x] 保留资产中心外壳与主题，角色内容改为基础形象、变体、三案原位比较与微调、后台进度、交付采用/修改。
- [x] 四区设定图契约：头像大图、无头正面身体、无头侧面身体、完整背面；头像从原图定位裁切，可手调预览。
- [x] 持久化候选、裁切资产版本、审核、一次自动修复额度及重复调度保护。修复携带原始资料、实际提示词、有效方案、失败图、问题建议与历史；限制参考图数量和字节数。
- [x] 手动修改保存更新后的完整方案与校验摘要，采用时同步视觉设定；变体不覆盖基础头像。
- [x] 简洁声音入口、按需挂载旧资料表单，修改意见和新增变体草稿在浏览器会话中保留。
- [x] 后端相关回归 434 项通过（排除已有 fixture 不兼容的 test_nonhuman_entrypoints.py）；最后接口调整另复测 19 项通过。前端 54 项通过，TypeScript `tsc -b` 通过。
- [x] 在真实本地角色页检查三案布局、新增变体与声音入口，截图 `/private/tmp/character-delivery-desktop.jpg`。
- [ ] 真实付费图像提供方的出图质量验收。本次未触发付费出图，因此模型能否稳定遵守四区构图仍需实际生成验证。

实现差异：保持旧 compiler 快照兼容，四区实际提示词另存于候选；审核接入现有 character_casting_review runner。前端以 character-delivery.tsx 和 avatar-crop-dialog.tsx 两个组件承载，不为纯布局拆出额外文件。

| 文件 | 职责 |
| --- | --- |
| `src/novelvideo/character_visual/casting_models.py` | 扩展候选资产组及旧记录默认值 |
| `src/novelvideo/character_visual/casting_store.py` | 保存资产、裁切、修复链与并发版本 |
| `src/novelvideo/character_visual/character_sheet.py`（新） | 四区契约、提示词及裁切坐标校验 |
| `src/novelvideo/character_visual/character_repair.py`（新） | 完整上下文、问题映射与一次修复额度 |
| `src/novelvideo/character_visual/casting_compiler.py` | 从确认方案编译四区提示词快照 |
| `src/novelvideo/character_visual/casting_submission.py` | 提交前冻结资料、参考图及路由 |
| `src/novelvideo/character_visual/casting_adoption.py` | 成套采用、基础头像与变体头像隔离 |
| `src/novelvideo/task_backend/runners/character_image.py` | 新版生成分支，保留历史格式读取 |
| `src/novelvideo/task_backend/runners/character_qc.py` | 按契约路由独立审核及修复复查 |
| `src/novelvideo/api/routes/character_casting.py` | 方案、生成、裁切、修改与采用 API |
| `frontend/src/types/character-casting.ts` | 资产组、阶段、修复与派生头像契约 |
| `frontend/src/lib/queries/character-casting.ts` | 查询、变更、版本冲突与缓存更新 |
| `frontend/src/components/assets/character-delivery/`（新目录） | 聚焦角色内容组件，不改全局主题 |
| `frontend/src/routes/_app/projects.$project/characters.lazy.tsx` | 保留外壳，替换六标签的详情组装 |

前端新组件：`character-delivery.tsx` 管理选中变体和任务展示；`variant-strip.tsx` 负责新增/切换；`proposal-comparison.tsx` 负责三案与原位微调；`sheet-delivery.tsx` 负责原图、派生头像与采用；`avatar-crop-dialog.tsx` 负责裁切；`voice-strip.tsx` 复用声线 API。无业务需要不新增全局状态层。

## 任务 1：四区契约与兼容数据

**测试文件：** 新建 `tests/test_character_sheet_contract.py`，扩展 `tests/test_character_casting_flow.py`。

- [ ] 在契约测试中覆盖裁切越界、非有限坐标、零尺寸以及像素边界；旧候选缺失新增字段仍能读取，新记录保存 `character_sheet_v4` 与派生头像来源。

```python
import pytest
from novelvideo.character_visual.character_sheet import pixel_box

def test_crop_uses_source_dimensions():
    assert pixel_box((0.1, 0.2, 0.3, 0.4), (1000, 500)) == (100, 100, 400, 300)

@pytest.mark.parametrize("box", [(0, 0, 0, .5), (.9, 0, .2, .4), (float('nan'), 0, .2, .2)])
def test_invalid_crop_rejected(box):
    with pytest.raises(ValueError):
        pixel_box(box, (1000, 500))
```

- [ ] 运行 `uv run pytest tests/test_character_sheet_contract.py -q`，确认因新模块不存在而失败。
- [ ] 创建纯契约函数，输入为归一化 x/y/width/height 和原图像素尺寸；拒绝越界而非默默裁错。提示词明确头像、无头正面、无头侧面、完整背面四区，不混用 v3 三联模板。

```python
import math

def pixel_box(box, image_size):
    x, y, w, h = box
    iw, ih = image_size
    if not all(math.isfinite(v) for v in box):
        raise ValueError("non-finite crop")
    if iw <= 0 or ih <= 0 or min(x, y) < 0 or min(w, h) <= 0 or x+w > 1 or y+h > 1:
        raise ValueError("invalid crop bounds")
    result = (round(x*iw), round(y*ih), round((x+w)*iw), round((y+h)*ih))
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("empty pixel crop")
    return result
```

- [ ] 扩展现有候选模型：保留原 ID、阶段归属与版本字段，新增可选 `sheet_contract`、`sheet_image`、`avatar_image`、`crop_box`、`source_image_digest`、`repair_chain_id`；旧数据默认空值，不伪造 v4。
- [ ] 运行 `uv run pytest tests/test_character_sheet_contract.py tests/test_character_casting_flow.py -q`，通过后只提交该任务文件。

## 任务 2：确认方案后直接生成完整设定图

**文件：** 上表中的 compiler、submission、character_image；测试扩展 `tests/test_character_casting_generation.py`、`tests/test_character_image_runner.py`。

- [ ] 添加回归用例：新角色不存在 portrait.png 时，确认的三案之一可提交；未确认或过期方案拒绝；第一次调用只生成一张完整图；换装引用已采用头像；年龄阶段读取阶段事实。
- [ ] 运行 `uv run pytest tests/test_character_casting_generation.py tests/test_character_image_runner.py -q`，确认新增断言暴露旧链路依赖。
- [ ] 在现有提交入口冻结原文出处、角色事实、画风、方案及用户微调、参考图、路由；新增格式显式进入 v4 编译分支，历史任务维持旧契约。实现分支应遵循：

```text
confirmed proposal + snapshot revision
  → validate ownership and current revision
  → compile character_sheet_v4
  → submit existing persisted image task (idempotent request key)
  → save raw candidate + actual prompt + provider request id
  → independent review; do not activate current assets
```

- [ ] 新版初次生成移除 Portrait 前置检查，但不得删掉其他历史任务所需校验。三案生成继续复用现有 proposals；差异不足或事实冲突时返回明确问题，不伪造三案。
- [ ] 重跑上述测试及 `uv run pytest tests/test_api_character_casting.py -q`，通过后提交相关文件。

## 任务 3：独立审核与完整上下文修复

**文件：** 新建 character_repair，修改 character_qc 和任务衔接；新建 `tests/test_character_sheet_repair.py`。

- [ ] 用提供方替身记录调用参数，测试修复携带原文、参考图、确认方案、失败图、实际提示词、问题建议和历史；缺图或来源失效时零次修复调用；第二次重复问题停止；进程恢复不重置额度。
- [ ] 运行 `uv run pytest tests/test_character_sheet_repair.py -q`，确认新增闭环尚不可用。
- [ ] 实现持久化修复链，按任务链 ID 在现有事务/锁下领取一次额度；不是前端计数。每个问题保存区域、违反要求、证据、建议与稳定身份，复查输出 solved/persisting/unknown，并检查新增问题。

```text
review passed → locate avatar
review unavailable → needs_attention; retry review only
review blocked + repair_count == 0 + complete_context
  → persist repair_count = 1 before dispatch
  → creator receives complete context and preservation constraints
  → changed prompt + change map → image task → independent re-review
review blocked after repair → needs_attention; no automatic image request
```

- [ ] 对提供方未知结果先查询或恢复原任务；重复 callback、取消后晚到结果、旧版本审核不得推进当前资产。用户再次发起修改属于新一轮，但继承历史问题与图片上下文。
- [ ] 重跑修复测试与任务 2 测试，断言每轮最多两次图片调用，审核故障不会重画。通过后提交。

## 任务 4：定位、裁切与成套采用

**文件：** character_sheet、casting_store、casting_adoption、角色 API；新建 `tests/test_character_sheet_adoption.py`，扩展 `tests/test_api_character_casting.py`。

- [ ] 测试候选裁切不改变当前头像、基础形象采用更新角色头像、变体采用不更新基础头像，以及不同版本混用/跨项目候选/丢失原图/并发采用被拒绝。
- [ ] 运行 `uv run pytest tests/test_character_sheet_adoption.py tests/test_api_character_casting.py -q`，记录新增失败。
- [ ] 定位调用传实际图像输入并返回归一化矩形；用任务 1 校验后通过现有图像处理依赖裁切源图。保存原图摘要、尺寸与裁切框。失败转为待手动裁切，不能自动触发生图。
- [ ] 在现有候选作用域下新增裁切变更 API，输入 `crop_box` 和 `expected_version`；服务端按项目/角色/候选定位图片，禁止传任意路径。写入派生图后原子更新候选版本。
- [ ] 采用事务检查候选快照仍有效且图片齐全，同时激活设定图、对应设计与派生头像。基础形象才更新全局角色头像引用；其他变体仅更新自身引用。中途写入失败保持旧资产组。
- [ ] 运行上述测试以及 `uv run pytest tests/test_api_character_identity_costume.py tests/test_api_character_image_selection.py -q`，通过后提交。

## 任务 5：在现有页面壳内接入角色交付区

**文件：** character-casting 类型与查询、新组件目录、角色路由；新建 `frontend/src/__tests__/components/assets/character-delivery.test.tsx`。

- [ ] 按现有 Vitest/Testing Library 与 QueryClient 测试模式创建行为测试：新增变体、原位比较编辑、提交后切换角色再返回、采用后刷新列表；候选过期不允许采用，旧形象仍可查看。
- [ ] 从 frontend 执行 `pnpm test src/__tests__/components/assets/character-delivery.test.tsx`，确认组件不存在或行为缺失导致失败。
- [ ] 用现有 Button、Dialog/Sheet、表单与主题令牌实现组件。状态由服务端任务/候选查询恢复，只有选中变体、展开区和未提交输入为本地 UI 状态。

```text
Character page shell (unchanged)
  CharacterHeaderRow (existing)
  CharacterDelivery
    VariantStrip
    active task summary
    current asset (collapsed during proposal/review work)
    ProposalComparison OR SheetDelivery
    VoiceStrip
    on-demand source/history drawers
```

- [ ] 查询契约分别呈现当前可用资产与后台制作任务。不同变体任务按变体 ID 归属，同变体重复提交通过幂等处理；不继承原型“一名角色仅一个任务”的演示限制。
- [ ] 新增变体填写名称和类型；年龄/身份变化沿用身份阶段 API，换装沿用已有造型状态模型，不再建立第二份人物事实。六标签下的功能迁入新的入口后再移除原标签组装。
- [ ] 三案选择后保留对比上下文，修改输入不会在角色切换时静默丢失；复用项目现有未保存变更处理。完成后仅突出采用与提出修改，后者直接携带当前交付，不强制返回三案。
- [ ] 执行 `pnpm test src/__tests__/components/assets/character-delivery.test.tsx src/__tests__/routes/characters.ce.test.tsx`，通过后提交。

## 任务 6：裁切交互、声音与完整验收

**文件：** avatar-crop-dialog、voice-strip、sheet-delivery；新建 `frontend/src/__tests__/components/assets/character-avatar-crop.test.tsx`，复用 `character-voice-panel` 与现有声线 queries。

- [ ] 添加源图缩放后坐标转换、键盘/拖动裁切、取消不保存、保存后头像失效刷新，以及声音更换/试听停止的行为测试。
- [ ] 运行 `pnpm test src/__tests__/components/assets/character-avatar-crop.test.tsx src/__tests__/components/assets/character-voice-panel.test.tsx`，确认新交互缺失。
- [ ] 复用已安装 react-image-crop；以原始图像宽高转换坐标，裁切预览与提交使用同一矩形。保存调用候选裁切 API，不调用生成 API。头像放在图像旁的小预览，基础头像保存成功后更新左侧列表缓存。
- [ ] 声音条使用真实已有声线数据与试听资源，设置在抽屉展开；角色切换或卸载停止旧音频，音频不可用时明确错误，不用假试听。
- [ ] 重跑任务 5、6 的前端测试，并运行 `pnpm exec tsc -b`；后端运行任务 1–4 的定向测试。记录失败是否来自本次修改，不能用已有失败掩盖新增问题。
- [ ] 在真实页面检查桌面和窄屏：外壳与主题一致，空角色首次出图、基础形象、换装、年龄阶段、缺失资料、裁切失败、修复耗尽、刷新恢复可操作。截图记录默认态、三案态、交付态与裁切态；人工确认前不宣称视觉验收通过。
- [ ] 使用提供方替身先验证整个链路；真实样例调用记录模型、调用数、各阶段耗时与费用。在执行时确认适用的生成权限及预算，不把设计批准当作不限额生成授权。
- [ ] 完成审核后提交任务文件，交付验证结果及限制。不自动 push 或合并其他人的改动。

## 自检与交接

- [x] 页面壳与主题、当前形象与变体、三案原位编辑、紧凑进度、候选采用/修改、简洁声音均有实施任务。
- [x] 首次无头像生成、四区契约、完整上下文修复、一次额度、重复问题停止均有后端任务。
- [x] 裁切可追溯、基础/变体头像隔离、版本及并发校验、历史兼容、性能测量有验收步骤。
- [ ] 实施前重新读取工作区目标文件变化，按当前真实接口完善每项局部补丁；不得用本计划伪造现有方法名。

本计划只定义实施与验证顺序，不表示列出的测试已运行或新功能已完成。
