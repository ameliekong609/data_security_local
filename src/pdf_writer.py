import fitz
from src.deterministic_redactor import Redaction


def apply_redactions(pdf_path: str, output_path: str, redactions: list[Redaction], password: str = "") -> int:
    """Apply all redactions to a PDF and save to output_path.
    Returns the number of redactions applied.
    """
    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        doc.authenticate(password)

    # Group redactions by page
    by_page: dict[int, list[Redaction]] = {}
    for r in redactions:
        by_page.setdefault(r.page_num, []).append(r)

    count = 0
    for page_num, page_redactions in by_page.items():
        page = doc[page_num]
        for r in page_redactions:
            # Clean replacement text -- remove newlines as they don't render
            # in redaction annotations
            clean_text = r.replacement_text.replace("\n", " ").strip()

            # Calculate a reasonable font size based on rect height
            rect_height = r.rect.height
            fontsize = max(6, min(rect_height * 0.8, 12))

            page.add_redact_annot(
                r.rect,
                text=clean_text,
                fontsize=fontsize,
                fill=(1, 1, 1),  # white background
            )
            count += 1
        # Apply all redactions on this page at once
        page.apply_redactions()

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
    return count
