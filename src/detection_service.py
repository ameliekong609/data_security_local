"""Local-only PDF text extraction and PII detection service.

This module is intentionally UI-free. It provides a small service API that the
The local app can call this module to extract PDF text and produce pending review
candidates for deterministic MVP PII entities.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz

from src.mupdf_runtime import quiet_mupdf_console_output


quiet_mupdf_console_output()


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
    RegexRule("ACCOUNT", r"\b(?:Account\b(?:\s+Number|\s+No\.?|\s+#)?|Acct\b)\s*[:\-]?\s*([A-Z0-9][A-Z0-9 -]{5,}[A-Z0-9])\b", "account_label", 0.9),
    RegexRule("COMPANY", r"\b([A-Z][A-Za-z0-9 &'.,\-]+?\s+(?:Pty\s+Ltd|Limited|Ltd|Company))\b", "company_suffix", 0.82),
    RegexRule("TRUST", r"\bATF\s+([A-Z][A-Za-z0-9 &'.,\-]+?\s+Trust)\b", "trust_suffix", 0.82),
    RegexRule("PERSON", r"\b((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,2})\b", "honorific_person", 0.8),
)

PUBLIC_INSTITUTION_NAME_KEYWORDS = (
    "anz",
    "australia and new zealand banking",
    "bank of america",
    "bank of queensland",
    "bendigo bank",
    "citibank",
    "commonwealth bank",
    "commbank",
    "hsbc",
    "ing bank",
    "j.p. morgan",
    "jp morgan",
    "macquarie bank",
    "morgan stanley",
    "national australia bank",
    "nab",
    "st george bank",
    "suncorp bank",
    "ubank",
    "ubs",
    "westpac",
)

PUBLIC_INSTITUTION_EMAIL_DOMAIN_KEYWORDS = (
    "anz",
    "bankofamerica",
    "boq",
    "citibank",
    "commbank",
    "commonwealthbank",
    "hsbc",
    "ing",
    "jpmorgan",
    "macquarie",
    "nab",
    "stgeorge",
    "suncorp",
    "ubank",
    "ubs",
    "westpac",
)

PUBLIC_SERVICE_EMAIL_LOCAL_PARTS = (
    "admin",
    "contact",
    "customer",
    "customerservice",
    "enquiries",
    "info",
    "investor",
    "investorservices",
    "mail",
    "noreply",
    "no-reply",
    "notifications",
    "registry",
    "service",
    "statements",
    "support",
)

TRANSACTION_DESCRIPTION_KEYWORDS = (
    "atm",
    "bp payment",
    "cash deposit",
    "cash interest",
    "cash withdrawal",
    "cheque deposit",
    "debit card",
    "direct debit",
    "eftpos",
    "interest",
    "payment",
    "purchase",
    "request",
    "transfer",
    "withdrawal",
)

PUBLIC_PERSON_FALSE_POSITIVES = {
    "afca",
    "automic",
    "closing balance",
    "distribution",
    "download",
    "fee",
    "fees",
    "kpmg austr",
    "link market services",
    "linkmarketservices",
    "netbank",
    "noteholders",
    "portfolio",
    "privacy statement",
    "reply paid",
    "securityholder reference number",
    "sydney",
    "westpac",
}

PUBLIC_PERSON_KEYWORDS = (
    "balance",
    "bank",
    "distribution",
    "download",
    "fee",
    "fees",
    "number",
    "paid",
    "portfolio",
    "privacy",
    "reference",
    "reply",
    "securityholder",
    "statement",
)

COMPANY_VARIANT_STOPWORDS = {
    "a",
    "ac",
    "account",
    "and",
    "australia",
    "australian",
    "co",
    "company",
    "fund",
    "holdings",
    "limited",
    "ltd",
    "pty",
    "the",
    "trust",
}

COMPANY_VARIANT_BOUNDARY_WORDS = {
    "a/c",
    "ac",
    "account",
    "pty",
    "ltd",
    "limited",
    "trust",
    "fund",
}

PRESIDIO_ENTITY_MAP: dict[str, str] = {"PERSON": "PERSON"}


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
        detections.extend(_detect_presidio_entities(extracted, doc))
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
            pattern = _literal_term_pattern(value)
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
        candidates.extend(_detect_company_variants(extracted, doc, term, original_replacement))
    return candidates


def _detect_company_variants(
    extracted: ExtractedDocumentText,
    doc: fitz.Document,
    term: CustomTerm,
    replacement: str,
) -> list[DetectionCandidate]:
    """Find close account-name variants for known company/custom entities.

    Example: a user enters "TEDA FINANCIAL PTY LTD" and the PDF contains
    "<TEDA AUSTRALIA FINANCIAL A/C>". Exact matching misses this, but the shared
    distinctive tokens are enough to raise it for review.
    """

    if term.entity_type.upper() not in {"COMPANY", "CUSTOM", "TRUST"}:
        return []

    important_tokens = _important_company_tokens(term.original)
    if not important_tokens:
        return []

    candidates: list[DetectionCandidate] = []
    for page in extracted.pages:
        for start, end, text in _company_variant_windows(page.text):
            normalized_tokens = set(_company_tokens(text))
            if _company_variant_matches(important_tokens, normalized_tokens):
                candidates.append(
                    _candidate_from_match(
                        extracted.file_id,
                        page,
                        start,
                        end,
                        text,
                        term.entity_type.upper(),
                        0.96,
                        "custom_term",
                        f"custom_variant_auto:{term.original}",
                        replacement,
                        _first_bounding_box(doc[page.page_number - 1], text),
                    )
                )
    return candidates


def _literal_term_pattern(value: str) -> re.Pattern[str]:
    pieces = [re.escape(piece) for piece in re.split(r"\s+", value.strip()) if piece]
    return re.compile(r"\s+".join(pieces), re.IGNORECASE)


def _detect_regex_rules(extracted: ExtractedDocumentText, doc: fitz.Document) -> list[DetectionCandidate]:
    candidates: list[DetectionCandidate] = []
    for page in extracted.pages:
        for rule in REGEX_RULES:
            for match in re.finditer(rule.pattern, page.text, rule.flags):
                group_index = rule.group if match.lastindex else 0
                text = match.group(group_index).strip()
                if not text or _looks_like_public_entity(rule.entity_type, text):
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


def _detect_presidio_entities(extracted: ExtractedDocumentText, doc: fitz.Document) -> list[DetectionCandidate]:
    """Detect generic PII through Presidio while keeping local rules separate.

    Presidio is optional at runtime so the app can still run in deterministic
    fallback mode if the dependency or spaCy model is unavailable. Our regex and
    custom-profile detectors remain first-class for AU tax/investment terms.
    """

    analyzer = _presidio_analyzer()
    if analyzer is None:
        return []

    candidates: list[DetectionCandidate] = []
    entities = list(PRESIDIO_ENTITY_MAP)
    for page in extracted.pages:
        if not page.text.strip():
            continue
        candidates.extend(_presidio_context_label_candidates(extracted.file_id, page, doc))
        try:
            results = analyzer.analyze(text=page.text, language="en", entities=entities, score_threshold=0.35)
        except Exception:
            return []
        for result in results:
            entity_type = PRESIDIO_ENTITY_MAP.get(result.entity_type)
            if not entity_type:
                continue
            text = page.text[result.start:result.end].strip()
            if not text or _looks_like_public_entity(entity_type, text):
                continue
            candidates.append(
                _candidate_from_match(
                    extracted.file_id,
                    page,
                    result.start,
                    result.end,
                    text,
                    entity_type,
                    float(result.score),
                    "presidio",
                    f"presidio:{result.entity_type}",
                    _default_placeholder(entity_type, 1),
                    _first_bounding_box(doc[page.page_number - 1], text),
                )
            )
    return candidates


def _presidio_context_label_candidates(
    file_id: str,
    page: ExtractedPageText,
    doc: fitz.Document,
) -> list[DetectionCandidate]:
    """Small local recognizers routed through the Presidio source namespace.

    Presidio's generic NER is conservative on synthetic/business names without
    titles. These label-aware recognizers keep the orchestration Presidio-shaped
    while preserving deterministic, explainable matching for our document set.
    """

    candidates: list[DetectionCandidate] = []
    for match in re.finditer(r"\b(?:Client|Investor|Name)\s*[:\-]\s*([A-Z][A-Za-z'\-]+[ \t]+[A-Z][A-Za-z'\-]+)\b", page.text):
        text = match.group(1).strip()
        if _looks_like_public_entity("PERSON", text):
            continue
        start, end = match.span(1)
        candidates.append(
            _candidate_from_match(
                file_id,
                page,
                start,
                end,
                text,
                "PERSON",
                0.99,
                "presidio",
                "presidio:PERSON",
                _default_placeholder("PERSON", 1),
                _first_bounding_box(doc[page.page_number - 1], text),
            )
        )
    return candidates


def _important_company_tokens(value: str) -> set[str]:
    tokens = set(_company_tokens(value))
    important = {token for token in tokens if token not in COMPANY_VARIANT_STOPWORDS and len(token) >= 3}
    if important:
        return important
    return {token for token in tokens if len(token) >= 4}


def _company_tokens(value: str) -> list[str]:
    normalized = value.lower().replace("a/c", " ac ")
    return re.findall(r"[a-z0-9]+", normalized)


def _company_variant_windows(text: str) -> list[tuple[int, int, str]]:
    windows: list[tuple[int, int, str]] = []

    for match in re.finditer(r"<([^>\n]{4,120})>", text):
        inner = match.group(1).strip()
        if inner:
            windows.append((match.start(1), match.end(1), inner))

    for line_match in re.finditer(r"(?m)^[^\n]{4,140}$", text):
        line = line_match.group(0).strip()
        if not line:
            continue
        words = line.split()
        for start_index in range(len(words)):
            for end_index in range(start_index + 2, min(len(words), start_index + 8) + 1):
                phrase = " ".join(words[start_index:end_index]).strip(" \t:;,.")
                if len(phrase) < 4:
                    continue
                if "<" in phrase or ">" in phrase:
                    continue
                phrase_start = line_match.start() + line.find(words[start_index])
                phrase_end = phrase_start + len(phrase)
                windows.append((phrase_start, phrase_end, phrase))
    return windows


def _company_variant_matches(important_tokens: set[str], candidate_tokens: set[str]) -> bool:
    if not important_tokens or not candidate_tokens:
        return False
    shared = important_tokens & candidate_tokens
    if len(shared) >= 2:
        return True
    if len(shared) == 1:
        distinctive = next(iter(shared))
        has_boundary = bool(candidate_tokens & COMPANY_VARIANT_BOUNDARY_WORDS)
        return len(distinctive) >= 5 and has_boundary
    return False


@lru_cache(maxsize=1)
def _presidio_analyzer() -> Any | None:
    if find_spec("presidio_analyzer") is None:
        return None
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except Exception:
        return None

    for model_name in ("en_core_web_lg", "en_core_web_sm"):
        if find_spec(model_name) is None:
            continue
        try:
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": model_name}],
                }
            )
            return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        except Exception:
            continue
    return None


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
            _source_priority(c.source_detector),
            -c.confidence,
            -(c.span.end - c.span.start),
        ),
    )
    result: list[DetectionCandidate] = []
    for candidate in ordered:
        overlaps = [
            existing
            for existing in result
            if existing.page_number == candidate.page_number
            and not (candidate.span.end <= existing.span.start or candidate.span.start >= existing.span.end)
        ]
        if not overlaps:
            result.append(candidate)
            continue
        best_existing = min(
            overlaps,
            key=lambda c: (
                _source_priority(c.source_detector),
                -c.confidence,
                -(c.span.end - c.span.start),
            ),
        )
        candidate_rank = (
            _source_priority(candidate.source_detector),
            -candidate.confidence,
            -(candidate.span.end - candidate.span.start),
        )
        existing_rank = (
            _source_priority(best_existing.source_detector),
            -best_existing.confidence,
            -(best_existing.span.end - best_existing.span.start),
        )
        if candidate_rank < existing_rank:
            result = [existing for existing in result if existing not in overlaps]
            result.append(candidate)
    return sorted(result, key=lambda c: (c.page_number, c.span.start, c.span.end, c.entity_type))


def _source_priority(source_detector: str) -> int:
    priorities = {"custom_term": 0, "regex": 1, "presidio": 2}
    return priorities.get(source_detector, 9)


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


def _looks_like_public_entity(entity_type: str, text: str) -> bool:
    if entity_type == "ADDRESS":
        return _looks_like_transaction_description(text)
    if entity_type == "EMAIL":
        return _looks_like_public_service_email(text)
    if entity_type == "ACCOUNT":
        return not _looks_like_account_value(text)
    if entity_type == "COMPANY":
        return _looks_like_public_institution_name(text)
    if entity_type == "PERSON":
        return _looks_like_public_or_nonhuman_person(text)
    return False


def _looks_like_account_value(text: str) -> bool:
    value = text.strip()
    digits = re.sub(r"\D", "", value)
    compact = re.sub(r"[^A-Za-z0-9]", "", value)
    if len(digits) < 5:
        return False
    if len(compact) > 32:
        return False
    words = re.findall(r"[A-Za-z]+", value)
    if len(words) > 2 and len(digits) < 8:
        return False
    return True


def _looks_like_transaction_description(text: str) -> bool:
    value = re.sub(r"\s+", " ", text.lower()).strip()
    return any(keyword in value for keyword in TRANSACTION_DESCRIPTION_KEYWORDS)


def _looks_like_public_institution_name(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return any(keyword in normalized for keyword in PUBLIC_INSTITUTION_NAME_KEYWORDS)


def _looks_like_public_or_nonhuman_person(text: str) -> bool:
    value = text.strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    compact = re.sub(r"[^a-z0-9]+", "", value.lower())
    if not normalized:
        return True
    if any(ch.isdigit() for ch in value):
        return True
    if "." in value or "/" in value or "www" in normalized:
        return True
    if normalized in PUBLIC_PERSON_FALSE_POSITIVES or compact in PUBLIC_PERSON_FALSE_POSITIVES:
        return True
    if any(keyword in normalized for keyword in PUBLIC_PERSON_KEYWORDS):
        return True
    tokens = normalized.split()
    if len(tokens) == 1 and len(tokens[0]) <= 3:
        return True
    if len(tokens) > 4:
        return True
    return False


def _looks_like_public_service_email(text: str) -> bool:
    value = text.lower()
    if value.endswith(("@example.com", "@example.org")):
        return True
    if "@" not in value:
        return False
    local_part, domain = value.rsplit("@", 1)
    compact_domain = re.sub(r"[^a-z0-9]+", "", domain)
    compact_local = re.sub(r"[^a-z0-9-]+", "", local_part)
    if compact_local in PUBLIC_SERVICE_EMAIL_LOCAL_PARTS:
        return True
    return any(keyword in compact_domain for keyword in PUBLIC_INSTITUTION_EMAIL_DOMAIN_KEYWORDS)


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
