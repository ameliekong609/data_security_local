"""Reusable review-state models and services for local PII review workflows.

The Streamlit UI should orchestrate these classes instead of storing review
business rules only in session state. All data is local and designed for
synthetic tests or user-selected local documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
import hashlib

import fitz

from src.deterministic_redactor import Redaction


class DetectionStatus(StrEnum):
    """Human review status for a detection candidate."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ReviewDetection:
    """A detection candidate with editable human-review metadata."""

    detection_id: str
    document_path: str
    document_name: str
    page_num: int
    original_text: str
    entity_type: str
    replacement_label: str
    context_before: str = ""
    context_after: str = ""
    status: DetectionStatus = DetectionStatus.PENDING
    source: str = "rule-based"
    rejection_reason: str = ""
    rect: tuple[float, float, float, float] | None = None

    @property
    def page_label(self) -> int:
        """Return a human-readable one-based page number."""

        return self.page_num + 1

    def to_redaction(self) -> Redaction:
        """Convert an approved review item back into the legacy redaction model."""

        if self.rect is None:
            raise ValueError(
                f"Detection {self.detection_id} has no PDF rectangle; custom UI-only "
                "detections must be anchored before PDF export."
            )
        redaction = Redaction(
            page_num=self.page_num,
            rect=fitz.Rect(self.rect),
            original_text=self.original_text,
            replacement_text=self.replacement_label,
            redaction_type=self.entity_type,
            redaction_id=self.detection_id,
        )
        return redaction


@dataclass
class ReviewSession:
    """Mutable local review workflow state independent of any UI framework."""

    detections: list[ReviewDetection] = field(default_factory=list)
    confirmed: bool = False

    def get_detection(self, detection_id: str) -> ReviewDetection:
        for detection in self.detections:
            if detection.detection_id == detection_id:
                return detection
        raise KeyError(f"Unknown detection_id: {detection_id}")

    def approve_detection(self, detection_id: str) -> ReviewDetection:
        detection = self.get_detection(detection_id)
        detection.status = DetectionStatus.APPROVED
        detection.rejection_reason = ""
        self.confirmed = False
        return detection

    def reject_detection(self, detection_id: str, reason: str = "") -> ReviewDetection:
        detection = self.get_detection(detection_id)
        detection.status = DetectionStatus.REJECTED
        detection.rejection_reason = reason
        self.confirmed = False
        return detection

    def approve_pending(
        self,
        *,
        document_name: str | None = None,
        entity_type: str | None = None,
    ) -> int:
        """Approve pending detections, optionally scoped to one file or type."""

        changed = 0
        for detection in self.detections:
            if detection.status != DetectionStatus.PENDING:
                continue
            if document_name is not None and detection.document_name != document_name:
                continue
            if entity_type is not None and detection.entity_type != entity_type:
                continue
            detection.status = DetectionStatus.APPROVED
            detection.rejection_reason = ""
            changed += 1
        if changed:
            self.confirmed = False
        return changed

    def reject_pending(
        self,
        *,
        document_name: str | None = None,
        entity_type: str | None = None,
        reason: str = "",
    ) -> int:
        """Reject pending detections, optionally scoped to one file or type."""

        changed = 0
        for detection in self.detections:
            if detection.status != DetectionStatus.PENDING:
                continue
            if document_name is not None and detection.document_name != document_name:
                continue
            if entity_type is not None and detection.entity_type != entity_type:
                continue
            detection.status = DetectionStatus.REJECTED
            detection.rejection_reason = reason
            changed += 1
        if changed:
            self.confirmed = False
        return changed

    def edit_detection(
        self,
        detection_id: str,
        *,
        entity_type: str | None = None,
        replacement_label: str | None = None,
        original_text: str | None = None,
    ) -> ReviewDetection:
        detection = self.get_detection(detection_id)
        changed = False
        if entity_type is not None:
            next_entity_type = entity_type.strip() or detection.entity_type
            if next_entity_type != detection.entity_type:
                detection.entity_type = next_entity_type
                changed = True
        if replacement_label is not None:
            next_replacement_label = replacement_label.strip() or detection.replacement_label
            if next_replacement_label != detection.replacement_label:
                detection.replacement_label = next_replacement_label
                changed = True
        if original_text is not None:
            next_original_text = original_text.strip() or detection.original_text
            if next_original_text != detection.original_text:
                detection.original_text = next_original_text
                changed = True
        if changed:
            self.confirmed = False
        return detection

    def add_custom_detection(
        self,
        *,
        document_path: str | Path,
        page_num: int,
        original_text: str,
        entity_type: str,
        replacement_label: str,
        context_before: str = "",
        context_after: str = "",
        rect: tuple[float, float, float, float] | None = None,
    ) -> ReviewDetection:
        path = Path(document_path)
        detection = ReviewDetection(
            detection_id=_stable_detection_id(
                str(path), page_num, original_text, entity_type, len(self.detections)
            ),
            document_path=str(path),
            document_name=path.name,
            page_num=page_num,
            original_text=original_text,
            entity_type=entity_type,
            replacement_label=replacement_label,
            context_before=context_before,
            context_after=context_after,
            status=DetectionStatus.PENDING,
            source="custom",
            rect=rect,
        )
        self.detections.append(detection)
        self.confirmed = False
        return detection

    def reviewed_detections(self) -> list[ReviewDetection]:
        return [d for d in self.detections if d.status != DetectionStatus.PENDING]

    def approved_detections(self) -> list[ReviewDetection]:
        return [d for d in self.detections if d.status == DetectionStatus.APPROVED]

    def confirm_replacement_map(self) -> dict[str, dict[str, Any]]:
        """Mark the reviewed map as confirmed and return the exportable map."""

        pending = [d for d in self.detections if d.status == DetectionStatus.PENDING]
        if pending:
            raise ValueError(f"Cannot confirm replacement map with {len(pending)} pending detection(s).")
        self.confirmed = True
        return self.export_replacement_map()

    def export_replacement_map(self) -> dict[str, dict[str, Any]]:
        """Return only approved replacements for local export/audit."""

        return {
            detection.original_text: {
                "entity_type": detection.entity_type,
                "replacement_label": detection.replacement_label,
                "document_name": detection.document_name,
                "page_num": detection.page_num,
            }
            for detection in self.approved_detections()
        }


