# Character voice, dubbing, and audio tracks

This module distinguishes an adopted character voice reference, per-line acting direction, independent dubbing, H3-native audio, and the separated ambience stem. These paths have different inputs and do not imply a general-purpose hosted voice-cloning API.

## Build a character voice design

**Inputs:** canonical character identity, age impression, vocal range/pitch, texture, resonance, articulation, pace, accent, stable emotional baseline, approved performance range, one short audition line, language, and any negative constraints. Keep identity facts separate from scene emotion and line performance.

For the structured canvas operation `characterVoiceNode.prepare_design`, save `data.voiceDesign` using this shape (all property names shown are current schema names):

```json
{
  "voice_spec": {
    "age_impression": "成年",
    "pitch": "中低音域",
    "texture": "温暖、略带颗粒感",
    "resonance": "胸腔共鸣适中",
    "articulation": "清楚但不过分播音",
    "pace": "中慢，句尾收住",
    "accent": "普通话，无明显地域口音",
    "emotional_baseline": "克制、可靠",
    "negative_constraints": ["不要播音腔", "不要夸张气声"]
  },
  "performance": {
    "intention": "试探后退",
    "emotion": "犹豫",
    "emphasis": "弱化句尾",
    "pauses": "转折前短停",
    "delivery_scale": "轻声但清晰"
  },
  "rationale": "身份音色稳定；犹豫只作为本句表演",
  "audition_text": "我还在想。",
  "audition_source": "script"
}
```

The public `CharacterVoiceDesign` contract rejects unknown fields. `voice_spec` includes `age_impression`, `pitch`, `texture`, `resonance`, `articulation`, `pace`, `accent`, `emotional_baseline`, `negative_constraints`; `performance` includes `intention`, `emotion`, `emphasis`, `pauses`, `delivery_scale`; the outer model includes `rationale`, `audition_text`, `audition_source`. `voiceDescriptionDraft` is a proposal; it is not accepted until the user/editor adopts the compiled design. Run `characterVoiceNode.prepare_design` to validate/compile locally. `design` queues a reviewable candidate and may incur a provider charge. It does not adopt a voice.

**Review and adopt:** use the real audition and candidate receipt. A candidate is not a production voice until the human listens and explicitly approves it. `voice approve` requires `--confirm-reviewed`, a real review reason, and server-side checks. Never claim the agent has heard audio unless it actually played it.

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas run CANVAS --node VOICE_NODE --operation prepare_design --request-id episode-003-voice-prepare-1
nuomi --project PROJECT canvas run CANVAS --node VOICE_NODE --operation design --request-id episode-003-voice-design-1
nuomi --project PROJECT canvas status VOICE_RUN_ID
nuomi --project PROJECT voice candidates CHARACTER_ID
nuomi --project PROJECT voice recheck CHARACTER_ID CANDIDATE_ID
# A real human has auditioned this exact candidate and decided to use it:
nuomi --project PROJECT voice approve CHARACTER_ID CANDIDATE_ID --confirm-reviewed --reason '人工试听：角色身份、咬字与剧本试听句符合定稿'
nuomi --project PROJECT voice preflight CHARACTER_ID
nuomi --project PROJECT voice preflight-episode 003
```

To bring an existing sample into Nuomi, use the supported import rather than imagining a separate voice-clone endpoint:

```bash
nuomi --project PROJECT voice import CHARACTER_ID assets/voice/lin-du-reference.wav \
  --kind dialogue --slot default --source '用户提供的录音，2026-10-25' \
  --rights '用户确认拥有此录音的配音使用权'
