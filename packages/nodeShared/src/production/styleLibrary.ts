export const styleLibrary = [
  {
    "id": "anime",
    "content": {
      "kind": "style",
      "name": "动漫风格",
      "positive": "Create a high-quality Japanese anime key visual illustration. Style: 2D cel animation with crisp black outlines (medium weight), flat color fills with 2-layer cel shading (base color + single shadow tone). Eyes: large, detailed with multi-layer reflections and catchlights. Hair: flowing strands with distinct highlight bands. Background: painted watercolor-style environment with softer detail than foreground characters. Color palette: vibrant and saturated with high contrast between light and shadow areas. Render at anime production key frame quality.",
      "avoid": "FORBIDDEN: photorealistic rendering, 3D CGI, photograph textures. NOT realistic skin pores or film grain. No gradient shading or soft blending — use FLAT cel-shading only. No watermarks, signatures, or text overlays. No bad anatomy, extra limbs, or mutated hands.",
      "source": "Nuomi styles/presets/anime.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "chinese_period_drama",
    "content": {
      "kind": "style",
      "name": "写实古装剧",
      "positive": "Create a live-action Chinese period drama image with grounded historical realism when the beat or scene context calls for a period setting. Use natural lighting appropriate to the time of day with soft realistic falloff and restrained contrast. Ensure realistic skin texture with visible pores, subtle imperfections, and no beauty-retouching. Use a natural 50mm cinematic lens feel with moderate depth of field, not glossy fashion photography. Follow the beat, scene, character, and prop descriptions for exact era, wardrobe, architecture, technology, and materials; do not override explicit modern, foreign, or traversal-story details. Keep color grading restrained and filmic, avoiding poster-like polish or painterly haze.",
      "avoid": "NOT anime, NOT cartoon, NOT illustration. No plastic skin or airbrushed texture. No AI artifacts or oversaturated HDR. No extra limbs, mutated hands, or deformed faces. No text, watermarks, or labels on image.",
      "source": "Nuomi styles/presets/chinese_period_drama.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "guoman_fantasy",
    "content": {
      "kind": "style",
      "name": "3D玄幻国漫",
      "positive": "Create a premium 3D Chinese fantasy animation image with next-generation realistic CG quality. Use high-precision PBR materials, cinematic lighting, refined 3D edge light, clean high-definition rendering, and polished Unreal Engine / Octane style finish. Blend xianxia fantasy, orthodox Dunhuang flying-apsera elegance, new-Chinese minimalist design, and dark fantasy atmosphere as the preset's default flavor. When character descriptions or reference images do not specify facial style, favor refined mature 3D Guoman character aesthetics with clear readable facial structure, elegant proportions, and polished high-end animated-drama modeling. When wardrobe is unspecified and the scene supports fantasy styling, favor lightweight improved Dunhuang-xianxia garments or high-status fantasy robes with layered silk, brocade, metal, jade, embroidery, and restrained black-gold or muted luxury accents. Always follow explicit character descriptions, identity reference images, scene era, wardrobe, nationality, regional appearance, expression, temperament, and prop descriptions over these default style flavors. Keep the image transparent, detailed, atmospheric, and production-quality for Chinese animated drama.",
      "avoid": "FORBIDDEN: live-action photography, Western comic style, flat 2D cel anime, chibi, childish face, influencer face, oily vulgar glamour, cheap web-novel cover look, low-poly game asset, plastic toy texture, wax figure appearance, over-smoothed skin, excessive HDR, messy ornament overload, deformed hands, extra limbs, broken anatomy, text, labels, watermarks.",
      "source": "Nuomi styles/presets/guoman_fantasy.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "post_apocalyptic",
    "content": {
      "kind": "style",
      "name": "写实末日风格",
      "positive": "Create in PHOTOREALISTIC live-action film style. REAL WORLD scene with REAL PEOPLE — NOT animation, NOT illustration, NOT CGI. When the beat or scene context calls for a post-apocalyptic setting, use desaturated muted tones, dusty grays and browns, harsh natural light, weathering, dirt, sweat, cracked concrete, rusted metal, peeling paint, overgrown vegetation, and practical survival detail. Follow the beat, scene, character, and prop descriptions for exact era, location, wardrobe, technology, and materials; do not override explicit non-apocalyptic, modern, ancient, foreign, or traversal-story details. Maintain gritty realism with documentary-like authenticity.",
      "avoid": "ABSOLUTELY FORBIDDEN: anime, manga, cartoon, illustrated, or painted styles. NOT CGI, NOT 3D animated. No smooth flawless skin — must show natural texture and weathering. No text, watermarks, or labels on image.",
      "source": "Nuomi styles/presets/post_apocalyptic.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "realistic",
    "content": {
      "kind": "style",
      "name": "写实现代",
      "positive": "Create a live-action image with grounded realism. Use natural lighting and restrained contrast, avoiding glossy fashion-editorial polish. Ensure realistic skin texture with visible pores, subtle imperfections, and no beauty-retouching. Use a natural 50mm cinematic lens feel with moderate depth of field. Keep the final image photographic and human, with subtle film grain and controlled color grading. Follow the beat, scene, character, and prop descriptions for exact era, wardrobe, architecture, technology, and materials.",
      "avoid": "FORBIDDEN: anime, cartoon, illustration, painting styles. NOT CGI or 3D rendered. No plastic skin, wax figure, or mannequin appearance. No AI artifacts, uncanny valley effects, or HDR overprocessing. No extra limbs, mutated hands, or deformed features. No text, watermarks, or labels on image.",
      "source": "Nuomi styles/presets/realistic.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "republican_era_drama",
    "content": {
      "kind": "style",
      "name": "民国年代剧",
      "positive": "Create a live-action Republican-era Chinese drama image with grounded historical realism and a 1920s to 1940s atmosphere when the beat or scene context calls for it. Use natural lighting appropriate to the time of day with soft realistic falloff and restrained contrast. Keep the image photographic and human, with realistic skin texture, visible pores, subtle imperfections, and no beauty-retouching. Use a natural 50mm cinematic lens feel with moderate depth of field. Favor vintage wardrobe, shikumen houses, old Shanghai streets, period interiors, wood, stone, fabric, paper, brass, and practical materials only when they fit the explicit scene. Follow the beat, scene, character, and prop descriptions for exact era, wardrobe, architecture, technology, and materials; do not override explicit modern, ancient, foreign, or traversal-story details. Keep color grading restrained and filmic with a warm neutral palette and subtle film grain.",
      "avoid": "Do not create anime, cartoon, or illustration styles. Never add plastic skin, airbrushed texture, or wax figure appearance. Ensure no AI artifacts, oversaturated colors, or HDR overprocessing. Do not include extra limbs, mutated hands, or deformed faces. No text, watermarks, or labels on image.",
      "source": "Nuomi styles/presets/republican_era_drama.json",
      "sourceVersion": "4b60b6b84b247efbab40bc7831912cc970a042ce"
    }
  },
  {
    "id": "drama_ext.japanese_cel_animation",
    "content": {
      "kind": "style",
      "name": "日系赛璐璐",
      "positive": "Japanese cel animation medium; crisp inked contours, clean cel shading; graphic key light with controlled rim highlights; bright harmonized palette, precise color blocks; animation-grade composition, clean lens separation; high-fidelity finish, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.shojo_manga",
    "content": {
      "kind": "style",
      "name": "少女漫画",
      "positive": "delicate shojo manga illustration; fine expressive linework, soft screentone rendering; diffused luminous glow, sparkling highlights; airy pastel palette, pearlescent accents; elegant close framing, shallow graphic depth; polished print quality, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.shonen_manga",
    "content": {
      "kind": "style",
      "name": "热血少年漫",
      "positive": "high-energy shonen manga artwork; bold ink contours, dynamic hatching, sharp cel rendering; hard directional light, punchy rim contrast; saturated primary palette, deep graphic shadows; kinetic perspective, dramatic wide-angle lens feel; crisp production detail, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.korean_webtoon",
    "content": {
      "kind": "style",
      "name": "韩式条漫",
      "positive": "premium Korean webtoon illustration; clean digital linework, smooth layered shading; soft cinematic key light, glossy highlights; refined contemporary palette, balanced saturation; vertical-canvas composition, cinematic depth cues; mobile-readable clarity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.western_superhero_comic",
    "content": {
      "kind": "style",
      "name": "欧美超级英雄漫画",
      "positive": "modern Western superhero comic art; heavy ink contours, sculpted anatomy shading, fine halftones; heroic high-contrast lighting, powerful rim light; bold comic-book palette, rich shadow colors; monumental low-angle framing, cinematic lens compression; premium print finish, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.american_retro_comic",
    "content": {
      "kind": "style",
      "name": "美式复古漫画",
      "positive": "vintage American comic print; Ben-Day dots, bold contour inks, subtle registration offset; flat graphic lighting, hard-edged shadow shapes; limited four-color print palette, aged ink tones; classic panel composition, graphic lens flattening; authentic print texture, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.french_picture_book",
    "content": {
      "kind": "style",
      "name": "法式绘本",
      "positive": "French editorial picture-book illustration; loose hand-drawn contours, tactile gouache texture; gentle ambient illumination, understated highlights; muted sophisticated palette, warm paper undertones; poetic editorial composition, softly layered depth; refined handmade finish, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.watercolor_storybook",
    "content": {
      "kind": "style",
      "name": "水彩故事书",
      "positive": "transparent watercolor storybook painting; wet-on-wet washes, delicate dry-brush detail; luminous paper-white glow, soft tonal transitions; transparent layered pigments, gentle color blooms; open storybook composition, atmospheric depth; clean watercolor craft, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.ink_wash_guoman",
    "content": {
      "kind": "style",
      "name": "水墨国漫",
      "positive": "Chinese ink-wash animation painting; expressive brush contours, layered ink diffusion; mist-soft illumination, restrained tonal glow; ink-led palette with sparse mineral accents; expansive negative-space composition, layered atmospheric depth; controlled brush clarity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, ink-double-exposure-poster, history-classical-themes",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.gongbi_heavy_color",
    "content": {
      "kind": "style",
      "name": "工笔重彩",
      "positive": "gongbi heavy-color painting; meticulous fine-line drawing, layered mineral-pigment rendering; even luminous illumination, subtle gilded highlights; saturated mineral palette, elegant tonal harmony; balanced scroll-like composition, precise planar depth; museum-grade detail, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, history-classical-themes",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.dunhuang_mural",
    "content": {
      "kind": "style",
      "name": "敦煌壁画国风",
      "positive": "Dunhuang-inspired mineral mural painting; decorative flowing linework, weathered plaster texture; matte ambient light, restrained golden radiance; earthy mineral pigments, oxidized jewel tones; frieze-like composition, flattened monumental depth; archival mural clarity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, history-classical-themes",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.guoman_2d_painterly",
    "content": {
      "kind": "style",
      "name": "2D 国漫厚涂",
      "positive": "premium 2D guoman painterly illustration; rich opaque brushwork, sculpted digital painting; cinematic volumetric key light, controlled rim glow; deep harmonized palette, nuanced color grading; cinematic composition, dimensional lens depth; production-ready detail, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.guoman_3d_animation",
    "content": {
      "kind": "style",
      "name": "3D 国漫动画",
      "positive": "premium 3D guoman animation render; refined stylized geometry, physically based materials; cinematic global illumination, polished rim highlights; rich cinematic grading, balanced material colors; feature-animation framing, realistic lens depth; high-end render fidelity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：3d-collectible-toy, character-design-sheet, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.clay_stop_motion",
    "content": {
      "kind": "style",
      "name": "黏土定格",
      "positive": "handcrafted clay stop-motion animation; tactile clay surfaces, subtle handcrafted imperfections; miniature studio lighting, soft practical falloff; warm tactile palette, matte material colors; macro miniature lens feel, stop-motion framing; coherent handcrafted detail, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：3d-collectible-toy, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.paper_cut_animation",
    "content": {
      "kind": "style",
      "name": "剪纸动画",
      "positive": "layered paper-cut animation; precise cut-paper silhouettes, visible fiber texture; backlit paper glow, crisp layered shadows; bold limited paper palette, strong color contrast; flat stage composition, layered parallax depth; clean cut-edge fidelity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, scene-storytelling, history-classical-themes",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.noir_suspense_manga",
    "content": {
      "kind": "style",
      "name": "黑白悬疑漫画",
      "positive": "black-and-white suspense manga; dense cross-hatching, sharp ink silhouettes, granular screentones; stark chiaroscuro, isolated hard highlights; pure monochrome values, crushed blacks; claustrophobic framing, tense oblique lens angles; precise tonal readability, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.cinematic_photorealism",
    "content": {
      "kind": "style",
      "name": "电影级写实",
      "positive": "cinematic photorealistic digital capture; physically accurate materials, natural micro-detail; motivated cinematic lighting, realistic dynamic range; restrained filmic color grade, natural tonal separation; cinema-lens depth, controlled optical falloff; photographic coherence, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：realistic-photography, scene-storytelling",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.vintage_film_realism",
    "content": {
      "kind": "style",
      "name": "复古胶片写实",
      "positive": "photorealistic analog film capture; fine organic grain, subtle emulsion texture; soft highlight halation, natural exposure rolloff; vintage film color response, gently faded blacks; classic cinema-lens character, subtle optical softness; authentic film fidelity, preserve identity and wardrobe/props/action/setting from base prompt",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：realistic-photography",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  },
  {
    "id": "drama_ext.jinshi_ink_suspense",
    "content": {
      "kind": "style",
      "name": "金石证痕",
      "positive": "semi-realistic 2.5D Chinese ink-line animation; stone-rubbing texture with restrained mineral-pigment wash; precise engraved contours, stable facial planes, crisp material edges; controlled layered brush texture with clean silhouettes; low-key directional side light; warm practical light against cool ambient shadow; stone-cyan, charcoal, and ash-white palette; sparse cinnabar focal accents with restrained saturation; clean focal hierarchy, stable layered depth; restrained lens distortion, readable close-detail framing; production-ready clarity, preserve identity and wardrobe/props/action/setting from base prompt; no non-diegetic typography or watermark, no glossy plastic finish, no excessive fantasy glow",
      "avoid": "避免画风漂移、人物身份变化、画面水印和损坏的解剖结构。",
      "source": "Nuomi extension_styles/catalog.json；freestylefly/awesome-gpt-image-2：illustration-art-style, ink-double-exposure-poster, character-design-sheet, scene-storytelling, history-classical-themes",
      "sourceVersion": "1 / 3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    }
  }
] as const;
