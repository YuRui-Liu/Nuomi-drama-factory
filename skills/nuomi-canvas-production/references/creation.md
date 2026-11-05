# From source material to screenplay

Read what the user names: premise, project settings, story bible, outlines, continuity notes, existing episodes, and local reference files. Record the source path and which facts it supports in a project-local note. Separate facts the user has locked from suggestions the user has not approved. Do not infer character appearance, relationships, or plot facts from names alone.

## Select the episode's existing screenplay source

Before drafting or editing, inspect the current screenplay node and formal handoff state. These are separate stores and must not become parallel editable copies:

```bash
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT request episodes/003/director-workspace
nuomi --project PROJECT script documents list
```

If the screenplay node has `data.documentRef`, it points to a project-local file managed by `ProductionDocuments`. Read and write that file with `canvas documents`; its returned `hash` is the optimistic concurrency token. If the node has `data.documentId` and `data.documentRevisionId`, it points to a formal immutable revision in `script_creation.DocumentStore`; use `script documents` and revision-bound proposals. A confirmed handoff for the same document and episode is the binding that connects that formal document to Episode Source. Check `director-workspace.script.source_sync` and `director-workspace.script.artifact` before treating a formal revision as current production source.

When both stores appear to contain the same screenplay, do not choose by filename or copy one over the other. Follow the existing node and confirmed-handoff binding. If the binding is missing or conflicting, stop before creating another editable copy and resolve the source path explicitly. A formal DocumentStore screenplay is never copied into `ProductionDocuments` to edit it there.

## Supplied complete screenplay in a project-local document

Use this route only when the episode's existing screenplay source is a project-local `documentRef`. Preserve the provided screenplay as source text. If it needs adaptation, save a clearly labeled working version and record what changed; never describe newly written dialogue, scenes, or connective text as user-authored. Create one project-local document only when no target file exists and the user has selected this local-document workflow:

```bash
nuomi --project PROJECT canvas documents write CANVAS episodes/003/script.md \
  --text-file supplied-screenplay.md
nuomi --project PROJECT canvas documents read CANVAS episodes/003/script.md
```

For each later edit, read the current `hash` and pass it as `--expected-hash`; on `409`, read the latest text and merge intentionally. The screenplay node's `data.documentRef` names this project-relative file. Do not also create a formal `episode_script` document for the same source unless the user explicitly chooses a formal handoff workflow.

## Formal Script Creation DocumentStore screenplay

For a genuinely new episode with no existing screenplay binding, use this route only if the user has selected the formal Script Creation workflow. Create one `episode_script` document; creation itself does not bind it to Episode Source or create a canvas:

```bash
nuomi --project PROJECT script documents create --json \
  '{"kind":"episode_script","title":"第 3 集","episode_number":3,"markdown":"SCREENPLAY_TEXT","client_mutation_id":"episode-003-script-create-1"}'
```

If the episode already has a formal document, use that document ID and revision. If the existing node instead selects a local `documentRef`, keep the local-document workflow and do not create this formal copy.

Read the formal document and its current immutable revision through the Script Creation CLI:

```bash
nuomi --project PROJECT script documents get DOCUMENT_ID
nuomi --project PROJECT script documents revisions DOCUMENT_ID
nuomi --project PROJECT script handoffs list --episode-number 003
```

For a bound episode, use the existing formal document. A reviewed rewrite proposal is one edit route; the supported direct `script documents save` and `script documents restore` commands are also valid. Each operation creates the formal current revision and then uses the same source-sync policy against the existing confirmed handoff. For a proposal, review it and accept only the intended set against the current revision:

```bash
nuomi --project PROJECT script proposals list DOCUMENT_ID
nuomi --project PROJECT script proposals accept --json \
  '{"proposal_ids":["PROPOSAL_ID"],"base_revision_id":"CURRENT_DOCUMENT_REVISION","client_mutation_id":"episode-003-accept-1"}'
nuomi --project PROJECT request episodes/003/director-workspace
```

If the user supplies a revised body directly, save it against the current revision; to restore an earlier revision, create a new revision from that historical body. Both responses run the same source-sync policy:

```bash
nuomi --project PROJECT script documents save DOCUMENT_ID --json \
  '{"base_revision_id":"CURRENT_DOCUMENT_REVISION","markdown":"REVISED_SCREENPLAY_TEXT","client_mutation_id":"episode-003-save-1"}'
nuomi --project PROJECT script documents restore DOCUMENT_ID --json \
  '{"revision_id":"HISTORICAL_REVISION","base_revision_id":"CURRENT_DOCUMENT_REVISION","client_mutation_id":"episode-003-restore-1"}'
```

