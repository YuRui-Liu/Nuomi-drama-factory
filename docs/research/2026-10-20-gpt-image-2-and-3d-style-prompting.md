# GPT Image 2 与 3D 国漫出图提示词调研（2026-10-20）

## 结论先行

1. **GPT Image 2 吃结构，不吃形容词**：实践指南要求 Scene / Subject / Important details / Use case / Constraints 五段式；“stunning、cinematic、8K”这类词会把分布拉散。
2. **风格要有“视觉目标”**：不能只写“3D 国漫风格”，要写可观察事实——风格化角色建模、几何体块、哑光无毛孔皮肤、成组发束、简洁褶皱色块、柔和全局光、冷灰青、非写实摄影。
3. **negative 不可靠**：GPT Image 2 没有真正的 negative prompt；把“不要写实”改写成正向事实（“风格化 3D 动画渲染、动画材质”）比列禁止项更有效。
4. **参考图会压过文字**：4 张写实/半写实资产参考在场时，模型优先继承参考的质感；单靠 prompt 很难把整体拉回 3D 国漫。
5. **Higgsfield 的 bake it into the asset**：会漂移的属性（风格、质感、光感）应当前移到资产/底图，而不是每次靠提示词追。风格应作为项目常量，最好由一张风格参考图或统一资产底图承担。

## GPT Image 2 提示词模板（fal / OpenAI cookbook 实践）

```
Scene: [地点、时间、背景、环境]
Subject: [主体与画面任务]
Important details: [材质、服装、光线、机位、构图、风格的可观察事实]
Use case: [3D 动画电影分镜联系表 / 电影制作预览图]
Constraints: [不漂移项：角色身份、格位、无水印无文字]
```

要点：分段换行；每段只放一类信息；风格写成“视觉事实”；约束写“必须保留什么”，少写“不要什么”。

## 本次实测

- 第 3 次：无风格 → 写实电影感；
- 第 4 次：加一句风格 + 4 张写实参考 → 仍写实；
- 第 5 次：按五段式、风格前置、Use case 写“3D 动画电影分镜”、约束写明非真人 → 仍偏写实，只略有风格化。

**判断**：在当前 gpt-image-2（newapi → grsai）路由下，文字无法压过写实参考图；继续加词只会烧钱。

## 可行方案（按成本排序）

1. **加一张风格参考图**：选一张已认可的 3D 国漫画面作 @image5，角色声明“只取渲染风格、材质与色彩，不取构图”；这是 bake it into the asset 的最小实现。
2. **统一资产底图**：把角色/场景资产重出一版 3D 国漫质感（保持身份），之后再出宫格；一致性最稳，成本最高。
3. **换模型/供应商**：选明确支持动漫/3D 风格或带 style reference 的图像模型；需先列可用模型与价格。
4. **接受写实版**：仅当项目允许写实分镜时可行。

## 来源

- fal.ai, GPT Image 2 Prompting Guide: https://fal.ai/learn/tools/prompting-gpt-image-2
- OpenAI Cookbook, Generate images with GPT Image: https://developers.openai.com/cookbook/examples/generate_images_with_gpt_image
- Higgsfield prompt skill (block scaffold, reference roles, bake it into the asset): https://github.com/OSideMedia/higgsfield-ai-prompt-skill/blob/main/skills/higgsfield-seedance/SKILL.md
- Hell Grind 生产手册: https://github.com/OSideMedia/higgsfield-ai-prompt-skill/blob/main/skills/higgsfield-seedance/HELL-GRIND.md
