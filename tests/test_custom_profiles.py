import tempfile
import unittest
from pathlib import Path

import fitz

from src.services.profiles import CustomTerm, ProfileStore, RedactionProfile
from src.services.custom_terms import CustomTermDetector


class CustomProfileTests(unittest.TestCase):
    def test_profile_store_can_create_update_delete_terms_and_persist_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            profile = store.create_profile("Synthetic Matter")
            term = CustomTerm(
                original="Synthetic Trustee Pty Ltd",
                entity_type="COMPANY",
                replacement="[COMPANY_1]",
                variants=["Synthetic Trustee"],
                notes="synthetic fixture only",
            )

            profile = store.add_term(profile.profile_id, term)
            saved = store.get_profile(profile.profile_id)

            self.assertEqual(saved.profile_name, "Synthetic Matter")
            self.assertEqual(len(saved.terms), 1)
            self.assertEqual(saved.terms[0].original, "Synthetic Trustee Pty Ltd")
            self.assertEqual(saved.terms[0].variants, ["Synthetic Trustee"])

            updated = store.update_term(
                profile.profile_id,
                saved.terms[0].term_id,
                original="Synthetic Trustee Pty Ltd",
                entity_type="TRUST",
                replacement="[TRUST_1]",
                variants=["Synthetic Trustee", "STPL"],
                notes="updated synthetic fixture",
            )
            self.assertEqual(updated.terms[0].entity_type, "TRUST")
            self.assertEqual(updated.terms[0].replacement, "[TRUST_1]")
            self.assertEqual(updated.terms[0].variants, ["Synthetic Trustee", "STPL"])

            reloaded = ProfileStore(Path(tmp)).get_profile(profile.profile_id)
            self.assertEqual(reloaded.terms[0].replacement, "[TRUST_1]")

            emptied = store.delete_term(profile.profile_id, saved.terms[0].term_id)
            self.assertEqual(emptied.terms, [])

    def test_custom_term_detector_finds_terms_and_variants_as_pending_review_rows(self):
        profile = RedactionProfile(profile_name="Synthetic Profile")
        profile.terms.append(CustomTerm(
            original="Synthetic Holdings Pty Ltd",
            entity_type="COMPANY",
            replacement="[COMPANY_1]",
            variants=["Synth Holdings"],
        ))

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Client: Synth Holdings\nLegal name: Synthetic Holdings Pty Ltd")

        findings = CustomTermDetector(profile).detect_pdf(doc, file_id="synthetic.pdf")
        doc.close()

        found_texts = {finding.text for finding in findings}
        self.assertIn("Synthetic Holdings Pty Ltd", found_texts)
        self.assertIn("Synth Holdings", found_texts)
        self.assertTrue(all(f.status == "pending" for f in findings))
        self.assertTrue(all(f.source_detector == "custom_term" for f in findings))
        self.assertTrue(all(f.proposed_replacement == "[COMPANY_1]" for f in findings))
        self.assertTrue(all(f.bounding_box for f in findings))

    def test_custom_term_detector_does_not_export_pending_findings_until_approved(self):
        profile = RedactionProfile(profile_name="Synthetic Profile")
        profile.terms.append(CustomTerm(
            original="Synthetic Holdings Pty Ltd",
            entity_type="COMPANY",
            replacement="[COMPANY_1]",
            variants=[],
        ))

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Synthetic Holdings Pty Ltd")

        detector = CustomTermDetector(profile)
        findings = detector.detect_pdf(doc, file_id="synthetic.pdf")
        pending_redactions = detector.redactions_for_findings(findings)

        findings[0].status = "approved"
        approved_redactions = detector.redactions_for_findings(findings)
        doc.close()

        self.assertEqual(pending_redactions, [])
        self.assertEqual(len(approved_redactions), 1)
        self.assertEqual(approved_redactions[0].redaction_type, "custom_term")


if __name__ == "__main__":
    unittest.main()
