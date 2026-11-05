# Canvas CLI workflow

Use `nuomi --project PROJECT canvas ...` for canvas operations. If the installed `nuomi` command is unavailable in a repository checkout, use `.venv/bin/python -m novelvideo.production_cli` with the same arguments. Authentication and project scope come from the configured Nuomi client; do not bypass it with direct provider calls.

For a new episode, configure progress before creating or editing graph state, then read the current saved state:

```bash
nuomi --project PROJECT canvas progress configure CANVAS EPISODE_ID --mode review
nuomi --project PROJECT canvas progress get CANVAS EPISODE_ID
```

The allowed policy values are `review` and `auto`. `--stop-after STAGE` persists an intentional stop under either policy; `--clear-stop-after` clears it. Use [stage-contracts.md](stage-contracts.md) for the full screenplay → assets → storyboard → video → audio → composition contracts.

## Read before writing

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas director capabilities
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT canvas progress get CANVAS EPISODE_ID
```

Capabilities are the operation allowlist. Run only operations shown as available for the exact node type; `available` means the adapter is installed, not that a provider call has succeeded. The canvas read returns the saved nodes, edges, and revision. Treat its current document references, parameters, and assets as authoritative before a patch or run.

## Select one screenplay source

Inspect the existing screenplay node, formal document binding, and confirmed handoff before editing:

```bash
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT script documents list
nuomi --project PROJECT request episodes/003/director-workspace
```

`canvas documents` is `ProductionDocuments`, the project-local file service. Use it when the existing screenplay node's `data.documentRef` selects a project-relative local document. `script documents` is the formal `script_creation.DocumentStore`; use it when the screenplay node has `documentId`/`documentRevisionId` and a confirmed handoff binds that document to the same episode. These are distinct stores. Do not mirror a formal screenplay into a local file for editing, and do not create a formal duplicate when a local `documentRef` is already the chosen source. If neither binding exists or they disagree, stop and resolve which source the user intends before writing.

For the selected local document, `canvas documents` returns a content hash. Use that hash for every edit after the initial creation:

```bash
nuomi --project PROJECT canvas documents read CANVAS episodes/003/script.md
nuomi --project PROJECT canvas documents write CANVAS episodes/003/script.md \
  --text-file script.md --expected-hash DOCUMENT_HASH
```

After a conflict, read the current body and merge deliberately; never retry a stale write unchanged. A local-file screenplay node keeps the same `data.documentRef`.

For a formal bound screenplay, proposals advance immutable DocumentStore revisions. Accept the reviewed proposal set against the current base revision, then inspect `source_sync` and the current director-workspace record:

```bash
nuomi --project PROJECT script documents get DOCUMENT_ID
nuomi --project PROJECT script proposals list DOCUMENT_ID
nuomi --project PROJECT script proposals accept --json \
  '{"proposal_ids":["PROPOSAL_ID"],"base_revision_id":"CURRENT_DOCUMENT_REVISION","client_mutation_id":"episode-003-accept-1"}'
nuomi --project PROJECT request episodes/003/director-workspace
```

Direct save, restore, and proposal acceptance all call the same revision-sync helper. For an existing binding, it reuses the latest confirmed handoff for the same document and episode only while Episode Source still matches that handoff's revision and hash. Each episode-script write response includes `source_sync`; `progress_invalidation` is present when an applied managed canvas binds this document and episode. On a clean sync it also returns the refreshed current document revision. `requires_explicit_handoff` means no prior binding exists, `conflict` means Episode Source changed, and `review_required` means the handoff review is incomplete. A conflict or review failure preserves the saved DocumentStore revision and leaves Episode Source intact. Creating a formal document alone does not establish the first binding. Do not mark screenplay progress ready until the sync is resolved.

Use the current revision as the compare-and-swap base for direct edits or restores:

```bash
nuomi --project PROJECT script documents save DOCUMENT_ID --json \
  '{"base_revision_id":"CURRENT_DOCUMENT_REVISION","markdown":"REVISED_SCREENPLAY_TEXT","client_mutation_id":"episode-003-save-1"}'
nuomi --project PROJECT script documents restore DOCUMENT_ID --json \
  '{"revision_id":"HISTORICAL_REVISION","base_revision_id":"CURRENT_DOCUMENT_REVISION","client_mutation_id":"episode-003-restore-1"}'
