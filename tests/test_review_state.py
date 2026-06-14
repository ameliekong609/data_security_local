from pathlib import Path

import fitz

from src.deterministic_redactor import Redaction
from src.review_state import (
    DetectionStatus,
    build_review_session,
)


def test_review_session_supports_edit_reject_custom_and_confirmed_map(tmp_path):
    pdf_path = tmp_path / "synthetic.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Client: Ada Lovelace\nAccount: 123456789\nSafe line")
    doc.save(pdf_path)
    doc.close()

    source = fitz.open(pdf_path)
    rect = source[0].search_for("Ada Lovelace")[0]
    redaction = Redaction(
        page_num=0,
        rect=rect,
        original_text="Ada Lovelace",
        replacement_text="[PERSON_1]",
        redaction_type="keyword",
    )

    review = build_review_session(pdf_path, [redaction], context_window=30)

    assert len(review.detections) == 1
    first = review.detections[0]
    assert first.document_name == "synthetic.pdf"
    assert first.page_num == 0
    assert first.status == DetectionStatus.PENDING
    assert "Client:" in first.context_before
    assert "Account:" in first.context_after

    review.edit_detection(first.detection_id, entity_type="person", replacement_label="[CLIENT_PERSON_1]")
    review.reject_detection(first.detection_id, reason="synthetic false positive for smoke test")
    custom = review.add_custom_detection(
        document_path=pdf_path,
        page_num=0,
        original_text="123456789",
        entity_type="account",
        replacement_label="[ACCOUNT_1]",
        context_before="Account: ",
        context_after="\nSafe line",
    )
    review.approve_detection(custom.detection_id)
    review.confirm_replacement_map()

    replacement_map = review.export_replacement_map()

    assert review.confirmed is True
    assert replacement_map == {
        "123456789": {
            "entity_type": "account",
            "replacement_label": "[ACCOUNT_1]",
            "document_name": "synthetic.pdf",
            "page_num": 0,
        }
    }
    assert review.detections[0].replacement_label == "[CLIENT_PERSON_1]"
    assert review.detections[0].status == DetectionStatus.REJECTED
