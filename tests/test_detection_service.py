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
        "EMAIL",
        "ACCOUNT",
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
    assert "DOB" not in by_type
    assert "PHONE" not in by_type
    assert "INVESTOR_ID" not in by_type
    assert "CLIENT_ID" not in by_type
    assert "TFN" not in by_type
    assert "ABN" not in by_type

    custom_alias = next(c for c in result.detections if c.text == "J Example")
    assert custom_alias.entity_type == "CUSTOM"
    assert custom_alias.proposed_placeholder == "[PERSON_1]"


def test_detect_pdf_pii_ignores_public_bank_contacts_but_keeps_client_pii(tmp_path):
    pdf_path = tmp_path / "bank-contact.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "Client: Wei Wang",
                "Email: wei.wang@example.test",
                "Bank: Macquarie Bank Limited",
                "Bank contact: investorservices@macquarie.com",
                "Account Number: 123456789",
            ]
        ),
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)
    detected_text = {candidate.text for candidate in result.detections}

    assert "Wei Wang" in detected_text
    assert "wei.wang@example.test" in detected_text
    assert "123456789" in detected_text
    assert "Macquarie Bank Limited" not in detected_text
    assert "investorservices@macquarie.com" not in detected_text


def test_custom_company_term_detects_close_account_name_variant(tmp_path):
    pdf_path = tmp_path / "teda-payment.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Payment from <TEDA AUSTRALIA FINANCIAL A/C>",
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(
        pdf_path,
        custom_terms=[
            CustomTerm(
                original="TEDA FINANCIAL PTY LTD",
                entity_type="COMPANY",
                replacement_label="[COMPANY_1]",
            )
        ],
    )

    match = next(candidate for candidate in result.detections if candidate.text == "TEDA AUSTRALIA FINANCIAL A/C")
    assert match.entity_type == "COMPANY"
    assert match.proposed_placeholder == "[COMPANY_1]"
    assert match.source_rule == "custom_variant_auto:TEDA FINANCIAL PTY LTD"


def test_addresses_are_not_detected_by_default(tmp_path):
    pdf_path = tmp_path / "transactions.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "21 cash deposit - USD cash interest",
                "22 cash deposit - USD cash intestet",
                "18 Cash Deposit - AUD Cash Interest",
                "57 Cash Deposit - AUD Cash Interest",
                "67 Cash Deposit - AUD Cash Interest",
                "89 Cash Deposit - AUD Cash Interest",
                "00 Direct Debit Request",
                "123 Synthetic Street, Melbourne VIC 3000",
            ]
        ),
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)

    assert not [candidate for candidate in result.detections if candidate.entity_type == "ADDRESS"]


def test_account_detector_ignores_labels_and_sentence_fragments(tmp_path):
    pdf_path = tmp_path / "account-false-positives.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "Account Payment reference",
                "Account income as at the date indicated",
                "Account of your objectives",
                "Account has an attached overdraft limit or facility and we send you a statement every 4 or 6",
                "Accounting Fees",
                "Accountant or tax advisor",
                "Account details",
                "Account Number: 06 3000 14000700",
            ]
        ),
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)
    account_texts = {candidate.text for candidate in result.detections if candidate.entity_type == "ACCOUNT"}

    assert "Payment reference" not in account_texts
    assert "income as at the date indicated" not in account_texts
    assert "of your objectives" not in account_texts
    assert "has an attached overdraft limit or facility and we send you a statement every 4 or 6" not in account_texts
    assert "ing Fees" not in account_texts
    assert "ant or tax advisor" not in account_texts
    assert "details" not in account_texts
    assert "06 3000 14000700" in account_texts


def test_dates_and_urls_are_not_detected_by_default(tmp_path):
    pdf_path = tmp_path / "dates-and-urls.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "1st January 2025 to 31st January 2025",
                "Monday to Friday 9am-5pm",
                "01 Nov 2024",
                "1 July 2019",
                "linkmarketservices.com.au",
                "westpac.com.au/privacy",
                "Contact: Wei Wang",
            ]
        ),
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)
    by_type = {candidate.entity_type for candidate in result.detections}

    assert "DATE" not in by_type
    assert "URL" not in by_type


def test_person_detector_ignores_public_service_and_business_labels(tmp_path):
    pdf_path = tmp_path / "public-labels.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "\n".join(
            [
                "NetBank",
                "AFCA",
                "Reply Paid",
                "Sydney",
                "Noteholders",
                "Automic",
                "Westpac",
                "Closing Balance",
                "Download",
                "Distribution",
                "Fee(s)",
                "Portfolio",
                "Privacy Statement",
                "Benpi Qrt Dst 001318737810",
                "Kpmg Austr",
                "evp.com.au Securityholder Reference Number",
                "Client: Wei Wang",
            ]
        ),
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)
    person_texts = {candidate.text for candidate in result.detections if candidate.entity_type == "PERSON"}

    assert "Wei Wang" in person_texts
    assert "NetBank" not in person_texts
    assert "AFCA" not in person_texts
    assert "Reply Paid" not in person_texts
    assert "Sydney" not in person_texts
    assert "Noteholders" not in person_texts
    assert "Automic" not in person_texts
    assert "Westpac" not in person_texts
    assert "Closing Balance" not in person_texts
    assert "Download" not in person_texts
    assert "Distribution" not in person_texts
    assert "Fee(s)" not in person_texts
    assert "Portfolio" not in person_texts
    assert "Privacy Statement" not in person_texts
    assert "Benpi Qrt Dst 001318737810" not in person_texts
    assert "Kpmg Austr" not in person_texts
    assert "evp.com.au Securityholder Reference Number" not in person_texts


def test_person_detector_handles_uppercase_titled_joint_names(tmp_path):
    pdf_path = tmp_path / "joint-names.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "MISS WEI WANG &\nMR SIMIAO ZHANG",
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(pdf_path)
    person_texts = {candidate.text for candidate in result.detections if candidate.entity_type == "PERSON"}

    assert "MISS WEI WANG" in person_texts
    assert "MR SIMIAO ZHANG" in person_texts


def test_custom_name_term_matches_inside_title_with_flexible_spacing(tmp_path):
    pdf_path = tmp_path / "known-name-contained.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Account holders:\nMISS WEI   WANG &\nMR SIMIAO ZHANG",
        fontsize=12,
    )
    doc.save(pdf_path)
    doc.close()

    result = detect_pdf_pii(
        pdf_path,
        custom_terms=[
            CustomTerm(original="Wei Wang", entity_type="PERSON", replacement_label="[PERSON_1]"),
            CustomTerm(original="Simiao Zhang", entity_type="PERSON", replacement_label="[PERSON_2]"),
        ],
    )
    custom_texts = {candidate.text for candidate in result.detections if candidate.source_detector == "custom_term"}

    assert "WEI   WANG" in custom_texts
    assert "SIMIAO ZHANG" in custom_texts
