import fitz

from src.custom_term_detector import CustomTerm, CustomTermDetector


def test_custom_term_detector_reports_configured_term_text_not_bbox_textbox_bleed():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Client: Teda Financial Pty Ltd\nContact: Jane Example", fontsize=12)

    detector = CustomTermDetector([
        CustomTerm(
            term_id="synthetic-company",
            entity_type="COMPANY",
            original="Teda Financial Pty Ltd",
            replacement="[COMPANY_1]",
        )
    ])

    findings = detector.detect_document(doc, file_id="synthetic.pdf")
    doc.close()

    assert len(findings) == 1
    assert findings[0].text == "Teda Financial Pty Ltd"
    assert "Jane Example" not in findings[0].text
