<!-- lang-switch -->
**English** · [简体中文](../../zh/getting-started/configuring-models.md)

# Configuring Model Providers

> Configure the official RelayClaw channel or the local NewAPI bundled with CE.

Nuomi Drama Factory CE connects text, image, video, audio, and embedding models through an OpenAI-compatible NewAPI gateway. Channel selection, gateway address, and runtime token are saved from the web UI to local `settings.db`; CE does not read them from environment variables.

## A. Nuomi Drama Factory official key (recommended, simplest)

The default `docker-compose.yml` already routes models through the "Official Channel". After `docker compose up -d --build` is running:

1. Open **`http://localhost:8080`** in your browser and go to Settings → **Model Configuration → Official Channel**.
2. The official gateway address is fixed as `https://relayclaw.cdnfg.com/v1`; **paste your Nuomi Drama Factory key** and click "Save and Enable".
3. It works immediately — RelayClaw has all of Nuomi Drama Factory's logical models configured on its backend, so **no `*_MODEL` mapping is required**.

> Don't have a Nuomi Drama Factory key yet? Sign up / purchase at **<https://relayclaw.cdnfg.com>**.

## B. Local NewAPI

Fully local with no dependency on an external gateway: use the selfhosted orchestration, which additionally brings up a built-in `newapi` container:

```bash
docker compose -f docker-compose.selfhosted.yml up -d --build
```

On first start, open Settings → Model Configuration → Local NewAPI. The initialization flow creates the administrator and runtime token, then stores the runtime address and token in `settings.db`. Configure upstream channels and model mappings on the same page. See the [Self-Hosting Handbook](../guides/self-hosting.md) for details.

### Mapping logical model names

`.env.example` has roughly 30 `*_MODEL` entries that use logical names (e.g. `HERMES_MODEL=DC-hermes-LLM`, `SCENE_BUILD_MODEL=DC-scene-builder-LLM`). Two ways to handle them:

1. **Keep the logical names and map them to real upstream models in Local NewAPI** (recommended); or
2. **Change each `*_MODEL` to a model name your gateway actually provides.**

Grouped by purpose: text (Hermes/Cognee/the various planners/normalizers, etc.), image (`NEWAPI_IMAGE_MODEL`, `NEWAPI_NANOBANANA2_MODEL` and the various `*_IMAGE_*`), video (`VIDEO_BACKEND`, `NEWAPI_VIDEO_MODELS`…), audio (`INDEXTTS2_NEWAPI_MODEL`).

> When using a Nuomi Drama Factory official key, skip this section — RelayClaw already has everything configured.

After changing the key or channel, new clients use the new settings. Hermes rotates its worker automatically. If Cognee has already initialized in the current process, restart Nuomi Drama Factory before using the novel knowledge base again.

## Media capability configuration foundation

Nuomi Drama Factory is migrating image, video, and TTS generation from provider-specific backend names to stable media capability contracts. This foundation coexists with the NewAPI logical-model mappings above and does not automatically replace the existing production path.

### Current implementation scope

The current code provides:

- Structured request models for image, video, and TTS capabilities, including frame-reference validation.
- Configuration models for `ProviderAccount`, `WorkflowProfile`, `CapabilityImplementation`, and `RoutingPolicy`.
- SQLite configuration persistence, parameter inheritance, and explicit candidate-route resolution.
- Bounded RunningHub Workflow API JSON import, semantic-binding validation, and SHA-256 calculation.
- Asynchronous concurrency leases by provider account and capability; for example, one RunningHub account may allow up to five concurrent remote tasks.
- A recoverable local ledger for media tasks and attempts.

The media-capability management API is registered at `/api/v1/media-capabilities`. It manages and validates configuration; it does not execute real provider generation jobs.

### Management API overview

All paths below are relative to `/api/v1/media-capabilities`:

| Resource | Endpoint | Purpose |
|---|---|---|
| Provider | `GET /providers`, `GET /providers/{provider_id}` | List or read redacted provider-account configuration. |
| Provider | `PUT /providers/{provider_id}`, `DELETE /providers/{provider_id}` | Create, update, or delete a provider account. |
| Workflow | `GET /workflows`, `GET /workflows/{profile_id}/{version}` | List or read workflow profiles. |
| Workflow | `POST /workflows/import` | Upload Workflow API JSON and semantic bindings as `multipart/form-data` to create a draft. |
| Workflow | `POST /workflows/{profile_id}/{version}/publish`, `DELETE /workflows/{profile_id}/{version}` | Publish or delete a workflow version when deletion is allowed. |
| Implementation | `GET /implementations`, `GET /implementations/{implementation_id}` | List or read capability implementations. |
| Implementation | `PUT /implementations/{implementation_id}`, `DELETE /implementations/{implementation_id}` | Create, update, or delete a capability implementation. |
| Route | `GET /routes`, `GET /routes/{capability}` | List or read capability routing policies. |
| Route | `PUT /routes/{capability}`, `DELETE /routes/{capability}` | Create, update, or delete a routing policy. |

