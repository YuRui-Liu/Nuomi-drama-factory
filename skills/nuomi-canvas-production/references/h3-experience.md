# Experience retrieval, H3 preparation, and sound

## Search local experience when useful

Experience is optional. Search only when a scene has a specific directing problem such as an emotional reaction, a movement, a framing choice, or an ending pose. The catalog is local and read-only; search does not fetch repositories, execute example scripts, or submit media work.

```bash
nuomi --project PROJECT canvas experience search \
  --query '克制的惊讶反应' --mode ref_plus_first_last \
  --duration 6 --limit 5
```

The current input modes are `i2v`, `fl2v`, `ref_only`, `ref_plus_first`, and `ref_plus_first_last`; the wire routes are I2VA, FL2VA, or Ref2VA according to the current capability response. The route also accepts wire aliases `i2va`, `fl2va`, and `ref2va`. Do not infer support for T2VA or L2VA. Read `canvas director capabilities` before preparing a segment; use its current frame, duration, reference, and profile constraints.

For continuous long takes, prefer `fl2v` or `ref_plus_first_last` when supported: prepare and review both actual endpoint images, preserving identity, setting, camera continuity and reachable prop/action states. Do not default to first-frame-only video for these takes. Keep explicit cuts as separate storyboard shots; endpoint interpolation does not prove the intervening action is correct. Preserve the narrative-group submission/recovery workflow rather than introducing per-shot paid loops.

For actual endpoint image preparation, layout-aware extraction, and the public per-shot group binding API, read [video-keyframes.md](video-keyframes.md). Accepted pre-vis stays intact; bind each shot's own first and last image instead of treating an adjacent shot as its endpoint.

## Public MiniMax prompt guide: adapt its directing ideas, not its schema

The [official MiniMax H3 prompt-writing skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing) presents three core text sections for its base modes: `integrated_multimodal_description`, `overall_soundscape`, and `non_diegetic_music`. Its [base guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt) distinguishes a first-frame anchor that develops forward from a first-to-last-frame continuous path. For an explicit multi-shot text prompt, it asks for the first shot without a cut timestamp, later shots with strictly increasing cumulative cut times, and a timeline that fits the requested duration.

Its [full-reference guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt) uses six sections in order: `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Keep labels semantically distinct: `<Subject N>` names reusable visible identity/content; `<Picture N>` is an image used as a concrete keyframe or composition anchor. An image used only to define a character, scene, costume, or style belongs as a source within the matching subject definition rather than becoming a keyframe label. Keep each label's meaning stable through the sections.

These are official prompt-rewriting conventions, not a guarantee that the local adapter exposes those modes or fields. Nuomi compiles its structured director draft through the active profile; do not paste the six sections, add cut-time fields, assume English prompt output, or infer a new mode from this guide. Translate the directing intent into the saved shot/frame/reference/audio fields the current compiler accepts, then inspect `preview` and the compiled output. The local route currently exposes no T2VA or L2VA request mode; external guide examples do not change that.

Search results are concise and attributed. `applicability.requested_mode` and `requested_duration_seconds` describe the query. `applicability.source_metadata` reports the case catalog's own mode and duration and is not proof that the example itself matches the query. Only `compatible_technique_ids` contains active local techniques checked for the requested mode, aligned duration, and last-frame constraint. A technique that forbids a last frame is excluded from a `ref_plus_first_last` result even though both use the Ref2VA route.

Each result identifies its case and source IDs, links, and provenance, such as author-provided or reconstructed. Use a returned case ID to read one case's full details only when necessary:

```bash
nuomi --project PROJECT request techniques/cases/CASE_ID
```

Do not load every prompt into context. Do not paste a whole third-party prompt into the screenplay or H3 prompt. Extract a short, transferable directing idea and rewrite it for the current locked facts, shot, references, and duration. If the catalog is unavailable or no result fits, proceed without experience. A user's explicit technique selection that is absent, inactive, or incompatible must be reported as a conflict; do not silently replace it.

Record each applied case in a project-local production log with case ID, source ID/link, provenance, short adapted idea, and segment ID. In the segment draft, use the existing `case_reference` to bind the applied case and segment context, and `technique` for a curated card (`id` and `version`). Those fields do not replace the source attribution in the local log. Do not add a duplicate experience field or put a full copied prompt into `source_prompts`. The H3 draft fingerprint already includes both source fields. User constraints and facts outrank experience; changing a source or technique requires a fresh preview and prepared fingerprint.

## Use the shared H3 compiler

The supported path is structured draft → current capability/profile validation → preview and fingerprint → shared wire compilation → exact prepared preview → existing attempt queue. Never call a provider endpoint directly or send free-form text as a substitute for the structured director model.

1. Read the saved director node and current canvas revision. Build `draft.json` from the exact saved segment IDs, timing, frames, references, `technique`, and `case_reference`. A draft change invalidates prior preparation.
2. Run `canvas director capabilities` and `canvas director preview --body-file draft.json`. Resolve unsupported combinations or validation errors before creating a prepared object.
3. Prepare structured output under the active `OptimizedDirector` contract in `prepared.json`. Its top level carries the current revision, route (`h3` or `h3_ref`), profile ID/version, optimization timestamp, and segments. Each segment binds its ID, requested and aligned duration, frame count, wire object, and prompt. Do not invent wire fields: preserve the exact shared profile/compiler representation.
4. Run `canvas director prepare --body-file draft.json --prepared-file prepared.json`. This makes two read-only previews: first to fingerprint the current draft, then to validate and compile the prepared object. It returns the exact compiled preview; it does not create or edit `prepared.json`.
5. Submit only after the compiled preview matches the intended visual segments, reference identities, frame order, and timing. By default, keep dialogue out of video generation and pass it to a separate dubbing track; include dialogue in the video audio only when the user explicitly chooses the original-video-dialogue route. Keep sound cues on their selected track:

```bash
nuomi --project PROJECT canvas director submit CANVAS DIRECTOR_NODE \
  --body-file draft.json --prepared-file prepared.json \
  --request-id episode-003-director-1
