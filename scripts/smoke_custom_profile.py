"""Synthetic local smoke test for custom redaction profile detection/review."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.custom_terms import CustomTermDetector
from src.services.profiles import CustomTerm, ProfileStore


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        profiles_dir = root / "profiles"
        pdf_path = root / "synthetic.pdf"

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Synthetic Alpha Pty Ltd is the client for this synthetic test.")
        doc.save(pdf_path)
        doc.close()

        store = ProfileStore(profiles_dir)
        profile = store.create_profile("Synthetic Smoke Profile")
        profile = store.add_term(profile.profile_id, CustomTerm(
            original="Synthetic Alpha Pty Ltd",
            entity_type="COMPANY",
            replacement="[COMPANY_1]",
            variants=["Synthetic Alpha"],
        ))

        doc = fitz.open(pdf_path)
        detector = CustomTermDetector(profile)
        findings = detector.detect_pdf(doc, file_id="synthetic.pdf")
        doc.close()

        if not findings:
            raise AssertionError("Expected at least one custom-term finding")
        if any(finding.status != "pending" for finding in findings):
            raise AssertionError("Custom-term findings must enter review as pending")
        if findings[0].source_detector != "custom_term":
            raise AssertionError("Expected custom_term source detector")
        if findings[0].proposed_replacement != "[COMPANY_1]":
            raise AssertionError("Expected profile replacement label on finding")
        print("custom-profile smoke ok: synthetic PDF produced pending custom-term review finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
