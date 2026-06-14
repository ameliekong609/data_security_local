from pathlib import Path
import fitz

out = Path('/tmp/data_security_synthetic_ui_smoke.pdf')
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 72), 'Client: Teda Financial Pty Ltd\nContact: Jane Example\nEmail: jane.example@example.test\nAccount: ACC-123456', fontsize=12)
doc.save(out)
doc.close()
print(out)
