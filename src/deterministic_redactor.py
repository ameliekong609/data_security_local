import re
import fitz
from dataclasses import dataclass
from src.config_loader import KeywordRule, AddressRule


@dataclass
class Redaction:
    page_num: int
    rect: fitz.Rect
    original_text: str
    replacement_text: str
    redaction_type: str  # "keyword", "address", "field"
    redaction_id: str = ""  # unique ID for reversibility


def _search_text_on_page(page: fitz.Page, text: str, case_sensitive: bool = False) -> list[fitz.Rect]:
    """Search for text on a page and return bounding rectangles."""
    flags = 0 if case_sensitive else re.IGNORECASE
    quads = page.search_for(text, flags=flags)
    return [fitz.Rect(q) for q in quads]


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace (including newlines) into single spaces."""
    return re.sub(r'\s+', ' ', text).strip()


def find_keyword_redactions(doc: fitz.Document, rules: list[KeywordRule]) -> list[Redaction]:
    """Find all keyword/name matches and return redaction objects."""
    redactions = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for rule in rules:
            rects = _search_text_on_page(page, rule.pattern, rule.case_sensitive)
            for rect in rects:
                redactions.append(Redaction(
                    page_num=page_num,
                    rect=rect,
                    original_text=rule.pattern,
                    replacement_text=rule.replacement,
                    redaction_type="keyword",
                ))
    return redactions


def find_address_redactions(doc: fitz.Document, rules: list[AddressRule]) -> list[Redaction]:
    """Find address matches including multi-line variants."""
    redactions = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for rule in rules:
            # Try the main pattern first
            all_patterns = [rule.pattern] + rule.variants
            for pattern_text in all_patterns:
                # For multi-line patterns, search each line separately and combine
                lines = pattern_text.split('\n')
                if len(lines) == 1:
                    rects = _search_text_on_page(page, pattern_text, rule.case_sensitive)
                    for rect in rects:
                        redactions.append(Redaction(
                            page_num=page_num,
                            rect=rect,
                            original_text=pattern_text,
                            replacement_text=rule.replacement,
                            redaction_type="address",
                        ))
                else:
                    # Multi-line: find each line and merge into a combined rect
                    line_rects = []
                    all_found = True
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        rects = _search_text_on_page(page, line, rule.case_sensitive)
                        if rects:
                            line_rects.append(rects[0])
                        else:
                            all_found = False
                            break
                    if all_found and line_rects:
                        # Create individual redactions for each line of the address
                        addr_lines = [l.strip() for l in lines if l.strip()]
                        for i, rect in enumerate(line_rects):
                            replacement = rule.replacement if i == 0 else ""
                            original = addr_lines[i] if i < len(addr_lines) else ""
                            redactions.append(Redaction(
                                page_num=page_num,
                                rect=rect,
                                original_text=original,
                                replacement_text=replacement,
                                redaction_type="address",
                            ))
    return redactions


def _rects_overlap(r1: fitz.Rect, r2: fitz.Rect, threshold: float = 0.5) -> bool:
    """Check if two rects overlap significantly."""
    intersection = r1 & r2
    if intersection.is_empty:
        return False
    area1 = r1.width * r1.height
    if area1 == 0:
        return False
    return (intersection.width * intersection.height) / area1 > threshold


def deduplicate_redactions(redactions: list[Redaction]) -> list[Redaction]:
    """Remove duplicate/overlapping redactions, preferring more specific ones."""
    # Sort by specificity: address > keyword with longer patterns first
    result = []
    for r in redactions:
        is_duplicate = False
        for existing in result:
            if r.page_num == existing.page_num and _rects_overlap(r.rect, existing.rect):
                is_duplicate = True
                break
        if not is_duplicate:
            result.append(r)
    return result
