#!/usr/bin/env python3
"""Small local desktop app for PII redaction review.

The UI runs in a native webview window. The redaction engine stays in Python and
all selected files are processed on the local machine.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import json
import re
import shutil
import zipfile

import webview

from src.config_loader import default_redaction_config
from src.detection_service import CustomTerm
from src.image_redactor import redact_image
from src.mupdf_runtime import quiet_mupdf_console_output
from src.review_state import DetectionStatus, ReviewSession
from src.review_workflow import (
    add_custom_detection_from_pdf,
    export_reviewed_pdfs,
    build_enhanced_review_for_pdf,
    write_local_review_artifacts,
)


SUPPORTED_PDF_SUFFIXES = {".pdf"}
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SUPPORTED_SUFFIXES = SUPPORTED_PDF_SUFFIXES | SUPPORTED_IMAGE_SUFFIXES


def _file_dialog_kind(name: str) -> Any:
    dialog = getattr(webview, "FileDialog", None)
    if dialog is not None:
        return getattr(dialog, name)
    return getattr(webview, f"{name}_DIALOG")


class DesktopApi:
    def __init__(self) -> None:
        self.window: webview.Window | None = None
        self.review = ReviewSession()
        self.selected_files: list[Path] = []
        self.output_dir = Path("review_outputs").resolve()
        self.image_outputs: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.exported_paths: list[Path] = []
        self.known_terms_text = ""

    def choose_files(self) -> dict[str, Any]:
        if self.window is None:
            return self._error("Desktop window is not ready.")
        paths = self.window.create_file_dialog(
            _file_dialog_kind("OPEN"),
            allow_multiple=True,
            file_types=("Documents (*.pdf;*.png;*.jpg;*.jpeg)", "All files (*.*)"),
        )
        if paths:
            self.selected_files = self._supported_files(Path(path) for path in paths)
        return self.state()

    def choose_folder(self) -> dict[str, Any]:
        if self.window is None:
            return self._error("Desktop window is not ready.")
        paths = self.window.create_file_dialog(_file_dialog_kind("FOLDER"))
        if paths:
            root = Path(paths[0])
            self.selected_files = self._supported_files(
                path for path in root.rglob("*") if path.is_file()
            )
        return self.state()

    def choose_output_folder(self) -> dict[str, Any]:
        if self.window is None:
            return self._error("Desktop window is not ready.")
        paths = self.window.create_file_dialog(_file_dialog_kind("FOLDER"))
        if paths:
            self.output_dir = Path(paths[0]).resolve()
        return self.state()

    def set_known_terms(self, raw_terms: str) -> dict[str, Any]:
        self.known_terms_text = raw_terms
        return self.state()

    def detect(self) -> dict[str, Any]:
        self.review = ReviewSession()
        self.image_outputs = []
        self.warnings = []
        self.exported_paths = []

        config = default_redaction_config()
        pdf_paths = [path for path in self.selected_files if path.suffix.lower() in SUPPORTED_PDF_SUFFIXES]
        image_paths = [path for path in self.selected_files if path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES]
        custom_terms = self._known_terms()

        for pdf_path in pdf_paths:
            try:
                session = build_enhanced_review_for_pdf(pdf_path, custom_terms=custom_terms)
                self.review.detections.extend(session.detections)
            except Exception as exc:
                self.warnings.append(f"{pdf_path.name}: {exc}")
        self.review.approve_pending()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        for image_index, image_path in enumerate(image_paths, start=1):
            result = redact_image(
                image_path,
                self.output_dir,
                config,
                output_name=f"image_{image_index:03d}_redacted.png",
            )
            self.image_outputs.append({
                "input_filename": result.input_filename,
                "output_filename": result.output_filename,
                "redaction_count": len(result.redactions),
                "error": result.error,
            })
            if result.error:
                self.warnings.append(f"{image_path.name}: image OCR/redaction failed: {result.error}")

        return self.state()

    def approve_all_pending(self) -> dict[str, Any]:
        self.review.approve_pending()
        return self.state()

    def reject_all_pending(self) -> dict[str, Any]:
        self.review.reject_pending(reason="Bulk rejected in desktop app")
        return self.state()

    def approve_detection(self, detection_id: str) -> dict[str, Any]:
        target = self.review.get_detection(detection_id)
        original_text = target.original_text.casefold()
        changed = 0
        for detection in self.review.detections:
            if detection.original_text.casefold() != original_text:
                continue
            self.review.approve_detection(detection.detection_id)
            changed += 1
        if changed > 1:
            self.warnings.append(f"Approved {changed} rows matching: {target.original_text}")
        return self.state()

    def reject_detection(self, detection_id: str) -> dict[str, Any]:
        target = self.review.get_detection(detection_id)
        original_text = target.original_text.casefold()
        changed = 0
        for detection in self.review.detections:
            if detection.original_text.casefold() != original_text:
                continue
            self.review.reject_detection(detection.detection_id, reason="Rejected matching original text in desktop app")
            changed += 1
        if changed > 1:
            self.warnings.append(f"Rejected {changed} rows matching: {target.original_text}")
        return self.state()

    def edit_detection(self, detection_id: str, entity_type: str, replacement_label: str) -> dict[str, Any]:
        self.review.edit_detection(
            detection_id,
            entity_type=entity_type,
            replacement_label=replacement_label,
        )
        return self.state()

    def add_custom_detection(
        self,
        document_path: str,
        page_number: int,
        original_text: str,
        entity_type: str,
        replacement_label: str,
    ) -> dict[str, Any]:
        try:
            detection_id = add_custom_detection_from_pdf(
                self.review,
                document_path=document_path,
                page_num=max(0, int(page_number) - 1),
                original_text=original_text,
                entity_type=entity_type,
                replacement_label=replacement_label,
            )
            self.review.approve_detection(detection_id)
        except Exception as exc:
            self.warnings.append(f"Custom detection failed: {exc}")
        return self.state()

    def add_bulk_custom_detections(self, raw_terms: str) -> dict[str, Any]:
        """Add multiple leftover redactions across selected PDFs.

        Format per line:
          text | type | optional replacement | optional page number
        """

        pdf_paths = [path for path in self.selected_files if path.suffix.lower() in SUPPORTED_PDF_SUFFIXES]
        if not pdf_paths:
            self.warnings.append("No selected PDFs available for custom detections.")
            return self.state()

        added = 0
        counters = self._replacement_counters()
        for line_number, raw_line in enumerate(raw_terms.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if not parts[0]:
                self.warnings.append(f"Custom line {line_number}: missing text.")
                continue
            original_text = parts[0]
            entity_type = parts[1] if len(parts) >= 2 and parts[1] else "custom"
            entity_key = self._normalized_entity_type(entity_type, "")
            replacement_label = parts[2] if len(parts) >= 3 and parts[2] else self._next_replacement_label(entity_key, counters)
            page_filter = None
            if len(parts) >= 4 and parts[3]:
                try:
                    page_filter = max(0, int(parts[3]) - 1)
                except ValueError:
                    self.warnings.append(f"Custom line {line_number}: page must be a number.")
                    continue

            for pdf_path in pdf_paths:
                added += self._add_custom_matches_for_pdf(
                    pdf_path,
                    original_text=original_text,
                    entity_type=entity_type,
                    replacement_label=replacement_label,
                    page_filter=page_filter,
                )
        if added == 0:
            self.warnings.append("No matching leftover text was found in the selected PDFs.")
        return self.state()

    def use_exported_pdfs_as_input(self) -> dict[str, Any]:
        exported_pdfs = [
            path
            for path in self.exported_paths
            if path.suffix.lower() == ".pdf" and path.exists()
        ]
        if not exported_pdfs:
            self.warnings.append("No exported redacted PDFs are available for another pass yet.")
            return self.state()
        self.selected_files = exported_pdfs
        self.review = ReviewSession()
        self.image_outputs = []
        self.exported_paths = []
        self.warnings.append("Exported redacted PDFs are now selected. Click Detect to run another pass.")
        return self.state()

    def export_outputs(self) -> dict[str, Any]:
        try:
            pending_count = sum(1 for detection in self.review.detections if detection.status == DetectionStatus.PENDING)
            if pending_count:
                self.review.approve_pending()
                self.warnings.append(
                    f"{pending_count} pending detection(s) were included as approved. "
                    "Rejected rows are excluded."
                )
            approved_count = sum(1 for detection in self.review.detections if detection.status == DetectionStatus.APPROVED)
            successful_image_count = sum(1 for item in self.image_outputs if item.get("output_filename") and not item.get("error"))
            if approved_count == 0 and successful_image_count == 0:
                self.warnings.append(
                    "Export not created: no approved redactions yet. "
                    "Leave at least one correct detection unrejected, or add a leftover redaction."
                )
                return self.state()
            self.review.confirm_replacement_map()
            pdf_outputs = export_reviewed_pdfs(self.review, self.output_dir)
            map_path, audit_path = write_local_review_artifacts(self.review, self.output_dir)
            image_output_paths = [
                self.output_dir / item["output_filename"]
                for item in self.image_outputs
                if item.get("output_filename") and not item.get("error")
            ]
            self.exported_paths = [*pdf_outputs, map_path, audit_path, *image_output_paths]
            zip_path = self.output_dir / "redaction_outputs.zip"
            self._write_zip(zip_path, self.exported_paths)
            self.exported_paths.append(zip_path)
            self.warnings.append(f"Export created in {self.output_dir}.")
        except Exception as exc:
            self.warnings.append(f"Export failed: {exc}")
        return self.state()

    def open_output_folder(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            webview.windows[0].evaluate_js(f"console.log({json.dumps(str(self.output_dir))})")
            if shutil.which("open"):
                import subprocess

                subprocess.Popen(["open", str(self.output_dir)])
            elif shutil.which("explorer"):
                import subprocess

                subprocess.Popen(["explorer", str(self.output_dir)])
        except Exception as exc:
            self.warnings.append(f"Could not open output folder: {exc}")
        return self.state()

    def state(self) -> dict[str, Any]:
        return {
            "selected_files": [str(path) for path in self.selected_files],
            "output_dir": str(self.output_dir),
            "detections": [
                self._detection_to_dict(detection)
                for detection in sorted(self.review.detections, key=self._detection_sort_key)
            ],
            "summary": self._summary(),
            "warnings": self.warnings,
            "image_outputs": self.image_outputs,
            "exported_paths": [str(path) for path in self.exported_paths],
            "known_terms_text": self.known_terms_text,
        }

    def _detection_sort_key(self, detection: Any) -> tuple[int, int, str, int, str]:
        return (
            self._status_priority(detection.status),
            self._category_priority(detection.entity_type, detection.replacement_label),
            detection.document_name,
            detection.page_num,
            detection.original_text.casefold(),
        )

    def _status_priority(self, status: DetectionStatus) -> int:
        if status == DetectionStatus.REJECTED:
            return 1
        return 0

    def _category_priority(self, entity_type: str, replacement_label: str) -> int:
        normalized = self._normalized_entity_type(entity_type, replacement_label)
        priority = {
            "PERSON": 0,
            "EMAIL": 1,
            "ACCOUNT": 2,
            "ACCOUNT_NUMBER": 2,
            "COMPANY": 3,
            "TRUST": 4,
            "CUSTOM": 5,
        }
        return priority.get(normalized, 99)

    def _summary(self) -> dict[str, int]:
        return {
            "pending": sum(1 for d in self.review.detections if d.status == DetectionStatus.PENDING),
            "approved": sum(1 for d in self.review.detections if d.status == DetectionStatus.APPROVED),
            "rejected": sum(1 for d in self.review.detections if d.status == DetectionStatus.REJECTED),
            "files": len(self.selected_files),
            "images": len(self.image_outputs),
        }

    def _detection_to_dict(self, detection: Any) -> dict[str, Any]:
        data = asdict(detection)
        data["status"] = detection.status.value
        data["page_label"] = detection.page_label
        data["display_entity_type"] = self._display_entity_type(detection.entity_type, detection.replacement_label)
        data["category_priority"] = self._category_priority(detection.entity_type, detection.replacement_label)
        return data

    def _display_entity_type(self, entity_type: str, replacement_label: str) -> str:
        normalized = self._normalized_entity_type(entity_type, replacement_label)
        labels = {
            "PERSON": "Person",
            "COMPANY": "Company",
            "TRUST": "Trust",
            "EMAIL": "Email",
            "ACCOUNT": "Account",
            "ACCOUNT_NUMBER": "Account",
            "CUSTOM": "Custom",
            "KEYWORD": "Keyword",
        }
        return labels.get(normalized, normalized.replace("_", " ").title())

    def _normalized_entity_type(self, entity_type: str, replacement_label: str) -> str:
        normalized = entity_type.strip().upper()
        if normalized == "FIELD":
            for candidate in ("EMAIL", "ACCOUNT"):
                if candidate in replacement_label.upper():
                    return candidate
        return normalized

    def _supported_files(self, paths: Any) -> list[Path]:
        supported = []
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            if resolved.is_file() and resolved.suffix.lower() in SUPPORTED_SUFFIXES:
                supported.append(resolved)
        return sorted(dict.fromkeys(supported))

    def _write_zip(self, zip_path: Path, paths: list[Path]) -> None:
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            used_names: set[str] = set()
            for path in paths:
                if not path.exists() or not path.is_file():
                    continue
                archive_name = path.name
                if archive_name in used_names:
                    archive_name = f"{path.stem}-{len(used_names) + 1}{path.suffix}"
                used_names.add(archive_name)
                archive.write(path, arcname=archive_name)

    def _add_custom_matches_for_pdf(
        self,
        pdf_path: Path,
        *,
        original_text: str,
        entity_type: str,
        replacement_label: str,
        page_filter: int | None,
    ) -> int:
        import fitz

        added = 0
        doc = fitz.open(pdf_path)
        try:
            for page_num in range(len(doc)):
                if page_filter is not None and page_num != page_filter:
                    continue
                page = doc[page_num]
                rects = page.search_for(original_text)
                for rect in rects:
                    detection = self.review.add_custom_detection(
                        document_path=pdf_path,
                        page_num=page_num,
                        original_text=original_text,
                        entity_type=entity_type,
                        replacement_label=replacement_label,
                        rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                    )
                    detection.source = "custom_bulk"
                    self.review.approve_detection(detection.detection_id)
                    added += 1
        finally:
            doc.close()
        return added

    def _known_terms(self) -> list[CustomTerm]:
        terms: list[CustomTerm] = []
        counters: dict[str, int] = {}
        for raw_line in self.known_terms_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            original = parts[0]
            entity_type = parts[1].upper() if len(parts) >= 2 and parts[1] else "CUSTOM"
            counters[entity_type] = counters.get(entity_type, 0) + 1
            replacement = parts[2] if len(parts) >= 3 and parts[2] else f"[{entity_type}_{counters[entity_type]}]"
            variants = [
                variant.strip()
                for variant in parts[3].split(",")
                if variant.strip()
            ] if len(parts) >= 4 else []
            terms.append(
                CustomTerm(
                    original=original,
                    entity_type=entity_type,
                    replacement_label=replacement,
                    variants=variants,
                )
            )
        return terms

    def _replacement_counters(self) -> dict[str, int]:
        counters: dict[str, int] = {}
        for detection in self.review.detections:
            entity_type = self._normalized_entity_type(detection.entity_type, detection.replacement_label)
            match = re.search(r"\[(?P<entity>[A-Z_]+)_(?P<index>\d+)\]", detection.replacement_label.upper())
            if match:
                entity_type = match.group("entity")
                counters[entity_type] = max(counters.get(entity_type, 0), int(match.group("index")))
            else:
                counters.setdefault(entity_type, 0)
        return counters

    def _next_replacement_label(self, entity_type: str, counters: dict[str, int]) -> str:
        entity_key = (entity_type or "CUSTOM").strip().upper()
        counters[entity_key] = counters.get(entity_key, 0) + 1
        return f"[{entity_key}_{counters[entity_key]}]"

    def _error(self, message: str) -> dict[str, Any]:
        self.warnings.append(message)
        return self.state()


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Data Security Local</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --danger: #b42318;
      --warn-bg: #fff7ed;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    .privacy { color: var(--muted); font-size: 13px; }
    main {
      padding: 20px 24px 28px;
      display: grid;
      grid-template-columns: minmax(400px, 440px) minmax(0, 1fr);
      gap: 18px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    h2 { font-size: 15px; margin: 0 0 12px; }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 9px 11px;
      font-size: 13px;
      cursor: pointer;
      min-height: 36px;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.primary:hover { background: var(--accent-strong); }
    button.danger { color: var(--danger); }
    button.selected-approve {
      background: #d1fae5;
      border-color: #10b981;
      color: #065f46;
      font-weight: 650;
    }
    button.selected-reject {
      background: #fee2e2;
      border-color: #ef4444;
      color: #991b1b;
      font-weight: 650;
    }
    button:disabled { opacity: .45; cursor: default; }
    .stack { display: grid; gap: 10px; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .muted { color: var(--muted); font-size: 13px; }
    .small { font-size: 12px; }
    .file-list {
      max-height: 150px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fbfcfd;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }
    .stat strong { display: block; font-size: 20px; }
    table {
      width: 100%;
      min-width: 1180px;
      border-collapse: collapse;
      font-size: 13px;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 600; background: #fbfcfd; position: sticky; top: 0; }
    .table-wrap {
      max-height: 520px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    th:nth-child(1), td:nth-child(1) { width: 86px; }
    th:nth-child(2), td:nth-child(2) { width: 180px; }
    th:nth-child(3), td:nth-child(3) { width: 56px; }
    th:nth-child(4), td:nth-child(4) { width: 150px; }
    th:nth-child(5), td:nth-child(5) { width: 250px; }
    th:nth-child(6), td:nth-child(6) { width: 230px; }
    th:nth-child(7), td:nth-child(7) { width: 220px; }
    td {
      overflow-wrap: anywhere;
    }
    td input {
      min-width: 0;
    }
    input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      font-size: 13px;
    }
    textarea {
      width: 100%;
      min-height: 120px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      font-size: 13px;
      font-family: inherit;
      line-height: 1.4;
    }
    #knownTerms {
      min-height: 170px;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .warnings {
      background: var(--warn-bg);
      border-color: #fed7aa;
      color: #7c2d12;
    }
    .status-chip {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 650;
      border: 1px solid var(--line);
      background: #f8fafc;
      color: #475467;
    }
    .status-approved {
      background: #d1fae5;
      border-color: #10b981;
      color: #065f46;
    }
    .status-rejected {
      background: #fee2e2;
      border-color: #ef4444;
      color: #991b1b;
    }
    .status-pending {
      background: #fef3c7;
      border-color: #f59e0b;
      color: #92400e;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Data Security Local</h1>
      <div class="privacy">Local document review. No Portal, no cloud app, no public AI upload.</div>
    </div>
    <button onclick="refresh()">Refresh</button>
  </header>
  <main>
    <section class="stack">
      <h2>Files</h2>
      <button onclick="chooseFiles()">Choose PDFs/images</button>
      <button onclick="chooseFolder()">Choose folder</button>
      <div class="file-list" id="files">No files selected.</div>
      <h2>Output</h2>
      <button onclick="chooseOutput()">Choose output folder</button>
      <div class="muted small" id="output"></div>
      <h2>Known sensitive names</h2>
      <textarea id="knownTerms" placeholder="One per line:
Jane Example | PERSON
Example Holdings Pty Ltd | COMPANY
Example Family Trust | TRUST | [TRUST_1] | Example Trust"></textarea>
      <div class="hint">Format: name | type | optional replacement | aliases. Case-insensitive.</div>
      <button onclick="saveKnownTerms()">Save names</button>
      <button class="primary" onclick="detect()">Detect</button>
      <div class="row">
      <button onclick="approveAll()">Approve all pending</button>
      <button class="danger" onclick="rejectAll()">Reject all pending</button>
      </div>
      <button class="primary" onclick="exportOutputs()">Export reviewed output</button>
      <button onclick="useExportedAsInput()">Use exported PDFs for another pass</button>
      <button onclick="openOutput()">Open output folder</button>
      <div id="warnings"></div>
    </section>
    <section>
      <div class="stats">
        <div class="stat"><strong id="pending">0</strong><span class="muted">Pending</span></div>
        <div class="stat"><strong id="approved">0</strong><span class="muted">Approved</span></div>
        <div class="stat"><strong id="rejected">0</strong><span class="muted">Rejected</span></div>
        <div class="stat"><strong id="images">0</strong><span class="muted">Images</span></div>
      </div>
      <h2>Detections</h2>
      <div class="muted small">Detected rows are approved by default. Reject rows that should stay visible. Automatic detection only covers human names, emails, bank account numbers, and company/trust names.</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>File</th>
              <th>Page</th>
              <th>Category</th>
              <th>Original</th>
              <th>Replacement</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="detections"></tbody>
        </table>
      </div>
      <h2 style="margin-top:16px">Bulk leftover redactions</h2>
      <textarea id="bulkCustom" placeholder="One per line:
leftover text | type
Jane Example | person
Tenet Legacy Pty Ltd | company
123456789 | account"></textarea>
      <button onclick="addBulkCustom()">Add bulk leftovers</button>
      <h2 style="margin-top:16px">Exports</h2>
      <div id="exports" class="muted">No exports yet.</div>
    </section>
  </main>
  <script>
    let currentState = null;

    async function callApi(name, ...args) {
      setBusy(true);
      try {
        const state = await window.pywebview.api[name](...args);
        render(state);
      } finally {
        setBusy(false);
      }
    }
    function setBusy(isBusy) {
      document.querySelectorAll("button").forEach(button => button.disabled = isBusy);
    }
    async function refresh() { await callApi("state"); }
    async function chooseFiles() { await callApi("choose_files"); }
    async function chooseFolder() { await callApi("choose_folder"); }
    async function chooseOutput() { await callApi("choose_output_folder"); }
    async function detect() {
      await window.pywebview.api.set_known_terms(document.getElementById("knownTerms").value);
      await callApi("detect");
    }
    async function saveKnownTerms() {
      await callApi("set_known_terms", document.getElementById("knownTerms").value);
    }
    async function approveAll() { await callApi("approve_all_pending"); }
    async function rejectAll() { await callApi("reject_all_pending"); }
    async function exportOutputs() { await callApi("export_outputs"); }
    async function useExportedAsInput() { await callApi("use_exported_pdfs_as_input"); }
    async function openOutput() { await callApi("open_output_folder"); }
    async function approveDetection(id) { await callApi("approve_detection", id); }
    async function rejectDetection(id) { await callApi("reject_detection", id); }
    async function saveDetection(id) {
      const type = normalizeEntityType(document.getElementById(`type-${id}`).value);
      const replacement = document.getElementById(`replacement-${id}`).value;
      await callApi("edit_detection", id, type, replacement);
    }
    async function addBulkCustom() {
      await callApi("add_bulk_custom_detections", document.getElementById("bulkCustom").value);
    }
    function render(state) {
      currentState = state;
      document.getElementById("pending").textContent = state.summary.pending;
      document.getElementById("approved").textContent = state.summary.approved;
      document.getElementById("rejected").textContent = state.summary.rejected;
      document.getElementById("images").textContent = state.summary.images;
      document.getElementById("output").textContent = state.output_dir;
      if (document.activeElement?.id !== "knownTerms") {
        document.getElementById("knownTerms").value = state.known_terms_text || "";
      }
      document.getElementById("files").innerHTML = state.selected_files.length
        ? state.selected_files.map(escapeHtml).join("<br>")
        : "No files selected.";
      document.getElementById("warnings").innerHTML = state.warnings.length
        ? `<section class="warnings">${state.warnings.map(escapeHtml).join("<br>")}</section>`
        : "";
      document.getElementById("detections").innerHTML = state.detections.length
        ? state.detections.map(renderDetection).join("")
        : `<tr><td colspan="7" class="muted">Choose files and click Detect.</td></tr>`;
      document.getElementById("exports").innerHTML = state.exported_paths.length
        ? state.exported_paths.map(escapeHtml).join("<br>")
        : "No exports yet.";
    }
    function renderDetection(item) {
      const status = String(item.status || "pending").toLowerCase();
      const approveClass = status === "approved" ? "selected-approve" : "";
      const rejectClass = status === "rejected" ? "selected-reject" : "danger";
      return `<tr>
        <td><span class="status-chip status-${escapeAttr(status)}">${escapeHtml(status)}</span></td>
        <td>${escapeHtml(item.document_name)}</td>
        <td>${item.page_label}</td>
        <td><input id="type-${item.detection_id}" value="${escapeAttr(item.display_entity_type)}" /></td>
        <td>${escapeHtml(item.original_text)}</td>
        <td><input id="replacement-${item.detection_id}" value="${escapeAttr(item.replacement_label)}" /></td>
        <td>
          <div class="row">
            <button class="${approveClass}" onclick="approveDetection('${item.detection_id}')">Approve</button>
            <button class="${rejectClass}" onclick="rejectDetection('${item.detection_id}')">Reject</button>
            <button onclick="saveDetection('${item.detection_id}')">Save</button>
          </div>
        </td>
      </tr>`;
    }
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }
    function normalizeEntityType(value) {
      const normalized = String(value ?? "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      const aliases = {
        "person": "person",
        "company": "company",
        "trust": "trust",
        "address": "address",
        "email": "email",
        "phone": "phone",
        "account": "account",
        "account_number": "account",
        "client_id": "client_id",
        "investor_id": "investor_id",
        "date_of_birth": "dob",
        "dob": "dob",
        "date": "date",
        "abn": "abn",
        "tfn": "tfn",
        "custom": "custom",
        "keyword": "keyword"
      };
      return aliases[normalized] || normalized || "custom";
    }
    function escapeAttr(value) { return escapeHtml(value).replace(/`/g, "&#096;"); }
    window.addEventListener("pywebviewready", refresh);
  </script>
</body>
</html>
"""


def main() -> None:
    quiet_mupdf_console_output()
    api = DesktopApi()
    window = webview.create_window(
        "Data Security Local",
        html=HTML,
        js_api=api,
        width=1360,
        height=760,
        min_size=(880, 620),
    )
    api.window = window
    webview.start(debug=False)


if __name__ == "__main__":
    main()
