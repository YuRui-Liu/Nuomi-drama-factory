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
- RunningHub MiniMax H3 Director (`runninghub:minimax-h3`) remains the default. It uses workflow `2089723723468328961`, does not use global reference images, binds only `12.timeline_data`, and downloads node `7`. One task can create multiple shots; first-frame is i2v, first-plus-last-frame is fl2v, and tail-only is not exposed.
- MiniMax H3 Director Ref (`runninghub:minimax-h3-ref`) is an explicit alternative using workflow `2096502793044582401`. It combines the group's ordered global character, scene, prop, or temporary-upload references with every video's first frame and optional last frame. The default global-reference limit is 5 and can be configured from 1 to 10; first and last frames do not count toward that limit.
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

`scripts/smoke_runninghub_h3.py` submits one legacy Director timeline and prints the task ID. H3 prompts are Chinese-first; any dialogue must be recognizable for lip sync. Native ambience/SFX stay in the video. Per-span dialogue defaults to `external_tts` and can switch to `h3_native`; that only recomposes and never regenerates video.

H3 Director Ref requires at least one global Ref and a first frame. FL2V additionally requires a last frame. The selected order, Subject descriptions, asset bytes, and reference revision are frozen before upload; snapshots keep hashes and metadata, not local paths or image bytes. Missing, changed, undecodable, oversized, or out-of-project assets block submission. A failure never retries with `runninghub:minimax-h3`, removes refs or the last frame, or truncates the list.

The Ref compatibility smoke is a manual release gate, not an automated test. It makes exactly one potentially billable RunningHub submission and refuses to run unless the operator explicitly supplies absolute inputs and a resolution, then sets `RUNNINGHUB_REAL_SMOKE=1` after confirming cost:

```bash
RUNNINGHUB_REAL_SMOKE=1 uv run python scripts/smoke_runninghub_h3_ref.py \
  --reference /absolute/path/to/reference.png \
  --first-frame /absolute/path/to/first.png \
  --last-frame /absolute/path/to/last.png \
  --resolution 720p \
  --output /absolute/path/to/h3-ref-smoke.mp4
```

The output path must be absolute, its real (non-symlink) parent must already exist, and the target must not exist; the gate never overwrites a previous result. Every invocation uses a new temporary runtime ledger and reports `runtime_cache=fresh`, so a persistent cache hit cannot count as release evidence. After the runtime returns, the gate independently requires a readable non-empty artifact, uses `ffprobe` to require the exact selected H3 dimensions, and prints the output SHA-256 and actual size before `status=succeeded`. The temporary ledger is then discarded without touching application runtime data.

The command also prints workflow `2096502793044582401`, the input digest, provider task ID, final status, and local result path. Do not set the opt-in variable in CI or ordinary local checks. A remote rejection, missing/quality-failed artifact, or size mismatch is reported once without fallback or retry. Ref and frame images are preflighted as PNG/JPEG/WEBP with 20 MiB and 40-megapixel limits before submission.
