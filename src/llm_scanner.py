"""Local LLM PII scanner using Ollama. All data stays on-device.

Two-pass approach:
  Pass 1 - Discovery: Scan text for potential PII
  Pass 2 - Validation: Feed findings back to verify, filtering hallucinations and FPs
"""

import json
import requests
from dataclasses import dataclass


OLLAMA_URL = "http://localhost:11434/api/generate"

# --- Pass 1: Discovery ---
DISCOVERY_PROMPT = """You are a PII detection assistant for Australian financial documents.
Analyze the text below and identify any personally identifiable information (PII) that has NOT yet been redacted.

Already-redacted items to IGNORE (do NOT flag these):
- "Person 1", "Person 2" (redacted names)
- "XYZ", "XYZ FINANCIAL PTY LTD" or any variation with XYZ (redacted entity)
- "Address 1", "Address 2", "Address 3", "Address 4" (redacted addresses)
- Any value with consecutive asterisks like ***, **8243, ****042 (already masked)

PII types to look for:
- Person names (first name, last name, full name)
- Physical addresses (street, suburb, state, postcode, country) belonging to a person or client entity
- Email addresses belonging to a person (not institutional registry/contact emails)
- Personal phone numbers (not institutional 1800/1300/1300 numbers or office numbers)
- Bank account numbers (unmasked, more than 3 visible consecutive digits)
- BSB numbers (6 digits, unmasked)
- Tax File Numbers (TFN)
- Australian Business Numbers (ABN) belonging to the client (not banks or institutions)
- Securityholder Reference Numbers (SRN/HIN) (unmasked)

Do NOT flag any of these:
- Institutional/public information (bank names, ABNs of banks/companies, registry contact emails, office addresses)
- Financial amounts, dollar values, dates, percentages
- Document reference numbers, payment references, biller codes, barcode text
- ASX codes, security codes, ISIN numbers
- Generic terms like "Account Number:", "BSB:", "SRN/HIN:" as labels without actual values
- Fund names, trust names, company names that are public entities
- Line items in financial statements (e.g. "Unsecured Loan", "Beneficiary Accounts")

Return ONLY a valid JSON array. Each finding must have:
- "text": the EXACT text as it appears in the document (copy it precisely)
- "type": one of: name, address, email, phone, account, bsb, tfn, abn, srn, other
- "context": the surrounding sentence or line where it appears
- "confidence": "high", "medium", or "low"

If no unredacted PII is found, return exactly: []

TEXT TO ANALYZE:
{text}

JSON ARRAY:"""


# --- Pass 2: Validation ---
VALIDATION_PROMPT = """You are a PII validation assistant. Your job is to verify whether flagged items are genuinely unredacted personal information, or false positives.

For each item below, determine if it is:
- GENUINE PII: An actual person's name, personal address, personal email, personal phone number, unmasked bank account, unmasked BSB, TFN, client ABN, or unmasked SRN that should be redacted.
- FALSE POSITIVE: Something that looks like PII but is actually institutional/public info, a financial line item, a document label, a hallucination (text that doesn't actually appear in the document), or already redacted.

The original document text is provided so you can verify each finding actually exists in the text.

DOCUMENT TEXT:
{text}

ITEMS TO VERIFY:
{findings_json}

For each item, return a JSON object with:
- "text": the flagged text
- "verdict": "genuine" or "false_positive"
- "reason": brief explanation

Return a JSON array of verdicts. Example: [{{"text": "John Doe", "verdict": "genuine", "reason": "Personal name found in header"}}]

JSON ARRAY:"""


@dataclass
class PiiFinding:
    text: str
    pii_type: str
    context: str
    confidence: str
    page_num: int


