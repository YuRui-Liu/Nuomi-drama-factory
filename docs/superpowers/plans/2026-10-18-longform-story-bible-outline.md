# Implementation Plan: Long-form Story Bible and Outline

> **For implementation agents:** REQUIRED SUB-SKILL: use `subagent-driven-development` when the user selects parallel/subagent execution, otherwise use `executing-plans`. Work in a dedicated Git worktree if available. The current checkout contains unrelated dirty work; do not reset, stash, stage, or commit unrelated files. Use TDD and complete each task before moving on.

**Goal:** Let a creator manage a 60–100 episode series bible, story arcs, and episode outlines as versioned, source-linked creative documents, then retrieve continuity relevant to a specific episode without forcing a page-by-page workflow.

**Architecture:** Extend the existing project-local immutable `DocumentStore`/script document model and existing project ontology/domain primitives. Use typed stable IDs and explicit links for narrative entities and facts; keep the bible as the creator-facing document and structured data as a retrieval/index layer. All AI-produced changes remain candidates until adopted. Do not build a general ontology editor or replace the existing document revision system.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite/aiosqlite, existing `novelvideo.creation.ontology.ProjectDomain`, React/TypeScript only where the existing story-document UI requires it, pytest/pytest-asyncio, Vitest.

## File Ownership and Boundaries

- Backend document behavior: `src/novelvideo/script_creation/models.py`, `store.py`, `documents.py`, `consistency.py`, and a focused new `src/novelvideo/script_creation/continuity.py` if extraction is warranted.
- API: `src/novelvideo/api/routes/script_creation.py` and its focused tests.
- UI/API client: existing story bible/script document feature and its tests, discovered from current imports before editing; do not create a second editor.
- Tests: `tests/script_creation/`, `tests/test_api_script_creation.py`, plus a focused acceptance test under `tests/acceptance/`.
- Do not modify Agent plan orchestration or media production here; those are covered by the companion plans.

## Task 1: Add hierarchical series outline documents and stable outline identities

**Files:** `src/novelvideo/script_creation/models.py`, `store.py`, `tests/script_creation/test_documents.py`

1. Add explicit typed representation for series arc, story arc, and episode outline references. Preserve existing document kinds and backward compatibility for `outline` and `episode_script`.
2. Add immutable revision persistence for outline hierarchy and ordering. Each outline item must have a stable ID, parent ID, ordinal, episode range (when applicable), and source/provenance metadata. Reject duplicate IDs, cycles, invalid ranges, and episode numbers outside 1–100.
3. Use optimistic concurrency (`base_revision_id`) and idempotent `client_mutation_id` consistent with existing `DocumentStore.save` contracts. Reorder/split/merge is an explicit structural edit; do not infer lineage from text similarity.

Add tests first, for example:

```python
async def test_outline_tree_keeps_ids_when_items_are_reordered(store):
    doc = await store.create_outline(project_id="p", title="全剧大纲", items=[...])
    updated = await store.reorder_outline(doc.id, base_revision_id=doc.current_revision_id,
                                          item_ids=["arc-b", "arc-a"], client_mutation_id="move-1")
    assert [item.id for item in updated.items] == ["arc-b", "arc-a"]
    assert updated.items[0].parent_id == "series-main"
```

Then implement the smallest domain API and migration-compatible schema. Run `pytest -q tests/script_creation/test_documents.py` and verify existing document serialization remains unchanged for legacy records.

## Task 2: Persist a unified story bible and explicit continuity facts

**Files:** `src/novelvideo/script_creation/models.py`, `store.py`, `continuity.py`, `tests/script_creation/test_continuity.py`

1. Represent bible sections as one user-facing document revision with structured sections for premise, world rules, characters/relationships, timeline, arcs, foreshadowing, and episode outlines. Keep text editable in the existing document editor.
2. Add typed fact records with stable subject/object IDs, fact type, state (`candidate`, `confirmed`, `planned`, `occurred`, `retracted`, `unknown`), effective episode/arc, source document/revision and locator, and adoption status. A model inference without evidence must remain `candidate` or `unknown`.
3. Provide pure query helpers that retrieve a bounded context for an episode from adopted facts and adopted prior scripts, with returned source references and revision IDs. Exclude unadopted candidates and later episodes by default.
4. Record proposed patches as immutable candidates. Adoption creates a new bible revision and an adoption receipt; it never edits adopted history in place.

Test behavior before implementation:

```python
def test_episode_context_excludes_unadopted_and_future_facts(facts):
    context = build_episode_context(facts, episode=4)
    assert "candidate-secret" not in {item.id for item in context.facts}
    assert "episode-9-reveal" not in {item.id for item in context.facts}
    assert all(item.source_revision_id for item in context.facts)
```

Run `pytest -q tests/script_creation/test_continuity.py tests/script_creation/test_documents.py`.

## Task 3: Add outline and story-bible API contracts

**Files:** `src/novelvideo/api/routes/script_creation.py`, `tests/test_api_script_creation.py`

1. Add strict request/response schemas and endpoints for outline hierarchy read/write, continuity context preview, patch candidate creation, and patch adoption. Follow existing project scope and editor/viewer authorization.
2. Require base revision and mutation ID on writes; return `409` with current revision on conflict. Validate all referenced IDs belong to the same project.
3. The context preview endpoint must return sources and versions, not raw hidden model reasoning. Adoption must be a distinct endpoint and auditable action.

Test unauthorized writes, viewer reads, cross-project IDs, invalid hierarchy, retries with the same mutation ID, and revision conflict. Run `pytest -q tests/test_api_script_creation.py tests/script_creation/test_continuity.py`.

## Task 4: Present long outlines and source-linked facts in the existing workspace

**Files:** identify existing story-bible/document UI via `rg -n "script-creation|documents" frontend/src`; modify that feature and its tests only.

1. Add a collapsible series arc → story arc → episode outline navigator with search/filter by arc and episode. Support 60–100 episode rows through the existing virtual-list pattern if present.
2. Keep one editing surface for bible and outline content. Show source/revision and candidate/adopted state inline; do not expose ontology schema controls.
3. Add a continuity patch review card with before/after diff, evidence links, affected episodes, and explicit adopt/reject actions. Do not silently mutate the bible.
4. Preserve free canvas as an alternate way to place/link outline nodes; the document hierarchy does not impose execution order.

Write UI tests for reorder display, 100-episode navigation, candidate-vs-adopted facts, revision conflicts, and patch adoption. Run focused `pnpm test -- <test-file>` and `pnpm exec tsc -b`.

## Task 5: Validate the three-episode continuity slice

**Files:** `tests/acceptance/test_longform_continuity_slice.py` and only the minimal fixtures it needs.

Create a deterministic fixture with a 60-episode outline, three adopted episode scripts, one planned reveal, one unknown relationship, and one unadopted patch. Assert that episode 3 context includes relevant adopted episode 1–2 facts, excludes future/unadopted facts, and returns source locators. Confirm a script adoption creates a planned patch candidate and that adopting it creates a new bible revision without rewriting prior scripts.

Run `pytest -q tests/acceptance/test_longform_continuity_slice.py`; then run the focused document/API suites from Tasks 1–3. Record any existing baseline failures without broadening unrelated scope.

## Acceptance

- A 60–100 episode outline can be created, reordered and revised with stable identities.
- A single readable bible document remains the creator-facing source of truth.
- Every adopted continuity fact has source/version evidence; candidate facts are never authoritative.
- Episode context is scoped, bounded, versioned and excludes later or unadopted material.
- Plan and occurred continuity updates are separate, reviewable candidates.
- The workflow does not require navigating fixed stage pages or editing an ontology graph.
