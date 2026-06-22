from __future__ import annotations

from typing import Iterable

import fitz

from src.custom_term_detector import (
    CustomTerm as DetectorTerm,
    CustomTermDetector as CoreCustomTermDetector,
    candidates_to_redactions,
)
from src.detection_models import DetectionCandidate
from src.deterministic_redactor import Redaction
from src.services.profiles import RedactionProfile


# Backwards-compatible name used by the review table. The concrete
# model is the common detector output shape required by the review workflow.
ReviewFinding = DetectionCandidate


class CustomTermDetector:
    """Detect local custom profile terms and variants as pending review rows."""

    source_name = "custom_term"

    def __init__(self, profile: RedactionProfile):
        self.profile = profile
        self._detector = CoreCustomTermDetector(
            DetectorTerm(
                term_id=term.term_id,
                entity_type=term.entity_type,
                original=term.original,
                replacement=term.replacement,
                variants=term.variants,
            )
            for term in profile.terms
        )

    def detect_pdf(self, doc: fitz.Document, file_id: str) -> list[ReviewFinding]:
        """Return deduplicated pending candidates for human review.

        This method intentionally does not export or apply redactions. The
        caller must explicitly approve findings before converting them to
        legacy Redaction objects.
        """
        return self._detector.detect_document(doc, file_id=file_id)

    def redactions_for_findings(self, findings: Iterable[ReviewFinding]) -> list[Redaction]:
        """Convert only approved review findings to redactions."""
        return candidates_to_redactions(findings)

    def redactions_for_pdf(self, doc: fitz.Document, file_id: str) -> list[Redaction]:
        """Compatibility API: detection alone does not produce redactions.

        Custom terms must enter review as pending rows and cannot be exported
        until a review layer marks them approved. Therefore this legacy helper
        always returns an empty list for freshly detected PDF findings.
        """
        return self.redactions_for_findings(self.detect_pdf(doc, file_id=file_id))
