from src.detection_models import DetectionCandidate
from src.review_actions import (
    finding_key,
    record_seen_findings,
    summarize_review_loop,
)


def _finding(text: str, status: str = "pending") -> DetectionCandidate:
    return DetectionCandidate(
        file_id="synthetic.pdf",
        page_number=0,
        text=text,
        entity_type="COMPANY",
        bounding_box=None,
        proposed_replacement="[COMPANY_1]",
        source_detector="custom_term",
        confidence=0.99,
        context="Synthetic context",
        status=status,
    )


def test_summarize_review_loop_counts_new_pending_approved_and_rejected_findings():
    first_seen = {finding_key(_finding("Existing Co", "approved"))}
    findings = [
        _finding("Existing Co", "approved"),
        _finding("New Co", "pending"),
        _finding("False Positive Pty Ltd", "rejected"),
    ]

    summary = summarize_review_loop(findings, seen_keys=first_seen, pass_number=2, max_passes=3)

    assert summary.pass_number == 2
    assert summary.max_passes == 3
    assert summary.total_count == 3
    assert summary.new_count == 2
    assert summary.pending_count == 1
    assert summary.approved_count == 1
    assert summary.rejected_count == 1
    assert summary.can_mark_complete is False
    assert summary.can_export is False
    assert summary.at_max_passes is False


def test_review_loop_can_complete_and_export_when_no_pending_findings_remain():
    findings = [_finding("Existing Co", "approved"), _finding("False Positive Pty Ltd", "rejected")]
    seen = record_seen_findings([], findings)

    summary = summarize_review_loop(findings, seen_keys=seen, pass_number=2, max_passes=3, review_complete=True)

    assert summary.new_count == 0
    assert summary.pending_count == 0
    assert summary.can_mark_complete is True
    assert summary.can_export is True
    assert summary.stop_reason == "review complete; safe to export approved findings"


def test_review_loop_warns_at_max_passes_when_findings_are_still_pending():
    findings = [_finding("Still Pending Pty Ltd", "pending")]

    summary = summarize_review_loop(findings, seen_keys=set(), pass_number=3, max_passes=3)

    assert summary.at_max_passes is True
    assert summary.can_export is False
    assert "max review passes reached" in summary.stop_reason
