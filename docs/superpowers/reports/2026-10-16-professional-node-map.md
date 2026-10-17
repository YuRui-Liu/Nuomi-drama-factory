# 专业 Agent / 节点能力地图

日期：2026-10-16
范围：19 类画布节点、能力目录的 35 个操作、真实执行与恢复入口，以及当前 Creation Agent、资产版本和叙事组生产流程的接缝。本文只做静态代码与既有测试核对；未启动服务、未提交模型或媒体任务，也未改动代码。

## 结论

- 后端能力目录声明 19 类节点、35 个操作。API 返回的运行时目录以 `available_operations()` 标记 26 项已接入、9 项 `adapter_not_installed`。`available=true` 只表示 orchestration adapter 有分发路径，不代表模型、凭据、余额、队列或输入已经就绪。
- 前端 `nodeRegistry.ts` 定义节点外观、默认数据、菜单可见性和连线规则；它不是执行注册表。Capability 声明、浏览器按钮、Freezone HTTP handler、`NodeAdapter` 的可执行操作是不同层次。
- 画布 RunStore、Scheduler、媒体验证和结果投影已经构成可复用的执行骨架。部分操作会通过它调用既有 Freezone API / task backend；节点结果最终写回画布，但成本与账单明细不在 RunStore 中。
- Creation Agent 当前只读受限上下文、写文本候选、起草本体定义、提出媒体方案。Agent 权限不是节点操作权限；`media.propose` 明确关闭执行。通用 `OperationRequest` 虽枚举 `node.run`，实际服务会明确拒绝。
- 因此产品确认的“专家在授权范围内直接调用节点，并让节点投影已有任务记录”尚未接通。现有专家 adoption、授权委托、节点执行、任务/成本与本体版本关系分散在不同域。
- Episode production 仍受 `skills/nuomi-production/SKILL.md` 约束：以叙事组为生产单位，整组分镜 → 整组视频 → 合成，并复用已有引用、版本、任务及并发设置。普通画布 Run 的 `videoDirectorNode` / `videoComposeNode` 组合目前不是这个生产入口的等价实现。

## 口径与共同权限、恢复路径

`capabilities.py::_SPECS` 是 19 类节点和 35 个操作名的声明源。`capabilities(available=None)` 不传集合时会将全部操作标成不可用；HTTP `GET /projects/{project}/freezone/orchestration/capabilities` 才传入 `available_operations()`。当前测试 `tests/freezone/orchestration/test_graph.py::test_capability_catalog_matches_frontend_and_expands_skills` 只检查类型集合、技能清单和非空操作，没有验证每个操作的运行时可用位；`tests/freezone/orchestration/test_api.py::test_api_lists_all_nodes_including_blocked_operations` 只断言返回 19 个节点。因此下表状态来自实现路径交叉核对，不是从目录名称推断。

编排 API 由 `get_api_user` 认证，并通过 `api.routes.freezone_orchestration._resolve_freezone_project` 做项目角色检查：建画布、改图、启动、取消、恢复和重试默认要求 editor；能力目录、预览、运行记录和事件读取要求 viewer。恢复 worker 会重新检查保存的 actor 对项目的访问权。实际 Freezone handler 也会解析 actor 与项目上下文。专家服务另有边界：`creation_library.agent_runs_service` 和 `agent_tools_service` 拒绝 `agent_session` 直接调用；Agent 的动作须经过可信宿主及委托校验。

已接入的节点操作共用 `POST /freezone/orchestration/runs`：`start_run` 检查目标子图和 adapter，`RunStore.create` 冻结图快照、输入指纹、请求幂等键和每节点 attempt，`Scheduler.tick/drive` 控依赖、并发、投递和核验，`NodeAdapter.reconcile/verify/project` 取回现有任务结果、验证项目内文件并投影到节点。`GET /runs/{id}`、画布 runs/events、`POST /runs/{id}/resume|retry|cancel` 是共享状态与恢复入口；API startup 的 `recover_runs` 会恢复数据库内非终态记录。取消任务可恢复；失败付费调用须显式 retry；retry 只为失败 attempt 开新记录、已完成依赖复用。提交状态未知不能盲目重提。当前 `NodeAdapter.reconcile_submission` 只能凭本地队列容量拒绝证据判定“未入队”；其他缺响应场景保留 `submission_unknown`，没有通用供应商查询器。

