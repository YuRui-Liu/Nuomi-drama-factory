# 功能覆盖与证据

核对日期：2026-10-07。正文：manual.md。目标：N3s2d20I5oITKnxIZv7cp8MHnwc。

|范围|正文位置|界面证据|实现证据|
|---|---|---|---|
|项目/导航/状态|1、4.1|01|routes/_app/index.tsx；project-navigation-routes.ts|
|运行时/模型/并发/存储|2|20、21|components/settings/settings-dialog.tsx|
|快速制作流程|3|08、10、11、13|episode/narrative-workbench；episode/script|
|分集/小说/预检/历史/图谱|4|03|components/ingest；routes/ingest.tsx|
|设定/生成/改稿/版本/关联/交接|5|04|features/script-creation|
|角色/身份/声线|6.1—6.4|05、07|components/assets/character-delivery；character-voice-panel|
|场景/变体/全景/导演世界|6.5|06|scenes-panel；scene-asset-card|
|道具与引用|6.6|同资产中心页面结构；代码核对|props-panel；prop-asset-card|
|脚本校对/导演审核|7.1—7.3|11、12|screenplay-workbench；director-review|
|分镜/切分/参考|7.4|10|narrative-workbench|
|视频/Ref/模式/声音/重试|7.5—7.7|08、09|group-video-stage.tsx|
|合成/下载/SRT/ZIP|7.8、12.1|13|episodes/compose.lazy.tsx|
|画布/节点/连线/执行/恢复/提交|8|02、23|features/canvas；features/freezone|
|配乐台|8.7|未执行生成或修改素材；代码核对|canvas/music/MusicDeskModal.tsx|
|工作室/角色卡/团队/导演|9.1—9.4|14、25|features/studios|
|3D预演|9.5|15|studios/previs/previs-studio.tsx|
|片头|9.6|24|studios/intro/intro-studio.tsx|
|视觉风格|9.7|16|routes/styles.tsx|
|手法库/案例|9.8|22|features/technique-library|
|助手/通知/全局入口|10|19|features/superchat；layout|
|任务|11.1|17（空闲状态）|routes/tasks.tsx|
|费用/规则/订阅/补算|11.2—11.3|18|features/costs|
|交付与故障|12|实际视频及合成错误提示|上述制作组件条件|

截图是实际页面，非所有功能均已执行。费用与生产生成未作为手册验证任务触发。图示为原创操作说明，保留为 PNG。

发布核对（2026-10-07）：已写入目标飞书文档，最终写入版本 revision 27；各章节追加均返回 success，无 warnings。完整回读确认阅读说明与 12 章标题、30 个带资源引用的图片块（25 张截图、5 张示意图），末章内容完整。
