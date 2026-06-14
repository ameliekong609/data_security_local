"""Local export workflow for reviewed PDF redaction candidates."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.custom_term_detector import candidates_to_redactions
from src.detection_models import DetectionCandidate
from src.mapping_generator import FileMapping, generate_audit_log, generate_mapping_json
from src.pdf_writer import apply_redactions


@dataclass(frozen=True)
class ExportWorkflowResult:
    input_filename: str
    output_pdf: Path
    mapping_json: Path
    audit_json: Path
    redaction_count: int


def export_reviewed_pdf(
    *,
    pdf_bytes: bytes,
    input_filename: str,
    findings: Iterable[DetectionCandidate],
    output_dir: str | Path,
) -> ExportWorkflowResult:
    """Export approved findings to a safely redacted PDF plus local map/audit.

    The caller must pass findings from the human review layer. Only findings with
    ``status == "approved"`` are converted to redactions; pending/rejected rows are
    ignored so detection cannot bypass review.
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _safe_stem(input_filename)
    output_pdf = output_dir / f"{safe_stem}_redacted.pdf"
    mapping_json = output_dir / f"{safe_stem}_replacement_map.json"
    audit_json = output_dir / f"{safe_stem}_audit.json"

    redactions = candidates_to_redactions(findings)
    for index, redaction in enumerate(redactions, start=1):
        if not redaction.redaction_id:
            redaction.redaction_id = _stable_redaction_id(input_filename, redaction.original_text, index)

    with tempfile.TemporaryDirectory() as temp_dir:
        source_pdf = Path(temp_dir) / input_filename
        source_pdf.write_bytes(pdf_bytes)
        count = apply_redactions(str(source_pdf), str(output_pdf), redactions)

    mapping = FileMapping(
        input_filename=input_filename,
        output_filename=output_pdf.name,
        redactions=redactions,
        llm_findings=[],
    )
    generate_mapping_json([mapping], mapping_json)
    generate_audit_log([mapping], audit_json)

    return ExportWorkflowResult(
        input_filename=input_filename,
        output_pdf=output_pdf,
        mapping_json=mapping_json,
        audit_json=audit_json,
        redaction_count=count,
    )


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem.strip() or "redacted"
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)


def _stable_redaction_id(input_filename: str, original_text: str, index: int) -> str:
    digest = hashlib.sha256(f"{input_filename}|{original_text}|{index}".encode("utf-8")).hexdigest()[:12]
    return f"R-{digest}"
