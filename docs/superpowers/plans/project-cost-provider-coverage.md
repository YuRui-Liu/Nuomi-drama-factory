# Project cost provider coverage

This inventory defines capture boundaries; it does not claim that a provider has
reported a charge. Product credits are not procurement costs.

| Provider | Capture owner | Attribution and coverage |
| --- | --- | --- |
| RunningHub | `RunningHubClient.submit/query` | Lower-level capture covers executor and direct pipeline calls. `RunningHubTaskExecutor.step` supplies stable `attempt.id` and `provider_account_id`, but has no `project_id`. |
| Grsai | `GrsaiClient.submit/query` | `grsai_execution` and image pipeline supply context; they must not independently record the same submission. |
| Codex | `CodexCliStructuredBackend._run_once` in `knowledge_runtime/codex.py` | Generation boundary includes internal schema retries. `_create_codex_process` also runs version/login checks, so utility calls must be excluded. |

Project/resource/billing context comes from
`llm_instrumentation.get_project_context`, `get_resource_kind_context`, and
`get_billing_metadata_context`. Task context comes from
`task_state.get_current_project_task_id`. `run_core` populates this context.
Missing attribution must not be silently invented.

`RunningHubRuntimeConfiguration.create_client` and
`GrsaiRuntimeConfiguration.create_client` own the stable configured `account.id`;
the client capture integration must pass that identity through explicitly.

Direct RunningHub paths that bypass upper executors include:

- `image/pipeline._generate` and `_upscale`
- `tts/runninghub_indextts2.generate_indextts2_audio`
- `tts/runninghub_voice_design.generate_qwen3_voice_sample`
- `video/runninghub_h3.generate_minimax_h3_video`

The verified RunningHub live snapshot exposes status/results/message, with no
proven monetary field. Grsai extras likewise have no proven accounting field.
Codex does not request JSON usage; token counts must not be invented. Missing
amounts remain unpriced unless a configured price and measured usage support an
estimate. Subscription coverage requires explicit account configuration; Codex
must not automatically be treated as free. A confirmed zero requires explicit
evidence recorded in the cost reason.

This document is based on the verified code inventory. No provider requests are
needed for model validation or this coverage definition.
