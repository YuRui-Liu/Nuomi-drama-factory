# Production stage contracts

Use these contracts to move a Nuomi episode from source material to a reviewed, deliverable cut. Each stage consumes saved project state, writes named artifacts, updates that stage's progress record, and checks the saved result before downstream work. Use the project CLI (`nuomi --project PROJECT ...`); in a checkout without the `nuomi` entry point, use `.venv/bin/python -m novelvideo.production_cli --project PROJECT ...`.

## Start with the episode policy

Configure the episode before reading or changing its canvas. The default is the three existing human review points. Do not add another approval mechanism.

```bash
nuomi --project PROJECT canvas progress configure CANVAS EPISODE_ID --mode review
nuomi --project PROJECT canvas progress get CANVAS EPISODE_ID
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas director capabilities
```

`--mode auto` skips the screenplay, assets, and storyboard checkpoints; `--stop-after STAGE` still halts after that stage, including in auto mode. Clear a saved stop only when asked to continue: `canvas progress configure CANVAS EPISODE_ID --clear-stop-after`. Re-read saved policy on resume. All canvas mutations use the returned current revision and a unique request ID; after any conflict, read current state and rebuild the intended patch.

## Idea and screenplay

**Inputs:** the user's premise or supplied script, named project files, project settings, existing episode continuity, exact dialogue, locked facts, and unresolved questions. Track source and confidence in a project-local note. Keep sourced facts, approved decisions, and proposed connective writing visibly separate. Before drafting or editing, identify the episode's existing source binding: `screenplayNode.data.documentRef` means project-local `ProductionDocuments`; `documentId`/`documentRevisionId` plus a confirmed handoff means formal `script_creation.DocumentStore`.

**Method:** first extract the protagonist's concrete want, obstacle, choice, consequence, episode turn, intended audience/tone, and facts that cannot change. For an idea, draft a beat outline and check cause → choice → consequence before writing scenes. For a complete supplied screenplay, preserve its text; any adaptation is separately labeled. Use the already-selected source store for edits. `canvas documents` is the project-local file API and uses a path/hash; `script documents` is the formal immutable revision API. Never copy a bound formal screenplay into a local project file as another editable source. If the binding is absent or ambiguous, do not create a second document or imply that a formal document is synchronized; resolve the intended handoff explicitly.

For a project-local source, read/write the existing file with its current hash and keep the screenplay node's `documentRef` on that same path:

```bash
nuomi --project PROJECT canvas documents read CANVAS episodes/003/script.md
nuomi --project PROJECT canvas documents write CANVAS episodes/003/script.md \
  --text-file source-or-draft.md --expected-hash DOCUMENT_HASH
nuomi --project PROJECT canvas patch CANVAS --body-file script-graph.json --request-id episode-003-script-1
nuomi --project PROJECT canvas get CANVAS
```

For a formal bound source, read its current revision. Apply a direct save or restore against that revision, or accept only the reviewed proposal against it; each write path returns synchronization and progress-invalidation results. Then inspect the current workspace:

```bash
nuomi --project PROJECT script documents get DOCUMENT_ID
nuomi --project PROJECT script proposals accept --json \
  '{"proposal_ids":["PROPOSAL_ID"],"base_revision_id":"CURRENT_DOCUMENT_REVISION","client_mutation_id":"episode-003-accept-1"}'
nuomi --project PROJECT request episodes/003/director-workspace
```

The shared formal-revision sync policy applies after `script documents save`, `script documents restore`, and proposal acceptance. It reuses only the latest confirmed handoff for the same document and episode, and only if Episode Source still matches that handoff's source revision and hash. If sync reports `requires_explicit_handoff`, `conflict`, `review_required`, or `error`, do not continue as though the current formal revision were the production source. A conflict preserves the saved DocumentStore revision and changed Episode Source; a review failure likewise leaves Episode Source unchanged. `director-workspace.script.artifact` is an immutable handoff snapshot with `path`, `sha256`, `source_revision`, and `matches_episode_source`; use it as the formal screenplay progress output only when `source_sync.status` is `synced`, artifact `status` is `ready`, and `matches_episode_source` is `true`. Never edit that receipt file. These write routes report progress invalidation when an applied episode canvas binds this exact document and episode; reread progress in that case and recompute affected downstream stages. Creating a formal document alone still does not establish an Episode Source binding.

