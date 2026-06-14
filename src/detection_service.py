"""Local-only PDF text extraction and PII detection service.

This module is intentionally UI-free. It provides a small service API that the
Streamlit/local app can call to extract PDF text and produce pending review
candidates for deterministic MVP PII entities.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import fitz


@dataclass(frozen=True)
class TextSpan:
    """Character span within the extracted text for one PDF page."""

    start: int
    end: int


@dataclass(frozen=True)
class BoundingBox:
    """PDF-space bounding box for a detection, when PyMuPDF can locate it."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class ExtractedPageText:
    """Extracted text and raw PyMuPDF text metadata for one page."""

    page_number: int
    text: str
    text_dict: dict


@dataclass(frozen=True)
class ExtractedDocumentText:
    """Local extraction result for a PDF file."""

    file_id: str
    pages: list[ExtractedPageText]


@dataclass(frozen=True)
class CustomTerm:
    """A locally supplied sensitive term from a redaction profile."""

    original: str
    entity_type: str = "CUSTOM"
    replacement_label: str | None = None
    variants: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(frozen=True)
class DetectionCandidate:
    """Pending review candidate produced by local detectors."""

    file_id: str
    page_number: int
    text: str
    entity_type: str
    span: TextSpan
    context: str
    confidence: float
    source_detector: str
    source_rule: str
    proposed_placeholder: str
    bounding_box: BoundingBox | None = None
    status: str = "pending"


@dataclass(frozen=True)
class DetectionResult:
    """PII detection response for a single PDF."""

    file_id: str
    pages: list[ExtractedPageText]
    detections: list[DetectionCandidate]


@dataclass(frozen=True)
class RegexRule:
    entity_type: str
    pattern: str
    source_rule: str
    confidence: float = 0.85
    flags: int = re.IGNORECASE | re.MULTILINE
    group: int = 1


