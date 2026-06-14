from src.detection_models import DetectionCandidate
from src.review_actions import (
    approve_all_findings,
    set_all_findings_status,
    set_findings_status_by_entity,
)


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


def test_set_all_findings_status_marks_every_current_finding():
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
            text="Jane Example",
            entity_type="PERSON",
            bounding_box=None,
            proposed_replacement="[PERSON_1]",
            source_detector="custom_term",
            confidence=0.99,
            context="Synthetic context",
            status="approved",
        ),
    ]

    set_all_findings_status(findings, "rejected")

    assert [finding.status for finding in findings] == ["rejected", "rejected"]


def test_set_findings_status_by_entity_updates_only_matching_entity_type():
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
            text="Jane Example",
            entity_type="PERSON",
            bounding_box=None,
            proposed_replacement="[PERSON_1]",
            source_detector="custom_term",
            confidence=0.99,
            context="Synthetic context",
            status="pending",
        ),
    ]

    changed = set_findings_status_by_entity(findings, entity_type="company", status="approved")

    assert changed == 1
    assert [finding.status for finding in findings] == ["approved", "pending"]
