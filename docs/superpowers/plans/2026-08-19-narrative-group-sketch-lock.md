# Narrative Group Real Models and Sketch Lock Implementation Plan

> Execute immediately after approval. Keep validation targeted.

**Goal:** Make narrative-group sketch/render use explicit real GRSAI provider/model bindings, and make render consume the latest completed sketch grid as a strong composition constraint unless the user explicitly opts out.

**Architecture:** Project media defaults store independent sketch/render bindings. Each queued task freezes the resolved binding and sketch provenance. The runner revalidates the sketch revision/path, prepends the sketch grid to the ordered reference list, compiles a strong-lock prompt, and records provenance in the stage sidecar. The existing whole-grid generation and split pipeline remains authoritative; per-beat repair stays in the existing repair flow.

**Tech stack:** FastAPI/Pydantic, immutable narrative-group sidecars, GRSAI capability runtime, React Query/React, pytest/Vitest.

---

## Task 1: Persist and expose real project image defaults

**Files:**
- Modify: `src/novelvideo/api/schemas.py`
- Modify: `src/novelvideo/api/routes/projects.py`
- Modify: `frontend/src/lib/queries/media-models.ts`
- Test: `tests/test_api_projects.py` (or nearest existing project media-default test)

**Steps:**
1. Add failing contract tests for independent sketch/render provider and model defaults.
2. Extend media-default GET/PUT while preserving video fields.
3. Default to `grsai-main/nano-banana-2` for sketch and `grsai-main/gpt-image-2` for render.
4. Run only the project media-default tests and commit.

## Task 2: Freeze and validate model/sketch constraint in group task payload

**Files:**
- Modify: `src/novelvideo/api/routes/narrative_groups.py`
- Modify: `src/novelvideo/narrative_groups/models.py`
- Modify: `src/novelvideo/narrative_groups/service.py`
- Test: `tests/test_api_narrative_groups.py`

**Steps:**
1. Add failing tests for stage defaults, per-request override, missing sketch block, explicit unconstrained override, and frozen sketch revision/path.
2. Validate requested provider is an enabled GRSAI account and model is in the documented GRSAI model set/account contract.
3. For render, require latest completed sketch unless `allow_unconstrained=true`; freeze sketch revision/path and constraint mode.
4. Persist `source_sketch_revision` and `constraint_mode` in stage state/history.
5. Run targeted API/service tests and commit.

## Task 3: Enforce strong composition lock in the runner

**Files:**
- Modify: `src/novelvideo/task_backend/runners/narrative_group.py`
- Test: `tests/test_narrative_group_runner_references.py`

**Steps:**
1. Add failing tests proving render reference 1 is the sketch grid, only eight other image references remain, prompt contains explicit composition-lock rules, selected provider/model reaches GRSAI, and stale sketch provenance fails closed.
2. Resolve the frozen provider id; submit the frozen model rather than the provider default.
3. Revalidate sketch revision and canonical asset at execution, prepend it to references, and compile strong-lock Chinese instructions.
4. Record actual provider/model plus constraint provenance.
5. Run only narrative-group runner tests and commit.

## Task 4: Add the project defaults and explicit override to UI

**Files:**
- Modify: `frontend/src/lib/queries/narrative-groups.ts`
- Modify: `frontend/src/components/episode/narrative-workbench/group-reference-dialog.tsx`
- Modify: `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- Modify: `frontend/src/components/episode/narrative-workbench/group-grid-stage.tsx`
- Test: related narrative-workbench Vitest files

**Steps:**
1. Add failing payload/UI tests for per-stage model selection and the explicit “无草图约束生成” confirmation.
2. Show actual provider/model and strong/unconstrained state on stage cards.
3. Send temporary provider/model overrides only for this task; save project defaults through the existing media-default endpoint.
4. Run only related Vitest tests plus `tsc -b`, then commit.

## Verification

- `pytest` only for project defaults, narrative-group API/service, and narrative-group runner tests.
- `vitest` only for narrative-group query/dialog/workbench tests.
- `tsc -b` and `git diff --check`.
- No live provider generation in this pass.