表中 `编排可用` 是上述共享入口中的 `available=true`；`未接入` 指 `NodeAdapter` 没有对应执行分支或 `ROUTES`，不是说前端完全没有同名交互。恢复码和授权码在全文统一适用：HTTP 路径为 `A1`；节点运行恢复为 `R1`；仅前端/独立旧服务的路径标 `UI` 或说明其独立存储。

## 逐节点、逐操作地图

| 节点类型 | 声明操作及编排状态 | 已核实 handler / 接入边界 | 建议使用专家（当前无节点绑定） | 主要测试证据 |
|---|---|---|---|---|
| `uploadNode` | `validate` 编排可用（LOCAL）；`upload` 未接入 | `NodeAdapter.submit` 本地校验；浏览器上传由 `nodes/UploadNode.tsx::processFile` 及 Freezone upload API 承担，不走 NodeAdapter。 | 素材管理员、场景/角色资料员 | upload 手工链路未见 orchestration 操作级测试；通用图目录测试。 |
| `imageNode` | `edit` 编排可用 | `ROUTES` → `NodeAdapter.submit` → `adapters.requests::build_request` → `freezone.freezone_edit`。 | 概念美术、图像修订专家 | `test_adapters.py::test_reference_input_order_obeys_saved_order`。 |
| `imageGenNode` | `generate`、`edit` 编排可用 | `freezone.freezone_gen` / `freezone.freezone_edit`。count 目前必须为 1；图片尺寸和型号会在 run admission 校验。 | 概念美术、角色/场景设计师 | `test_image_node_parameters_override_stale_request_and_include_style_camera`、`test_image_request_consumes_upstream_references_and_text`、`test_unimplemented_image_batch_does_not_silently_generate_one`。 |
| `exportImageNode` | `export` 未接入 | `nodes/ImageNode.tsx` 是结果/预览节点；重生成由 `application/regenerateExportNode.ts` 回到上游生成逻辑，能力目录中的 `export` 没有 orchestration handler。 | 出图整理/资产管理专家 | 未见专门 adapter 测试。 |
| `beatContextNode` | `validate` 编排可用（LOCAL） | 由 `NodeAdapter.submit` 按节点数据本地完成，不调用供应商。 | 剧本分析、上下文整理专家 | 未见该类型独立 adapter 测试。 |
| `textAnnotationNode` | `validate` 编排可用（LOCAL） | 本地 adapter 将 `content` 作为 `text` 产出；没有模型调用。 | 文本编剧、资料校对专家 | `test_text_input_local_adapter_is_consumable_without_fake_task`。 |
| `groupNode` | `validate` 编排可用（LOCAL） | 本地容器校验并输出容器 artifact；不是媒体任务。 | 制片统筹、画布整理专家 | scheduler 测试覆盖依赖与运行；未见 group 专属执行测试。 |
| `storyboardNode` | `split` 未接入 | 现有拆分是浏览器工具：`application/toolProcessor.ts::splitStoryboard` → `webImageSplitGateway`，不是 node run adapter。 | 分镜师、图像整理专家 | `StoryboardFramePairs` / 工具组件测试不等同于 adapter 覆盖；目录操作没有 handler 测试。 |
| `storyboardGenNode` | `generate` 编排可用 | `freezone.freezone_gen`；叙事模式会先走 `adapters.storyboard::compile_canvas_storyboard`。UI `StoryboardGenNode` 使用 `startCanvasRun` 并可先请求只读预览。 | 分镜设计师、视觉连续性专家 | `tests/freezone/orchestration/test_storyboard_contract.py`、`test_adapters.py::test_image_request_consumes_upstream_references_and_text`、`tests/freezone/orchestration/test_planning.py`。 |
| `videoNode` | `generate`、`image_to_video`、`keyframes`、`reference`、`edit`、`upscale`、`erase`、`extract_frames`、`separate_audio` 均编排可用 | `ROUTES` 分别映射到 `freezone_video_gen`、`freezone_video_i2v`、`freezone_video_keyframes`、`freezone_video_omni_gen`、`freezone_video_edit`、`freezone_video_upscale`、`freezone_video_erase`、`freezone_extract_frames`、`freezone_audio_separate`。UI 的 `VideoNode` / `VideoProcessingPanel` 还有直接任务 API 与本地生成状态路径。 | 视频生成专家、镜头连续性/后期专家 | `test_video_request_uses_actual_schema_duration_and_references`、`test_freezone_video_*` runner/backend tests、`tests/contract/test_m06_route_contracts.py`。上述测试不代表 9 项都经 NodeAdapter 端到端覆盖。 |
| `videoDirectorNode` | `generate` 编排可用 | `NodeAdapter.submit` → `freezone_video_director.create_attempt`；完成查询走 `get_attempt`，由独立 Video Director 服务/store 持有供应商任务。若连了 storyboard 节点，执行前会检查输入指纹并调用 `prepare_storyboard` 冻结导演草案。 | 视频导演、镜头设计专家 | `tests/freezone/video_director/test_service.py`、`test_api.py`、`test_end_to_end_contract.py`；adapter 的 draft 转换由 `test_director_draft_converts_browser_shape_without_losing_frames` 覆盖。 |
| `audioNode` | `speech`、`music` 编排可用 | 分别进 `freezone_audio_speech`、`freezone_audio_eleven_music`，沿用 task backend / 音频计量。 | 配音导演、声音设计师 | `tests/contract/test_m06_route_contracts.py`、`tests/test_freezone_audio_backend.py`；未见两项均通过 NodeAdapter 端到端测试。 |
| `videoStoryNode` | `analyze` 编排可用 | `freezone_analyze_video_story`，用 `freezone_job_result` 查询任务；属于视频内容分析。 | 视频分析、连续性审查专家 | `tests/test_freezone_video_story_backend.py` 与 route contract。 |
| `videoComposeNode` | `compose` 编排可用 | `adapters.requests::_compose` 要求明确 timeline，按所声明源顺序转为 `freezone_video_compose`。结果投影还会创建稳定 ID 的成片 `videoNode`。 | 剪辑师、声音后期 | `test_compose_requires_explicit_order_not_upstream_completion_order`；`tests/music/test_end_to_end.py` 是另一条音乐桌端到端路径。 |
| `musicDeskNode` | `edit`、`export` 均未接入 | 前端 `nodes/MusicDeskNode.tsx` / `music/MusicDeskModal.tsx` 经 `music/api.ts` 使用独立音乐计划、任务和导出服务；能力目录操作名没有进入 `available_operations()`。 | 配乐编辑、混音/后期专家 | `tests/music/test_end_to_end.py`、`tests/music/test_api.py` 覆盖独立音乐服务，不覆盖 orchestration adapter。 |
| `scriptNode` | `generate`、`plan_episode` 编排可用 | `generate` → `freezone_story_script_generate`；`plan_episode` → `freezone_canvas_plan` task，由 `NodeAdapter.reconcile` 取结果。计划专用路由 `plan_episode`、`get_plan`、`apply_plan` 另有 run 查询和画布应用。 | 编剧、剧本医生；整集规划须配导演专家审查 | `tests/freezone/orchestration/test_planning.py`、`test_adapters.py::test_cancelled_planner_releases_canvas_instead_of_running_forever`、`tests/contract/test_m06_route_contracts.py`。 |
| `pano360ViewerNode` | `view`、`capture` 均未接入 | `Pano360ViewerNode` 本地渲染视口并可 `captureFrame` / `captureToGroup`；截图上传后通过画布状态新增素材节点，不进入 NodeAdapter run。 | 全景场景设计、环境概念专家 | 未见 orchestration 操作测试。 |
| `threeDWorldNode` | `generate` 编排可用；`view`、`capture` 未接入 | `generate` → `freezone_image_to_3gs`；view/capture 在 `ThreeDWorldNode` viewer / capture UI 内，部分截图走 Freezone 3D viewer screenshot API 并写入画布素材。 | 3D 场景/空间设计师 | `test_adapters.py::test_world_request_uses_public_source_url_field`；viewer/capture 无 orchestration adapter 测试。 |
| `skillNode` | `run` 编排可用 | `NodeAdapter.submit` → `freezone_skill_run`，取上游输入并以 attempt ID 做幂等；查询走 `freezone_skill_run_result`。定义由 `freezone/skill_registry.py::list_skills` 动态展开。 | 与授权技能匹配的工作流专家；不能视为通用 shell/API Agent | `tests/test_freezone_skill_parameters.py`、`tests/test_render_runner_canvas_skill_inputs.py`；AgentTools 的限制由下节的 Creation 测试覆盖。 |

