"""Small reusable actions for human-review finding lists."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.detection_models import DetectionCandidate


@dataclass(frozen=True)
class ReviewLoopSummary:
    pass_number: int
    max_passes: int
    total_count: int
    new_count: int
    pending_count: int
    approved_count: int
    rejected_count: int
    can_mark_complete: bool
    can_export: bool
    at_max_passes: bool
    stop_reason: str


def set_all_findings_status(
    findings: Iterable[DetectionCandidate],
    status: str,
) -> None:
    """Mark every current finding with the requested review status."""
    normalized_status = status.strip().lower()
    for finding in findings:
        finding.status = normalized_status


def approve_all_findings(findings: Iterable[DetectionCandidate]) -> None:
    """Mark every current finding approved for local export."""
    set_all_findings_status(findings, "approved")


def set_findings_status_by_entity(
    findings: Iterable[DetectionCandidate],
    *,
    entity_type: str,
    status: str,
) -> int:
    """Mark findings of one entity type with the requested review status.

    Returns the number of findings changed so the UI can show a useful message.
    """
    normalized_entity_type = entity_type.strip().casefold()
    normalized_status = status.strip().lower()
    changed = 0
    for finding in findings:
        if finding.entity_type.strip().casefold() == normalized_entity_type:
            finding.status = normalized_status
            changed += 1
    return changed


def finding_key(finding: DetectionCandidate) -> tuple[object, ...]:
    """Stable identity for comparing findings across review passes."""
    bbox = finding.bounding_box
    bbox_key = None
    if bbox is not None:
        bbox_key = (
            round(float(bbox.x0), 1),
            round(float(bbox.y0), 1),
            round(float(bbox.x1), 1),
            round(float(bbox.y1), 1),
        )
    return (
        finding.file_id,
        finding.page_number,
        finding.text.casefold().strip(),
        finding.entity_type.casefold().strip(),
        bbox_key,
    )


def record_seen_findings(
    seen_keys: Iterable[tuple[object, ...]],
    findings: Iterable[DetectionCandidate],
) -> set[tuple[object, ...]]:
    """Return an updated set of finding keys already seen in this review loop."""
    updated = set(seen_keys)
    updated.update(finding_key(finding) for finding in findings)
    return updated


def summarize_review_loop(
    findings: Iterable[DetectionCandidate],
    *,
    seen_keys: Iterable[tuple[object, ...]],
    pass_number: int,
    max_passes: int = 3,
    review_complete: bool = False,
) -> ReviewLoopSummary:
    """Summarize bounded review-loop state for UI and export gating."""
    findings = list(findings)
    seen = set(seen_keys)
    pending_count = sum(1 for finding in findings if finding.status == "pending")
    approved_count = sum(1 for finding in findings if finding.status == "approved")
    rejected_count = sum(1 for finding in findings if finding.status == "rejected")
    new_count = sum(1 for finding in findings if finding_key(finding) not in seen)
    at_max_passes = pass_number >= max_passes
    can_mark_complete = bool(findings) and pending_count == 0
    can_export = review_complete and can_mark_complete and approved_count > 0

    if can_export:
        stop_reason = "review complete; safe to export approved findings"
    elif pending_count > 0 and at_max_passes:
        stop_reason = "max review passes reached with pending findings; resolve pending items before export"
    elif pending_count > 0:
        stop_reason = "review pending findings, add missed terms if needed, then rerun detection"
    elif new_count > 0:
        stop_reason = "new findings appeared this pass; review them before marking complete"
    elif can_mark_complete:
        stop_reason = "no pending findings; mark review complete to enable export"
    else:
        stop_reason = "run detection to start the bounded review loop"

    return ReviewLoopSummary(
        pass_number=pass_number,
        max_passes=max_passes,
        total_count=len(findings),
        new_count=new_count,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        can_mark_complete=can_mark_complete,
        can_export=can_export,
        at_max_passes=at_max_passes,
        stop_reason=stop_reason,
    )
