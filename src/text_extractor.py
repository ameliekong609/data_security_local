import fitz
from dataclasses import dataclass


@dataclass
class PageText:
    page_num: int
    full_text: str
    text_dict: dict  # from page.get_text("dict") - has position info


def extract_text(pdf_path: str) -> list[PageText]:
    doc = fitz.open(pdf_path)
    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        full_text = page.get_text("text")
        text_dict = page.get_text("dict")
        pages.append(PageText(
            page_num=page_num,
            full_text=full_text,
            text_dict=text_dict,
        ))
    doc.close()
    return pages
