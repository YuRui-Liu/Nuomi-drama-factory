# 剧本创作 CLI 实现计划

> 使用 subagent-driven-development 顺序实现并审查。用户已授权子代理和中间决策；本次是已确认工作台的 CLI 补齐。

**目标：** 在 `nuomi --project ID script` 暴露已存在的剧本创作 API，在当前 `fix/codex-identity-qc` 工作区实现。
**架构：** Typer 独立子模块，注入现有 HTTP 请求、JSON 读取、ID 校验和输出函数。复用认证、超时、错误、脱敏与 dry-run。API 继续负责授权、修订冲突、幂等和任务。兼容 `novelvideo production` 及 Python 模块入口。
**技术栈：** Python、Typer、httpx、pytest。

## 命令契约

- documents: list/create/get/save/revisions/restore/import。
- generations: list/start/get/retry/rebase/candidate/review。
- rewrites: create/get/list；proposals: list/accept/discard。
- checks: list/start/get/intentional/targets/rewrite-targets。
- entities: list/put/assets；handoffs: list/get/prepare/confirm/retry。
- 写操作以 `--json` 或 `--body-file` 接收 API 请求对象。调用者明确提供 mutation ID、基线修订和作用范围；不静默生成新重试键，不自动采纳或确认。
- 筛选参数通过 httpx params 编码，dry-run 单独显示 params；不能放宽路径防逃逸校验。无筛选的旧命令保持输出兼容。
- 一次命令只提交明确的一次 API 操作。已有 `wait TASK_ID` 跟踪后台结果。读失败和提交结果未知继续使用现有结构化错误。

## 文件职责

- 新增 src/novelvideo/script_creation_cli.py：七组具名命令和说明，不加载数据库或 API 服务模块。
- 修改 src/novelvideo/production_cli.py：注册子应用，HTTP 传输增加可选 params；不重构已有生产逻辑。
- 新增 tests/test_script_creation_cli.py：命令到实际 HTTP 请求映射、JSON 文件、dry-run、路径隔离、冲突和网络失败。
- 新增 docs/cookbook/creation/10-script-creation-cli.md：完整可运行命令、请求体示例及从文档到显式交接的顺序。
- 修改 skills/nuomi-production/SKILL.md：新增创作入口指引，区分文字草稿与媒体生产。

## 执行步骤

- [x] TDD：先通过 CliRunner 调用尚不存在的 script 命令，确认失败由命令缺失造成。
- [x] 实现独立命令模块、依赖注入注册、params 扩展；不得从子模块重新导入 __main__ CLI。
- [x] 用 httpx.MockTransport 验证全部具名路由及查询编码；验证 409、提交未知不自动重试、dry-run 不发网络、无隐式采纳或媒体调用。
- [x] 增加文档与技能入口；逐一核对示例请求字段与现有 API 模型。
- [x] 执行 `PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/test_script_creation_cli.py tests/test_production_cli.py -q`，再执行已有 script_creation API 回归。
- [x] 规格与质量审查，修复后在当前工作区保留原有改动完成提交，重跑 CLI 测试及 help/dry-run。

## 示例测试契约

```python
result = runner.invoke(app, ['--project', 'p1', '--dry-run', 'script', 'documents', 'list'])
assert result.exit_code == 0
assert json.loads(result.stdout)['path'] == '/projects/p1/script-creation/documents'
```

主工作区 production_cli.py 与生产技能已有其他任务的未提交改动。用户补充要求直接修改当前工作区：在 fix/codex-identity-qc（3365195）上实现；保留全部现有改动，只提交本任务新增文件和明确的 CLI 注册、查询参数及技能说明增量。

## 验收结果

直接在当前 fix/codex-identity-qc 工作区实现；34 个 API 操作均有 CLI 命令。规格、质量复审通过。主代理运行 CLI 与剧本 API 组合测试 220 项通过；仅加载本次拟提交 CLI 内容的新测试 49 项通过，确认不依赖已有未提交的声音与生产流程改动。真实 HTTP 文档列表读取通过；没有发送模型或媒体生成任务。CLI 和生产技能仅选择性提交本次增量，保留其他工作的未提交内容。
