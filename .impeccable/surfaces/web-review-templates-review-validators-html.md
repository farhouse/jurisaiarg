---
version: 1
slug: "web-review-templates-review-validators-html"
primary_target: "web/review/templates/review/validators.html"
related_targets: ["web/review/templates/review/validator_detail.html","web/review/templates/review/validator_form.html"]
---

## Direction contract
THESIS: Extend Gestión with validator account management and attributable review activity, without sending admins to Django's technical interface.
OWN-WORLD: Preserve existing paper background, green actions, serif headings, system body font, panels and semantic tables from management.html and app.css.
STORY: An administrator finds a validator, creates an account or edits its profile/access, then inspects current law decisions and recent passage events. Existing identities and votes remain unchanged.
FIRST VIEWPORT: Back link to Gestión, heading Validadores, primary Crear validador action, compact search/status filters and account table. Profile detail places account form before review activity. Mobile stacks fields and contains wide tables in labeled scroll regions.
FORM: Narrow extension, code-led; no concept seed or new visual identity. Reuse existing controls, explicit empty/error states, Django password validation, and staff-only access. No admin role editing or deletion.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
