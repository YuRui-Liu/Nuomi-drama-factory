# 金石证痕全局扩展风格设计

## 背景与目标

Nuomi Drama Factory 已有 18 项 `drama_ext.*` 全局只读扩展风格，但现有的“水墨国漫”偏诗性扩散，“黑白悬疑漫画”偏日式黑白漫画，缺少同时满足以下条件的中式悬疑生产风格：

- 中式审美鲜明，但不依赖仙侠光效、宫廷堆砌或具体朝代符号；
- 适合古风悬疑中的人物、环境和证物特写；
- 轮廓、材质分层和光色锚点稳定，适合 MiniMax H3 的首帧/首尾帧视频链路；
- 保持故事中立，不覆盖剧情中的角色身份、服饰、场景、道具、动作或时代信息；
- 使用原创组合语言，不引用艺术家、影视作品或受版权保护的角色风格。

本设计新增第 19 项全局只读扩展风格 `drama_ext.jinshi_ink_suspense`，产品名为“金石证痕”。它进入现有扩展目录、StyleService、API、项目默认风格和画布节点，不建立新的风格系统。

## 非目标

- 不修改现有 18 项扩展风格的顺序、字段或提示词语义。
- 不修改 6 个内置 preset，也不把本风格加入 `styles/presets/*.json`。
- 不创建项目私有 `custom_styles` 记录。
- 不改变 StyleResolver、H3 导演编译器或扩展风格去重规则。
- 不在自动测试中发起付费的图片或 MiniMax H3 生成请求。

## 视觉定义

“金石证痕”使用半写实 2.5D 墨线动画、金石拓印肌理和矿物色薄染。视觉张力来自刻线、侧光、冷暖关系、稳定景深和留白，而不是高频纹理、夸张特效或符号堆砌。

色彩层级固定为：

1. 石青：环境主色和冷环境光；
2. 墨黑：轮廓、深影和视觉压重；
3. 灰白：材质、留白和明度分隔；
4. 朱砂：稀疏焦点强调，不作为大面积背景色。

该风格不得把“古代”“某朝代”“某类人物”“某种服装”或特定证物写入生成提示词。产品名称、摘要和用途可以帮助用户识别悬疑适用性，但真正注入生成链路的六段提示词必须保持故事中立。

## 目录契约

新增目录项使用现有 `ExtensionStyle` 契约：

```json
{
  "id": "drama_ext.jinshi_ink_suspense",
  "name": "金石证痕",
  "category": "chinese",
  "summary": "半写实金石墨线与矿物色薄染结合，以克制朱砂和冷暖侧光强化悬疑氛围、材质层次与焦点辨识度。",
  "prompt_fragment": {
    "medium": [
      "semi-realistic 2.5D Chinese ink-line animation",
      "stone-rubbing texture with restrained mineral-pigment wash"
    ],
    "rendering": [
      "precise engraved contours, stable facial planes, crisp material edges",
      "controlled layered brush texture with clean silhouettes"
    ],
    "lighting": [
      "low-key directional side light",
      "warm practical light against cool ambient shadow"
    ],
    "color": [
      "stone-cyan, charcoal, and ash-white palette",
      "sparse cinnabar focal accents with restrained saturation"
    ],
    "camera": [
      "clean focal hierarchy, stable layered depth",
      "restrained lens distortion, readable close-detail framing"
    ],
    "constraints": [
      "production-ready clarity, preserve identity and wardrobe/props/action/setting from base prompt",
      "no non-diegetic typography or watermark, no glossy plastic finish, no excessive fantasy glow"
    ]
  },
  "use_cases": [
    "古风悬疑漫剧",
    "证物与推理特写",
    "雨夜低照度场景"
  ],
  "preview_asset": "/images/extension-styles/jinshi-ink-suspense.webp",
  "source": {
    "repository": "freestylefly/awesome-gpt-image-2",
    "source_ids": [
      "illustration-art-style",
      "ink-double-exposure-poster",
      "character-design-sheet",
      "scene-storytelling",
      "history-classical-themes"
    ],
    "license_review": "approved",
    "imported_revision": "3a9c63baa03e6bbe2f28c89a2654cf9845466646"
  },
  "version": "1"
}
```

六个 fragment 数组都必须非空，并继续按照 `medium → rendering → lighting → color → camera → constraints` 的固定顺序编译。ID、分类、预览路径、来源许可和锁定修订遵循现有 schema，不扩展字段。

## 生成链路投影

目录项经现有 StyleResolver 形成不可变 StyleSnapshot：

- `director` 使用 `medium + camera + constraints`，为规划提供媒介、构图和质量边界；
- `image` 使用全部六段，保证首帧、尾帧和画布出图共享完整视觉定义；
- `video` 使用 `medium + rendering + lighting + color`，向 MiniMax H3 提供媒介、轮廓、纹理、光线和调色锚点；
- `panel_tag` 继续由每段首个非空短语确定性生成。

H3 的动作、镜头幅度、速度、首尾帧变化和对白节奏仍由现有 H3 导演计划与编译器负责。风格的 `camera` 和图片质量约束不进入 `video` 投影，避免与动态指令或 H3 提示词格式冲突。