```

On a successful sync, `director-workspace.script.artifact` is the immutable receipt file with `path`, `sha256`, `source_revision`, and `matches_episode_source`. Use it as the screenplay stage output only when `script.source_sync.status` is `synced`, artifact `status` is `ready`, and `matches_episode_source` is `true`. Never edit this file. If an applied episode canvas binds this exact document and episode, each direct save, restore, or proposal acceptance invalidates its screenplay progress receipt; follow the response's `progress_invalidation`, read progress again, and recompute dependent stages.

For an episode that has no formal binding, do not expect proposal acceptance to create one. Establish a first handoff only when that transition is explicitly intended. The supported CLI prepare/confirm and canvas-bridge routes are:

```bash
nuomi --project PROJECT script handoffs prepare --json \
  '{"document_id":"DOCUMENT_ID","revision_id":"DOCUMENT_REVISION","reference_revisions":{},"selected_entity_ids":[],"update_scope":{"mode":"none","scene_ids":[]},"fact_acknowledgement":{"mode":"unchecked","reason":"记录本次交接的事实核对决定"},"client_mutation_id":"episode-003-handoff-prepare-1"}'
nuomi --project PROJECT script handoffs confirm HANDOFF_ID --json \
  '{"expected_source_project_revision":0,"client_mutation_id":"episode-003-handoff-confirm-1"}'
nuomi --project PROJECT request script-creation/handoffs/HANDOFF_ID/canvas \
  --method POST --json '{}'
```

Replace all placeholders with the prepare response and current source, including the integer returned as `expected_source_project_revision`; `0` above is illustrative only. `unchecked` requires a reason and is an explicit acknowledgement, not an automatic approval. Confirm only after the human has reviewed the exact source snapshot and update scope. There is no `script handoffs canvas` subcommand; the final command calls the existing project API route through the generic request wrapper.

Batch related node and edge changes in one operations JSON array, then apply once:

```bash
nuomi --project PROJECT canvas patch CANVAS \
  --body-file operations.json --request-id episode-003-graph-1
nuomi --project PROJECT canvas get CANVAS
```

The CLI reads the current canvas revision and sends one atomic patch. A `409` means the graph changed or the request ID conflicts; read again and re-evaluate the intended operations before sending a new request. Do not automatically replay old operations on the new revision. Use core artifact kinds only; keep planning, collaboration, and execution records in project documents and progress state.

## One-pass external screenplay and director drafts

When the current Codex is asked to author episode planning directly, prepare the semantic beats and director groups from one pinned source snapshot, then submit that supplied content through these import endpoints. These endpoints bind and validate the supplied draft; they do not invoke an internal model, provider, or task queue. Use deterministic source-context IDs and ranges rather than inventing scene, beat, or source-block IDs.

Read the formal episode source and capture its current revision and hash:

```bash
nuomi --project PROJECT request episodes/003/screenplay-semantics/source-context
```

The response includes `data.source_revision`, `data.source_hash`, and parsed scenes. Scene ordinals and each block's `id`, `kind`, `text`, and `source_range` identify the exact material to cover. Use `source_hash` verbatim for both imports; it is formatted as `sha256:` followed by 64 lowercase hexadecimal characters. The semantic import must include every parsed scene ordinal exactly once, with at least one beat per scene. Use the block IDs for dialogue evidence and the source's line numbers for beat `source_ranges`.

Prepare all semantic beats against that snapshot and submit them in one request. Each beat requires the fields below, at least one `source_ranges`, one or more `must_show` items, one or more `script_facts`, and a positive `estimated_duration_seconds` no greater than 30.

```json
{
  "expected_source_revision": 7,
  "expected_source_hash": "sha256:64-lowercase-hex-characters-from-source-context",
  "client_mutation_id": "episode-003-semantics-1",
  "scenes": [
    {
      "scene_ordinal": 1,
      "beats": [
        {
          "source_ranges": [{"start_line": 12, "end_line": 14}],
          "characters": ["林默"],
          "goal": "...",
          "obstacle": "...",
          "action": "...",
          "reaction": "...",
          "turn": "...",
          "result": "...",
          "emotional_shift": "...",
          "dialogue_source_ids": ["line-13"],
          "estimated_duration_seconds": 12,
          "must_show": ["..."],
          "script_facts": ["..."],
          "director_interpretation": ["..."]
        }
      ]
    }
  ]
}
```

```bash
nuomi --project PROJECT request episodes/003/screenplay-semantics/external-drafts \
  --method POST --body-file semantic-draft.json
nuomi --project PROJECT request episodes/003/screenplay-semantics/SEMANTIC_REVISION_ID
```

Inspect the returned validation report and exact bound source before using the existing semantic activation route. Keep the `client_mutation_id` and exact body stable for a retry; reusing that ID with changed content conflicts. If the source compare-and-swap returns `409`, fetch source-context again and reconcile the draft instead of replaying it unchanged.

```bash
nuomi --project PROJECT request episodes/003/screenplay-semantics/SEMANTIC_REVISION_ID/activate \
  --method POST --json '{}'
