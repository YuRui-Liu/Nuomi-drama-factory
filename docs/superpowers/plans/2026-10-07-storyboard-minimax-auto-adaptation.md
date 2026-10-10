# 分镜到 MiniMax 自动适配实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 让每格独立镜头成为组视频时间线的一个首帧片段，最终经现有组级入口生成视频。

**架构：** 后端提供可幂等重试的拆图和绑定准备服务，前端只展示持久化结果、编辑和冲突。直接生成与编排调用同一个准备服务；输入快照通过校验并持久化后才创建 Director attempt。

**技术栈：** Python/Pillow/Pydantic/FastAPI、React/TypeScript、pytest/Vitest。

**规格：** `docs/superpowers/specs/2026-10-07-storyboard-minimax-auto-adaptation-design.md`。

---

## 工作区和文件责任

当前功能依赖尚未提交的编排、分镜 UI 等修改，不能从裸 HEAD 新建工作区后假定依赖存在。执行前记录状态并保留现有改动；提交只包含本任务文件或明确属于本任务的 diff，禁止一键提交整个工作区。

新增后端 `src/novelvideo/freezone/video_director/storyboard_adaptation.py`：图片拆分、指纹、片段构造及冲突判定。
新增后端 `src/novelvideo/freezone/video_director/storyboard_preparation.py`：项目内资产读取、画布版本校验、准备结果持久化。
修改 `src/novelvideo/api/routes/freezone_video_director.py`：公开准备接口及提交前一致性校验。
修改 `src/novelvideo/freezone/orchestration/adapters/__init__.py`：复用准备服务，替换无条件追加 references。
修改 `src/novelvideo/freezone/orchestration/planning.py` 和 `adapters/requests.py`：结构化镜头与无边框宫格提示。
修改 `frontend/src/api/videoDirector.ts`、`domain/canvasNodes.ts`：准备接口和持久化类型。
新增 `frontend/src/features/canvas/director/StoryboardAdaptation.tsx`：拆图预览、逐镜头描述和裁剪调整、冲突操作。
修改 `frontend/src/features/canvas/director/useVideoDirectorTask.ts`、`nodes/VideoDirectorNode.tsx`：先保存和准备再冻结提交。
修改 `frontend/src/features/canvas/nodes/StoryboardGenNode.tsx`：单格预览及准备状态。
修改 `frontend/src/features/canvas/domain/videoDirectorBindings.ts`：自动适配绑定与普通 reference 连线互斥。

## 任务 1：确定性图片拆分

- [ ] 在 `tests/freezone/video_director/test_storyboard_adaptation.py` 写失败测试，使用四色 101×57 图片验证 2×2 裁剪顺序与边界，不连接供应商。

```python
from PIL import Image
from novelvideo.freezone.video_director.storyboard_adaptation import grid_rectangles

def test_grid_covers_odd_dimensions_in_reading_order():
    assert grid_rectangles(101, 57, 2, 2) == [
        (0, 0, 50, 28), (50, 0, 101, 28),
        (0, 28, 50, 57), (50, 28, 101, 57),
    ]
```

- [ ] 运行 `.venv/bin/python -m pytest tests/freezone/video_director/test_storyboard_adaptation.py -q`，确认缺少函数导致失败。
- [ ] 实现 `grid_rectangles(width, height, rows, cols)`；拒绝非正整数和产生零宽高的格子。矩形边界使用下列计算，不使用累积四舍五入。

```python
return [(col * width // cols, row * height // rows,
         (col + 1) * width // cols, (row + 1) * height // rows)
        for row in range(rows) for col in range(cols)]
```

- [ ] 实现 `split_storyboard(image_bytes, rows, cols, target_size, rects=None)`：解码、EXIF 转正、验证自定义裁剪框在图内且非空，逐格 crop；`ImageOps.pad` 等比补边生成目标尺寸，保留原裁剪图。返回原图 SHA256、矩形和图片字节；不在纯函数中写画布。
- [ ] 增加损坏图片、1×3、越界矩形、画幅不拉伸测试，运行同一测试文件至通过。提交本任务新增模块和测试。

## 任务 2：镜头信息与绑定契约

- [ ] 扩展同一测试文件，验证输入 4 格、组总时长 18 秒得到 4 片段，首帧不同、尾帧空、references 空；使用运行时 H3_FPS 验证总帧数。增加缺少描述必须报错的测试。
- [ ] 运行上述 pytest，确认片段构造缺失导致失败。
- [ ] 定义 `build_storyboard_draft(draft, source_node_id, cells, shots, total_frames, fps)`。cells 含 image_id/url/index；shots 含 description 和可选 duration_seconds。描述为空时报 `shot_description_required`，数量不一致时报 `shot_count_mismatch`。

