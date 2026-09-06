---
name: publishing-nuomi-extension-styles
description: Use when adding, publishing, validating, or repairing a drama_ext.* global read-only extension style in Nuomi Drama Factory.
---

# Publishing Nuomi Extension Styles

Publish one contract-compliant visual style without rediscovering the catalog architecture.

## Non-negotiable workflow

1. Work from the Nuomi Drama Factory Git root. Read its `AGENTS.md` and preserve unrelated changes.
2. Read [the manifest contract](references/release-manifest.md). Ask only for missing creative decisions; derive mechanical source fields from `source_audit.json`.
3. Create `release.json` and `release-plan.json` outside the repository, normally under `/tmp`.
4. Run `prepare`:

```bash
.venv/bin/python skills/publishing-nuomi-extension-styles/scripts/publish_style.py \
  prepare --manifest /tmp/release.json --plan /tmp/release-plan.json
```

5. Compute `shasum -a 256 /tmp/release-plan.json`. Show the user the normalized style, six fragment groups, provenance, preview prompt, plan SHA256, and three target files. **Stop and wait for explicit approval.** Preparing a plan is never approval to generate or write.
6. After approval, use the built-in GPT Image/image generation tool to create one original preview. Do not imitate a living artist, protected character, brand, or existing work. Avoid readable text, logos, and watermarks.
7. Re-read the plan targets. If `AGENTS.md` requires recording hooks for Bash writes, call its `beforeEditFile` hook once for each target. Then run:

```bash
.venv/bin/python skills/publishing-nuomi-extension-styles/scripts/publish_style.py \
  apply --plan /tmp/release-plan.json \
  --approved-plan-sha256 <sha256-shown-to-user> \
  --preview /absolute/generated-preview.png
```

Call the required `afterEditFile` hook once for each target immediately afterward. Never bypass a catalog-hash drift failure; rerun `prepare` and request approval again.
8. Run the fixed verification suite:

```bash
.venv/bin/python -m pytest skills/publishing-nuomi-extension-styles/tests/test_publish_style.py \
  tests/test_extension_style_catalog.py tests/test_extension_style_registry.py \
  tests/test_extension_style_previews.py tests/test_sync_extension_style_catalog.py \
  tests/test_api_styles.py -q
python scripts/sync_extension_style_catalog.py --check
npm --prefix frontend test -- --run src/__tests__/features/canvas/extension-style-catalog.test.ts
git diff --check
```

9. Inspect the exact diff and preview. Commit only the three release targets when the user has authorized committing.

## Failure rules

- Exit `2`: fix the manifest, source audit relationship, ID, prompt bias, or image; prepare again.
- Exit `3`: catalog changed after approval; discard the stale plan and restart at prepare.
- If the plan SHA256 differs from the value shown for approval, discard it and restart at prepare.
- Any write, rollback, test, hook, or visual-review failure: stop and report it. Do not weaken tests or hand-edit generated output to force success.

The script never calls paid models and never commits. The skill owns approval, image generation, project hooks, verification, and precise Git scope.
