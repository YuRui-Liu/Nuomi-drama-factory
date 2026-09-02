# 角色小传、三套视觉提案与提取锁定实施计划

> 执行方式：测试先行；锁定持久化、视觉提案领域逻辑、前端交互并行，随后集中接入抽取流程与头像生成门禁。

## 验收目标

1. 自动提取为每个未锁定角色生成可追溯人物小传、剧本事实和 3 套差异化视觉提案，并标出推荐方案。
2. 已锁定角色不进入模型输入、不被候选结果覆盖，事务提交前再次检查锁定状态；未在本次结果出现的角色不删除。
3. 未锁定既有角色可刷新自动字段和未确认提案，但不覆盖人工资产、声音、已确认 VisualBible。
4. 头像生成必须依赖已确认 VisualBible；缺失时在调用图像供应商前返回可操作错误。
5. 前端可锁定/解锁角色、查看小传和事实、切换三套提案、编辑并确认视觉设定。

## Task 1：锁定字段与原子合并

**文件**
- 修改 `src/novelvideo/models.py`
- 修改 `src/novelvideo/sqlite_store.py`
- 修改 `src/novelvideo/api/schemas.py`
- 修改 `src/novelvideo/api/routes/characters.py`
- 修改 `tests/test_sqlite_structured_phase_c.py`
- 修改 `tests/test_api_characters_asset_contract.py`

**步骤**
1. 先添加失败测试：SQLite 迁移默认解锁；锁定角色在发布时保持全部字段；未锁定角色只刷新自动字段；提交前锁状态变化仍能阻止覆盖；API 锁定操作幂等。
2. 为 `NovelCharacter`、SQLite schema/迁移/读写和角色列表响应添加 `extraction_locked: bool = False`。
3. 新增 `PATCH /projects/{project}/characters/{name}/extraction-lock`，请求体 `{locked: bool}`。
4. 将 `publish_character_analysis_atomic` 改为返回 `{added, updated, locked_skipped, preserved}`，事务内按锁状态做受控 upsert；不删除缺席角色。
5. 运行：`.venv\\Scripts\\python.exe -m pytest tests/test_sqlite_structured_phase_c.py tests/test_api_characters_asset_contract.py -q`。

## Task 2：人物小传、三套提案与反通用脸质量门

**文件**
- 修改 `src/novelvideo/character_visual/models.py`
- 新建 `src/novelvideo/character_visual/proposals.py`
- 修改 `src/novelvideo/character_visual/store.py`
- 新建 `tests/character_visual/test_proposals.py`
- 修改 `tests/character_visual/test_store.py`

**步骤**
1. 先添加失败测试：必须正好 3 套提案、只能有 1 个推荐、每套至少 3 个身份锚点、至少 1 个非对称/个性细节；提案之间不可结构同质化。
2. 扩展 `CharacterDesignProposal`：`recommended`、`identity_anchors`、`asymmetry_detail`、`quality_issues`。
3. 实现 `validate_design_proposals()` 和 `build_character_visual_workspace()`：事实与创意分层；禁止明星名、空泛审美词替代骨相；质量不合格不持久化。
4. 为 workspace store 增加批量原子保存，保留已确认 VisualBible 和人工选中状态。
5. 运行：`.venv\\Scripts\\python.exe -m pytest tests/character_visual -q`。

## Task 3：结构化抽取接入与锁定输入隔离

**文件**
- 修改 `src/novelvideo/structured_extraction.py`
- 修改 `src/novelvideo/structured_builders.py`
- 修改 `src/novelvideo/task_backend/runners/graph_build.py`
- 修改 `tests/test_structured_extraction.py`
- 新建 `tests/test_character_structured_builder.py`

**步骤**
1. 先添加失败测试：锁定角色名/别名从分块输入中隔离；小传包含经历、关系、性格、戏剧功能；构建结果返回新增/更新/锁定跳过计数。
2. 扩展结构化候选契约，要求模型输出 narrative profile、事实证据和 3 套视觉提案；输入提示明确仅依据剧本事实推导、不以角色名猜外貌。
3. 构建开始时快照锁定角色并排除；发布前由 SQLite 再检查；为未锁定角色保存 workspace，保留 confirmed VisualBible。
4. graph task 把统计与质量问题写入结果/日志。
5. 运行：`.venv\\Scripts\\python.exe -m pytest tests/test_structured_extraction.py tests/test_character_structured_builder.py -q`。

## Task 4：头像生成硬门禁

**文件**
- 修改 `src/novelvideo/task_backend/runners/character_image.py`
- 修改 `tests/test_character_image_runner.py`

**步骤**
1. 把现有“缺 face_prompt 自动拼通用脸”的测试改成失败门禁测试，并断言供应商未调用。
2. 从 `CharacterVisualWorkspaceStore.get_confirmed_bible()` 编译头像提示词；无 confirmed VisualBible 时返回 `CHARACTER_VISUAL_BIBLE_REQUIRED` 和 `transport_called: false`。
3. 删除生产路径中的通用脸 fallback，仅保留兼容诊断函数（若仍有调用者）。
4. 运行：`.venv\\Scripts\\python.exe -m pytest tests/test_character_image_runner.py tests/character_visual/test_compiler.py -q`。

## Task 5：前端锁定与提案工作台

**文件**
- 修改 `frontend/src/types/character.ts`
- 修改 `frontend/src/lib/queries/characters.ts`
- 修改 `frontend/src/components/assets/character-visual-profile/character-visual-profile.tsx`
- 修改 `frontend/src/routes/_app/projects.$project/characters.lazy.tsx`
- 修改 `frontend/src/__tests__/components/assets/character-visual-profile.test.tsx`
- 修改 `frontend/src/__tests__/routes/characters.ce.test.tsx`

**步骤**
1. 先添加失败测试：锁定按钮显示状态且可切换；三套提案可选；确认前可编辑；锁定角色重新提取后 UI 数据不变。
2. 增加 extraction lock query/mutation；构建完成后刷新角色与视觉 workspace。
3. 将只读视觉提案区改为卡片选择、推荐标识、差异锚点、编辑/确认动作；已确认 VisualBible 明确展示版本。
4. 运行：`npm test -- --run frontend/src/__tests__/components/assets/character-visual-profile.test.tsx frontend/src/__tests__/routes/characters.ce.test.tsx`（在 `frontend` 目录按项目脚本调整路径）。

## 集成验收

1. 运行全部针对性后端与前端测试及 `git diff --check`。
2. 启动本地服务，在真实项目 `test2` 锁定周禾，重新提取；验证周禾不变，其余角色获得小传和三套提案。
3. 未确认 VisualBible 时尝试生成头像，确认供应商未调用；选择并确认方案后真机生成一张头像，核对任务日志中的最终提示词和 VisualBible revision。
4. 仅提交本计划及本功能实际修改文件，提交信息使用 `Hermes: 实现角色视觉提案与提取锁定`。