This is a system-level management API. Every GET, PUT, POST, and DELETE operation requires a system user session whose role is `admin` or `owner`. An `agent_session` receives 403 even if it carries the `owner` role, and a regular `viewer` cannot read or write this global configuration.

The following production execution is not connected yet:

- GRSAI storyboard-grid generation, quality preflight, upscaling, splitting, and border removal.
- A RunningHub executor for upload, submission, asynchronous polling, download, cancellation, and normalized errors.
- RunningHub first-frame, last-frame, first-and-last-frame, image-to-video, and TTS workflows.
- MiniMax H3 prompt compilation, video quality gates, and artifact promotion.
- The management UI and default cutover from the legacy NewAPI media path.

Saving the following configuration therefore means that the foundation can validate, resolve, and persist it; it **does not mean that Nuomi Drama Factory can already generate media through GRSAI or RunningHub**.

### Parameter precedence

Effective parameters are merged in the following order, with the leftmost layer taking precedence:

```text
task overrides > project overrides > system implementation defaults > provider defaults
```

A `null` value in a higher layer does not erase a valid lower-layer value. The merged result must still satisfy the capability implementation and workflow constraints; unknown or unsupported parameters do not silently take effect.

Routing is also explicit. The resolver only considers the ordered candidates declared by `default_implementation` and `fallback_chain`. It never switches to an unlisted provider merely because another provider failed. With an empty fallback chain, retries remain within the default implementation as allowed by policy.

### Configuration model examples

A Provider PUT request must contain `credential_ref`, but no Provider GET or PUT response returns the original reference or the underlying secret. Responses expose only `credential_configured` and the reference type in `credential_scheme`.

For example, the JSON body for `PUT /api/v1/media-capabilities/providers/runninghub-main` is:

```json
{
  "provider_type": "runninghub",
  "base_url": "https://www.runninghub.cn",
  "credential_ref": "env://RUNNINGHUB_API_KEY",
  "enabled": true,
  "max_concurrency": 5,
  "poll_concurrency": 20,
  "queue_limit": 100,
  "capability_limits": {
    "video.*": 3,
    "tts.*": 2,
    "image.grid_upscale_split": 2
  }
}
```

The corresponding redacted response is:

```json
{
  "id": "runninghub-main",
  "provider_type": "runninghub",
  "base_url": "https://www.runninghub.cn",
  "enabled": true,
  "max_concurrency": 5,
  "poll_concurrency": 20,
  "queue_limit": 100,
  "capability_limits": {
    "video.*": 3,
    "tts.*": 2,
    "image.grid_upscale_split": 2
  },
  "credential_configured": true,
  "credential_scheme": "env"
}
```

`credential_ref` never appears in the response. A 422 response for an invalid reference also does not echo the submitted value.

`max_concurrency: 5` allows at most five remote tasks to occupy that account's quota at once. A sixth task remains in the local queue until it can acquire a lease; this is not five threads serially blocking requests. Both the account-wide and capability-specific limits must be satisfied.

Credential references support `env://` and `secret://`; the current model also accepts `keyring://`. For example:

```text
env://RUNNINGHUB_API_KEY
secret://media/runninghub-main
```

Never place a real API key in examples, Workflow JSON, logs, task snapshots, or the repository.

A capability implementation binds a stable capability to a concrete account and optional workflow. A routing policy lists only explicitly allowed candidates:

```json
{
  "implementation": {
    "id": "minimax-h3-fl2va-runninghub",
    "capability": "video.fl2va",
    "provider_account": "runninghub-main",
    "workflow_profile": "minimax-h3-video",
    "prompt_profile": "minimax-h3-v1"
  },
  "routing_policy": {
    "capability": "video.fl2va",
    "default_implementation": "minimax-h3-fl2va-runninghub",
    "fallback_chain": ["newapi-seedance-fl2va"],
    "concurrency_limit": 3
  }
}
```

Use an empty `fallback_chain` when cross-provider fallback is not allowed. Duplicate candidate IDs, the default implementation repeated in the fallback chain, capability mismatches, or a route with no available candidates are rejected.

### Importing RunningHub Workflow JSON