An accepted proposal advances the formal DocumentStore revision. Direct save and restore do the same source-sync check. These write routes reuse the latest confirmed handoff only when it belongs to this exact document and episode and current Episode Source still matches the handoff's saved revision and hash. Each episode-script write response reports `source_sync`; `progress_invalidation` is present when an applied managed canvas binds this document and episode. Then read the current `director-workspace.script`:

- `source_sync.status: synced` means the current formal revision was synchronized through the existing handoff. The response also includes the refreshed current document revision. `script.artifact` is an immutable snapshot at `episodes/NNN/handoff-receipts/REVISION_ID.md`, with `path`, `sha256`, `source_revision`, `matches_episode_source`, and handoff/document/revision IDs. Use that path as the screenplay stage's immutable output; never edit it.
- `source_sync.status: requires_explicit_handoff` means this document has no confirmed episode binding. Do not treat the revision as the production source or silently create one.
- A director-workspace `source_sync.status: pending_sync` means its current formal revision is newer than the last handoff. A subsequent supported save, restore, or proposal acceptance applies the shared sync policy; it succeeds only if the binding, source compare-and-swap check, and required review all pass.
- `source_sync.status: conflict` means Episode Source changed since the last handoff. The saved revision remains current in DocumentStore, but the changed Episode Source is preserved; inspect both revisions and resolve the mismatch before production.
- `source_sync.status: review_required` means the handoff's required review is incomplete. Complete the actual review before attempting synchronization.

Only `script.source_sync.status: synced` together with `script.artifact.status: ready` and `script.artifact.matches_episode_source: true` establishes that the immutable snapshot matches the current Episode Source. A conflict or `review_required` response preserves the newly saved formal revision but leaves Episode Source unchanged; complete the required review or resolve the source conflict before production. If the sync is pending, the match is false, the artifact is absent, or its status is not `ready`, stop downstream production and resolve the source. When an applied episode canvas binds this exact document and episode, each save, restore, or proposal acceptance invalidates its screenplay progress receipt; inspect `progress_invalidation`, reread progress, and recompute dependent stages from the new source.

If the user explicitly wants to establish a new formal handoff, use the existing prepare/confirm flow with the source revision, reference/entity snapshot, typed update scope, fact acknowledgement, and current source project revision. Confirm only after the user has reviewed that handoff; then use the existing handoff-to-canvas endpoint. Proposal acceptance does not invent or create a first handoff for an unbound episode. See [cli.md](cli.md) for the actual CLI/API route.

## Idea or notes to screenplay

1. Extract the premise, intended audience/tone, required events, character facts, exact dialogue, episode boundary, and continuity constraints. Cite the file or user message for each locked fact. Keep open creative areas separate.
2. Draft or update a project-local story bible and outline as needed. Mark Codex-created text as a proposal until the user adopts it. Preserve source fragments and exact dialogue separately from new connective writing.
3. Write or propose a complete screenplay using the episode's selected existing source route above. Check scene order, causality, character continuity, dialogue attribution, episode ending, and that no unapproved premise or fact has been presented as fixed.
4. Create or update the core `idea` and `screenplay` nodes in one patch. The idea node summarizes the premise; the screenplay node points to the selected source using the matching supported field (`documentRef` for the local project file, or `documentId`/`documentRevisionId` for the formal store). Do not add visible planner, agent, execution-plan, or collaboration-task nodes.
5. Read the same selected source again, read the canvas and (for a formal document) director-workspace status, then show the screenplay checkpoint. Record the source hash/revision and stage input fingerprint. For a formal source, use the immutable handoff receipt as the progress output only when it matches Episode Source.

Continue only when the screenplay is complete enough for asset and storyboard work. In default `review` mode, record the screenplay outputs and wait at the checkpoint until the user approves the current hash. If the user requests auto-advance, configure `--mode auto`; that skips the fixed checkpoints while preserving any explicit `stop_after`.

## Assets and storyboard handoff

Before producing assets, read existing project and canvas references. Reuse approved character and location designs when suitable. Make a reuse checklist under the episode's project path even when no new images are needed, then record the asset stage. If generating an asset, inspect the actual image and confirm it follows the locked appearance and visual style before marking the stage ready.

The storyboard checkpoint requires both the table and the grid image. Keep shot identity, order, action, dialogue, visual state, and duration aligned between them. Save both outputs under project-relative paths, complete the storyboard stage with labels `table` and `grid`, and show both at review. Do not treat a storyboard table alone as storyboard completion.

When source text or an approved stage output changes, update that stage's document or node and progress record. Rebuild only dependent work; do not rerun confirmed assets or media whose inputs did not change. For H3 and experience use, see [h3-experience.md](h3-experience.md); for output labels and exact CLI syntax, see [cli.md](cli.md).
