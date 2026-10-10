# 恢复与证据

首先 `canvas status RUN_ID`。不要在不知道旧任务状态时创建新画布或运行。

| 状态 | 操作 |
| --- | --- |
| pending/running | `wait`；网页或 CLI 退出不会取消运行 |
| failed | 核对最后尝试错误；修复原因后 `retry RUN_ID --failed --request-id RETRY_ID` |
| blocked，节点 verifying | 产物已生成但尚未落图；核对输入冲突，用 `resume` 只恢复验证/回写 |
| submission_unknown | 禁止盲目重试；核对 provider/task 记录，缺少对账证据就保留阻塞 |
| cancel_requested | 已停止下游提交；在途任务继续对账，不能当作供应商已取消 |
| cancelled | 明确继续制作时 `resume`，复用已成功产物；若返回 failed，修复原因后再 `retry --failed` |
| succeeded | 核对 artifacts 后导出、播放；仍需内容验收 |

当前运行冻结输入。修改画布输入不能偷偷替换旧运行输入；旧运行中的输入冲突必须显式处理。重复 apply/提交使用原幂等 ID。已完成节点不应重复生成；服务端只有回写失败时不得重新调用媒体生成。

证据至少记录 canvas_id、run_id、node_id、operation、尝试/任务 ID、实际 runtime/model、产物 hash 和媒体信息，以及网页可见/播放结果。无模型调用的容器、引用节点记录结构及被下游消费的证据，不制造 task ID。全景和三维还需浏览器真实加载/视角/捕获证据。

全节点验收须固定能力目录版本。每种公开操作和每个 skill ID 有独立结果；依赖缺失、供应商拒绝、未实现均为 blocked/failed。局部自动测试不能写为真机通过；复用旧产物必须与本次输入指纹和真实任务来源相符。
