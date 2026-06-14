"""Generate a mapping file documenting all redactions applied to each PDF."""

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from src.deterministic_redactor import Redaction
from src.llm_scanner import PiiFinding


REDACTION_SOURCE_LABEL = "rule-based"


@dataclass
class FileMapping:
    input_filename: str
    output_filename: str
    redactions: list[Redaction]
    llm_findings: list[PiiFinding]


def generate_mapping_csv(
    mappings: list[FileMapping],
    output_path: Path,
):
    """Generate a deduplicated CSV mapping file for human review.
    Coordinates are omitted for readability -- the JSON has full detail for reversals.
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Input File",
            "Output File",
            "Page",
            "Redaction Type",
            "From (Original)",
            "To (Redacted)",
            "Source",
        ])

        seen = set()
        for m in mappings:
            for r in m.redactions:
                key = (m.input_filename, r.page_num, r.original_text, r.replacement_text)
                if key in seen:
                    continue
                seen.add(key)
                writer.writerow([
                    m.input_filename,
                    m.output_filename,
                    r.page_num + 1,
                    r.redaction_type,
                    r.original_text,
                    r.replacement_text,
                    REDACTION_SOURCE_LABEL,
                ])

            for finding in m.llm_findings:
                key = (m.input_filename, finding.page_num, finding.text, "ai-scan")
                if key in seen:
                    continue
                seen.add(key)
                writer.writerow([
                    m.input_filename,
                    m.output_filename,
                    finding.page_num + 1,
                    finding.pii_type,
                    finding.text,
                    f"[FLAGGED - {finding.confidence}]",
                    "ai-scan",
                ])


def generate_mapping_json(
    mappings: list[FileMapping],
    output_path: Path,
):
    """Generate a JSON mapping file documenting all redactions."""
    data = {
        "generated_at": datetime.now().isoformat(),
        "total_files": len(mappings),
        "total_redactions": sum(len(m.redactions) for m in mappings),
        "total_llm_findings": sum(len(m.llm_findings) for m in mappings),
        "files": [],
    }

    for m in mappings:
        file_entry = {
            "input_filename": m.input_filename,
            "output_filename": m.output_filename,
            "redactions": [
                {
                    "id": r.redaction_id,
                    "page": r.page_num + 1,
                    "type": r.redaction_type,
                    "from": r.original_text,
                    "to": r.replacement_text,
                    "bbox": {
                        "x0": round(r.rect.x0, 1),
                        "y0": round(r.rect.y0, 1),
                        "x1": round(r.rect.x1, 1),
                        "y1": round(r.rect.y1, 1),
                    },
                    "source": REDACTION_SOURCE_LABEL,
                }
                for r in m.redactions
            ],
            "llm_findings": [
                {
                    "page": f.page_num + 1,
                    "type": f.pii_type,
                    "text": f.text,
                    "context": f.context,
                    "confidence": f.confidence,
                    "source": "ai-scan",
                }
                for f in m.llm_findings
            ],
        }
        data["files"].append(file_entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _sha256_text(value: str) -> str:
    """Return a stable digest for local audit correlation without storing raw PII."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_audit_log(
    mappings: list[FileMapping],
    output_path: Path,
):
    """Generate a privacy-preserving local audit log for redaction exports.

    The replacement map is the local source of truth for original->replacement
    values. The audit log intentionally avoids raw original PII and stores only
    hashes, lengths, entity metadata, page/position hints, and replacement labels.
    """
    data = {
        "generated_at": datetime.now().isoformat(),
        "total_files": len(mappings),
        "total_redactions": sum(len(m.redactions) for m in mappings),
        "files": [],
    }

    for m in mappings:
        file_entry = {
            "input_filename": m.input_filename,
            "output_filename": m.output_filename,
            "export_status": "exported" if m.output_filename else "error",
            "redactions": [],
            "llm_findings": [],
        }

        for r in m.redactions:
            file_entry["redactions"].append({
                "id": r.redaction_id,
                "page": r.page_num + 1,
                "type": r.redaction_type,
                "replacement_label": r.replacement_text,
                "original_sha256": _sha256_text(r.original_text),
                "original_length": len(r.original_text),
                "bbox": {
                    "x0": round(r.rect.x0, 1),
                    "y0": round(r.rect.y0, 1),
                    "x1": round(r.rect.x1, 1),
                    "y1": round(r.rect.y1, 1),
                },
                "source": REDACTION_SOURCE_LABEL,
                "reviewer_action": "approved",
            })

        for finding in m.llm_findings:
            file_entry["llm_findings"].append({
                "page": finding.page_num + 1,
                "type": finding.pii_type,
                "text_sha256": _sha256_text(finding.text),
                "text_length": len(finding.text),
                "confidence": finding.confidence,
                "source": "ai-scan",
                "reviewer_action": "flagged_for_review",
            })

        data["files"].append(file_entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
