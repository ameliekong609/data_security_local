import tempfile
import unittest
from pathlib import Path

import fitz

from src.custom_term_detector import CustomTerm, CustomTermDetector, deduplicate_candidates
from src.detection_models import DetectionCandidate


class CustomTermDetectorTests(unittest.TestCase):
    def _make_pdf(self, pages: list[str]) -> Path:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        doc = fitz.open()
        for text in pages:
            page = doc.new_page()
            page.insert_text((72, 72), text, fontsize=12)
        doc.save(tmp.name)
        doc.close()
        return Path(tmp.name)

    def test_detects_original_and_variant_as_pending_review_candidates_with_bounding_boxes(self):
        pdf_path = self._make_pdf([
            "Synthetic Company Pty Ltd paid Synthetic Co on behalf of Client Alpha.",
            "client alpha also used investor ID INV-0001.",
        ])
        try:
            detector = CustomTermDetector([
                CustomTerm(
                    term_id="company-1",
                    entity_type="COMPANY",
                    original="Synthetic Company Pty Ltd",
                    replacement="[COMPANY_1]",
                    variants=["Synthetic Co"],
                ),
                CustomTerm(
                    term_id="person-1",
                    entity_type="PERSON",
                    original="Client Alpha",
                    replacement="[PERSON_1]",
                    variants=[],
                ),
            ])

            doc = fitz.open(pdf_path)
            candidates = detector.detect_document(doc, file_id="synthetic.pdf")
            doc.close()

            by_text = {candidate.text.lower(): candidate for candidate in candidates}
            self.assertIn("synthetic company pty ltd", by_text)
            self.assertIn("synthetic co", by_text)
            self.assertIn("client alpha", by_text)

            company = by_text["synthetic company pty ltd"]
            self.assertIsInstance(company, DetectionCandidate)
            self.assertEqual(company.file_id, "synthetic.pdf")
            self.assertEqual(company.page_number, 0)
            self.assertEqual(company.entity_type, "COMPANY")
            self.assertEqual(company.proposed_replacement, "[COMPANY_1]")
            self.assertEqual(company.source_detector, "custom_term")
            self.assertEqual(company.status, "pending")
            self.assertGreater(company.confidence, 0.9)
            self.assertIn("Synthetic Company Pty Ltd", company.context)
            self.assertGreater(company.bounding_box.width, 0)
            self.assertGreater(company.bounding_box.height, 0)

            person = by_text["client alpha"]
            self.assertEqual(person.page_number, 1)
            self.assertEqual(person.proposed_replacement, "[PERSON_1]")
        finally:
            pdf_path.unlink(missing_ok=True)

    def test_deduplicates_overlapping_candidates_by_preferring_longer_then_stable_order(self):
        short = DetectionCandidate(
            file_id="synthetic.pdf",
            page_number=0,
            text="Synthetic Company",
            entity_type="COMPANY",
            bounding_box=fitz.Rect(10, 10, 100, 20),
            proposed_replacement="[COMPANY_SHORT]",
            source_detector="custom_term",
            confidence=0.95,
            context="Synthetic Company Pty Ltd",
        )
        long = DetectionCandidate(
            file_id="synthetic.pdf",
            page_number=0,
            text="Synthetic Company Pty Ltd",
            entity_type="COMPANY",
            bounding_box=fitz.Rect(10, 10, 150, 20),
            proposed_replacement="[COMPANY_LONG]",
            source_detector="custom_term",
            confidence=0.95,
            context="Synthetic Company Pty Ltd",
        )
        other_page = DetectionCandidate(
            file_id="synthetic.pdf",
            page_number=1,
            text="Synthetic Company",
            entity_type="COMPANY",
            bounding_box=fitz.Rect(10, 10, 100, 20),
            proposed_replacement="[COMPANY_SHORT]",
            source_detector="custom_term",
            confidence=0.95,
            context="Synthetic Company Pty Ltd",
        )

        deduped = deduplicate_candidates([short, long, other_page])

        self.assertEqual([candidate.text for candidate in deduped], [
            "Synthetic Company Pty Ltd",
            "Synthetic Company",
        ])
        self.assertEqual(deduped[0].page_number, 0)
        self.assertEqual(deduped[1].page_number, 1)


if __name__ == "__main__":
    unittest.main()