```python
base, remainder = divmod(total_frames, len(cells))
frame_counts = [base + (index < remainder) for index in range(len(cells))]
segments = [{"id": f"{source_node_id}:cell:{index}",
             "prompt": shot["description"],
             "duration_seconds": count / fps,
             "first_frame": {"image_id": cell["image_id"], "url": cell["url"]},
             "last_frame": None}
            for index, (cell, shot, count) in enumerate(zip(cells, shots, frame_counts))]
```

- [ ] 已有逐镜头时长时保留其对齐后总帧数，检查与组时长一致；不一致明确报错。使用 DirectorDraft 和 validate_generation 校验最终结构。拒绝没有足够帧数的分配。
- [ ] 在 `planning.py` 添加结构化 shots 字段（description、duration），新增生成计划要求按格对应镜头；有字段时检验镜头数量和总时长，旧计划无字段保持可读但标记待补全。`requests.py` 强调等大、无边框、无文字，并使用逐镜头描述。
- [ ] 在 `tests/freezone/orchestration/test_storyboard_contract.py` 增加计划到单格描述映射测试；运行该文件及任务 1 测试至通过。提交限定 diff。

## 任务 3：准备服务和冲突保护

- [ ] 新增 `tests/freezone/video_director/test_storyboard_preparation.py`，用临时项目和画布验证准备后两节点一起持久化，重复准备不新增文件，来源更新使旧绑定失效，手动修改阻止静默覆盖。
- [ ] 运行 `.venv/bin/python -m pytest tests/freezone/video_director/test_storyboard_preparation.py -q`，确认准备模块缺失失败。
- [ ] 实现 `prepare_storyboard_director(project_dir, canvas_id, node_id, base_revision, request_id, resolution)`；resolution 只接受 `safe`、`keep_edits`、`replace_generated`。读取画布找唯一上游叙事分镜；多来源报 `ambiguous_storyboard`。复用项目资产 URL 解析和路径安全校验，不任意读取本地路径或远程 URL。
- [ ] 输出文件名使用内容摘要与裁剪配置摘要；输出放现有项目静态资产目录。使用现有 save_canvas/patch_graph 原子更新，版本变化报冲突。新增节点字段如下，并保持其他字段原样：

```json
{"storyboardAdaptation":{"schemaVersion":1,"sourceNodeId":"board-g1",
 "sourceFingerprint":"sha256","status":"ready","baselineDraft":{},
 "cells":[]},"activeInputMode":"frames"}
```

- [ ] baselineDraft 保存上次自动结果；逐字段比较当前 draft 和基线，仅覆盖未手动修改字段。保留尾帧、提示词、时长的手动修改时再次校验。源变更且有冲突返回 `adaptation_conflict`，不得创建 attempt。源未变的重复准备不递增 draft revision；有效变更递增并使旧视频 resultRevision 过期。
- [ ] 为准备接口添加项目权限、幂等和 revision 测试，运行任务 1–3 测试至通过；提交新增服务和限定路由 diff。

## 任务 4：统一两条提交路径

- [ ] 在 `tests/freezone/orchestration/test_adapters.py` 增加回归：frames 的首帧不得被上游角色 refs 污染；显式 ref 仍接受参考输入；自动绑定过期时不得 enqueue。
- [ ] 运行 `.venv/bin/python -m pytest tests/freezone/orchestration/test_adapters.py -q` 确认当前无条件追加 references 失败。
- [ ] 在编排入口复用准备服务，读取准备后的版本作为提交快照；不修改已冻结 attempt。普通手动帧连线按 edge slot 投影到正确 segment。明确使用模式投影：

```python
if mode == "frames":
    draft["references"] = []
else:
    for segment in draft["segments"]:
        segment["first_frame"] = None
        segment["last_frame"] = None
```

- [ ] `create_attempt` 在创建前检查自动绑定来源指纹；既有 request_id 重试优先返回同一 attempt，不因之后画布修改生成重复付费任务。保持未使用自动适配的历史客户端兼容。
- [ ] 增加 API 与编排最终 draft 相同、保存冲突不提交、Ref 回归测试；运行任务 1–4 测试至通过并提交限定 diff。

## 任务 5：画布预览、编辑和错误反馈