`available_operations()` 的 26 项 = 19 个 `ROUTES` + 4 个纯本地 validate + `videoDirectorNode.generate`、`skillNode.run`、`scriptNode.plan_episode`。剩余 9 项是：`uploadNode.upload`、`exportImageNode.export`、`storyboardNode.split`、`musicDeskNode.edit/export`、`pano360ViewerNode.view/capture`、`threeDWorldNode.view/capture`。这些声明仍保留在目录中，API 应将其显示为不可用；不能因为前端有同名面板或本地交互就当成可由 Agent 调用的后台节点操作。

## Agent、授权、本体和运行记录的真实接缝

1. **Agent 入口与当前工具范围。** `creation/delegations.py::OPERATIONS` 只允许 `context.read`、`proposal.create`、`ontology.definition.draft`、`media.propose`；`AgentTools._current` 同时核验委托仍有效、当前用户/会话/画布/节点范围和输入快照、硬约束未变，以及 Agent capability 与委托 allowed_operations 的交集。`AgentTools.dispatch` 对 `media.propose` 只保存方案并返回 `execution_enabled=false`。`tests/creation/test_agent_tools.py::test_real_handlers_create_only_candidates_and_atomic_replay` 验证不会改画布；`test_unknown_or_paid_operations_never_write` 把 `node.run` 列入拒绝操作。

