# Editing, sound mix, and delivery

Compose only after the saved shot table, generated video segments, and selected audio are current. The composition must reflect the story's continuity and include each intended source exactly once.

## Prepare the edit

**Inputs:** approved script, saved storyboard table/grid, current director timeline and segment manifests, approved video and image assets, selected voice takes, chosen dialogue source per span, intended ambience/effects/music, subtitle text/timing, aspect/resolution, and destination format.

**Method:** build an edit decision list before rendering. For every segment record its beat/shot ID, source path, in/out or actual duration, transition, audio source and level intent, subtitle range, and continuity handoff. Preserve the original story order. A dissolve or audio lead/lag is a narrative decision, not a default. Include music or effects only when in the screenplay or approved sound plan. Check that all voice and music tracks have exactly one intended path to the final mix.

Where the current graph groups work by scene or narrative group, keep reference-dependent video work together for continuity. After a first assembly, derive any pickup request from a concrete gap in the approved shot table/edit list and render only that approved shot. Do not add story beats to fill an edit. Finish content and continuity repairs first; only then do a global color-match pass and ambience continuity pass if the current editor exposes those controls. If it does not, record the intended finishing note without claiming the grade or sound pass was applied.

## Respect the selected audio source

- **H3-native:** the timeline declares `dialogue_source: "h3_native"`; keep the audio stream embedded in that generated video span. Do not place another take of the same lines over it.
- **Separate dialogue:** the timeline declares `dialogue_source: "external_tts"`; the timeline references `external_audio_path` and the manifest retains `ambience_stem_path`/status from the Demucs `no_vocals` split. The composer uses that ambience with the new dialogue and does not use the original mixed audio. Do not additionally attach the `original_audio_path` or `dialogue_stem_path` to the final mix.
- **Music/effects:** add each once at the approved layer and inspect dialogue intelligibility and headroom. A blank sound cue means no authored effect. Do not claim stereo/binaural behavior unless the selected renderer reports it.

For group spans, the public dialogue source endpoint carries `span_index`, `dialogue_source`, and current `revision`; it queues the existing recomposition path. For standalone canvas audio, use only the currently available `audioNode` operations from `canvas capabilities` (`music`, `prepare_dubbing`, `generate_dubbing`, `group_dubbing`, `audition`) and the available `musicDeskNode` / `videoComposeNode` / `episodeComposeNode` operations. Do not confuse the `canvas run` generic canvas composition with the narrative-group composition task; inspect the corresponding saved node and task result.

## Compose and inspect

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT canvas run CANVAS --node COMPOSITION_NODE --operation compose --request-id episode-003-compose-1
nuomi --project PROJECT canvas status COMPOSE_RUN_ID
nuomi --project PROJECT canvas wait COMPOSE_RUN_ID --timeout 3600
nuomi --project PROJECT canvas export COMPOSE_RUN_ID --output episode-003-review.mp4
```

The generic run example is valid only if that node/operation pair is marked available. A narrative-group recomposition is queued by its own existing group dialogue-source workflow; use the task receipt returned there. Do not use `compose` as a way to regenerate video or to bypass a stale group revision.

Play the exported file from beginning to end. Check shot order, missing/black frames, transitions, identity and prop continuity, actual runtime, audio start/end, dialogue intelligibility, repeated or clipped lines, retained ambience, music/effects balance, subtitles, and aspect ratio. Inspect the manifest and final file, not just the success status. Keep the source edit list, progress outputs, final path, and any delivery variant in the project output directory.

When mixing separate dialogue clips, inspect a dialogue window in the exported file as well as the full-file audio level. Sequential clips must not become quieter merely because more clips are present on the timeline. A mixer that normalizes by every input, including currently silent or delayed clips, can dilute dialogue; repair the public mixer and recompose from existing takes rather than regenerating voices or increasing every source blindly. Preserve intended source levels and use peak limiting without automatic gain.

## Delivery record and recovery

For the `composition` stage, complete progress with a current input fingerprint and an existing output file labeled `composition` or `final`. Read the saved progress result and verify its output hash; report exact duration, frame size, selected audio policy, and any unresolved issue. A task finishing or an MP4 existing does not approve content.

If one span fails, preserve successful segments and existing takes. Check whether the failure is in the current source revision, voice binding, TTS duration, ambience stem, render, or edit. Repair only the failed/invalid dependency and recompose from the current manifest. If `external_tts` requires ambience and the stored stem is missing or unusable, restore or rerun separation before rendering the mix; do not silently switch to the original mixed audio. If an output changes after `progress complete`, complete it again with new hashes and present the new approval hash before asking for review.