Patch only core graph artifacts: an idea node and screenplay node (type `ideationNode` / `screenplayNode`), retaining the selected matching source field (`documentRef` for the local file, or formal `documentId`/`documentRevisionId`). The current `screenplayNode` operation allowlist is `plan`, `inspect`, `review`, and `rewrite`; read capabilities before choosing one. Do not invent a `generate_script` operation.

**Output and graph:** for a local source, record its current project-relative path and hash; for a formal source, record the immutable handoff receipt path/hash/source revision and the `matches_episode_source` check. Keep the idea and screenplay nodes and source/continuity note. Record progress stage `screenplay` with output label `script` or `screenplay`. Validate scene order, causality, speaker attribution, locked facts, episode ending, and the distinction between user-authored and proposed text. In review mode, complete the existing screenplay stage, show the saved source and current approval hash, and wait for the user to approve it before asset work.

## Character, location, and prop assets

**Inputs:** approved screenplay and story bible, established character identities, location facts, prop ownership/handedness/state, existing asset library and style settings.

**Method:** check reuse first. A character sheet should resolve identity, face, body, clothing, and any approved variants; use turnaround/reference views to make changes legible and keep design views clean. A location reference fixes only sourced anchors, materials, and spatial relationships. A prop brief states scale/use/state only when established. Keep one exact reusable visual descriptor for each approved design, but do not claim this wording alone guarantees image consistency.

The supported asset CLI accepts an existing brief reference and canvas ID. Read the saved brief before editing. Write a JSON brief through a file with its current hash, then validate it. `propose` records a reason, evidence, and reuse check; `admit` records a human-supplied decision and basis. Compiling does not perform image generation or human acceptance.

The brief's validated outer shape is `schema_version: 1`, `asset_kind: character|scene|prop`, `asset_id`, optional `variant_id`, source-linked `facts`, `design`, `consistency`, `render`, optional `pano`, and `admission`. Each fact has `field`, `value`, and `source: {episode, quote}`. For example:

```json
{
  "schema_version": 1,
  "asset_kind": "character",
  "asset_id": "lin-du",
  "variant_id": null,
  "facts": [{"field":"wardrobe","value":"深灰外套，左袖有补缀","source":{"episode":3,"quote":"他按住补缀的左袖"}}],
  "design": {"description":"成年男性，窄肩，短黑发；外套版型与衣料见本条定稿"},
  "consistency": {"anchors":["左袖补缀","窄肩"],"forbidden":["新增徽章"]},
  "render": {"material":"中性布料","volume":"正面、侧面、背面","negative":["不加入电影光效"]},
  "pano": null,
  "admission": {"confirmed":false,"confirmed_by":"","basis":""}
}
```

The model allows `facts` from the listed fact-field vocabulary (including age, gender, hair, face, body, wardrobe, carried item, location, era, weather, time of day, light, landmark, material, color, scale, state, and owner). Keep `design` and render values specific to the target asset and runtime compiler. Use the service's validation report rather than extending the schema with invented keys.

```bash
nuomi --project PROJECT canvas assets brief read CHARACTER_ASSET_REF CANVAS
nuomi --project PROJECT canvas assets brief write CHARACTER_ASSET_REF CANVAS --file character-brief.json --expected-hash BRIEF_HASH
nuomi --project PROJECT canvas assets validate CHARACTER_ASSET_REF CANVAS
nuomi --project PROJECT canvas assets propose CHARACTER_ASSET_REF CANVAS --name '林渡正侧背造型' --reason '剧本有多场跨机位复用' --evidence 'episodes/003/script.md: 场景 2, 4' --reuse-check '现有角色头像缺少背面衣装'
nuomi --project PROJECT canvas assets compile CHARACTER_ASSET_REF CANVAS --aspect 3:2 --resolution 1K
nuomi --project PROJECT canvas assets admit CHARACTER_ASSET_REF CANVAS --confirmed-by user --basis '用户指定' --decision accept
nuomi --project PROJECT canvas progress complete CANVAS 003 --stage assets --input-hash STAGE_INPUT_SHA256 --outputs-file assets-outputs.json
```

