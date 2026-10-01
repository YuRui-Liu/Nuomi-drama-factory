# H3 三源案例吸收实现计划

> 面向 AI 代理的工作者：使用 subagent-driven-development 逐任务实施，先规格审查再质量审查。用户已批准书面规格；直接使用当前分支，不新建 worktree。根代理负责实际数据吸收与集成，工作者不得提交其他任务变更。

**目标：** 三源全量案例索引、至少 24 张跨题材原创手法、分页案例浏览与画布选卡复用。

**架构：** 导入适配器读取固定上游提交，产生不含外部提示词全文的版本化本地案例包；归并保留多来源，发布失败保持旧包。查询 API 离线分页；手法关联案例但生成投影保持既有白名单。

**技术栈：** Python/Pydantic/FastAPI、JSON 数据包、React/TanStack Query、pytest/Vitest。

## 任务 1：导入器、数据契约与查询

文件：新建 `src/novelvideo/technique_library/{__init__,models,adapters,ingest,catalog,cli}.py`；数据位于 `src/novelvideo/technique_library/data/catalog.json`；修改 `src/novelvideo/api/routes/technique_library.py`；测试 `tests/technique_library/test_ingest.py`、`test_catalog.py`、`test_api.py`。

- [ ] 先测试来源 URL 规范化、同帖多媒体保留、跨仓库同媒体多来源、缺字段显式失败、提示词出处枚举、幂等、发布失败保持旧文件、API 分页和认证。
- [ ] 运行 `PYTHONPATH=src .venv/bin/python -m pytest tests/technique_library -q`，确认缺失实现导致失败。
- [ ] 实现来源适配、稳定 ID、保守去重及原子发布。数据契约：
  ```python
  # Case record: id, title, summary, use_cases, provenance,
  # sources[{repository, revision, path, upstream_id, url, author}],
  # media_url(optional source link), mode, duration, local_verification="unverified"
  # Bundle: schema_version, sources, report, cases
  # query_cases(q="", use_case="", provenance="", offset=0, limit=24)
  # -> {items, total, offset, limit}
  ```
- [ ] 维护入口 `python -m novelvideo.technique_library.cli --source-root PATH --output PATH` 从三个指定仓库的本地只读快照导入；清单固定 SHA。空源/解析异常不发布，报告异常条目。发布前验证唯一ID、安全HTTP(S)来源及完整计数。摘要使用原创字段组合，不截取外部全文作为摘要。
- [ ] API 为 `GET /api/v1/techniques/cases`，参数 q/use_case/provenance/offset/limit；`GET /api/v1/techniques/cases/{id}` 返回单条及关联手法 ID；沿用认证与 `{ok,data}`。不得动态抓取外部链接。
- [ ] 运行测试，自审并通过规格与质量审查。

## 任务 2：固定快照吸收与原创手法

文件：新建 `src/novelvideo/technique_library/data/{catalog,techniques}.json`、`src/novelvideo/technique_library/enrichment.py`；修改 `src/novelvideo/freezone/video_director/techniques.py`；测试 `tests/technique_library/test_published_catalog.py`；报告 `docs/superpowers/quality/2026-10-01-h3-case-absorption.md`。

- [ ] 获取规格内固定 SHA 的文本数据至临时目录，不下载案例媒体，不执行外部脚本；读取上游字段和代表案例以验证解析器。
- [ ] 测试发布包覆盖三源、报告计数守恒、至少 24 张不同手法、六用途各至少两张、来源/关联案例可解析、原 8 张版本和哈希不变。
  ```python
  assert len(list_techniques()) >= 24
  assert all(card.sources for card in list_techniques())
  assert "use_cases" not in project_technique(list_techniques()[0])
  ```
- [ ] 执行全量导入并核对逐源读取数量、错误、去重。由实际案例提示词证据提炼新增手法，不从未观察视频臆测摄影。每卡原创字段包含 intent/action_beats/performance/camera/ending_composition/avoid，来源标 analysis_only 并注明原文或反推依据。
- [ ] 卡片附加展示用 use_cases/case_ids；保留既有生成白名单、旧版定义及原 hash。新增题材与技术分类分离，限制来自本地能力和手法要求而非照搬案例参数。
- [ ] 写真实计数和采样证据报告，运行全部 `tests/technique_library` 和 `tests/freezone/video_director`，完成审查。

## 任务 3：案例浏览与跨题材筛选

文件：新增 `frontend/src/features/technique-library/{CaseBrowser,RelatedCases}.tsx`；修改现有 `TechniqueLibrary.tsx`、`TechniqueDetail.tsx`、`presentation.ts`、`frontend/src/api/{techniqueLibrary,videoDirector}.ts`；新增 `frontend/src/__tests__/features/canvas/technique-cases.test.tsx`，只添加本功能语言资源。

- [ ] 测试先红：全局切到案例、搜索/用途/出处分页参数、加载失败重试、从案例打开关联手法、片段只应用手法不应用案例全文、换条件重置分页、旧请求不覆盖新条件。
- [ ] API 类型与任务 1 保持一致，React Query key 包含完整筛选；不一次拉取全部案例。手法网格增加用途过滤，未知分类不默认为人物表演。
- [ ] 手法详情显示关联案例，案例仅显示来源链接/原创摘要/未实测状态。保留原有弹窗焦点、Esc、移动端返回、精确版本选择和不兼容禁用。
- [ ] 运行 `vitest run video-director technique-library technique-cases`、header 测试及 `tsc -p tsconfig.app.json --noEmit`，完成规格与质量审查。

## 任务 4：集成验收和提交

- [ ] 根代理独立运行后端两测试目录、前端目标测试、TypeScript、Vite build 与 bundle budget。
- [ ] 本地浏览器验证案例分页搜索、三源出处、六用途手法、案例到手法到片段的链路、窄屏和回焦；不触发付费生成。
- [ ] 最终审查全部差异；只提交本任务文件或自有 hunks，保留工作区其他开发。
- [ ] 更新本计划和导入报告，给出实际案例数/归并数/手法数、测试结果与未实测限制。