- [ ] 新增 `frontend/src/__tests__/features/canvas/storyboard-adaptation.test.tsx`。mock 准备接口验证：显示四张单格图；准备未成功不提交；冲突提供保留/重新适配；显式 Ref 不切回；缺失逐格描述可以编辑补全。
- [ ] 运行 `npm test -- --run src/__tests__/features/canvas/storyboard-adaptation.test.tsx`（工作目录 frontend），确认组件缺失失败。
- [ ] 添加 API 适配类型及组件，按 cell index 显示“镜头 N”、对应首帧和时长。裁剪调整编辑源图像素矩形；保存通过准备接口重新校验。保持预览与后端返回 cells 一致。
- [ ] 修改生成处理顺序为保存画布 → 调用准备 → 应用返回节点版本 → 投影有效模式 → 校验 → 冻结现有 pendingSubmission → 原有提交日记流程。检查异步返回仍属当前 project/canvas/node，避免切画布串写。
- [ ] 为手动模式切换持久化用户选择；自动适配的已有参考连线仅在 Ref 生效，frames 模式不报参考图缺失。来源更新显示过期状态并阻止旧提交；重新加载恢复同样状态。
- [ ] 运行上述测试以及 `video-director-bindings.test.ts`、`storyboard-narrative.test.tsx`，检查实际文件名并运行现有 Director 提交日记回归；运行 `npx tsc --noEmit -p tsconfig.app.json`，单列原有错误和新增错误。提交限定 diff。

## 任务 6：当前画布恢复与视频验收

- [ ] 重新读取 `skills/nuomi-production/SKILL.md`，通过公开生产 CLI 和画布 API 获取最新 revision。检查组 1、4 图片是否仍存在，不能照旧快照覆盖。
- [ ] 通过准备接口拆分已有组图。逐格描述从已有可信计划恢复；若没有，按原剧本和实际单格画面整理描述后通过公开 API 保存，无法匹配的组明确阻塞。保留全部已有素材。
- [ ] 浏览器检查单格顺序、对应片段首帧、总时长及 Ref/frames 切换；确认已保存后刷新仍一致。留一张实际页面证据图。
- [ ] 运行 `.venv/bin/python -m pytest tests/freezone/video_director tests/freezone/orchestration -q` 和任务 5 前端测试，记录已有失败，不将部分通过写成全通过。
- [ ] 用公开组级生产入口提交一个准备完成的组，读取保存的并发设置；核对请求的实际首帧列表、一个组的多片段时间线、任务成功和视频播放。供应商错误保留具体信息，不反复付费重试、不绕过公开入口。
- [ ] 运行 `git diff --check`，审查限定 diff。报告已修复范围、回归结果、当前画布成功/待生成/阻塞组及视频证据；不把请求被接受等同于已出视频。

## 自检结果

## 执行记录（2026-10-08）

任务 1–5 的实现及独立规格/质量审查已完成。自动适配使用独立单格首帧，保留手动尾帧、描述和时长，支持裁剪修正、来源过期校验、保存冲突与提交幂等；手动帧连线的有效草稿在冻结前持久化。状态目录与媒体目录分离的本地配置已实测。

验证：前端 8 个测试文件共 100 项通过；后端扩展回归 238 项通过、1 项原有失败（`test_invalid_image_size_rejected_before_run_created`，非法图像尺寸期望 422、实际 202）。TypeScript 检查仍有 3 项原有错误；`git diff --check` 通过。未因当前工作区包含其他任务修改而作整仓提交。

当前验收画布的叙事组 1、4、5 已通过公开准备 API 保存，每组 4 张独立首帧；组 2、3、6、7 尚无分镜图。浏览器刷新后确认 MiniMax H3 首尾帧模式及片段首帧恢复正确，页面证据保存于 `/private/tmp/nuomi-auto-adaptation-preview.png`。

组 1 通过公开 Director API 提交，attempt 为 `f4bb5234f694471d8ea83961dcb6c15b`，provider task 为 `2107997662900092929`，已绑定原画布 `video-g1`。任务已 completed，本地 ffprobe 确認 19.25 秒、1280×736 且有音轨；请求 18 秒按模型约束编译为 462 帧。此结果为此前 Frames 路径的历史产物，用户随后要求自动适配也走 Ref，参见 `2026-10-08-storyboard-auto-ref.md`；不能将这个视频记为 Ref 验收结果。

拆图和裁剪调整覆盖于任务 1、5；逐格语义、时长覆盖于任务 2；版本、幂等、人工编辑及旧视频保护覆盖于任务 3；两提交入口与模式互斥覆盖于任务 4；刷新恢复覆盖于任务 5；旧画布与真实组视频验收覆盖于任务 6。全流程保留组级生产单位。