`canvas assets compile` supports `--style-fragment`, `--aspect`, `--resolution`, repeated `--document N=PATH`, and `--asset-exists`; use only relevant options. The brief schema is validated by the running service, so read an existing brief and current field definitions before writing one. The `admit --confirmed-by` flag is a record of an actual human decision, never a way for the agent to self-approve. Inspect each generated image and compare it with the locked identity, approved style, and reference purpose; replace a missed detail locally. An existing reference is not proof that a newly generated asset matches.

**360-degree scenes:** a scene reference can be sliced from an existing 2:1 equirectangular project image; this command does not create a 360 image.

```bash
nuomi --project PROJECT canvas assets pano slice SCENE_ASSET_REF CANVAS \
  --source assets/scene-360.png --yaw 0 --pitch 0 --fov 80 \
  --out assets/scene-anchor.png --width 1024 --height 768
```

Record exact project-relative asset paths, version/hash and intended reference use. Retain a reuse checklist even if no new image is needed. Complete the existing `assets` checkpoint with output label `assets` plus `reuse_checklist` or `checklist`; show references and decisions, then await the human approval hash in review mode.

## Storyboard table and grid

**Inputs:** approved screenplay and asset decisions, scene geography, intended audio/dialogue route, shot duration constraints, and style snapshot. Read [`storyboard-direction.md`](storyboard-direction.md) for blocking, axis, light, props, expression, and the still instant contract. Read [`visual-continuity.md`](visual-continuity.md) for reference bindings.

**Method:** make the table the editable shot authority. Give each row an ID, source beat, one clear shot action, verbatim dialogue or silence, duration, framing, intended movement, start/end state, subject positions/gaze, light, prop state, and linked visual references. Derive a static panel for a single selected instant; keep continuous action and audio in the table/timeline. Do not copy a paragraph of screenplay as every panel. A generated grid must use actual connected reference images with frozen version/hash and `referenceBindings`; text labels alone are not image references.

The canvas static panel contract includes `id`, `shot_id`, `anchor` (`start`, `selected`, or `end`), `state`, `framing`, `subjects` (including entity/appearance reference and per-subject position, pose, gaze), `setting`, `lighting`, and `visible_text`. The exact runtime model and saved project reference schema remain authoritative. The storyboard adapter rejects an absent `static_panel`, a mismatched `shot_id`, a stale `static_source_description`, duplicate reference bindings, unconnected images, and references without an asset version/hash.

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT canvas patch CANVAS --body-file storyboard-graph.json --request-id episode-003-storyboard-1
nuomi --project PROJECT canvas validate CANVAS
nuomi --project PROJECT canvas preview CANVAS --node STORYBOARD_NODE
nuomi --project PROJECT canvas run CANVAS --node STORYBOARD_NODE --operation render --request-id episode-003-grid-1
nuomi --project PROJECT canvas status RUN_ID
nuomi --project PROJECT canvas wait RUN_ID --timeout 3600
```

Run only the operation the current `storyboardGenNode` capabilities mark available. Review the preview's compiled static panel and actual reference order before generation. Afterward compare every cell to the table for identity, selected instant, action direction, axis, lighting, prop state, framing, and readable text. If the panel changes, update `static_panel` and `static_source_description` together and regenerate only affected images. Save both the table and grid under the project root; complete progress as `storyboard` with both `table` and `grid` output labels. Show both artifacts and await the existing checkpoint approval.

## Video, voice and sound, composition, delivery

Use the existing structured H3 director preparation, not a free-form provider call; see [`h3-experience.md`](h3-experience.md). Keep camera, framing, motion, light, identity references, timing, and dialogue track choices in the actual supported fields. Watch every result as a continuous clip against the screenplay and storyboard, then change the smallest causal input and rerun only the affected segment.

For dubbing and voice, follow [`voice-and-audio.md`](voice-and-audio.md). Keep an episode or group dialogue on one chosen source: independently generated dialogue plus available/desired ambience, or explicitly chosen H3-native video audio. Do not layer the original dialogue over the new take. For final editing and export, follow [`editing-and-delivery.md`](editing-and-delivery.md): validate current timeline order, each required audio source exactly once, subtitles, duration, picture/audio playback, and output file.

The progress records require existing output files under the project output root. Use stage `audio` with `audio`, `dubbing`, or `music`; stage `composition` with `composition` or `final`. Read and verify progress after every completion. A queued task or successful file export is not content approval or proof of review.
