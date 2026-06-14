"""Local custom-term detector for review-candidate generation.

Custom terms are local profile data. This module never exports profile contents;
it only converts in-memory profile terms into pending DetectionCandidate objects
that a review layer can approve/reject before redaction or mapping export.
"""

from dataclasses import dataclass, field
import re
from typing import Iterable

import fitz

from src.detection_models import DetectionCandidate
from src.deterministic_redactor import Redaction, _rects_overlap


SOURCE_DETECTOR = "custom_term"


@dataclass(frozen=True)
class CustomTerm:
    term_id: str
    entity_type: str
    original: str
    replacement: str
    variants: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict) -> "CustomTerm":
        return cls(
            term_id=str(data.get("term_id", "")),
            entity_type=str(data["entity_type"]),
            original=str(data["original"]),
            replacement=str(data["replacement"]),
            variants=[str(v) for v in data.get("variants", [])],
        )

    def search_terms(self) -> list[str]:
        """Return unique original+variant search strings in deterministic order."""
        seen = set()
        terms = []
        for value in [self.original, *self.variants]:
            value = value.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                terms.append(value)
        return terms


class CustomTermDetector:
    """Detect local profile terms and variants in PDF pages."""

    def __init__(self, terms: Iterable[CustomTerm | dict]):
        self.terms = [
            term if isinstance(term, CustomTerm) else CustomTerm.from_mapping(term)
            for term in terms
        ]

    @classmethod
    def from_profile(cls, profile: dict) -> "CustomTermDetector":
        """Build a detector from the accepted local profile schema."""
        return cls(profile.get("terms", []))

    def detect_document(self, doc: fitz.Document, file_id: str) -> list[DetectionCandidate]:
        candidates: list[DetectionCandidate] = []
        sequence = 0

        for page_number in range(len(doc)):
            page = doc[page_number]
            full_text = page.get_text("text")
            for term_index, term in enumerate(self.terms):
                for search_term in term.search_terms():
                    rects = _search_case_insensitive(page, search_term)
                    for rect in rects:
                        actual_text = _matched_text(page, rect, search_term)
                        candidates.append(DetectionCandidate(
                            file_id=file_id,
                            page_number=page_number,
                            text=actual_text,
                            entity_type=term.entity_type,
                            bounding_box=fitz.Rect(rect),
                            proposed_replacement=term.replacement,
                            source_detector=SOURCE_DETECTOR,
                            confidence=_confidence(search_term, term.original),
                            context=_context_for_match(full_text, actual_text),
                            status="pending",
                            term_id=term.term_id,
                        ))
                        # Stash deterministic ordering on the instance without
                        # exposing it as part of the inter-step candidate schema.
                        setattr(candidates[-1], "_custom_term_order", (page_number, term_index, sequence))
                        sequence += 1

        return deduplicate_candidates(candidates)


def candidates_to_redactions(candidates: Iterable[DetectionCandidate]) -> list[Redaction]:
    """Convert approved custom-term candidates to legacy Redaction objects.

    The caller is responsible for passing only reviewed/approved candidates.
    Pending candidates are intentionally ignored so detection does not bypass
    human review.
    """
    redactions = []
    for candidate in candidates:
        if candidate.source_detector != SOURCE_DETECTOR or candidate.status != "approved":
            continue
        redactions.append(Redaction(
            page_num=candidate.page_number,
            rect=fitz.Rect(candidate.bounding_box),
            original_text=candidate.text,
            replacement_text=candidate.proposed_replacement,
            redaction_type="custom_term",
        ))
    return redactions


def deduplicate_candidates(candidates: Iterable[DetectionCandidate]) -> list[DetectionCandidate]:
    """Remove overlapping candidates deterministically.

    For overlaps on the same file/page, prefer the more specific match (longer
    matched text), then higher confidence, then original input order. Keep
    non-overlapping candidates and candidates on different pages.
    """
    indexed = list(enumerate(candidates))
    indexed.sort(
        key=lambda item: (
            item[1].file_id,
            item[1].page_number,
            -len(item[1].text),
            -item[1].confidence,
            item[0],
        )
    )

    accepted: list[tuple[int, DetectionCandidate]] = []
    for original_index, candidate in indexed:
        if any(_same_location(candidate, existing) for _, existing in accepted):
            continue
        accepted.append((original_index, candidate))

    accepted.sort(key=lambda item: (
        item[1].file_id,
        item[1].page_number,
        item[1].bounding_box.y0,
        item[1].bounding_box.x0,
        item[0],
    ))
    return [candidate for _, candidate in accepted]


def _same_location(candidate: DetectionCandidate, existing: DetectionCandidate) -> bool:
    return (
        candidate.file_id == existing.file_id
        and candidate.page_number == existing.page_number
        and _rects_overlap(candidate.bounding_box, existing.bounding_box)
    )


def _search_case_insensitive(page: fitz.Page, search_term: str) -> list[fitz.Rect]:
    # PyMuPDF text search is already case-insensitive for ASCII. Search the
    # supplied spelling first to preserve reliable PDF text positioning.
    return [fitz.Rect(rect) for rect in page.search_for(search_term)]


def _matched_text(page: fitz.Page, rect: fitz.Rect, fallback: str) -> str:
    # ``page.get_textbox(rect)`` can bleed into adjacent line fragments when the
    # search rectangle touches nearby glyphs. The profile term/variant is the
    # reviewed local source of truth, so report the configured matched value while
    # preserving the PyMuPDF rectangle for PDF redaction.
    return fallback


def _confidence(search_term: str, original: str) -> float:
    return 0.99 if search_term.casefold() == original.casefold() else 0.97


def _context_for_match(full_text: str, matched_text: str, window: int = 60) -> str:
    match = re.search(re.escape(matched_text), full_text, re.IGNORECASE)
    if not match:
        return full_text[: window * 2].strip()
    start = max(match.start() - window, 0)
    end = min(match.end() + window, len(full_text))
    return full_text[start:end].strip()