```

After semantic activation, bind the already-prepared director groups to that active semantic revision using its returned source-span and beat IDs; do not rerun authoring to obtain new content. The request's `groups` use the existing manual `NarrativeGroupPlan` schema: each group has `id`, `ordinal`, `source_span_ids`, `scene_anchor`, `time_anchor`, `objective`, `visible_turn`, `relation_to_previous`, and `shots`; each shot uses the existing `ShotPlan` fields such as `id`, `source_span_ids`, `subject`, `action`, `visible_start_state`, `visible_end_state`, and `duration_seconds`. Manual groups allow at most four shots and 15 seconds per shot. Do not supply revision status, validation, active-state, or style-snapshot metadata; the server owns those values. Leave `creative_contract_version` unset for the existing manual schema rather than claiming creative-contract v1 clearance.

Submit the pinned source and active semantic revision with a stable mutation ID:

```json
{
  "expected_source_revision": 7,
  "expected_source_hash": "sha256:64-lowercase-hex-characters-from-source-context",
  "semantic_revision_id": "ACTIVE_SEMANTIC_REVISION_ID",
  "client_mutation_id": "episode-003-director-1",
  "groups": [
    {
      "id": "group-1",
      "ordinal": 1,
      "source_span_ids": ["line-12", "line-13", "line-14"],
      "dramatic_beat_ids": ["beat-scene-001-01"],
      "scene_anchor": "走廊",
      "time_anchor": "夜",
      "objective": "...",
      "visible_turn": "...",
      "relation_to_previous": "single",
      "shots": [
        {
          "id": "shot-1",
          "source_span_ids": ["line-12", "line-13"],
          "subject": "...",
          "action": "...",
          "visible_start_state": "...",
          "visible_end_state": "...",
          "duration_seconds": 8
        }
      ]
    }
  ]
}
```

```bash
nuomi --project PROJECT request episodes/003/director-plans/external-drafts \
  --method POST --body-file director-draft.json
nuomi --project PROJECT request episodes/003/director-plans/DIRECTOR_REVISION_ID
```

The imported director revision is a `review_required` candidate, with server-recorded `director_model: external:codex`, human edit provenance, and the server's current prompt version. Inspect its server-generated validation report, style snapshot, and groups through the existing read endpoint, then use the existing activation route only after the candidate is accepted. The director `/review` route is for creative-contract v1 review and is not the review step for this manual external schema. Imports do not create creative clearance or relax activation checks. Keep the same mutation ID and body for a retry; a changed body needs a new ID. On source or semantic revision conflict, refresh the snapshot/revision and re-author against the new inputs.

```bash
nuomi --project PROJECT request episodes/003/director-plans/DIRECTOR_REVISION_ID/activate \
  --method POST --json '{}'
nuomi --project PROJECT canvas get CANVAS
```

Once that exact director revision is active, materialize its production groups into the existing episode canvas. Take `base_revision` from the latest canvas read and use a stable unique `request_id`. This only creates the visible production graph; it does not start generation or change adoption. Read the canvas again and continue through the existing stage contracts.

```bash
nuomi --project PROJECT request canvases/CANVAS/production-graph --method POST --json \
  '{"base_revision":12,"request_id":"episode-003-production-graph-1","episode":3,"revision_id":"ACTIVE_DIRECTOR_REVISION_ID"}'
nuomi --project PROJECT canvas get CANVAS
```

## Per-episode review and batch progression

Configure policy and inspect the saved state:

```bash
nuomi --project PROJECT canvas progress configure CANVAS 003 --mode review
nuomi --project PROJECT canvas progress configure CANVAS 004 --mode auto
nuomi --project PROJECT canvas progress configure CANVAS 005 --mode auto --stop-after storyboard
nuomi --project PROJECT canvas progress configure CANVAS 005 --clear-stop-after
nuomi --project PROJECT canvas progress get CANVAS 003
```

`review` is the default: screenplay, assets, and storyboard await review. The storyboard stop requires both a table and a grid image. `auto` skips those review gates. A configured `stop_after` still pauses at that stage even in auto mode; clear it explicitly when the user asks to continue past it. These records are per episode, so configuring one episode does not change another.

After a stage's outputs are present, list their project-relative file paths in a JSON object. Required labels are `script` or `screenplay`; `assets`, `reuse_checklist`, or `checklist`; both `table` and `grid`; `video`, `videos`, or `segments`; `audio`, `dubbing`, or `music`; and `composition` or `final`. The output files must exist under the project output root.

```bash
nuomi --project PROJECT canvas progress complete CANVAS 003 \
  --stage storyboard --input-hash STAGE_INPUT_SHA256 \
  --outputs-file storyboard-outputs.json
