# Director Plan v2 smoke tests

The free local smoke goes through `DirectorPlanService` and its durable store,
style resolution, active-plan materialization, the production grid split runner,
`H3EpisodePackOptimizer`, the isolated segment runner, and the deterministic
composition planner. Local fake providers sit only at the paid transport
boundaries; application orchestration and validation are not replaced.

```powershell
.\.venv\Scripts\python.exe tests\scripts\test_smoke_director_plan_v2.py
```

Expected final line:

```text
DIRECTOR_PLAN_V2_LOCAL_SMOKE_OK groups=2 batches=2 segments=3
```

## Paid real-provider smoke

The real smoke has no mock or fallback path. It resolves the application's
persisted text/media configuration, then uses `DirectorPlanner` plus
`DirectorPlanService`, the GRSAI adapter plus production grid splitter, the
whole-episode H3 optimizer, and the production segment provider path. It refuses
to run unless the operator explicitly accepts cost.

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
