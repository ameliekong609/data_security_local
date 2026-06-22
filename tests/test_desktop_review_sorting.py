from desktop_app import DesktopApi
from src.review_state import ReviewSession


def test_desktop_state_sorts_high_priority_pii_first(tmp_path):
    api = DesktopApi()
    review = ReviewSession()
    review.add_custom_detection(
        document_path=tmp_path / "doc.pdf",
        page_num=0,
        original_text="Example Holdings Pty Ltd",
        entity_type="company",
        replacement_label="[COMPANY_1]",
    )
    review.add_custom_detection(
        document_path=tmp_path / "doc.pdf",
        page_num=0,
        original_text="Wei Wang",
        entity_type="person",
        replacement_label="[PERSON_1]",
    )
    review.add_custom_detection(
        document_path=tmp_path / "doc.pdf",
        page_num=0,
        original_text="wei@example.test",
        entity_type="email",
        replacement_label="[EMAIL_1]",
    )
    api.review = review

    state = api.state()

    assert [d["display_entity_type"] for d in state["detections"]] == ["Person", "Email", "Company"]


def test_desktop_state_keeps_rejected_rows_at_bottom(tmp_path):
    api = DesktopApi()
    review = ReviewSession()
    rejected = review.add_custom_detection(
        document_path=tmp_path / "doc.pdf",
        page_num=0,
        original_text="Aaron Example",
        entity_type="person",
        replacement_label="[PERSON_1]",
    )
    approved = review.add_custom_detection(
        document_path=tmp_path / "doc.pdf",
        page_num=0,
        original_text="Wei Wang",
        entity_type="person",
        replacement_label="[PERSON_2]",
    )
    review.approve_pending()
    review.reject_detection(rejected.detection_id)
    api.review = review

    state = api.state()

    assert [d["original_text"] for d in state["detections"]] == [approved.original_text, rejected.original_text]
    assert state["detections"][-1]["status"] == "rejected"