nuomi --project PROJECT voice candidates CHARACTER_ID
```

`--kind` is `dialogue` or `nonverbal`; `--slot` is `default` for dialogue (or the actual nonverbal purpose); `--source` and `--rights` must be nonempty. Import creates a candidate; it neither approves the candidate nor changes the production voice. After the human auditions the current candidate, explicitly approve it as above. Pin the resulting approved voice version ID on the connected character voice node as `data.voiceVersionId` (or its `voiceReference.version_id`) with the matching `characterId` and `identityId`. Dubbing requires one fixed adopted version per dialogue identity. Do not call this an independently trained voice model or claim a cloud clone endpoint.

## Per-line acting and separate dubbing

Keep each canonical dialogue line verbatim. Give only grounded direction for intention, emotional color, emphasis, pauses, and delivery intensity. Do not change the speaker or add words; do not bind emotion so strongly that it changes character identity. For `audioNode`, current operations are `prepare_dubbing`, `generate_dubbing`, `group_dubbing`, and `audition` when available in current capabilities. The separated preparation/generation contract uses fixed `voice_versions`, exact dialogue IDs, optional performance drafts, `preparation_id`, current `expected_revision`, and stable `request_id`.

An editable canvas performance draft is keyed by `<episode>:<groupId>:<dialogueId>` (or the supported `performance` mapping) and accepts only:

```json
{
  "mode": "emotion_text",
  "direction": "压住紧张，句尾不扬",
  "payload": "迟疑、轻声"
}
```

`mode` is `neutral` or `emotion_text`. `direction` is bounded text, `payload` is optional bounded text; neither accepts URLs. The active IndexTTS2 adapter supports text emotion through its `emotion_prompt`/`emotion_alpha` parameters. It rejects audio-emotion/vector modes on this path; verify capabilities before using another adapter.

The prepare operation snapshots script/group revisions, selected dialogue, pinned voice versions and performance, and returns `dubbingPreparation` plus validation issues. Resolve all missing speakers and stale hashes before generation. `generate_dubbing` must use that current `preparation_id`, `expected_revision`, and a unique request ID. The durable output is a set of immutable takes containing the actual audio path/hash, `voice_version_id`, duration, provider/model, effective parameters, and technical status. Audition and select only takes that match the current script and voice version. Use `auditionDraft` only with `text`, `emotionPrompt`, and an adopted `voiceVersionId` from a connected voice node. A successful task can still yield failed dialogue IDs; inspect individual take status.

Prepare against the actual saved clone provider, not a historical environment default. The modern dubbing route supports the saved RunningHub IndexTTS2 workflow and pins its account, endpoint and workflow alongside the voice versions. Check credential availability during preparation. If provider configuration changes, create a new preparation; keep failed-task receipts and retry only the missing dialogue. Do not regenerate video to repair a TTS configuration failure. An external director episode must resolve its adopted source dialogue even when legacy SQLite beats are empty.

Before the first synthesis on a RunningHub deployment, verify its actual graph contract. A four-branch IndexTTS2 graph is not the single-switch graph with selector node `103`. For the four-branch graph, submit only the selected output and its dependencies through the documented full-workflow override; validate that the submitted graph has one `SaveAudio` output and select that output by node identity. Do not remove a missing selector and then run all branches, or assume the first returned audio is the requested performance. Validate one missing dialogue through the public dubbing route before submitting the remaining lines. Preserve the user's adopted voice versions throughout this repair.

## Clone from an imported reference in the narrative-group video path

This path uses the imported/adopted sample as IndexTTS2 reference audio; it is not a standalone voice-training API. For one H3 span, the supported CLI can call the narrative-group dialogue-source route:

```bash
nuomi --project PROJECT request episodes/003/narrative-groups/GROUP_ID/video/dialogue-source \
  --method POST \
  --json '{"span_index":0,"dialogue_source":"external_tts","revision":CURRENT_VIDEO_REVISION}'
```

The request schema is `span_index` (zero-based), `dialogue_source` (`external_tts` or `h3_native`), and current `revision`. The group composer calls `prepare_external_audio`: it first resolves every speaker to its pinned reference audio and separates the source H3 audio before making paid TTS calls. Each immutable source line is synthesized with its reference path and original text. Takes are joined in source order and fitted against the actual span duration. If the cloned dialogue exceeds 1.35× the span it stops for a script/timing revision rather than silently truncating the final line.

For `external_tts`, the timeline records per-entry `external_audio_path` for the replacement dialogue and manifest fields `original_audio_path`, `dialogue_stem_path`, `ambience_stem_path` plus their status fields. Nuomi uses the Demucs `vocals` / `no_vocals` two-stem split; the retained ambience input is the `no_vocals` stem. During final composition the route uses the external dialogue with that ambience, not the original mixed audio, so the original embedded dialogue is not added twice. This is a two-stem separator, not a guarantee of clean dialogue-only/environment-only isolation. If Demucs is missing, this route stops before TTS and asks for audio separation capability; do not silently drop intended ambience.

Use `h3_native` only when the user explicitly selects it and the current H3 adapter/workflow allows it. That route keeps the generated video's audio stream. Do not generate a separate copy of the same dialogue for that span. A no-dialogue segment needs no voice track. Independent dubbing does not imply mouth movement or lip sync; add visible speech performance only when requested and supported by the active video route.

## Checks, graph, and recovery

The voice node should retain `characterId`, `identityId`, `voiceVersionId`, and (when applicable) a versioned `voiceReference`; the dubbing node is connected to each required voice node. The `audioNode` draft should name current `episodeNumber`, `groupId`, and unique `dialogueIds`. Do not store voice references as unpinned arbitrary URLs. Keep all text and generated assets in the existing core graph/documents; do not create an extra clone-training node.

Before adopting a take, verify verbatim text, identity/timbre, language/pronunciation, acting arc, clipping/noise, duration against the span, and no duplicate embedded dialogue. If a text/hash/version changes, prepare again. If a task is unknown, query its stable task/attempt receipt before retrying; never launch the same paid request blindly. Record the chosen audio files with the existing progress stage `audio` and output label `dubbing` or `audio`.
