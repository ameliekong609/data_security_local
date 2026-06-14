from pathlib import Path
import sys

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.export_workflow import export_reviewed_pdf
from src.services.custom_terms import CustomTermDetector
from src.services.profiles import CustomTerm, RedactionProfile

pdf_path = Path('/tmp/data_security_synthetic_ui_smoke.pdf')
output_dir = Path('/tmp/data_security_ui_exports_continue')
profile = RedactionProfile(
    profile_name='Synthetic Smoke Profile Continue',
    terms=[CustomTerm(
        original='Teda Financial Pty Ltd',
        entity_type='COMPANY',
        replacement='[COMPANY_1]',
        variants=['Teda Financial'],
        notes='Synthetic E2E export smoke term',
    )],
)

doc = fitz.open(pdf_path)
findings = CustomTermDetector(profile).detect_pdf(doc, file_id=pdf_path.name)
doc.close()
assert len(findings) == 1, findings
assert findings[0].text == 'Teda Financial Pty Ltd', findings[0].text
findings[0].status = 'approved'

result = export_reviewed_pdf(
    pdf_bytes=pdf_path.read_bytes(),
    input_filename=pdf_path.name,
    findings=findings,
    output_dir=output_dir,
)

redacted_doc = fitz.open(result.output_pdf)
text = '\n'.join(page.get_text('text') for page in redacted_doc)
redacted_doc.close()
assert 'Teda Financial Pty Ltd' not in text
assert '[COMPANY_1]' in text
assert result.mapping_json.exists()
assert result.audit_json.exists()
print('e2e-export-ok')
print(f'pdf={result.output_pdf}')
print(f'map={result.mapping_json}')
print(f'audit={result.audit_json}')
