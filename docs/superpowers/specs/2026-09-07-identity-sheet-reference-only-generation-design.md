# Identity Sheet Reference-Only Generation Design

## Problem

Identity Sheet v2 currently generates a three-panel candidate and then reconstructs
the final image with deterministic 50%/25%/25% crops. The confirmed portrait is
copied into the left half as pixels.

This creates two destructive failure modes:

- Any hand, weapon, or prop already present in the confirmed portrait is copied into
  the final sheet and can obstruct the face.
- If the model places the front body across the nominal 50% boundary, replacing the
  left half clips the front body even when the raw candidate was otherwise complete.

The visual QC path also calls the legacy NewAPI text runtime directly. On the current
installation that runtime has no API key, while text work is routed through Codex.
The exception is collapsed to `qc_unavailable`, leaving every candidate blocked with
no actionable diagnostic.

## Confirmed Product Behavior

The confirmed portrait is an identity reference only. Its pixels must never be
copied, cropped, pasted, or overlaid into the generated Identity Sheet.

One image-model request generates the complete 3:2 sheet:

- Left 50%: newly rendered clean three-quarter portrait, head and face prominent,
  with limited shoulder/neck visibility permitted.
- Center 25%: complete faceless front full body, including the complete head outline
  and both feet.
- Right 25%: complete back full body.

The provider output is saved as the candidate without destructive panel
recomposition. The portrait remains listed in generation metadata as the identity
reference and sole facial authority.

## Generation Contract

The prompt must explicitly require:

- clear gutters and strict containment inside each proportional panel;
- no person or body part crossing a panel boundary;
- no hands, arms, weapons, tools, clothing, hair, or props covering the portrait's
  eyes, nose, mouth, jawline, or recognizable facial contour;
- a clean portrait pose with no hand-to-face gesture;
- complete front and back silhouettes with safe margins around head and feet;
- one visible face only, in the left portrait panel.

The raw provider image becomes the final version asset. Generation metadata retains
the raw path for compatibility, but records that the composition mode is
`provider_canvas` and no pixel replacement occurred.

## Quality Control

Identity Sheet QC adds stable defect codes for:

- `portrait_face_occluded`: face or identifying contour is obstructed;
- `panel_boundary_intrusion`: a person or body part crosses a panel boundary;
- `body_cropped`: the front or back body is missing part of the head, limbs, or feet.

These defects are hard visual failures and remain non-adoptable.

The QC transport must retain the underlying exception as a sanitized technical
diagnostic instead of silently discarding it. A provider/schema/route failure remains
`qc_unavailable`, but it is distinguished from a visual rejection.

## Manual Adoption Fallback

When and only when all reported QC issues are `qc_unavailable`, the version remains a
candidate that can be manually adopted after an explicit warning confirmation.
It is never auto-promoted.

Candidates with any actual visual defect remain blocked. The backend enforces the
same rule; the frontend cannot bypass it by crafting a request.

The confirmation explains that automated QC did not run and asks the user to verify
the portrait is unobstructed and both body views are complete before adoption.

## Compatibility and Migration

- Existing image assets are not rewritten. Existing broken versions remain in history
  and can be regenerated. New generations use reference-only mode.
- Legacy Identity Sheet v1 assets retain their current display behavior.
- Existing `qc_unavailable` candidates become eligible for the same manual-confirmation
  flow; visual-QC-failing candidates do not.
- API responses keep existing fields and add only optional metadata/diagnostic fields.

## Verification

Tests cover:

- prompt exclusions for face obstruction and panel intrusion;
- provider output is copied unchanged rather than recomposed;
- QC extraction includes all new defect codes;
- QC exceptions preserve a sanitized diagnostic;
- backend manual adoption allows only pure `qc_unavailable` candidates;
- frontend shows confirmation for QC-unavailable candidates and keeps true visual
  failures disabled;
- normal QC-passed adoption remains unchanged.
