# E01 · H3 整集视频最终 prompt（v28）

本目录按组/批次导出 9 个 H3 批次（覆盖 14 个镜头）的最终 wire prompt、片段时长、run id、video URL 与自检结果。

## 覆盖与总览

| 批次 | 组 | 镜头 | 请求时长 | 实际时长/帧 | run id | 状态 | 手法 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| g1b1 | g1 | 镜头1-2 | 11.000s | 11.750s / 282f | `635b28fa46b84a4cac46827e1b109087` | completed | 无（compatible_technique_ids 为空） |
| g1b2 | g1 | 镜头3-4 | 10.000s | 10.333s / 248f | `fece3027768a452892bad659d1ed6455` | completed | 无（compatible_technique_ids 为空） |
| g1b3 | g1 | 镜头5 | 4.000s | 4.458s / 107f | `51e9d1c8f4b44c6099f4f8954dceda97` | completed | 无（compatible_technique_ids 为空） |
| g2b1 | g2 | 镜头6 | 9.000s | 9.417s / 226f | `e2e3b8c6815240d69ae01e95dbaca2dc` | completed | 无（compatible_technique_ids 为空） |
| g2b2 | g2 | 镜头7 | 8.000s | 8.000s / 192f | `1c35c2c9666d4a4b831066b084b93918` | completed | 无（compatible_technique_ids 为空） |
| g2b3 | g2 | 镜头8-9 | 13.000s | 13.875s / 333f | `8b5ee2a292f6484ab336297a95bee4c5` | completed | 无（compatible_technique_ids 为空） |
| g3b1 | g3 | 镜头10-11 | 14.000s | 14.583s / 350f | `a15a0c16fa194b0c995b679a9b1b1c1d` | completed | 无（compatible_technique_ids 为空） |
| g3b2 | g3 | 镜头12-13 | 14.000s | 14.583s / 350f | `9f6223e1ab9647d48a563512e6cd3be0` | completed | 无（compatible_technique_ids 为空） |
| g3b3 | g3 | 镜头14 | 7.000s | 7.292s / 175f | `5d14f21c70264219986bb66851b7fe38` | completed | two-person-gaze@1.0.0（检索采用；提交快照未绑定，见备注） |

- 批次：9/9 completed；镜头覆盖：14/14（g1 5 / g2 4 / g3 5）。
- 实际总时长：**94.292s**（源请求总时长 90s，按 H3 合法 17k+5 帧对齐后变长）。
- 分辨率：全部 1920×1088 / 24fps。
- 并发：wave1 g1b1/g1b2/g1b3/g2b1/g2b2（5 批）；wave2 g2b3/g3b1/g3b2/g3b3（4 批）；每批只提交 1 次，无付费重试。

## 批次文件

- [`g-1-b1.md`](g-1-b1.md) — g1 批次1（镜头1-2）
- [`g-1-b2.md`](g-1-b2.md) — g1 批次2（镜头3-4）
- [`g-1-b3.md`](g-1-b3.md) — g1 批次3（镜头5）
- [`g-2-b1.md`](g-2-b1.md) — g2 批次1（镜头6）
- [`g-2-b2.md`](g-2-b2.md) — g2 批次2（镜头7）
- [`g-2-b3.md`](g-2-b3.md) — g2 批次3（镜头8-9）
- [`g-3-b1.md`](g-3-b1.md) — g3 批次1（镜头10-11）
- [`g-3-b2.md`](g-3-b2.md) — g3 批次2（镜头12-13）
- [`g-3-b3.md`](g-3-b3.md) — g3 批次3（镜头14）

## 关键说明

- **g1-b1 已重做**：旧视频 `eb003c16…` 的 first frame 裁图来自旧宫格 `2bde4520…`；本次用 `prepare-storyboard` 从当前宫格 `0d802d00…` 重新切格并重编，run id `635b28fa…`，已通过 v28 自检，画布节点已指向新视频。旧视频文件仍保留，可回退。
- **g3-b3 手法绑定缺口**：`experience search` 返回 compatible `expression-build` / `two-person-gaze`，prompt 已采用 `two-person-gaze` 的调度思路，但提交快照的 `segment.technique` / `case_reference` 未写入。视频与 prompt 不受影响；provenance 已在 `g-3-b3.md` 记录。
- 所有批次均使用 `canvas director prepare`（只读两次 preview）通过服务端编译；提交前本地自检六段式、英文、`[Shot 1]`、`(S1)`、`[reference generation]`、retention、350–500 词，全部 PASS。
- 感染者群演特征写入每个含感染者的 prompt：苍白皮肤、失焦眼神、迟缓步伐、无血色；无血腥、无烂眼、无伤口特写。单人/双人镜头显式约束无额外人物。

## 内容核对与已知缺口

已完成 9 批全部回读，逐批 ffprobe 校验：全部 1920×1088 / 24fps，视频时长与对齐帧数一致，均含 AAC 双声道音轨。

1. **14/14 镜头覆盖，无缺镜**：g1 5 镜（b1 1-2、b2 3-4、b3 5）、g2 4 镜（b1 6、b2 7、b3 8-9）、g3 5 镜（b1 10-11、b2 12-13、b3 14）。
2. **g3 角色身份上游缺口**：g3 宫格把车窗内持地图者画成男性，与许岑资产（女性短发）不符；g3b1/b2/b3 沿用宫格首帧，导致持图/发问者性别与资产不一致。需重绘 g3 宫格或单独重跑 g3 批次才能修复，本次未重跑。
3. **g3b3 手法绑定缺口**：compatible 手法 `two-person-gaze` 的调度思路已体现在 prompt，但提交快照未写入 `technique` / `case_reference`；视频与 prompt 不受影响，provenance 已记录。
4. **g1b1 重做说明**：旧视频 `eb003c16…` 首帧来自旧宫格，本次已按当前宫格重做；画布节点现指向新视频 `635b28fa…`。若需保留旧版，可回退节点 URL。
