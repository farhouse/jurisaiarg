---
version: 1
slug: web-review-templates-review-pilots-index-html
primary_target: web/review/templates/review/pilots/index.html
related_targets: [web/review/templates/review/pilots/detail.html, web/review/static/review/pilots.css]
---

## Direction contract

Mode: Operate. Extend the existing administration forms, preserving JurisAI's paper background, green controls, typography and layout.
Audience: administrators selecting an LLM for an isolated pilot; collaborators do not see or operate these settings.
Outcome: choose a downloaded batch and a free structured-output model from one native select. Show the model in the pilot list and detail. Keep credentials exclusively in the crawler.
Comparison: a stopped or completed pilot offers an expandable action to create another execution of the same frozen sample with another model. Preserve previous proposals, evaluations, card answers and links. Do not mutate a running pilot or mix model outputs.
States: public catalog loaded, temporarily unavailable with explicit configured-model fallback, no compatible models, invalid choice, queued comparison and idempotent retry. Announced compatibility is checked again by the worker before inference.
Verification: desktop 1440 and mobile 390, label/keyboard access, contained tables, no document overflow. Test model persistence, permissions, CSRF, request replay and sample preservation without live inference.

## Completion record

The implementation retains the administration shell and primitives in `web/static/app.css`: paper background, green actions, serif page headings, system body text, bordered panels and native form controls. The model field uses the existing label, help and error pattern. The comparison form uses native `details`/`summary`, opens after a submitted error, and creates a separate execution with a link to its source. Long model identifiers wrap; wide report tables retain their existing scroll container. This is a surface extension, not a new global design system.

The finish reviewer returned **ship / no material findings** after reviewing the creation and expanded comparison surfaces at desktop (1440 px) and mobile (390 px). Evidence is in `.impeccable/review/model-selector/desktop-create.png`, `mobile-create.png`, `desktop-compare.png` and `mobile-compare.png`. These captures are review evidence; no raster assets ship with this feature.

`README.md`, `docs/procesamiento-hibrido.md` and `.env.example` agree with the implemented distinction between server default and persisted selection, public free/schema-compatible catalog, explicit outage fallback and separate comparison execution. The source keeps its proposals, reviews and card responses; the new execution reuses frozen inputs and available Laya results without treating previous model proposals or human evaluations as its own output.

Limitations: screenshot evidence covers the loaded catalog and expanded comparison form. Catalog availability and compatibility can change; the worker rechecks the selected model before inference. This documentation pass did not run live inference or deploy the application. `PRODUCT.md` and `DESIGN.md` are absent; no global product or visual rules were inferred from this narrow change.
