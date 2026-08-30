# Director Plan v2 smoke tests

The free local smoke validates the Director Plan contract, a one-shot plus a
two-shot generation batch, deterministic grid splitting, whole-pack H3 prompt
compilation, and ordered segment composition. It makes no network requests.

```powershell
.\.venv\Scripts\python.exe tests\scripts\test_smoke_director_plan_v2.py
```

Expected final line:

```text
DIRECTOR_PLAN_V2_LOCAL_SMOKE_OK groups=2 batches=2 segments=3
```

## Paid real-provider smoke

The real smoke has no mock or fallback path. It sends one minimal plan request
to DeepSeek, one two-cell 9:16 image request to GRSAI, and one 5-second H3
segment to RunningHub. It refuses to run unless the operator explicitly accepts
cost. Configure `DEEPSEEK_API_KEY`, `GRSAI_API_KEY`, and either
`RUNNINGHUB_API_KEY` or `RUNNINGHUB_KEY`; optional overrides are
`GRSAI_BASE_URL` and `RUNNINGHUB_H3_WORKFLOW_ID`.

```powershell
.\.venv\Scripts\python.exe tests\scripts\test_smoke_director_plan_v2_providers.py `
  --confirm-cost `
  --output-dir .\.runtime_logs\director-plan-v2-provider-smoke
```

A valid run exits zero and prints three provider request IDs, absolute paths for
the plan, image, and video artifacts, followed by:

```text
DIRECTOR_PLAN_V2_REAL_PROVIDER_SMOKE_OK
```

Any missing configuration, HTTP/provider rejection, timeout, malformed result,
or download failure exits nonzero. Diagnostics include provider status and
content type while redacting bearer tokens and `sk-` secrets. Never paste API
keys or full authorization headers into this evidence document.

Record real-run evidence in the release ticket as: UTC timestamp, git revision,
the three request IDs, the three absolute artifact paths, and the success marker.
The paid smoke is operator-triggered and is intentionally not executed by CI.