REGEX_RULES: tuple[RegexRule, ...] = (
    RegexRule("EMAIL", r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b", "email", 0.98),
    RegexRule("PHONE", r"(?<!\d)(\+?61\s?4\d{2}\s?\d{3}\s?\d{3}|\+?61\s?[2378]\s?\d{4}\s?\d{4}|0[2378]\s?\d{4}\s?\d{4}|04\d{2}\s?\d{3}\s?\d{3})(?!\d)", "au_phone", 0.9),
    RegexRule("DOB", r"\b(?:DOB|Date\s+of\s+Birth)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", "dob_label", 0.95),
    RegexRule("ABN", r"\bABN\s*[:\-]?\s*(\d{2}\s?\d{3}\s?\d{3}\s?\d{3})\b", "abn_like", 0.92),
    RegexRule("TFN", r"\b(?:TFN|Tax\s+File\s+Number)\s*[:\-]?\s*(\d{3}\s?\d{3}\s?\d{3})\b", "tfn_label", 0.95),
    RegexRule("ACCOUNT", r"\b(?:Account(?:\s+Number|\s+No\.?|\s+#)?|Acct)\s*[:\-]?\s*([A-Z0-9][A-Z0-9 -]{5,}[A-Z0-9])\b", "account_label", 0.9),
    RegexRule("INVESTOR_ID", r"\b(?:Investor\s*(?:No\.?|Number|ID)|Investment\s+ID)\s*[:\-]?\s*([A-Z]{0,4}\d[A-Z0-9-]{3,})\b", "investor_id_label", 0.92),
    RegexRule("CLIENT_ID", r"\b(?:Client\s*(?:ID|No\.?|Number)|Customer\s*ID)\s*[:\-]?\s*([A-Z]{1,5}-?\d[A-Z0-9-]{3,})\b", "client_id_label", 0.92),
    RegexRule("ADDRESS", r"\b(\d{1,5}[A-Z]?[/\-]?\d*\s+[A-Z][A-Z .'\-]+(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Court|Ct|Place|Pl|Boulevard|Blvd|Parade|Pde|Terrace|Tce)\b[^\n,]*(?:,?\s+[A-Z][A-Za-z .'\-]+\s+(?:NSW|VIC|QLD|SA|WA|TAS|ACT|NT)\s+\d{4})?)", "au_address_like", 0.78),
    RegexRule("COMPANY", r"\b([A-Z][A-Za-z0-9 &'.,\-]+?\s+(?:Pty\s+Ltd|Limited|Ltd|Company))\b", "company_suffix", 0.82),
    RegexRule("TRUST", r"\bATF\s+([A-Z][A-Za-z0-9 &'.,\-]+?\s+Trust)\b", "trust_suffix", 0.82),
    RegexRule("PERSON", r"\b(?:Mr|Mrs|Ms|Miss|Dr)\s+([A-Z][A-Za-z'\-]+\s+[A-Z][A-Za-z'\-]+)\b", "honorific_person", 0.7),
)


def extract_pdf_text(pdf_path: str | Path, *, password: str | None = None) -> ExtractedDocumentText:
    """Extract text from a local PDF without external services."""

    path = Path(pdf_path)
    pages: list[ExtractedPageText] = []
    doc = fitz.open(str(path))
    try:
        if doc.is_encrypted and not doc.authenticate(password or ""):
            raise ValueError("PDF is encrypted and could not be opened with the supplied password")
        for page_index in range(len(doc)):
            page = doc[page_index]
            pages.append(
                ExtractedPageText(
                    page_number=page_index + 1,
                    text=page.get_text("text"),
                    text_dict=page.get_text("dict"),
                )
            )
    finally:
        doc.close()
    return ExtractedDocumentText(file_id=path.name, pages=pages)


def detect_pdf_pii(
    pdf_path: str | Path,
    *,
    custom_terms: Sequence[CustomTerm] | None = None,
    password: str | None = None,
) -> DetectionResult:
    """Extract a PDF and return local pending PII review candidates."""

    path = Path(pdf_path)
    extracted = extract_pdf_text(path, password=password)
    doc = fitz.open(str(path))
    try:
        if doc.is_encrypted and not doc.authenticate(password or ""):
            raise ValueError("PDF is encrypted and could not be opened with the supplied password")
        detections = _detect_custom_terms(extracted, doc, custom_terms or [])
        detections.extend(_detect_regex_rules(extracted, doc))
    finally:
        doc.close()

    deduped = _deduplicate_candidates(detections)
    numbered = _renumber_regex_placeholders(deduped)
    return DetectionResult(file_id=extracted.file_id, pages=extracted.pages, detections=numbered)


def _detect_custom_terms(
    extracted: ExtractedDocumentText,
    doc: fitz.Document,
    custom_terms: Sequence[CustomTerm],
) -> list[DetectionCandidate]:
    candidates: list[DetectionCandidate] = []
    for term in custom_terms:
        original_replacement = term.replacement_label or _default_placeholder(term.entity_type, 1)
        term_values = [(term.original, term.entity_type, f"custom_term:{term.original}")]
        term_values.extend((variant, "CUSTOM", f"custom_variant:{term.original}") for variant in term.variants)
        for value, entity_type, source_rule in term_values:
            if not value:
                continue
            pattern = re.compile(re.escape(value), re.IGNORECASE)
            for page in extracted.pages:
                for match in pattern.finditer(page.text):
                    candidates.append(
                        _candidate_from_match(
                            extracted.file_id,
                            page,
                            match.start(),
                            match.end(),
                            match.group(0),
                            entity_type.upper(),
                            1.0,
                            "custom_term",
                            source_rule,
                            original_replacement,
                            _first_bounding_box(doc[page.page_number - 1], match.group(0)),
                        )
                    )
    return candidates


def _detect_regex_rules(extracted: ExtractedDocumentText, doc: fitz.Document) -> list[DetectionCandidate]:
    candidates: list[DetectionCandidate] = []
    for page in extracted.pages:
        for rule in REGEX_RULES:
            for match in re.finditer(rule.pattern, page.text, rule.flags):
                group_index = rule.group if match.lastindex else 0
                text = match.group(group_index).strip()
                if not text or _looks_like_public_service_email(text):
                    continue
                start, end = match.span(group_index)
                candidates.append(
                    _candidate_from_match(
                        extracted.file_id,
                        page,
                        start,
                        end,
                        text,
                        rule.entity_type,
                        rule.confidence,
                        "regex",
                        rule.source_rule,
                        _default_placeholder(rule.entity_type, 1),
                        _first_bounding_box(doc[page.page_number - 1], text),
                    )
                )
    return candidates


def _candidate_from_match(
    file_id: str,
    page: ExtractedPageText,
    start: int,
    end: int,
    text: str,
    entity_type: str,
    confidence: float,
    source_detector: str,
    source_rule: str,
    proposed_placeholder: str,
    bounding_box: BoundingBox | None,
) -> DetectionCandidate:
    return DetectionCandidate(
        file_id=file_id,
        page_number=page.page_number,
        text=text,
        entity_type=entity_type,
        span=TextSpan(start=start, end=end),
        context=_context_window(page.text, start, end),
        confidence=confidence,
        source_detector=source_detector,
        source_rule=source_rule,
        proposed_placeholder=proposed_placeholder,
        bounding_box=bounding_box,
    )


def _context_window(text: str, start: int, end: int, radius: int = 48) -> str:
    prefix_start = max(0, start - radius)
    suffix_end = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[prefix_start:suffix_end]).strip()