2. **专家与版本。** 现有内置 Agent 是“通用主创”“文本编剧”（`creation/builtin_agents.py`），二者能力都属于内容/媒体提案，未声明 `NodeAdapter` 节点执行能力。Agent profile 的服务器能力清单和预览响应也标 `execution_enabled=false`（`creation/agent_profiles.py::capabilities/preview`）。Agent adoption 固定 `adoption_id + revision`；项目主 Agent 用 `project_main_agent_versions` 记录绑定修订；`Delegations` 固化合约和专家配置，`AgentRuns.start` 固化所选 specialist adoption 快照。这个版本机制适合作为未来专家授权主体，但当前 `allowed_operations` 仍是 Creation 动作词汇，不是 `(node_type, operation)`。

3. **人工画布与 Agent 的不同运行入口。** 前端 `useCanvasRun` / `orchestrationGateway.startCanvasRun` 接入 `RunStore`；目前明确用它的关键生成按钮是 `StoryboardGenNode`。多数既有节点 UI 仍由组件直接打 Freezone API 或记录 `generationTaskKey` 等旧字段，因而它们的手工提交/状态恢复不自动拥有同一个 RunStore attempt。Creation 的 Agent API 位于 `api/routes/creation_library.py`：用户明确启动后 `AgentRuns.start/drive` 调用受限文本模型、将 decision 与工具结果记入 `agent_runs`；写入 `AgentTools.outputs`，不是 `freezone/orchestration/runs.sqlite3`，也没有自动调用 `NodeAdapter` 或将结果投影到节点。

4. **通用执行协议的硬断点。** `creation/models.py::OperationRequest.kind` 包含 `node.run`、`result.adopt`、`proposal.confirm` 等；但 `creation/operations.py::Operations.apply` 对 `node.run` 明确抛 `capability_unavailable: 节点执行服务尚未接入`，其他未接的操作也走拒绝分支。`graph.patch` 和 `asset.bind` 有事务/投影 outbox，但这不是节点付费任务。不得把 enum、Agent prompt schema 或 UI 操作按钮当作执行已接通。

5. **任务、投影与计量。** 编排节点 adapter 会调用原 Freezone 路由，由原路由 enqueue 到 task backend，并把 task_type/job_id 等提交信息存为 RunStore attempt；恢复时用 job result 或特定 Director/Skill 查询器取已有记录。完成后 `verification.verify_media` 校验本项目路径、文件、SHA-256 和可解码性，`project_result` 只允许写入 `RESULT_FIELDS` 并将结果及 `{attempt_id, status}` 写回 canvas。RunStore schema 没有 cost/usage 字段；生成成本沿用 task/provider 的 usage meter / credit reservation 上下文（例如 `task_backend/run_core.py`、`freezone/audio_node.py`）。Creation `agent_runs` 会保存模型 usage，但公开 run 的 cost 仍是 `{status:'unknown', amount:null}`（`creation/agent_model.py`、`creation/agent_runs.py`）。目前缺少统一的 node ↔ task/run/attempt ↔ usage 读取投影，前端节点也无法仅凭 `orchestration.attempt_id` 打开完整账单/任务记录。

