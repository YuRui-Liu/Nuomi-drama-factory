# Implementation Plan: Episode Production, Asset Continuity, and Delivery

> **For implementation agents:** REQUIRED SUB-SKILL: use `subagent-driven-development` when the user selects parallel/subagent execution, otherwise use `executing-plans`. Work in a dedicated Git worktree if available. The current checkout contains unrelated dirty work; do not reset, stash, stage, or commit unrelated files. Use TDD and complete each task before moving on.

**Goal:** Prove one episode can move from an adopted script through assets, directing/storyboards, performance/blocking, video, composition and dubbing to a playable, source-traceable deliverable, while preserving free canvas creation and explicit media-batch consent.

**Architecture:** Reuse the existing freezone orchestration graph, scheduler, adapters, project asset center, base/variant identity, and director/compose/dubbing contracts. Production graph creation is a proposed dependency plan, not a mandatory global workflow. Bind every generated result to the adopted script and asset revisions; media execution is separately confirmed in bounded batches. Keep the production canvas editable and allow tasks to start from any valid node.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, existing orchestration scheduler/adapters and media providers, React/TypeScript, pytest/pytest-asyncio, Vitest; controlled local/stub providers for tests.

## File Ownership and Boundaries

- Production graph and orchestration: `src/novelvideo/freezone/orchestration/production_graph.py`, `graph.py`, `planning.py`, `scheduler.py`, `store.py`, and adapters under `adapters/`.
- API: `src/novelvideo/api/routes/freezone_orchestration.py` and production-related route tests.
- Asset identity/reference: existing project asset center modules under `src/novelvideo/creation/`; retain base/variant semantics.
- Frontend: existing canvas production panels/nodes and `frontend/src/features/canvas/infrastructure/orchestrationGateway.ts`.
- Do not recreate story-bible facts or Agent plan storage here; consume their adopted revision references and confirmed task plans.

## Task 1: Define the one-episode production dependency contract

**Files:** `src/novelvideo/freezone/orchestration/production_graph.py`, `models.py`, `tests/freezone/orchestration/test_production_graph.py`

1. Model accepted script revision, episode canvas, selected asset/base/variant revisions, director/storyboard inputs, performance/blocking outputs, video outputs, compose target and voice/dubbing outputs as typed references.
2. Build dependencies only for requested deliverables. Preserve independent asset prep, performance exploration, soundtrack/dubbing preparation or other work when their declared inputs are available; never impose page navigation or hidden stage gates.
3. Validate episode/canvas scope and source revisions. A changed adopted script or asset version marks dependent candidates stale and asks for review; it does not overwrite completed work.
4. Keep generated graph creation idempotent and report exact created/reused node IDs and source fingerprints.

Add contract tests first:

```python
def test_partial_production_graph_contains_only_requested_deliverables():
    graph = build_episode_graph(script=script_ref("rev-3"), requested={"storyboard"})
    assert {node.kind for node in graph.nodes} == {"storyboard"}
    assert graph.nodes[0].inputs[0].revision_id == "rev-3"
```

Run `pytest -q tests/freezone/orchestration/test_production_graph.py`.

## Task 2: Preserve asset identity and base/variant provenance through production

**Files:** existing `src/novelvideo/creation/asset_reference.py`, asset publication/reference modules as needed, `tests/creation/test_canvas_asset_publication.py`, orchestration frozen-input tests.

1. Ensure production references a project asset by stable asset ID plus explicit base/variant and media revision. Copying a prompt or URL into a node is not an asset reference.
2. Keep episode-generated variants local candidates until creator chooses publish to shared asset center. Publishing creates a new immutable variant/revision; it never replaces the shared base silently.
3. Capture the exact asset revision in director/storyboard/video run inputs and result provenance. Detect source changes before retry or downstream composition.
4. Make base/variant selection visible and testable from canvas nodes and asset-center views.

Test that two episodes may reference the same base but distinct variants; a newly generated variant does not mutate the base; and a changed source cannot be silently used as if frozen. Run `pytest -q tests/creation/test_canvas_asset_publication.py tests/freezone/orchestration/test_asset_design_frozen_inputs.py`.

## Task 3: Wire director, storyboard, performance, video, composition, and dubbing adapters

