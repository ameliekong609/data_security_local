from pathlib import Path

import fitz

from src.detection_models import DetectionCandidate
from src.export_workflow import export_reviewed_pdf


def test_export_reviewed_pdf_only_exports_approved_custom_findings(tmp_path):
    source = tmp_path / "synthetic_custom.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Client: Synthetic Alpha Pty Ltd\nOther: Public Fund", fontsize=12)
    doc.save(source)
    doc.close()

    doc = fitz.open(source)
    rect = fitz.Rect(doc[0].search_for("Synthetic Alpha Pty Ltd")[0])
    doc.close()

    approved = DetectionCandidate(
        file_id=source.name,
        page_number=0,
        text="Synthetic Alpha Pty Ltd",
        entity_type="COMPANY",
        bounding_box=rect,
        proposed_replacement="[COMPANY_1]",
        source_detector="custom_term",
        confidence=0.99,
        context="Client: Synthetic Alpha Pty Ltd",
        status="approved",
        term_id="synthetic-term-1",
    )
    pending = DetectionCandidate(
        file_id=source.name,
        page_number=0,
        text="Public Fund",
        entity_type="CUSTOM",
        bounding_box=rect,
        proposed_replacement="[CUSTOM_1]",
        source_detector="custom_term",
        confidence=0.99,
        context="Other: Public Fund",
        status="pending",
        term_id="synthetic-term-2",
    )

    result = export_reviewed_pdf(
        pdf_bytes=source.read_bytes(),
        input_filename=source.name,
        findings=[approved, pending],
        output_dir=tmp_path / "exports",
    )

    assert result.redaction_count == 1
    assert result.output_pdf.name == "synthetic_custom_redacted.pdf"
    assert result.output_pdf.exists()
    assert result.mapping_json.exists()
    assert result.audit_json.exists()

    redacted_doc = fitz.open(result.output_pdf)
    text = "\n".join(page.get_text("text") for page in redacted_doc)
    redacted_doc.close()

    assert "Synthetic Alpha Pty Ltd" not in text
    assert "[COMPANY_1]" in text
    assert "Public Fund" in text
    assert "[CUSTOM_1]" not in text
