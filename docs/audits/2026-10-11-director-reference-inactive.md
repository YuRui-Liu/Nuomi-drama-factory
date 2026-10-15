# 导演优化失败调查：b932693875c341c090236febd7e77529

## 已核实的证据

- 项目：01M4GJSK1MRSDN4MF73QTG8JB0；画布：fairytale-episode-2-production；节点：video-g1。
- 尝试记录的失败阶段是 optimizing，错误类型 KnowledgeRuntimeError，错误码 CODEX_STRUCTURED_OUTPUT_INVALID。
- 最内层 wire 校验代码为 reference_subject_inactive。
- definition_tags 是两行分别绑定 Subject 1/Picture 1、Subject 2/Picture 2；retention_tags 同样是 Subject 1、Subject 2。该记录没有显示人物定义数量或编号不匹配。
- 当前错误出现在视频供应商提交之前。任务页的媒体存储提示和创作方法快照缺失，没有证据表明是这次失败的触发原因。

## 确定性复现

直接调用 h3_wire.inspect_h3_reference_semantics，给定两个合法人物定义和 retention 条目：

| detailed_description | 校验结果 |
| --- | --- |
| 莉娅画外问，诺尔的手在雪地画马车。 | reference_subject_inactive |
| Subject 2 的手画马车，莉娅画外问（仅对前者使用规范标签） | reference_subject_inactive |
| 两个规范 Subject 标签，其中一个明确为画外、不入镜 | 通过 |

实现要求所有已定义主体的精确协议标签都出现在 detailed_description；人物自然语言姓名不足以通过。未知 Subject 也会触发相同代码。

## 证据边界

失败持久化记录没有保存本次 detailed_description 的 active_tags，也未定位失败 segment_id，因此还不能断言具体是哪个镜头、遗漏哪个标签，或是额外标签触发。不能把上述复现样例当成本次真实模型原文。

## 架构问题与建议讨论

当前优化器要求模型同时负责导演表达、冻结对白复写、主体编号和供应商协议格式；严格字符串校验依赖模型自由文本，一处不符即可丢弃整组优化结果。多次追加提示词并未消除该耦合。

建议把稳定身份与协议映射交给程序：采用明确的角色 ID、逐镜可见/画外状态和已冻结对白，编译为供应商标签；模型负责镜头动作和表达，不重新编号人物。禁止以增加人物入镜、自动改对白或关闭未知引用校验来绕过失败。

实施前需核对现有导演数据是否足够表达画外角色；不得凭姓名替换或补一句所有角色都存在来伪造结构。先增加脱敏的 segment_id、active_tags、missing_tags、unexpected_tags 诊断，再在同一冻结输入上验证。

## 验收

四个导演镜头保持四个视频片段，人物基础形象为 Ref，分镜裁图只作各段首帧/尾帧；手调保留；生成一个可播放的叙事组视频，并在原画布回显。本次调查没有达到视频生成验收，也没有新增供应商生成任务。

## 经确认后的实施

- 新增 ReferencePlan：AI 输出动作、人物稳定 ID 及 visible/offscreen/unused 状态，程序生成 Subject/Picture 映射和 retention，并原样拼接冻结对白。
- 编译器拒绝未知主体、顺序变更及模型自行输出的供应商标签；画外身份不会变为强制入镜。
- 本轮兼容范围是有明确 `Picture N 是姓名` 绑定且无 technique 卡片的 Ref 草稿。缺少身份绑定的旧草稿、技术卡片路径保留原校验，不猜测身份、不绕过卡片冲突检查。
- 增加失败 segment_id 与脱敏的 active/missing/unexpected tags 诊断。
- 导演后端测试 151 项通过，包含四镜顺序、画外角色、冻结对白及未知人物拒绝的回归。
- 对原失败任务发起受支持的 retry：d2938e1975674ecbbdc654ff3f85f0b5，冻结版本仍是 7、四段、两个人物 Ref。网页草稿已被手动改成版本 8；不覆盖该编辑，新任务产物须标明版本 7。
- 实际运行已完成全部四镜优化并提交供应商，任务 ID 为 2109235854781411330。第二镜明确是诺尔的手入镜、莉娅画外；三句冻结对白均由编译器加入。
- 实际执行为 h3_ref、四段、24 fps、394 帧，模型对齐后 16.4167 秒；四张分镜裁图分别为对应片段首帧。浏览器确认四段、图片加载成功、生成中且无 pageerror。
- 供应商仍在生成时不能判定成片通过；需补实际播放结果。

## 实机结果

- d2938e1975674ecbbdc654ff3f85f0b5 已 completed，原优化错误未复现。
- 产物：`freezone/_outputs/video_director/d2938e1975674ecbbdc654ff3f85f0b5.mp4`。ffprobe 确认视频 1280×736、16.416667 秒，含音轨。
- 原画布浏览器实际播放推进至 2.965932 秒，跳至末尾后 ended=true，未出现媒体错误；节点显示四段、已完成，并明确提示结果版本 7、当前编辑版本 8。
- 播放截图：`/private/tmp/e2-compiled-video-result.png`。抽样画面人物基础形象一致，包含画马车、孩子、名字等画面。
- 质量限制：抽样末尾出现模型自带中文字幕，与原文无字幕要求不一致；本次证明生成及回显链路恢复，不宣称最终成片质量全部通过，未擅自再付费重生。
