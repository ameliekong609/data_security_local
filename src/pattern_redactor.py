import re
import fitz
from src.deterministic_redactor import Redaction
from src.config_loader import FieldRule


def _mask_keeping_last(value: str, keep_last: int) -> str:
    """Replace all but the last N characters with asterisks, preserving spaces/dashes."""
    digits_only = re.sub(r'[^\d]', '', value)
    if len(digits_only) <= keep_last:
        return value  # already short enough

    result = []
    digits_seen = 0
    total_digits = len(digits_only)
    digits_to_mask = total_digits - keep_last

    for ch in value:
        if ch.isdigit():
            digits_seen += 1
            if digits_seen <= digits_to_mask:
                result.append('*')
            else:
                result.append(ch)
        else:
            result.append(ch)
    return ''.join(result)


def _mask_email(email: str) -> str:
    """Mask email keeping first char of local part and first char of domain.
    e.g., joe.z@zxyfinancial.com -> j**.*@z***********.com
    """
    match = re.match(r'^([^@]+)@([^.]+)(\..*)', email)
    if not match:
        return email
    local, domain, tld = match.groups()

    # Mask local part: keep first char, mask rest preserving dots
    masked_local = local[0]
    for ch in local[1:]:
        masked_local += '.' if ch == '.' else '*'

    # Mask domain: keep first char, mask rest
    masked_domain = domain[0] + '*' * (len(domain) - 1)

    return f"{masked_local}@{masked_domain}{tld}"


def _is_already_masked(value: str) -> bool:
    """Check if a value is already partially masked with asterisks."""
    return '**' in value


def _build_proximity_text(page: fitz.Page) -> str:
    """Build text that groups nearby labels and values together.

    Some PDFs have labels ('BSB', 'Account Number') in one text block
    and values ('033-364', '088 243') in a separate block at the same y-position.
    Standard get_text("text") doesn't interleave them, so we sort all lines by
    y-position then x-position to reconstruct the reading order.
    """
    d = page.get_text("dict")
    lines_with_pos = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:  # skip image blocks
            continue
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", []))
            text = text.strip()
            if text:
                y = round(line["bbox"][1], 0)  # round y to group nearby lines
                x = line["bbox"][0]
                lines_with_pos.append((y, x, text))

    # Sort by y then x to get reading order
    lines_with_pos.sort(key=lambda t: (t[0], t[1]))

    # Group lines at similar y positions (within 5px) onto same line
    grouped = []
    current_y = None
    current_parts = []
    for y, x, text in lines_with_pos:
        if current_y is None or abs(y - current_y) > 5:
            if current_parts:
                grouped.append(" ".join(current_parts))
            current_y = y
            current_parts = [text]
        else:
            current_parts.append(text)
    if current_parts:
        grouped.append(" ".join(current_parts))

    return "\n".join(grouped)


def find_field_redactions(doc: fitz.Document, field_rules: dict[str, FieldRule]) -> list[Redaction]:
    """Find and create redactions for partial field masking."""
    redactions = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # Use both standard text and proximity text for matching
        page_text = page.get_text("text")
        proximity_text = _build_proximity_text(page)
        combined_text = page_text + "\n" + proximity_text

        for field_name, rule in field_rules.items():
            for pattern_str in rule.context_patterns:
                for match in re.finditer(pattern_str, combined_text, re.IGNORECASE):
                    matched_value = match.group(1) if match.lastindex else match.group(0)

                    # Skip whitelisted values
                    if any(wl.lower() == matched_value.lower() for wl in rule.whitelist):
                        continue

                    # Skip already-masked values
                    if _is_already_masked(matched_value):
                        continue

                    # Compute masked value
                    if field_name == "email":
                        masked = _mask_email(matched_value)
                    else:
                        masked = _mask_keeping_last(matched_value, rule.keep_last)

                    # Skip if masking didn't change anything
                    if masked == matched_value:
                        continue

                    # Find the matched value's position on the page
                    rects = page.search_for(matched_value)
                    for rect in rects:
                        redactions.append(Redaction(
                            page_num=page_num,
                            rect=fitz.Rect(rect),
                            original_text=matched_value,
                            replacement_text=masked,
                            redaction_type="field",
                        ))

    return redactions