def _first_bounding_box(page: fitz.Page, text: str) -> BoundingBox | None:
    matches = page.search_for(text)
    if not matches:
        return None
    rect = fitz.Rect(matches[0])
    return BoundingBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


def _deduplicate_candidates(candidates: Iterable[DetectionCandidate]) -> list[DetectionCandidate]:
    ordered = sorted(
        candidates,
        key=lambda c: (
            c.page_number,
            c.span.start,
            -(c.span.end - c.span.start),
            -c.confidence,
            0 if c.source_detector == "custom_term" else 1,
        ),
    )
    result: list[DetectionCandidate] = []
    for candidate in ordered:
        overlaps = [
            existing
            for existing in result
            if existing.page_number == candidate.page_number
            and existing.entity_type == candidate.entity_type
            and not (candidate.span.end <= existing.span.start or candidate.span.start >= existing.span.end)
        ]
        if not overlaps:
            result.append(candidate)
            continue
        best_existing = max(overlaps, key=lambda c: (c.confidence, c.span.end - c.span.start))
        if candidate.confidence > best_existing.confidence and candidate.source_detector == "custom_term":
            result = [existing for existing in result if existing not in overlaps]
            result.append(candidate)
    return sorted(result, key=lambda c: (c.page_number, c.span.start, c.span.end, c.entity_type))


def _renumber_regex_placeholders(candidates: list[DetectionCandidate]) -> list[DetectionCandidate]:
    counters: dict[str, int] = {}
    numbered: list[DetectionCandidate] = []
    for candidate in candidates:
        if candidate.source_detector == "custom_term":
            numbered.append(candidate)
            continue
        counters[candidate.entity_type] = counters.get(candidate.entity_type, 0) + 1
        numbered.append(
            DetectionCandidate(
                **{
                    **asdict(candidate),
                    "span": candidate.span,
                    "bounding_box": candidate.bounding_box,
                    "proposed_placeholder": _default_placeholder(candidate.entity_type, counters[candidate.entity_type]),
                }
            )
        )
    return numbered


def _default_placeholder(entity_type: str, index: int) -> str:
    return f"[{entity_type.upper()}_{index}]"


def _looks_like_public_service_email(text: str) -> bool:
    # Keep the service conservative: app-specific allow lists can be layered later.
    return text.lower().endswith(("@example.com", "@example.org"))


def _to_jsonable(result: DetectionResult) -> dict:
    return asdict(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PDF text and detect MVP PII locally")
    parser.add_argument("pdf", type=Path, help="Synthetic/local PDF path")
    parser.add_argument(
        "--custom-term",
        action="append",
        default=[],
        metavar="TEXT:ENTITY:PLACEHOLDER",
        help="Local custom term, e.g. 'Jane Example:PERSON:[PERSON_1]'",
    )
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    custom_terms = []
    for raw in args.custom_term:
        parts = raw.split(":", 2)
        if len(parts) == 1:
            custom_terms.append(CustomTerm(original=parts[0]))
        elif len(parts) == 2:
            custom_terms.append(CustomTerm(original=parts[0], entity_type=parts[1]))
        else:
            custom_terms.append(CustomTerm(original=parts[0], entity_type=parts[1], replacement_label=parts[2]))

    print(json.dumps(_to_jsonable(detect_pdf_pii(args.pdf, custom_terms=custom_terms, password=args.password)), indent=2))


if __name__ == "__main__":
    main()
