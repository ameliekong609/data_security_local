# Architecture Decision — Local Custom Redaction Profiles

Status: Accepted
Decision owner: Amelie / boss
PM: Putin / Hermes
Applies to: MVP v0.1 local-first PDF workflow

## Decision

Local custom redaction profiles are a core MVP feature.

The app must let users provide known sensitive terms locally, reuse those terms across a job/client/matter, and feed them into the detector pipeline before human review and export.

No further approval prompt is required before implementing this direction.

## Rationale

Generic PII detectors can find structured identifiers such as emails, phone numbers, account numbers, ABN/TFN-like numbers, and some names/locations. However, professional services documents often contain sensitive client-specific names and entity terms that generic detectors may miss or misclassify:

- Client names.
- Company names.
- Trust names.
- Director/adviser/related-party names.
- Investor/client IDs.
- Addresses and variants.
- Abbreviations, legacy names, spelling variants, and capitalisation variants.

Local custom terms provide high-confidence deterministic detection while preserving the local-first privacy rule.

## Product requirements

The local app should support:

1. Create/select a redaction profile for a job/client/matter.
2. Add/edit/delete custom terms.
3. Assign entity type and replacement label.
4. Add aliases/variants for a term.
5. Import/export profile data locally.
6. Run CustomTermDetector as a required detector before export.
7. Show all custom-term findings in the same review screen as regex/context/optional detectors.
8. Let the user approve, reject, or edit findings before export.

## Suggested local profile schema

```json
{
  "profile_id": "local-profile-id",
  "profile_name": "Synthetic Alpha sample profile",
  "created_at": "ISO timestamp",
  "updated_at": "ISO timestamp",
  "terms": [
    {
      "term_id": "term-id",
      "entity_type": "COMPANY",
      "original": "Synthetic Company Pty Ltd",
      "replacement": "[COMPANY_1]",
      "variants": ["Synthetic Company", "SYNTHETIC COMPANY PTY LTD"],
      "notes": "optional local-only note"
    }
  ]
}
```

Storage can start as local JSON under an app-owned data directory, then move to local SQLite/encrypted storage later if needed.

## Security rules

- Profiles are local-only.
- Profile contents must not be sent to Telegram, Codex, OpenAI, Claude, or public AI.
- Worker tasks may reference the profile schema and synthetic examples only.
- Progress reports must not quote real profile contents.
- Audit logs should avoid storing full original PII where possible; replacement maps may store originals locally because they are the local restoration key.

## Detector pipeline implication

All detectors should output a common `DetectionCandidate` model:

- `file_id`
- `page_number`
- `text`
- `entity_type`
- `bounding_box`
- `proposed_replacement`
- `source_detector`
- `confidence`
- `context`
- `status = pending`

Required MVP detectors:

1. `CustomTermDetector`.
2. `RegexDetector`.
3. `ContextLabelDetector`.

Optional detector plugins:

1. `PresidioDetector` for local NER/PII suggestions.
2. `LocalLLMDetector` for local-only missed-PII suggestions.

Optional detectors must not bypass human review.

## Acceptance criteria

- A synthetic profile can be created and saved locally.
- Custom terms and variants are detected in synthetic PDFs.
- Detections include page/bounding-box information for PDF redaction.
- Custom-term detections appear in review state as pending candidates.
- Approved custom-term detections are exported as typed pseudonyms.
- Rejected custom-term detections are not exported.
- Tests use synthetic data only.