**Files:** existing adapters under `src/novelvideo/freezone/orchestration/adapters/`, `director_bridge.py`, `tests/freezone/orchestration/test_adapters.py`, `test_storyboard_contract.py`, `test_voice_adapter.py`, `test_dubbing_adapter.py`.

1. For each deliverable, map the confirmed plan output to an existing registered operation and a typed result node. Reuse existing director/storyboard/voice/dubbing adapters rather than inventing parallel endpoints.
2. Keep expression, action, position/blocking and camera intent attached to stable scene/shot/character identities and source screenplay revision.
3. Validate adapter outputs before node projection; preserve partial outputs and structured errors. A provider HTTP success is submission evidence only, not completed media evidence.
4. Keep composition inputs ordered and explicit; dubbing tracks align to adopted script/dialogue and selected voices. Unsupported provider features must surface as unavailable instead of producing fake fields.

Use stub providers only. Add tests for contract mapping, partial failure, result provenance, retry idempotency, and composition/dubbing ordering. Run the five focused orchestration test modules listed above.

## Task 4: Enforce per-batch media confirmation at the provider boundary

**Files:** orchestration permission/submission service, `src/novelvideo/api/routes/freezone_orchestration.py`, `tests/freezone/orchestration/test_api.py`, adapter permission tests.

1. Present exact target node IDs/count, frozen inputs, provider/model settings, expected output type and provider estimate. If no trustworthy estimate is available, return an explicit unavailable value.
2. Bind user confirmation to the batch fingerprint and graph/run revision. Revalidate authorization immediately before provider submission.
3. A changed prompt, node count, source revision or provider configuration invalidates the confirmation. Retrying a definite failed task can reuse confirmation only when its fingerprint is unchanged and policy permits; unknown submission reconciles the original request.
4. No real provider call occurs in preview or plan-confirm endpoints.

Tests assert no call before explicit batch confirmation, exactly one call after confirmation, invalidation on mutation, and safe recovery from unknown submission. Run `pytest -q tests/freezone/orchestration/test_api.py tests/freezone/orchestration/test_scheduler.py` with fake adapters.

## Task 5: Make production status and recovery reliable in canvas UI

**Files:** `frontend/src/features/canvas/production/DirectorProductionPanel.tsx`, production nodes and `orchestrationGateway.ts`, corresponding `frontend/src/__tests__/features/canvas/` tests.

1. Render production task status from the durable run projection. Map every terminal state to a non-spinner view with message and available retry/reconcile actions.
2. Show missing/stale source versions and links to adopted script, asset and relevant canvas nodes. Preserve result candidates and media history after a retry.
3. Show batch size, provider/configuration, estimate or “未提供估算”, and confirmation action before launch. Keep composition and dubbing controls contextual to selected targets.
4. Keep user-created canvas nodes available even if production graph setup is incomplete; do not redirect to a fixed wizard.

Add UI tests for running→failed, stale source, unknown submission, estimate unavailable, batch confirm/cancel, and retry enabled only for eligible states. Run focused Vitest files and `cd frontend && pnpm exec tsc -b`.

## Task 6: Verify one complete episode with a controlled production fixture

**Files:** `tests/acceptance/test_episode_production_slice.py` and focused frontend acceptance coverage if an existing browser harness supports it.

Using a fixed adopted script, project asset base plus one approved variant, and fake/stub media adapters, execute a complete state transition through director/storyboard, performance/blocking, video outputs, composition and dubbing. Assert every output records source revision IDs and adoption state; compose references outputs in order; generated assets remain candidates until explicitly published; failed work is visibly terminal and retryable; no provider receives a request without batch confirmation.

Run `pytest -q tests/acceptance/test_episode_production_slice.py` plus the focused adapter/scheduler/API suites. A real paid-provider smoke test is a separately scheduled human-approved activity and is not part of automated verification.

## Acceptance

- An episode can be produced from confirmed source revisions without navigating a fixed page pipeline.
- Each stage can be started independently when its declared inputs exist.
- Asset bases and variants keep stable identity and precise provenance across episodes.
- Every output is a candidate until adopted; completed outputs survive retries.
- Media submission requires a separate fingerprint-bound batch confirmation.
- A controlled full-episode run produces a playable ordered composition with aligned voice/dubbing references and source traceability.
- Failed and unknown tasks show accurate terminal/reconciliation status and never spin forever.
