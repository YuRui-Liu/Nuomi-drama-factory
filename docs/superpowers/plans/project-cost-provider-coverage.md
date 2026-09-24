# Project cost provider coverage

This inventory defines capture boundaries; it does not claim that a provider has
reported a charge. Product credits are not procurement costs.

| Provider | Capture owner | Attribution and coverage |
| --- | --- | --- |
| RunningHub | `RunningHubClient.submit/query/cancel` | Lower-level capture covers executor and direct pipeline calls. `RunningHubExecutor.step` supplies stable `attempt.id`; runtime configuration supplies `account.id` and workflow media metadata. |
| Grsai | `GrsaiClient.submit/query` | `grsai_execution` inherits runtime project context; image pipeline scopes each stage separately. Synchronous success at submit is observed once; cached/repeated query does not add a charge. |
| Codex | `CodexCliStructuredBackend._run_once` in `knowledge_runtime/codex.py` | Generation boundary includes internal schema retries. `_create_codex_process` also runs version/login checks, so utility calls must be excluded. |

Current application routes support the following procurement capture combinations;
this is not a claim about the providers' general product capabilities:

| Provider | Image | Audio | Video | Text |
| --- | --- | --- | --- | --- |
| RunningHub | Supported | Supported | Supported | No current route |
| Grsai | Supported | No current route | No current route | No current route |
| Codex | No image generation route | No current route | No current route | Supported |

The image catalog and pipeline route image generation to Grsai or RunningHub;
TTS routes to RunningHub, and the video runtime/workflow registry routes H3 to
RunningHub. Codex structured generation produces text; reference image input
does not make it an image generation route. Unsupported combinations must not
appear as captured or priced coverage merely because a prototype displays them.

Project/task context comes from `costs.resolve_cost_context`, using explicit
`CostContext` or `llm_instrumentation.get_project_context` and
`task_state.get_current_project_task_id`. `run_core` populates this context.
Resource IDs are recorded only when explicitly provided through `CostContext`;
paths, prompts and legacy credit billing metadata are not mined for attribution.
Project-independent calls do not open a ledger. A project-attributed call with
missing account or unknown RunningHub workflow media fails before sending.

`RunningHubRuntimeConfiguration.create_client` and
`GrsaiRuntimeConfiguration.create_client` own the stable configured `account.id`;
the client capture integration must pass that identity through explicitly.

Direct RunningHub paths that bypass upper executors include:

- `image/pipeline._generate` and `_upscale`
- `tts/runninghub_indextts2.generate_indextts2_audio`
- `tts/runninghub_voice_design.generate_qwen3_voice_sample`
- `video/runninghub_h3.generate_minimax_h3_video`

Attempt identity must be allocated before submission, independent of any provider
task ID returned later:

| Entry | Attempt ID source for capture integration |
| --- | --- |
| `RunningHubExecutor.step` | Existing stable `attempt.id`; retained during submission and resumed polling |
| `image/pipeline._generate` | Application-generated UUID before each submission where no stable attempt exists |
| `image/pipeline._upscale` | Application-generated UUID before each submission where no stable attempt exists |
| `generate_indextts2_audio` | Application-generated UUID before each submission |
| `generate_qwen3_voice_sample` | Application-generated UUID before each submission |
| `generate_minimax_h3_video` | Application-generated UUID before each submission where no stable attempt exists |
| Grsai direct submission | Application-generated UUID before submission where no stable attempt exists |
| `CodexCliStructuredBackend._run_once` | Application-generated UUID for each generation invocation, including retry invocations |

UUIDs are allocated before network submission in the capture boundary or the
direct caller's `requested_cost_context`. Each direct image stage gets a fresh
UUID even when an outer context already contains an attempt ID. Codex always
allocates a new UUID per `_run_once`, including each schema repair invocation.