nuomi --project PROJECT canvas progress get CANVAS 003
nuomi --project PROJECT canvas progress approve CANVAS 003 storyboard \
  --input-hash CURRENT_APPROVAL_HASH
```

Use a stable fingerprint of the exact saved stage inputs. `complete` snapshots output file hashes and returns the current `approval_hash`; show those exact results, then pass that hash to `approve` only after the user's confirmation. If a document, node parameter, reference, or output changes, re-read the stage and use its new hashes.

## Run, observe, and export

For a regular available node operation, use a unique request ID and the exact current revision when needed:

```bash
nuomi --project PROJECT canvas run CANVAS --node NODE \
  --operation OPERATION --request-id episode-003-NODE-1
nuomi --project PROJECT canvas status RUN_ID
nuomi --project PROJECT canvas wait RUN_ID --timeout 3600
```

The H3 Director workflow uses its dedicated commands below; it is not a generic `videoDirectorNode generate` request. `canvas run` returning successfully only means the server accepted the work. Verify the final artifact and progress stage before continuing.

## Assets and voice candidates

`canvas assets brief read/write`, `validate`, `propose`, `compile`, `admit`, and `pano slice` are project-scoped operations. Write a brief from the schema returned by `brief read`, preserve its returned hash in `--expected-hash`, then validate before compiling. `admit` records a human decision; its flags do not constitute approval when the human has not made that decision. `pano slice` extracts a view from an existing 2:1 project panorama; it does not generate one.

```bash
nuomi --project PROJECT canvas assets brief read ASSET_REF CANVAS
nuomi --project PROJECT canvas assets brief write ASSET_REF CANVAS --file brief.json --expected-hash BRIEF_HASH
nuomi --project PROJECT canvas assets validate ASSET_REF CANVAS
nuomi --project PROJECT canvas assets compile ASSET_REF CANVAS --aspect 3:2 --resolution 1K
nuomi --project PROJECT canvas assets admit ASSET_REF CANVAS --confirmed-by user --basis '用户指定' --decision accept
nuomi --project PROJECT canvas assets pano slice ASSET_REF CANVAS --source assets/scene-360.png --yaw 0 --pitch 0 --fov 80 --out assets/scene-anchor.png --width 1024 --height 768
```

`voice import` imports a source sound into a reviewable candidate. It requires `--kind dialogue|nonverbal`, `--slot`, `--source`, and `--rights`. `voice candidates` and `voice recheck` inspect a candidate; only after a human actually listens and accepts it may the caller use `voice approve ... --confirm-reviewed --reason ...`. There is no separate command here that trains an independently hosted voice model. See [voice-and-audio.md](voice-and-audio.md) for pinned voice-version and H3 external-dialogue contracts.

```bash
nuomi --project PROJECT voice import CHARACTER_ID assets/voice/reference.wav \
  --kind dialogue --slot default --source '用户提供的录音' --rights '用户确认拥有配音使用权'
nuomi --project PROJECT voice candidates CHARACTER_ID
nuomi --project PROJECT voice approve CHARACTER_ID CANDIDATE_ID \
  --confirm-reviewed --reason '人工试听并确认角色身份与咬字符合定稿'
```

When the composition run has succeeded, export and play the file:

```bash
nuomi --project PROJECT canvas export COMPOSITION_RUN_ID --output episode-003.mp4
```

## H3 Director preparation

```bash
nuomi --project PROJECT canvas director capabilities
nuomi --project PROJECT canvas director preview --body-file draft.json
nuomi --project PROJECT canvas director prepare --body-file draft.json \
  --prepared-file prepared.json
nuomi --project PROJECT canvas director submit CANVAS DIRECTOR_NODE \
  --body-file draft.json --prepared-file prepared.json \
  --request-id episode-003-director-1
nuomi --project PROJECT request freezone/video-director/attempts/ATTEMPT_ID
```

`preview` is read-only. `prepare` makes two read-only previews: it fingerprints the exact current draft and sends the structured prepared input through the shared compiler. It prints the compiled result; it does not author `prepared.json`. Submit revalidates the pair in the CLI and again in the service before queuing the existing attempt. Record the returned `attempt_id`; query it with the project-scoped request command above. See [h3-experience.md](h3-experience.md) for supported modes, JSON contract, provenance, and audio guidance.

Run `nuomi --project PROJECT canvas --help` or a subcommand's `--help` when a local build differs from these examples. Do not guess a command, operation, field, capability, or mode that the current CLI and capability response do not expose.
