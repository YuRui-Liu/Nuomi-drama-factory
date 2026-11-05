# Recovery and batch continuity

Before resuming, read both the execution record and the current saved inputs:

```bash
nuomi --project PROJECT canvas progress get CANVAS EPISODE_ID
nuomi --project PROJECT canvas get CANVAS
nuomi --project PROJECT canvas status RUN_ID
```

Do not create a duplicate node, run, or H3 attempt while the state of its earlier request is unknown. Reuse successful outputs and update only affected downstream stages.

## Canvas orchestration runs

| Run state | Recovery |
| --- | --- |
| `pending` or `running` | Poll the same ID with `canvas wait RUN_ID`; leaving the CLI does not cancel server work. |
| `failed` | Read the failed node and last attempt, correct its cause, then retry only the failed work with `nuomi --project PROJECT canvas retry RUN_ID --failed --request-id RETRY_ID`. |
| `blocked` | Read the reported blocking reason and resolve the specific validation or write conflict. Resume only if the state says the existing result can be verified or projected without repeating media generation, using `nuomi --project PROJECT canvas resume RUN_ID`. |
| `submission_unknown` | Do not retry. Reconcile the original task/provider request first; preserve the block if there is no evidence whether it was accepted. |
| `cancel_requested` | Treat in-flight work as unresolved until the service reports a terminal state. A cancel request is not proof that the provider stopped. |
| `cancelled` | Continue only when requested. Resume the original run to reuse completed outputs; retry a failed item only after its cause is fixed. |
| `succeeded` | Re-read the saved canvas and inspect the outputs. Reuse them when their input fingerprint still matches. |

A wait timeout only ends local polling; it does not cancel the task. Continue polling the same run ID. A revision conflict means the user or another process changed the graph: read the latest revision and compare its inputs before deciding what to do.

## H3 attempts

H3 submission returns an `attempt_id`. Inspect that same attempt instead of submitting another draft:

```bash
nuomi --project PROJECT request freezone/video-director/attempts/ATTEMPT_ID
```

- For active stages such as optimizing, uploading, submitting, queued, generating, or downloading, query the same attempt again and retain its identifiers.
- For `completed`, reuse the returned media if the attempt fingerprint matches the current director draft.
- For `failed`, inspect `failed_stage` and error details; fix the cause, then retry that exact failed attempt through the project-scoped CLI route. Do not create a new attempt just to clear an error:

  ```bash
  nuomi --project PROJECT request \
    freezone/video-director/attempts/ATTEMPT_ID/retry --method POST
  ```
- For `submission_unknown`, do not retry or resubmit. Reconcile the task with the provider first; if acceptance cannot be determined, leave it blocked.

If the draft, profile, references, frames, durations, technique, or `case_reference` changes, run `director preview` and `director prepare` again. The prepared fingerprint is tied to the exact draft; never attach an old prepared object to a changed draft.

## Review checkpoints and batches

Use one progress record per episode. `review` pauses after screenplay, assets, and storyboard; storyboard requires both the table and grid outputs. Show the exact saved result before approval. `approve` must use the current stage `approval_hash` from `progress get` or `progress complete`; a stale hash is a conflict. If the user switches to auto, configure `--mode auto`, then continue; this records an automatic resolution, not a user approval. `stop_after` still pauses after its named stage until it is cleared.

For a batch, keep each episode's run IDs, request IDs, document hashes, stage outputs, and review hashes separate. A failure in one episode does not invalidate another episode's completed assets or outputs. Reconcile every unknown state before continuing the batch. Reuse confirmed assets and successful stage outputs; regenerate only the failed or input-stale dependency chain. When source material or a reviewed output changes, update that episode's affected progress stage so its downstream stages are no longer treated as current.

Record the canvas ID, episode ID, run or attempt ID, node and operation, current input fingerprint, output paths and hashes, and remaining review items. Mark a stage complete only after its required output files exist. A successful API response, task status, or file export is not a substitute for checking scene order, dialogue, sound, and playback.
