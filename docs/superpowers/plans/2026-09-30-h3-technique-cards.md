# MiniMax H3 精选手法卡片实现计划

> **面向 AI 代理的工作者：** 使用 `executing-plans` 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 让画布导演台的每个片段可选一张精选手法卡，由现有 runtime 结合素材改写，并在生成历史保留实际使用的卡片版本与投影。

**架构：** 服务端维护只读版本化目录与兼容性判断。草稿仅保存 `id@version` 选择，提交时解析、验证并冻结卡片投影；优化器只把目标片段的投影加入结构化输入。前端从目录接口读取同一规则并提前提示，后端始终重新校验。

**技术栈：** Python/Pydantic/FastAPI/Pytest；React/TypeScript/Vitest；现有 H3 结构化 runtime 与 RunningHub 提交链。

---

## 文件职责

- `src/novelvideo/freezone/video_director/techniques.py`：精选目录、内容哈希、版本解析、适用性和 H3 投影；不包含第三方完整提示词。
- `src/novelvideo/freezone/video_director/models.py`：草稿选择与冻结投影的严格模型。
- `src/novelvideo/freezone/video_director/service.py`：提交时解析和冻结；重试只使用冻结内容，优化缓存与内容哈希关联。
- `src/novelvideo/freezone/video_director/optimizer.py`：只向选卡片段传递结构化手法意图，明确事实优先级。
- `src/novelvideo/api/routes/freezone_video_director.py`：只读目录端点。
- `frontend/src/api/videoDirector.ts`、`frontend/src/features/canvas/domain/canvasNodes.ts`：目录和选择的前后端 wire 契约。
- `frontend/src/features/canvas/director/TechniqueCardPicker.tsx`：片段卡片选择、禁用原因、来源链接。
- `frontend/src/features/canvas/director/{VideoDirectorPanel,DirectorSegmentEditor,directorValidation,DirectorHistory}.tsx`：接入选择与历史；既有无卡行为不变。
- `frontend/public/locales/{zh,en}/translation.json`：中英文文案。
- `tests/freezone/video_director/test_techniques.py`、相关服务与优化测试、前端 director 测试：真实契约回归。

### 任务 1：只读精选目录与适用性

- [x] 写失败测试：目录有 8–12 张启用卡，ID/版本唯一、哈希由手法内容确定、每张有追溯链接；`i2v`/`fl2v`/`ref_only` 与对齐时长的兼容性返回精确原因。
- [x] 运行 `pytest tests/freezone/video_director/test_techniques.py -q`，确认失败。
- [x] 在 `techniques.py` 建立严格卡片模型与 8 张有具体来源支撑的原创归纳卡；来源只存链接和提炼依据，读取目录不联网；按 `frames_for_duration(..., H3_FPS)` 的对齐秒数检查时长。
- [x] 增加 `GET /techniques`，返回目录、目录版本及契约字段；API 测试验证结构。
- [x] 运行目录和 API 测试，提交本任务。

### 任务 2：草稿选择、提交冻结与历史

- [x] 写失败测试：无卡草稿继续有效；选卡解析错误、退役和模式/时长冲突会拒绝提交且指出片段；合法卡在 attempt 冻结 ID、版本、哈希、投影、来源。
- [x] 扩展 `DirectorSegment`，加入可空 `technique` 选择；在 attempt 的 `detail` 存 `frozen_techniques`，按片段 ID 索引并保持历史可读。
- [x] `DirectorService.create` 在图片冻结前解析并校验；`_resume_locked` 只从 attempt 的冻结投影读取，不访问在线目录；旧 attempt 无字段保持原行为。
- [x] 优化缓存校验加入冻结投影的稳定哈希；重试同一 attempt 不因目录更新改写已冻结手法。
- [x] 运行 service/store/capabilities 测试，提交本任务。

### 任务 3：逐片段结构化改写

- [x] 写失败测试：仅目标片段的 `INPUT_JSON` 有 `technique`；相邻片段不继承；无卡输入结构不变；冲突或 runtime 失败仍阻止供应商提交。
- [x] 给 `optimize` 增加冻结投影参数，在对应 `source_data` 加 `technique`；提示词明确原文、图片、对白和已对齐时长优先，卡片只指导动作节奏、表演、运镜与结尾构图。
- [x] 运行 optimizer/service 相关测试，提交本任务。

### 任务 4：前端选择和前后端校验一致

- [x] 写失败测试：选择卡、复制/删除/重排/刷新草稿保持绑定；不兼容项可见但不可选，已选失配保留并禁止生成；目录读取失败允许无卡生成。
- [x] 传输层补齐 `technique` wire 字段及目录读取；在面板读取目录并用 `alignDirectorDuration` 计算与后端一致的模式/时长。
- [x] `TechniqueCardPicker` 展示标题、效果、来源、适用范围和禁用理由，支持清除选择；接入片段编辑器与历史视图。
- [x] 运行 director 前端测试、类型检查和 Vite 构建，提交本任务。`tsc -p tsconfig.app.json --noEmit` 仅剩与本任务无关的 `team-studio.test.tsx` 缺失 `../studio-context`。

### 任务 5：端到端契约与离线质量评估准备

- [x] 补端到端测试覆盖 H3 首帧、首尾帧、H3 Ref 三种路径及无卡基线，确认没有新增 QC 闸门或原文直送回退。
- [x] 为离线成对比较保存固定样本和评分字段模板，不在生产生成路径执行盲评或增加收费生成。
- [x] 运行 `pytest tests/freezone/video_director -q`、前端相关测试、类型检查和构建；检查 `git diff --check`，记录任何环境性限制。
- [x] 审查变更、提交，保留隔离工作树供用户审阅。
