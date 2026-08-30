# Episode Import Windows Journal 修复设计

## 问题

Windows 下执行 `episode_import` 时，事务 journal 首次创建后会被立即再次原子覆盖，仅用于把 `phase` 从 `prepared` 改成 `novel_replaced`。项目位于 Obsidian 目录，文件观察器可能短暂占用新文件，使第二次 `os.replace` 以 WinError 5 失败。恢复代码并不读取 `phase`，而是只根据数据库修订号决定恢复旧版或保留新版。

## 采用方案

1. 删除 journal 的第二次 phase 覆盖，保留首次 `prepared` journal、小说原子替换、SQLite 提交及提交后清理顺序。
2. journal 首次发布遇到 Windows WinError 5 时做短时、有限次数重试；其他异常及超过重试上限的权限错误原样抛出。
3. 不改变 journal 路径、备份校验、事务回滚和崩溃恢复协议。

## 测试

- 新增回归测试：模拟首次 journal 发布遭遇一次 WinError 5，随后恢复，提交应成功且不残留 journal/备份。
- 新增回归测试：模拟 journal 已创建后禁止再次覆盖，证明提交不再执行无效的第二次 journal 替换。
- 运行 episode import 事务、source store 与 task runner 相关回归。
- 使用 NamelessHush 的 E002 执行一次真实导入验收，不使用 mock。

## 成功标准

- E002 导入任务完成，项目修订号递增并出现第 2 集来源记录。
- journal、临时文件和备份在成功提交后全部清理。
- 持久权限错误仍明确失败，不被无限重试掩盖。
