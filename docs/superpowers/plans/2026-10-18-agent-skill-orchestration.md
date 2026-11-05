# Implementation Plan: Agent and Skill Orchestration on the Canvas

> **For implementation agents:** REQUIRED SUB-SKILL: use `subagent-driven-development` when the user selects parallel/subagent execution, otherwise use `executing-plans`. Work in a dedicated Git worktree if available. The current checkout contains unrelated dirty work; do not reset, stash, stage, or commit unrelated files. Use TDD and complete each task before moving on.

**Goal:** Let creators describe a task in natural language or discover a Skill, review/edit a version-bound plan, confirm text work, and receive traceable candidate outputs in a collapsible canvas run group. Independent tasks may run concurrently without imposing a fixed creative pipeline.

**Architecture:** Extend the existing Agent run/delegation and Skill context services, not a second orchestration framework. The plan is a durable immutable snapshot of scope, inputs, roles, Skills, dependencies, outputs and permissions. Existing `agent_runs`, `Delegations`, `task_skill_context`, canvas revisions and orchestration run records remain authoritative; run groups are a canvas projection of completed/active work. Adoption remains an explicit user operation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, existing Agent task backend and skill files, React/TypeScript, Vitest, pytest/pytest-asyncio.

## File Ownership and Boundaries

- Agent domain and Skill resolution: `src/novelvideo/creation/agent_runs.py`, `delegations.py`, `agent_skill_context.py`, `agent_context.py`, and focused new `agent_plan.py` only if separation is needed.
- APIs: existing Agent/collaboration route modules discovered via route registration; do not add endpoints to unrelated production routes.
- Canvas projection and UI: `frontend/src/features/agent-runtime/`, `frontend/src/features/canvas/`, `frontend/src/api/agentRuns.ts`, and focused tests.
- Reuse skill materials under `skills/script-master/`, `skills/nuomi-canvas-production/`, and `skills/nuomi-production/`; do not copy their fixed stage sequence into application code.
- Do not change story-bible persistence or provider/media execution adapters here.

## Task 1: Create a durable, editable creative execution plan contract

**Files:** `src/novelvideo/creation/agent_plan.py` (or existing run model module), `tests/creation/test_agent_plan.py`

1. Define strict Pydantic models for plan scope (project/canvas/optional episode/nodes), input references with frozen revision IDs, role/Skill assignments, task keys, dependency keys, output types/targets, permission class, and estimated media batch metadata.
2. Validate unique task keys, dependency existence, acyclic graph, scope compatibility, selected input versions, and that media operations cannot be authorized by text confirmation.
3. Persist plan revision, request idempotency, creator edits and explicit text confirmation. A plan confirmation must pin input revisions; changed inputs create a new plan revision/run, not mutate a submitted plan.

Begin with contract tests such as:

```python
def test_plan_rejects_dependency_cycle():
    with pytest.raises(PlanValidation, match="cycle"):
        CreativePlan.model_validate(plan(tasks=[task("a", ["b"]), task("b", ["a"])]))

def test_text_confirmation_cannot_authorize_media():
    with pytest.raises(PlanValidation, match="media confirmation"):
        confirm_plan(plan_with_task("video.generate"), permission="text")
```

Run `pytest -q tests/creation/test_agent_plan.py`.

## Task 2: Resolve topic/stage Skills and role responsibilities into the plan

**Files:** `src/novelvideo/creation/agent_skill_context.py`, `professional_roles.py`, focused tests in `tests/creation/test_agent_skill_context.py`

1. Replace hardcoded operation-only Skill choice with a resolver using task intent, stage, project brief/topic, and explicit creator selection. Role defines responsibility/read/write boundaries; Skill supplies methods/references.
2. Return a transparent manifest: selected Skills, loaded resource paths/revisions, rationale labels, excluded resources and the project constraints that remain in force. Do not expose chain-of-thought.
3. Allow a creator to add/remove a Skill in the proposed plan; persist the final set in the plan snapshot. Project brief constraints win; deviations are explicit fields requiring creator confirmation.
4. Reuse applicable `script-master` writing/framework/doctor/dialogue resources and production Skills selectively; add no fixed stage prerequisite list.

Tests should cover genre/stage matching, explicit Skill override, user brief priority, stable resource snapshot and no unknown Skill path loads. Run `pytest -q tests/creation/test_agent_skill_context.py tests/creation/test_agent_runs.py`.

## Task 3: Add natural-language plan preview, edit, and confirm APIs

