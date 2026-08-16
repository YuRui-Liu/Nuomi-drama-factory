# Media production center

The production center organizes GRSAI image generation, RunningHub video, and TTS as a previewable and recoverable Production DAG. Projects stay on the legacy pipelines until production is explicitly enabled.

## Local startup

Start the backend from the project virtual environment:

```powershell
.\.venv\Scripts\novelvideo.exe api --port 8780
pnpm --dir frontend dev
```

The UI normally runs at `http://localhost:5173` and proxies `/api/v1` to the API. Configure provider credentials through Settings or the credential store. Never place keys in workflow JSON, logs, or project configuration.

## Providers and workflows

- GRSAI supports single images, grids, upscaling, and grid splitting. Source grids and extracted panels are registered separately.
- RunningHub video uses versioned workflow profiles for first-frame, last-frame, first-and-last-frame, and image-to-video bindings. MiniMax H3 prompts are compiled before submission.
- RunningHub TTS supports Qwen3 voice design and IndexTTS2 cloning, emotion variants, batch dialogue, and audio merging.

After importing a RunningHub API JSON file, create a profile with an explicit workflow ID, version, capability, and allow-listed node bindings. Arbitrary nodes and fields are rejected.

## Parallel production runs

RunningHub provider concurrency can be set to `5`. A shared coordinator lets video and TTS submissions run together. Polling does not consume submission slots, and queued work does not serially block independent branches.

Operator flow:

1. Select `strict`, `balanced`, or `auto`, then request a preview.
2. Review create, reuse, invalidate, skip, estimated cost, and missing assets.
3. Start with the preview `snapshot_token`; any configuration change makes the old token stale.
4. Creation returns HTTP 202 and a `run_id` while execution continues asynchronously.
5. Pause, resume, or cancel a run. Only failed, quality-failed, or cancelled nodes may be retried.

The `auto` policy still blocks `quality_failed` and high-risk artifacts from downstream composition.

## Feature flags and migration

Project defaults are:

```json
{
  "image_pipeline": "legacy",
  "video_pipeline": "legacy",
  "tts_pipeline": "legacy",
  "production_scheduler": false
}
```

Enable one production pipeline first, then enable the scheduler. Always run legacy migration in dry-run mode, review the register/skip/conflict lists, and apply explicitly. Migration records hashes and associations without moving or deleting source files; unknown provider/workflow values remain `null`.

To roll back, pause new runs, restore the four defaults above, and separately handle tasks already submitted remotely. Do not delete the Production database or legacy assets.

## Artifacts and smoke tests

Use each run detail's artifact `local_path` as the source of truth. Content-addressed outputs live under the configured artifact root; image stage files live in the batch `output_dir`. Automated tests use MockTransport and do not produce a real video. A real MP4 exists only after an explicitly enabled smoke test submits to RunningHub with valid credentials.

Start with one low-cost shot. Verify the remote task ID, downloaded file, SHA-256, quality result, and restart recovery before increasing concurrency to five.
