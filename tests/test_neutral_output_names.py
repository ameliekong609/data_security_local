from pathlib import Path

import fitz

from src.deterministic_redactor import Redaction
from src.image_redactor import redact_image
from src.config_loader import default_redaction_config
from src.review_state import ReviewSession, build_review_session
from src.review_workflow import export_reviewed_pdfs


def test_review_export_uses_original_pdf_filename_with_redacted_suffix(tmp_path: Path):
    source = tmp_path / "Jane Example Payment Advice 2025.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Email: jane.example@example.test", fontsize=12)
    doc.save(source)
    doc.close()

    doc = fitz.open(source)
    rect = fitz.Rect(doc[0].search_for("jane.example@example.test")[0])
    doc.close()

    review = build_review_session(
        source,
        [
            Redaction(
                page_num=0,
                rect=rect,
                original_text="jane.example@example.test",
                replacement_text="[EMAIL_1]",
                redaction_type="email",
            )
        ],
    )
    review.approve_pending()
    review.confirm_replacement_map()

    exported = export_reviewed_pdfs(review, tmp_path / "exports")

    assert [path.name for path in exported] == ["Jane Example Payment Advice 2025_redacted.pdf"]


def test_image_export_can_use_neutral_filename(tmp_path: Path):
    from PIL import Image

    source = tmp_path / "Jane Example Screenshot.jpg"
    Image.new("RGB", (120, 60), color="white").save(source)

    result = redact_image(
        source,
        tmp_path / "exports",
        default_redaction_config(),
        output_name="image_001_redacted.png",
    )

    assert result.error is None
    assert result.output_filename == "image_001_redacted.png"
    assert "Jane Example" not in result.output_filename
