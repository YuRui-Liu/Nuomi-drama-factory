# Release manifest contract

Use one temporary UTF-8 JSON file. The publisher rejects extra top-level keys.

```json
{
  "style": {
    "id": "drama_ext.celadon_shadow",
    "name": "青瓷影",
    "category": "chinese",
    "summary": "青瓷釉色与克制暗影构成的东方视觉语言。",
    "prompt_fragment": {
      "medium": ["celadon glaze texture", "delicate mineral pigment"],
      "rendering": ["restrained contour rhythm", "subtle crackle detail"],
      "lighting": ["soft directional shadow", "quiet tonal separation"],
      "color": ["celadon green", "warm ivory", "charcoal accents"],
      "camera": ["balanced negative space", "layered planar depth"],
      "constraints": ["no readable text", "no logo", "story-neutral styling"]
    },
    "use_cases": ["东方悬疑", "含蓄叙事"],
    "preview_asset": "/images/extension-styles/celadon-shadow.webp",
    "source": {
      "repository": "freestylefly/awesome-gpt-image-2",
      "source_ids": ["illustration-art-style", "history-classical-themes"],
      "license_review": "approved",
      "imported_revision": "3a9c63baa03e6bbe2f28c89a2654cf9845466646"
    },
    "version": "1.0.0"
  },
  "preview_prompt": "Create an original 16:9 visual style study with celadon glaze texture, restrained mineral-pigment contours, quiet directional shadows, celadon green, warm ivory and charcoal accents, balanced negative space, no text, no logo, no watermark, no imitation of an existing artwork or artist."
}
```

## Style rules

- `id`: lowercase `drama_ext.*`; use snake_case after the prefix.
- `category`: one of `2d`, `3d`, `realistic`, `chinese`, `experimental`.
- `prompt_fragment`: exactly `medium`, `rendering`, `lighting`, `color`, `camera`, `constraints`. Values are arrays of reusable phrases.
- Keep fragments story-neutral. Do not name characters, dynasties, costumes, props, locations, plot actions, outcomes, or required on-screen words.
- `use_cases` describe compatible genres or production needs, not a fixed story.
- `preview_asset`: derive the ID suffix as kebab-case under `/images/extension-styles/`; the example becomes `celadon-shadow.webp`.
- `source`: copy `repository` and `imported_revision` from `src/novelvideo/extension_styles/source_audit.json`; choose one or more `source_ids` listed in its `templates`; keep `license_review` as `approved`.
- `version`: begin at `1.0.0` for a new ID.

## Preview prompt rules

The preview is evidence of a visual system, not a story frame. Demonstrate medium, rendering, light, palette, composition, and production constraints. Request a clean 16:9 composition with no readable text, logo, watermark, protected character, brand, or imitation of a named artist or artwork.

The generated source may be PNG, JPEG, or WebP. `apply` center-crops it and writes a 640×360 RGB WebP at quality 88.

## Commands and outcomes

```bash
.venv/bin/python skills/publishing-nuomi-extension-styles/scripts/publish_style.py \
  prepare --manifest /tmp/release.json --plan /tmp/release-plan.json

shasum -a 256 /tmp/release-plan.json

.venv/bin/python skills/publishing-nuomi-extension-styles/scripts/publish_style.py \
  apply --plan /tmp/release-plan.json \
  --approved-plan-sha256 <sha256-shown-to-user> \
  --preview /absolute/generated-preview.png
```

`prepare` writes only the requested plan. The plan records schema version, normalized style, prompt, catalog and frontend snapshot SHA256 values, and the three fixed targets. Present the plan SHA256 to the user; `apply` requires that exact approved digest. It rejects changed catalogs or snapshots, builds all outputs in memory, and restores already replaced targets when a later replacement fails.

- Exit `0`: success.
- Exit `2`: invalid input, source, image, target, or write/rollback failure.
- Exit `3`: catalog SHA256 changed since prepare.