**Files:** existing Agent route module, `tests/creation/test_agent_runs_api.py`, `frontend/src/api/agentRuns.ts` and API tests.

1. Add endpoints to propose a plan from a creator request and selected canvas scope, read/edit its structured tasks, and confirm a specific text-only plan revision.
2. Enforce project role and canvas scope on every reference. The preview must return input source versions, dependencies/parallel lanes, output destinations, Skill/role labels and required permission. Do not start tasks during preview or edit.
3. On confirm, validate all input revisions again, enqueue only text-safe tasks, and return durable run/task IDs. A stale input returns a conflict with a refresh/replan action.
4. Keep existing chat and delegation interfaces compatible; adapt legacy single-task callers through a one-task plan where possible.

Test preview has no side effects, edited plans persist, stale revisions are rejected, wrong-canvas references are rejected, and confirm enqueues once per idempotency key. Run `pytest -q tests/creation/test_agent_runs_api.py tests/creation/test_agent_runs.py` and the focused frontend API tests.

## Task 4: Execute dependency graphs with accurate partial/failure states

**Files:** `src/novelvideo/creation/agent_runs.py`, `collaboration_tasks.py`, `collaboration_store.py`, focused tests in `tests/creation/`.

1. Schedule only tasks whose dependencies are adopted or successfully completed within the same pinned run. Run independent text tasks concurrently under configured limits.
2. Persist task-level queued/running/succeeded/partial/failed/blocked/cancelled/submission-unknown states, frozen inputs, attempt IDs, outputs and errors. Do not report a parent run as complete while children remain active.
3. On definite failure, clear active leases and expose retry/resume only for safe tasks with unchanged inputs. Unknown provider submissions reconcile the same task identity and never auto-submit again.
4. Never let task failure remain an infinite spinner; terminal status and retry affordance must be returned in the durable API projection.

Use deterministic fake model/provider adapters. Test parallel independent tasks, dependency gating, partial completion, cancellation, definite failure/retry, unknown submission reconciliation and stale-input blocking. Run `pytest -q tests/creation/test_agent_runs.py tests/creation/test_agent_runs_api.py`.

## Task 5: Project run results into collapsible canvas groups and candidate nodes

**Files:** existing canvas projection/UI feature, `frontend/src/features/agent-runtime/`, `frontend/src/features/canvas/`, focused Vitest tests.

1. Project one durable run group onto the plan's confirmed destination canvas. Default collapsed; expanded view shows per-task state, role/Skill, provenance and candidate outputs.
2. Candidate nodes use ordinary free layout and link to their run/task/source versions. They must not overwrite formal document, asset, or media nodes before adoption.
3. Support selecting one or many candidates for adoption through existing business-specific adoption contracts. Rejecting/dismissing a candidate does not delete run evidence.
4. Keep free canvas and project ideation canvas permissive; missing episode scope remains missing and must never default to E01.

Write component tests for collapsed/expanded state, partial failures, free placement, source/version display, individual/batch adoption and absent episode scope. Run `cd frontend && pnpm test -- <focused-tests>` and `pnpm exec tsc -b`.

## Task 6: Add separate media-batch estimate and authorization gate

**Files:** plan permission/domain module, relevant provider submission boundary, `tests/creation/test_agent_plan.py`, focused API tests.

1. Before image/video/audio/dubbing submission, construct a batch summary from exact selected nodes/counts/configuration and provider availability. Use provider estimates when available; otherwise display “估算不可用”.
2. Require a separate, explicit media-batch confirmation bound to the plan revision, batch manifest hash and provider configuration. Text plan confirmation is insufficient.
3. Revalidate batch identity at submit time; any changed count/config/input requires a new confirmation. Maintain idempotent submission and unknown-state reconciliation.

Test that absent or mismatched confirmation submits zero provider jobs; exact confirmation submits once; estimate absence does not fabricate cost; changed manifests require renewed confirmation. Run focused API and adapter suites without calling real paid providers.

## Acceptance

- Natural-language requests produce a reviewable/editable plan without starting work.
- The plan explicitly lists scope, source versions, roles, Skills, dependencies, outputs and permission.
- Independent tasks may run concurrently; dependent tasks wait for valid outputs.
- All outputs remain candidates until adopted; a run group preserves provenance and failure state.
- Text confirmation cannot authorize paid media execution; media batch confirmation is version-bound and separate.
- Failed/blocked tasks stop showing active progress and expose a safe recovery path.