def build_review_session(
    pdf_path: str | Path,
    redactions: list[Redaction],
    *,
    context_window: int = 80,
) -> ReviewSession:
    """Build review state from detected PDF redactions with local text context."""

    path = Path(pdf_path)
    doc = fitz.open(path)
    try:
        detections = []
        for index, redaction in enumerate(redactions):
            page_text = doc[redaction.page_num].get_text("text") if redaction.page_num < len(doc) else ""
            before, after = _surrounding_context(page_text, redaction.original_text, context_window)
            detection_id = redaction.redaction_id or _stable_detection_id(
                str(path), redaction.page_num, redaction.original_text, redaction.redaction_type, index
            )
            detections.append(
                ReviewDetection(
                    detection_id=detection_id,
                    document_path=str(path),
                    document_name=path.name,
                    page_num=redaction.page_num,
                    original_text=redaction.original_text,
                    entity_type=redaction.redaction_type,
                    replacement_label=redaction.replacement_text,
                    context_before=before,
                    context_after=after,
                    source="rule-based",
                    rect=(redaction.rect.x0, redaction.rect.y0, redaction.rect.x1, redaction.rect.y1),
                )
            )
    finally:
        doc.close()
    return ReviewSession(detections=detections)


def approved_redactions_for_export(review: ReviewSession) -> list[Redaction]:
    """Convert approved review detections into redactions for PDF export."""

    return [d.to_redaction() for d in review.approved_detections() if d.rect is not None]


def _surrounding_context(page_text: str, needle: str, window: int) -> tuple[str, str]:
    if not needle:
        return "", page_text[:window]
    lower_text = page_text.lower()
    lower_needle = needle.lower()
    start = lower_text.find(lower_needle)
    if start == -1:
        return page_text[:window], ""
    end = start + len(needle)
    return page_text[max(0, start - window):start], page_text[end:end + window]


def _stable_detection_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return "D-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
