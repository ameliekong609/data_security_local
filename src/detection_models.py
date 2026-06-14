"""Common detection candidate models for human review workflows."""

from dataclasses import dataclass

import fitz


@dataclass
class DetectionCandidate:
    """A detector finding that must be reviewed before export/redaction.

    All detector outputs use this shape so the UI/review layer can approve,
    reject, or edit findings without knowing which detector produced them.
    """

    file_id: str
    page_number: int
    text: str
    entity_type: str
    bounding_box: fitz.Rect
    proposed_replacement: str
    source_detector: str
    confidence: float
    context: str
    status: str = "pending"
    term_id: str = ""