6. **本体/资产的版本关系。** Creation 已有 `ObjectStore` 不可变 `object_versions`、`Ontology` 的 `ontology_definition_versions` 与显式 `schema_revision` 关系，以及资产 `AssetRef(base_asset_id, variant_id, revision)` 和 `asset_binding` relation。Agent 可以 `definition.draft`，但通过 AgentTools 不可 `definition.confirm`、创建 ontology relation 或绑定资产；草稿 revision 不等于激活事实。编排 `project_result` 写媒体 URL 和 attempt 状态，不会自动创建本体对象/关系或引用资产版本。Agent 直接执行节点之前，必须有明确的授权关系来固定目标节点、输入资产版本、定义/关系 schema revision 和输出归属；现有版本系统是可复用底座，不是现成的执行连线。

## 叙事组生产约束与画布规划边界

`orchestration/planning.py::compile_plan` 能把完整剧本覆盖、scene/group line range、group duration、静态分镜契约编译成图：每个 narrative group 创建 `storyboardGenNode` → `videoDirectorNode`，最后汇入 `videoComposeNode`，并保留 `scene_id/group_id/ordinal/source_start_line/source_end_line`。`plan_episode` 做的是受校验的蓝图规划并等待用户/前端调用 `apply_plan`；它本身不等于 episode media production。

但后续普通 `POST /freezone/orchestration/runs` 对这个图会通过 `freezone_gen` 生成图片、通过独立 Director service 启动一个 Director attempt，再通过 `freezone_video_compose` 合成；这条 adapter map 没有调用 `novelvideo.production_cli` / `nuomi` 的 narrative-group 批处理，不负责原生产工作流的 group stage、复用现有合格产物、Ref 生产语义、版本快照、失败组重试策略或组间并发读取。规划图里存在 `group_id` 元数据不足以证明旧生产阶段约束已经满足。当前真实 episode production 继续遵循 `skills/nuomi-production/SKILL.md` 的现行规则。目标架构是在统一节点模型中复用共享生成、生产合同与恢复能力，并在迁移时同步调整受支持入口和生产说明、淘汰旧阶段引擎；迁移完成前，专业画布或 Agent 不能提前旁路现行规则，也不能以单镜头循环替代叙事组生产。

## 建议的 P0 接入顺序

1. 将 Agent 授权范围明确为节点 ID + 操作 + 固定输入版本 + 可用专家 adoption/revision；执行时服务端再次核验委托快照、节点输入指纹和项目角色。不要把目前 Creation 的四个 `allowed_operations` 直接复用为节点能力。
2. 让可信宿主把用户授权传到既有 RunStore/Scheduler/NodeAdapter 边界；所有专家节点调用沿用现有任务/计量入口。将 task/run/attempt/usage 关联投影到节点，恢复动作查询已有 attempt，不在 `submission_unknown` 时重新付费提交。
3. 将资产版本、本体对象版本、关系定义 revision、输入/输出的 provenance 固定进 run 快照；候选/草稿、用户确认、正式关系和媒体任务完成分状态表达。
4. 逐项为目前 9 个未接入操作确定执行边界：服务端执行 adapter、仅交互/本地变换，或不可由服务端执行。目录应持续保留完整操作及不可用原因，并明确区分交互专用操作与服务端执行操作；不通过隐藏不可用项来制造覆盖假象。
5. 单独评审 episode production adapter 与 `nuomi` 的 group workflow 兼容边界。在有受支持入口前，Canvas/Agent node map 只作为能力盘点和准备界面，不是生产工作流替代品。

## 验证证据与覆盖边界

静态解析 `capabilities.py::_SPECS` 得到 19 种节点、35 个操作；核对 `adapters.requests.ROUTES` 得到 19 条 endpoint route，再与 `adapters.__init__::LOCAL` 及额外 Director / Skill / plan 分支交叉得到 26 个编排可用操作、9 个未接入操作。对照 `frontend/src/features/canvas/domain/canvasNodes.ts`、`nodeRegistry.ts` 与 `nodes/index.ts`，确认 19 个前端类型齐全，并抽查节点组件真实 UI 入口。逐项核读 NodeAdapter 的 submit/reconcile/verify/project、Scheduler、RunStore、project_result、Creation Agent、Operations、Ontology 和生产技能规则。

本报告没有运行 pytest，因为没有实现行为变化；可直接复核的既有证据集中在 `tests/freezone/orchestration/test_graph.py`、`test_api.py`、`test_adapters.py`、`test_scheduler.py`、`test_planning.py`、`tests/creation/test_agent_tools.py`、`test_agent_runs.py` 和独立 `tests/music/` / Freezone route contract 测试。已有测试验证通用编排骨架与若干请求翻译，不构成 35 个操作逐项的端到端覆盖；状态矩阵将“有专门测试”与“仅有目录/通用测试”分开说明。