`CodexCliStructuredBackend` uses the stable local credential-slot alias
`codex-cli:local` by default, with an explicit constructor override available.
This is not an authenticated user identity, and conveys neither a subscription
nor a zero charge. Switching the CLI's signed-in user cannot be distinguished
historically unless a distinct account alias is supplied. Login/version checks
through `_create_codex_process` are deliberately outside the capture boundary.

Semantic request facts currently captured:

| Path | Safe requested usage |
| --- | --- |
| All attributable generation submissions | `call=1` |
| Grsai generation and RunningHub image workflows | `item=1` |
| Qwen3 voice-design / IndexTTS2 direct wrappers | Trimmed spoken-text `character` count, without the text |
| H3 direct wrapper / H3VideoPipeline base and reference timelines | Validated request `second` duration |
| Codex structured generation, including image-input reasoning | `call=1`, media `text`; no invented token counts |

Upper wrappers only scope safe request facts and do not write ledger records.
The generic executor propagates stable identity; workflow/node payloads are not
parsed into usage. H3 reference runtime and base video runtime both enter
`H3VideoPipeline` and then the same executor/client. Existing Grsai connect-only
retry stays one logical attempt; query, upload and download create none.

The verified RunningHub live snapshot exposes status/results/message, with no
proven monetary field. Grsai extras likewise have no proven accounting field.
Codex does not request JSON usage; token counts must not be invented. Missing
amounts remain unpriced unless a configured price and safe usage support an
estimate (requested usage is explicitly marked `usage_source=request`). Failed,
cancelled or unknown execution cannot be priced from requested usage alone.
Subscription coverage requires explicit account configuration; Codex
must not automatically be treated as free. A confirmed zero requires explicit
evidence recorded in the cost reason.

This document is based on the verified code inventory. No provider requests are
needed for model validation or this coverage definition.

Recovery behavior: durable prepare failure aborts before network submission.
Ambiguous submission errors retain an unknown intent and are never automatically
resubmitted by capture. After acceptance, accounting write failure is logged with
no response/exception payload and the external ID is still returned to existing
task callbacks/checkpoints. The durable pending intent signals incomplete data;
subsequent query reconciles via the same client's accepted-ID map or an executor's
saved attempt ID. After process loss, direct UUID-only callers whose external-ID
write failed cannot automatically link the orphaned intent; it remains an
explicit gap requiring reconciliation, never a fabricated zero. Polls resolve
provider/account/external identity and check project ownership before writing.
Status observations use `poll:` plus a hash of normalized safe facts, making
repeated observations idempotent. No historical completeness is claimed.

## Explicit historical import boundary

`python -m novelvideo.costs.backfill --project <stable-registry-id>` resolves the
existing project registry after bootstrap and requires its local/home node.
It reads only that record's `runtime_dir/media_h3/tasks.db` and
`runtime_dir/media_h3_ref/tasks.db`, using SQLite read-only connections and
schema validation, never the migrating `TaskStore` constructor or a media scan.

Only supported `video.*` capabilities with an explicit RunningHub workflow
profile, stable task/attempt identities, account, remote task ID and timezone-aware
submission timestamp qualify. Request `duration` is the only optional historical
quantity (`second`); a proven submission supplies `call=1`. Historical output
counts, arbitrary `cost_json`, prompts, diagnostics and provider payloads are not
billing evidence and are not copied. Imported entries remain unpriced until an
explicit repricing preview is applied. Audit evidence uses fixed local-source IDs.

Attempt identity and provider/account/external identity both participate in
transactional deduplication. Existing captures and prices stay unchanged, including
on a rerun after a source lifecycle change. Cross-project collisions are reported
without reassignment. Missing/unsupported/unreadable sources and rejected rows
have counted reason codes, and one bad source does not block the other source.
Existing coverage starts and gaps are preserved. All history remains partial:
the two task databases cannot reconstruct legacy direct Grsai, TTS, upscale or
Codex invocations and must never imply full project cost coverage.
