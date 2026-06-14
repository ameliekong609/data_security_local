# MVP Roadmap — Local-First PII Redaction App

Status: MVP scope locked on 2026-06-13
Source of truth: `docs/hermes/mvp-scope.md`
Boss / approval point: Amelie
PM: Putin / Hermes

## Current locked MVP

Build a simple local web app or desktop-style UI for a semi-technical professional services user to process synthetic/local PDF documents first:

1. Select local PDF files from a folder.
2. Choose/create a local custom redaction profile for the job/client/matter.
3. Enter or locally derive known sensitive custom terms.
4. Extract text locally.
5. Detect likely PII locally using custom terms, deterministic patterns, context/label rules, and optional local-only suggestions.
6. Review/edit/approve detections.
7. Export safely redacted/pseudonymised copies.
8. Produce local-only audit log and replacement map.

The project `inputs/` directory is approved synthetic data for Codex development and testing.

## Milestone 0 — PM/project guardrails

- Keep `docs/hermes/mvp-scope.md` current as the scope source of truth.
- Ensure worker tasks explicitly forbid use of real client data or uploading document text/screenshots to public AI.
- Ensure workers are pointed to synthetic fixtures only, including approved `inputs/` data.

## Milestone 1 — PDF local processing foundation

Goal: A local backend/service pipeline can ingest synthetic PDFs, extract text locally, detect likely PII, and generate reviewed pseudonym mappings.

Expected tasks:

- Inventory existing modules and identify reusable services versus UI/CLI code.
- Create synthetic test fixtures if `inputs/` coverage is incomplete.
- Implement/normalize local PDF extraction service.
- Implement local custom redaction profile models/storage.
- Implement CustomTermDetector as a required detector.
- Implement ContextLabelDetector for account/client/investor identifier labels.
- Implement detection model/rules for MVP entities.
- Implement review-state model for approve/reject/edit/add detections.

## Milestone 2 — PDF review UI MVP

Goal: A simple local UI lets Amelie choose files, review detections, edit labels/types, and confirm export.

Expected tasks:

- Local file/folder selection workflow.
- Create/select/import/edit local custom redaction profiles.
- Add/edit custom terms and variants before detection.
- Detection review screen with context.
- Add/edit/reject detection controls.
- Replacement map preview/confirmation.
- Clear warnings that mappings and exports remain local.

## Milestone 3 — Safe PDF export + audit

Goal: Exported synthetic PDFs are redacted/pseudonymised safely and verified so original synthetic PII is not extractable.

Expected tasks:

- Implement true PDF redaction or safe generated output where possible.
- Generate local replacement map.
- Generate local audit log with reviewer actions and export status.
- Add tests/smoke checks proving sensitive synthetic strings are not extractable from output PDFs.

## Milestone 4 — Extend after PDF path is usable

Order after PDF MVP is accepted:

1. DOCX.
2. Excel / CSV.
3. Scanned PDF/image OCR only if needed.

## Non-goals for current MVP

- Real client data in development tasks.
- Public/cloud AI processing of real document contents.
- Command-line-only user workflow.
- DOCX/Excel/CSV before PDF workflow is usable.
- Unreviewed/batch export without a human approval gate.
