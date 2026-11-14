---
version: 1
slug: web-review-templates-review-pilots-index-html
primary_target: web/review/templates/review/pilots/index.html
related_targets: [web/review/templates/review/pilots/detail.html, web/review/templates/review/pilots/token_fields.html, web/review/static/review/pilots.css]
---

## Direction contract

Mode: Operate. Extend the existing administration forms, preserving JurisAI's paper background, green controls, typography and layout.
Audience: administrators selecting an LLM for an isolated pilot; collaborators do not see or operate these settings.
Outcome: choose a downloaded batch, a free structured-output model and numeric limits for total output and reasoning. Explain that reasoning shares the total with JSON. Persist the requested settings per execution; show them in the detail. Keep credentials exclusively in the crawler.
Comparison: a stopped or completed pilot offers an expandable action to create another execution of the same frozen sample with another model or token limits, including the same model. Preserve previous proposals, evaluations, card answers and links. Do not mutate a running pilot or mix model outputs.
States: public catalog loaded, temporarily unavailable with explicit configured-model fallback, no compatible models, invalid choice, invalid token combination beside its field, queued comparison and idempotent retry. Announced compatibility and budgets are checked again by the worker before inference; unsupported reasoning controls are explicitly omitted.
Verification: desktop 1440 and mobile 390, label/keyboard access, contained tables, no document overflow. Test persisted settings, shared budget validation, environment changes, permissions, CSRF, request replay and sample preservation without live inference.

## Completion record

The implementation retains the administration shell and primitives in `web/static/app.css`: paper background, green actions, serif page headings, system body text, bordered panels and native form controls. Creation and comparison share a token fieldset in `token_fields.html`, using the existing label, help and error pattern. Two numeric controls explain that reasoning and the JSON response share the total output limit. They sit in two columns above the existing mobile breakpoint (850 px) and stack below it. Their measured height is 44.5 px. The comparison form uses native `details`/`summary`, opens after a submitted error, and creates a separate execution with a link to its source. Long model identifiers wrap; wide report tables retain their existing scroll container. These additions preserve the incumbent visual system.

`ModelForm` and the shared `request_budget` validator require integer limits, a total of 2,048–131,072 tokens, and at least 1,024 reasoning tokens while leaving at least 1,024 tokens for JSON. An invalid combination returns HTTP 400, retains the submitted values and shows the specific error beside reasoning. Each execution freezes the requested limits in `config.request_settings`; the worker honors those settings over later environment changes. Unsupported reasoning parameters are omitted, with a detail-page explanation. A comparison may use the same model with changed limits; UUID replay checks include budget changes. The source keeps its proposals, reviews and card responses; the new execution reuses frozen inputs and available Laya results without treating previous model proposals or human evaluations as its own output.

The latest finish reviewer returned **Ship / no material findings** after inspecting all six captures in `.impeccable/review/token-settings/`: `desktop-create.png`, `mobile-create.png`, `desktop-compare.png`, `mobile-compare.png`, `desktop-error.png` and `mobile-error.png`. These cover creation, expanded comparison and a submitted invalid combination at desktop (1440 px) and mobile (390 px). Browser checks passed for defaults, native validation, retained invalid values with a specific server error, same-model comparison creating a separate execution, original execution preservation and persistence of the new limits. No document overflow was found at either width. These captures are review evidence; no raster assets ship with this feature.

Verification passed 37 web tests and 26 crawler tests against an isolated disposable PostgreSQL database. Both Compose configurations validate. `README.md`, `docs/procesamiento-hibrido.md`, `.env.example` and Compose configuration were updated for the requested limits, frozen execution settings and server defaults, alongside the existing catalog and comparison behavior.

Limits of the evidence: the unsupported-reasoning explanation was checked in source only. Catalog availability and compatibility can change; the worker rechecks the selected model before inference. No live inference or API-key use was performed. No deployment or push was performed for this pass. Global product and design documents were not changed by this surface documentation update.
