"""Reusable local services for the human review workflow."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
from datetime import datetime
from typing import Iterable

import fitz

from src.config_loader import RedactionConfig
from src.detection_service import CustomTerm, DetectionCandidate, detect_pdf_pii
from src.deterministic_redactor import (
    Redaction,
    deduplicate_redactions,
    find_address_redactions,
    find_keyword_redactions,
)
from src.pattern_redactor import find_field_redactions
from src.review_state import DetectionStatus, ReviewDetection, ReviewSession, build_review_session
from src.pdf_writer import apply_redactions


def collect_pdf_files(selected_files: Iterable[str | Path] = (), folder: str | Path | None = None) -> list[Path]:
    """Return local PDF paths from explicit selections and/or a folder."""

    paths: list[Path] = []
    for item in selected_files:
        path = Path(item).expanduser()
        if path.is_file() and path.suffix.lower() == ".pdf":
            paths.append(path)

    if folder:
        folder_path = Path(folder).expanduser()
        if folder_path.is_dir():
            paths.extend(sorted(folder_path.glob("*.pdf")))
            paths.extend(sorted(folder_path.glob("*.PDF")))

    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(resolved)
    return unique_paths


def detect_redactions_for_pdf(pdf_path: str | Path, config: RedactionConfig, password: str = "") -> list[Redaction]:
    """Run local rule-based detection for one PDF without exporting."""

    path = Path(pdf_path)
    doc = fitz.open(path)
    try:
        if doc.is_encrypted and not doc.authenticate(password):
            raise ValueError(f"Could not open encrypted PDF: {path.name}")
        redactions = deduplicate_redactions(
            find_address_redactions(doc, config.address_rules)
            + find_keyword_redactions(doc, config.keyword_rules)
            + find_field_redactions(doc, config.field_rules)
        )
    finally:
        doc.close()

    for index, redaction in enumerate(redactions):
        if not redaction.redaction_id:
            raw = f"{path.name}|{redaction.page_num}|{redaction.rect}|{redaction.original_text}|{index}"
            redaction.redaction_id = "D-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return redactions


def build_review_for_pdfs(pdf_paths: list[Path], config: RedactionConfig) -> ReviewSession:
    """Detect all selected PDFs and return a combined review session."""

    combined = ReviewSession()
    for pdf_path in pdf_paths:
        redactions = detect_redactions_for_pdf(pdf_path, config)
        session = build_review_session(pdf_path, redactions)
        combined.detections.extend(session.detections)
    return combined


def build_enhanced_review_for_pdf(
    pdf_path: str | Path,
    password: str = "",
    custom_terms: list[CustomTerm] | None = None,
) -> ReviewSession:
    """Build review state with the stronger local detection service.

    This path combines regex/context rules and optional local Presidio detection.
    It is still local-only and produces pending review items for the UI.
    """

    path = Path(pdf_path)
    result = detect_pdf_pii(path, password=password, custom_terms=custom_terms or [])
    review = ReviewSession()
    for candidate in result.detections:
        detection = _candidate_to_review_detection(path, candidate)
        if detection is not None:
            review.detections.append(detection)
    return review


def _candidate_to_review_detection(pdf_path: Path, candidate: DetectionCandidate) -> ReviewDetection:
    rect_tuple = None
    if candidate.bounding_box is not None:
        rect_tuple = (
            candidate.bounding_box.x0,
            candidate.bounding_box.y0,
            candidate.bounding_box.x1,
            candidate.bounding_box.y1,
        )
    detection_id = hashlib.sha256(
        f"{pdf_path}|{candidate.page_number}|{candidate.text}|{candidate.entity_type}|{candidate.span.start}".encode("utf-8")
    ).hexdigest()[:12]
    return ReviewDetection(
        detection_id=f"D-{detection_id}",
        document_path=str(pdf_path),
        document_name=pdf_path.name,
        page_num=max(0, candidate.page_number - 1),
        original_text=candidate.text,
        entity_type=candidate.entity_type.lower(),
        replacement_label=candidate.proposed_placeholder,
        context_before="",
        context_after=candidate.context,
        status=DetectionStatus.PENDING,
        source=candidate.source_detector,
        rect=rect_tuple,
    )


def add_custom_detection_from_pdf(
    review: ReviewSession,
    *,
    document_path: str | Path,
    page_num: int,
    original_text: str,
    entity_type: str,
    replacement_label: str,
) -> str:
    """Add a custom detection and anchor it to the first local PDF match if present."""

    path = Path(document_path)
    context_before = ""
    context_after = ""
    rect_tuple = None
    doc = fitz.open(path)
    try:
        page = doc[page_num]
        page_text = page.get_text("text")
        lower_text = page_text.lower()
        lower_original = original_text.lower()
        start = lower_text.find(lower_original)
        if start >= 0:
            context_before = page_text[max(0, start - 80):start]
            context_after = page_text[start + len(original_text):start + len(original_text) + 80]
        rects = page.search_for(original_text)
        if rects:
            rect = fitz.Rect(rects[0])
            rect_tuple = (rect.x0, rect.y0, rect.x1, rect.y1)
    finally:
        doc.close()

    detection = review.add_custom_detection(
        document_path=path,
        page_num=page_num,
        original_text=original_text,
        entity_type=entity_type,
        replacement_label=replacement_label,
        context_before=context_before,
        context_after=context_after,
        rect=rect_tuple,
    )
    return detection.detection_id


def export_reviewed_pdfs(review: ReviewSession, output_dir: str | Path) -> list[Path]:
    """Apply approved detections to local PDFs after the replacement map is confirmed."""

    if not review.confirmed:
        raise ValueError("Confirm the replacement map before exporting redacted PDFs.")

    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)

    exported: list[Path] = []
    by_document: dict[str, list[Redaction]] = {}
    for detection in review.approved_detections():
        if detection.rect is None:
            continue
        by_document.setdefault(detection.document_path, []).append(detection.to_redaction())

    for document_path, redactions in sorted(by_document.items()):
        source = Path(document_path)
        target = output_path / f"{source.stem}_redacted.pdf"
        apply_redactions(str(source), str(target), redactions)
        exported.append(target)
    return exported


def write_local_review_artifacts(review: ReviewSession, output_dir: str | Path) -> tuple[Path, Path]:
    """Write local replacement map and low-PII audit log for the review session."""

    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)

    replacement_map_path = output_path / "review_replacement_map.json"
    audit_log_path = output_path / "review_audit_log.json"

    replacement_map_path.write_text(
        json.dumps(review.export_replacement_map(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    audit_entries = [
        {
            "detection_id": detection.detection_id,
            "document_name": detection.document_name,
            "page": detection.page_label,
            "entity_type": detection.entity_type,
            "replacement_label": detection.replacement_label,
            "status": detection.status.value,
            "source": detection.source,
        }
        for detection in review.detections
    ]
    audit_log_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "confirmed": review.confirmed,
                "detections": audit_entries,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return replacement_map_path, audit_log_path
