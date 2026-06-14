from src.detection_models import DetectionCandidate
from src.review_actions import approve_all_findings


def test_approve_all_findings_marks_all_pending_findings_approved():
    findings = [
        DetectionCandidate(
            file_id="synthetic.pdf",
            page_number=0,
            text="Synthetic Alpha Pty Ltd",
            entity_type="COMPANY",
            bounding_box=None,
            proposed_replacement="[COMPANY_1]",
            source_detector="custom_term",
            confidence=0.99,
            context="Synthetic context",
            status="pending",
        ),
        DetectionCandidate(
            file_id="synthetic.pdf",
            page_number=0,
            text="Synthetic Beta Pty Ltd",
            entity_type="COMPANY",
            bounding_box=None,
            proposed_replacement="[COMPANY_2]",
            source_detector="custom_term",
            confidence=0.99,
            context="Synthetic context",
            status="rejected",
        ),
    ]

    approve_all_findings(findings)

    assert [finding.status for finding in findings] == ["approved", "approved"]