```

The CLI revalidates before the request; the service freezes the current draft and checks the prepared fingerprint again before enqueuing on the existing attempt queue. Record and inspect its `attempt_id`. A stale profile or fingerprint requires rebuilding and preparing the draft; never force an old preview through.

The current supported combinations are image-to-video with a first frame, first-and-last-frame video, and reference-to-video with references alone, references plus a first frame, or references plus both first and last frames. This adapter rejects a last frame without a first frame. Frame combinations are checked independently from the route. For each segment, preserve user dialogue verbatim in the selected audio route: use a separate dubbing track with the character's voice profile by default, and include it in video audio only when the user explicitly selected that route. Bind references to the intended identity, respect last-frame constraints, and align duration to the returned frame count. Read all limits from runtime capabilities rather than copying an older mode or duration table.

## Add dubbing, music, and composition once

Inspect the prepared H3 output and generated video before adding audio. By default, create dialogue as a separate dubbing track using each character's voice profile, or reuse an existing external independent dubbing track when it matches the current dialogue. Keep dialogue out of video generation by default; mute or replace any dialogue embedded in the video to prevent doubled voices. Retain environmental sound when it is available and intended. Reuse dialogue embedded in the original video only when the user explicitly chooses that audio route. A segment with no dialogue needs no dubbing. Dubbing does not require lip-sync; add visible mouth motion only when the user requests it and the current route supports it. Check `canvas capabilities` for the current `audioNode` operations; use the relevant `prepare_dubbing`, `generate_dubbing`, or `group_dubbing` operation and audition the result. `audioNode` `music` is the music-generation operation. `musicDeskNode` `edit`/`export` and `videoComposeNode` or `episodeComposeNode` `compose` are usable only when capabilities report them available.

Before composing, compare the saved timeline to the storyboard: every intended shot appears once, in story order, with the correct duration. Add each intended independent dialogue, effects, and music track once. Mute or replace dialogue embedded in the video by default, while retaining environmental sound when available and intended; use embedded dialogue only when the user selected the original-video-audio route. Do not count embedded dialogue as a separate dubbing track. Check for missing clips, silent required dialogue, duplicate tracks, unintended gaps, and total duration. Preview or play the composition before recording the `audio` and `composition` stage outputs and completing their progress records.
