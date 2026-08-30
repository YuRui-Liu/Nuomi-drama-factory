# Episode Import Windows Journal 修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 消除分集导入 journal 的无效二次覆盖，并让首次发布可恢复一次短暂 WinError 5。

**架构：** `EpisodeSourceStore` 仍使用同目录临时文件与 `os.replace` 原子发布 journal。发布函数仅对 Windows access denied 做有限重试；journal 创建后不再重写未被恢复协议消费的 phase。

**技术栈：** Python 3.11、asyncio、pytest、SQLite、Windows 文件系统

---

### 任务 1：修复 journal 发布

**文件：**
- 修改：`src/novelvideo/episode_source_store.py`
- 测试：`tests/test_episode_import_transaction.py`

- [ ] **步骤 1：编写失败的测试**

新增真实 `EpisodeSourceStore` 提交测试：包装 `os.replace`，首次发布 journal 时抛出带 `winerror=5` 的 `PermissionError`，第二次允许成功；journal 已存在后若再次覆盖则让测试失败。最终断言提交成功、journal 尝试次数为 2、没有 journal/备份残留。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
$env:COGNEE_LOG_FILE='false'; & '.\.venv\Scripts\python.exe' -m pytest tests\test_episode_import_transaction.py::test_episode_journal_recovers_one_access_denied_without_rewrite -q --basetemp '.pytest_tmp\episode-journal-red-20260830' -p no:cacheprovider
```

预期：FAIL，首次 `PermissionError` 从 `_replace_json` 直接抛出。

- [ ] **步骤 3：编写最少实现**

在 `EpisodeSourceStore` 中新增 journal 原子替换辅助逻辑：最多尝试 4 次，只捕获 `PermissionError` 且 `winerror == 5`，使用短递增等待；其他错误立即抛出。删除 `upsert_sources` 中 `_update_journal_phase("novel_replaced")` 调用及未使用方法。

- [ ] **步骤 4：运行核心验证**

运行定向测试及以下核心回归：

```powershell
$env:COGNEE_LOG_FILE='false'; & '.\.venv\Scripts\python.exe' -m pytest tests\test_episode_import_transaction.py tests\test_episode_source_store.py tests\test_task_episode_import_runner.py -q --basetemp '.pytest_tmp\episode-journal-green-20260830' -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 5：真机验收并提交**

重试 NamelessHush E002 导入，确认任务完成、`episode_sources` 出现第 2 集、journal 文件无残留。只暂存本任务两个代码/测试文件和本计划，提交：

```powershell
git commit -m "Hermes: 修复 Windows 分集导入 journal 冲突"
```