def _call_ollama(prompt: str, model: str, timeout: int = 180) -> str:
    """Make a request to local Ollama and return raw response text."""
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 2048},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _parse_json_array(raw: str) -> list[dict]:
    """Extract a JSON array from LLM response text."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(raw[start:end + 1])
        return items if isinstance(items, list) else []
    except json.JSONDecodeError:
        return []


def _is_already_redacted(text: str) -> bool:
    """Check if text looks like an already-redacted value."""
    lower = text.lower()
    if lower in ("person 1", "person 2", "xyz", "address 1", "address 2", "address 3", "address 4"):
        return True
    if "xyz financial" in lower or "xyz australia" in lower:
        return True
    if "**" in text:
        return True
    return False


def _is_obvious_false_positive(text: str, pii_type: str) -> bool:
    """Hard filter for things that are clearly not PII -- applied before both passes."""
    lower = text.lower()
    if len(text) <= 2:
        return True
    if text.startswith("$") or text.startswith("A$"):
        return True
    if pii_type in ("date of birth", "dob"):
        return True
    # Generic labels without values
    labels = ["account number", "account no", "bsb:", "srn/hin:",
              "securityholder reference number", "investor no.",
              "hin/srn", "tfn/abn"]
    if lower.rstrip(":") in labels or lower in labels:
        return True
    # Fund/trust/investment names are public
    if "trust" in lower or "fund" in lower or "investment" in lower:
        return True
    # Hallucinated person numbers beyond what exists
    if lower.startswith("person ") and lower not in ("person 1", "person 2"):
        return True
    # Institutional phone numbers (1800, 1300, +61 office lines)
    if pii_type == "phone" and ("1800" in text or "1300" in text or "9600 2828" in text):
        return True
    # Institutional emails and addresses that are public contact info
    inst_contacts = ["info@afca", "investors@silc", "evp.com.au",
                     "reply paid", "locked bag", "gpo box",
                     "collins st", "email address", "tax file number"]
    if any(c in lower for c in inst_contacts):
        return True
    # Public institutional ABNs (banks, exchanges)
    known_public_abns = ["11 068 049 178", "33 007 457 141", "11 005 357 522",
                         "99 791 009 636", "48 123 123 124", "88 624 689 694",
                         "93 000 626 264"]
    if pii_type == "abn" and any(a in text for a in known_public_abns):
        return True
    return False


def _pass1_discover(page_text: str, page_num: int, model: str) -> list[PiiFinding]:
    """Pass 1: Discover potential PII in page text."""
    if not page_text.strip():
        return []

    truncated = page_text[:4000] if len(page_text) > 4000 else page_text
    prompt = DISCOVERY_PROMPT.format(text=truncated)

    try:
        raw = _call_ollama(prompt, model)
        items = _parse_json_array(raw)
    except requests.ConnectionError:
        print("    WARNING: Cannot connect to Ollama. Is it running? (ollama serve)")
        return []
    except requests.Timeout:
        print(f"    WARNING: Ollama timed out on page {page_num + 1} (pass 1)")
        return []
    except Exception as e:
        print(f"    WARNING: LLM error on page {page_num + 1} (pass 1): {e}")
        return []

    findings = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "").strip()
        pii_type = item.get("type", "other")
        if not text:
            continue
        if _is_already_redacted(text):
            continue
        if _is_obvious_false_positive(text, pii_type):
            continue
        findings.append(PiiFinding(
            text=text,
            pii_type=pii_type,
            context=item.get("context", ""),
            confidence=item.get("confidence", "low"),
            page_num=page_num,
        ))
    return findings


def _pass2_validate(
    findings: list[PiiFinding],
    page_text: str,
    model: str,
    page_num: int,
) -> list[PiiFinding]:
    """Pass 2: Validate findings against the original text to filter FPs."""
    if not findings:
        return []

    truncated = page_text[:4000] if len(page_text) > 4000 else page_text

    findings_for_llm = [
        {"text": f.text, "type": f.pii_type, "context": f.context}
        for f in findings
    ]

    prompt = VALIDATION_PROMPT.format(
        text=truncated,
        findings_json=json.dumps(findings_for_llm, indent=2),
    )

    try:
        raw = _call_ollama(prompt, model)
        verdicts = _parse_json_array(raw)
    except Exception as e:
        print(f"    WARNING: LLM error on page {page_num + 1} (pass 2): {e}")
        # If validation fails, return all findings as-is (safer)
        return findings

    # Build lookup of verdicts by text
    verdict_map = {}
    for v in verdicts:
        if isinstance(v, dict):
            verdict_map[v.get("text", "")] = v.get("verdict", "false_positive")

    # Keep only findings validated as genuine
    validated = []
    for f in findings:
        verdict = verdict_map.get(f.text, "false_positive")
        if verdict == "genuine":
            validated.append(f)

    return validated


def scan_page_for_pii(
    page_text: str,
    page_num: int,
    model: str = "llama3.1:8b",
    timeout: int = 180,
) -> list[PiiFinding]:
    """Two-pass PII scan: discover then validate."""
    # Pass 1: Discover
    findings = _pass1_discover(page_text, page_num, model)
    if not findings:
        return []

    # Pass 2: Validate
    validated = _pass2_validate(findings, page_text, model, page_num)
    return validated


def scan_document(
    pages_text: list[tuple[int, str]],
    model: str = "llama3.1:8b",
) -> list[PiiFinding]:
    """Scan all pages of a document for unredacted PII (two-pass)."""
    all_findings = []
    for page_num, text in pages_text:
        findings = scan_page_for_pii(text, page_num, model=model)
        all_findings.extend(findings)
    return all_findings