Workflow import processes the contents of a RunningHub API JSON export; it does not trust an arbitrary server-side file path supplied by a client. The current importer accepts UTF-8 JSON bytes, a string, or an object. It reads a local `Path` only when the caller also constrains access with `allowed_root`. Imports are limited to 5 MiB, must be JSON objects, never execute scripts or instructions, and reject suspected credential fields, duplicate keys, overly deep structures, and invalid node references.

Each semantic field must be explicitly bound to a node and input field. Outputs must identify a node and media type. MiniMax H3 Director workflow `2089723723468328961` accepts one complete `timeline_data` payload:

```json
{
  "id": "minimax-h3-video",
  "version": 1,
  "workflow_id": "2089723723468328961",
  "capabilities": ["video.i2va", "video.fl2va"],
  "bindings": {
    "timeline_data": {"node_id": "12", "field": "timeline_data"}
  },
  "outputs": {
    "video": {"node_id": "7", "media_type": "video"}
  },
  "constraints": {},
  "status": "draft"
}
```

Import calculates `source_sha256` over normalized content and returns a draft profile. The current configuration store persists profile metadata, bindings, constraints, and the digest; that must not be interpreted as storing an executable copy of the original Workflow JSON. A published ID/version cannot be overwritten in place. Create a new version when the workflow ID, bindings, outputs, or constraints change.

### Provider capabilities

- **GRSAI:** planned implementations for `image.storyboard_grid` and `image.single` will generate narrative storyboard grids. A later `image.grid_upscale_split` stage will upscale, split, remove borders, and preserve deterministic cell-to-shot mapping.
- **RunningHub MiniMax H3 video:** workflow `2089723723468328961` is the default `runninghub_minimax_h3` backend. It submits one version-5 `timeline_data` to node 12 and downloads video from node 7. It supports first-frame i2v and first-plus-last-frame fl2v; tail-only generation is deliberately not exposed in the product.
- **RunningHub TTS:** the contracts include `tts.synthesize`, `tts.voice_design`, and `tts.voice_clone`; workflow submission, parallel segmentation, ordered merging, and audio quality checks remain future work.
- **MiniMax H3 Skills:** the Chinese-first prompt layer compiles structured shots into the official H3 format. Dialogue shots must include recognizable speaker, line, and timing information for lip sync. See the [official MiniMax H3 Skills](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills).

### MiniMax H3 Director operation

- One Director output can contain several shots. Composition, subtitles, and export use manifest spans and insert that physical video once.
- H3 ambience and sound effects are retained. Newly generated spans default to `external_tts`; migrated legacy MP4s default to `h3_native` because no verified stems exist, so composition keeps their original audio.
- To backfill old beat/group MP4s without altering them, first run `python scripts/h3_director_migration.py <project-dir>` (dry-run), then explicitly add `--write`. Only a same-revision mapped narrative group is CAS-attached as `completed` for production composition; a beat with no group is reported as `unattached`. `external_tts` migration additionally requires both existing `--ambience-stem` and `--dialogue-stem`; newer sidecar results are never overwritten.

### Security checklist

- Store only `env://...`, `secret://...`, or supported `keyring://...` references, never real secrets.
- Treat Workflow JSON as untrusted input; remove tokens, signed URLs, and private sample-asset names before upload.
- Do not treat a client path as a readable server path; file upload must transfer the JSON content.
- Never echo credentials in logs, exceptions, task snapshots, exports, or frontend responses.
- Confirm all required semantic bindings, output nodes, media types, and parameter constraints before publication.
- Configure fallback chains explicitly; do not rely on implicit switching between providers.
- Keep concurrency within account quotas and bound polling and local queues so personal batch production cannot overwhelm one account.

### Verifying the foundation

From the project root, use the repository's Python 3.11 virtual environment:

```powershell
venv\Scripts\python.exe -m pytest tests/media_capabilities -q
venv\Scripts\python.exe -m pytest tests/test_api_media_capabilities.py -q
venv\Scripts\python.exe -m ruff check src/novelvideo/media_capabilities tests/media_capabilities
venv\Scripts\python.exe -m ruff check src/novelvideo/api/routes/media_capabilities.py tests/test_api_media_capabilities.py
git diff --check
```

These commands verify contracts, configuration persistence, explicit routing, workflow import, concurrency leases, the task ledger, and the management API's authentication, authorization, redacted responses, and CRUD contracts. They do not call real providers.

For detailed boundaries, see the [unified media capability design](../../superpowers/specs/2026-08-14-media-provider-capability-design.md) and [foundation implementation plan](../../superpowers/plans/2026-08-14-media-capability-foundation.md).

### Reference media (optional)

If you use "upload reference image", you need to configure an OSS relay (`OSS_RELAY_ENDPOINT/BUCKET/AK/SK`); plain-text workflows can leave it unconfigured for now.
