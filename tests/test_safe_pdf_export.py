import json
from pathlib import Path

import fitz

from src.deterministic_redactor import Redaction
from src.mapping_generator import FileMapping, generate_audit_log, generate_mapping_json
from src.pdf_writer import apply_redactions


def _make_synthetic_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Client: Jane Example\nEmail: jane.example@example.test\nAccount: 123456789",
        fontsize=12,
    )
    doc.set_metadata({"title": "Jane Example private statement", "author": "Jane Example"})
    doc.save(path)
    doc.close()


def _redactions_for(pdf_path: Path) -> list[Redaction]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    redactions = [
        Redaction(
            page_num=0,
            rect=fitz.Rect(page.search_for("Jane Example")[0]),
            original_text="Jane Example",
            replacement_text="[PERSON_1]",
            redaction_type="person",
            redaction_id="R-person-1",
        ),
        Redaction(
            page_num=0,
            rect=fitz.Rect(page.search_for("jane.example@example.test")[0]),
            original_text="jane.example@example.test",
            replacement_text="[EMAIL_1]",
            redaction_type="email",
            redaction_id="R-email-1",
        ),
        Redaction(
            page_num=0,
            rect=fitz.Rect(page.search_for("123456789")[0]),
            original_text="123456789",
            replacement_text="[ACCOUNT_1]",
            redaction_type="account",
            redaction_id="R-account-1",
        ),
    ]
    doc.close()
    return redactions


def test_redacted_pdf_text_extraction_omits_synthetic_source_strings(tmp_path):
    source = tmp_path / "synthetic_source.pdf"
    redacted = tmp_path / "synthetic_redacted.pdf"
    _make_synthetic_pdf(source)

    redactions = _redactions_for(source)
    applied = apply_redactions(str(source), str(redacted), redactions)

    assert applied == 3
    doc = fitz.open(redacted)
    extracted_text = "\n".join(page.get_text("text") for page in doc)
    doc.close()

    assert "Jane Example" not in extracted_text
    assert "jane.example@example.test" not in extracted_text
    assert "123456789" not in extracted_text
    assert "[PERSON_1]" in extracted_text
    assert "[EMAIL_1]" in extracted_text
    assert "[ACCOUNT_1]" in extracted_text

    redacted_doc = fitz.open(redacted)
    metadata = redacted_doc.metadata
    redacted_doc.close()
    assert "Jane Example" not in json.dumps(metadata)


def test_replacement_map_and_privacy_preserving_audit_are_written_locally(tmp_path):
    source = tmp_path / "synthetic_source.pdf"
    _make_synthetic_pdf(source)
    redactions = _redactions_for(source)
    mapping = FileMapping(
        input_filename="synthetic_source.pdf",
        output_filename="synthetic_redacted.pdf",
        redactions=redactions,
        llm_findings=[],
    )

    map_path = tmp_path / "redaction_mapping.json"
    audit_path = tmp_path / "redaction_audit.json"

    generate_mapping_json([mapping], map_path)
    generate_audit_log([mapping], audit_path)

    assert map_path.exists()
    assert audit_path.exists()

    replacement_map = json.loads(map_path.read_text(encoding="utf-8"))
    assert replacement_map["files"][0]["redactions"][0]["from"] == "Jane Example"
    assert replacement_map["files"][0]["redactions"][0]["to"] == "[PERSON_1]"

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_text = json.dumps(audit)
    assert "Jane Example" not in audit_text
    assert "jane.example@example.test" not in audit_text
    assert "123456789" not in audit_text
    first_event = audit["files"][0]["redactions"][0]
    assert first_event["replacement_label"] == "[PERSON_1]"
    assert first_event["original_sha256"]
    assert first_event["original_length"] == len("Jane Example")
    assert "original_text" not in first_event
