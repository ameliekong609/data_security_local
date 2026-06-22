from pathlib import Path

import fitz

from desktop_app import DesktopApi
from src.review_state import DetectionStatus, ReviewSession


def _write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Client: Jane Example\nCompany: Tenet Legacy Pty Ltd\nAccount: 123456789",
        fontsize=12,
    )
    doc.save(path)
    doc.close()


def test_bulk_custom_detections_adds_multiple_leftovers(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _write_pdf(source)

    api = DesktopApi()
    api.selected_files = [source]

    state = api.add_bulk_custom_detections(
        "Jane Example | person | [PERSON_1]\n"
        "Tenet Legacy Pty Ltd | company | [COMPANY_1]"
    )

    assert state["summary"]["pending"] == 0
    assert state["summary"]["approved"] == 2
    assert [d["original_text"] for d in state["detections"]] == [
        "Jane Example",
        "Tenet Legacy Pty Ltd",
    ]
    assert {d["source"] for d in state["detections"]} == {"custom_bulk"}


def test_bulk_custom_detections_auto_generates_replacements(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _write_pdf(source)

    api = DesktopApi()
    api.selected_files = [source]

    state = api.add_bulk_custom_detections(
        "Jane Example | person\n"
        "Tenet Legacy Pty Ltd | company\n"
        "123456789 | account"
    )

    replacements = {d["original_text"]: d["replacement_label"] for d in state["detections"]}
    assert replacements["Jane Example"] == "[PERSON_1]"
    assert replacements["Tenet Legacy Pty Ltd"] == "[COMPANY_1]"
    assert replacements["123456789"] == "[ACCOUNT_1]"


def test_use_exported_pdfs_as_input_selects_only_pdf_outputs(tmp_path: Path):
    exported_pdf = tmp_path / "document_001_redacted.pdf"
    exported_zip = tmp_path / "redaction_outputs.zip"
    _write_pdf(exported_pdf)
    exported_zip.write_bytes(b"dummy")

    api = DesktopApi()
    api.exported_paths = [exported_pdf, exported_zip]

    state = api.use_exported_pdfs_as_input()

    assert state["selected_files"] == [str(exported_pdf)]
    assert state["summary"]["pending"] == 0


def test_reject_detection_rejects_matching_original_text(tmp_path: Path):
    api = DesktopApi()
    review = ReviewSession()
    first = review.add_custom_detection(
        document_path=tmp_path / "a.pdf",
        page_num=0,
        original_text="NetBank",
        entity_type="person",
        replacement_label="[PERSON_1]",
    )
    second = review.add_custom_detection(
        document_path=tmp_path / "b.pdf",
        page_num=1,
        original_text="netbank",
        entity_type="person",
        replacement_label="[PERSON_2]",
    )
    other = review.add_custom_detection(
        document_path=tmp_path / "b.pdf",
        page_num=1,
        original_text="Wei Wang",
        entity_type="person",
        replacement_label="[PERSON_3]",
    )
    review.approve_pending()
    api.review = review

    state = api.reject_detection(first.detection_id)

    statuses = {item["original_text"]: item["status"] for item in state["detections"]}
    assert statuses["NetBank"] == "rejected"
    assert statuses["netbank"] == "rejected"
    assert statuses["Wei Wang"] == "approved"
    assert second.status == DetectionStatus.REJECTED
    assert other.status == DetectionStatus.APPROVED


def test_approve_detection_approves_matching_original_text(tmp_path: Path):
    api = DesktopApi()
    review = ReviewSession()
    first = review.add_custom_detection(
        document_path=tmp_path / "a.pdf",
        page_num=0,
        original_text="Download",
        entity_type="person",
        replacement_label="[PERSON_1]",
    )
    second = review.add_custom_detection(
        document_path=tmp_path / "b.pdf",
        page_num=1,
        original_text="download",
        entity_type="person",
        replacement_label="[PERSON_2]",
    )
    other = review.add_custom_detection(
        document_path=tmp_path / "b.pdf",
        page_num=1,
        original_text="Wei Wang",
        entity_type="person",
        replacement_label="[PERSON_3]",
    )
    review.approve_pending()
    review.reject_detection(first.detection_id)
    review.reject_detection(second.detection_id)
    api.review = review

    state = api.approve_detection(first.detection_id)

    statuses = {item["original_text"]: item["status"] for item in state["detections"]}
    assert statuses["Download"] == "approved"
    assert statuses["download"] == "approved"
    assert statuses["Wei Wang"] == "approved"
    assert second.status == DetectionStatus.APPROVED
    assert other.status == DetectionStatus.APPROVED
