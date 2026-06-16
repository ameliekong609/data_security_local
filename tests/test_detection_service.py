from pathlib import Path

import fitz

from src.detection_service import CustomTerm, detect_pdf_pii, extract_pdf_text


def _write_synthetic_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "Client: Jane Example",
                "DOB: 18/08/1986",
                "Email: jane.example@example.test",
                "Phone: +61 412 345 678",
                "Address: 123 Synthetic Street, Melbourne VIC 3000",
                "Account Number: 1234 5678 9012",
                "Investor No: INV123456",
                "Client ID: CL-987654",
                "TFN: 123 456 789",
                "ABN: 12 345 678 901",
                "Entity: Example Holdings Pty Ltd ATF Example Family Trust",
                "Custom alias: J Example",
            ]
        ),
    )
    doc.save(path)
    doc.close()


def test_extract_pdf_text_returns_page_text_with_line_offsets(tmp_path):
    pdf_path = tmp_path / "synthetic-pii.pdf"
    _write_synthetic_pdf(pdf_path)

    extracted = extract_pdf_text(pdf_path)

    assert extracted.file_id == "synthetic-pii.pdf"
    assert len(extracted.pages) == 1
    assert extracted.pages[0].page_number == 1
    assert "Jane Example" in extracted.pages[0].text


def test_detect_pdf_pii_uses_presidio_orchestration_for_unprofiled_people(tmp_path):
    pdf_path = tmp_path / "synthetic-pii.pdf"
    _write_synthetic_pdf(pdf_path)

    result = detect_pdf_pii(pdf_path)

    person = next(c for c in result.detections if c.entity_type == "PERSON" and c.text == "Jane Example")
    assert person.source_detector == "presidio"
    assert person.source_rule == "presidio:PERSON"
    assert person.proposed_placeholder == "[PERSON_1]"
    assert person.bounding_box is not None


def test_detect_pdf_pii_preserves_deterministic_rules_and_custom_terms(tmp_path):
    pdf_path = tmp_path / "synthetic-pii.pdf"
    _write_synthetic_pdf(pdf_path)

    result = detect_pdf_pii(
        pdf_path,
        custom_terms=[
            CustomTerm(
                original="Jane Example",
                entity_type="PERSON",
                replacement_label="[PERSON_1]",
                variants=["J Example"],
            )
        ],
    )

    by_type = {candidate.entity_type for candidate in result.detections}
    assert {
        "PERSON",
        "DOB",
        "EMAIL",
        "PHONE",
        "ADDRESS",
        "ACCOUNT",
        "INVESTOR_ID",
        "CLIENT_ID",
        "TFN",
        "ABN",
        "COMPANY",
        "TRUST",
        "CUSTOM",
    }.issubset(by_type)

    jane = next(c for c in result.detections if c.entity_type == "PERSON")
    assert jane.page_number == 1
    assert jane.span.start >= 0
    assert jane.span.end > jane.span.start
    assert "Client:" in jane.context
    assert jane.confidence == 1.0
    assert jane.source_detector == "custom_term"
    assert jane.source_rule == "custom_term:Jane Example"
    assert jane.proposed_placeholder == "[PERSON_1]"
    assert jane.status == "pending"

    email = next(c for c in result.detections if c.entity_type == "EMAIL")
    assert email.proposed_placeholder == "[EMAIL_1]"
    assert email.source_detector == "regex"

    custom_alias = next(c for c in result.detections if c.text == "J Example")
    assert custom_alias.entity_type == "CUSTOM"
    assert custom_alias.proposed_placeholder == "[PERSON_1]"