“稳定 facial planes”“crisp material edges”“controlled layered brush texture”“restrained saturation”等措辞用于降低连续帧中的面部结构、物体边缘、纹理密度和颜色漂移风险。它们是生成约束，不承诺模型输出绝对无漂移。

## 接入与数据流

`src/novelvideo/extension_styles/catalog.json` 是唯一真源。新增记录后，使用已有同步脚本生成 `frontend/src/features/canvas/extension-styles/catalog.generated.json`。前端继续通过现有解析器验证并深度冻结目录，不新增手写 TypeScript 常量。

数据流保持不变：

```text
backend catalog
  -> ExtensionStyleRegistry / last-known-good snapshot
  -> StyleService / styles API / project default
  -> StyleResolver / immutable StyleSnapshot
  -> director, image, MiniMax H3 video projections

backend catalog
  -> deterministic sync script
  -> frontend generated catalog
  -> style drawer and canvas node selection
```

`drama_ext.*` 命名空间继续全局只读。项目可以把该 ID 保存为默认视觉风格，画布节点可以显式选择或覆盖它，但项目自定义风格不能创建、更新、删除或遮蔽该 ID。项目默认与节点选择相同时，沿用现有去重行为，不重复追加提示词。

## 预览资产

新增 `frontend/public/images/extension-styles/jinshi-ink-suspense.webp`，要求：

- 使用 GPT Image 根据本规格的原创视觉描述生成底图，再按仓库资产契约裁切、缩放并转换为 WebP；
- 原创画面，不复制上游案例图、影视画面或艺术家作品；
- 画布为 640 × 360，WebP，RGB 或 RGBA，文件大于 8 KB；
- 用抽象、非专属角色展示金石刻线、矿物薄染、冷暖侧光、石青/墨黑/灰白/朱砂层级和清晰材质边缘；
- 不出现品牌、Logo、水印或无法确认授权的字体内容；
- 不用仙侠粒子、霓虹、油亮塑料 3D 或大面积朱砂抢占视觉层级。

预览只证明风格方向和资产契约，不作为 MiniMax H3 输出质量的替代验收。
生成提示词、原始输出与归一化步骤不进入运行时产品链路；最终只提交满足契约的 WebP 资产。

## 来源与原创性审计

新条目沿用现有锁定的 MIT 上游模板方法与 revision。`source_ids` 只记录参与构思的模板方法；本地 prompt fragment 是面向 Nuomi 契约重新编写的故事中立表达，预览图也由本项目原创生成。

`source_audit.json` 中“18 extension styles”的描述更新为 19，同时保留仓库、不可变 URL、许可证和模板 JSON pointer。无需增加新上游来源或新许可证。

## 错误处理

- 新条目违反 schema、出现剧情内容偏置、缺少来源审批或预览路径不合法时，目录加载失败。
- 运行时热加载到非法目录时，ExtensionStyleRegistry 继续保留最后一个有效快照，并暴露现有降级诊断。
- 后端目录与前端生成快照不一致时，同步脚本的 `--check` 模式失败且不写文件。
- 缺失、尺寸错误、格式错误或过小的预览资产使预览契约测试失败。
- 未注册的 `drama_ext.*` 继续按非法风格处理，不回退为同名自定义风格。

不为本风格增加特例式回退或异常吞噬。

## 测试与验收

实现时执行以下聚焦验证：

1. 更新后端目录精确契约：目录共 19 项，新 ID 的名称、分类、来源模板和预览路径准确，原 18 项保持不变。
2. 更新 registry、API 和 StyleService 的数量断言：6 个内置 preset、19 个扩展风格；新风格可列出、查询和设为项目默认，且保持只读。
3. 增加新风格的 StyleSnapshot 回归：
   - `image` 含六段内容；
   - `video` 含 `medium/rendering/lighting/color`；
   - `video` 不含 `camera/constraints` 的专属短语；
   - 相同目录与版本得到稳定 hash。
4. 运行前端目录解析、抽屉和 prompt compose 测试，确认同步快照可消费且同 ID 不重复叠加。
5. 运行预览资产检查，确认 WebP 格式、640 × 360 尺寸、RGB/RGBA 和最小体积。
6. 运行来源审计与内置 preset 哈希保护，确认上游锁定记录有效且现有 preset 未改变。

建议的聚焦命令由后续实现计划给出。完成标准以自动契约测试通过和人工查看预览资产为准；不要求付费 H3 真实生成冒烟，因此不得宣称已通过真实模型画质验证。

## 预计修改文件

- `src/novelvideo/extension_styles/catalog.json`
- `src/novelvideo/extension_styles/source_audit.json`
- `frontend/src/features/canvas/extension-styles/catalog.generated.json`
- `frontend/public/images/extension-styles/jinshi-ink-suspense.webp`
- `tests/test_extension_style_catalog.py`
- `tests/test_extension_style_registry.py`
- `tests/test_extension_style_previews.py`
- `tests/test_api_styles.py`
- `tests/test_extension_style_service.py`（仅当现有精确数量或只读用例位于该文件）
- `tests/test_style_resolver.py`
- 必要的前端扩展目录测试文件（仅更新硬编码数量或新增该 ID 的针对性断言）

不修改上述范围之外的业务实现，除非实现阶段的失败测试证明现有契约无法承载该目录项；若出现这种情况，应先回到设计评审，而不是扩大实现范围。
